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
├── training/        # TrainingModule, Quiz, TrainingAssignment, completion tracking (Phase 2)
├── risk_scoring/    # RiskScoreSnapshot model + scoring algorithm(s), versioned (Phase 4, not built yet)
├── engine/          # PhishingEngineClient adapter + Gophish-specific implementation
├── dashboard/       # read-only staff dashboard (templates + HTMX + Chart.js), Phase 4.1
└── core/            # shared utilities, RBAC/permissions helpers, audit log
```

**Cross-app model FKs are Django's lazy string form** (`ForeignKey("employees.Employee", ...)`), never a direct class import (`from apps.employees.models import Employee` then `ForeignKey(Employee, ...)`). The direct-import style was Phase 1's original pattern and it would have created a real circular import once Phase 2 needed `Campaign → TrainingModule` and `TrainingAssignment → Employee`/`→ Event` at the same time (campaigns → training → events → campaigns). String references sidestep this entirely — apply it to every cross-app FK, not just the ones that happen to cycle today.

Project-level infra that isn't a domain concern (the `/healthz` endpoint checking DB/Redis connectivity) lives in `config/` alongside settings/urls/wsgi, not inside an app — `config/health.py` is the existing example. Don't move it into `apps/core` later just for tidiness; it's deliberately outside the app layer because it checks infrastructure, not business logic.

`engine/` is the only app allowed to import a Gophish HTTP client or know Gophish's API shape. Every other app depends on `engine.PhishingEngineClient`'s interface, never on Gophish specifics.

## `PhishingEngineClient` adapter

Define it as an abstract interface (ABC or Protocol) in `engine/base.py`, with a concrete `GophishClient` implementation in `engine/gophish.py`. Shape (adjust as real needs emerge, keep it minimal):

```python
class PhishingEngineClient(ABC):
    def create_campaign(self, name, template_id, target_group_id, send_profile_id, page_id, url) -> ExternalCampaignRef: ...
    def get_campaign_results(self, external_campaign_id) -> list[EngineEvent]: ...
    def launch_campaign(self, external_campaign_id) -> None: ...
