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

import difflib
import hashlib
import json
import math
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
CAPTION_FLOOR_TOLERANCE = 1.02
CAPTION_BOTTOM_LIMIT = 1600          # bottom 300 px belong to Shorts UI
DEFAULT_ACCENT = "#F5C542"
DEFAULT_BG = "#0B0D12"
NEAR_BLACK_LUMA = 40

# ---- text effects (text_fx: none | subtle | pop)
TEXT_FX_LEVELS = ("none", "subtle", "pop")
TITLE_STAGGER = 0.08                 # s between title lines
TITLE_SLIDE_PX = 24                  # slide-up distance
TITLE_ENTER = 0.30                   # slide + fade duration
BAR_T0, BAR_T1 = 0.45, 0.85          # accent bar sweep (pop)
BAR_H = 8
POP_SCALE = 112                      # % at the peak of the word pop
POP_MS = 140                         # total pop duration (up 70 + down 70)
CUE_FADE_MS = 120
RESEG_MIN_DUR, RESEG_MAX_DUR = 0.8, 4.5   # caption cue length bounds (s)
RESEG_PAUSE = 0.25                        # word gap that counts as a pause
RESEG_COMPOUND_GAP = 0.08                 # gap below which two syllables are treated as one compound
# small hints for the compound / dangling-word heuristics (the timing gap rule does the general work)
COMPOUND_PAIRS = {("nguyên", "tử"), ("điện", "trường"), ("từ", "trường"), ("khoa", "học"), ("nhà", "khoa"),
                  ("vật", "chất"), ("phản", "vật"), ("hạt", "nhân"), ("năng", "lượng"), ("vũ", "trụ"),
                  ("thí", "nghiệm"), ("nhiệt", "độ"), ("gia", "tốc"), ("lực", "hấp"), ("hấp", "dẫn"),
                  ("trọng", "lực"), ("điện", "tử"), ("proton", "phản")}
DANGLING_END = {"nhưng", "và", "thì", "là", "của", "các", "những", "một", "này", "để", "nên", "vì", "nếu", "khi",
                "mà", "với", "trong", "ở", "cho", "như", "hay", "hoặc", "rằng", "có", "không", "rất", "sẽ", "đã"}
RESEG_FILL_GAP = 0.15                     # gaps shorter than this are closed by extending the previous cue
UNIT_WORDS = {"tấn", "tạ", "yến", "kg", "g", "gam", "gram", "mg", "km", "m", "cm", "mm", "mét", "triệu", "tỷ", "tỉ",
              "nghìn", "ngàn", "trăm", "chục", "độ", "lần", "năm", "tháng", "ngày", "giờ", "phút", "giây", "kwh",
              "mw", "gw", "kw", "j", "ev", "mev", "gev", "tev", "hz", "đồng", "usd", "đô", "phần", "trăm"}

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


def render_title_lines(fit: dict, tmp: Path, accent: str = DEFAULT_ACCENT, spacing: float = TITLE_LINE_SPACING,
                       pad: int = 16) -> dict[str, Any]:
    """Render each title line to its own 1080-wide transparent PNG (same look as ``render_title_png``).

    Line ``i`` sits at y = title_y - pad + i*lh.  Also returns the accent spans (x0, x1, baseline_y) per line,
    in the PNG's coordinates, used to place the marker bars.
    """
    from PIL import Image, ImageDraw, ImageFilter
    size = fit["size"]
    font = _font(size)
    lh = line_height(size, spacing)
    h = lh + 2 * pad
    space = font.getlength(" ")
    asc, desc = font.getmetrics()
    lines = []
    for li, line in enumerate(fit["lines"]):
        shadow = Image.new("RGBA", (OUT_W, h), (0, 0, 0, 0))
        img = Image.new("RGBA", (OUT_W, h), (0, 0, 0, 0))
        sd, d = ImageDraw.Draw(shadow), ImageDraw.Draw(img)
        widths = [font.getlength(w) for w, _ in line]
        total = sum(widths) + space * (len(line) - 1)
        x = (OUT_W - total) / 2
        y = pad + (lh - (asc + desc)) / 2
        spans: list[list[float]] = []
        for k, ((w, acc), ww) in enumerate(zip(line, widths)):
            sd.text((x, y + 4), w, font=font, fill=(0, 0, 0, 170))
            d.text((x, y), w, font=font, fill=(*hex_rgb(accent if acc else "#FFFFFF"), 255))
            if acc:
                if spans and spans[-1][2] == k - 1:
                    spans[-1][1], spans[-1][2] = x + ww, k
                else:
                    spans.append([x, x + ww, k])
            x += ww + space
        out = Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(5)), img)
        path = tmp / f"title_line{li}.png"
        out.save(path)
        lines.append({"path": path, "y_off": li * lh - pad, "baseline": y + asc,
                      "spans": [(a, b) for a, b, _ in spans]})
    return {"lines": lines, "png_h": h, "line_h": lh}


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
        m = mf(s)
        idx = wrap_balanced(words, max_w, m)
        if (len(idx) <= max_lines and all(m(" ".join(words[j] for j in ln)) <= max_w for ln in idx)) or s <= min_size:
            break
        s = max(s - 2, min_size)
    if len(idx) > max_lines:
        # at the floor: tolerate up to +2 % width (<= ~10 px past the safe band) before a 3rd line
        alt = wrap_balanced(words, max_w * CAPTION_FLOOR_TOLERANCE, mf(s))
        if len(alt) <= max_lines:
            idx = alt
    return s, [" ".join(words[j] for j in ln) for ln in idx]


