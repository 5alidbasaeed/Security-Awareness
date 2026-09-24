from datetime import timedelta

import pytest
from django.utils import timezone

from apps.events.models import Event
from apps.risk_scoring.scoring import ScoringEvent, compute_risk_score

NOW = timezone.now()
E = Event.EventType


def _event(event_type, campaign_id=1, days_ago=0):
    return ScoringEvent(event_type=event_type, campaign_id=campaign_id, occurred_at=NOW - timedelta(days=days_ago))


def _score(*events):
    return compute_risk_score(list(events), NOW).score


def test_no_events_scores_zero():
    assert _score() == 0


def test_a_click_scores_25_and_data_submission_scores_50():
    assert _score(_event(E.LINK_CLICKED)) == 25
    assert _score(_event(E.CREDENTIAL_ATTEMPT)) == 50


def test_click_plus_submission_on_one_campaign_counts_once_at_the_worst_outcome():
    assert _score(_event(E.LINK_CLICKED), _event(E.CREDENTIAL_ATTEMPT)) == 50


def test_clicking_repeatedly_on_one_campaign_is_one_failure():
    assert _score(_event(E.LINK_CLICKED), _event(E.LINK_CLICKED), _event(E.LINK_CLICKED)) == 25


def test_repeat_failures_across_campaigns_add_a_penalty():
    # 25 + 25 + 10 repeat penalty
    assert _score(_event(E.LINK_CLICKED, 1), _event(E.LINK_CLICKED, 2)) == 60


def test_reporting_reduces_the_score_and_never_below_zero():
    assert _score(_event(E.LINK_CLICKED, 1), _event(E.PHISHING_REPORTED, 1)) == 10
    assert _score(_event(E.PHISHING_REPORTED, 1), _event(E.PHISHING_REPORTED, 2)) == 0


def test_score_is_capped_at_100():
    events = [_event(E.CREDENTIAL_ATTEMPT, campaign) for campaign in range(1, 6)]
    assert _score(*events) == 100


def test_old_failures_decay_with_a_180_day_half_life():
    assert _score(_event(E.CREDENTIAL_ATTEMPT, days_ago=180)) == pytest.approx(25, abs=0.01)


def test_email_opened_and_training_events_never_affect_the_score():
    ignored = [E.EMAIL_OPENED, E.EMAIL_SENT, E.EMAIL_DELIVERED, E.TRAINING_ASSIGNED, E.TRAINING_COMPLETED, E.QUIZ_COMPLETED]
    assert _score(*[_event(t) for t in ignored]) == 0


def test_contributing_metrics_explain_the_score():
    result = compute_risk_score([_event(E.CREDENTIAL_ATTEMPT, 1), _event(E.LINK_CLICKED, 2)], NOW)

    assert result.metrics["campaigns_failed"] == 2
    assert result.metrics["credential_attempt_campaigns"] == 1
    assert result.metrics["click_only_campaigns"] == 1
    assert result.metrics["repeat_failures"] == 1
    assert "email_opened" in result.metrics["ignored_event_types"]
