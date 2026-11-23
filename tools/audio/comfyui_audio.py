"""Background music (ACE-Step 1.5 turbo) and sound effects (Stable Audio 3 small SFX)
on a ComfyUI server -- local or remote, only a URL.

Server URL, most specific first:
    music: COMFYUI_MUSIC_SERVER_URL -> COMFYUI_SERVER_URL -> http://localhost:8188
    sfx:   COMFYUI_SFX_SERVER_URL   -> COMFYUI_SERVER_URL -> http://localhost:8188

Workflows are the ones VideoCreatorSystem runs in production
(``image-service/workflows/ace-step-music.json`` and ``stable-audio-sfx.json``),
bundled under ``tools/_comfyui/workflows/``. Both write FLAC.

Licences: ACE-Step 1.5 -- Apache-2.0. Stable Audio 3 small -- Stability AI
Community License (free below USD 1M annual revenue).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from tools._comfyui.client import ComfyUIClient, ComfyUIError
from tools._comfyui.metadata import COMFYUI_SETUP_OFFER, workflow_hash
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

_WORKFLOWS = Path(__file__).resolve().parent.parent / "_comfyui" / "workflows"
_ACE_WORKFLOW = "ace-step-1.5-turbo-music.json"
_SFX_WORKFLOW = "stable-audio-3-sfx.json"
_ACE_MODELS = ["ace_step_1.5_turbo_aio.safetensors"]
_SFX_MODELS = ["stable_audio_3_small_sfx.safetensors"]
_KEYS = re.compile(r"\b([A-G][#b]?) (major|minor)\b", re.I)


def _comfy_error(exc: ComfyUIError) -> ToolResult:
    data = {"prompt_id": exc.prompt_id} if exc.prompt_id else {}
    msg = str(exc)
    if exc.prompt_id:
        msg += (f"\nThe job may still be running on the server; call again with "
                f"resume_prompt_id={exc.prompt_id!r} to collect it without resubmitting.")
    return ToolResult(success=False, error=msg, data=data)


class ComfyUIAceStep15Music(BaseTool):
    name = "comfyui_music_ace15"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "music_generation"
    provider = "comfyui"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.LOCAL_GPU

    dependencies = []
    setup_offer = COMFYUI_SETUP_OFFER
    install_instructions = (
        "Run ComfyUI (local or remote) with ace_step_1.5_turbo_aio.safetensors in "
        "models/checkpoints, then set COMFYUI_MUSIC_SERVER_URL (or COMFYUI_SERVER_URL)."
    )
    agent_skills = ["acestep", "comfyui", "music"]
    capabilities = ["generate_background_music", "generate_instrumental"]
    supports = {"seed": True, "lyrics": False, "bpm_from_prompt": True, "offline": True}
    best_for = [
        "free instrumental beds for narration on your own or a LAN ComfyUI GPU",
        "fast drafts: 8 sampling steps (ACE-Step 1.5 turbo)",
    ]
    not_good_for = ["songs with sung lyrics", "setups without a ComfyUI server"]
    fallback_tools = ["comfyui_music", "pixabay_music"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": (
                "Mood/genre tags. '80 bpm' and a key like 'E minor' in the text are "
                "applied to the encoder, e.g. 'quiet curious intro, slow 80 bpm, electric piano'.")},
            "duration_seconds": {"type": "number", "default": 60.0},
            "seed": {"type": "integer"},
            "model": {"type": "string", "description": "Checkpoint file override."},
            "output_path": {"type": "string"},
            "timeout_seconds": {"type": "integer", "default": 1800},
            "resume_prompt_id": {"type": "string"},
        },
    }
    resource_profile = ResourceProfile(cpu_cores=2, ram_mb=8000, vram_mb=10000, network_required=True)
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
    idempotency_key_fields = ["prompt", "duration_seconds", "seed", "model"]
    side_effects = ["writes audio file to output_path"]
    user_visible_verification = ["Listen for mood, tempo and loop seams"]

    def __init__(self) -> None:
        self._client = ComfyUIClient(capability="music")

    def get_status(self) -> ToolStatus:
        if not self._client.is_available():
            return ToolStatus.UNAVAILABLE
        if not self._client.has_node("TextEncodeAceStepAudio1.5"):
            return ToolStatus.UNAVAILABLE
        _, missing = self._client.check_models(_ACE_MODELS)
        return ToolStatus.DEGRADED if missing else ToolStatus.AVAILABLE

    def status_detail(self) -> str | None:
        if self._client.is_available() and not self._client.has_node("TextEncodeAceStepAudio1.5"):
            return (
                f"ComfyUI server at {self._client.server_url} is missing the "
                "ACE-Step 1.5 nodes (TextEncodeAceStepAudio1.5) — update ComfyUI."
            )
        return self._client.status_detail(_ACE_MODELS)

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 30.0 + float(inputs.get("duration_seconds", 60.0)) * 0.5

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        if not self._client.is_available():
            return ToolResult(success=False, error=self._client.unavailable_reason())
        seed = inputs.get("seed")
        if seed is None:
            seed = ComfyUIClient.random_seed()
        seconds = float(inputs.get("duration_seconds") or 60.0)
        prompt = inputs["prompt"]
        output_path = Path(inputs.get("output_path") or f"comfyui_music_ace15_{seed}.flac")

        wf = ComfyUIClient.load_workflow(_WORKFLOWS / _ACE_WORKFLOW)
        if inputs.get("model"):
            wf["1"]["inputs"]["ckpt_name"] = inputs["model"]
        enc = wf["2"]["inputs"]
        enc.update(tags=prompt, seed=seed, duration=seconds)
        if m := re.search(r"(\d{2,3})\s*bpm", prompt, re.I):
            enc["bpm"] = int(m[1])
        if m := _KEYS.search(prompt):
            enc["keyscale"] = f"{m[1][0].upper()}{m[1][1:]} {m[2].lower()}"
        wf["5"]["inputs"]["seconds"] = seconds
        wf["6"]["inputs"]["seed"] = seed
        wf["8"]["inputs"]["filename_prefix"] = f"openmontage/{output_path.stem}"

        start = time.time()
        try:
            paths = self._client.generate(wf, output_node="8", dest=output_path,
                                          timeout=int(inputs.get("timeout_seconds") or 1800),
                                          interval=5, resume_prompt_id=inputs.get("resume_prompt_id"))
        except ComfyUIError as exc:
            return _comfy_error(exc)
        except Exception as exc:  # network, bad workflow
            return ToolResult(success=False, error=f"ComfyUI music generation failed: {exc}")
        model = wf["1"]["inputs"]["ckpt_name"]
        return ToolResult(
            success=True,
            data={
                "provider": "comfyui",
                "server": self._client.server_url,
                "model": model,
                "prompt": prompt,
                "duration_seconds": seconds,
                "bpm": enc.get("bpm"),
                "keyscale": enc.get("keyscale"),
                "output": str(paths[0]),
                "format": paths[0].suffix.lstrip("."),
                "license": "Apache-2.0",
                "workflow_provenance": {"source": "bundled", "workflow": _ACE_WORKFLOW,
                                        "workflow_hash_sha256": workflow_hash(wf), "output_node": "8"},
            },
            artifacts=[str(p) for p in paths],
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
            seed=seed,
            model=model,
        )


class ComfyUISFX(BaseTool):
    name = "comfyui_sfx"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "sound_effects"
    provider = "comfyui"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.LOCAL_GPU

    dependencies = []
    setup_offer = COMFYUI_SETUP_OFFER
    install_instructions = (
        "Run ComfyUI (local or remote) with stable_audio_3_small_sfx.safetensors in "
        "models/checkpoints and t5gemma_b_b_ul2.safetensors in models/text_encoders, then set "
        "COMFYUI_SFX_SERVER_URL (or COMFYUI_SERVER_URL)."
    )
    agent_skills = ["sound-effects", "comfyui"]
    capabilities = ["generate_sound_effect"]
    supports = {"seed": True, "offline": True, "max_seconds": 10}
    best_for = ["free whooshes, hits, ambience and foley one-shots on your own GPU"]
    not_good_for = ["music", "speech", "setups without a ComfyUI server"]
    fallback_tools = ["minicut_sfx"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "e.g. 'short soft whoosh transition'"},
            "duration_seconds": {"type": "number", "default": 2.0, "minimum": 0.5, "maximum": 10},
            "negative_prompt": {"type": "string", "default": ""},
            "seed": {"type": "integer"},
            "model": {"type": "string", "description": "Checkpoint file override."},
            "output_path": {"type": "string"},
            "timeout_seconds": {"type": "integer", "default": 600},
            "resume_prompt_id": {"type": "string"},
        },
    }
    resource_profile = ResourceProfile(cpu_cores=2, ram_mb=4000, vram_mb=4000, network_required=True)
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
    idempotency_key_fields = ["prompt", "duration_seconds", "negative_prompt", "seed", "model"]
    side_effects = ["writes audio file to output_path"]
    user_visible_verification = ["Listen for the intended sound and clean attack/tail"]

    def __init__(self) -> None:
        self._client = ComfyUIClient(capability="sfx")

    def get_status(self) -> ToolStatus:
        if not self._client.is_available():
            return ToolStatus.UNAVAILABLE
        _, missing = self._client.check_models(_SFX_MODELS)
        return ToolStatus.DEGRADED if missing else ToolStatus.AVAILABLE

    def status_detail(self) -> str | None:
        return self._client.status_detail(_SFX_MODELS)

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 15.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        if not self._client.is_available():
            return ToolResult(success=False, error=self._client.unavailable_reason())
        seed = inputs.get("seed")
        if seed is None:
            seed = ComfyUIClient.random_seed()
        seconds = min(max(float(inputs.get("duration_seconds") or 2.0), 0.5), 10.0)
        output_path = Path(inputs.get("output_path") or f"comfyui_sfx_{seed}.flac")

        wf = ComfyUIClient.load_workflow(_WORKFLOWS / _SFX_WORKFLOW)
        if inputs.get("model"):
            wf["1"]["inputs"]["ckpt_name"] = inputs["model"]
        wf["3"]["inputs"]["text"] = inputs["prompt"]
        wf["4"]["inputs"]["text"] = inputs.get("negative_prompt") or ""
        wf["5"]["inputs"]["seconds"] = seconds
        wf["6"]["inputs"]["seed"] = seed
        wf["8"]["inputs"]["filename_prefix"] = f"openmontage/{output_path.stem}"

        start = time.time()
        try:
            paths = self._client.generate(wf, output_node="8", dest=output_path,
                                          timeout=int(inputs.get("timeout_seconds") or 600),
                                          interval=2, resume_prompt_id=inputs.get("resume_prompt_id"))
        except ComfyUIError as exc:
            return _comfy_error(exc)
        except Exception as exc:
            return ToolResult(success=False, error=f"ComfyUI SFX generation failed: {exc}")
        model = wf["1"]["inputs"]["ckpt_name"]
        return ToolResult(
            success=True,
            data={
                "provider": "comfyui",
                "server": self._client.server_url,
                "model": model,
                "prompt": inputs["prompt"],
                "duration_seconds": seconds,
                "output": str(paths[0]),
                "format": paths[0].suffix.lstrip("."),
                "license": "Stability AI Community License (free below USD 1M annual revenue)",
                "workflow_provenance": {"source": "bundled", "workflow": _SFX_WORKFLOW,
                                        "workflow_hash_sha256": workflow_hash(wf), "output_node": "8"},
            },
            artifacts=[str(p) for p in paths],
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
            seed=seed,
            model=model,
        )
