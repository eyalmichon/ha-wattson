"""HTTP helpers for talking to the Home Assistant API."""

from __future__ import annotations

import contextlib
import signal
import subprocess
from pathlib import Path
from typing import Any

import aiohttp

from .config import HA_URL, PROJECT_ROOT, get_ha_refresh_token

_HTTP_OK = 200
_PROJECT = Path(PROJECT_ROOT)


async def get_access_token() -> str | None:
    """Get a short-lived HA access token from the refresh token."""
    refresh_token = get_ha_refresh_token()
    if not refresh_token:
        return None
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"{HA_URL}/auth/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": f"{HA_URL}/",
                    "refresh_token": refresh_token,
                },
            ) as resp,
        ):
            if resp.status == _HTTP_OK:
                return (await resp.json())["access_token"]
    except (aiohttp.ClientError, OSError, TimeoutError):
        pass
    return None


async def api_request(
    method: str, path: str, json_data: dict | None = None
) -> dict[str, Any]:
    """Make an authenticated HA API request."""
    token = await get_access_token()
    if not token:
        return {"error": "Could not get HA access token. Is HA running?"}

    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        kwargs: dict[str, Any] = {"headers": headers}
        if json_data is not None:
            kwargs["json"] = json_data

        async with session.request(method, f"{HA_URL}{path}", **kwargs) as resp:
            try:
                body = await resp.json()
            except (ValueError, aiohttp.ContentTypeError):
                body = await resp.text()
            return {"status": resp.status, "body": body}


def find_hass_pids() -> list[int]:
    """Find running hass process PIDs."""
    try:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-f", "python.*hass"],
            capture_output=True,
            text=True,
            check=False,
        )
        return [int(p) for p in result.stdout.strip().split("\n") if p]
    except OSError:
        return []


def kill_hass() -> list[int]:
    """Kill all running hass processes. Returns the PIDs that were killed."""
    pids = find_hass_pids()
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            import os  # noqa: PLC0415

            os.kill(pid, signal.SIGTERM)
    return pids


def clear_pycache() -> None:
    """Remove all __pycache__ dirs under custom_components."""
    cc = _PROJECT / "custom_components"
    for d in cc.rglob("__pycache__"):
        if d.is_dir():
            import shutil  # noqa: PLC0415

            shutil.rmtree(d, ignore_errors=True)


def ensure_custom_components_symlink() -> None:
    """Ensure the HA config dir has a symlink to custom_components."""
    link = _PROJECT / ".dev" / "ha" / "custom_components"
    target = _PROJECT / "custom_components"
    if not link.exists():
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
