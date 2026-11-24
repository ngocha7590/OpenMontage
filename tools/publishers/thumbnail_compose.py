"""Compose a YouTube thumbnail: AI background (no text) + code-drawn headline.

Text is always rendered here with a bundled font (Be Vietnam Pro ExtraBold,
SIL OFL, full Vietnamese diacritics). Never ask an image model to draw text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

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

FONT_DIR = Path(__file__).parent / "fonts"
BUNDLED_FONT = FONT_DIR / "BeVietnamPro-ExtraBold.ttf"
_SYSTEM_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
MAX_BYTES = 2 * 1024 * 1024
LAYOUTS = ("left", "right", "bottom", "center")


def resolve_font_path() -> Path | None:
    if BUNDLED_FONT.is_file():
        return BUNDLED_FONT
    for p in _SYSTEM_FONTS:
        if Path(p).is_file():
            return Path(p)
    return None


def _hex(value: str, default: str) -> tuple[int, int, int]:
    v = (value or default).lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        v = default.lstrip("#")
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word, flags=re.UNICODE).casefold()


class ThumbnailCompose(BaseTool):
    name = "thumbnail_compose"
    version = "0.1.0"
    tier = ToolTier.PUBLISH
    capability = "publish"
    provider = "local"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = []
    install_instructions = "Requires Pillow (pip install pillow). Font is bundled."
    agent_skills = []

    capabilities = ["compose_thumbnail"]
    supports = {"local_offline": True, "free": True, "vietnamese": True}
    best_for = ["YouTube thumbnails: AI background plus a legible code-drawn headline"]
    not_good_for = ["generating the background image itself"]

    input_schema = {
        "type": "object",
        "required": ["background_path", "headline", "output_path"],
        "properties": {
            "background_path": {"type": "string", "description": "AI-generated image with no text."},
            "headline": {"type": "string", "description": "3-6 words, in the video's language."},
            "accent_words": {"type": "array", "items": {"type": "string"}},
            "palette": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "accent": {"type": "string"},
                    "scrim": {"type": "string"},
                },
            },
            "layout": {"type": "string", "enum": list(LAYOUTS), "default": "left"},
            "output_path": {"type": "string"},
            "width": {"type": "integer", "default": 1280},
            "height": {"type": "integer", "default": 720},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "bytes": {"type": "integer"},
        },
    }
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=0, network_required=False)
    side_effects = ["writes a thumbnail image to output_path"]
    user_visible_verification = ["Open the thumbnail and check the headline is legible at small size"]

    def get_status(self) -> ToolStatus:
        try:
            import PIL  # noqa: F401
        except ImportError:
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    # ---- text layout ----

    @staticmethod
    def _wrap(words: list[str], font, max_w: int, draw, max_lines: int) -> list[list[str]] | None:
        lines: list[list[str]] = [[]]
        for w in words:
            trial = " ".join(lines[-1] + [w])
            if lines[-1] and draw.textlength(trial, font=font) > max_w:
                lines.append([w])
            else:
                lines[-1].append(w)
            if len(lines) > max_lines:
                return None
        if any(draw.textlength(" ".join(l), font=font) > max_w for l in lines):
            return None
        return lines

    def _fit(self, words, font_path, box_w, box_h, draw, from_size):
        """Largest size that fits in 1-3 lines. Prefers breaking after . ? ! when
        that costs less than ~20% of the size."""
        from PIL import ImageFont

        segments: list[list[str]] = [[]]
        for w in words:
            segments[-1].append(w)
            if w[-1:] in ".?!…" and w is not words[-1]:
                segments.append([])
        segments = [sg for sg in segments if sg]

        def search(wrap):
            for size in range(from_size, 23, -2):
                font = ImageFont.truetype(str(font_path), size)
                lines = wrap(font)
                if lines and int(size * 1.12) * len(lines) <= box_h:
                    return font, lines, size, int(size * 1.12)
            return None

        def seg_wrap(font):
            if len(segments) < 2 or len(segments) > 3:
                return None
            if any(draw.textlength(" ".join(sg), font=font) > box_w for sg in segments):
                return None
            return segments

        greedy = search(lambda f: self._wrap(words, f, box_w, draw, 3))
        seg = search(seg_wrap)
        if seg and (not greedy or seg[2] >= 0.8 * greedy[2]):
            return seg
        if greedy:
            return greedy
        return ImageFont.truetype(str(font_path), 24), [words], 24, 27

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        from PIL import Image, ImageDraw, ImageFont

        bg_path = Path(inputs["background_path"]).expanduser()
        if not bg_path.is_file():
            return ToolResult(success=False, error=f"background_path not found: {bg_path}")
        headline = (inputs.get("headline") or "").strip()
        if not headline:
            return ToolResult(success=False, error="headline is empty")
        font_path = resolve_font_path()
        if font_path is None:
            return ToolResult(success=False, error="no usable font found (bundled font missing)")

        W = int(inputs.get("width") or 1280)
        H = int(inputs.get("height") or 720)
        layout = inputs.get("layout") or "left"
        if layout not in LAYOUTS:
            return ToolResult(success=False, error=f"layout must be one of {LAYOUTS}")
        pal = inputs.get("palette") or {}
        text_c = _hex(pal.get("text"), "#FFFFFF")
        accent_c = _hex(pal.get("accent"), "#FFD23F")
        scrim_c = _hex(pal.get("scrim"), "#000000")
        accents = {_norm(a) for a in (inputs.get("accent_words") or []) if _norm(a)}

        # Cover-crop + Lanczos
        img = Image.open(bg_path).convert("RGB")
        scale = max(W / img.width, H / img.height)
        nw, nh = max(W, round(img.width * scale)), max(H, round(img.height * scale))
        img = img.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - W) // 2, (nh - H) // 2
        img = img.crop((left, top, left + W, top + H)).convert("RGBA")

        # Gradient scrim on the text side
        alpha = Image.new("L", (W, H), 0)
        px = alpha.load()
        peak = 0.88
        for y in range(H):
            for x in range(W):
                if layout == "left":
                    t = 1 - x / (W * 0.85)
                elif layout == "right":
                    t = 1 - (W - 1 - x) / (W * 0.85)
                elif layout == "bottom":
                    t = 1 - (H - 1 - y) / (H * 0.65)
                else:
                    t = 0.62
                px[x, y] = int(255 * peak * max(0.0, min(1.0, t)) ** 1.2)
        scrim = Image.new("RGBA", (W, H), scrim_c + (255,))
        scrim.putalpha(alpha)
        img = Image.alpha_composite(img, scrim)

        # Text box
        margin = int(W * 0.05)
        if layout in ("left", "right"):
            box_w, box_h = int(W * 0.55) - margin, H - 2 * margin
            box_x = margin if layout == "left" else W - margin - box_w
        elif layout == "bottom":
            box_w, box_h = W - 2 * margin, int(H * 0.42)
            box_x = margin
        else:
            box_w, box_h = int(W * 0.8), int(H * 0.6)
            box_x = (W - box_w) // 2
        box_y = {"bottom": H - margin - box_h}.get(layout, (H - box_h) // 2)

        draw = ImageDraw.Draw(img)
        words = headline.split()
        font, lines, size, lh = self._fit(words, font_path, box_w, box_h, draw, int(H * 0.30))

        text_h = lh * len(lines)
        y = box_y + (box_h - text_h) // 2 if layout != "bottom" else box_y + box_h - text_h
        stroke = max(2, size // 14)
        shadow = max(2, size // 16)
        space = draw.textlength(" ", font=font)
        for line in lines:
            line_w = draw.textlength(" ".join(line), font=font)
            if layout == "center":
                x = box_x + (box_w - line_w) / 2
            elif layout == "right":
                x = box_x + box_w - line_w
            else:
                x = box_x
            for w in line:
                color = accent_c if _norm(w) in accents else text_c
                draw.text((x + shadow, y + shadow), w, font=font, fill=scrim_c + (200,),
                          stroke_width=stroke, stroke_fill=scrim_c + (200,))
                draw.text((x, y), w, font=font, fill=color + (255,),
                          stroke_width=stroke, stroke_fill=scrim_c + (255,))
                x += draw.textlength(w, font=font) + space
            y += lh

        out = Path(inputs["output_path"]).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        rgb = img.convert("RGB")
        rgb.save(out, "PNG", optimize=True)
        if out.stat().st_size > MAX_BYTES:
            jpg = out.with_suffix(".jpg")
            rgb.save(jpg, "JPEG", quality=90, optimize=True)
            if jpg != out:
                out.unlink(missing_ok=True)
            out = jpg
        return ToolResult(
            success=True,
            data={"path": str(out), "width": W, "height": H, "bytes": out.stat().st_size,
                  "font": font_path.name, "font_size": size, "lines": [" ".join(l) for l in lines]},
            artifacts=[str(out)],
        )
