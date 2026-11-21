"""Image generation through a web-app gateway (Gemini web, muse.ai).

Each gateway is a separate process -- on this machine or another -- that keeps
a pool of logged-in accounts behind an OpenAI-style API (see
``tools/_gateway/client.py``). Configure it with a URL only:

    GATEWAY_GEMINIWEB_URL=http://127.0.0.1:18620/v1   (+ GATEWAY_GEMINIWEB_API_KEY)
    GATEWAY_MUSE_URL=http://127.0.0.1:18610/v1        (+ GATEWAY_MUSE_API_KEY)

The tool is AVAILABLE only while the gateway answers and has at least one usable
account, so the image selector falls back by itself when the pool is exhausted.
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

_ASPECT_SIZES = {
    "16:9": (1344, 768), "9:16": (768, 1344), "1:1": (1024, 1024),
    "4:3": (1152, 896), "3:4": (896, 1152), "21:9": (1536, 640),
}

_INPUT_SCHEMA = {
    "type": "object",
    "required": ["prompt"],
    "properties": {
        "prompt": {"type": "string"},
        "model": {"type": "string", "description": "Gateway model id; defaults to the gateway's image model."},
        "aspect_ratio": {"type": "string", "enum": list(_ASPECT_SIZES)},
        "width": {"type": "integer"},
        "height": {"type": "integer"},
        "seed": {"type": "integer", "description": "Passed through; most web apps ignore it."},
        "image_path": {"type": "string", "description": "One reference image (style/subject)."},
        "image_paths": {"type": "array", "items": {"type": "string"},
                        "description": "Reference images, sent as data URLs."},
        "output_path": {"type": "string"},
        "timeout_seconds": {"type": "integer", "default": 300},
    },
}


class _GatewayImageMixin:
    """Shared behaviour; concrete classes below set the gateway identity."""

    gateway_id: str = ""
    default_url: str = ""
    default_model: str = ""
    max_refs: int = 4
    license_note: str = ""

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

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()  # type: ignore[misc]
        client = self._client()
        info["gateway"] = {"id": self.gateway_id, "url_env": client.url_env,
                           "key_env": client.key_env, "license": self.license_note}
        return info

    @staticmethod
    def _size(inputs: dict[str, Any]) -> str | None:
        if inputs.get("width") and inputs.get("height"):
            return f"{int(inputs['width'])}x{int(inputs['height'])}"
        if inputs.get("aspect_ratio") in _ASPECT_SIZES:
            w, h = _ASPECT_SIZES[inputs["aspect_ratio"]]
            return f"{w}x{h}"
        return None

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        client = self._client()
        if not client.is_configured():
            return ToolResult(success=False, error=(
                f"Gateway {self.gateway_id} is not configured. Set {client.url_env} "
                f"(local or remote URL, including /v1)."))
        if not client.has_usable_account():
            return ToolResult(success=False, error=client.unavailable_reason())

        refs = list(inputs.get("image_paths") or [])
        if inputs.get("image_path"):
            refs.insert(0, inputs["image_path"])
        missing = [p for p in refs if not Path(p).is_file()]
        if missing:
            return ToolResult(success=False, error=f"Reference image not found: {missing[0]}")
        if len(refs) > self.max_refs:
            return ToolResult(success=False, error=(
                f"Gateway {self.gateway_id} accepts at most {self.max_refs} reference images."))

        model = inputs.get("model") or self.default_model
        output_path = Path(inputs.get("output_path") or f"{self.name}_{int(time.time())}.png")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        try:
            data = client.image(model, inputs["prompt"], size=self._size(inputs),
                                seed=inputs.get("seed"), refs=refs or None,
                                timeout=float(inputs.get("timeout_seconds") or 300))
        except GatewayLimited as exc:
            return ToolResult(success=False, error=f"{exc} -- every account is busy or resting; "
                                                   "use another image provider.")
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
                "reference_count": len(refs),
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


_COMMON = dict(
    version="0.1.0",
    tier=ToolTier.GENERATE,
    capability="image_generation",
    stability=ToolStability.EXPERIMENTAL,
    execution_mode=ExecutionMode.SYNC,
    determinism=Determinism.STOCHASTIC,
    runtime=ToolRuntime.API,
)


class GatewayGeminiWebImage(_GatewayImageMixin, BaseTool):
    name = "gateway_geminiweb_image"
    version = _COMMON["version"]
    tier = _COMMON["tier"]
    capability = _COMMON["capability"]
    provider = "geminiweb"
    stability = _COMMON["stability"]
    execution_mode = _COMMON["execution_mode"]
    determinism = _COMMON["determinism"]
    runtime = _COMMON["runtime"]

    gateway_id = "geminiweb"
    default_url = "http://127.0.0.1:18620/v1"
    default_model = "gemini-image"
    max_refs = 4
    license_note = ("Gemini web via gemini-web2api -- Google Gemini terms; "
                    "no clear licence for commercial use of the output")

    dependencies = []
    install_instructions = (
        "Run gemini-web2api (VideoCreatorSystem/provider/gemini-web2api), log in one or "
        "more Google accounts with login.sh, then set GATEWAY_GEMINIWEB_URL "
        "(e.g. http://127.0.0.1:18620/v1) and GATEWAY_GEMINIWEB_API_KEY."
    )
    agent_skills = ["visual-style"]
    capabilities = ["text_to_image", "image_to_image"]
    supports = {"reference_images": True, "custom_size": True, "seed": False, "offline": False}
    best_for = [
        "free scene stills, character sheets and thumbnails through logged-in Gemini web accounts",
        "style or subject transfer from up to 4 reference images",
    ]
    not_good_for = ["guaranteed commercial licensing", "pixel-exact sizes", "real-person likeness"]
    fallback_tools = ["comfyui_image", "google_imagen"]
    input_schema = _INPUT_SCHEMA
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, network_required=True)
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["HTTP 429", "HTTP 5"])
    idempotency_key_fields = ["prompt", "model", "width", "height", "aspect_ratio", "image_paths"]
    side_effects = ["writes image file to output_path", "uses one gateway account"]
    user_visible_verification = ["Inspect the image for prompt adherence and artefacts"]


class GatewayMuseImage(_GatewayImageMixin, BaseTool):
    name = "gateway_muse_image"
    version = _COMMON["version"]
    tier = _COMMON["tier"]
    capability = _COMMON["capability"]
    provider = "muse"
    stability = _COMMON["stability"]
    execution_mode = _COMMON["execution_mode"]
    determinism = _COMMON["determinism"]
    runtime = _COMMON["runtime"]

    gateway_id = "muse"
    default_url = "http://127.0.0.1:18610/v1"
    default_model = "muse-image"
    max_refs = 4
    license_note = ("muse.ai web via muse2api -- Meta AI terms; "
                    "no clear licence for commercial use of the output")

    dependencies = []
    install_instructions = (
        "Run muse2api (VideoCreatorSystem/provider/muse2api), add accounts with "
        "login.sh --label <name>, then set GATEWAY_MUSE_URL "
        "(e.g. http://127.0.0.1:18610/v1) and GATEWAY_MUSE_API_KEY (data/api_key)."
    )
    agent_skills = ["visual-style"]
    capabilities = ["text_to_image", "image_to_image"]
    supports = {"reference_images": True, "custom_size": True, "seed": False, "offline": False}
    best_for = ["free stills when the Gemini web pool is busy", "first frames for muse video clips"]
    not_good_for = ["guaranteed commercial licensing", "real-person likeness"]
    fallback_tools = ["gateway_geminiweb_image", "comfyui_image"]
    input_schema = _INPUT_SCHEMA
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, network_required=True)
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["HTTP 429", "HTTP 5"])
    idempotency_key_fields = ["prompt", "model", "width", "height", "aspect_ratio", "image_paths"]
    side_effects = ["writes image file to output_path", "uses one gateway account"]
    user_visible_verification = ["Inspect the image for prompt adherence and artefacts"]
