"""MQTT broker and publishing tools."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from mcp_server.config import MQTT_HOST, MQTT_PORT

_MOSQUITTO_CONF = Path("/tmp/mosquitto.conf")


def register(mcp) -> None:  # noqa: ANN001
    """Register all MQTT tools on the given FastMCP instance."""

    @mcp.tool
    def mqtt_publish(topic: str, payload: str | dict) -> dict[str, Any]:
        """Publish a message to the local MQTT broker.

        Args:
            topic: MQTT topic (e.g. 'homeassistant/sensor/state').
            payload: Message payload (string or dict that will be JSON-encoded).
        """
        if isinstance(payload, dict):
            payload = json.dumps(payload)

        try:
            result = subprocess.run(
                ["mosquitto_pub", "-h", MQTT_HOST, "-p", str(MQTT_PORT), "-t", topic, "-m", payload],
                capture_output=True, text=True, timeout=5,
                check=False,
            )
            if result.returncode == 0:
                return {"status": "published", "topic": topic}
        except FileNotFoundError:
            return {"status": "error", "message": "mosquitto_pub not found. Install mosquitto-clients."}
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "stderr": result.stderr}
        return {"status": "error", "message": "unexpected state"}  # pragma: no cover

    @mcp.tool
    def mqtt_ensure_broker() -> dict[str, Any]:
        """Ensure the Mosquitto MQTT broker is running.

        Starts it if not already running, with anonymous access on port 1883.
        """
        try:
            result = subprocess.run(
                ["pgrep", "mosquitto"],
                capture_output=True, text=True,
                check=False,
            )
            if result.returncode == 0:
                return {"status": "already_running", "pid": result.stdout.strip()}
        except OSError:
            pass

        if not _MOSQUITTO_CONF.exists():
            _MOSQUITTO_CONF.write_text("listener 1883\nallow_anonymous true\n")

        subprocess.Popen(
            ["mosquitto", "-c", str(_MOSQUITTO_CONF), "-d"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)

        try:
            result = subprocess.run(
                ["pgrep", "mosquitto"],
                capture_output=True, text=True,
                check=False,
            )
            if result.returncode == 0:
                return {"status": "started", "pid": result.stdout.strip()}
        except OSError:
            pass

        return {"status": "error", "message": "Failed to start mosquitto"}
