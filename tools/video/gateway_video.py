"""Video clips through the muse.ai web-app gateway (muse2api).

muse2api runs as its own process -- here or on another machine -- and keeps a
pool of logged-in muse.ai accounts behind an OpenAI-style ``/videos`` API.
OpenMontage only needs its URL and key:

    GATEWAY_MUSE_URL=http://127.0.0.1:18610/v1
    GATEWAY_MUSE_API_KEY=<muse2api data/api_key>

AVAILABLE only while the gateway answers and has a usable account, so the video
selector moves on by itself when every account is busy or resting.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tools._gateway import GatewayClient, GatewayError, GatewayLimited
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

_ASPECT_SIZES = {"16:9": "1280x720", "9:16": "720x1280", "1:1": "960x960"}


class GatewayMuseVideo(BaseTool):
    name = "gateway_muse_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "muse"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    gateway_id = "muse"
    default_url = "http://127.0.0.1:18610/v1"
    default_model = "muse-video"
    max_refs = 2
    license_note = ("muse.ai web via muse2api -- Meta AI terms; "
                    "no clear licence for commercial use of the output")

    dependencies = []
    install_instructions = (
        "Run muse2api (VideoCreatorSystem/provider/muse2api), add accounts with "
        "login.sh --label <name>, then set GATEWAY_MUSE_URL "
        "(e.g. http://127.0.0.1:18610/v1) and GATEWAY_MUSE_API_KEY (data/api_key)."
    )
    agent_skills = ["ai-video-gen"]
    capabilities = ["text_to_video", "image_to_video"]
    supports = {
        "text_to_video": True,
        "image_to_video": True,
        "reference_images": True,
        "native_audio": False,
        "seed": False,
        "offline": False,
    }
    best_for = [
        "free short motion clips through logged-in muse.ai accounts",
        "animating an approved still as the first frame",
    ]
    not_good_for = ["clips longer than a few seconds", "guaranteed commercial licensing",
                    "real-person likeness"]
    fallback_tools = ["comfyui_video"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "operation": {"type": "string", "enum": ["text_to_video", "image_to_video"]},
            "model": {"type": "string", "description": "Gateway model id; defaults to muse-video."},
            "duration": {"type": "number", "default": 5, "description": "Seconds (integer on the wire)."},
            "aspect_ratio": {"type": "string", "enum": list(_ASPECT_SIZES)},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "seed": {"type": "integer"},
            "first_frame_path": {"type": "string", "description": "Still used as the first frame."},
            "image_path": {"type": "string", "description": "Alias of first_frame_path."},
            "reference_image_path": {"type": "string", "description": "Alias of first_frame_path."},
            "reference_image_paths": {"type": "array", "items": {"type": "string"},
                                      "description": "Subject/style images (max 2)."},
            "output_path": {"type": "string"},
            "timeout_seconds": {"type": "integer", "default": 900},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, network_required=True)
    retry_policy = RetryPolicy(max_retries=0, retryable_errors=[])
    idempotency_key_fields = ["prompt", "model", "duration", "aspect_ratio",
                              "first_frame_path", "image_path", "reference_image_path"]
    side_effects = ["writes video file to output_path", "uses one gateway account"]
    user_visible_verification = ["Watch the clip for motion, artefacts and prompt adherence"]

    def _client(self) -> GatewayClient:
        client = getattr(self, "_gateway_client", None)
        if client is None:
            client = GatewayClient(self.gateway_id, self.default_url)
            self._gateway_client = client
        return client

    def get_status(self) -> ToolStatus:
        client = self._client()
        if not client.is_configured():
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE if client.has_usable_account() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 180.0

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        client = self._client()
        info["gateway"] = {"id": self.gateway_id, "url_env": client.url_env,
                           "key_env": client.key_env, "license": self.license_note}
        return info

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        client = self._client()
        if not client.is_configured():
            return ToolResult(success=False, error=(
                f"Gateway muse is not configured. Set {client.url_env} (local or remote URL, incl. /v1)."))
        if not client.has_usable_account():
            return ToolResult(success=False, error=client.unavailable_reason())

        first = (inputs.get("first_frame_path") or inputs.get("image_path")
                 or inputs.get("reference_image_path"))
        refs = list(inputs.get("reference_image_paths") or [])
        for p in ([first] if first else []) + refs:
            if not Path(p).is_file():
                return ToolResult(success=False, error=f"Image not found: {p}")
        if len(refs) > self.max_refs:
            return ToolResult(success=False, error=f"muse accepts at most {self.max_refs} reference images.")

        size = None
        if inputs.get("width") and inputs.get("height"):
            size = f"{int(inputs['width'])}x{int(inputs['height'])}"
        elif inputs.get("aspect_ratio") in _ASPECT_SIZES:
            size = _ASPECT_SIZES[inputs["aspect_ratio"]]

        model = inputs.get("model") or self.default_model
        seconds = max(1, round(float(inputs.get("duration") or 5)))
        output_path = Path(inputs.get("output_path") or f"gateway_muse_{int(time.time())}.mp4")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        try:
            data = client.video(model, inputs["prompt"], seconds=seconds, size=size,
                                seed=inputs.get("seed"), first_frame=first, refs=refs or None,
                                timeout=float(inputs.get("timeout_seconds") or 900),
                                on_progress=lambda msg: print(f"[{self.name}] {msg}"))
        except GatewayLimited as exc:
            return ToolResult(success=False, error=f"{exc} -- every muse account is busy or out of "
                                                   "video quota; use another video provider.")
        except GatewayError as exc:
            return ToolResult(success=False, error=str(exc))
        output_path.write_bytes(data)
        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "gateway": self.gateway_id,
                "model": model,
                "prompt": inputs["prompt"],
                "operation": "image_to_video" if first else "text_to_video",
                "duration_seconds": seconds,
                "output": str(output_path),
                "license": self.license_note,
                "commercial": False,
            },
            artifacts=[str(output_path)],
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
            seed=inputs.get("seed"),
            model=model,
        )
