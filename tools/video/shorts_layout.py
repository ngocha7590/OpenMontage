"""YouTube-Shorts layout for vertical clips cut from 16:9 sources.

One ffmpeg pass produces a finished 1080x1920 clip::

    y=180   ┌──────────────────────┐  title  (Be Vietnam Pro ExtraBold ~76 px,
            │  big hook title      │          <=3 lines, accent words coloured)
            ├──────────────────────┤  +40 px
            │  content box 1080xH  │  source content, zoomed per shot onto its
            │                      │  content bbox (never cuts text/diagrams)
            ├──────────────────────┤  +40 px
            │  burned captions     │  libass ASS, <=2 lines, centred in 960 px
    <=1600  └──────────────────────┘  (bottom 300 px = Shorts UI, kept empty)

The per-shot content analysis is reused from ``_content_reframe`` (shot
detection, per-frame content bbox, run splitting); only the target window
changes: the box aspect 1080:H instead of the full 9:16 frame.  The box top
and height are fixed for the whole clip (H = tallest shot needs) so the layout
never jumps; shorter shots are centred inside the box.

Pure geometry/text helpers live at module level so they can be unit tested
without ffmpeg.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ResumeSupport,
    ToolResult,
    ToolStability,
    ToolTier,
)

# ------------------------------------------------------------------ geometry
OUT_W, OUT_H = 1080, 1920
SAFE_X0, SAFE_X1 = 60, 1020          # nothing critical outside this band
TEXT_W = 960                         # max text width (centred => x 60..1020)
TITLE_TOP = 180                      # below the Shorts top UI
TITLE_SIZE, TITLE_MIN_SIZE, TITLE_MAX_LINES = 76, 56, 3
TITLE_LINE_SPACING = 1.16
GAP = 40                             # title -> box -> captions
BOX_MAX_H = 760
CAPTION_SIZE, CAPTION_MAX_LINES = 62, 2
CAPTION_LINE_SPACING = 1.3
CAPTION_BOTTOM_LIMIT = 1600          # bottom 300 px belong to Shorts UI
DEFAULT_ACCENT = "#F5C542"
DEFAULT_BG = "#0B0D12"
NEAR_BLACK_LUMA = 40

FONT_PATH = Path(__file__).resolve().parents[1] / "publishers" / "fonts" / "BeVietnamPro-ExtraBold.ttf"
FONT_FAMILY = "Be Vietnam Pro ExtraBold"   # fontconfig/libass family of the bundled file


# ------------------------------------------------------------------- text
def _font(size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONT_PATH), size)


def _norm_word(w: str) -> str:
    w = unicodedata.normalize("NFC", w).casefold()
    return re.sub(r"^\W+|\W+$", "", w)


def tokenize_title(title: str, accent_words: list[str] | None) -> list[tuple[str, int]]:
    """Split a title into (word, accent_phrase_id); id -1 = plain. Accent entries may be multi-word phrases."""
    title = unicodedata.normalize("NFC", title)
    words = title.split()
    ids = [-1] * len(words)
    normed = [_norm_word(w) for w in words]
    for k, phrase in enumerate(accent_words or []):
        pw = [_norm_word(p) for p in unicodedata.normalize("NFC", phrase).split()]
        if not pw:
            continue
        for i in range(len(words) - len(pw) + 1):
            if normed[i:i + len(pw)] == pw:
                for j in range(i, i + len(pw)):
                    ids[j] = k
    return list(zip(words, ids))


def _units(toks: list[tuple[str, int]]) -> list[list[tuple[str, int]]]:
    """Group consecutive words of one accent phrase so a wrap never splits the phrase."""
    units: list[list[tuple[str, int]]] = []
    for w, k in toks:
        if units and k >= 0 and units[-1][-1][1] == k:
            units[-1].append((w, k))
        else:
            units.append([(w, k)])
    return units


def _greedy(words: list[str], limit: float, measure) -> list[list[int]]:
    lines: list[list[int]] = [[]]
    for i, w in enumerate(words):
        cand = " ".join(words[j] for j in lines[-1] + [i])
        if lines[-1] and measure(cand) > limit:
            lines.append([i])
        else:
            lines[-1].append(i)
    return lines


def wrap_balanced(words: list[str], max_w: float, measure) -> list[list[int]]:
    """Greedy wrap, then tighten the limit while the line count stays the same (balanced lines)."""
    lines = _greedy(words, max_w, measure)
    k = len(lines)
    if k <= 1:
        return lines
    lo, hi = max_w * 0.45, max_w
    best = lines
    for _ in range(14):
        mid = (lo + hi) / 2
        cand = _greedy(words, mid, measure)
        if len(cand) <= k and all(measure(" ".join(words[j] for j in ln)) <= max_w for ln in cand):
            best, hi = cand, mid
        else:
            lo = mid
    return best


def fit_title(title: str, accent_words: list[str] | None = None, size: int = TITLE_SIZE,
              min_size: int = TITLE_MIN_SIZE, max_lines: int = TITLE_MAX_LINES,
              max_w: int = TEXT_W, measure_factory=None) -> dict[str, Any]:
    """Wrap ``title`` into <= max_lines lines of <= max_w px, shrinking from ``size`` to ``min_size``.

    Returns {size, lines: [[(word, accent), ...], ...], truncated}.
    ``measure_factory(size) -> callable(str) -> px`` is injectable for tests.
    """
    units = _units(tokenize_title(title, accent_words))
    words = [" ".join(w for w, _ in u) for u in units]
    if not words:
        return {"size": size, "lines": [], "truncated": False}
    mf = measure_factory or (lambda s: (lambda t, f=_font(s): f.getlength(t)))
    s = max(size, min_size)
    while True:
        measure = mf(s)
        idx = wrap_balanced(words, max_w, measure)
        if len(idx) <= max_lines or s <= min_size:
            break
        s = max(s - 2, min_size)
    truncated = len(idx) > max_lines
    if truncated:
        idx = idx[:max_lines]
        last = idx[-1]
        while len(last) > 1 and measure(" ".join(words[j] for j in last) + "…") > max_w:
            last.pop()
    lines = [[(w, k >= 0) for j in ln for (w, k) in units[j]] for ln in idx]
    if truncated:
        w, a = lines[-1][-1]
        lines[-1][-1] = (w + "…", a)
    return {"size": s, "lines": lines, "truncated": truncated}


def line_height(size: int, spacing: float) -> int:
    return int(round(size * spacing))


def hex_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def render_title_png(fit: dict, path: Path, accent: str = DEFAULT_ACCENT, spacing: float = TITLE_LINE_SPACING,
                     pad: int = 16) -> tuple[int, int]:
    """Render the title block to a 1080-wide transparent PNG. Returns (png_h, text_block_h)."""
    from PIL import Image, ImageDraw, ImageFilter
    size = fit["size"]
    font = _font(size)
    lh = line_height(size, spacing)
    block_h = lh * len(fit["lines"])
    h = block_h + 2 * pad
    shadow = Image.new("RGBA", (OUT_W, h), (0, 0, 0, 0))
    img = Image.new("RGBA", (OUT_W, h), (0, 0, 0, 0))
    sd, d = ImageDraw.Draw(shadow), ImageDraw.Draw(img)
    space = font.getlength(" ")
    asc = font.getmetrics()[0]
    for li, line in enumerate(fit["lines"]):
        widths = [font.getlength(w) for w, _ in line]
        total = sum(widths) + space * (len(line) - 1)
        x = (OUT_W - total) / 2
        y = pad + li * lh + (lh - (asc + font.getmetrics()[1])) / 2
        for (w, acc), ww in zip(line, widths):
            sd.text((x, y + 4), w, font=font, fill=(0, 0, 0, 170))
            d.text((x, y), w, font=font, fill=(*hex_rgb(accent if acc else "#FFFFFF"), 255))
            x += ww + space
    shadow = shadow.filter(ImageFilter.GaussianBlur(5))
    out = Image.alpha_composite(shadow, img)
    out.save(path)
    return h, block_h


# --------------------------------------------------------------- captions
_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        for k, ln in enumerate(lines):
            m = _TS.search(ln)
            if m:
                g = [int(x) for x in m.groups()]
                t0 = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 10 ** len(m.group(4))
                t1 = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 10 ** len(m.group(8))
                body = " ".join(" ".join(lines[k + 1:]).split())
                body = re.sub(r"<[^>]+>|\{[^}]*\}", "", body).strip()
                if body and t1 > t0:
                    cues.append((t0, t1, unicodedata.normalize("NFC", body)))
                break
    return cues


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_color(hexc: str) -> str:
    r, g, b = hex_rgb(hexc)
    return f"&H00{b:02X}{g:02X}{r:02X}"


def wrap_caption(text: str, size: int, min_size: int, max_lines: int = CAPTION_MAX_LINES,
                 max_w: int = TEXT_W, measure_factory=None) -> tuple[int, list[str]]:
    """Wrap a cue into <= max_lines (shrinking to ``min_size``). Returns (size, lines)."""
    words = text.split()
    mf = measure_factory or (lambda s: (lambda t, f=_font(s): f.getlength(t)))
    s = size
    while True:
        measure = mf(s)
        idx = wrap_balanced(words, max_w, measure)
        if len(idx) <= max_lines or s <= min_size:
            break
        s = max(s - 2, min_size)
    return s, [" ".join(words[j] for j in ln) for ln in idx]


def build_ass(cues: list[tuple[float, float, str]], size: int, min_size: int, top_y: int,
              color: str = "#FFFFFF", font_family: str = FONT_FAMILY, measure_factory=None) -> str:
    """ASS with explicit style on a 1080x1920 canvas; cues are top-anchored at ``top_y``."""
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{font_family},{size},{_ass_color(color)},&H00FFFFFF,&H00101010,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,5,2,8,{SAFE_X0},{OUT_W - SAFE_X1},{top_y},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    ev = []
    for t0, t1, body in cues:
        s, lines = wrap_caption(body, size, min_size, measure_factory=measure_factory)
        txt = "\\N".join(lines)
        if s != size:
            txt = f"{{\\fs{s}}}" + txt
        ev.append(f"Dialogue: 0,{_ass_time(t0)},{_ass_time(t1)},Cap,,0,0,0,,{txt}")
    return head + "\n".join(ev) + "\n"


# ----------------------------------------------------------------- layout
def compute_layout(title_lines: int, title_size: int, caption_size: int, box_max_h: int = BOX_MAX_H,
                   title_top: int = TITLE_TOP, title_spacing: float = TITLE_LINE_SPACING) -> dict[str, Any]:
    """Zone sizes. The stack (title + gap + box + gap + 2-line captions) must fit in title_top..1600;
    box_max_h is reduced when it would not."""
    title_h = title_lines * line_height(title_size, title_spacing)
    cap_h = int(round(CAPTION_MAX_LINES * caption_size * CAPTION_LINE_SPACING)) + 10  # + outline
    avail = CAPTION_BOTTOM_LIMIT - title_top - title_h - cap_h - 2 * GAP
    return {
        "title_top": title_top,
        "title": {"x": SAFE_X0, "y": title_top, "w": TEXT_W, "h": title_h},
        "box_max_h": max(2, min(box_max_h, avail) // 2 * 2),
        "caption_h": cap_h,
    }


def box_height(content_heights: list[int], box_max_h: int) -> int:
    """One box height for the whole clip: tallest shot need, even, capped; 0 shots -> 16:9 full width."""
    h = max(content_heights) if content_heights else int(OUT_W * 9 / 16)
    return int(min(max(h, 2), box_max_h) // 2 * 2)


def finish_layout(lay: dict[str, Any], box_h: int) -> dict[str, Any]:
    """Place the stack: centred vertically in title_top..CAPTION_BOTTOM_LIMIT (2-line caption space reserved)."""
    title_h, cap_h = lay["title"]["h"], lay["caption_h"]
    stack = title_h + GAP + box_h + GAP + cap_h
    offset = max(0, (CAPTION_BOTTOM_LIMIT - lay["title_top"] - stack) // 2)
    ty = lay["title_top"] + offset
    box_top = ty + title_h + GAP
    cap_top = box_top + box_h + GAP
    return {**lay, "title": {**lay["title"], "y": ty}, "box_top": box_top, "stack_h": stack,
            "box": {"x": 0, "y": box_top, "w": OUT_W, "h": box_h},
            "caption": {"x": SAFE_X0, "y": cap_top, "w": TEXT_W, "h": cap_h}}


def shot_window(bbox_full: list[float] | None, src_w: int, src_h: int, margin: float, zmax: float,
                box_max_h: int) -> dict[str, Any]:
    """Source window for one shot: width src_w/zoom centred on the content bbox (never cutting it).

    zoom = clamp(1/(bbox_w+2*margin), 1, zmax).  Window spans the full source height unless that would
    exceed box_max_h once scaled to 1080 wide (then it is centre-cropped vertically; only for tall sources).
    Returns window geometry in source px plus the scaled content height ``ch`` in the box.
    """
    if bbox_full is None:
        z, cx = 1.0, src_w / 2
    else:
        bw = bbox_full[1] - bbox_full[0] + 2 * margin
        z = float(min(max(1.0 / max(bw, 1e-6), 1.0), zmax))
        cx = (bbox_full[0] + bbox_full[1]) / 2 * src_w
    ww = int(round(src_w / z / 2)) * 2
    wx = int(round(max(0, min(cx - ww / 2, src_w - ww))))
    wh, wy, vcrop = src_h, 0, False
    if src_h * OUT_W / ww > box_max_h:
        wh = int(round(box_max_h * ww / OUT_W / 2)) * 2
        wy, vcrop = (src_h - wh) // 2, True
    ch = int(round(wh * OUT_W / ww / 2)) * 2
    return {"zoom": round(z, 3), "win_w": ww, "win_h": wh, "win_x": wx, "win_y": wy, "content_h": ch,
            "vertical_crop": vcrop}


def background_luma(frames) -> int:
    """Dominant background luma over sampled gray frames (0..255)."""
    import numpy as np
    sub = frames[:: max(1, len(frames) // 40)]
    hist = np.bincount((sub.astype(np.int16) >> 3).ravel(), minlength=32)
    return int(np.argmax(hist)) * 8 + 4


# ------------------------------------------------------------------- tool
class ShortsLayout(BaseTool):
    name = "shorts_layout"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "video_post"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = ["cmd:ffmpeg"]
    install_instructions = "FFmpeg with libass is required (subtitles filter). Pillow and numpy for text/analysis."
    agent_skills = ["ffmpeg"]
    capabilities = ["shorts_layout", "title_overlay", "burned_captions", "content_aware_crop"]

    input_schema = {
        "type": "object",
        "required": ["input_path", "output_path", "title"],
        "properties": {
            "input_path": {"type": "string", "description": "16:9 source clip (with audio)"},
            "output_path": {"type": "string"},
            "title": {"type": "string", "description": "Hook title (3-10 words, brief language)"},
            "accent_words": {"type": "array", "items": {"type": "string"},
                             "description": "Words/phrases of the title drawn in accent_color"},
            "captions_srt": {"type": "string", "description": "SRT burned under the content box"},
            "bg": {"type": "string", "enum": ["auto", "solid", "blur"], "default": "auto",
                   "description": "auto: solid if the source background is near-black, else blurred source"},
            "bg_color": {"type": "string", "default": DEFAULT_BG},
            "accent_color": {"type": "string", "default": DEFAULT_ACCENT},
            "max_zoom": {"type": "number", "default": 1.6, "minimum": 1.0,
                         "description": "Max zoom onto the content bbox (also limited by the box height cap)"},
            "title_font_size": {"type": "integer", "default": TITLE_SIZE},
            "caption_font_size": {"type": "integer", "default": CAPTION_SIZE},
            "caption_highlight_color": {"type": "string", "description": "Colour of the active caption (default white)"},
            "title_fade": {"type": "number", "default": 0.3, "description": "Title fade-in seconds (0 = off)"},
            "box_max_height": {"type": "integer", "default": BOX_MAX_H},
            "strategy": {"type": "string", "enum": ["auto", "content_aware", "face"], "default": "auto",
                         "description": "content_aware: per-shot bbox window. face: talking head, window centred on the face. "
                                        "auto: face if faces are found, else content_aware."},
            "scene_threshold": {"type": "number", "default": 0.3},
            "content_margin": {"type": "number", "default": 0.02},
            "crf": {"type": "integer", "default": 18},
            "still_time": {"type": "number", "description": "Render only one PNG frame at this time (mockups/QA)"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=4, ram_mb=2048, vram_mb=0, disk_mb=4000, network_required=False)
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["FFmpeg error"])
    resume_support = ResumeSupport.FROM_START
    idempotency_key_fields = ["input_path", "title", "accent_words", "captions_srt", "bg", "bg_color", "accent_color",
                              "max_zoom", "title_font_size", "caption_font_size", "caption_highlight_color",
                              "title_fade", "box_max_height", "strategy", "scene_threshold", "content_margin", "crf"]
    side_effects = ["writes the finished clip and <output>.layout.json"]
    user_visible_verification = [
        "Contact sheet the output: title readable, no on-screen text/numbers cut, captions inside 60..1020 and above y=1600",
        "Read <output>.layout.json for per-shot window/zoom decisions",
    ]

    # ---- helpers
    def _probe(self, path: Path) -> tuple[int, int, float, float]:
        import subprocess
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True)
        d = json.loads(out.stdout)
        s = d["streams"][0]
        n, _, den = s["r_frame_rate"].partition("/")
        fps = float(n) / float(den or 1)
        return int(s["width"]), int(s["height"]), fps, float(d["format"]["duration"])

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        from tools.video import _content_reframe as cr
        from lib.caption_size import clamp_caption_px, min_caption_px

        start = time.time()
        src = Path(inputs["input_path"])
        if not src.exists():
            return ToolResult(success=False, error=f"Input not found: {src}")
        title = (inputs.get("title") or "").strip()
        if not title:
            return ToolResult(success=False, error="title is required")
        out = Path(inputs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        src_w, src_h, src_fps, duration = self._probe(src)
        if src_w <= 0 or duration <= 0:
            return ToolResult(success=False, error="Could not probe input")
        fps = src_fps if 1 < src_fps <= 60 else 30.0
        accent = inputs.get("accent_color", DEFAULT_ACCENT)

        # --- text + zones
        fit = fit_title(title, inputs.get("accent_words"), int(inputs.get("title_font_size", TITLE_SIZE)))
        cap_size = clamp_caption_px(inputs.get("caption_font_size", CAPTION_SIZE), OUT_W, OUT_H)
        lay = compute_layout(len(fit["lines"]), fit["size"], cap_size, int(inputs.get("box_max_height", BOX_MAX_H)))
        box_max = lay["box_max_h"]
        zmax_h = box_max / (src_h * OUT_W / src_w)            # zoom at which the full-height window reaches the cap
        zmax = max(1.0, min(float(inputs.get("max_zoom", cr.MAX_ZOOM)), zmax_h))
        margin = float(inputs.get("content_margin", cr.CONTENT_MARGIN))

        # --- analysis
        frames, afps = cr.sample_gray_frames(str(src), duration)
        if len(frames) == 0:
            return ToolResult(success=False, error="Could not decode frames for content analysis")
        strategy = inputs.get("strategy") or "auto"
        face_cx = None
        if strategy in ("auto", "face"):
            face_cx = self._face_center(inputs, src, src_fps, duration)
            if face_cx is None and strategy == "face":
                strategy = "content_aware"
            elif face_cx is not None:
                strategy = "face"
            else:
                strategy = "content_aware"
        shots: list[dict] = []
        if strategy == "face":
            # tiny bbox => zoom = zmax: the window fills the box at its aspect, centred on the face
            win = shot_window([face_cx - 0.001, face_cx + 0.001], src_w, src_h, 0.0, zmax, box_max)
            shots = [{"start": 0.0, "end": round(duration, 3), "strategy": "face", "reason": "face-centred window", **win}]
        else:
            det = cr.detect_shots(str(src), duration, float(inputs.get("scene_threshold", cr.SCENE_THRESHOLD)))
            narrow_w = int(round(src_w / zmax))
            decs = cr.analyse_shots(frames, afps, det, duration, src_w, narrow_w, margin, zmax)
            for d in decs:
                w = shot_window(d.get("bbox_full"), src_w, src_h, margin, zmax, box_max)
                shots.append({"start": d["start"], "end": d["end"], "strategy": "content_aware",
                              "reason": d.get("reason"), "bbox_full": d.get("bbox_full"), **w})
            shots[-1]["end"] = round(duration, 3)
        box_h = box_height([s["content_h"] for s in shots], box_max)
        lay = finish_layout(lay, box_h)
        box_top = lay["box_top"]
        bg_color = inputs.get("bg_color", DEFAULT_BG)
        bg_mode = inputs.get("bg", "auto")
        bg_luma = background_luma(frames)
        if bg_mode == "auto":
            bg_mode = "solid" if bg_luma < NEAR_BLACK_LUMA else "blur"

        # --- assets
        tmp = Path(tempfile.mkdtemp(prefix="shorts_layout_"))
        title_png = tmp / "title.png"
        png_h, _ = render_title_png(fit, title_png, accent)
        pad = (png_h - lay["title"]["h"]) // 2
        sub_filter = ""
        if inputs.get("captions_srt"):
            cues = parse_srt(Path(inputs["captions_srt"]).read_text(encoding="utf-8-sig"))
            ass = tmp / "cap.ass"
            ass.write_text(build_ass(cues, cap_size, min_caption_px(OUT_W, OUT_H), lay["caption"]["y"],
                                     inputs.get("caption_highlight_color") or "#FFFFFF"), encoding="utf-8")
            esc = lambda p: str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            sub_filter = f",subtitles=filename='{esc(ass)}':fontsdir='{esc(FONT_PATH.parent)}'"

        # --- filter graph
        n = len(shots)
        fade = float(inputs.get("title_fade", 0.3))
        g: list[str] = []
        if bg_mode == "blur":
            labels = "[bgsrc]" + "".join(f"[in{i}]" for i in range(n))
            g.append(f"[0:v]split={n + 1}{labels}")
            g.append(f"[bgsrc]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
                     f"scale=108:192:flags=area,gblur=sigma=8,scale={OUT_W}:{OUT_H}:flags=bicubic,"
                     f"eq=brightness=-0.45:saturation=0.7,setsar=1,format=yuv420p[v0]")
        else:
            g.append(("[0:v]null[in0]" if n == 1 else f"[0:v]split={n}" + "".join(f"[in{i}]" for i in range(n))))
            g.append("[2:v]format=yuv420p[v0]")
        lift = ""
        if bg_mode != "blur":  # lift the source's blacks to the solid bg colour so the box edge is invisible
            r, gg, bb = (c / 255 for c in hex_rgb(bg_color))
            lift = f"curves=r='0/{r:.3f} 1/1':g='0/{gg:.3f} 1/1':b='0/{bb:.3f} 1/1',"
        for i, s in enumerate(shots):
            a, b = s["start"], s["end"]
            y = box_top + (box_h - s["content_h"]) // 2
            g.append(f"[in{i}]trim=start={a}:end={b},setpts=PTS-STARTPTS+{a}/TB,"
                     f"crop={s['win_w']}:{s['win_h']}:{s['win_x']}:{s['win_y']},{lift}"
                     f"scale={OUT_W}:{s['content_h']}:flags=lanczos,setsar=1[s{i}]")
            g.append(f"[v{i}][s{i}]overlay=0:{y}:eof_action=pass:enable='gte(t,{a})*lt(t,{b})'[v{i + 1}]")
        ti = "format=rgba" + (f",fade=t=in:st=0:d={fade}:alpha=1" if fade > 0 else "")
        g.append(f"[1:v]{ti}[ti]")
        g.append(f"[v{n}][ti]overlay=0:{lay['title']['y'] - pad}:format=auto{sub_filter},format=yuv420p[vout]")

        cmd = ["ffmpeg", "-y", "-nostdin", "-i", str(src),
               "-loop", "1", "-framerate", f"{fps}", "-t", f"{duration}", "-i", str(title_png)]
        if bg_mode != "blur":
            cmd += ["-f", "lavfi", "-i", f"color=c={bg_color.replace('#', '0x')}:s={OUT_W}x{OUT_H}:r={fps}:d={duration}"]
        cmd += ["-filter_complex", ";".join(g), "-map", "[vout]"]
        still = inputs.get("still_time")
        if still is not None:
            cmd += ["-ss", f"{float(still)}", "-frames:v", "1", str(out)]
        else:
            cmd += ["-map", "0:a?", "-c:v", "libx264", "-profile:v", "high", "-crf", str(inputs.get("crf", 18)),
                    "-preset", "fast", "-pix_fmt", "yuv420p", "-r", f"{fps}", "-c:a", "copy",
                    "-t", f"{duration}", "-movflags", "+faststart", str(out)]
        try:
            self.run_command(cmd, timeout=1800)
        except Exception as e:
            return ToolResult(success=False, error=f"FFmpeg render failed: {e}")

        report = {"source": f"{src_w}x{src_h}", "output": f"{OUT_W}x{OUT_H}", "fps": fps, "strategy": strategy,
                  "bg": bg_mode, "bg_luma": bg_luma, "title": {"size": fit["size"], "lines": [" ".join(w for w, _ in ln) for ln in fit["lines"]],
                                                                  "truncated": fit["truncated"]},
                  "caption_size": cap_size, "zones": {k: lay[k] for k in ("title", "box", "caption")},
                  "max_zoom": zmax, "shots": shots}
        rp = Path(str(out) + ".layout.json")
        rp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return ToolResult(success=True, data={"output": str(out), "report": str(rp), **{k: report[k] for k in ("strategy", "bg", "zones")}},
                          artifacts=[str(out), str(rp)], duration_seconds=round(time.time() - start, 2))

    def _face_center(self, inputs, src: Path, fps: float, duration: float) -> float | None:
        """Mean face centre x (fraction) when faces appear in enough samples, else None."""
        try:
            from tools.video.auto_reframe import AutoReframe
            faces = AutoReframe()._get_face_data(inputs, src, fps)
        except Exception:
            return None
        if len(faces) < max(3, 0.3 * duration * float(inputs.get("sample_fps", 5))):
            return None
        xs = [f["bbox"]["x"] + f["bbox"]["width"] / 2 for f in faces]
        return float(sum(xs) / len(xs))

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 60.0
