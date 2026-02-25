"""HA lifecycle and entity tools."""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from mcp_server.config import HA_URL, PROJECT_ROOT
from mcp_server.ha_helpers import (
    api_request,
    clear_pycache,
    ensure_custom_components_symlink,
    find_hass_pids,
    kill_hass,
)

_HTTP_OK = 200
_PROJECT = Path(PROJECT_ROOT)
_DOMAIN = "wattson"


def register(mcp) -> None:  # noqa: ANN001
    """Register all HA tools on the given FastMCP instance."""

    @mcp.tool
    def ha_status() -> dict[str, Any]:
        """Check if Home Assistant is running and responding.

        Returns HA process status and HTTP reachability.
        """
        pids = find_hass_pids()
        http_ok = False
        if pids:
            try:
                result = subprocess.run(
                    ["/usr/bin/curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"{HA_URL}/"],
                    capture_output=True, text=True, timeout=5,
                    check=False,
                )
                http_ok = result.stdout.strip() == str(_HTTP_OK)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return {"running": len(pids) > 0, "pids": pids, "http_responding": http_ok, "url": HA_URL}

    @mcp.tool
    def ha_restart(wait: bool = True, timeout_seconds: int = 60) -> dict[str, Any]:
        """Restart Home Assistant cleanly.

        Kills running hass, clears __pycache__, starts fresh.
        Waits until HA responds by default.

        Args:
            wait: Wait until HA is fully up (default True).
            timeout_seconds: Max seconds to wait (default 60).
        """
        killed = kill_hass()
        if killed:
            time.sleep(3)

        clear_pycache()
        ensure_custom_components_symlink()

        subprocess.Popen(
            ["/usr/bin/bash", str(_PROJECT / "scripts" / "develop")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        if not wait:
            return {"status": "started", "message": "HA starting in background"}

        start = time.time()
        while time.time() - start < timeout_seconds:
            time.sleep(5)
            try:
                result = subprocess.run(
                    ["/usr/bin/curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"{HA_URL}/"],
                    capture_output=True, text=True, timeout=5,
                    check=False,
                )
                if result.stdout.strip() == str(_HTTP_OK):
                    return {"status": "ready", "elapsed_seconds": int(time.time() - start), "url": HA_URL}
            except (OSError, subprocess.TimeoutExpired):
                continue

        return {"status": "timeout", "message": f"HA not ready after {timeout_seconds}s"}

    @mcp.tool
    def ha_logs(lines: int = 30, filter_text: str | None = None) -> str:
        """Get recent Home Assistant logs.

        Args:
            lines: Number of lines to return (default 30).
            filter_text: Optional text to filter log lines (case-insensitive).
        """
        log_path = _PROJECT / ".dev" / "ha" / "home-assistant.log"
        try:
            all_lines = log_path.read_text().splitlines(keepends=True)
        except FileNotFoundError:
            return "Log file not found. Is HA configured?"

        if filter_text:
            all_lines = [line for line in all_lines if filter_text.lower() in line.lower()]

        return "".join(all_lines[-lines:])

    @mcp.tool
    async def ha_get_state(entity_id: str) -> dict[str, Any]:
        """Get the current state and attributes of an HA entity.

        Args:
            entity_id: Full entity ID (e.g. 'sensor.example').
        """
        if not re.match(r"^[a-z_]+\.[a-z0-9_]+$", entity_id):
            return {"error": f"Invalid entity_id format: {entity_id}"}
        result = await api_request("GET", f"/api/states/{entity_id}")
        if "error" in result:
            return result
        body = result["body"]
        if isinstance(body, dict) and "state" in body:
            return {
                "entity_id": body.get("entity_id"),
                "state": body.get("state"),
                "attributes": body.get("attributes", {}),
            }
        return result

    @mcp.tool
    async def ha_find_entities(pattern: str = "") -> list[dict[str, str]]:
        """Find all HA entities matching a pattern in their entity_id.

        Args:
            pattern: Text to match (case-insensitive).
        """
        result = await api_request("GET", "/api/states")
        if "error" in result:
            return [result]

        return [
            {"entity_id": s["entity_id"], "state": s["state"]}
            for s in result["body"]
            if pattern.lower() in s.get("entity_id", "").lower()
        ]

    @mcp.tool
    async def ha_call_service(
        domain: str, service: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call a Home Assistant service.

        Args:
            domain: Service domain (e.g. 'switch', 'logger').
            service: Service name (e.g. 'turn_on', 'set_level').
            data: Optional service data dict.
        """
        if not re.match(r"^[a-z_]+$", domain) or not re.match(r"^[a-z_]+$", service):
            return {"error": f"Invalid domain/service format: {domain}/{service}"}
        return await api_request("POST", f"/api/services/{domain}/{service}", data or {})

    @mcp.tool
    async def ha_integration_status(domain: str = _DOMAIN) -> dict[str, Any]:
        """Get the status of an HA integration config entry.

        Args:
            domain: Integration domain (defaults to this integration).
        """
        result = await api_request("GET", "/api/config/config_entries/entry")
        if "error" in result:
            return result

        manifest_version = None
        manifest_path = Path(PROJECT_ROOT) / "custom_components" / domain / "manifest.json"
        with contextlib.suppress(FileNotFoundError, json.JSONDecodeError, KeyError):
            manifest_version = json.loads(manifest_path.read_text())["version"]

        entries = [
            {
                "entry_id": e["entry_id"],
                "state": e["state"],
                "options": e.get("options", {}),
                "integration_version": manifest_version or f"{e.get('version')}.{e.get('minor_version')}",
            }
            for e in result["body"]
            if e.get("domain") == domain
        ]
        return {"entries": entries} if entries else {"error": f"No {domain} entries found"}

    @mcp.tool
    async def ha_reload_integration(domain: str = _DOMAIN) -> dict[str, Any]:
        """Reload a config entry without restarting HA.

        Args:
            domain: Integration domain (defaults to this integration).
        """
        result = await api_request("GET", "/api/config/config_entries/entry")
        if "error" in result:
            return result

        for entry in result["body"]:
            if entry.get("domain") == domain:
                entry_id = entry["entry_id"]
                reload_result = await api_request(
                    "POST", f"/api/config/config_entries/entry/{entry_id}/reload"
                )
                return {"entry_id": entry_id, "result": reload_result.get("body")}

        return {"error": f"No {domain} config entry found"}
