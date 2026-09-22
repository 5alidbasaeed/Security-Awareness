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
- [ ] Is every new model added to `apps/core/management/commands/setup_groups.py`'s `MANAGED_MODELS`? A model missing from that list gets **zero** Admin/Viewer permissions — even the "Admin" group can't touch it without superuser. This was missed for all six Phase 2 training models on first landing; there's no system check that catches it, so it has to be checked by hand every time.
- [ ] Does every new `ModelAdmin` that supports create/update/delete use `AuditedAdminMixin` for consistency with the rest of the codebase? If a custom admin action does a bulk `.update()`/`.delete()` (bypasses `save_model`/`delete_model`), does it log explicitly (see `CampaignAdmin.launch_campaign`, `TrainingAssignmentAdmin.mark_started` for the pattern)?

## Network / secrets
- [ ] Does a Docker Compose or Nginx change publish a port for Postgres, Redis, MySQL, or Gophish's admin/API outside the `internal` network?
- [ ] Is any new dev-convenience Compose file named `docker-compose.override.yml`? That exact filename is auto-loaded by plain `docker compose up` with no flag — a real deployment running the standard command on a fresh clone would silently inherit whatever it publishes. Dev-only network/port overrides must use a different filename (`docker-compose.dev.yml` is the existing convention) and be loaded explicitly via `-f`. Found and fixed once already — see git history.
- [ ] Does the Gophish image tag remain pinned (not `:latest`)?
- [ ] Do the Gophish API key, webhook secret, or DB credentials appear in a committed file, default value, or hardcoded string?

## Scope / phase discipline
- [ ] Does the change assume RBAC/roles beyond what's been built yet, or skip audit logging for a sensitive action (campaign launch, target-list change, data export) that should be logged per Phase 1 scope?

If every box above is empty, defer to the general `code-review` skill for correctness/simplification/efficiency review of the diff itself.
