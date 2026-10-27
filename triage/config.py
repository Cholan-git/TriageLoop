"""Operational configuration for a simulation run.

Each run starts with a copy of DEFAULTS recorded in its ``run_started`` event;
later changes are recorded as ``settings_changed`` events, so replaying a run
always uses the configuration that was in force at the time.

Clinical thresholds below are the ones the original TriageLoop dashboard used
(see docs/DASHBOARD.md). They are simulation settings, not clinical guidance.
"""
from __future__ import annotations

from copy import deepcopy

RULESET_VERSION = "tl-rules-2.0"

RESOURCES = ("CT", "BEDS", "LAB")
RESOURCE_LABELS = {"CT": "CT", "BEDS": "Beds", "LAB": "Lab"}

DEFAULTS: dict = {
    # Units of each resource that can be in use at once.
    "capacity": {"CT": 1, "BEDS": 12, "LAB": 3},
    # Minutes one patient occupies one unit (simulation model).
    "duration": {"CT": 30, "BEDS": 120, "LAB": 20},
    # Reassessment target in minutes by ESI level (original dashboard values).
    "reassess_target": {"1": 0, "2": 30, "3": 60, "4": 120, "5": 240},
    "news2_rise": 2,          # rise above baseline that raises a NEWS2-rise flag
    "news2_urgent_rise": 5,   # a rise flag becomes urgent at this NEWS2 or above
    "news2_urgent": 7,        # NEWS2 at or above this is urgent on its own
    "vitals_stale_min": 60,   # data-quality notice when the latest vitals are older
    "scenario": "Normal",     # synthetic arrival profile: Normal | Surge
    "vitals_drift": True,     # Advance adds seeded, synthetic vitals changes
}

SCENARIOS = ("Normal", "Surge")

# Validation bounds for editable settings: key -> (min, max)
INT_BOUNDS = {
    "capacity": (0, 50),
    "duration": (1, 600),
    "reassess_target": (0, 600),
    "news2_rise": (1, 20),
    "news2_urgent_rise": (1, 20),
    "news2_urgent": (1, 20),
    "vitals_stale_min": (5, 1440),
}


def default_config() -> dict:
    return deepcopy(DEFAULTS)


def validate_changes(current: dict, changes: dict) -> dict:
    """Return a cleaned copy of ``changes`` or raise ValueError with a message."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("No settings supplied")
    clean: dict = {}
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise ValueError(f"Unknown setting: {key}")
        if key == "scenario":
            if value not in SCENARIOS:
                raise ValueError(f"scenario must be one of {', '.join(SCENARIOS)}")
            clean[key] = value
            continue
        if key == "vitals_drift":
            if not isinstance(value, bool):
                raise ValueError("vitals_drift must be true or false")
            clean[key] = value
            continue
        lo, hi = INT_BOUNDS[key]
        if isinstance(DEFAULTS[key], dict):
            if not isinstance(value, dict) or not value:
                raise ValueError(f"{key} must be an object")
            sub = {}
            for k, v in value.items():
                k = str(k)
                if k not in DEFAULTS[key]:
                    raise ValueError(f"Unknown {key} entry: {k}")
                sub[k] = _int(f"{key}.{k}", v, lo, hi)
            clean[key] = sub
        else:
            clean[key] = _int(key, value, lo, hi)
    merged = merge(current, clean)
    if merged["news2_urgent_rise"] > merged["news2_urgent"]:
        raise ValueError("news2_urgent_rise cannot exceed news2_urgent")
    return clean


def merge(current: dict, changes: dict) -> dict:
    out = deepcopy(current)
    for key, value in changes.items():
        if isinstance(value, dict):
            out[key].update(value)
        else:
            out[key] = value
    return out


def _int(name, value, lo, hi) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a whole number")
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number") from None
    if isinstance(value, float) and value != n:
        raise ValueError(f"{name} must be a whole number")
    if not lo <= n <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return n
