"""Client for local/remote web-app gateways (muse2api, gemini-web2api, ...)."""

from tools._gateway.client import GatewayClient, GatewayError, GatewayLimited

__all__ = ["GatewayClient", "GatewayError", "GatewayLimited"]