```

`page_id` (the Gophish landing-page *name*) and `url` (the redirect URL) are separate parameters — don't collapse them or reuse `target_group_id` for the page. That was a real bug found in Phase 1's first cut: it silently sent the department name as the landing-page name.

Views, models, and Celery tasks depend on this interface via dependency injection or a settings-configured factory — never `import gophish_sdk` outside `engine/`.

## Webhook ingestion

- One view per webhook endpoint, in `events/views.py`. Verify `X-Gophish-Signature` (HMAC-SHA256 against the shared secret from the secret store) **before** touching the payload — reject with 401 on mismatch, don't process-then-check.
- Dedupe on `external_id` at the DB level (unique constraint scoped to `source`), not just an application-level check — a race between two webhook deliveries should fail at the DB, not silently double-insert.
- **Wrap the dedup-relying insert in its own `transaction.atomic()` block**, not a bare `try/except IntegrityError` around `Event.objects.create(...)`. Without the savepoint, the constraint violation poisons any enclosing transaction (pytest-django's per-test wrapping, a future `ATOMIC_REQUESTS=True`, or any caller already inside `atomic()`) and the same-request `except` won't actually recover — every query after it raises `TransactionManagementError` instead. Found by running the real test suite against Postgres, not by inspection.
- Strip any `password`-resembling key from the payload before it touches `metadata` — do this in a small shared function (`events/sanitize.py`), not inline in the view, so it's applied consistently everywhere a payload is stored.
- Webhook views return fast (enqueue a Celery task for anything beyond validate+store) — don't do risk-score recomputation synchronously inside the webhook request.
- The atomic-insert-plus-dedupe pattern above lives in one place, `events/services.py::record_event()` — both the webhook view and the reconciliation task call it rather than each keeping their own copy of the transaction/IntegrityError logic. If you're about to write `transaction.atomic()` around an `Event.objects.create(...)` anywhere else, you're probably duplicating this — call `record_event()` instead.

## Celery tasks

- `events.tasks.reconcile_campaign(campaign_id)` — polls `PhishingEngineClient.get_campaign_results` and inserts any event missing by `external_id`. This is a fallback, not the primary path — don't build features that assume it runs frequently. **Adding a task isn't enough — it must actually appear in `CELERY_BEAT_SCHEDULE`** (see `config/settings/base.py`); a task that exists but isn't scheduled is silent dead code, which is exactly what happened in Phase 1's first cut. `events.tasks.reconcile_all_active_campaigns` is the scheduled entry point that fans out to `reconcile_campaign` per launched campaign.
- `risk_scoring.tasks.recompute_score(employee_id, algorithm_version=None)` — inserts a new `RiskScoreSnapshot` row, never updates an existing one. Triggered on new relevant events (credential_attempt, link_clicked, phishing_reported, training_completed), not on a blanket schedule for every employee. Not built yet (Phase 4).
- `training.tasks.send_training_reminders` — hourly per `CELERY_BEAT_SCHEDULE`; the task itself enforces a per-assignment 24h reminder cooldown (`TrainingAssignment.last_reminded_at`), so the schedule interval and the cooldown are two independent knobs — don't assume tightening one changes the other.
- Name tasks by `<app>.tasks.<verb>_<noun>` consistently; keep task bodies thin — real logic lives in a plain function/service the task calls, so it's unit-testable without Celery's test harness.

## Auto-assignment via signals

`apps/training/signals.py` connects a `post_save` receiver on `events.Event` (wired in `apps/training/apps.py::ready()`) that creates a `TrainingAssignment` when a qualifying event (`link_clicked`, `credential_attempt`) fires on a campaign with a `training_module` set. Dedupes against an already-outstanding (incomplete) assignment for the same employee+module — a second failure doesn't spam a second assignment. This is the one cross-app signal in the codebase; if you're tempted to add another app-to-app `post_save` receiver, first check whether a direct service-function call (like `record_event()`) would be more traceable than an implicit signal connection.

## Settings / secrets

- Gophish API key, webhook HMAC secret, SMTP credentials: read from environment variables via `django-environ` or equivalent, never hardcoded, never committed (`.env` stays in `.gitignore`).
- One settings module per environment (`settings/base.py`, `settings/dev.py`, `settings/prod.py`) rather than `if DEBUG:` branching scattered through a single file.

## Errors & logging

- Webhook signature failures, adapter call failures, and reconciliation mismatches log at `WARNING`+ with enough context (campaign id, external_id) to debug without needing to reproduce — these are the failure modes the `debugger` project skill/agent will look for first.
- Don't swallow exceptions from `PhishingEngineClient` calls silently — either handle a specific, expected failure mode or let it propagate/alert.

## Shared launch path & RBAC (Phase 3)

- **Never launch a campaign anywhere except `campaigns/services.py::launch_campaign(campaign, actor, client)`.** The admin action and the `launch_scheduled_campaigns` Celery task both call it; it owns the approval-state check, rate limit, exemption-filtered group sync, the Gophish call, and the audit entry. It raises `CampaignLaunchError` (never returns a bool) so callers can't ignore a failure. `actor=None` is valid (scheduler).
- **Exemptions are enforced only by the `is_exempt=False` filter inside that service** — Gophish knows nothing about them. `PhishingEngineClient.sync_target_group()` makes the Gophish group exactly match the contacts passed in.
- **Row-level scoping is admin code, not Django permissions.** Django's permission framework is model-level only. Department Manager scoping is `DepartmentScopedAdminMixin.get_queryset()`; any new admin view/URL you add (e.g. the landing-page preview) must fetch objects via `self.get_queryset(request)`, never `Model.objects`, or it silently bypasses scoping — a real bug found in review.
- Custom permissions (`campaigns.approve_campaign`) are how "who may approve" differs from "who may edit"; gate actions with `require_permission()`.
- Add every new task to `CELERY_BEAT_SCHEDULE` (already burned us once).

## Risk scoring (Phase 4)

- The algorithm is a pure function (`risk_scoring/scoring.py`, no DB) — change scoring by adding a new `ALGORITHM_VERSION` there, never by editing v1 in place, then recompute. Snapshots are inserted, never updated.
- Recompute is triggered from `risk_scoring/signals.py` via `transaction.on_commit` (the worker must not run before the event row is visible); the daily `recompute_all_scores` covers time decay.
- Row scoping for anything new that lists people/campaigns: use `core.scoping.managed_departments(user)` (shared by the admin mixin and the `/analytics/` views) so admin and API can't disagree.
