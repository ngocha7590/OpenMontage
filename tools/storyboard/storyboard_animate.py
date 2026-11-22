"""storyboard_animate — image_to_video from approved storyboard keyframes.

Product-ad pipeline (PLAN-03 §4.3). Only frames approved at the storyboard gate
are animated; the prompt describes motion only (the frame carries appearance).
"""

from __future__ import annotations

import math
import shutil
import time
from pathlib import Path
from typing import Any

from schemas.artifacts import validate_artifact
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolStability,
    ToolStatus,
    ToolTier,
)
from tools.storyboard._common import (
    frame_index,
    load_json,
    rel_to,
    resolve,
    scenes,
    shot_for,
    slug,
    write_json,
)


def motion_prompt(scene: dict[str, Any], shot: dict[str, Any], *, has_end: bool) -> str:
    """Motion-only prompt: camera, light, the allowed change — never re-describe
    the product (the first frame defines its appearance)."""
    lang = scene.get("shot_language") or {}
    parts = []
    move = str(lang.get("camera_movement") or "").replace("_", " ").strip()
    parts.append(f"Camera: {move}." if move else "Camera: slow, steady move.")
    if shot.get("allowed_change"):
        parts.append(f"Only this changes: {shot['allowed_change']}.")
    if has_end and shot.get("end_state"):
        parts.append(f"End on: {shot['end_state']}.")
    if scene.get("shot_intent"):
        parts.append(f"Intent: {scene['shot_intent']}")
    parts.append(
        "Keep the product exactly as in the first frame: same shape, colour, details and label. "
        "No morphing, no extra objects, no text."
    )
    return " ".join(parts)


def _accepts_end_frame(tool: BaseTool | None) -> bool:
    if tool is None:
        return False
    props = (getattr(tool, "input_schema", {}) or {}).get("properties", {})
    if any(k in props for k in ("last_image_path", "last_image_url", "last_frame_image_path",
                                "end_image_url", "end_image_path")):
        return True
    supports = getattr(tool, "supports", {}) or {}
    return bool(supports.get("first_last_frame") or supports.get("first_last_frame_to_video"))


