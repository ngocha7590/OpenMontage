"""Narration from a self-hosted TTS server -- on this machine or another, only a URL.

Two server kinds are recognised from the URL alone (``LOCAL_TTS_URL``):

* ``studio`` -- VideoCreatorSystem ``tts-service`` (VieNeu / Qwen3-TTS Vietnamese on
  :3202, VoxCPM2 English on :3201). ``GET /health`` + ``POST /v1/synthesize``
  ``{text, voice, format: "wav"}`` -> WAV PCM16 mono 24 kHz. Understands ``[pause]``.
* ``f5_gradio`` -- a Gradio "F5-TTS Tieng Viet" app. Reads ``GET /config``, calls the
  ``do_generate`` API over Gradio's SSE queue. F5 does not know ``[pause]``, so the
  text is split there and silence is inserted; each piece ends with sentence
  punctuation and is re-read when F5 clips its last syllable.

Ported from VideoCreatorSystem ``studio/studio/adapters/tts.py``.

Environment:
    LOCAL_TTS_URL            e.g. http://127.0.0.1:3202 or http://100.116.28.106:7860
    LOCAL_TTS_BACKEND        auto (default) | studio | f5_gradio
    LOCAL_TTS_VOICE_MAP      JSON, studio voice id -> F5 voice name ({"vi-VN": "Anh Duc"})
    LOCAL_TTS_DEFAULT_VOICE  F5 voice used when a voice is not in the map
    LOCAL_TTS_F5_STEPS       F5 sampling steps (default 32)
"""

from __future__ import annotations

import array
import io
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

DEFAULT_URL = "http://127.0.0.1:3202"
F5_API = "do_generate"
PAUSE_RE = re.compile(r"\[pause(?:=(\d+(?:\.\d+)?))?\]", re.I)
DEFAULT_PAUSE_S = 0.5
MAX_PAUSE_S = 5.0
CUT_DB = -35.0
CUT_RETRIES = 2
FADE_S = 0.02
_END_OK = ".!?…"
_END_SOFT = re.compile(r"[\s,;:–—\-\"'”“»«)(]+$")
_PROBE_TTL_S = 30.0

LICENSES = {
    "studio": "VieNeu-TTS / Qwen3-TTS-Vi / VoxCPM2 -- Apache-2.0",
    "f5_gradio": "F5-TTS -- base weights CC-BY-NC-4.0; check the Vietnamese fine-tune before commercial use",
}


class LocalTTSError(RuntimeError):
    pass


