# Internal Phishing Simulation & Security Awareness Training Platform

Internal, self-hosted defensive security tool: runs simulated phishing campaigns against company employees, auto-assigns training on failure, and reports on organizational risk. **Not** a commercial product, not for actual malicious use. Full architecture rationale lives in [phishing-training-platform-plan.md](phishing-training-platform-plan.md) — read it for anything not covered below.

**Status**: Phase 0 infra portion complete and verified (domain/deliverability portion still pending real infra — see README.md). **Phase 1 done, bug-reviewed twice, fixed both times**: `Employee`/`Department`/`Campaign`/`Event`/`AuditLogEntry` models; a real `GophishClient` adapter; Gophish webhook ingestion with signature verification, DB-level idempotency, and defensive credential stripping; reconciliation scheduled via `CELERY_BEAT_SCHEDULE`; basic `Admin`/`Viewer` RBAC via `setup_groups`; audit logging on campaign launch, employee/department CRUD, and CSV export. **Phase 2 (training loop) done, verified live, and bug-reviewed**: `TrainingModule`/`Quiz`/`QuizQuestion`/`QuizChoice`/`TrainingAssignment`/`QuizAttempt` models; auto-assignment on simulation failure via a `post_save` signal on `Event`, tested end-to-end through a real signed webhook POST; pure `score_quiz()` function; hourly reminder task with a 24h cooldown, verified live (sent once, correctly suppressed on retry) via the console email backend. A **second review pass** (after Phase 2 landed) found and fixed: `setup_groups` was never updated for the six new training models, so a non-superuser Admin couldn't touch training data at all — fixed, and now has a regression test (`test_admin_group_can_manage_training_models`) so it can't silently reappear. Training admins also didn't use `AuditedAdminMixin` unlike every other admin in the codebase — fixed for consistency, including explicit audit logging on the `mark_started` bulk action (bulk `.update()` bypasses `save_model`). A refactor pass preceded Phase 2: cross-app FKs converted to Django's lazy string form (avoids a real circular import that direct-import FKs would have hit: campaigns → training → events → campaigns), plus deduplication of the webhook/reconciliation transaction logic (`events/services.py::record_event`) and the admin permission-check pattern (`AuditedAdminMixin.require_permission`) — the pre-refactor test suite passed unmodified afterward, confirming no behavior change. 23/23 tests passing. **Third review pass** found one more: the local-dev browser-access file was initially named `docker-compose.override.yml`, which `docker compose up` auto-loads with no flag — meaning a real deployment running the plain standard command on a fresh clone would've silently exposed the admin on `:8000`, directly against invariant #8's "hard requirement, not hardening." Renamed to `docker-compose.dev.yml`, which requires an explicit `-f docker-compose.dev.yml` to load — see README "Viewing the admin UI in a browser." **Fourth pass — full completeness audit against the plan doc's phase checklists** (not just bug-hunting): confirmed every Phase 0–2 line item is either done-and-verified or explicitly, deliberately flagged as not done — see the completeness table kept in the session record / ask for a re-summary if needed. The one previously-unverified claim this pass closed: "campaign sending" (Phase 1) had only ever been tested against `FakePhishingEngineClient`, never a real Gophish instance. Verified for real: set up a live Gophish instance (real API key, template, landing page, sending profile, group, all via its REST API), ran `CampaignAdmin.launch_campaign` through the actual browser, and confirmed Gophish genuinely created and accepted the campaign (201, `status: "In progress"`, target queued) — `GophishClient.create_campaign()` works correctly end-to-end. Also incidentally confirmed invariant #4's password-stripping live: the landing page's `<input type="password">` came back from Gophish's own API with its `name` attribute stripped, exactly as documented. Found in the same pass: Gophish's mail worker can panic and crash the container on a bad sending profile (see "Known deployment quirks") — fixed by adding `restart: unless-stopped` to every long-running service in `docker-compose.yml` (none had a restart policy before). Django admin remains the interim UI, no custom dashboard templates yet. MFA is **explicitly out of scope for now** (invariant #7), not merely deferred.

## Non-negotiable invariants

These come from explicit decisions in the plan doc. Don't relitigate them without updating the plan doc first.

