"""Minimum burned-caption font size (4.2% of the short side).

Agents often write 30-40 px captions into edit_decisions; at 1080p that is
hard to read. Every path that burns subtitles clamps to this floor.
Mirror of the clamp in remotion-composer/src/components/CaptionOverlay.tsx.
"""

from __future__ import annotations

import subprocess

MIN_CAPTION_RATIO = 0.042
# libass scales SRT / un-sized ASS to a 288-line virtual canvas, so the
# ``FontSize`` of force_style is in those units, not output pixels.
ASS_DEFAULT_PLAY_RES_Y = 288


def min_caption_px(width: int, height: int) -> int:
    """Smallest allowed caption size in output pixels (45 at 1080p / 1080x1920)."""
    return round(MIN_CAPTION_RATIO * min(int(width), int(height)))


def clamp_caption_px(font_size, width: int, height: int) -> int:
    """Clamp a pixel font size to the floor; larger values pass unchanged."""
    floor = min_caption_px(width, height)
    try:
        value = float(font_size)
    except (TypeError, ValueError):
        return floor
    return max(int(round(value)), floor)


def clamp_ass_font_size(
    font_size,
    width: int,
    height: int,
    play_res_y: int = ASS_DEFAULT_PLAY_RES_Y,
) -> int:
    """Clamp an ASS ``FontSize`` (relative to ``play_res_y``) to the floor.

    The pixel floor is converted into ASS units: floor_px * play_res_y / height.
    """
    floor = max(1, round(min_caption_px(width, height) * play_res_y / int(height)))
    try:
        value = float(font_size)
    except (TypeError, ValueError):
        return floor
    return max(int(round(value)), floor)


def probe_video_size(path, default: tuple[int, int] = (1920, 1080)) -> tuple[int, int]:
    """Return (width, height) via ffprobe; ``default`` when it cannot be read."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip().splitlines()[0]
        w, h = out.split("x")[:2]
        w, h = int(w), int(h)
        return (w, h) if w > 0 and h > 0 else default
    except Exception:
        return default
