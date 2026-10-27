
from triage.scoring import score_param, score_vitals, NEWS2Result


def test_normal_vitals_have_zero_scores():
    assert score_param("rr", 16) == 0
    assert score_param("spo2", 98) == 0
    assert score_param("on_oxygen", False) == 0
    assert score_param("sbp", 120) == 0
    assert score_param("hr", 75) == 0
    assert score_param("avpu", "A") == 0
    assert score_param("temp", 37.0) == 0


def test_high_risk_values_receive_three_points():
    assert score_param("rr", 8) == 3
    assert score_param("spo2", 91) == 3
    assert score_param("on_oxygen", True) == 2
    assert score_param("sbp", 90) == 3
    assert score_param("hr", 40) == 3
    assert score_param("avpu", "U") == 3
    assert score_param("temp", 35.0) == 3


def test_score_vitals_tracks_missing_values():
    result = score_vitals({
        "rr": 16,
        "spo2": 98,
    })

    assert isinstance(result, NEWS2Result)
    assert result.total == 0
    assert result.parts == {"rr": 0, "spo2": 0}
    assert "hr" in result.missing
    assert "sbp" in result.missing
    assert "temp" in result.missing


def test_score_vitals_calculates_total_and_band():
    result = score_vitals({
        "rr": 8,
        "spo2": 98,
        "on_oxygen": False,
        "sbp": 120,
        "hr": 75,
        "avpu": "A",
        "temp": 37.0,
    })

    assert result.total == 3
    assert result.any_three is True
    assert result.band == "low-medium"
    assert result.missing == []


# ---- ported from tests/test_engine.py (A3: scoring) ----
import pytest  # noqa: E402

NORMAL = dict(rr=16, spo2=97, on_oxygen=False, sbp=120, hr=70, avpu="A", temp=37.0)
BAD = dict(rr=30, spo2=88, on_oxygen=True, sbp=85, hr=140, avpu="V", temp=34.5)


def test_known_vitals_scores():
    assert score_vitals(NORMAL).total == 0 and score_vitals(NORMAL).band == "low"
    assert score_vitals(BAD).total == 20 and score_vitals(BAD).band == "high"
    mid = dict(rr=22, spo2=95, on_oxygen=False, sbp=105, hr=115, avpu="A", temp=38.5)
    assert score_vitals(mid).total == 7
    single3 = {**NORMAL, "rr": 8}
    assert score_vitals(single3).total == 3 and score_vitals(single3).band == "low-medium"


@pytest.mark.parametrize("name,value,expected", [
    ("rr", 8, 3), ("rr", 9, 1), ("rr", 11, 1), ("rr", 12, 0), ("rr", 20, 0),
    ("rr", 21, 2), ("rr", 24, 2), ("rr", 25, 3),
    ("spo2", 91, 3), ("spo2", 92, 2), ("spo2", 93, 2), ("spo2", 94, 1), ("spo2", 95, 1), ("spo2", 96, 0),
    ("sbp", 90, 3), ("sbp", 91, 2), ("sbp", 100, 2), ("sbp", 101, 1), ("sbp", 110, 1),
    ("sbp", 111, 0), ("sbp", 219, 0), ("sbp", 220, 3),
    ("hr", 40, 3), ("hr", 41, 1), ("hr", 50, 1), ("hr", 51, 0), ("hr", 90, 0), ("hr", 91, 1),
    ("hr", 110, 1), ("hr", 111, 2), ("hr", 130, 2), ("hr", 131, 3),
    ("temp", 35.0, 3), ("temp", 35.1, 1), ("temp", 36.0, 1), ("temp", 36.1, 0),
    ("temp", 38.0, 0), ("temp", 38.1, 1), ("temp", 39.0, 1), ("temp", 39.1, 2),
    ("avpu", "A", 0), ("avpu", "C", 3), ("avpu", "U", 3),
    ("on_oxygen", True, 2), ("on_oxygen", False, 0),
])
def test_news2_boundaries(name, value, expected):
    assert score_param(name, value) == expected
