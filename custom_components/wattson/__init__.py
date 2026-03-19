"""The Wattson integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.core import ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PLATFORMS
from .coordinator import WattsonCoordinator
from .store import WattsonStore

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .profile_matcher import Profile

_LOGGER = logging.getLogger(__name__)

SERVICE_LIST_PROFILES = "list_profiles"
SERVICE_RENAME_PROFILE = "rename_profile"
SERVICE_DELETE_PROFILE = "delete_profile"
SERVICE_RENAME_PHASE = "rename_phase"
SERVICE_SET_PHASE_DONE = "set_phase_done"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_ENTITY_ID = "entity_id"
ATTR_NAME = "name"
ATTR_PHASE_INDEX = "phase_index"
ATTR_DONE = "done"

SERVICE_SCHEMA_LIST = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): str})
SERVICE_SCHEMA_RENAME = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): str,
        vol.Required(ATTR_NAME): str,
    }
)
SERVICE_SCHEMA_DELETE = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): str,
    }
)
SERVICE_SCHEMA_RENAME_PHASE = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): str,
        vol.Required(ATTR_PHASE_INDEX): int,
        vol.Required(ATTR_NAME): str,
    }
)
SERVICE_SCHEMA_SET_PHASE_DONE = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): str,
        vol.Required(ATTR_PHASE_INDEX): int,
        vol.Required(ATTR_DONE): bool,
    }
)


def _get_coordinator(hass: HomeAssistant, entry_id: str) -> WattsonCoordinator:
    """Look up a coordinator by config entry ID, raising on invalid input."""
    entries = hass.data.get(DOMAIN, {})
    entry_data = entries.get(entry_id)
    if entry_data is None:
        msg = f"No Wattson entry with ID {entry_id}"
        raise ServiceValidationError(msg)
    return entry_data["coordinator"]


def _resolve_profile_select(
    hass: HomeAssistant, entity_id: str
) -> tuple[WattsonCoordinator, str]:
    """Resolve a profile select entity_id to (coordinator, profile_id)."""
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None or entry.config_entry_id is None:
        msg = f"Entity {entity_id} is not a Wattson profile selector"
        raise ServiceValidationError(msg)

    coordinator = _get_coordinator(hass, entry.config_entry_id)

    state = hass.states.get(entity_id)
    if state is None:
        msg = f"Entity {entity_id} has no state"
        raise ServiceValidationError(msg)

    profile_id = state.attributes.get("profile_id")
    if not profile_id:
        msg = "No profile is currently selected"
        raise ServiceValidationError(msg)

    return coordinator, profile_id


def _resolve_profile(
    hass: HomeAssistant, entity_id: str
) -> tuple[WattsonCoordinator, str, Profile]:
    """Resolve entity_id to (coordinator, profile_id, profile). Raises on failure."""
    coordinator, profile_id = _resolve_profile_select(hass, entity_id)
    profile = coordinator.store.get_profile(profile_id)
    if profile is None:
        msg = f"Profile {profile_id} not found"
        raise ServiceValidationError(msg)
    return coordinator, profile_id, profile


def _validate_phase_index(profile: Profile, phase_index: int) -> None:
    """Raise if phase_index is out of bounds for the profile."""
    if not profile.phases or phase_index < 0 or phase_index >= len(profile.phases):
        msg = f"Phase index {phase_index} out of range"
        raise ServiceValidationError(msg)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Wattson from a config entry."""
    store = WattsonStore(hass, entry.entry_id)
    coordinator = WattsonCoordinator(hass, entry, store)
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services once (first entry to load).
    if not hass.services.has_service(DOMAIN, SERVICE_LIST_PROFILES):
        _register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator: WattsonCoordinator = entry_data["coordinator"]
        await coordinator.async_shutdown()

    # Remove services when no entries remain.
    if not hass.data.get(DOMAIN):
        for svc in (
            SERVICE_LIST_PROFILES,
            SERVICE_RENAME_PROFILE,
            SERVICE_DELETE_PROFILE,
            SERVICE_RENAME_PHASE,
            SERVICE_SET_PHASE_DONE,
        ):
            hass.services.async_remove(DOMAIN, svc)

    return unload_ok


def _register_services(hass: HomeAssistant) -> None:
    """Register Wattson domain services."""

    async def handle_list_profiles(call: ServiceCall) -> ServiceResponse:
        entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
        coordinator = _get_coordinator(hass, entry_id)
        return {
            "profiles": [p.to_dict() for p in coordinator.store.profiles],
        }

    async def handle_rename_profile(call: ServiceCall) -> None:
        coordinator, profile_id, _ = _resolve_profile(hass, call.data[ATTR_ENTITY_ID])
        name = call.data[ATTR_NAME]
        await coordinator.async_rename_profile(profile_id, name)
        _LOGGER.debug("Renamed profile %s to '%s'", profile_id, name)

    async def handle_delete_profile(call: ServiceCall) -> None:
        coordinator, profile_id, _ = _resolve_profile(hass, call.data[ATTR_ENTITY_ID])
        await coordinator.async_delete_profile(profile_id)
        _LOGGER.debug("Deleted profile %s", profile_id)

    async def handle_rename_phase(call: ServiceCall) -> None:
        coordinator, profile_id, profile = _resolve_profile(
            hass, call.data[ATTR_ENTITY_ID]
        )
        phase_index = call.data[ATTR_PHASE_INDEX]
        _validate_phase_index(profile, phase_index)
        name = call.data[ATTR_NAME]
        await coordinator.async_rename_phase(profile_id, phase_index, name)
        _LOGGER.debug("Renamed phase %d of %s to '%s'", phase_index, profile_id, name)

    async def handle_set_phase_done(call: ServiceCall) -> None:
        coordinator, profile_id, profile = _resolve_profile(
            hass, call.data[ATTR_ENTITY_ID]
        )
        phase_index = call.data[ATTR_PHASE_INDEX]
        _validate_phase_index(profile, phase_index)
        done = call.data[ATTR_DONE]
        await coordinator.async_set_phase_done(profile_id, phase_index, done=done)
        _LOGGER.debug("Set phase %d done=%s for %s", phase_index, done, profile_id)

    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_PROFILES,
        handle_list_profiles,
        schema=SERVICE_SCHEMA_LIST,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RENAME_PROFILE,
        handle_rename_profile,
        schema=SERVICE_SCHEMA_RENAME,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_PROFILE,
        handle_delete_profile,
        schema=SERVICE_SCHEMA_DELETE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RENAME_PHASE,
        handle_rename_phase,
        schema=SERVICE_SCHEMA_RENAME_PHASE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_PHASE_DONE,
        handle_set_phase_done,
        schema=SERVICE_SCHEMA_SET_PHASE_DONE,
    )
