"""Wattson coordinator — orchestrates cycle detection, recording, and matching."""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import (
    CONF_END_DELAY,
    CONF_ENTITY_ID,
    CONF_MQTT_TOPIC,
    CONF_OFF_THRESHOLD,
    CONF_SOURCE_TYPE,
    CONF_START_THRESHOLD,
    DEFAULT_END_DELAY_S,
    DEFAULT_OFF_THRESHOLD_W,
    DEFAULT_START_THRESHOLD_W,
    ESTIMATE_SMOOTH_ALPHA,
    ESTIMATE_SMOOTH_THRESHOLD_S,
    EVENT_PHASE_CHANGED,
    MIN_CYCLE_DURATION_S,
    MIN_SAMPLES,
    SOURCE_MQTT,
    CycleState,
    adaptive_phase_params,
)
from .cycle_detector import CycleDetector, CycleDetectorConfig
from .cycle_recorder import CycleRecorder
from .phase_extractor import extract_phases
from .profile_matcher import MatchResult, ProfileMatcher

if TYPE_CHECKING:
    from homeassistant.components.mqtt.models import ReceiveMessage
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity

    from .cycle_recorder import CycleData
    from .profile_matcher import Profile, ProfilePhase
    from .select import WattsonPhaseSelect, WattsonProfileSelect
    from .store import WattsonStore

_LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL = timedelta(seconds=5)


