---
name: debugging-playbook
description: Symptom-to-cause playbook for the phishing-simulation platform — webhook delivery, event dedup, network split, Celery reconciliation. Load when investigating a bug or unexpected behavior in this project.
---

# Debugging playbook — phishing-simulation platform

Check in this order — most bugs in this system trace back to one of these, not to application logic being wrong.

## Symptom: events missing from the dashboard

1. Check Gophish's webhook delivery log / Django's webhook view logs for signature failures (`X-Gophish-Signature` mismatch) — a rejected webhook produces no event row and often no visible error unless logging is checked directly.
2. Check whether the Celery Beat reconciliation task (`events.tasks.reconcile_campaign`) is actually running: `celery -A <project> inspect scheduled` / check `django-celery-beat` schedule, and check its task logs for exceptions.
3. Query directly: `SELECT * FROM events_event WHERE campaign_id = X ORDER BY occurred_at DESC LIMIT 20;` — compare against what Gophish's own campaign results API reports for the same campaign, to isolate whether the gap is on the ingestion side or the query/dashboard side.

## Symptom: duplicate/double-counted events

1. Check the `UNIQUE (source, external_id)` constraint exists and is actually being hit — `\d events_event` in psql to confirm the constraint is present, not just intended.
2. Check whether the webhook view catches the resulting `IntegrityError` gracefully (expected, means dedup worked) vs. some other insert path bypassing the constraint (e.g. the reconciliation task using `bulk_create` without `ignore_conflicts=True`).

## Symptom: "Django can't reach Gophish" / "Gophish can't be administered"

This is almost always the public/internal Docker network split, not application code:
1. `docker network inspect <public_network>` and `<internal_network>` — confirm which containers are actually attached to which network.
2. Confirm Gophish's container publishes its phishing-listener port on `public` and its admin/API port only on `internal` — a common misconfiguration is accidentally putting both ports in the same `ports:` block.
3. From inside the Django container: `docker exec -it <django_container> curl -v http://gophish:3333/...` (admin/API port) — should succeed only if Django is on `internal` alongside Gophish's admin port.

## Symptom: risk score looks wrong

1. Don't assume the scoring algorithm is broken — first check which row is being displayed: `SELECT * FROM riskscoring_riskscoresnapshot WHERE employee_id = X ORDER BY computed_at DESC LIMIT 5;`. A UI bug showing a stale snapshot looks identical to a scoring bug.
2. Check `algorithm_version` on the displayed snapshot vs. the current version — a score computed under an old algorithm isn't automatically recomputed; if the expectation is "recompute everyone after an algorithm change," confirm that backfill job actually ran.
3. Confirm `email_opened` events aren't contributing weight — grep the scoring function for `email_opened` to make sure it wasn't accidentally included.

## Symptom: admin can't log in / MFA issues

1. Check `allauth.mfa` is actually enforced (middleware/decorator on the admin views), not just installed/available.
2. Check whether the user has an MFA method registered at all — a first-login admin account may need MFA enrollment before it can pass the login flow, which can look like a "broken login" from the outside.

## Symptom: employee can't complete training (SSO)

1. This is almost always the OIDC/SSO integration with the company IdP, not Django's own auth code — check the `django-allauth` OIDC provider config and the IdP-side redirect URI registration first.

## General principle

State the hypothesis from the list above before touching code. If none of these match, it's genuinely a new failure mode — document it here once resolved so the next debugging session benefits.
