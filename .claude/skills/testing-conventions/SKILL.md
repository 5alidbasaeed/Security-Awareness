---
name: testing-conventions
description: Test layout, fixtures, and required-coverage conventions for the phishing-simulation platform — mocking the PhishingEngineClient adapter, idempotency and credential-stripping tests. Load before writing tests in this project.
---

# Testing conventions — phishing-simulation platform

Use `pytest-django` (preferred) or Django's `TestCase` — check `pyproject.toml`/`pytest.ini` for what's already configured before assuming.

## Layout

```
apps/<app>/tests/
├── test_models.py
├── test_views.py
├── test_tasks.py
└── factories.py      # factory_boy factories for that app's models
```
One factory per model in `factories.py`, reused across the app's tests rather than constructing model instances by hand in every test.

## Mocking the engine boundary

Never let a test hit a real Gophish instance. Provide a `FakePhishingEngineClient` (implements the same interface as `PhishingEngineClient`, returns canned data) in `apps/engine/tests/fakes.py`, and inject it via Django's dependency-injection point (settings override / fixture) in any test that would otherwise need Gophish. A test that requires network access to Gophish to pass is a test that's testing the wrong layer — mock at the adapter boundary, not deeper.

## Required tests (treat as non-optional, not just "nice coverage")

- **Webhook idempotency**: POST the same webhook payload (same `external_id`) twice; assert exactly one `Event` row exists afterward.
- **Signature rejection**: POST a webhook with a missing/invalid `X-Gophish-Signature`; assert 401/403 and that no `Event` row was created.
- **Credential stripping**: feed a payload containing a `password` (or similarly named) key through the ingestion path; assert it's absent from the stored `metadata`, even though this "shouldn't happen" given Gophish's config — this is defense-in-depth and must be tested as its own invariant, not assumed to follow from the signature test.
- **Event immutability**: assert there's no straightforward `.update()`/`.delete()` path exposed on the event queryset/manager for normal application code (a raw SQL/admin escape hatch existing is fine; a convenient application-level one is the thing to catch).
- **Risk score insert-only**: after two calls to the scoring function for the same employee, assert two `RiskScoreSnapshot` rows exist (not one row mutated) and that `computed_at` differs.
- **`email_opened` scoring exclusion**: assert a scoring run that includes only `email_opened` events produces a score equal to the baseline / doesn't move the score materially.
- **RBAC** (once role-gated views exist): for each restricted view (campaign launch, data export, admin dashboard), assert a non-privileged user gets 403, not just that a privileged user gets 200.

## What not to test

- Don't write tests that assert on Gophish's internal API response shape beyond what `FakePhishingEngineClient` needs to return — that's Gophish's test suite's job, not this project's.
- Don't write integration tests that spin up the real Docker Compose stack for routine unit-level coverage; reserve that for a smaller number of end-to-end smoke tests (Phase 0/1 deliverable), not the default per-PR test run.
