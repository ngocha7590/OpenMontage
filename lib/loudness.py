"""Two-pass EBU R128 loudness normalisation for delivered files.

Target: -16 LUFS integrated, -1.5 dBTP true peak, LRA 11. YouTube
normalises to roughly -14 LUFS, so -16 +/- 2 is the QA band.
The video stream is copied untouched; only the audio is re-encoded
(AAC 192k).
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

TARGET_I = -16.0
TARGET_TP = -1.5
TARGET_LRA = 11.0
QA_BAND_LU = 2.0


def _has_audio(path: Path) -> bool:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        ).stdout
        return "audio" in out
    except Exception:
        return False


def _parse_loudnorm_json(stderr: str) -> Optional[dict[str, Any]]:
    matches = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, flags=re.S)
    if not matches:
        return None
    try:
        return json.loads(matches[-1])
    except json.JSONDecodeError:
        return None


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def measure(path: Path | str, *, i: float = TARGET_I, tp: float = TARGET_TP,
            lra: float = TARGET_LRA) -> Optional[dict[str, Any]]:
    """Run loudnorm analysis; returns the raw measured dict or None."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-map", "0:a:0",
         "-af", f"loudnorm=I={i}:TP={tp}:LRA={lra}:print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return _parse_loudnorm_json(proc.stderr)


def normalize_file(path: Path | str, *, target_i: float = TARGET_I,
                   target_tp: float = TARGET_TP, target_lra: float = TARGET_LRA,
                   audio_bitrate: str = "192k") -> dict[str, Any]:
    """Normalise ``path`` in place. Returns a loudness report dict.

    Report keys: applied (bool), input_i, output_i, true_peak (output),
    input_true_peak, target_i, target_tp, within_band, and ``reason`` /
    ``error`` when not applied.
    """
    path = Path(path)
    report: dict[str, Any] = {"applied": False, "target_i": target_i, "target_tp": target_tp}
    if not path.is_file():
        report["error"] = f"file not found: {path}"
        return report
    if not _has_audio(path):
        report["reason"] = "no audio stream"
        return report

    first = measure(path, i=target_i, tp=target_tp, lra=target_lra)
    if not first or _num(first.get("input_i")) is None:
        report["error"] = "loudness measurement failed (silent or unreadable audio)"
        return report
    in_i = _num(first["input_i"])
    report["input_i"] = in_i
    report["input_true_peak"] = _num(first.get("input_tp"))
    if in_i <= -70.0:
        report["reason"] = "audio is silent; not normalised"
        return report

    af = (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"
        f":measured_I={first['input_i']}:measured_TP={first['input_tp']}"
        f":measured_LRA={first['input_lra']}:measured_thresh={first['input_thresh']}"
        f":offset={first['target_offset']}:linear=true:print_format=json"
    )
    tmp = path.with_name(f".{path.stem}.loudnorm-{time.time_ns()}{path.suffix}")
    try:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-i", str(path),
               "-map", "0:v?", "-map", "0:a:0", "-c:v", "copy", "-af", af,
               "-ar", "48000", "-c:a", "aac", "-b:a", audio_bitrate]
        if path.suffix.lower() in (".mp4", ".mov", ".m4v"):
            cmd += ["-movflags", "+faststart"]
        cmd.append(str(tmp))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not tmp.is_file():
            report["error"] = f"loudnorm encode failed: {proc.stderr[-400:]}"
            return report
        # Verify the delivered result with a fresh measurement.
        after = measure(tmp, i=target_i, tp=target_tp, lra=target_lra)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()

    out_i = _num((after or {}).get("input_i"))
    report.update(
        applied=True,
        output_i=out_i,
        true_peak=_num((after or {}).get("input_tp")),
        within_band=(out_i is not None and abs(out_i - target_i) <= QA_BAND_LU),
    )
    return report
