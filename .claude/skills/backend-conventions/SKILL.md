---
name: backend-conventions
description: Django app structure, the PhishingEngineClient adapter interface, Celery task and webhook-view conventions for the phishing-simulation platform. Load before backend implementation work in this project.
---

# Backend conventions — phishing-simulation platform

## App layout

One Django app per bounded concept, not one giant app:

```
apps/
├── employees/      # Employee, Department models
├── campaigns/      # Campaign metadata, gophish_campaign_id reference field
├── events/         # append-only Event model, webhook ingestion view, reconciliation task
├── training/        # TrainingModule, Quiz, Assignment, completion tracking
├── risk_scoring/    # RiskScoreSnapshot model + scoring algorithm(s), versioned
├── engine/          # PhishingEngineClient adapter + Gophish-specific implementation
└── core/            # shared utilities, RBAC/permissions helpers, audit log
```

Project-level infra that isn't a domain concern (the `/healthz` endpoint checking DB/Redis connectivity) lives in `config/` alongside settings/urls/wsgi, not inside an app — `config/health.py` is the existing example. Don't move it into `apps/core` later just for tidiness; it's deliberately outside the app layer because it checks infrastructure, not business logic.

`engine/` is the only app allowed to import a Gophish HTTP client or know Gophish's API shape. Every other app depends on `engine.PhishingEngineClient`'s interface, never on Gophish specifics.

## `PhishingEngineClient` adapter

Define it as an abstract interface (ABC or Protocol) in `engine/base.py`, with a concrete `GophishClient` implementation in `engine/gophish.py`. Shape (adjust as real needs emerge, keep it minimal):

```python
class PhishingEngineClient(ABC):
    def create_campaign(self, name, template_id, target_group_id, send_profile_id, url) -> ExternalCampaignRef: ...
    def get_campaign_results(self, external_campaign_id) -> list[EngineEvent]: ...
    def launch_campaign(self, external_campaign_id) -> None: ...
```

Views, models, and Celery tasks depend on this interface via dependency injection or a settings-configured factory — never `import gophish_sdk` outside `engine/`.

## Webhook ingestion

- One view per webhook endpoint, in `events/views.py`. Verify `X-Gophish-Signature` (HMAC-SHA256 against the shared secret from the secret store) **before** touching the payload — reject with 401 on mismatch, don't process-then-check.
- Dedupe on `external_id` at the DB level (unique constraint scoped to `source`), not just an application-level check — a race between two webhook deliveries should fail at the DB, not silently double-insert.
- **Wrap the dedup-relying insert in its own `transaction.atomic()` block**, not a bare `try/except IntegrityError` around `Event.objects.create(...)`. Without the savepoint, the constraint violation poisons any enclosing transaction (pytest-django's per-test wrapping, a future `ATOMIC_REQUESTS=True`, or any caller already inside `atomic()`) and the same-request `except` won't actually recover — every query after it raises `TransactionManagementError` instead. Found by running the real test suite against Postgres, not by inspection.
- Strip any `password`-resembling key from the payload before it touches `metadata` — do this in a small shared function (`events/sanitize.py`), not inline in the view, so it's applied consistently everywhere a payload is stored.
- Webhook views return fast (enqueue a Celery task for anything beyond validate+store) — don't do risk-score recomputation synchronously inside the webhook request.

## Celery tasks

- `events.tasks.reconcile_campaign(campaign_id)` — Celery Beat scheduled, polls `PhishingEngineClient.get_campaign_results` and inserts any event missing by `external_id`. This is a fallback, not the primary path — don't build features that assume it runs frequently.
- `risk_scoring.tasks.recompute_score(employee_id, algorithm_version=None)` — inserts a new `RiskScoreSnapshot` row, never updates an existing one. Triggered on new relevant events (credential_attempt, link_clicked, phishing_reported, training_completed), not on a blanket schedule for every employee.
- Name tasks by `<app>.tasks.<verb>_<noun>` consistently; keep task bodies thin — real logic lives in a plain function/service the task calls, so it's unit-testable without Celery's test harness.

## Settings / secrets

- Gophish API key, webhook HMAC secret, SMTP credentials: read from environment variables via `django-environ` or equivalent, never hardcoded, never committed (`.env` stays in `.gitignore`).
- One settings module per environment (`settings/base.py`, `settings/dev.py`, `settings/prod.py`) rather than `if DEBUG:` branching scattered through a single file.

## Errors & logging

- Webhook signature failures, adapter call failures, and reconciliation mismatches log at `WARNING`+ with enough context (campaign id, external_id) to debug without needing to reproduce — these are the failure modes the `debugger` project skill/agent will look for first.
- Don't swallow exceptions from `PhishingEngineClient` calls silently — either handle a specific, expected failure mode or let it propagate/alert.
