"""NEWS2-inspired score (RCP 2017 chart, SpO2 scale 1). Not for clinical use.

Vitals dict keys: rr, spo2, on_oxygen, sbp, hr, avpu, temp
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _rr(v):   return 3 if v <= 8 else 1 if v <= 11 else 0 if v <= 20 else 2 if v <= 24 else 3
def _spo2(v): return 3 if v <= 91 else 2 if v <= 93 else 1 if v <= 95 else 0
def _o2(v):   return 2 if v else 0
def _sbp(v):  return 3 if v <= 90 else 2 if v <= 100 else 1 if v <= 110 else 0 if v <= 219 else 3
def _hr(v):   return 3 if v <= 40 else 1 if v <= 50 else 0 if v <= 90 else 1 if v <= 110 else 2 if v <= 130 else 3
def _temp(v): return 3 if v <= 35.0 else 1 if v <= 36.0 else 0 if v <= 38.0 else 1 if v <= 39.0 else 2
def _avpu(v): return 0 if str(v).strip().upper() in ("A", "ALERT") else 3   # C, V, P, U -> 3


SCORERS = {
    "rr": _rr, "spo2": _spo2, "on_oxygen": _o2, "sbp": _sbp,
    "hr": _hr, "avpu": _avpu, "temp": _temp,
}


def score_param(name: str, value) -> int:
    return SCORERS[name](value)


@dataclass
class NEWS2Result:
    total: int
    parts: dict[str, int] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def any_three(self) -> bool:
        return any(s == 3 for s in self.parts.values())

    @property
    def band(self) -> str:
        if self.total >= 7:
            return "high"
        if self.total >= 5:
            return "medium"
        if self.any_three:
            return "low-medium"
        return "low"


def score_vitals(vitals: dict) -> NEWS2Result:
    """Score each present parameter; absent ones are listed in `missing`."""
    parts, missing = {}, []
    for name, fn in SCORERS.items():
        if vitals.get(name) is None:
            missing.append(name)
        else:
            parts[name] = fn(vitals[name])
    return NEWS2Result(total=sum(parts.values()), parts=parts, missing=missing)