class WattsonCoordinator:
    """Coordinates cycle detection, recording, and profile matching for one appliance."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: WattsonStore,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.store = store

        self._unsub_listener: CALLBACK_TYPE | None = None
        self._unsub_mqtt: CALLBACK_TYPE | None = None
        self._unsub_poll: CALLBACK_TYPE | None = None

        # Entities register themselves here so the coordinator can update them.
        self._entities: list[Entity] = []

        # Read thresholds from options (with fallback to data, then defaults).
        opts = {**entry.data, **entry.options}
        start_threshold = float(
            opts.get(CONF_START_THRESHOLD, DEFAULT_START_THRESHOLD_W)
        )
        off_threshold = float(opts.get(CONF_OFF_THRESHOLD, DEFAULT_OFF_THRESHOLD_W))
        end_delay_raw = float(opts.get(CONF_END_DELAY, 0))
        # A value of 0 (or unset) means "auto-adapt from profile".
        user_overrides_end_delay = CONF_END_DELAY in opts and end_delay_raw > 0
        end_delay = end_delay_raw if user_overrides_end_delay else DEFAULT_END_DELAY_S

        self.detector = CycleDetector(
            CycleDetectorConfig(
                start_threshold_w=start_threshold,
                off_threshold_w=off_threshold,
                end_delay_s=end_delay,
            )
        )
        self._user_end_delay: float | None = (
            end_delay_raw if user_overrides_end_delay else None
        )
        self.recorder = CycleRecorder()
        self.matcher = ProfileMatcher()

        self.current_power: float = 0.0
        self.match_result: MatchResult | None = None
        self.time_remaining: float | None = None
        self._recording = False

        # Phase tracking state.
        self.current_phase_index: int | None = None
        self.current_phase_name: str | None = None
        self.cycle_done_by_phase: bool = False
        self._rolling_powers: deque[tuple[float, float]] = deque()
        self._phase_confirm_since: float | None = None
        self._phase_confirm_index: int | None = None

    def register_entity(self, entity: Entity) -> None:
        """Register an entity to be updated on state changes."""
        self._entities.append(entity)

    def get_profile_select(self) -> WattsonProfileSelect | None:
        """Return the profile select entity, if registered."""
        from .select import WattsonProfileSelect

        for entity in self._entities:
            if isinstance(entity, WattsonProfileSelect):
                return entity
        return None

    def get_phase_select(self) -> WattsonPhaseSelect | None:
        """Return the phase select entity, if registered."""
        from .select import WattsonPhaseSelect

        for entity in self._entities:
            if isinstance(entity, WattsonPhaseSelect):
                return entity
        return None

    def refresh_profile_selects(self) -> None:
        """Rebuild options on profile select entities after profile changes."""
        select = self.get_profile_select()
        if select is not None:
            select.refresh_options()
            select.async_write_ha_state()

    def refresh_phase_selects(self) -> None:
        """Rebuild options on phase select entities after phase changes."""
        select = self.get_phase_select()
        if select is not None:
            select.refresh_options()
            select.async_write_ha_state()

    async def async_rename_profile(self, profile_id: str, name: str) -> None:
        """Rename a profile and persist the change."""
        profile = self.store.get_profile(profile_id)
        if profile is None:
            return
        self.store.update_profile(replace(profile, name=name))
        await self.store.async_save()
        self.refresh_profile_selects()

    async def async_delete_profile(self, profile_id: str) -> None:
        """Delete a profile and persist the change."""
        self.store.delete_profile(profile_id)
        await self.store.async_save()
        self.refresh_profile_selects()

    async def async_rename_phase(
        self, profile_id: str, phase_index: int, name: str
    ) -> None:
        """Rename a phase within a profile and persist the change."""
        profile = self.store.get_profile(profile_id)
        if profile is None or profile.phases is None:
            return
        if phase_index < 0 or phase_index >= len(profile.phases):
            return
        phase = profile.phases[phase_index]
        profile.phases[phase_index] = replace(phase, name=name)
        self.store.update_profile(profile)
        await self.store.async_save()
        self.refresh_phase_selects()

    async def async_set_phase_done(
        self, profile_id: str, phase_index: int, *, done: bool
    ) -> None:
        """Set or clear the marks_cycle_done flag on a phase."""
        profile = self.store.get_profile(profile_id)
        if profile is None or profile.phases is None:
            return
        if phase_index < 0 or phase_index >= len(profile.phases):
            return
        phase = profile.phases[phase_index]
        profile.phases[phase_index] = replace(phase, marks_cycle_done=done)
        self.store.update_profile(profile)
        await self.store.async_save()
        self.refresh_phase_selects()

    async def async_setup(self) -> None:
        """Start listening for power updates."""
        await self.store.async_load()

        source_type = self.entry.data.get(CONF_SOURCE_TYPE)
        if source_type == SOURCE_MQTT:
            await self._setup_mqtt()
        else:
            self._setup_entity_listener()

    async def async_shutdown(self) -> None:
        """Stop listening and persist data."""
        if self._unsub_listener is not None:
            self._unsub_listener()
            self._unsub_listener = None
        if self._unsub_mqtt is not None:
            self._unsub_mqtt()
            self._unsub_mqtt = None
        self._stop_poll_timer()
        await self.store.async_save()

    def _setup_entity_listener(self) -> None:
        entity_id = self.entry.data.get(CONF_ENTITY_ID)
        if not entity_id:
            return

        self._unsub_listener = async_track_state_change_event(
            self.hass, [entity_id], self._handle_state_event
        )

        # Process current state if available.
        current_state = self.hass.states.get(entity_id)
        if current_state and current_state.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
            None,
        ):
            try:
                power = float(current_state.state)
                self._process_power(power)
            except (ValueError, TypeError):
                pass

    async def _setup_mqtt(self) -> None:
        topic = self.entry.data.get(CONF_MQTT_TOPIC)
        if not topic:
            return

        try:
            from homeassistant.components.mqtt import async_subscribe

            self._unsub_mqtt = await async_subscribe(
                self.hass, topic, self._handle_mqtt_message
            )
        except ImportError:
            _LOGGER.exception("MQTT integration not available")

    @callback
    def _handle_state_event(self, event: Event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        if new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            # Feed 0W so end_delay can fire instead of keeping the cycle
            # alive on stale power readings from the poll timer.
            if self.detector.state != CycleState.OFF:
                self._process_power(0.0)
            return

        try:
            power = float(new_state.state)
        except (ValueError, TypeError):
            return

        self._process_power(power)

    @callback
    def _handle_mqtt_message(self, msg: ReceiveMessage) -> None:
        try:
            power = float(msg.payload)
        except (ValueError, TypeError):
            return

        self._process_power(power)

    def _process_power(self, power_w: float) -> None:
        """Core processing: feed power to detector, recorder, matcher."""
        timestamp = time.time()
        self.current_power = power_w

        prev_state = self.detector.state
        new_state = self.detector.update(power_w, timestamp)

        # Handle state transitions.
        if prev_state != CycleState.RUNNING and new_state == CycleState.RUNNING:
            self._on_cycle_start(timestamp)
        elif prev_state == CycleState.RUNNING and new_state == CycleState.OFF:
            self._on_cycle_end()

        # Record during active cycle.
        if self._recording and new_state == CycleState.RUNNING:
            self.recorder.record(power_w, timestamp)
            self._update_time_estimate()
            self._update_phase(power_w, timestamp)

        # Manage the poll timer: active when detector is not OFF so that
        # end_delay can fire even if the monitored sensor value stays constant.
        if new_state != CycleState.OFF:
            self._start_poll_timer()
        else:
            self._stop_poll_timer()

        self._update_entities()

    @callback
    def _poll_tick(self, _now: object) -> None:
        """Re-process the last known power so the detector can evaluate timers."""
        self._process_power(self.current_power)

    def _start_poll_timer(self) -> None:
        if self._unsub_poll is not None:
            return
        self._unsub_poll = async_track_time_interval(
            self.hass,
            self._poll_tick,
            _POLL_INTERVAL,
        )

    def _stop_poll_timer(self) -> None:
        if self._unsub_poll is not None:
            self._unsub_poll()
            self._unsub_poll = None

    def _on_cycle_start(self, timestamp: float) -> None:
        self._recording = True
        self.match_result = None
        self.time_remaining = None
        self.recorder.start(timestamp)

        # Reset phase tracking.
        self.current_phase_index = 0
        self.current_phase_name = None
        self.cycle_done_by_phase = False
        self._rolling_powers.clear()
        self._phase_confirm_since = None
        self._phase_confirm_index = None

        _LOGGER.debug("Cycle started for %s", self.entry.title)

    def _on_cycle_end(self) -> None:
        if not self._recording:
            return

        self._recording = False
        cycle_data = self.recorder.finish(time.time())

        if (
            cycle_data.duration_s < MIN_CYCLE_DURATION_S
            or len(cycle_data.samples) < MIN_SAMPLES
        ):
            _LOGGER.debug("Cycle too short, discarding")
            self.match_result = None
            self.time_remaining = None
            return

        # Extract phases using params scaled to the cycle's actual duration.
        params = adaptive_phase_params(cycle_data.duration_s)
        new_phases = extract_phases(
            cycle_data.samples,
            smoothing_window_s=params["smoothing_window_s"],
            rolling_window_s=params["rolling_window_s"],
            min_duration_s=params["min_duration_s"],
        )

        profiles = self.store.profiles
        result = self.matcher.match(cycle_data, profiles)

        if result is not None:
            cycle_data.profile_id = result.profile_id
            existing = self.store.get_profile(result.profile_id)
            if existing is not None:
                updated = self.matcher.update_profile(
                    existing,
                    cycle_data,
                    new_phases=new_phases,
                )
                self.store.update_profile(updated)
            _LOGGER.debug(
                "Cycle matched profile '%s' (corr=%.3f)",
                result.profile_name,
                result.correlation,
            )
        else:
            new_profile = self.matcher.create_profile(
                cycle_data,
                name=None,
                phases=new_phases or None,
            )
            cycle_data.profile_id = new_profile.id
            self.store.add_profile(new_profile)
            _LOGGER.debug("New profile created from cycle")
            self._notify_new_profile(new_profile, cycle_data)
            result = MatchResult(
                profile_id=new_profile.id,
                profile_name=new_profile.name,
                correlation=1.0,
                dtw_distance=None,
            )

        self.store.add_cycle(cycle_data)
        self.match_result = result
        self.time_remaining = None

        # Adapt end_delay for the next cycle (unless user set it explicitly).
        if self._user_end_delay is None:
            profile = self.store.get_profile(result.profile_id)
            if profile is not None:
                p = adaptive_phase_params(profile.avg_duration_s)
                self.detector.update_end_delay(p["end_delay_s"])

        # Reset phase tracking.
        self.current_phase_index = None
        self.current_phase_name = None

        self.hass.async_create_task(self.store.async_save())
        self.refresh_profile_selects()
        self.refresh_phase_selects()
        _LOGGER.debug("Cycle ended for %s", self.entry.title)

    def _notify_new_profile(
        self,
        profile: Profile,
        cycle_data: CycleData,
    ) -> None:
        """Fire a persistent notification when a new unnamed profile is created."""
        duration_min = round(cycle_data.duration_s / 60, 1)
        energy = round(cycle_data.energy_wh, 2)
        self.hass.async_create_task(
            self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": f"Wattson — New program for {self.entry.title}",
                    "message": (
                        f"Learned a new program (~{duration_min} min, {energy} Wh).\n\n"
                        f"Select it in the **Profile** dropdown and type a name in **Profile Name** to rename it."
                    ),
                    "notification_id": f"wattson_new_profile_{profile.id}",
                },
            )
        )

    def _update_phase(self, power_w: float, timestamp: float) -> None:  # noqa: C901
        """Track the current phase within a matched profile."""
        if self.match_result is None:
            return

        profile = self.store.get_profile(self.match_result.profile_id)
        if profile is None or not profile.phases:
            return

        phases = profile.phases
        idx = self.current_phase_index
        if idx is None or idx >= len(phases):
            return

        params = adaptive_phase_params(profile.avg_duration_s)

        if self.current_phase_name is None:
            self._init_phase(idx, phases[idx], profile)

        self._rolling_powers.append((timestamp, power_w))
        cutoff = timestamp - params["rolling_window_s"]
        while self._rolling_powers and self._rolling_powers[0][0] < cutoff:
            self._rolling_powers.popleft()

        if len(self._rolling_powers) < MIN_SAMPLES:
            return

        rolling_avg = sum(p for _, p in self._rolling_powers) / len(
            self._rolling_powers
        )

        next_idx = idx + 1
        if next_idx >= len(phases):
            return

        dist_current = abs(rolling_avg - phases[idx].avg_power_w)
        dist_next = abs(rolling_avg - phases[next_idx].avg_power_w)

        if dist_next < dist_current:
            if self._phase_confirm_index != next_idx:
                self._phase_confirm_index = next_idx
                self._phase_confirm_since = timestamp
            elif (
                self._phase_confirm_since is not None
                and timestamp - self._phase_confirm_since >= params["phase_confirm_s"]
            ):
                self._transition_phase(next_idx, phases[next_idx], profile)
        else:
            self._phase_confirm_index = None
            self._phase_confirm_since = None

    def _init_phase(self, idx: int, phase: ProfilePhase, profile: Profile) -> None:
        """Set up the initial phase (index 0) at the start of a matched cycle."""
        self.current_phase_name = phase.name or f"Phase {idx + 1}"
        if phase.marks_cycle_done:
            self.cycle_done_by_phase = True
        self.hass.bus.async_fire(
            EVENT_PHASE_CHANGED,
            {
                "entry_id": self.entry.entry_id,
                "profile_name": profile.name,
                "phase_index": idx,
                "phase_name": self.current_phase_name,
                "previous_phase": None,
                "marks_cycle_done": phase.marks_cycle_done,
            },
        )
        _LOGGER.debug(
            "Phase initialized to '%s' (index %d) for %s",
            self.current_phase_name,
            idx,
            self.entry.title,
        )

    def _transition_phase(
        self, new_index: int, phase: ProfilePhase, profile: Profile
    ) -> None:
        """Handle a phase transition: update state, fire event."""
        prev_name = (
            self.current_phase_name or f"Phase {(self.current_phase_index or 0) + 1}"
        )
        self.current_phase_index = new_index
        self.current_phase_name = phase.name or f"Phase {new_index + 1}"
        self._phase_confirm_index = None
        self._phase_confirm_since = None

        if phase.marks_cycle_done:
            self.cycle_done_by_phase = True

        self.hass.bus.async_fire(
            EVENT_PHASE_CHANGED,
            {
                "entry_id": self.entry.entry_id,
                "profile_name": profile.name,
                "phase_index": new_index,
                "phase_name": self.current_phase_name,
                "previous_phase": prev_name,
                "marks_cycle_done": phase.marks_cycle_done,
            },
        )

        _LOGGER.debug(
            "Phase changed to '%s' (index %d) for %s",
            self.current_phase_name,
            new_index,
            self.entry.title,
        )

    def _update_time_estimate(self) -> None:  # noqa: C901, PLR0912
        if not self._recording:
            return

        profiles = self.store.profiles
        if not profiles:
            self.time_remaining = None
            return

        partial = self.recorder._samples  # noqa: SLF001
        if len(partial) < MIN_SAMPLES:
            return

        best_remaining: float | None = None
        best_score = -2.0
        best_profile: Profile | None = None

        for profile in profiles:
            remaining, score, _progress = self.matcher.estimate_remaining(
                partial,
                profile,
            )
            if remaining is not None and score > best_score:
                best_remaining = remaining
                best_score = score
                best_profile = profile

        # Smooth transitions: when the estimate jumps (e.g. profile switch),
        # blend toward the new value instead of snapping to it.
        if best_remaining is not None:
            if self.time_remaining is not None:
                diff = abs(best_remaining - self.time_remaining)
                if diff > ESTIMATE_SMOOTH_THRESHOLD_S:
                    self.time_remaining = (
                        1 - ESTIMATE_SMOOTH_ALPHA
                    ) * self.time_remaining + ESTIMATE_SMOOTH_ALPHA * best_remaining
                else:
                    self.time_remaining = best_remaining
            else:
                self.time_remaining = best_remaining
        else:
            self.time_remaining = None

        if best_profile is not None and best_remaining is not None:
            self.match_result = MatchResult(
                profile_id=best_profile.id,
                profile_name=best_profile.name,
                correlation=best_score,
                dtw_distance=None,
            )
            # Adapt end_delay to the matched profile (unless the user set it).
            if self._user_end_delay is None:
                params = adaptive_phase_params(best_profile.avg_duration_s)
                self.detector.update_end_delay(params["end_delay_s"])

    @callback
    def _update_entities(self) -> None:
        for entity in self._entities:
            entity.async_write_ha_state()
