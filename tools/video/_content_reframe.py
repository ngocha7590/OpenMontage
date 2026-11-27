"""Content-aware reframing helpers (16:9 graphics -> 9:16).

Pure numpy logic (bbox detection + per-shot decision) plus ffmpeg filter
builders. Used by ``auto_reframe`` strategies ``content_aware`` / ``fit_blur``.

Why: face tracking is meaningless on motion graphics, slides, screen
recordings and wide shots.  A blind centre crop chops text and numbers.  Here
we measure where the content actually is, per shot, and either crop a window
centred on it (only when the whole content fits) or fall back to a full-width
fit over a blurred copy of the video.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

import numpy as np

# ---------------------------------------------------------------- thresholds
ANALYSIS_W = 480            # analysis frame width (gray)
DIFF_THRESHOLD = 30         # luma distance from background that counts as content
EDGE_THRESHOLD = 28         # gradient magnitude that counts as content (thin text)
MIN_COL_FRACTION = 0.012    # a column needs this fraction of rows "on" to count
MIN_CONTENT_COLS = 0.02     # < 2 % of width as content => treat frame as empty
BBOX_PERCENTILE = 5         # percentile used for left/right edge over samples
CONTENT_MARGIN = 0.025      # margin (fraction of source width) each side of bbox
SCENE_THRESHOLD = 0.30      # ffmpeg scene score
MIN_SHOT_SECONDS = 0.8      # shots shorter than this are merged into neighbours
PAN_MIN_TRAVEL = 0.30       # pan only if centre moves > 30 % of window width


# ------------------------------------------------------------- bbox (numpy)
def content_bbox_x(gray: np.ndarray) -> tuple[float, float] | None:
    """Horizontal extent of content in a gray frame, as fractions (x0, x1).

    Content = pixels clearly different from the dominant background luma, plus
    strong gradients so thin text on a near-background colour still counts.
    Returns None for an empty frame.
    """
    g = gray.astype(np.int16)
    h, w = g.shape
    hist = np.bincount((g >> 3).ravel(), minlength=32)
    bg = int(np.argmax(hist)) * 8 + 4
    mask = np.abs(g - bg) > DIFF_THRESHOLD
    gx = np.abs(np.diff(g, axis=1, prepend=g[:, :1]))
    gy = np.abs(np.diff(g, axis=0, prepend=g[:1, :]))
    mask |= np.maximum(gx, gy) > EDGE_THRESHOLD
    cols = mask.sum(axis=0) >= max(2, int(round(MIN_COL_FRACTION * h)))
    idx = np.flatnonzero(cols)
    if idx.size == 0 or idx.size < MIN_CONTENT_COLS * w:
        return None
    return float(idx[0]) / w, float(idx[-1] + 1) / w


def shot_bbox(samples: list[tuple[float, float] | None]) -> tuple[float, float] | None:
    """Percentile bbox over per-frame extents (covers motion inside a shot)."""
    valid = [s for s in samples if s is not None]
    if not valid:
        return None
    xs0 = np.array([s[0] for s in valid])
    xs1 = np.array([s[1] for s in valid])
    return float(np.percentile(xs0, BBOX_PERCENTILE)), float(np.percentile(xs1, 100 - BBOX_PERCENTILE))


def decide_shot(
    samples: list[tuple[float, float] | None],
    sample_times: list[float],
    src_w: int,
    crop_w: int,
    margin: float = CONTENT_MARGIN,
) -> dict[str, Any]:
    """Decide crop vs fit_blur for one shot.

    ``samples``: per-frame (x0, x1) fractions (None = empty), ``sample_times``
    relative to shot start.  Returns a dict with ``strategy`` (``crop`` or
    ``fit_blur``), ``reason``, ``bbox`` and, for crop, ``crop_x`` (static) or
    ``keyframes`` [(t, x)] for a pan.
    """
    bb = shot_bbox(samples)
    if bb is None:
        return {"strategy": "fit_blur", "reason": "no content detected", "bbox": None}
    x0, x1 = bb[0] * src_w, bb[1] * src_w
    m = margin * src_w
    need = (x1 - x0) + 2 * m
    info = {"bbox": [round(bb[0], 3), round(bb[1], 3)], "need_px": round(need), "window_px": crop_w}

    def clamp(x: float) -> int:
        return int(max(0, min(round(x), src_w - crop_w)))

    if need <= crop_w:
        cxs = [((s[0] + s[1]) / 2) * src_w for s in samples if s is not None]
        # centre on the shot bbox; pan only for large, real travel
        travel = (max(cxs) - min(cxs)) if cxs else 0.0
        if travel <= PAN_MIN_TRAVEL * crop_w or len(cxs) < 3:
            return {"strategy": "crop", "reason": "content fits window", "crop_x": clamp((x0 + x1) / 2 - crop_w / 2), **info}

    # Union does not fit. Could each frame fit on its own (content moves)?
    pts = [(t, s) for t, s in zip(sample_times, samples) if s is not None]
    widths = [(s[1] - s[0]) * src_w + 2 * m for _, s in pts]
    if len(pts) >= 3 and widths and max(widths) <= crop_w:
        cx = np.array([((s[0] + s[1]) / 2) * src_w for _, s in pts])
        ts = np.array([t for t, _ in pts])
        k = 3
        cs = np.convolve(np.pad(cx, (k // 2, k // 2), mode="edge"), np.ones(k) / k, mode="valid")
        if cs.max() - cs.min() > PAN_MIN_TRAVEL * crop_w:
            kfs = [(round(float(t), 3), clamp(c - crop_w / 2)) for t, c in zip(ts, cs)]
            return {"strategy": "crop", "reason": "content moves, smooth pan", "keyframes": kfs, **info}
    if need <= crop_w:
        return {"strategy": "crop", "reason": "content fits window", "crop_x": clamp((x0 + x1) / 2 - crop_w / 2), **info}
    return {"strategy": "fit_blur", "reason": "content wider than 9:16 window", **info}


# -------------------------------------------------------------- ffmpeg io
def detect_shots(path: str, duration: float, threshold: float = SCENE_THRESHOLD,
                 min_shot: float = MIN_SHOT_SECONDS) -> list[tuple[float, float | None]]:
    """Shot list [(start, end)] via ffmpeg scene detection (last end = None)."""
    cmd = ["ffmpeg", "-nostdin", "-i", path, "-an",
           "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
           "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    cuts = sorted({float(m.group(1)) for m in re.finditer(r"pts_time:([0-9.]+)", p.stderr)})
    bounds = [0.0]
    for c in cuts:
        if c - bounds[-1] >= min_shot and duration - c >= min_shot:
            bounds.append(c)
    return [(bounds[i], bounds[i + 1] if i + 1 < len(bounds) else None) for i in range(len(bounds))]


def sample_gray_frames(path: str, duration: float, fps: float = 4.0,
                       max_frames: int = 1500) -> tuple[np.ndarray, float]:
    """Decode the video once to small gray frames. Returns (frames[N,H,W], fps)."""
    fps = min(fps, max_frames / max(duration, 1.0))
    fps = max(fps, 0.05)
    # probe size
    pr = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", path], capture_output=True, text=True)
    sw, sh = (int(v) for v in pr.stdout.strip().split(",")[:2])
    aw = ANALYSIS_W
    ah = max(2, int(round(aw * sh / sw / 2)) * 2)
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-an",
           "-vf", f"fps={fps},scale={aw}:{ah}:flags=area,format=gray",
           "-f", "rawvideo", "-"]
    p = subprocess.run(cmd, capture_output=True)
    buf = np.frombuffer(p.stdout, dtype=np.uint8)
    n = buf.size // (aw * ah)
    return buf[: n * aw * ah].reshape(n, ah, aw), fps


MIN_RUN_SECONDS = 2.0       # crop/fit runs shorter than this are absorbed (no flicker)


def _split_runs(extents: list, times: list[float], src_w: int, crop_w: int,
                margin: float, fps: float) -> list[tuple[int, int]]:
    """Split one shot into runs of consistently narrow/wide content.

    Returns index ranges [(i0, i1)) into ``extents``.  Short runs merge into the
    previous one, and a merged run is always treated as wide (safe).
    """
    def narrow(e):
        return e is not None and (e[1] - e[0]) * src_w + 2 * margin * src_w <= crop_w
    flags = [narrow(e) for e in extents]
    runs: list[list] = []
    for i, f in enumerate(flags):
        if runs and runs[-1][2] == f:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, f])
    min_n = max(2, int(MIN_RUN_SECONDS * fps))
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for k, r in enumerate(runs):
            if r[1] - r[0] < min_n:
                # absorb into a wide neighbour if any, else the longer one
                nb = [j for j in (k - 1, k + 1) if 0 <= j < len(runs)]
                nb.sort(key=lambda j: (runs[j][2], -(runs[j][1] - runs[j][0])))
                j = nb[0]
                runs[j][0], runs[j][1] = min(runs[j][0], r[0]), max(runs[j][1], r[1])
                runs[j][2] = runs[j][2] and r[2] if r[1] - r[0] >= 1 and runs[j][2] != r[2] else runs[j][2]
                del runs[k]
                changed = True
                break
    return [(r[0], r[1]) for r in runs]


def analyse_shots(frames: np.ndarray, fps: float, shots: list[tuple[float, float | None]],
                  duration: float, src_w: int, crop_w: int, margin: float = CONTENT_MARGIN) -> list[dict]:
    extents = [content_bbox_x(f) for f in frames]
    out = []
    for start, end in shots:
        e = duration if end is None else end
        i0 = int(np.ceil(start * fps - 1e-6))
        i1 = max(i0 + 1, int(np.ceil(e * fps - 1e-6)))
        idx = list(range(min(i0, len(frames) - 1), min(i1, len(frames))))
        if not idx:
            idx = [len(frames) - 1]
        # skip first 0.25 s of a shot: transitions often fade in
        later = [i for i in idx if i / fps - start >= 0.25]
        if len(later) >= 2:
            idx = later
        runs = _split_runs([extents[i] for i in idx], [i / fps for i in idx], src_w, crop_w, margin, fps)
        for r0, r1 in runs:
            ri = idx[r0:r1]
            rs = start if r0 == 0 else ri[0] / fps
            re_ = e if r1 == len(idx) else idx[r1] / fps
            d = decide_shot([extents[i] for i in ri], [i / fps - rs for i in ri], src_w, crop_w, margin)
            d.update({"start": round(rs, 3), "end": round(re_, 3), "samples": len(ri)})
            if out and d["strategy"] == "fit_blur" and out[-1]["strategy"] == "fit_blur":
                prev = out[-1]  # adjacent fit_blur shots are visually identical: merge
                prev["end"] = d["end"]
                prev["samples"] += d["samples"]
                if d.get("bbox") and prev.get("bbox"):
                    prev["bbox"] = [min(prev["bbox"][0], d["bbox"][0]), max(prev["bbox"][1], d["bbox"][1])]
                continue
            out.append(d)
    return out


# ---------------------------------------------------------- filter building
def fit_blur_chain(i: int, out_w: int, out_h: int, fit_scale: float, fit_y: float) -> str:
    """Filter chain (input label [s{i}], output [o{i}]) for a fit_blur shot."""
    fw = int(round(out_w * fit_scale / 2)) * 2
    fg = f"scale={fw}:-2:flags=lanczos"
    if fw > out_w:
        fg += f",crop={out_w}:ih:(iw-{out_w})/2:0"
    sw, sh = max(2, out_w // 8 // 2 * 2), max(2, out_h // 8 // 2 * 2)
    return (
        f"[s{i}]split=2[a{i}][b{i}];"
        f"[a{i}]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},"
        f"scale={sw}:{sh}:flags=area,gblur=sigma=10,scale={out_w}:{out_h}:flags=bicubic,"
        f"eq=brightness=-0.35:saturation=0.7[bg{i}];"
        f"[b{i}]{fg}[fg{i}];"
        f"[bg{i}][fg{i}]overlay=x=(W-w)/2:y=(H-h)*{fit_y:.4f}:format=auto,setsar=1[o{i}]"
    )


def crop_chain(i: int, decision: dict, crop_w: int, crop_h: int, out_w: int, out_h: int) -> str:
    if "keyframes" in decision:
        kfs = decision["keyframes"]
        expr = str(kfs[0][1])
        for (t0, x0), (t1, x1) in zip(kfs, kfs[1:]):
            if x1 != x0 and t1 > t0:
                expr += f"+({x1 - x0})*clip((t-{t0})/{t1 - t0:.3f},0,1)"
        x = f"'{expr}'"
    else:
        x = str(decision["crop_x"])
    return f"[s{i}]crop={crop_w}:{crop_h}:{x}:0,scale={out_w}:{out_h}:flags=lanczos,setsar=1[o{i}]"


def build_filter_graph(decisions: list[dict], crop_w: int, crop_h: int, out_w: int, out_h: int,
                       fit_scale: float, fit_y: float) -> str:
    n = len(decisions)
    parts = []
    if n == 1:
        parts.append("[0:v]null[in0]")
    else:
        parts.append("[0:v]split=%d%s" % (n, "".join(f"[in{i}]" for i in range(n))))
    for i, d in enumerate(decisions):
        tr = f"start={d['start']}" + ("" if i == n - 1 else f":end={d['end']}")
        parts.append(f"[in{i}]trim={tr},setpts=PTS-STARTPTS[s{i}]")
        if d["strategy"] == "crop":
            parts.append(crop_chain(i, d, crop_w, crop_h, out_w, out_h))
        else:
            parts.append(fit_blur_chain(i, out_w, out_h, fit_scale, fit_y))
    parts.append("".join(f"[o{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0,setsar=1,format=yuv420p[vout]")
    return ";".join(parts)


def whole_video_decisions(strategy: str, duration: float, src_w: int, crop_w: int) -> list[dict]:
    """Single-shot plan for strategy ``fit_blur`` / ``center``."""
    base = {"start": 0.0, "end": round(duration, 3), "samples": 0}
    if strategy == "fit_blur":
        return [{"strategy": "fit_blur", "reason": "forced fit_blur", **base}]
    return [{"strategy": "crop", "reason": "forced center", "crop_x": (src_w - crop_w) // 2 // 2 * 2, **base}]
