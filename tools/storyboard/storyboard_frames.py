"""storyboard_frames — one keyframe per scene from the real product images.

Product-ad pipeline (PLAN-03 §4.2). The frames are reviewed at the storyboard
gate before any motion is paid for; approved frames feed storyboard_animate.
"""

from __future__ import annotations

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
    CONTROLS,
    IMAGE_EXTS,
    commercial,
    frame_index,
    load_json,
    rel_to,
    resolve,
    scenes,
    shot_for,
    slug,
    write_json,
)

MAX_REFS = 4  # gateway image providers accept at most 4 reference images


def frame_prompt(
    scene: dict[str, Any],
    shot: dict[str, Any],
    product: dict[str, Any],
    *,
    which: str,
    treatment: str = "",
    reference_note: str = "",
    aspect_ratio: str = "16:9",
) -> str:
    """Prompt for one storyboard keyframe (``which`` = start | end)."""
    lines = [
        "Product advertising keyframe for a video storyboard.",
        "The attached reference image(s) show the exact product. Reproduce it faithfully: "
        "same shape, proportions, colour, transparency, materials, buttons/ports and label layout.",
    ]
    if reference_note:
        lines.append(f"About the references: {reference_note}.")
    if product.get("sku"):
        lines.append(f"Product: {product['sku']}.")
    invariants = [str(i) for i in product.get("invariants") or [] if i]
    if invariants:
        lines.append("Must stay unchanged: " + "; ".join(invariants) + ".")
    if scene.get("description"):
        lines.append(f"Scene: {scene['description']}")
    state = shot.get("start_state" if which == "start" else "end_state")
    if state:
        lines.append(f"{'Opening' if which == 'start' else 'Closing'} state of the shot: {state}.")
    if shot.get("reference_view"):
        lines.append(f"Use this view from the references: {shot['reference_view']}.")
    lang = scene.get("shot_language") or {}
    shot_bits = []
    for key, label in (("shot_size", "framing"), ("lens_mm", "lens"), ("camera_angle", "angle"),
                       ("lighting_key", "lighting"), ("depth_of_field", "depth of field")):
        if lang.get(key) not in (None, ""):
            value = f"{lang[key]}mm" if key == "lens_mm" else str(lang[key]).replace("_", " ")
            shot_bits.append(f"{label} {value}")
    if shot_bits:
        lines.append("Shot: " + ", ".join(shot_bits) + ".")
    if treatment:
        lines.append(f"Visual treatment: {treatment}")
    lines.append(
        f"Aspect ratio {aspect_ratio}. Photorealistic commercial product photography. "
        "No text, no captions, no logos other than those on the product, no watermark, "
        "no other products or devices unless the scene asks for them."
    )
    return "\n".join(lines)


