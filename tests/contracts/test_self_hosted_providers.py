"""Self-hosted providers used by OpenMontage Studio: each one is configured by a URL
only (local or remote). Fake HTTP servers stand in for tts-service, an F5 Gradio
app and a muse2api / gemini-web2api gateway; ComfyUI generate() is stubbed."""

from __future__ import annotations

import base64
import io
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tools.audio.comfyui_audio import ComfyUIAceStep15Music, ComfyUISFX
from tools.audio.local_tts import LocalTTS, f5_text, segments
from tools.base_tool import ToolStatus
from tools.graphics.gateway_image import GatewayGeminiWebImage, GatewayMuseImage
from tools.tool_registry import ToolRegistry
from tools.video.gateway_video import GatewayMuseVideo


def _wav(seconds: float = 0.2, loud_tail: bool = False) -> bytes:
    sr = 24000
    n = int(sr * seconds)
    frames = bytearray()
    for i in range(n):
        v = 8000 if (loud_tail or i < n - 2000) and i % 2 else 0
        frames += int(v).to_bytes(2, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return buf.getvalue()


class _Server:
    def __init__(self, routes):
        routes_ = routes

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _go(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                self.server.calls.append((method, self.path, body, dict(self.headers)))
                for (m, prefix), fn in routes_.items():
                    if m == method and self.path.startswith(prefix):
                        status, ctype, payload = fn(self.path, body)
                        self.send_response(status)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                        return
                self.send_response(404)
                self.end_headers()

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.calls = []
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    @property
    def calls(self):
        return self.httpd.calls

    def close(self):
        self.httpd.shutdown()


def _json(obj, status=200):
    return status, "application/json", json.dumps(obj).encode()


# ------------------------------------------------------------------ registry --

def test_registry_discovers_all_self_hosted_tools():
    reg = ToolRegistry()
    reg.discover()
    for name, cap in [("local_tts", "tts"), ("comfyui_music_ace15", "music_generation"),
                      ("comfyui_sfx", "sound_effects"), ("gateway_geminiweb_image", "image_generation"),
                      ("gateway_muse_image", "image_generation"), ("gateway_muse_video", "video_generation")]:
        tool = reg.get(name)
        assert tool is not None, name
        assert tool.capability == cap
        assert tool.estimate_cost({"prompt": "x", "text": "x"}) == 0.0


def test_env_example_documents_every_url(tmp_path):
    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    for var in ["LOCAL_TTS_URL", "COMFYUI_MUSIC_SERVER_URL", "COMFYUI_SFX_SERVER_URL",
                "GATEWAY_GEMINIWEB_URL", "GATEWAY_GEMINIWEB_API_KEY",
                "GATEWAY_MUSE_URL", "GATEWAY_MUSE_API_KEY"]:
        assert f"\n{var}=" in text, var


# ---------------------------------------------------------------- local_tts --

def test_f5_text_always_ends_a_sentence():
    assert f5_text("xin chào,") == "xin chào. "
    assert f5_text("Xong!") == "Xong! "
    assert f5_text("Lâm Vãn Ngâm") == "Lâm Vãn Ngâm. "


def test_segments_split_on_pause_tags():
    assert segments("A [pause] B [pause=1.5] C") == [
        ("text", "A"), ("pause", 0.5), ("text", "B"), ("pause", 1.5), ("text", "C")]


def test_local_tts_unavailable_without_server(monkeypatch):
    monkeypatch.setenv("LOCAL_TTS_URL", "http://127.0.0.1:9")
    assert LocalTTS().get_status() == ToolStatus.UNAVAILABLE


def test_local_tts_studio_backend(monkeypatch, tmp_path):
    srv = _Server({
        ("GET", "/health"): lambda p, b: _json({"status": "ok", "model": "vieneu"}),
        ("POST", "/v1/synthesize"): lambda p, b: (200, "audio/wav", _wav()),
    })
    try:
        monkeypatch.setenv("LOCAL_TTS_URL", srv.url)
        tool = LocalTTS()
        assert tool.get_status() == ToolStatus.AVAILABLE
        out = tmp_path / "n.wav"
        res = tool.execute({"text": "Xin chào [pause] thế giới", "voice": "vi-VN", "output_path": str(out)})
        assert res.success, res.error
        assert res.data["backend"] == "studio"
        assert out.read_bytes()[:4] == b"RIFF"
        body = json.loads(srv.calls[-1][2])
        assert body == {"text": "Xin chào [pause] thế giới", "voice": "vi-VN", "format": "wav"}
    finally:
        srv.close()


def test_local_tts_f5_gradio_backend(monkeypatch, tmp_path):
    config = {"api_prefix": "/gradio_api", "title": "F5-TTS Tiếng Việt",
              "components": [{"id": 1, "props": {"choices": [["Anh Duc", "Anh Duc"], ["Chi Mai", "Chi Mai"]]}}],
              "dependencies": [{"api_name": "do_generate", "inputs": [1]}]}
    sse = b'event: complete\ndata: [{"path": "/tmp/out.wav"}]\n\n'
    srv = _Server({
        ("GET", "/health"): lambda p, b: (404, "text/plain", b"no"),
        ("GET", "/config"): lambda p, b: _json(config),
        ("POST", "/gradio_api/call/do_generate"): lambda p, b: _json({"event_id": "e1"}),
        ("GET", "/gradio_api/call/do_generate/"): lambda p, b: (200, "text/event-stream", sse),
        ("GET", "/gradio_api/file="): lambda p, b: (200, "audio/wav", _wav()),
    })
    try:
        monkeypatch.setenv("LOCAL_TTS_URL", srv.url)
        monkeypatch.setenv("LOCAL_TTS_VOICE_MAP", json.dumps({"vi-VN": "Chi Mai"}))
        tool = LocalTTS()
        out = tmp_path / "f5.wav"
        res = tool.execute({"text": "Câu một, [pause=0.3] câu hai", "voice": "vi-VN", "output_path": str(out)})
        assert res.success, res.error
        assert res.data["backend"] == "f5_gradio" and res.data["voice"] == "Chi Mai"
        posts = [json.loads(c[2]) for c in srv.calls if c[0] == "POST"]
        assert posts[0]["data"][0] == "Chi Mai"
        assert posts[0]["data"][1].endswith(". ")          # punctuation added for F5
        with wave.open(str(out)) as w:                     # two pieces + 0.3 s silence
            assert w.getnframes() > 2 * int(0.2 * 24000)
    finally:
        srv.close()


# ----------------------------------------------------------------- gateways --

def _gateway(usable=2, video_status="succeeded"):
    png = base64.b64encode(b"\x89PNG fake").decode()
    polls = {"n": 0}

    def poll(p, b):
        polls["n"] += 1
        if p.endswith("/content"):
            return 200, "video/mp4", b"MP4DATA"
        status = "running" if polls["n"] < 2 else video_status
        return _json({"id": "v1", "status": status, "error": "out of credits" if status == "failed" else None})

    return _Server({
        ("GET", "/v1/models"): lambda p, b: _json({"data": [{"id": "gemini-image", "kind": "image"}]}),
        ("GET", "/v1/capacity"): lambda p, b: _json({"usable": usable, "busy": 0, "total": 3}),
        ("POST", "/v1/images/generations"): lambda p, b: _json({"data": [{"b64_json": png}]}),
        ("POST", "/v1/videos"): lambda p, b: _json({"id": "v1", "status": "queued"}),
        ("GET", "/v1/videos/"): poll,
    })


def test_gateway_image_needs_a_url(monkeypatch):
    monkeypatch.delenv("GATEWAY_GEMINIWEB_URL", raising=False)
    tool = GatewayGeminiWebImage()
    assert tool.get_status() == ToolStatus.UNAVAILABLE
    assert "GATEWAY_GEMINIWEB_URL" in tool.execute({"prompt": "x"}).error


def test_gateway_image_unavailable_when_no_usable_account(monkeypatch):
    srv = _gateway(usable=0)
    try:
        monkeypatch.setenv("GATEWAY_GEMINIWEB_URL", srv.url + "/v1")
        assert GatewayGeminiWebImage().get_status() == ToolStatus.UNAVAILABLE
    finally:
        srv.close()


def test_gateway_image_generates_with_refs_and_key(monkeypatch, tmp_path):
    srv = _gateway()
    try:
        monkeypatch.setenv("GATEWAY_GEMINIWEB_URL", srv.url + "/v1")
        monkeypatch.setenv("GATEWAY_GEMINIWEB_API_KEY", "sk-test")
        ref = tmp_path / "ref.png"
        ref.write_bytes(b"\x89PNG ref")
        out = tmp_path / "img.png"
        tool = GatewayGeminiWebImage()
        assert tool.get_status() == ToolStatus.AVAILABLE
        res = tool.execute({"prompt": "a lantern", "aspect_ratio": "16:9",
                            "image_path": str(ref), "output_path": str(out)})
        assert res.success, res.error
        assert out.read_bytes() == b"\x89PNG fake"
        method, path, body, headers = [c for c in srv.calls if c[0] == "POST"][0]
        sent = json.loads(body)
        assert sent["model"] == "gemini-image" and sent["size"] == "1344x768"
        assert sent["image"][0].startswith("data:image/png;base64,")
        assert headers.get("Authorization") == "Bearer sk-test"
    finally:
        srv.close()


def test_muse_image_uses_its_own_url(monkeypatch):
    monkeypatch.setenv("GATEWAY_MUSE_URL", "http://example.invalid/v1")
    assert GatewayMuseImage()._client().base == "http://example.invalid/v1"


def test_gateway_video_polls_and_downloads(monkeypatch, tmp_path):
    srv = _gateway()
    try:
        monkeypatch.setenv("GATEWAY_MUSE_URL", srv.url + "/v1")
        frame = tmp_path / "f.png"
        frame.write_bytes(b"\x89PNG frame")
        out = tmp_path / "clip.mp4"
        res = GatewayMuseVideo().execute({"prompt": "lantern sways", "duration": 4.6,
                                          "first_frame_path": str(frame),
                                          "output_path": str(out), "timeout_seconds": 20})
        assert res.success, res.error
        assert out.read_bytes() == b"MP4DATA"
        sent = json.loads([c for c in srv.calls if c[0] == "POST"][0][2])
        assert sent["seconds"] == 5 and sent["image"].startswith("data:image/png")
        assert res.data["operation"] == "image_to_video"
    finally:
        srv.close()


def test_gateway_video_quota_error_is_reported(monkeypatch, tmp_path):
    srv = _gateway(video_status="failed")
    try:
        monkeypatch.setenv("GATEWAY_MUSE_URL", srv.url + "/v1")
        res = GatewayMuseVideo().execute({"prompt": "x", "output_path": str(tmp_path / "c.mp4"),
                                          "timeout_seconds": 20})
        assert not res.success and "another video provider" in res.error
    finally:
        srv.close()


# ------------------------------------------------------------ comfyui audio --

def _stub_generate(captured):
    def gen(self, workflow, output_node, dest, **kw):
        captured["wf"], captured["node"] = workflow, output_node
        Path(dest).write_bytes(b"fLaC")
        return [Path(dest)]
    return gen


def test_ace15_music_patches_bpm_key_and_duration(monkeypatch, tmp_path):
    from tools._comfyui.client import ComfyUIClient
    captured = {}
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self: True)
    monkeypatch.setattr(ComfyUIClient, "generate", _stub_generate(captured))
    res = ComfyUIAceStep15Music().execute({"prompt": "calm intro, 80 bpm, E minor, piano",
                                           "duration_seconds": 45, "seed": 7,
                                           "output_path": str(tmp_path / "m.flac")})
    assert res.success, res.error
    enc = captured["wf"]["2"]["inputs"]
    assert enc["bpm"] == 80 and enc["keyscale"] == "E minor" and enc["duration"] == 45
    assert captured["wf"]["5"]["inputs"]["seconds"] == 45 and captured["node"] == "8"


def test_sfx_patches_prompt_and_clamps_duration(monkeypatch, tmp_path):
    from tools._comfyui.client import ComfyUIClient
    captured = {}
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self: True)
    monkeypatch.setattr(ComfyUIClient, "generate", _stub_generate(captured))
    res = ComfyUISFX().execute({"prompt": "soft whoosh", "duration_seconds": 30, "seed": 1,
                                "output_path": str(tmp_path / "s.flac")})
    assert res.success, res.error
    assert captured["wf"]["3"]["inputs"]["text"] == "soft whoosh"
    assert captured["wf"]["5"]["inputs"]["seconds"] == 10.0


def test_sfx_reads_its_own_server_url(monkeypatch):
    monkeypatch.setenv("COMFYUI_SFX_SERVER_URL", "http://10.0.0.5:8188")
    assert ComfyUISFX()._client.server_url == "http://10.0.0.5:8188"