_PUNCT_BREAK = re.compile(r"[.!?…]$")
_SOFT_BREAK = re.compile(r"[,;:–—-]$")


def split_cue(text: str, size: int, max_lines: int = CAPTION_MAX_LINES, max_w: int = TEXT_W,
              measure_factory=None) -> list[str]:
    """Split a cue into the fewest sub-cues that each fit ``max_lines`` balanced lines at ``size``.

    Breaks land on word boundaries, preferring sentence punctuation, then commas, then balanced lengths.
    A single word that cannot fit is left alone (the caller shrinks it).
    """
    words = text.split()
    mf = measure_factory or (lambda s: (lambda t, f=_font(s): f.getlength(t)))
    measure = mf(size)

    def fits(i: int, j: int) -> bool:
        seg = words[i:j]
        return j - i == 1 or len(wrap_balanced(seg, max_w, measure)) <= max_lines and \
            len(_greedy(seg, max_w, measure)) <= max_lines

    n = len(words)
    if n == 0 or fits(0, n):
        return [text] if n else []
    # DP over suffixes: cost = (pieces, -break bonus, sum of squared piece lengths)
    best: dict[int, tuple] = {n: ((0, 0, 0), None)}
    for i in range(n - 1, -1, -1):
        cands = []
        for j in range(i + 1, n + 1):
            if not fits(i, j):
                if j - i > 1:
                    break
                continue
            c, _ = best[j]
            last = words[j - 1]
            bonus = 0 if j == n else (6 if _PUNCT_BREAK.search(last) else 3 if _SOFT_BREAK.search(last) else 0)
            ln = len(" ".join(words[i:j]))
            cands.append(((c[0] + 1, c[1] - bonus, c[2] + ln * ln), j))
        best[i] = min(cands)
    out, i = [], 0
    while i < n:
        j = best[i][1]
        out.append(" ".join(words[i:j]))
        i = j
    return out


def split_cues(cues: list[tuple[float, float, str]], size: int, measure_factory=None) -> list[tuple[float, float, str]]:
    """Expand cues into sub-cues; time is shared proportionally to character count (contiguous, same span)."""
    out = []
    for t0, t1, body in cues:
        parts = split_cue(body, size, measure_factory=measure_factory)
        if len(parts) <= 1:
            out.append((t0, t1, body))
            continue
        total = sum(len(p) for p in parts)
        acc = 0
        for k, p in enumerate(parts):
            a = t0 + (t1 - t0) * acc / total
            acc += len(p)
            b = t1 if k == len(parts) - 1 else t0 + (t1 - t0) * acc / total
            out.append((a, b, p))
    return out


_NUM_RE = re.compile(r"[≈~±<>≥≤+]*\d[\d.,:/\-]*[%‰]?")


def _strip_punct(w: str) -> str:
    return w.strip(".,;:!?…\"'“”‘’()[]{}")


def is_number_token(w: str) -> bool:
    """92, 2023, 50, 3,5, ≈100, 12%, %"""
    t = _strip_punct(w)
    return t == "%" or bool(_NUM_RE.fullmatch(t))


