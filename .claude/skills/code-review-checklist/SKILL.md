---
name: code-review-checklist
description: Project-specific review checklist for the phishing-simulation platform — the concrete things to check on every diff before merging. Complements the general-purpose code-review/security-review skills rather than replacing them. Load when reviewing a diff in this project.
---

# Code review checklist — phishing-simulation platform

Run this checklist on top of (not instead of) general code quality review. Each item traces to a specific decision in `phishing-training-platform-plan.md` / `CLAUDE.md` — treat every "yes" answer below as a blocking issue, not a style note.

## Engine boundary
- [ ] Does anything outside `engine/` import a Gophish SDK/HTTP client, or construct a Gophish API URL directly? → block.
- [ ] Does a new feature route through `PhishingEngineClient`'s existing interface, or does it require extending the interface? (Extending it is fine; bypassing it is not.)

## Database coupling
- [ ] Does any new code query or connect to Gophish's MySQL database from Django? → block.
- [ ] Is a Gophish ID (campaign, template, group) stored as a Django-level `ForeignKey`, implying a joinable relationship that doesn't actually exist? → should be a plain field.

## Event log integrity
- [ ] Does the diff `UPDATE` or `DELETE` a row in the events table, anywhere? → block, event rows are append-only.
- [ ] Does new webhook-handling code rely on `external_id` + a DB-level unique constraint for idempotency, or only an application-level check that can race? → prefer DB-level.
- [ ] Is `X-Gophish-Signature` verified before the payload is touched, with rejection (not just logging) on mismatch?

## Credential handling
- [ ] Does anything persist a field named or resembling `password`/`credential`/`secret` from a webhook payload or form? → block, this is a hard project rule.
- [ ] If a new landing-page-template import path is added, does it flag/require manual review for JS-based (non-native-form) submission that could bypass Gophish's password stripping?

## Risk scoring
- [ ] Is a risk score ever updated in place, or always inserted as a new versioned snapshot (`algorithm_version`, `computed_at`)?
- [ ] Does anything give `email_opened` events meaningful scoring weight? → should be excluded or near-zero per the plan's documented rationale (tracking-pixel prefetch unreliability).
- [ ] Are `training_completed`/`quiz_completed` folded into the risk score itself, or kept as separate compliance metrics? → should be separate.

