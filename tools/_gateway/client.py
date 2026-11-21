"""HTTP client for gateways that wrap a web app behind an OpenAI-style API.

A gateway (``muse2api``, ``gemini-web2api``...) is a separate process that keeps
its own pool of logged-in accounts. OpenMontage only needs its URL -- local
(``http://127.0.0.1:18610/v1``) or remote, it is the same -- plus an optional
bearer key. Contract (VideoCreatorSystem ``studio/design/GATEWAY-CONTRACT.md``):

    GET  {base}/models                  connectivity check
    GET  {base}/capacity                {"usable", "busy", "total"} (optional)
    POST {base}/images/generations      {"data": [{"b64_json"} | {"url"}]}
    POST {base}/videos                  {"id", "status"}
    GET  {base}/videos/{id}             status queued|running|in_progress|succeeded|
                                        completed|failed|cancelled|expired
    GET  {base}/videos/{id}/content     mp4 bytes when the task has no result.url

Configuration per gateway id (upper-cased): ``GATEWAY_<ID>_URL`` and
``GATEWAY_<ID>_API_KEY``. Cookies never pass through here.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import requests

_DONE = {"succeeded", "completed", "complete", "success"}
_FAILED = {"failed", "cancelled", "canceled", "expired", "error"}
_LIMIT_RE = re.compile(r"limit|quota|credits|no usable account|all accounts", re.I)
_STATUS_TTL_S = 30.0


class GatewayError(RuntimeError):
    """The gateway answered with an error the caller should surface."""


class GatewayLimited(GatewayError):
    """Every account is busy, resting or out of quota -- try another provider."""


class GatewayClient:
    def __init__(self, gateway_id: str, default_url: str) -> None:
        self.id = gateway_id
        self.env_prefix = f"GATEWAY_{gateway_id.upper()}"
        self.default_url = default_url
        self._status_cache: tuple[float, dict[str, Any]] | None = None

    # -- configuration -------------------------------------------------
    @property
    def url_env(self) -> str:
        return f"{self.env_prefix}_URL"

    @property
    def key_env(self) -> str:
        return f"{self.env_prefix}_API_KEY"

    @property
    def base(self) -> str:
        return (os.environ.get(self.url_env) or self.default_url).rstrip("/")

    def is_configured(self) -> bool:
        return bool(os.environ.get(self.url_env))

    def _headers(self) -> dict[str, str]:
        key = os.environ.get(self.key_env, "").strip()
        return {"Authorization": f"Bearer {key}"} if key else {}

    # -- status ----------------------------------------------------------
    def status(self, fresh: bool = False) -> dict[str, Any]:
        """{"up", "usable", "busy", "total", "models", "error"} -- cached 30 s so
        provider ranking does not hit the gateway once per candidate."""
        now = time.monotonic()
        if not fresh and self._status_cache and now - self._status_cache[0] < _STATUS_TTL_S:
            return self._status_cache[1]
        info: dict[str, Any] = {"up": False, "usable": 0, "busy": 0, "total": 0,
                                "models": [], "error": None}
        try:
            r = requests.get(f"{self.base}/models", headers=self._headers(), timeout=5)
            r.raise_for_status()
            info["models"] = [m.get("id") for m in (r.json().get("data") or [])
                              if isinstance(m, dict)]
            info["up"] = True
        except (requests.RequestException, ValueError) as exc:
            info["error"] = f"{self.base}/models: {exc}"
        if info["up"]:
            try:
                r = requests.get(f"{self.base}/capacity", headers=self._headers(), timeout=5)
                if r.status_code == 404:          # optional endpoint: assume one slot
                    info.update(usable=1, total=1)
                else:
                    r.raise_for_status()
                    js = r.json()
                    info["usable"] = int(js.get("usable") or 0)
                    info["busy"] = int(js.get("busy") or 0)
                    info["total"] = int(js.get("total") or info["usable"])
            except (requests.RequestException, ValueError, TypeError) as exc:
                info.update(usable=1, total=1, error=f"capacity: {exc}")
        self._status_cache = (now, info)
        return info

    def has_usable_account(self) -> bool:
        st = self.status()
        return st["up"] and st["usable"] > 0

    def unavailable_reason(self) -> str:
        st = self.status()
        if not st["up"]:
            return (f"Gateway {self.id} at {self.base} is not reachable ({st['error']}). "
                    f"Start it or set {self.url_env}.")
        return (f"Gateway {self.id} has no usable account right now "
                f"({st['usable']}/{st['total']} usable). Log an account back in "
                f"or wait for its cooldown.")

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def data_url(path: str | Path) -> str:
        p = Path(path)
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"

    def _request(self, method: str, url: str, timeout: float, **kw) -> requests.Response:
        try:
            r = requests.request(method, url, headers=self._headers(), timeout=timeout, **kw)
        except requests.RequestException as exc:
            raise GatewayError(f"gateway {self.id}: {exc}") from exc
        if r.status_code == 429 or r.status_code >= 500:
            text = r.text[:300]
            raise GatewayLimited(f"gateway {self.id} HTTP {r.status_code}: {text}")
        if r.status_code >= 400:
            raise GatewayError(f"gateway {self.id} HTTP {r.status_code}: {r.text[:300]}")
        return r

    def _download(self, url: str, timeout: float) -> bytes:
        url = urljoin(self.base + "/", url)
        same_host = urlsplit(url).netloc == urlsplit(self.base).netloc
        try:
            # The key only goes back to the gateway itself, never to a CDN.
            r = requests.get(url, headers=self._headers() if same_host else {}, timeout=timeout)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise GatewayError(f"gateway {self.id}: download failed: {exc}") from exc
        return r.content

    # -- generation --------------------------------------------------------
    def image(self, model: str, prompt: str, *, size: str | None = None,
              seed: int | None = None, refs: list[str] | None = None,
              timeout: float = 300) -> bytes:
        body: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1,
                                "response_format": "b64_json"}
        if size:
            body["size"] = size
        if seed is not None:
            body["seed"] = int(seed)
        if refs:
            body["image"] = [self.data_url(p) for p in refs]
        js = self._request("POST", f"{self.base}/images/generations", timeout, json=body).json()
        data = js.get("data") or []
        if not data:
            raise GatewayError(f"gateway {self.id}: response has no data[]")
        item = data[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            return self._download(item["url"], timeout)
        raise GatewayError(f"gateway {self.id}: data[0] has neither b64_json nor url")

    def video(self, model: str, prompt: str, *, seconds: int = 5, size: str | None = None,
              seed: int | None = None, first_frame: str | None = None,
              refs: list[str] | None = None, timeout: float = 900, poll_s: float = 3.0,
              on_progress: Callable[[str], None] | None = None) -> bytes:
        body: dict[str, Any] = {"model": model, "prompt": prompt, "seconds": max(1, int(seconds))}
        if size:
            body["size"] = size
        if seed is not None:
            body["seed"] = int(seed)
        if first_frame:
            body["image"] = self.data_url(first_frame)
        if refs:
            body["images"] = [self.data_url(p) for p in refs]
        task = self._request("POST", f"{self.base}/videos", 60, json=body).json()
        vid = task.get("id")
        if not vid:
            raise GatewayError(f"gateway {self.id}: POST /videos returned no id")
        deadline = time.monotonic() + timeout
        last = None
        while True:
            status = str(task.get("status") or "").lower()
            if on_progress and status != last:
                on_progress(f"gateway {self.id}: video {vid} {status or 'submitted'}")
                last = status
            if status in _DONE:
                result = task.get("result") or {}
                url = (result.get("url") or (result.get("video") or {}).get("url")
                       or task.get("url") or f"{self.base}/videos/{vid}/content")
                return self._download(url, timeout)
            if status in _FAILED:
                err = str(task.get("error") or status)
                if _LIMIT_RE.search(err):
                    raise GatewayLimited(f"gateway {self.id}: {err[:300]}")
                raise GatewayError(f"gateway {self.id}: video {vid} {status}: {err[:300]}")
            if time.monotonic() >= deadline:
                raise GatewayError(f"gateway {self.id}: video {vid} timed out after {timeout}s")
            time.sleep(poll_s)
            task = self._request("GET", f"{self.base}/videos/{vid}", 30).json()