class StoryboardAnimate(BaseTool):
    name = "storyboard_animate"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "storyboard"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=500)
    agent_skills = ["ai-video-gen"]
    capabilities = ["storyboard_to_video", "image_to_video_batch"]
    best_for = ["Animating approved product keyframes with image_to_video (first/last frame when supported)"]
    not_good_for = ["Frames not yet approved", "Text-to-video without a keyframe"]
    input_schema = {
        "type": "object",
        "properties": {
            "storyboard": {"type": "object", "description": "storyboard artifact (or storyboard_path)"},
            "storyboard_path": {"type": "string", "description": "Default <workspace>/artifacts/storyboard.json"},
            "scene_plan": {"type": "object"},
            "scene_plan_path": {"type": "string"},
            "workspace_path": {"type": "string"},
            "scenes": {"type": "array", "items": {"type": "string"}},
            "overwrite": {"type": "boolean", "default": False},
            "allowed_providers": {"type": "array", "items": {"type": "string"},
                                  "description": "Only these video providers; empty = video_selector ranking"},
            "motion_prompts": {"type": "object", "description": "{scene_id: motion-only prompt} overrides"},
            "notes": {"type": "object", "description": "{scene_id: why the previous clip was rejected}"},
            "max_attempts_per_scene": {"type": "integer", "default": 2, "minimum": 1},
            "budget_usd": {"type": "number", "description": "Stop starting new clips once spent ≥ this"},
            "min_duration": {"type": "number", "default": 2},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {"clips": {"type": "array"}, "failed": {"type": "array"}, "skipped": {"type": "array"},
                       "storyboard": {"type": "object"}},
    }
    artifact_schema = {"artifact": "storyboard"}
    side_effects = [
        "writes clips to <workspace>/assets/video/",
        "updates frames[].clip in artifacts/storyboard.json",
        "calls video_selector (may spend on paid providers)",
    ]
    user_visible_verification = ["Watch every clip against the product photos at the assets gate"]

    @staticmethod
    def _selector() -> BaseTool | None:
        from tools.tool_registry import registry

        registry.ensure_discovered()
        return registry.get("video_selector")

    @staticmethod
    def _tool(name: str | None) -> BaseTool | None:
        if not name:
            return None
        from tools.tool_registry import registry

        return registry.get(name)

    def get_status(self) -> ToolStatus:
        selector = self._selector()
        return ToolStatus.UNAVAILABLE if selector is None else selector.get_status()

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        workspace = inputs.get("workspace_path")
        if not workspace:
            return ToolResult(success=False, error="workspace_path (project directory) is required")
        ws = Path(workspace).resolve()
        sb_path = Path(inputs.get("storyboard_path") or ws / "artifacts" / "storyboard.json")
        storyboard = load_json(inputs.get("storyboard"), str(sb_path))
        if not storyboard or not storyboard.get("frames"):
            return ToolResult(success=False, error="storyboard with frames is required (run storyboard_frames first)")
        plan = load_json(inputs.get("scene_plan"),
                         inputs.get("scene_plan_path") or str(ws / "artifacts" / "scene_plan.json")) or {}
        scene_by_id = {str(s["id"]): s for s in scenes(plan)}
        selector = self._selector()
        if selector is None:
            return ToolResult(success=False, error="video_selector is not registered")

        only = set(inputs.get("scenes") or [])
        allowed = list(inputs.get("allowed_providers") or [])
        prompts = inputs.get("motion_prompts") or {}
        notes = inputs.get("notes") or {}
        attempts_max = max(1, int(inputs.get("max_attempts_per_scene") or 2))
        budget = inputs.get("budget_usd")
        min_dur = float(inputs.get("min_duration") or 2)
        aspect = storyboard.get("aspect_ratio")
        out_dir = ws / "assets" / "video"
        out_dir.mkdir(parents=True, exist_ok=True)

        clips: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        written: list[str] = []
        spent = 0.0

        for frame in storyboard["frames"]:
            sid = str(frame.get("scene_id"))
            if only and sid not in only:
                continue
            if frame.get("control") == "packshot_still":
                skipped.append({"scene_id": sid, "reason": "packshot_still — placed as a still in edit"})
                continue
            if frame.get("status") != "approved":
                failed.append({"scene_id": sid, "error": f"frame not approved (status={frame.get('status')})"})
                continue
            target = out_dir / f"{slug(sid)}.mp4"
            if target.is_file() and frame.get("clip") and not inputs.get("overwrite"):
                skipped.append({"scene_id": sid, "reason": "exists", "path": frame["clip"].get("path")})
                continue
            if budget is not None and spent >= float(budget):
                failed.append({"scene_id": sid, "error": f"budget reached ({spent:.2f} ≥ {float(budget):.2f} USD)"})
                continue
            first = resolve(ws, (frame.get("start_frame") or {}).get("path", ""))
            if not first.is_file():
                failed.append({"scene_id": sid, "error": f"start frame missing: {first}"})
                continue
            end_rel = (frame.get("end_frame") or {}).get("path")
            end = resolve(ws, end_rel) if end_rel else None
            if end is not None and not end.is_file():
                failed.append({"scene_id": sid, "error": f"end frame missing: {end}"})
                continue
            if target.is_file() and frame.get("clip"):
                keep = out_dir / "takes" / f"{slug(sid)}_{len(frame.get('takes') or []) + 1}.mp4"
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), keep)
                take = {"path": rel_to(ws, keep), "kind": "clip"}
                if notes.get(sid):
                    take["rejected_reason"] = str(notes[sid])
                frame.setdefault("takes", []).append(take)

            scene = scene_by_id.get(sid, {})
            shot = shot_for(plan, sid)
            length = None
            if isinstance(scene.get("start_seconds"), (int, float)) and isinstance(scene.get("end_seconds"), (int, float)):
                length = float(scene["end_seconds"]) - float(scene["start_seconds"])
            duration = max(min_dur, math.ceil(length)) if length else 5
            prompt = prompts.get(sid) or motion_prompt(scene, shot, has_end=end is not None)
            if notes.get(sid):
                prompt += f" Fix from review: {notes[sid]}."
            request: dict[str, Any] = {
                "prompt": prompt,
                "operation": "image_to_video",
                "reference_image_path": str(first),
                "duration": duration,
                "output_path": str(target),
            }
            if aspect:
                request["aspect_ratio"] = aspect
            if end is not None:
                request["last_image_path"] = str(end)
            if allowed:
                request["allowed_providers"] = allowed

            errors: list[str] = []
            result = None
            for _ in range(attempts_max):
                res = selector.execute(dict(request))
                spent += float(res.cost_usd or 0.0)
                if res.success and target.is_file():
                    result = res
                    break
                errors.append(res.error or "no video written")
                if budget is not None and spent >= float(budget):
                    break
            if result is None:
                failed.append({"scene_id": sid, "error": " | ".join(errors)})
                continue

            tool_name = str(result.data.get("selected_tool") or "")
            warnings = []
            used_end = end is not None and _accepts_end_frame(self._tool(tool_name))
            if end is not None and not used_end:
                warnings.append(f"{tool_name or 'provider'} has no end-frame input; only the start frame was used")
            clip = {
                "path": rel_to(ws, target),
                "prompt": prompt,
                "source_tool": tool_name or "video_selector",
                "duration_seconds": float(result.data.get("duration") or duration),
                "used_end_frame": used_end,
                "cost_usd": round(float(result.cost_usd or 0.0), 4),
            }
            if result.data.get("selected_provider"):
                clip["provider"] = str(result.data["selected_provider"])
            if result.model:
                clip["model"] = str(result.model)
            frame["clip"] = clip
            written.append(str(target))
            clips.append({"scene_id": sid, **clip, "warnings": warnings})

        validate_artifact("storyboard", storyboard)
        written.append(write_json(sb_path, storyboard))
        ok = bool(clips) or (not failed and bool(skipped))
        return ToolResult(
            success=ok,
            data={"clips": clips, "failed": failed, "skipped": skipped, "storyboard": storyboard},
            artifacts=written,
            error=None if ok else "; ".join(f"{f['scene_id']}: {f['error']}" for f in failed) or "nothing animated",
            cost_usd=round(spent, 4),
            duration_seconds=round(time.time() - start, 2),
        )
