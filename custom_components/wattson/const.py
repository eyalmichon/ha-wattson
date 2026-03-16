"""Constants for Wattson."""

from __future__ import annotations

from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "wattson"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.TEXT,
    Platform.BUTTON,
    Platform.SWITCH,
]

# --- Config keys ---

CONF_SOURCE_TYPE = "source_type"
CONF_ENTITY_ID = "entity_id"
CONF_MQTT_TOPIC = "mqtt_topic"
CONF_START_THRESHOLD = "start_threshold"
CONF_OFF_THRESHOLD = "off_threshold"

# --- Source types ---

SOURCE_ENTITY = "entity"
SOURCE_MQTT = "mqtt"

# --- Cycle detection defaults ---

DEFAULT_START_THRESHOLD_W: float = 5.0
DEFAULT_OFF_THRESHOLD_W: float = 1.0
DEFAULT_START_DURATION_S: float = 5.0
DEFAULT_START_ENERGY_WH: float = 0.2
DEFAULT_END_DELAY_S: float = 30.0

# --- Profile matching ---

MATCH_CORRELATION_THRESHOLD: float = 0.85
MATCH_CORRELATION_AMBIGUOUS: float = 0.70
MATCH_DTW_THRESHOLD: float = 50.0
MATCH_SCORE_THRESHOLD: float = 0.55
MATCH_SHAPE_WEIGHT: float = 0.4
MATCH_LEVEL_WEIGHT: float = 0.4
MATCH_DURATION_WEIGHT: float = 0.2
PROFILE_UPDATE_ALPHA: float = 0.3
ESTIMATE_MIN_CORRELATION: float = 0.5
ESTIMATE_MIN_PARTIAL_FRAC: float = 0.1
ESTIMATE_SMOOTH_THRESHOLD_S: float = 15.0
ESTIMATE_SMOOTH_ALPHA: float = 0.3
ESTIMATE_SHAPE_WEIGHT: float = 0.5
MAX_STORED_CYCLES: int = 50
RESAMPLE_POINTS: int = 100

# --- Downsampling ---

DOWNSAMPLE_POWER_DELTA: float = 1.0
DOWNSAMPLE_TIME_DELTA: float = 60.0

MIN_SAMPLES: int = 2
MIN_CYCLE_DURATION_S: float = 1.0
STD_EPSILON: float = 1e-9

# --- Phase extraction (static defaults, used as fallbacks) ---

PHASE_MIN_DURATION_S: float = 30.0
PHASE_PENALTY_FACTOR: float = 0.3
PHASE_INTERMITTENT_COV: float = 0.5
PHASE_PRE_SMOOTH_WINDOW_S: int = 15
PHASE_MIN_SMOOTH_WIN: int = 2
PHASE_FLAT_TOLERANCE: float = 1e-6

# --- Phase tracking (static defaults, used as fallbacks) ---

PHASE_CONFIRM_S: float = 15.0

EVENT_PHASE_CHANGED = "wattson_phase_changed"

# --- Entity limits ---

MAX_NAME_LENGTH: int = 64

# --- Config keys for options flow ---

CONF_END_DELAY = "end_delay"

# --- Adaptive parameter scaling ---
# Each parameter is derived as a fraction of cycle duration, with a floor
# to prevent degenerate values on very short cycles.

ADAPTIVE_CONFIRM_FRAC: float = 0.05
ADAPTIVE_CONFIRM_FLOOR_S: float = 5.0
ADAPTIVE_MIN_DURATION_FRAC: float = 0.03
ADAPTIVE_MIN_DURATION_FLOOR_S: float = 3.0
ADAPTIVE_END_DELAY_FRAC: float = 0.10
ADAPTIVE_END_DELAY_FLOOR_S: float = 15.0


def adaptive_phase_params(duration_s: float) -> dict[str, float]:
    """Compute phase-detection parameters scaled to the cycle duration.

    Returns sensible values for any appliance -- from a 2-minute coffee
    machine to a 4-hour 3D printer -- by deriving each parameter as a
    fraction of the total cycle length, with a floor to prevent
    degenerate values on very short cycles.
    """
    return {
        "phase_confirm_s": max(
            ADAPTIVE_CONFIRM_FLOOR_S, ADAPTIVE_CONFIRM_FRAC * duration_s
        ),
        "min_duration_s": max(
            ADAPTIVE_MIN_DURATION_FLOOR_S, ADAPTIVE_MIN_DURATION_FRAC * duration_s
        ),
        "end_delay_s": max(
            ADAPTIVE_END_DELAY_FLOOR_S, ADAPTIVE_END_DELAY_FRAC * duration_s
        ),
    }


class CycleState(StrEnum):
    """Appliance cycle states."""

    OFF = "off"
    STARTING = "starting"
    RUNNING = "running"
