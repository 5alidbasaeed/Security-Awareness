# Internal Phishing Simulation & Security Awareness Training Platform

Phase 0 (infra proof) + Phase 1 (core simulation loop backbone) + Phase 2 (training loop) + Phase 3 (RBAC, approval workflow, scheduling). See [phishing-training-platform-plan.md](phishing-training-platform-plan.md) for the full architecture and [CLAUDE.md](CLAUDE.md) for the non-negotiable invariants this implements.

## Running the stack locally

```bash
cp .env.example .env
# edit .env — at minimum set real values for POSTGRES_PASSWORD, MYSQL_PASSWORD,
# MYSQL_ROOT_PASSWORD, DJANGO_SECRET_KEY, GOPHISH_WEBHOOK_SECRET

docker compose up -d --build
docker compose run --rm django python manage.py migrate
docker compose run --rm django python manage.py setup_groups     # creates the 5 RBAC groups
docker compose run --rm django python manage.py createsuperuser  # for /admin/
```

This brings up: Postgres (Django's DB), MySQL (Gophish's DB), Redis (Celery broker), Django, Celery worker + beat, Gophish (builds from pinned source, see `gophish/Dockerfile`), and Nginx. `/admin/` is Phase 1/2's interim UI for employees, departments, campaigns, training, and read-only event/audit-log browsing.

## Viewing the admin UI in a browser