def _get(url: str, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise LocalTTSError(f"TTS {url} HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LocalTTSError(f"TTS {url} is not reachable ({getattr(e, 'reason', e)})") from e


# ----------------------------------------------------------------- WAV helpers --

def _pcm(wav: bytes):
    with wave.open(io.BytesIO(wav)) as w:
        p = w.getparams()
        a = array.array("h", w.readframes(w.getnframes()))
    return p, a


def tail_db(wav: bytes, sec: float = 0.03) -> float:
    p, a = _pcm(wav)
    n = max(1, int(p.framerate * sec) * p.nchannels)
    s = a[-n:] if len(a) >= n else a
    if not s:
        return -120.0
    r = math.sqrt(sum(x * x for x in s) / len(s))
    return 20 * math.log10(r / 32768) if r else -120.0


def _fade_out(wav: bytes, sec: float = FADE_S) -> bytes:
    p, a = _pcm(wav)
    n = min(len(a), int(p.framerate * sec) * p.nchannels)
    for i in range(n):
        a[len(a) - n + i] = int(a[len(a) - n + i] * ((n - i) / n) ** 2)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(p)
        w.writeframes(a.tobytes())
    return buf.getvalue()


def _concat(parts: list) -> bytes:
    """Join WAV bytes and silences (float seconds) using the first WAV's format."""
    params, frames = None, []
    for p in parts:
        if isinstance(p, bytes):
            with wave.open(io.BytesIO(p)) as w:
                cur = (w.getnchannels(), w.getsampwidth(), w.getframerate())
                if params and cur != params:
                    raise LocalTTSError(f"TTS returned mismatched WAV formats {cur} != {params}")
                params = cur
                frames.append(w.readframes(w.getnframes()))
        else:
            frames.append(p)
    if params is None:
        raise LocalTTSError("TTS: nothing to read")
    ch, sw, sr = params
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(sw)
        w.setframerate(sr)
        for f in frames:
            w.writeframes(f if isinstance(f, bytes) else b"\0" * (int(f * sr) * ch * sw))
    return buf.getvalue()


def segments(text: str) -> list[tuple[str, object]]:
    out, last = [], 0
    for m in PAUSE_RE.finditer(text):
        if text[last:m.start()].strip():
            out.append(("text", text[last:m.start()].strip()))
        sec = float(m.group(1)) if m.group(1) else DEFAULT_PAUSE_S
        out.append(("pause", min(max(sec, 0.0), MAX_PAUSE_S)))
        last = m.end()
    if text[last:].strip():
        out.append(("text", text[last:].strip()))
    return out


def f5_text(text: str) -> str:
    """Make every F5 piece end with sentence punctuation; otherwise F5 reads up to
    the edge of its length estimate and swallows the last syllable."""
    t = text.strip()
    core = _END_SOFT.sub("", t)
    if not core:
        return t
    tail = t[len(core):]
    if core[-1] == "…":
        return t + " . "
    if core[-1] in _END_OK:
        return t + " "
    closing = "".join(ch for ch in tail if ch in "\"'”»)")
    return core + "." + closing + " "


# --------------------------------------------------------------------- tool --

class LocalTTS(BaseTool):
    name = "local_tts"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "local_tts"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.LOCAL_GPU

    dependencies = []
    install_instructions = (
        "Run a TTS server and set LOCAL_TTS_URL to it -- local or remote:\n"
        "  VideoCreatorSystem tts-service: sh tts-service/run.sh vi  -> http://127.0.0.1:3202\n"
        "  or an F5-TTS Gradio app, e.g. http://<host>:7860\n"
        "For F5, map voices with LOCAL_TTS_VOICE_MAP / LOCAL_TTS_DEFAULT_VOICE."
    )
    agent_skills = ["text-to-speech"]
    capabilities = ["text_to_speech", "vietnamese_narration", "pause_tags"]
    supports = {
        "voice_cloning": False,
        "multilingual": True,
        "offline": True,
        "pause_tags": True,
        "native_audio": True,
    }
    best_for = [
        "free Vietnamese narration on your own GPU or a LAN/Tailscale machine",
        "long-form narration where per-character API pricing adds up",
    ]
    not_good_for = ["setups without a running TTS server"]
    fallback_tools = ["piper_tts", "google_tts"]

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "description": "Narration. [pause] or [pause=1.2] inserts silence."},
            "voice": {"type": "string", "default": "vi-VN",
                      "description": "Voice id, e.g. vi-VN, vi-VN-narrator-f1, en-US. F5 maps it via LOCAL_TTS_VOICE_MAP."},
            "voice_id": {"type": "string", "description": "Alias of voice."},
            "speed": {"type": "number", "default": 1.0, "description": "F5 only; studio backend ignores it."},
            "output_path": {"type": "string"},
            "timeout_seconds": {"type": "integer", "default": 900},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, network_required=True)
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["not reachable", "MODEL_LOADING"])
    idempotency_key_fields = ["text", "voice", "voice_id", "speed"]
    side_effects = ["writes audio file to output_path"]
    user_visible_verification = ["Listen for clipped endings and mispronounced names"]

    def __init__(self) -> None:
        self._probe: tuple[float, str, dict[str, Any]] | None = None

    # -- configuration ---------------------------------------------------
    @staticmethod
    def url() -> str:
        return (os.environ.get("LOCAL_TTS_URL") or DEFAULT_URL).rstrip("/")

    @staticmethod
    def _voice_map() -> dict[str, str]:
        raw = os.environ.get("LOCAL_TTS_VOICE_MAP") or ""
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _detect(self, fresh: bool = False) -> tuple[str, dict[str, Any]]:
        """(backend, info). info for f5_gradio = {prefix, voices}; cached 30 s."""
        now = time.monotonic()
        if not fresh and self._probe and now - self._probe[0] < _PROBE_TTL_S:
            return self._probe[1], self._probe[2]
        url = self.url()
        forced = (os.environ.get("LOCAL_TTS_BACKEND") or "auto").strip().lower()
        backend, info = "", {}
        if forced in ("auto", "studio"):
            try:
                with urllib.request.urlopen(f"{url}/health", timeout=5) as r:
                    js = json.loads(r.read())
                if isinstance(js, dict) and "status" in js:
                    backend, info = "studio", js
            except urllib.error.HTTPError as e:
                if e.code == 503:                      # MODEL_LOADING: it is tts-service
                    backend, info = "studio", {"status": "loading"}
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                pass
        if not backend and forced in ("auto", "f5_gradio"):
            info = self._f5_api(url)
            backend = "f5_gradio" if info else ""
        if not backend:
            raise LocalTTSError(f"No TTS server answers at {url} (tried /health and Gradio /config). "
                                "Start it or fix LOCAL_TTS_URL.")
        self._probe = (now, backend, info)
        return backend, info

    @staticmethod
    def _f5_api(url: str) -> dict[str, Any]:
        try:
            conf = json.loads(_get(f"{url}/config", 5))
        except (LocalTTSError, ValueError):
            return {}
        comps = {c.get("id"): c for c in conf.get("components") or []}
        dep = next((d for d in conf.get("dependencies") or [] if d.get("api_name") == F5_API), None)
        if dep is None:
            return {}
        first = comps.get((dep.get("inputs") or [None])[0]) or {}
        choices = (first.get("props") or {}).get("choices") or []
        voices = [str(c[0] if isinstance(c, (list, tuple)) else c) for c in choices]
        return {"prefix": conf.get("api_prefix") or "/gradio_api", "voices": voices,
                "title": conf.get("title") or ""}

    # -- BaseTool ----------------------------------------------------------
    def get_status(self) -> ToolStatus:
        try:
            backend, info = self._detect()
        except LocalTTSError:
            return ToolStatus.UNAVAILABLE
        if backend == "studio" and info.get("status") != "ok":
            return ToolStatus.DEGRADED
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        info["server"] = {"url_env": "LOCAL_TTS_URL", "url": self.url(),
                          "backend_env": "LOCAL_TTS_BACKEND"}
        return info

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        text = (inputs.get("text") or "").strip()
        if not text:
            return ToolResult(success=False, error="TTS: empty text")
        voice = inputs.get("voice") or inputs.get("voice_id") or "vi-VN"
        timeout = float(inputs.get("timeout_seconds") or 900)
        output_path = Path(inputs.get("output_path") or f"local_tts_{int(time.time())}.wav")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        try:
            backend, info = self._detect()
            if backend == "studio":
                wav, used_voice, model = self._studio(text, voice, timeout), voice, info.get("model") or "tts-service"
            else:
                wav, used_voice = self._f5(text, voice, info, timeout, float(inputs.get("speed") or 1.0))
                model = "f5-tts-gradio"
        except LocalTTSError as exc:
            return ToolResult(success=False, error=str(exc))
        output_path.write_bytes(wav)
        with wave.open(io.BytesIO(wav)) as w:
            seconds = round(w.getnframes() / float(w.getframerate()), 2)
        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "backend": backend,
                "server": self.url(),
                "voice": used_voice,
                "model": model,
                "text_length": len(text),
                "duration_seconds": seconds,
                "output": str(output_path),
                "format": "wav",
                "license": LICENSES[backend],
            },
            artifacts=[str(output_path)],
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )

    # -- studio (tts-service) ----------------------------------------------
    def _studio(self, text: str, voice: str, timeout: float) -> bytes:
        body = json.dumps({"text": text, "voice": voice, "format": "wav"}).encode("utf-8")
        req = urllib.request.Request(f"{self.url()}/v1/synthesize", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise LocalTTSError(f"TTS HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LocalTTSError(f"TTS {self.url()} is not reachable ({getattr(e, 'reason', e)})") from e

    # -- F5 Gradio -----------------------------------------------------------
    def _f5_voice(self, voice: str, remote: list[str]) -> str:
        name = self._voice_map().get(voice) or os.environ.get("LOCAL_TTS_DEFAULT_VOICE") or ""
        if not name:
            if not remote:
                raise LocalTTSError("F5-TTS has no voices yet -- add one in the F5 app first")
            return remote[0]
        if remote and name not in remote:
            raise LocalTTSError(f"F5-TTS has no voice '{name}' (for {voice}); it has: {', '.join(remote)}")
        return name

    def _f5_call(self, prefix: str, voice: str, text: str, timeout: float, speed: float) -> bytes:
        base = self.url() + prefix
        steps = int(os.environ.get("LOCAL_TTS_F5_STEPS") or 32)
        body = json.dumps({"data": [voice, text, speed, steps, True]}).encode("utf-8")
        req = urllib.request.Request(f"{base}/call/{F5_API}", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                eid = json.loads(r.read())["event_id"]
            with urllib.request.urlopen(f"{base}/call/{F5_API}/{eid}", timeout=timeout) as r:
                out = self._sse_result(r)
        except urllib.error.HTTPError as e:
            raise LocalTTSError(f"TTS F5 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LocalTTSError(f"TTS {self.url()} is not reachable ({getattr(e, 'reason', e)})") from e
        f = out[0] if out else None
        if not isinstance(f, dict) or not f.get("path"):
            raise LocalTTSError(f"TTS F5 returned no audio file: {str(out)[:200]}")
        # Download through the configured URL, not the host F5 reports for itself.
        return _get(f"{base}/file={urllib.parse.quote(f['path'])}", 60)

    @staticmethod
    def _sse_result(resp) -> list:
        event = None
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event in ("complete", "error"):
                data = line[5:].strip()
                if event == "error":
                    raise LocalTTSError(f"TTS F5 error: {data[:300] if data != 'null' else 'unknown'}")
                return json.loads(data)
        raise LocalTTSError("TTS F5: result stream closed before completion")

    def _f5_one(self, prefix: str, name: str, text: str, timeout: float, speed: float) -> bytes:
        t = f5_text(text)
        best = self._f5_call(prefix, name, t, timeout, speed)
        db = tail_db(best)
        for k in range(CUT_RETRIES):
            if db <= CUT_DB:
                break
            b = self._f5_call(prefix, name, t.rstrip() + " . . " if k == 0 else t, timeout, speed)
            d = tail_db(b)
            if d < db:
                best, db = b, d
        return _fade_out(best) if db > CUT_DB else best

    def _f5(self, text: str, voice: str, info: dict[str, Any], timeout: float,
            speed: float) -> tuple[bytes, str]:
        name = self._f5_voice(voice, info.get("voices") or [])
        segs = segments(text)
        if not any(k == "text" for k, _ in segs):
            raise LocalTTSError("TTS: empty text")
        parts = [self._f5_one(info["prefix"], name, v, timeout, speed) if k == "text" else v
                 for k, v in segs]
        return (parts[0] if len(parts) == 1 else _concat(parts)), name