## Auth
- [ ] MFA is explicitly out of scope for now (CLAUDE.md invariant #7, dropped by user decision) — don't flag its absence as a finding; do flag any hand-rolled auth logic that bypasses Django's own session auth.

## New model / new app (added after Phase 2 review found this gap twice-removed)
- [ ] Is every new model added to `apps/core/management/commands/setup_groups.py`'s `MANAGED_MODELS`? A model missing from that list gets **zero** permissions for every group — even Security Admin can't touch it without superuser. This was missed for all six Phase 2 training models on first landing; there's no system check that catches it, so it has to be checked by hand every time.
- [ ] Does every new `ModelAdmin` that supports create/update/delete use `AuditedAdminMixin` for consistency with the rest of the codebase? If a custom admin action does a bulk `.update()`/`.delete()` (bypasses `save_model`/`delete_model`), does it log explicitly (see `CampaignAdmin.launch_campaign`, `TrainingAssignmentAdmin.mark_started` for the pattern)?

## Risk scoring changes (Phase 4)
- [ ] Was v1's behavior edited in place instead of adding a new `ALGORITHM_VERSION`? Old snapshots must stay reproducible.
- [ ] Does a new score input use `email_opened` or fold in training/quiz completion? Both are excluded by design.
- [ ] Does a new analytics endpoint scope by `managed_departments(user)`, and require `view_riskscoresnapshot`?

## Data quality & robustness (full-review findings)
- [ ] Does a metric depend on a field nothing ever sets (e.g. `due_at` behind "overdue")? Trace the writer, not just the reader.
- [ ] Does a batch loop (reminders, reconciliation, scheduler) stop entirely when one item raises? One bad row must not starve the rest, every run.
- [ ] Does an export write admin-entered text without `core.csv_safe.csv_safe`?
- [ ] Is a derived fact (`passed`) also a hand-editable field that can contradict its source (`score_percent`)?
- [ ] Is the web server timeout longer than the sum of the external calls a request chains?

## Dependencies and migrations (full-review findings)
- [ ] Ran `pip-audit -r backend/requirements/base.txt` (in a throwaway container) and resolved or consciously accepted every finding? A pin that was current months ago can carry dozens of CVEs.
- [ ] Does `manage.py makemigrations --check --dry-run` report "No changes"? Editing a field's `help_text`/`choices` after generating its migration leaves drift.
- [ ] Does `ruff check --select F,E9,B` come back clean on non-test code (dead variables, unused imports)?

## Reporting (Phase 5)
- [ ] Does a report read snapshots or current state instead of recomputing "as of" the report date from the event log? Historical reports must be reproducible.
- [ ] Does a new report name individuals without `employee_level=True` (and so the `export_employee_level` permission)? Can a Department Manager's report include another department?
- [ ] Are generation and download both audit-logged, and is the archived file left immutable?
- [ ] Are user-controlled strings escaped in the PDF (`xml.sax.saxutils.escape`) and passed through `csv_safe` in CSV?
- [ ] Is a listing query pulling the `content` bytes instead of using `.without_content()`?

## Dashboard / frontend (Phase 4.1)
- [ ] Does a new dashboard view start from `dashboard/scope.py` (never `Model.objects`) and use `@dashboard_access`?
- [ ] Does a template add an inline `style=`/`<script>`/`onclick=`, or load anything from another origin? The dashboard CSP forbids it.
- [ ] Is a risk level/threshold hardcoded in a template or JS instead of coming from `risk_scoring.analytics`?
- [ ] Is colour ever the only signal? Every risk/status badge needs its text label.
- [ ] Does `hx-include` sit on a container that also holds HTMX links (double-sent params)? Does an empty query param bypass a default?

## Network / secrets
- [ ] Does a Docker Compose or Nginx change publish a port for Postgres, Redis, MySQL, or Gophish's admin/API outside the `internal` network?
- [ ] Is any new dev-convenience Compose file named `docker-compose.override.yml`? That exact filename is auto-loaded by plain `docker compose up` with no flag — a real deployment running the standard command on a fresh clone would silently inherit whatever it publishes. Dev-only network/port overrides must use a different filename (`docker-compose.dev.yml` is the existing convention) and be loaded explicitly via `-f`. Found and fixed once already — see git history.
- [ ] Does the Gophish image tag remain pinned (not `:latest`)?
- [ ] Do the Gophish API key, webhook secret, or DB credentials appear in a committed file, default value, or hardcoded string?

## Scope / phase discipline
- [ ] Does the change assume RBAC/roles beyond what's been built yet, or skip audit logging for a sensitive action (campaign launch, target-list change, data export) that should be logged per Phase 1 scope?

If every box above is empty, defer to the general `code-review` skill for correctness/simplification/efficiency review of the diff itself.

- [ ] Does any new admin view/URL fetch objects via `Model.objects` instead of `self.get_queryset(request)`? That bypasses Department Manager row-scoping (found in Phase 3 review on the landing-page preview).
- [ ] Does anything launch a campaign other than `campaigns/services.py::launch_campaign()`? It owns approval, rate limit, and exemption filtering — a second path skips all three.
- [ ] Can a workflow/state field (`status`, `approved_by`, ...) be edited through the plain admin form? It must be `readonly_fields` and change only via a permission-checked, audited action. Does every custom admin action call `require_permission()` (visibility of an action is not authorization)?
- [ ] After approval, can what was approved (template, landing page, target) change without dropping the campaign back to Draft?
- [ ] Does a role that is supposed to be row-scoped (Department Manager) hold `change_`/`add_` on a model that controls the scoping itself (Department)? Do its FK dropdowns offer only in-scope rows?
- [ ] Does any view return Gophish-hosted/author-controlled HTML from Django's origin without `Content-Security-Policy: sandbox`?
- [ ] Is a check-then-act (status check then external send) done under `select_for_update` in one transaction? Is an audit entry written before raising inside that same `atomic()` block (it would roll back)?

## Public endpoints and outbound mail (Phase 6 review)
- [ ] Does any endpoint reachable without authentication send email, call an external service, or trigger a lookup on request? It needs a per-target AND per-client throttle in the shared cache, a uniform response, and must never log or store the raw address (hash it).
- [ ] Do all email sends and outbound HTTP calls have a timeout, so one slow dependency can't pin a web worker?
- [ ] Does a POST-capable diagnostics view (DNS/HTTP checks) require a write-level permission rather than a view permission?
- [ ] After checking a branch out on Windows: do container scripts keep LF endings (`.gitattributes`), and did `docker compose down && up` run after any change to a network's subnet (stale DNS aliases)?
- [ ] Are runtime artifacts (`*.rdb`, beat schedules, sqlite files) ignored and untracked?

## End-to-end review findings (Phase 6)
- [ ] Does any middleware or post-processing overwrite a header a view set deliberately (CSP `sandbox`, cache headers)? Use `setdefault`; a stricter view policy must win.
- [ ] Does a time-based job (retention, reminders, escalation) measure from the right event? "Deactivated N days ago" needs a `deactivated_at`, not `created_at`. Check that the stamp is set on every code path, including partial `save(update_fields=...)` and bulk updates.
- [ ] Did you run `python -m e2e.run_e2e` after changing anything that crosses services (webhooks, Celery, Gophish adapter, permissions)? Unit tests with a fake engine can't catch integration drift.

