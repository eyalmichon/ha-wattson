"""Persistent storage for Wattson cycles and profiles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from .const import DOMAIN, MAX_STORED_CYCLES
from .cycle_recorder import CycleData
from .profile_matcher import Profile

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

STORAGE_VERSION = 1


class WattsonStore:
    """Manages persistent storage of cycle history and learned profiles."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}",
        )
        self._cycles: list[CycleData] = []
        self._profiles: list[Profile] = []

    async def async_load(self) -> None:
        """Load data from disk."""
        data = await self._store.async_load()
        if data is None:
            return

        self._cycles = [CycleData(**c) for c in data.get("cycles", [])]
        self._profiles = [Profile.from_dict(p) for p in data.get("profiles", [])]

    async def async_save(self) -> None:
        """Persist data to disk."""
        data: dict[str, Any] = {
            "cycles": [c.to_dict() for c in self._cycles],
            "profiles": [p.to_dict() for p in self._profiles],
        }
        await self._store.async_save(data)

    @property
    def cycles(self) -> list[CycleData]:
        """All stored cycles."""
        return self._cycles

    @property
    def profiles(self) -> list[Profile]:
        """All learned profiles."""
        return self._profiles

    def add_cycle(self, cycle: CycleData) -> None:
        """Add a completed cycle, pruning old entries."""
        self._cycles.append(cycle)
        if len(self._cycles) > MAX_STORED_CYCLES:
            self._cycles = self._cycles[-MAX_STORED_CYCLES:]

    def add_profile(self, profile: Profile) -> None:
        """Add a new profile."""
        self._profiles.append(profile)

    def update_profile(self, profile: Profile) -> None:
        """Replace an existing profile by id."""
        self._profiles = [profile if p.id == profile.id else p for p in self._profiles]

    def delete_profile(self, profile_id: str) -> None:
        """Remove a profile by id."""
        self._profiles = [p for p in self._profiles if p.id != profile_id]

    def get_profile(self, profile_id: str) -> Profile | None:
        """Get a profile by id."""
        for p in self._profiles:
            if p.id == profile_id:
                return p
        return None
