"""
The risk-scoring algorithm, as pure functions — no DB, no Django. Given an
employee's event history it returns a score and the metrics that produced it,
so the same history can be re-scored under any algorithm version.

v1 model (behavior only — see CLAUDE.md invariants #5/#6):
  * Per campaign, only the WORST failure counts: submitting data (50) beats
    clicking (25). Clicking twice on one campaign isn't two failures.
  * Reporting the phish is a credit (-15 per campaign).
  * Every additional failed campaign after the first adds a repeat-failure
    penalty (+10) — a pattern is riskier than a one-off.
  * Everything decays with a 180-day half-life, so old mistakes fade.
  * `email_opened` is ignored (tracking-pixel prefetch makes it unreliable),
    and training/quiz completion is NOT folded in — that's a separate
    compliance metric (see analytics.py), a process fact, not behavior.
"""

from dataclasses import dataclass
from datetime import datetime

from apps.events.models import Event

ALGORITHM_VERSION = "v1"

FAILURE_WEIGHTS = {
    Event.EventType.CREDENTIAL_ATTEMPT: 50.0,
    Event.EventType.LINK_CLICKED: 25.0,
}
REPORT_CREDIT = 15.0
REPEAT_FAILURE_PENALTY = 10.0
HALF_LIFE_DAYS = 180.0
# Event types that never influence the score. training_*/quiz_* are compliance metrics.
IGNORED_EVENT_TYPES = (
    Event.EventType.EMAIL_OPENED,
    Event.EventType.TRAINING_ASSIGNED,
    Event.EventType.TRAINING_STARTED,
    Event.EventType.TRAINING_COMPLETED,
    Event.EventType.QUIZ_COMPLETED,
)
# Event types whose arrival should trigger a recompute (used by signals.py).
SCORING_EVENT_TYPES = frozenset(FAILURE_WEIGHTS) | {Event.EventType.PHISHING_REPORTED}


@dataclass(frozen=True)
class ScoringEvent:
    event_type: str
    campaign_id: int
    occurred_at: datetime


@dataclass(frozen=True)
class ScoreResult:
    score: float
    metrics: dict


def _decay(occurred_at: datetime, now: datetime) -> float:
    age_days = max((now - occurred_at).total_seconds(), 0.0) / 86400
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def compute_risk_score(events, now: datetime) -> ScoreResult:
    worst_failure: dict[int, tuple[float, datetime]] = {}  # campaign -> (weight, when)
    reported: dict[int, datetime] = {}  # campaign -> when

    for event in events:
        weight = FAILURE_WEIGHTS.get(event.event_type)
        if weight is not None:
            current = worst_failure.get(event.campaign_id)
            if current is None or weight > current[0]:
                worst_failure[event.campaign_id] = (weight, event.occurred_at)
        elif event.event_type == Event.EventType.PHISHING_REPORTED:
            earlier = reported.get(event.campaign_id)
            if earlier is None or event.occurred_at > earlier:
                reported[event.campaign_id] = event.occurred_at

    failures = sorted(worst_failure.values(), key=lambda failure: failure[1])
    failure_points = sum(weight * _decay(when, now) for weight, when in failures)
    repeat_points = sum(REPEAT_FAILURE_PENALTY * _decay(when, now) for _, when in failures[1:])
    report_points = sum(REPORT_CREDIT * _decay(when, now) for when in reported.values())

    raw = failure_points + repeat_points - report_points
    score = round(min(max(raw, 0.0), 100.0), 2)

    metrics = {
        "campaigns_failed": len(failures),
        "credential_attempt_campaigns": sum(1 for weight, _ in failures if weight == FAILURE_WEIGHTS[Event.EventType.CREDENTIAL_ATTEMPT]),
        "click_only_campaigns": sum(1 for weight, _ in failures if weight == FAILURE_WEIGHTS[Event.EventType.LINK_CLICKED]),
        "campaigns_reported": len(reported),
        "repeat_failures": max(len(failures) - 1, 0),
        "failure_points": round(failure_points, 2),
        "repeat_points": round(repeat_points, 2),
        "report_credit": round(report_points, 2),
        "raw_score": round(raw, 2),
        "ignored_event_types": sorted(str(t) for t in IGNORED_EVENT_TYPES),
    }
    return ScoreResult(score=score, metrics=metrics)