1. **Gophish is only ever called through a `PhishingEngineClient` adapter.** No code outside that adapter calls Gophish's REST API directly. This is what keeps the engine replaceable.
2. **Django and Gophish never share a database.** Gophish's own DB (MySQL) is never queried or joined by Django. Gophish IDs (`gophish_campaign_id`, etc.) are stored in Django as plain reference fields, not FKs.
3. **The event log is append-only and immutable.** Events are never updated or deleted — this is what makes retroactive risk-score recalculation possible. Webhook ingestion must be idempotent using Gophish's `external_id` (webhooks can be delivered more than once).
4. **Never persist real passwords.** Gophish landing pages use `capture_credentials=true, capture_passwords=false`. Defense in depth: webhook-ingestion code must also explicitly strip any password-like field before storing `metadata`, never rely solely on Gophish-side config. Any cloned/imported landing page template must be manually checked to confirm it uses a native `<form>` + `type="password"` input (a JS-based submission can bypass the stripping).
5. **`email_opened` is excluded from risk scoring** (or weighted ~0) — tracking-pixel prefetching by mail clients makes it unreliable as a behavior signal. Fine for delivery diagnostics only.
6. **Risk scores are versioned snapshots computed from the event log**, not a hardcoded formula (`employee_id, score, computed_at, algorithm_version, contributing_metrics`) — must be recomputable if the algorithm changes.
7. ~~Admin auth requires MFA~~ — **dropped for now, by explicit user decision (2026-09-22)**, not an oversight. Admin login is currently plain Django session auth with no MFA, even though an admin account can launch simulated attacks org-wide. Revisit before this platform handles anything beyond local dev/testing. Employee training login (not yet built) was planned to use SSO/OIDC via the company's IdP — that piece is unaffected by this decision.
8. **Network isolation is a hard requirement, not hardening.** Two Docker networks: `public` (Nginx + Gophish's phishing listener only) and `internal` (Django, Celery, Postgres, Redis, Gophish admin/API — no published ports). Driven by an unpatched HIGH-severity CVE (GO-2026-4455) that leaks the Gophish admin API key in rendered dashboard HTML; isolating the admin UI is the mitigation.
9. **Never use `:latest` for the Gophish image.** Pin an exact version/digest.
10. **Secrets (Gophish API key, webhook HMAC secret) live in the deployment's secret store**, never in code or committed config.
11. **No SPA.** Frontend is Django templates + HTMX + Chart.js — deliberately rejected React for this project's scale/traffic pattern.

## Tech stack (condensed — see plan doc for full rationale)

| Layer | Choice |
|---|---|
| Custom app | Django + Postgres |
| Phishing engine | Gophish (pinned version) + its own MySQL |
| Task queue | Celery + Celery Beat, Redis broker |
| Auth | Django session auth for now (MFA dropped — see invariant #7). OIDC/SSO for employees still planned, not built. |
| Frontend | Django templates + HTMX + Chart.js |
| Reverse proxy | Nginx + Let's Encrypt |
| Containerization | Docker Compose, single server |

## Open decisions (see plan doc "Overall Readiness Assessment")

1. Event log schema finalization — not yet resolved.
2. Credential-capture configuration sign-off — not yet resolved.
3. ~~Network isolation topology finalization~~ — **resolved and verified**. Implemented in `docker-compose.yml` exactly as invariant #8 describes; live-tested (see README.md "Verifying the network isolation").
4. Gophish version to pin (upstream vs. vetted fork, given GO-2026-4455) — **partially resolved**: pinned to upstream `v0.12.1`, built from source, confirmed it boots and migrates correctly (see "Known deployment quirks" below). The upstream-vs-fork provenance question itself is still open — revisit before any real campaign.
5. ~~Whether basic audit logging/RBAC moves into Phase 1~~ — **resolved**: yes, both landed in Phase 1 (`AuditLogEntry`/`log_action`, `Admin`/`Viewer` groups via `setup_groups`) and are exercised by Phase 2's training-assignment/reminder code too.

## Known deployment quirks (found by actually running the stack — see git history)

- **Gophish requires a relaxed MySQL `sql_mode`.** Its oldest DB migrations insert zero-dates (`'0000-00-00'`) that MySQL 8's default strict mode rejects outright, crashing Gophish on first boot. Fixed with `--sql-mode=NO_ENGINE_SUBSTITUTION` on the `mysql` service in `docker-compose.yml` — another concrete data point that upstream Gophish predates current tooling defaults (see open decision #4).
- **Docker Desktop does not forward host-published ports onto a network marked `internal: true`.** A container's `ports:` binding will show in `docker inspect`'s `HostConfig.PortBindings` but never actually listen. This is *why* `django` has no `ports:` entry in `docker-compose.yml` — verification goes through `docker compose exec <service> curl ...` instead, which also happens to match the real deployment's access pattern (VPN/tunnel, no published port) better than a host port would have.
- **Gophish's mail-sending worker can crash the whole process (Go panic) on a bad sending profile.** Confirmed live: created a real campaign end-to-end via `GophishClient.create_campaign()` (proving Django's side of "campaign sending" actually works against a real Gophish instance, not just the fake test client) — Gophish accepted it (201, `status: "In progress"`), then panicked (`index out of range [0] with length 0` in `mailer.go`) trying to send through a sending profile pointing at an unreachable SMTP host, taking the container down. The campaign creation itself is Django's responsibility and it worked correctly; the crash is upstream Gophish failing to handle an SMTP connection failure gracefully — another concrete data point for the Gophish maintenance-status risk. Mitigated with `restart: unless-stopped` on every long-running service in `docker-compose.yml` (none had a restart policy before this was found) — that bounds the downtime but doesn't fix the underlying fragility or un-stick a campaign left mid-send; a real deployment needs a working sending profile (real SES/SendGrid, per the plan's tech stack) and should still expect to monitor Gophish's own health.

## Project-specific subagents

`.claude/agents/` has seven subagents scoped to this stack: `backend-engineer`, `frontend-engineer`, `architecture-reviewer`, `database-engineer`, `security-code-reviewer`, `debugger`, `test-engineer`. Prefer them over the generic `claude`/`general-purpose` agent for work that clearly falls in their lane.

## Project-specific skills

`.claude/skills/` has detailed reference material each subagent loads for its lane — design tokens, schema shapes, conventions, checklists, playbooks — kept separate from the subagent prompts so those stay lean:

| Skill | Content |
|---|---|
| `frontend-design` | Type scale, spacing scale, color tokens, component patterns, HTMX conventions |
| `backend-conventions` | App layout, `PhishingEngineClient` interface shape, Celery/webhook conventions |
| `database-schema` | Concrete table shapes, constraints, indexing, migration rules |
| `code-review-checklist` | Itemized project-specific review checklist (credential handling, engine boundary, network isolation, etc.) |
| `debugging-playbook` | Symptom → likely cause → concrete command/query, for this stack's common failure modes |
| `testing-conventions` | Test layout, adapter mocking, required-coverage list |