def gold_flags(words: list[str]) -> list[bool]:
    """Numbers stay gold for the whole cue; a unit word right after a number (``1 tấn``, ``50 triệu kWh``) too."""
    flags: list[bool] = []
    for i, w in enumerate(words):
        if is_number_token(w):
            flags.append(True)
        else:
            flags.append(bool(i and flags[i - 1] and _norm_word(w) in UNIT_WORDS))
    return flags


def proportional_times(words: list[str], t0: float, t1: float) -> list[tuple[float, float]]:
    """Contiguous word times inside [t0, t1], proportional to character count."""
    total = sum(len(w) for w in words) or 1
    out, acc = [], 0
    for w in words:
        a = t0 + (t1 - t0) * acc / total
        acc += len(w)
        out.append((a, t0 + (t1 - t0) * acc / total))
    return out


def _fold(w: str) -> str:
    """Accent/case/punctuation-insensitive key for aligning SRT words with ASR words."""
    w = unicodedata.normalize("NFD", w.replace("đ", "d").replace("Đ", "D"))
    w = "".join(c for c in w if unicodedata.category(c) != "Mn")
    return re.sub(r"[\W_]+", "", w.casefold())


def normalize_word_list(words: list[dict]) -> list[dict]:
    """ASR words -> [{word, start, end}] with multi-token entries split evenly and empties dropped."""
    out = []
    for w in words:
        parts = str(w["word"]).split()
        if not parts:
            continue
        t0, t1 = float(w["start"]), float(w["end"])
        for k, (a, b) in enumerate(proportional_times(parts, t0, max(t1, t0))):
            out.append({"word": parts[k], "start": a, "end": b})
    return out


