"""Shared helpers for the storyboard tools (product-ad pipeline, PLAN-03)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
CONTROLS = ("image_to_video", "first_last_frame", "packshot_still")


def slug(value: str) -> str:
    chars = [c.lower() if c.isalnum() else "-" for c in str(value).strip()]
    return "-".join("".join(chars).split("-")).strip("-") or "scene"


def load_json(obj: Any, path: str | None) -> dict[str, Any] | None:
    """An artifact passed inline, or read from ``path`` (``None`` if neither)."""
    if isinstance(obj, dict):
        return json.loads(json.dumps(obj))  # never mutate the caller's dict
    if path and Path(path).is_file():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return None


def write_json(path: Path, data: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def rel_to(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(path)


def resolve(workspace: Path, raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else workspace / p


def commercial(scene_plan: dict[str, Any]) -> dict[str, Any]:
    meta = scene_plan.get("metadata") or {}
    cp = meta.get("commercial_product") if isinstance(meta, dict) else None
    return cp if isinstance(cp, dict) else {}


def shot_for(scene_plan: dict[str, Any], scene_id: str) -> dict[str, Any]:
    for shot in commercial(scene_plan).get("shots") or []:
        if isinstance(shot, dict) and str(shot.get("scene_id")) == scene_id:
            return shot
    return {}


def scenes(scene_plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in scene_plan.get("scenes") or [] if isinstance(s, dict) and s.get("id") is not None]


def frame_index(storyboard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(f.get("scene_id")): f for f in storyboard.get("frames") or [] if isinstance(f, dict)}
