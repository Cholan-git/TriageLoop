"""Explainable advisory flags. The system suggests; the clinician decides.

Every reason carries the rule id, the ruleset version and the recorded facts
that triggered it. Rules never change a patient's ESI level or status.

Levels:
  urgent   - R-NEWS2-HIGH fired, or R-NEWS2-RISE fired with NEWS2 >= news2_urgent_rise
  reassess - any other warning or danger reason
  stable   - no reasons (informational notes do not raise the level)
"""
from __future__ import annotations

from .config import RESOURCE_LABELS, RULESET_VERSION
from .projection import Patient, State
from .timefmt import sim_clock

LEVEL_RANK = {"urgent": 0, "reassess": 1, "stable": 2, "closed": 3}

PARAM_LABELS = {"rr": "RR", "spo2": "SpO₂", "on_oxygen": "O₂", "sbp": "SBP",
                "hr": "HR", "avpu": "AVPU", "temp": "Temp"}


def _reason(rule, kind, text, why, **facts):
    return {"rule": rule, "kind": kind, "text": text, "why": why, "facts": facts}


def drivers(p: Patient) -> str:
    obs = p.latest
    if not obs:
        return ""
    parts = [f"{PARAM_LABELS[k]} {_fmt(obs.vitals[k])} (+{s})"
             for k, s in obs.score.parts.items() if s > 0]
    return ", ".join(parts)


def _fmt(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    return v


def evaluate(st: State, p: Patient, estimates: dict) -> dict:
    c = st.config
    wait = st.now - p.arrival
    since_review = st.now - (p.last_review if p.last_review is not None else p.arrival)
    base = {"wait": wait, "since_review": since_review, "news2": p.news2,
            "baseline": p.baseline_news2, "ruleset": RULESET_VERSION}
    if not p.active:
        return {**base, "level": "closed", "reasons": [], "urgent_rules": []}

    reasons, urgent_rules = [], []
    news2, baseline = p.news2, p.baseline_news2

    if news2 is None:
        reasons.append(_reason(
            "R-VITALS-MISSING", "warn", "No vitals recorded",
            "NEWS2 cannot be calculated because no observations have been recorded."))
    else:
        drv = drivers(p)
        drv_txt = f" Drivers: {drv}." if drv else ""
        if news2 >= c["news2_urgent"]:
            urgent_rules.append("R-NEWS2-HIGH")
            reasons.append(_reason(
                "R-NEWS2-HIGH", "danger", f"NEWS2 {news2} (≥ {c['news2_urgent']})",
                f"Latest NEWS2 is {news2}, at or above the urgent threshold of "
                f"{c['news2_urgent']}.{drv_txt}",
                news2=news2, threshold=c["news2_urgent"], at=p.latest.minute))
        if baseline is not None and news2 - baseline >= c["news2_rise"]:
            ref = "last clinician review" if p.last_review is not None else "arrival"
            urgent = news2 >= c["news2_urgent_rise"]
            if urgent:
                urgent_rules.append("R-NEWS2-RISE")
            reasons.append(_reason(
                "R-NEWS2-RISE", "danger", f"NEWS2 rose {baseline} → {news2}",
                f"NEWS2 rose by {news2 - baseline} since {ref} (rule: rise ≥ {c['news2_rise']}"
                + (f"; urgent because NEWS2 ≥ {c['news2_urgent_rise']}" if urgent else "")
                + f").{drv_txt}",
                baseline=baseline, news2=news2, rise=news2 - baseline,
                rise_threshold=c["news2_rise"], urgent=urgent))
        if p.latest.score.missing:
            missing = ", ".join(PARAM_LABELS[m] for m in p.latest.score.missing)
            reasons.append(_reason(
                "R-NEWS2-PARTIAL", "info", f"Partial NEWS2 (missing {missing})",
                f"NEWS2 was calculated without {missing}; the true score may be higher.",
                missing=p.latest.score.missing))
        age = st.now - p.latest.minute
        if age > c["vitals_stale_min"]:
            reasons.append(_reason(
                "R-VITALS-STALE", "warn", f"Vitals {age} min old",
                f"The latest observations were recorded {age} min ago "
                f"(data-quality limit {c['vitals_stale_min']} min).",
                age=age, limit=c["vitals_stale_min"]))

    for j in sorted(st.patient_jobs(p.pid), key=lambda j: j.resource):
        if j.state != "requested":
            continue
        label = RESOURCE_LABELS[j.resource]
        start = estimates.get(j.jid)
        o = st.outages.get(j.resource)
        offline = o is not None and o.end > st.now
        if start is None:
            reasons.append(_reason(
                "R-RESOURCE-DELAY", "warn", f"{label} unavailable (no capacity)",
                f"{label} capacity is set to 0, so this request cannot be scheduled.",
                resource=j.resource))
            continue
        delay = start - st.now
        if delay <= 0:
            continue
        word = "unavailable" if offline else "delayed"
        cause = (f"{label} is offline until {_clock(st, o.end)}" if offline
                 else f"all {label} units are in use or queued ahead")
        reasons.append(_reason(
            "R-RESOURCE-DELAY", "warn", f"{label} {word} ~{delay} min",
            f"Estimated {delay} min until {label} is available: {cause}.",
            resource=j.resource, estimate=delay, offline=offline))

    target = c["reassess_target"][str(p.esi)]
    if since_review > target:
        ref = "last clinician review" if p.last_review is not None else "arrival"
        reasons.append(_reason(
            "R-REASSESS-OVERDUE", "warn", f"Reassessment overdue ({since_review - target} min)",
            f"ESI {p.esi} target is reassessment every {target} min; "
            f"{since_review} min since {ref}.",
            target=target, elapsed=since_review))

    if urgent_rules:
        level = "urgent"
    elif any(r["kind"] in ("danger", "warn") for r in reasons):
        level = "reassess"
    else:
        level = "stable"
    order = {"danger": 0, "warn": 1, "info": 2}
    reasons.sort(key=lambda r: order[r["kind"]])
    return {**base, "level": level, "reasons": reasons, "urgent_rules": urgent_rules}


def _clock(st: State, minute: int) -> str:
    return sim_clock(st, minute)["time"]


def ranked(st: State, evals: dict) -> list:
    pats = list(st.patients.values())
    pats.sort(key=lambda p: (LEVEL_RANK[evals[p.pid]["level"]], p.esi,
                             -(p.news2 or 0), -evals[p.pid]["wait"], p.pid))
    return pats
