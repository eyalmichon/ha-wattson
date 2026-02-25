"""Shared configuration for the MCP dev server.

Secrets (HA refresh token) are read from HA's local
.storage/ files at runtime -- never hardcoded or committed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HA_URL = os.environ.get("HA_URL", "http://localhost:8123")
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
PROJECT_ROOT = os.environ.get("PROJECT_ROOT", "/workspaces/ha-wattson")

_HA_STORAGE = Path(PROJECT_ROOT) / ".dev" / "ha" / ".storage"


def get_ha_refresh_token() -> str | None:
    """Read the HA refresh token from local .storage/auth.

    Finds the token with client_id matching HA_URL.
    Falls back to HA_REFRESH_TOKEN env var if storage is unavailable.
    """
    env = os.environ.get("HA_REFRESH_TOKEN")
    try:
        data = json.loads((_HA_STORAGE / "auth").read_text())
        client_id = f"{HA_URL}/"
        for token in data["data"].get("refresh_tokens", []):
            if token.get("client_id") == client_id:
                return token["token"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        pass
    return env