def align_words(cues: list[tuple[float, float, str]], asr: list[dict]) -> tuple[list[list[tuple[float, float]]], dict]:
    """Per-cue word times for the SRT words, taken from ASR words aligned in sequence (difflib).

    Equal runs copy the ASR times; replaced runs share the ASR span by character count; words with no ASR
    counterpart are interpolated between timed neighbours (proportional fallback).  Everything is clamped to
    its cue.  Returns (times per cue, {"matched": ratio, "words": n}).
    """
    asr = normalize_word_list(asr)
    flat = [(ci, w) for ci, (_, _, body) in enumerate(cues) for w in body.split()]
    a_keys = [_fold(w) for _, w in flat]
    b_keys = [_fold(w["word"]) for w in asr]
    times: list[tuple[float, float] | None] = [None] * len(flat)
    equal = 0
    sm = difflib.SequenceMatcher(None, a_keys, b_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            equal += i2 - i1
            for k in range(i2 - i1):
                times[i1 + k] = (asr[j1 + k]["start"], asr[j1 + k]["end"])
        elif tag == "replace":
            span = proportional_times([w for _, w in flat[i1:i2]], asr[j1]["start"], asr[j2 - 1]["end"])
            times[i1:i2] = span
    out: list[list[tuple[float, float]]] = []
    pos = 0
    for ci, (t0, t1, body) in enumerate(cues):
        words = body.split()
        ts = times[pos:pos + len(words)]
        pos += len(words)
        i = 0
        while i < len(words):                       # interpolate runs of untimed words
            if ts[i] is not None:
                i += 1
                continue
            j = i
            while j < len(words) and ts[j] is None:
                j += 1
            lo = ts[i - 1][1] if i else t0
            hi = ts[j][0] if j < len(words) else t1
            lo, hi = (lo, hi) if hi > lo else (lo, lo + 0.05 * (j - i))
            ts[i:j] = proportional_times(words[i:j], lo, hi)
            i = j
        res, prev = [], t0
        for a, b in ts:                             # clamp into the cue, keep monotonic
            a = min(max(a, prev, t0), t1)
            b = min(max(b, a + 0.02), max(t1, a + 0.02))
            res.append((a, b))
            prev = a
        out.append(res)
    n = len(flat)
    return out, {"matched": round(equal / n, 3) if n else 0.0, "words": n}


_WHISPER_MODELS: dict[str, Any] = {}


def whisper_word_times(media: Path, language: str | None = None, model_size: str = "small",
                       cache_dir: Path | None = None) -> list[dict] | None:
    """faster-whisper word timestamps for ``media`` audio (CPU, int8), cached on disk.  None if unavailable."""
    import subprocess
    try:
        st = media.stat()
        key = hashlib.sha1(f"{media.resolve()}|{st.st_size}|{int(st.st_mtime)}|{model_size}|{language}".encode()).hexdigest()[:20]
        cdir = cache_dir or Path(tempfile.gettempdir()) / "shorts_layout_words"
        cdir.mkdir(parents=True, exist_ok=True)
        cache = cdir / f"{key}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        from faster_whisper import WhisperModel
        wav = cdir / f"{key}.wav"
        subprocess.run(["ffmpeg", "-y", "-nostdin", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000",
                        str(wav)], check=True, capture_output=True)
        model = _WHISPER_MODELS.get(model_size)
        if model is None:
            model = _WHISPER_MODELS[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
        segs, _info = model.transcribe(str(wav), word_timestamps=True, language=language, condition_on_previous_text=False)
        words = [{"word": w.word.strip(), "start": float(w.start), "end": float(w.end)}
                 for sg in segs for w in (sg.words or []) if w.word.strip()]
        wav.unlink(missing_ok=True)
        if not words:
            return None
        cache.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        return words
    except Exception:
        return None


def resolve_word_times(cues: list[tuple[float, float, str]], words: list[dict] | None,
                       source: str) -> tuple[list[list[tuple[float, float]]], dict]:
    """Word times per cue + report {source, matched, words}.  Falls back to proportional timing when the
    ASR words barely match the SRT (< 25 % equal words) or are missing."""
    n = sum(len(b.split()) for _, _, b in cues)
    if words:
        times, st = align_words(cues, words)
        if st["matched"] >= 0.25:
            return times, {"source": source, **st}
        st = {**st, "rejected": source}
    else:
        st = {"matched": 0.0, "words": n}
    return [proportional_times(b.split(), t0, t1) for t0, t1, b in cues], {"source": "proportional", **st}


def _ends_with(w: str, rx) -> bool:
    return bool(rx.search(w.rstrip("\"'”’)]}")))


def resegment_cues(words: list[tuple[str, float, float]], size: int, measure_factory=None,
                   max_w: int | None = None, min_dur: float = RESEG_MIN_DUR, max_dur: float = RESEG_MAX_DUR,
                   pause: float = RESEG_PAUSE) -> list[tuple[float, float, str, list[tuple[float, float]]]]:
    """Re-cut a word stream [(word, start, end)] into caption cues (text, times, per-word times).

    Dynamic programme over break positions.  A cue must fit <= 2 balanced lines within ``max_w`` and last at
    most ``max_dur`` (a single word is always allowed).  Costs: every cue 1; a break that is not at punctuation
    or a pause 2; a break between two syllables spoken < 80 ms apart with no punctuation 8 (compound words);
    a sentence end swallowed inside a cue 3 (comma / pause inside: 0.5); a cue shorter than ``min_dur`` 5.
    Cue start / end = first / last word time; gaps < 150 ms are closed by extending the previous cue.
    """
    n = len(words)
    if n == 0:
        return []
    mf = measure_factory or (lambda s_: (lambda t, f=_font(s_): f.getlength(t)))
    measure = mf(size)
    limit = max_w if max_w is not None else TEXT_W
    texts = [w for w, _, _ in words]

    def kind(k: int) -> str:
        """Nature of the boundary after word k (0-based, k < n-1)."""
        w = texts[k]
        if _ends_with(w, _PUNCT_BREAK):
            return "sentence"
        if _ends_with(w, _SOFT_BREAK):
            return "soft"
        gap = words[k + 1][1] - words[k][2]
        if (_norm_word(w), _norm_word(texts[k + 1])) in COMPOUND_PAIRS:
            return "compound"
        if _norm_word(w) in DANGLING_END and gap < pause:
            return "dangling"
        if gap >= pause:
            return "pause"
        if gap < RESEG_COMPOUND_GAP:
            return "tight"
        return "plain"

    kinds = [kind(k) for k in range(n - 1)]
    brk_cost = {"sentence": 0.0, "soft": 0.5, "pause": 1.0, "plain": 2.0, "compound": 8.0, "tight": 5.0, "dangling": 4.0}
    in_cost = {"sentence": 3.0, "soft": 0.5, "pause": 0.5, "plain": 0.0, "compound": 0.0, "tight": 0.0, "dangling": 0.0}

    def fits(i: int, j: int) -> bool:
        seg = texts[i:j]
        if j - i == 1:
            return True
        return len(wrap_balanced(seg, limit, measure)) <= CAPTION_MAX_LINES and \
            len(_greedy(seg, limit, measure)) <= CAPTION_MAX_LINES

    INF = float("inf")
    best = [INF] * (n + 1)
    prev = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        for i in range(j - 1, max(-1, j - 20), -1):
            if best[i] == INF:
                continue
            if j - i > 1 and (words[j - 1][2] - words[i][1] > max_dur or not fits(i, j)):
                break                                   # longer segments only get worse
            dur = words[j - 1][2] - words[i][1]
            c = 1.0 + (5.0 if dur < min_dur else 0.0) + sum(in_cost[kinds[k]] for k in range(i, j - 1))
            if j < n:
                c += brk_cost[kinds[j - 1]]
            if best[i] + c < best[j]:
                best[j], prev[j] = best[i] + c, i
    cuts, j = [], n
    while j > 0:
        cuts.append((prev[j], j))
        j = prev[j]
    cuts.reverse()
    out = []
    for i, j in cuts:
        wt = [(a, b) for _, a, b in words[i:j]]
        out.append([wt[0][0], wt[-1][1], " ".join(texts[i:j]), wt])
    for a, b in zip(out, out[1:]):
        if 0 < b[0] - a[1] < RESEG_FILL_GAP:
            a[1] = b[0]
    return [tuple(c) for c in out]


def _ass_head(size: int, top_y: int, color: str, font_family: str) -> str:
    return (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{font_family},{size},{_ass_color(color)},&H00FFFFFF,&H00101010,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,5,2,8,{SAFE_X0},{OUT_W - SAFE_X1},{top_y},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def title_bar_events(bars: list[dict], accent: str, end: float) -> list[str]:
    """Gold marker bars that sweep left->right (BAR_T0..BAR_T1) under the accent words, then stay."""
    ev = []
    t1 = int(round((BAR_T1 - BAR_T0) * 1000))
    for b in bars:
        w = max(2, int(round(b["x1"] - b["x0"])))
        ev.append(f"Dialogue: 1,{_ass_time(BAR_T0)},{_ass_time(end)},Cap,,0,0,0,,"
                  f"{{\\an7\\pos({int(round(b['x0']))},{int(round(b['y']))})\\bord0\\shad0\\1c{_ass_color(accent)}&"
                  f"\\fscx1\\t(0,{t1},0.5,\\fscx100)\\p1}}m 0 0 l {w} 0 l {w} {BAR_H} l 0 {BAR_H}")
    return ev


def build_ass(cues: list[tuple[float, float, str]], size: int, min_size: int, top_y: int,
              color: str = "#FFFFFF", font_family: str = FONT_FAMILY, measure_factory=None,
              text_fx: str = "none", word_times: list[list[tuple[float, float]]] | None = None,
              accent: str = DEFAULT_ACCENT, title_bars: list[dict] | None = None,
              duration: float = 0.0) -> str:
    """ASS with explicit style on a 1080x1920 canvas; cues are top-anchored at ``top_y``.

    ``text_fx`` "none": static cues (the original output).  "subtle": per-word colour (active word gold,
    numbers gold) + 120 ms cue fades.  "pop": the same plus a 100->112->100 % scale pop of the active word.
    ``word_times`` = per-cue [(start, end)] per word (default: proportional); ``title_bars`` = accent marker
    bars [{x0, x1, y}] (pop only).
    """
    head = _ass_head(size, top_y, color, font_family)
    ev = []
    if text_fx not in ("subtle", "pop"):
        for t0, t1, body in split_cues(cues, size, measure_factory):
            s, lines = wrap_caption(body, size, min_size, measure_factory=measure_factory)
            txt = "\\N".join(lines)
            if s != size:
                txt = f"{{\\fs{s}}}" + txt
            ev.append(f"Dialogue: 0,{_ass_time(t0)},{_ass_time(t1)},Cap,,0,0,0,,{txt}")
        return head + "\n".join(ev) + "\n"

    pop = text_fx == "pop"
    mf = measure_factory or (lambda s_: (lambda t, f=_font(s_): f.getlength(t)))
    base_c, gold_c = _ass_color(color) + "&", _ass_color(accent) + "&"
    for ci, (t0, t1, body) in enumerate(cues):
        words = body.split()
        if not words:
            continue
        wt = word_times[ci] if word_times else proportional_times(words, t0, t1)
        m = mf(size)
        # a word popping to 112 % widens its line by 12 % of its width: reserve that in the wrap limit
        extra = int(math.ceil((POP_SCALE - 100) / 100 * max(m(w) for w in words))) if pop else 0
        limit = TEXT_W - extra
        parts = split_cue(body, size, max_w=limit, measure_factory=measure_factory)
        flags = gold_flags(words)
        idx = 0
        spans = []
        for k, part in enumerate(parts):
            n = len(part.split())
            spans.append((idx, idx + n))
            idx += n
        for k, (i0, i1) in enumerate(spans):
            first, last = wt[i0][0], wt[i1 - 1][1]
            ev0 = t0 if k == 0 else (wt[i0 - 1][1] + first) / 2
            ev1 = t1 if k == len(spans) - 1 else (last + wt[i1][0]) / 2
            ev0 = round(min(ev0, first) if k == 0 else ev0, 2)
            ev1 = round(max(ev1, ev0 + 0.1), 2)
            sz, lines = wrap_caption(parts[k], size, min_size, max_w=limit, measure_factory=measure_factory)
            dur_ms = int((ev1 - ev0) * 1000)
            fade = min(CUE_FADE_MS, max(dur_ms // 3, 0))
            out_lines, wi = [], i0
            for ln in lines:
                toks = []
                for w in ln.split():
                    a = max(0, int(round((wt[wi][0] - ev0) * 1000)))
                    nxt = wt[wi + 1][0] if wi + 1 < i1 else None
                    b = int(round(((nxt if nxt is not None else wt[wi][1]) - ev0) * 1000))
                    b = max(b, a + 2)
                    if flags[wi]:
                        tags = f"\\1c{gold_c}"
                    else:
                        tags = f"\\1c{base_c}\\t({a},{a + 1},\\1c{gold_c})"
                        if nxt is not None:
                            tags += f"\\t({b},{b + 1},\\1c{base_c})"
                    if pop:
                        half = POP_MS // 2
                        tags += (f"\\fscx100\\fscy100\\t({a},{a + half},\\fscx{POP_SCALE}\\fscy{POP_SCALE})"
                                 f"\\t({a + half},{a + POP_MS},\\fscx100\\fscy100)")
                    toks.append(f"{{{tags}}}{w}")
                    wi += 1
                out_lines.append(" ".join(toks))
            pre = f"\\fad({fade},{fade})" + (f"\\fs{sz}" if sz != size else "")
            ev.append(f"Dialogue: 0,{_ass_time(ev0)},{_ass_time(ev1)},Cap,,0,0,0,,{{{pre}}}" + "\\N".join(out_lines))
    if pop and title_bars:
        ev += title_bar_events(title_bars, accent, max(duration, BAR_T1 + 0.5))
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
    version = "0.2.0"
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
            "title_fade": {"type": "number", "default": 0.3, "description": "Title fade-in seconds (0 = off); used when text_fx is none"},
            "text_fx": {"type": "string", "enum": list(TEXT_FX_LEVELS), "default": "pop",
                        "description": "Animated text. none: static title + captions. subtle: staggered title slide-up/fade, "
                                       "word-by-word caption colour (active word gold, numbers gold), 120 ms cue fades. "
                                       "pop: subtle + gold marker bar sweeping under the accent words + active word scale 100>112>100 %."},
            "resegment": {"type": "boolean", "default": True,
                          "description": "Re-cut the SRT text into phrase-based caption cues (sentence end > , ; : > pauses; "
                                         "keeps 2-syllable compounds together; <=2 lines; 0.8-4.5 s) using the word times. "
                                         "False keeps the SRT cues as given (long ones are still split to fit)."},
            "words_json": {"description": "Word timestamps [{word, start, end}] (clip-relative seconds) or a path to such a JSON "
                                          "file, e.g. from the transcriber. Without it faster-whisper is run on the clip audio; "
                                          "if that is unavailable timing is proportional to character count.",
                           "anyOf": [{"type": "array", "items": {"type": "object"}}, {"type": "string"}]},
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
                              "title_fade", "text_fx", "resegment", "words_json", "box_max_height", "strategy", "scene_threshold", "content_margin", "crf"]
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
        fx = inputs.get("text_fx", "pop")
        if fx not in TEXT_FX_LEVELS:
            return ToolResult(success=False, error=f"text_fx must be one of {TEXT_FX_LEVELS}")
        tmp = Path(tempfile.mkdtemp(prefix="shorts_layout_"))
        title_pngs: list[tuple[Path, int]] = []          # (png, absolute y)
        title_bars: list[dict] = []
        if fx == "none":
            title_png = tmp / "title.png"
            png_h, _ = render_title_png(fit, title_png, accent)
            title_pngs.append((title_png, lay["title"]["y"] - (png_h - lay["title"]["h"]) // 2))
        else:
            tl = render_title_lines(fit, tmp, accent)
            for ln in tl["lines"]:
                y0 = lay["title"]["y"] + ln["y_off"]
                title_pngs.append((ln["path"], y0))
                for x0, x1 in ln["spans"]:
                    title_bars.append({"x0": x0, "x1": x1, "y": y0 + ln["baseline"] + max(8, round(fit["size"] * 0.2))})
        sub_filter = ""
        timing: dict[str, Any] = {"source": "none"}
        caption_info: dict[str, Any] = {"resegment": False, "cues": 0}
        if inputs.get("captions_srt") or (fx == "pop" and title_bars):
            cues = parse_srt(Path(inputs["captions_srt"]).read_text(encoding="utf-8-sig")) if inputs.get("captions_srt") else []
            word_times = None
            reseg = bool(inputs.get("resegment", True))
            n_srt = len(cues)
            if (fx != "none" or reseg) and cues:
                wj, src_name = inputs.get("words_json"), "words_json"
                if isinstance(wj, str):
                    wj = json.loads(Path(wj).read_text(encoding="utf-8"))
                    wj = wj.get("words", wj) if isinstance(wj, dict) else wj
                if not wj:
                    wj, src_name = whisper_word_times(src, inputs.get("language")), "whisper"
                word_times, timing = resolve_word_times(cues, wj, src_name)
                if reseg:
                    stream = [(w, a, b) for (_, _, body), wts in zip(cues, word_times)
                              for w, (a, b) in zip(body.split(), wts)]
                    m_ = lambda t, f=_font(cap_size): f.getlength(t)
                    extra = math.ceil((POP_SCALE - 100) / 100 * max(m_(w) for w, _, _ in stream)) if fx == "pop" else 0
                    new = resegment_cues(stream, cap_size, max_w=TEXT_W - extra)
                    cues = [(c[0], c[1], c[2]) for c in new]
                    word_times = [c[3] for c in new]
                if fx == "none":
                    word_times = None
            caption_info = {"resegment": reseg and bool(word_times), "cues_in": n_srt, "cues": len(cues),
                            "timing_source": timing["source"]}
            ass = tmp / "cap.ass"
            ass.write_text(build_ass(cues, cap_size, min_caption_px(OUT_W, OUT_H), lay["caption"]["y"],
                                     inputs.get("caption_highlight_color") or "#FFFFFF", text_fx=fx,
                                     word_times=word_times, accent=accent, title_bars=title_bars,
                                     duration=duration), encoding="utf-8")
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
            g.append(f"[{1 + len(title_pngs)}:v]format=yuv420p[v0]")
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
        if fx == "none":
            ti = "format=rgba" + (f",fade=t=in:st=0:d={fade}:alpha=1" if fade > 0 else "")
            g.append(f"[1:v]{ti}[ti]")
            g.append(f"[v{n}][ti]overlay=0:{title_pngs[0][1]}:format=auto{sub_filter},format=yuv420p[vout]")
        else:
            prev = f"v{n}"
            for li, (_, ty) in enumerate(title_pngs):
                st = round(li * TITLE_STAGGER, 3)
                g.append(f"[{1 + li}:v]format=rgba,fade=t=in:st={st}:d={TITLE_ENTER}:alpha=1[tl{li}]")
                # cubic ease-out slide up TITLE_SLIDE_PX over TITLE_ENTER
                yexp = f"{ty}+{TITLE_SLIDE_PX}*pow(1-clip((t-{st})/{TITLE_ENTER},0,1),3)"
                last = li == len(title_pngs) - 1
                g.append(f"[{prev}][tl{li}]overlay=x=0:y='{yexp}':eval=frame:format=auto"
                         + (f"{sub_filter},format=yuv420p[vout]" if last else f"[o{li}]"))
                prev = f"o{li}"

        cmd = ["ffmpeg", "-y", "-nostdin", "-i", str(src),
               ]
        for png, _ in title_pngs:
            cmd += ["-loop", "1", "-framerate", f"{fps}", "-t", f"{duration}", "-i", str(png)]
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
                  "text_fx": fx, "word_timing": timing, "captions": caption_info, "caption_size": cap_size, "zones": {k: lay[k] for k in ("title", "box", "caption")},
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