class StoryboardFrames(BaseTool):
    name = "storyboard_frames"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "storyboard"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=100)
    agent_skills = []
    capabilities = ["storyboard_keyframes", "product_reference_frames"]
    best_for = [
        "Reviewable per-scene keyframes built from real product photos before paying for motion",
        "Start/end frames for first-last-frame product shots",
    ]
    not_good_for = ["Final motion — use storyboard_animate on approved frames"]
    input_schema = {
        "type": "object",
        "properties": {
            "scene_plan": {"type": "object", "description": "scene_plan artifact (or scene_plan_path)"},
            "scene_plan_path": {"type": "string"},
            "workspace_path": {"type": "string", "description": "Project directory"},
            "identity_references": {
                "type": "array", "items": {"type": "string"},
                "description": "Product images (project-relative or absolute). Default: "
                               "scene_plan.metadata.commercial_product.identity_references, else inputs/*.png|jpg|webp",
            },
            "reference_note": {"type": "string", "description": "e.g. 'one image with six angles of the same case'"},
            "treatment": {"type": "string", "description": "Approved visual treatment from the proposal"},
            "aspect_ratio": {"type": "string", "default": "16:9"},
            "scenes": {"type": "array", "items": {"type": "string"}, "description": "Only these scene ids"},
            "overwrite": {"type": "boolean", "default": False},
            "notes": {"type": "object", "description": "{scene_id: reviewer note} — kept on the frame and on rejected takes"},
            "approve": {
                "description": "Mark frames approved without generating: list of scene ids or 'all'",
                "oneOf": [{"type": "array", "items": {"type": "string"}}, {"type": "string", "enum": ["all"]}],
            },
            "allowed_providers": {"type": "array", "items": {"type": "string"},
                                  "description": "Tried one by one in order; empty = image_selector ranking"},
            "output_path": {"type": "string", "description": "Default <workspace>/artifacts/storyboard.json"},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {"storyboard": {"type": "object"}, "generated": {"type": "array"}, "failed": {"type": "array"}},
    }
    artifact_schema = {"artifact": "storyboard"}
    side_effects = [
        "writes keyframes to <workspace>/assets/storyboard/",
        "writes artifacts/storyboard.json",
        "calls image_selector in edit mode (may spend on paid providers)",
    ]
    user_visible_verification = ["Compare every keyframe with the product photos at the storyboard gate"]

    @staticmethod
    def _selector() -> BaseTool | None:
        from tools.tool_registry import registry

        registry.ensure_discovered()
        return registry.get("image_selector")

    def get_status(self) -> ToolStatus:
        selector = self._selector()
        return ToolStatus.UNAVAILABLE if selector is None else selector.get_status()

    # ------------------------------------------------------------------ io --
    @staticmethod
    def _references(inputs: dict[str, Any], plan: dict[str, Any], workspace: Path) -> list[Path]:
        raw = inputs.get("identity_references") or commercial(plan).get("identity_references") or []
        refs = [resolve(workspace, r) for r in raw if isinstance(r, str)]
        if not refs:
            inputs_dir = workspace / "inputs"
            if inputs_dir.is_dir():
                refs = sorted(p for p in inputs_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
        return [p for p in refs if p.is_file()]

    @staticmethod
    def _approve(storyboard: dict[str, Any], which: Any) -> list[str]:
        done = []
        for frame in storyboard.get("frames") or []:
            if which == "all" or (isinstance(which, list) and frame.get("scene_id") in which):
                frame["status"] = "approved"
                done.append(frame["scene_id"])
        return done

    def _generate(self, selector: BaseTool, request: dict[str, Any], target: Path,
                  allowed: list[str]) -> tuple[Any, list[str]]:
        """Try each allowed provider in order (image_selector itself does not
        fall back). Returns (result or None, errors of the providers that failed)
        — errors are kept on success too, so a skipped provider is reported."""
        errors: list[str] = []
        for attempt in [[p] for p in allowed] or [None]:
            req = dict(request, allowed_providers=attempt) if attempt else dict(request)
            result = selector.execute(req)
            if result.success and target.is_file():
                return result, errors
            errors.append(f"{attempt[0] + ': ' if attempt else ''}{result.error or 'no image written'}")
        return None, errors

    # ------------------------------------------------------------- execute --
    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        workspace = inputs.get("workspace_path")
        if not workspace:
            return ToolResult(success=False, error="workspace_path (project directory) is required")
        ws = Path(workspace).resolve()
        out_path = Path(inputs.get("output_path") or ws / "artifacts" / "storyboard.json")
        storyboard = load_json(None, str(out_path)) or {"version": "1.0", "frames": []}

        if inputs.get("approve"):
            approved = self._approve(storyboard, inputs["approve"])
            validate_artifact("storyboard", storyboard)
            write_json(out_path, storyboard)
            return ToolResult(success=bool(approved), data={"storyboard": storyboard, "approved": approved},
                              artifacts=[str(out_path)],
                              error=None if approved else "no matching frames to approve")

        plan = load_json(inputs.get("scene_plan"), inputs.get("scene_plan_path"))
        if not plan or not scenes(plan):
            return ToolResult(success=False, error="scene_plan with scenes is required")
        refs = self._references(inputs, plan, ws)
        if not refs:
            return ToolResult(success=False, error="no product reference images (identity_references or inputs/)")
        selector = self._selector()
        if selector is None:
            return ToolResult(success=False, error="image_selector is not registered")

        product = commercial(plan)
        aspect = inputs.get("aspect_ratio") or storyboard.get("aspect_ratio") or "16:9"
        note = inputs.get("reference_note") or storyboard.get("reference_note") or ""
        storyboard.update({
            "aspect_ratio": aspect,
            "identity_references": [rel_to(ws, r) for r in refs],
        })
        if note:
            storyboard["reference_note"] = note
        notes = inputs.get("notes") or {}
        only = set(inputs.get("scenes") or [])
        frames = frame_index(storyboard)
        out_dir = ws / "assets" / "storyboard"
        out_dir.mkdir(parents=True, exist_ok=True)
        generated: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        written: list[str] = []
        cost = 0.0

        for scene in scenes(plan):
            sid = str(scene["id"])
            if only and sid not in only:
                continue
            shot = shot_for(plan, sid)
            control = shot.get("control") if shot.get("control") in CONTROLS else "image_to_video"
            prev = frames.get(sid)
            frame: dict[str, Any] = {
                "scene_id": sid, "control": control, "status": "draft",
                "takes": list((prev or {}).get("takes") or []), "end_frame": None, "clip": None,
            }
            if sid in notes:
                frame["notes"] = str(notes[sid])
            elif prev and prev.get("notes"):
                frame["notes"] = prev["notes"]

            wanted = ["start"] + (["end"] if control == "first_last_frame" else [])
            scene_failed = False
            moved: list[tuple[Path, Path]] = []  # (take, original) — undone if the scene fails
            for which in wanted:
                key = f"{which}_frame"
                target = out_dir / f"{slug(sid)}_{which}.png"
                old = (prev or {}).get(key)
                if target.is_file() and not inputs.get("overwrite") and old:
                    frame[key] = old  # reuse the existing frame
                    generated.append({"scene_id": sid, "frame": which, "path": old["path"], "skipped": "exists"})
                    continue
                if old and old.get("path") and inputs.get("overwrite") and target.is_file():
                    keep = out_dir / "takes" / f"{slug(sid)}_{which}_{len(frame['takes']) + 1}.png"
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), keep)
                    moved.append((keep, target))
                    take = {"path": rel_to(ws, keep), "kind": key}
                    if frame.get("notes"):
                        take["rejected_reason"] = frame["notes"]
                    frame["takes"].append(take)

                provided = shot.get("use_provided") if which == "start" else shot.get("use_provided_end")
                if control == "packshot_still" and provided:
                    src = resolve(ws, provided)
                    if src.is_file():
                        shutil.copyfile(src, target)
                        frame[key] = {"path": rel_to(ws, target), "source": "provided"}
                        written.append(str(target))
                        generated.append({"scene_id": sid, "frame": which, "path": frame[key]["path"],
                                          "source": "provided"})
                        continue
                prompt = frame_prompt(scene, shot, product, which=which, treatment=inputs.get("treatment", ""),
                                      reference_note=note, aspect_ratio=aspect)
                if frame.get("notes"):
                    prompt += f"\nReviewer feedback to address: {frame['notes']}"
                request = {
                    "prompt": prompt,
                    "output_path": str(target),
                    "aspect_ratio": aspect,
                    "generation_mode": "edit",
                    "image_paths": [str(r) for r in refs[:MAX_REFS]],
                }
                result, errors = self._generate(selector, request, target, list(inputs.get("allowed_providers") or []))
                if result is None:
                    failed.append({"scene_id": sid, "frame": which, "error": " | ".join(errors)})
                    scene_failed = True
                    break
                cost += float(result.cost_usd or 0.0)
                image = {"path": rel_to(ws, target), "source": "generated", "prompt": prompt,
                         "source_tool": str(result.data.get("selected_tool") or "image_selector")}
                if result.data.get("selected_provider"):
                    image["provider"] = str(result.data["selected_provider"])
                if result.model:
                    image["model"] = str(result.model)
                frame[key] = image
                written.append(str(target))
                entry = {"scene_id": sid, "frame": which, "path": image["path"]}
                if errors:
                    entry["fallback_errors"] = errors  # providers skipped before this one worked
                generated.append(entry)

            if scene_failed:
                for keep, original in moved:  # put the last good frames back
                    if keep.is_file():
                        if original.is_file():
                            original.unlink()  # a half-made frame of this scene
                        shutil.move(str(keep), original)
                if prev:
                    frames[sid] = prev  # keep the last good frame on failure
                continue
            reused = all(
                any(g["scene_id"] == sid and g["frame"] == w and g.get("skipped") for g in generated)
                for w in wanted
            )
            if reused and prev:
                # nothing changed → keep review status and any clip already made
                frame["status"] = prev.get("status", "draft")
                frame["clip"] = prev.get("clip")
            if not frame["takes"]:
                frame.pop("takes")
            frames[sid] = frame

        order = [str(s["id"]) for s in scenes(plan)]
        storyboard["frames"] = [frames[s] for s in order if s in frames] + [
            f for s, f in frames.items() if s not in order
        ]
        validate_artifact("storyboard", storyboard)
        written.append(write_json(out_path, storyboard))
        ok = bool(generated)
        return ToolResult(
            success=ok,
            data={"storyboard": storyboard, "generated": generated, "failed": failed},
            artifacts=written,
            error=None if ok else "; ".join(f"{f['scene_id']}/{f['frame']}: {f['error']}" for f in failed)
            or "nothing generated",
            cost_usd=round(cost, 4),
            duration_seconds=round(time.time() - start, 2),
        )