By default (`docker compose up`, no flags) Django has **no** browser-reachable port — matching the real deployment, where the admin is VPN/tunnel-only (invariant #8). To browse it locally, opt in explicitly:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

This publishes Django on **http://localhost:8000/admin/**. Log in with the superuser created above. `docker-compose.dev.yml` is deliberately *not* named `docker-compose.override.yml` — that filename is auto-loaded by plain `docker compose up`, which would mean a real deployment running the standard command on a fresh clone gets the admin silently exposed on :8000. Requiring the explicit `-f` flag makes local browser access something you opt into, not something that happens unless you remember to delete a file first. See the file's header comment for the full rationale.

**Generating new migrations**: `docker compose run` containers are ephemeral — files written inside one (like a freshly generated migration) don't persist to the host unless you bind-mount the source. Use:
```bash
docker compose run --rm -v "$(pwd)/backend:/app" django python manage.py makemigrations
```
then rebuild (`docker compose build django`) so the migration is baked into the image for normal runs.

**Running tests**: `docker compose run --rm django pytest`

## Training loop (Phase 2)

Set a `Campaign.training_module` (via `/admin/campaigns/campaign/`) to auto-assign that `TrainingModule` whenever an employee clicks the link or submits data on that campaign — this fires from a `post_save` signal on `Event`, so it works through both the webhook path and the reconciliation fallback. Verified live: a signed `credential_attempt` webhook produced a real `TrainingAssignment` row end-to-end.

Reminders run hourly (`apps.training.tasks.send_training_reminders`, `CELERY_BEAT_SCHEDULE`) for assignments outstanding >2 days, capped at one reminder per 24h per assignment. `EMAIL_BACKEND` defaults to the console backend — reminders print to the `django`/`celery-worker` container logs rather than sending real mail; set `DJANGO_EMAIL_BACKEND` in `.env` to a real SMTP backend for production.

Completion is staff-recorded via `/admin/training/quizattempt/` for now — there's no employee self-service quiz UI (needs the still-pending auth/SSO decision plus the still-deferred custom dashboard).

## Verifying the network isolation (Phase 0's actual exit criterion)

The whole point of Phase 0 is proving the topology, not just that containers boot:

```bash
# 1. Datastores publish no host ports.
docker compose ps
# postgres / mysql / redis / gophish / django / celery-* should show no
# host-side port mapping in the PORTS column — only nginx does (80).

# 2. Django is reachable and healthy, checked from inside the internal
#    network (nothing on that network is published to the host — see
#    docker-compose.yml header comment; a real deployment reaches Django via
#    VPN/SSH tunnel the same way).
docker compose exec django curl -s http://localhost:8000/healthz
# → {"healthy": true, "checks": {"database": "ok", "redis": "ok"}}

# 3. Gophish's phish listener is reachable through nginx (the public path).
curl -I http://localhost/
# → any HTTP response (404 is expected with no campaign/landing-page
#   configured yet) proves the public proxy path works.

# 4. Gophish's ADMIN port is NOT reachable from the host/public side.
curl -m 3 http://localhost:3333
# → connection fails/refuses (nothing is published on 3333)

# 5. ...but Django CAN reach it over the internal network, as it needs to for
#    the PhishingEngineClient adapter (Phase 1):
docker compose exec django curl -s -o /dev/null -w '%{http_code}\n' http://gophish:3333
# → a response code (redirect to /login is typical), proving internal
#   reachability without public exposure
```

All five checks above were run and passed against this exact scaffold. Tear down when done: `docker compose down`.

**Known fix baked in**: `mysql` runs with `--sql-mode=NO_ENGINE_SUBSTITUTION` — Gophish's oldest DB migrations insert zero-dates that MySQL 8's default strict mode rejects outright, so it won't boot against MySQL 8.4 without this. Another concrete data point for the upstream-vs-fork decision below.

## What's NOT here yet

- **Sending-domain SPF/DKIM/DMARC + a real mail-security-gateway test.** Needs a real domain and real infra this repo can't provide — do this against the actual deployment host before Phase 0 is considered complete for real use.
- **VPN/SSH-tunnel access to the Django dashboard on a real host.** Also host infrastructure, not something Docker Compose alone can set up.
- **Gophish's actual admin setup**: on first boot Gophish generates an admin password (check its logs: `docker compose logs gophish`) and an API key. Copy the API key into `.env` (`GOPHISH_API_KEY`) once you have it — the `GophishClient` adapter needs it to actually launch campaigns.
- **Custom HTMX dashboard templates, risk scoring (Phase 4), reporting (Phase 5)** — deliberately deferred; Django admin is the interim UI.
- **MFA on admin login** — explicitly out of scope for now by user decision (see CLAUDE.md invariant #7), not a deferral. Admin login is plain Django session auth. Revisit before this handles anything beyond local dev/testing.
- **Employee self-service training/quiz UI, real SMTP for reminders, training content authoring** — Phase 2 scope, deliberately deferred (see CLAUDE.md).
- `apps/risk_scoring` — still an empty app skeleton, Phase 4.

## Open decisions carried from the plan doc

See `CLAUDE.md` → "Open decisions". Two are partially addressed by this scaffold:

- **Network topology**: implemented as described in the plan doc (`docker-compose.yml`).
- **Gophish version**: pinned to upstream `v0.12.1` (see comment header in `gophish/Dockerfile`) — this is the version affected by the unpatched GO-2026-4455 CVE; network isolation of the admin port is the mitigation, not a fix. Revisit upstream-vs-fork before any real campaign.

## Subagents & skills

`.claude/agents/` and `.claude/skills/` have project-scoped subagents and reference material for backend, frontend, database, architecture review, security review, debugging, and testing. See `CLAUDE.md` for the index.

## Campaign workflow & roles (Phase 3)

`setup_groups` creates five groups: **Security Admin** (everything, incl. approving campaigns), **Campaign Manager** (create/edit/submit campaigns, cannot approve), **Training Manager** (training models), **Report Viewer** (view-only), **Department Manager** (change access scoped to departments listed in `Department.managers`; assign a user there *and* to the group). Campaign lifecycle: `Draft → Submit for approval → Approve (Security Admin only) → Launch` (manually, or automatically at `scheduled_at` via Celery Beat every 5 min). Launch syncs the target department's non-exempt employees into a Gophish group first, and is capped by `CAMPAIGN_LAUNCH_RATE_LIMIT` per 24h. Each campaign row has a **Preview** link that renders the landing page HTML fetched from Gophish.
