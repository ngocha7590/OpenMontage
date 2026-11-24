"""Local export bundler — the first PUBLISH-tier tool.

Every pipeline ends in a `publish` stage that produces a `publish_log` artifact,
but `tools/publishers/` shipped empty, so the mechanical packaging (copying the
render, writing metadata files, laying out the export directory, and emitting a
schema-valid `publish_log`) had to be hand-rolled by the agent each time.

This tool does that packaging deterministically and locally — no external
account, no upload, no cost. It takes the final render path plus the SEO
metadata the publish-director skill prepares and writes a self-contained export
bundle a creator can hand to any platform, returning a validated `publish_log`
entry with `status: "exported"`.

A networked publisher (e.g. a YouTube uploader) can be added later as a separate
`provider` under the same `publish` capability.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


MIN_CHAPTER_SECONDS = 10
MAX_THUMB_BYTES = 2 * 1024 * 1024


class ExportBundle(BaseTool):
    name = "export_bundle"
    version = "0.1.0"
    tier = ToolTier.PUBLISH
    capability = "publish"
    provider = "local"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = []  # pure filesystem packaging
    install_instructions = "No setup required — runs locally with the Python standard library."

    agent_skills = []

    capabilities = ["package_export", "write_publish_log"]
    supports = {
        "local_offline": True,
        "free": True,
        "uploads": False,
    }
    best_for = [
        "packaging a finished render for hand-off to any platform",
        "producing a schema-valid publish_log without an external account",
        "offline / no-API-key publishing",
    ]
    not_good_for = [
        "uploading directly to YouTube/TikTok/etc. (no network publish)",
        "generating SEO metadata or thumbnails (the publish-director prepares those)",
    ]

    input_schema = {
        "type": "object",
        "required": ["video_path", "title"],
        "properties": {
            "video_path": {
                "type": "string",
                "description": "Path to the final rendered video (from render_report.outputs[].path).",
            },
            "title": {"type": "string", "description": "Video title / SEO title."},
            "project_name": {
                "type": "string",
                "description": "Project name; used for the export folder. Defaults to the video's parent-of-parent dir name.",
            },
            "export_dir": {
                "type": "string",
                "description": "Override the export root. Defaults to 'exports/<project_name>'.",
            },
            "description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "chapters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "description": "Either {start_seconds, title} or {time, label}.",
                },
            },
            "subtitles_path": {"type": "string"},
            "thumbnail_path": {"type": "string"},
            "thumbnail_concept": {
                "type": "object",
                "description": "Thumbnail concept JSON when no rendered thumbnail exists.",
            },
            "platform": {
                "type": "string",
                "description": "Target platform label for the publish_log entry. Defaults to 'local'.",
            },
            "visibility": {"type": "string", "enum": ["public", "private", "unlisted"]},
            "category_id": {"type": "string", "default": "27", "description": "YouTube categoryId (27 = Education)."},
            "default_language": {"type": "string", "description": "BCP-47 code, e.g. 'vi'."},
            "made_for_kids": {"type": "boolean", "default": False},
            "timestamp": {
                "type": "string",
                "description": "Override the ISO-8601 timestamp (mainly for deterministic tests).",
            },
        },
    }
    output_schema = {
        "type": "object",
        "properties": {
            "publish_log": {"type": "object"},
            "export_path": {"type": "string"},
            "files_written": {"type": "array", "items": {"type": "string"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=0, network_required=False
    )
    side_effects = ["writes an export bundle directory to disk"]
    user_visible_verification = [
        "Open the export folder and confirm the video, metadata, and chapters are present and correct",
    ]

    # ---- Helpers ----

    @staticmethod
    def _format_chapter_time(seconds: float) -> str:
        seconds = int(round(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @staticmethod
    def _probe_duration(video_path: Path) -> Optional[float]:
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(video_path)],
                capture_output=True, text=True, timeout=30,
            )
            return float(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
        except Exception:
            return None

    @staticmethod
    def _parse_time(value: Any) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        parts = [float(p) for p in str(value).strip().split(":") if p != ""]
        total = 0.0
        for p in parts:
            total = total * 60 + p
        return total

    def _normalize_chapters(self, chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for ch in chapters:
            if "start_seconds" in ch or "time_seconds" in ch:
                start = float(ch.get("start_seconds", ch.get("time_seconds", 0)))
            elif "time" in ch:
                try:
                    start = self._parse_time(ch["time"])
                except ValueError:
                    start = 0.0
            else:
                start = 0.0
            out.append({"start_seconds": start, "title": ch.get("title") or ch.get("label") or ""})
        out.sort(key=lambda c: c["start_seconds"])
        return out

    @staticmethod
    def _merge_short_chapters(chapters: list[dict[str, Any]], duration: Optional[float]) -> list[dict[str, Any]]:
        """Merge chapters shorter than 10 s into their neighbour (keep the earlier title)."""
        chs = [dict(c) for c in chapters]
        changed = True
        while changed and len(chs) > 1:
            changed = False
            for i in range(len(chs)):
                end = chs[i + 1]["start_seconds"] if i + 1 < len(chs) else duration
                if end is None or end - chs[i]["start_seconds"] >= MIN_CHAPTER_SECONDS:
                    continue
                if i + 1 < len(chs):
                    if i == 0:  # first chapter must stay at 0: absorb the next one
                        del chs[1]
                    else:       # absorb into the previous chapter
                        del chs[i]
                else:           # short last chapter folds into the previous one
                    del chs[i]
                changed = True
                break
        return chs

    @staticmethod
    def _tags_length(tags: list[str]) -> int:
        total = sum(len(t) + (2 if " " in t else 0) for t in tags)
        return total + max(0, len(tags) - 1)

    def _chapter_lines(self, chapters: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for ch in chapters:
            label = ch.get("title") or ch.get("label") or ""
            if "start_seconds" in ch or "time_seconds" in ch:
                ts = self._format_chapter_time(ch.get("start_seconds", ch.get("time_seconds", 0)))
            elif "time" in ch:
                ts = str(ch["time"])
            else:
                ts = "0:00"
            lines.append(f"{ts} - {label}".rstrip(" -"))
        return lines

    # ---- Execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        video_path = Path(inputs["video_path"]).expanduser()
        if not video_path.is_file():
            return ToolResult(success=False, error=f"video_path not found: {video_path}")

        title = inputs["title"]
        project_name = inputs.get("project_name") or self._infer_project_name(video_path)

        # Explicitly-provided optional assets must exist — silently dropping them
        # would ship a publish package missing part of an approved deliverable.
        for key in ("subtitles_path", "thumbnail_path"):
            val = inputs.get(key)
            if val and not Path(val).expanduser().is_file():
                return ToolResult(success=False, error=f"{key} provided but not found: {val}")

        export_root = (
            Path(inputs["export_dir"]).expanduser()
            if inputs.get("export_dir")
            else self._default_export_dir(video_path, project_name)
        )

        video_dir = export_root / "video"
        meta_dir = export_root / "metadata"
        thumb_dir = export_root / "thumbnails"
        for d in (video_dir, meta_dir, thumb_dir):
            d.mkdir(parents=True, exist_ok=True)

        files_written: list[str] = []

        # Video
        out_video = video_dir / f"output{video_path.suffix or '.mp4'}"
        shutil.copy2(video_path, out_video)
        files_written.append(str(out_video))

        # Subtitles (optional)
        subs_in = inputs.get("subtitles_path")
        if subs_in:
            subs_in = Path(subs_in).expanduser()
            if subs_in.is_file():
                out_subs = video_dir / f"subtitles{subs_in.suffix or '.srt'}"
                shutil.copy2(subs_in, out_subs)
                files_written.append(str(out_subs))

        description = inputs.get("description", "")
        tags = inputs.get("tags", []) or []
        hashtags = inputs.get("hashtags", []) or []
        raw_chapters = inputs.get("chapters", []) or []
        warnings: list[str] = []

        duration = self._probe_duration(video_path)
        chapters = self._merge_short_chapters(self._normalize_chapters(raw_chapters), duration) if raw_chapters else []
        if raw_chapters and (
            len(chapters) < 3
            or chapters[0]["start_seconds"] != 0
            or any(
                (chapters[i + 1]["start_seconds"] if i + 1 < len(chapters) else (duration or chapters[i]["start_seconds"] + MIN_CHAPTER_SECONDS))
                - chapters[i]["start_seconds"] < MIN_CHAPTER_SECONDS
                for i in range(len(chapters))
            )
        ):
            warnings.append("chapters_invalid")
        if len(chapters) < 3:
            chapters = []
        chapter_lines = self._chapter_lines(chapters)

        full_description = "\n\n".join(([description] if description else []) + (["\n".join(chapter_lines)] if chapter_lines else []))

        if len(title) > 100:
            warnings.append("title_too_long")
        elif len(title) > 70:
            warnings.append("title_long")
        if len(full_description) > 5000:
            warnings.append("description_too_long")
        if self._tags_length(tags) > 500:
            warnings.append("tags_too_long")
        if any(c in title or c in full_description for c in "<>"):
            warnings.append("angle_brackets")

        # metadata.json
        metadata = {
            "title": title,
            "description": description,
            "tags": tags,
            "hashtags": hashtags,
            "chapters": chapters,
        }
        meta_json = meta_dir / "metadata.json"
        meta_json.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        files_written.append(str(meta_json))

        # description.txt (description + chapters appended, ready to paste)
        desc_parts = [description] if description else []
        if chapter_lines:
            desc_parts.append("\n".join(chapter_lines))
        desc_txt = meta_dir / "description.txt"
        desc_txt.write_text("\n\n".join(desc_parts) + ("\n" if desc_parts else ""), encoding="utf-8")
        files_written.append(str(desc_txt))

        # tags.txt (one per line)
        if tags:
            tags_txt = meta_dir / "tags.txt"
            tags_txt.write_text("\n".join(tags) + "\n", encoding="utf-8")
            files_written.append(str(tags_txt))

        # chapters.txt
        if chapter_lines:
            chapters_txt = meta_dir / "chapters.txt"
            chapters_txt.write_text("\n".join(chapter_lines) + "\n", encoding="utf-8")
            files_written.append(str(chapters_txt))

        # Thumbnail: real image if given; concept.json is always written when available
        thumb_rel: Optional[str] = None
        thumb_in = inputs.get("thumbnail_path")
        if thumb_in and Path(thumb_in).expanduser().is_file():
            thumb_in = Path(thumb_in).expanduser()
            out_thumb = thumb_dir / f"thumbnail{thumb_in.suffix or '.png'}"
            shutil.copy2(thumb_in, out_thumb)
            files_written.append(str(out_thumb))
            thumb_rel = out_thumb.relative_to(export_root).as_posix()
            if out_thumb.stat().st_size > MAX_THUMB_BYTES:
                warnings.append("thumbnail_too_large")
        else:
            warnings.append("thumbnail_missing")
        if inputs.get("thumbnail_concept"):
            concept = thumb_dir / "concept.json"
            concept.write_text(json.dumps(inputs["thumbnail_concept"], indent=2, ensure_ascii=False), encoding="utf-8")
            files_written.append(str(concept))

        # metadata/youtube.json: body of YouTube Data API videos.insert, plus extras
        category_id = str(inputs.get("category_id") or "27")
        default_language = inputs.get("default_language")
        snippet: dict[str, Any] = {
            "title": title,
            "description": full_description,
            "tags": tags,
            "categoryId": category_id,
            "defaultLanguage": default_language,
            "defaultAudioLanguage": default_language,
        }
        youtube = {
            "snippet": snippet,
            "status": {
                "privacyStatus": inputs.get("visibility") or "private",
                "selfDeclaredMadeForKids": bool(inputs.get("made_for_kids", False)),
            },
            "hashtags": hashtags,
            "chapters": chapters,
            "thumbnail": thumb_rel,
            "warnings": warnings,
        }
        yt_json = meta_dir / "youtube.json"
        yt_json.write_text(json.dumps(youtube, indent=2, ensure_ascii=False), encoding="utf-8")
        files_written.append(str(yt_json))

        timestamp = inputs.get("timestamp") or datetime.now(timezone.utc).isoformat()
        entry: dict[str, Any] = {
            "platform": inputs.get("platform", "local"),
            "status": "exported",
            "export_path": str(export_root),
            "timestamp": timestamp,
            "metadata_used": {
                "title": title,
                "description": description,
                "hashtags": hashtags,
                "chapters": chapters,
                "tags": tags,
                "thumbnail_path": thumb_rel,
                "category_id": category_id,
                "default_language": default_language,
            },
        }
        entry["visibility"] = inputs.get("visibility") or "private"

        publish_log = {"version": "1.0", "entries": [entry]}

        # Validate against the canonical schema so a bad entry fails here, not at checkpoint.
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("publish_log", publish_log)
        except Exception as exc:  # pragma: no cover - defensive
            return ToolResult(success=False, error=f"publish_log failed schema validation: {exc}")

        return ToolResult(
            success=True,
            data={
                "publish_log": publish_log,
                "export_path": str(export_root),
                "files_written": files_written,
                "warnings": warnings,
            },
            artifacts=[str(out_video)],
        )

    @staticmethod
    def _default_export_dir(video_path: Path, project_name: str) -> Path:
        """Keep run output inside the project workspace.

        When the render lives at ``projects/<name>/renders/...`` (the OpenMontage
        convention), default the bundle to ``projects/<name>/exports/`` alongside
        ``artifacts/``, ``assets/`` and ``renders/``. Otherwise fall back to a
        top-level ``exports/<project_name>/``.
        """
        resolved = video_path.resolve()
        if resolved.parent.name == "renders":
            return resolved.parent.parent / "exports"
        return Path("exports") / project_name

    @staticmethod
    def _infer_project_name(video_path: Path) -> str:
        # projects/<name>/renders/final.mp4 -> <name>; fall back to the file stem.
        parents = video_path.resolve().parents
        if len(parents) >= 2:
            return parents[1].name
        return video_path.stem
