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

# 6. ...and nothing on the PUBLIC network can reach it either. Gophish sits on
#    both networks, so its admin listener is bound to its internal-network
#    address only (GOPHISH_ADMIN_LISTEN in docker-compose.yml), not 0.0.0.0.
docker compose exec nginx wget -qO- -T 3 http://gophish:3333
# → "Connection refused". Checks 4 and 6 together are invariant #8.
```

Checks 1-5 were run and passed against this scaffold. Check 6 was added in the 2026-09-24 review, after it turned out the admin port *was* reachable from nginx (it listened on 0.0.0.0); the fix was verified with stand-in containers on the real compose networks, not yet against a full Gophish build. Tear down when done: `docker compose down`.

**Known fix baked in**: `mysql` runs with `--sql-mode=NO_ENGINE_SUBSTITUTION` — Gophish's oldest DB migrations insert zero-dates that MySQL 8's default strict mode rejects outright, so it won't boot against MySQL 8.4 without this. Another concrete data point for the upstream-vs-fork decision below.

## What's NOT here yet

- **Sending-domain SPF/DKIM/DMARC + a real mail-security-gateway test.** Needs a real domain and real infra this repo can't provide — do this against the actual deployment host before Phase 0 is considered complete for real use.
- **VPN/SSH-tunnel access to the Django dashboard on a real host.** Also host infrastructure, not something Docker Compose alone can set up.
- **Gophish's actual admin setup**: on first boot Gophish generates an admin password (check its logs: `docker compose logs gophish`) and an API key. Copy the API key into `.env` (`GOPHISH_API_KEY`) once you have it — the `GophishClient` adapter needs it to actually launch campaigns.
- **Custom HTMX dashboard templates, risk scoring (Phase 4), reporting (Phase 5)** — deliberately deferred; Django admin is the interim UI.
- **MFA on admin login** — explicitly out of scope for now by user decision (see CLAUDE.md invariant #7), not a deferral. Admin login is plain Django session auth. Revisit before this handles anything beyond local dev/testing.
- **Employee self-service training/quiz UI, real SMTP for reminders, training content authoring** — Phase 2 scope, deliberately deferred (see CLAUDE.md).
- Employee-facing training/quiz pages (needs the SSO decision) and campaign creation in the dashboard (management stays in the admin).

## Open decisions carried from the plan doc

See `CLAUDE.md` → "Open decisions". Two are partially addressed by this scaffold:

- **Network topology**: implemented as described in the plan doc (`docker-compose.yml`).
- **Gophish version**: pinned to upstream `v0.12.1` (see comment header in `gophish/Dockerfile`) — this is the version affected by the unpatched GO-2026-4455 CVE; network isolation of the admin port is the mitigation, not a fix. Revisit upstream-vs-fork before any real campaign.

## Subagents & skills

`.claude/agents/` and `.claude/skills/` have project-scoped subagents and reference material for backend, frontend, database, architecture review, security review, debugging, and testing. See `CLAUDE.md` for the index.

## Reports (Phase 5)

**Reports** in the dashboard nav (`/reports/`). Choose a report and a date range; figures are recomputed from the event log as of the end date, so any past period can be regenerated exactly (the same inputs give byte-identical PDF/ZIP files and the same SHA-256). Types: executive summary (PDF), campaign results, department summary, training compliance, employee risk scores (CSV), and a compliance evidence package (ZIP: summary PDF, results, training, audit log excerpt, methodology, `manifest.json` and `SHA256SUMS.txt`; verify with `sha256sum -c SHA256SUMS.txt`). Reports that name individuals (training compliance, risk scores, evidence package) are Security-Admin-only; Report Viewers, Campaign Managers and Department Managers get the aggregate ones, and Department Managers only for their own departments. Every generation and download is audit-logged, and generated files are immutable in the archive. A monthly org-wide executive summary is created automatically. PDF caveat: right-to-left scripts (Arabic, Hebrew) are not shaped in the PDF; the CSV exports keep all names intact.

## Dashboard (Phase 4.1)

Open **http://localhost:8000/** (dev override, see above) and sign in with a staff account that can view risk data (Security Admin, Campaign Manager, Report Viewer, Department Manager — Department Managers see only their own departments). Pages: Overview, Campaigns (+ funnel per campaign), Employees (live search, sort, department filter; per-person score history and what drives the score), Departments, Training. It is read-only; create and edit things in **Manage** (`/admin/`). Charts and interactions are HTMX + Chart.js, self-hosted (no CDN), under a strict Content-Security-Policy.

To see it with realistic data locally: `docker compose exec django python manage.py seed_demo_data` (DEBUG only; add `--reset` to remove it).

## Risk scoring & analytics (Phase 4)

Every scoring-relevant event (click, data submission, report) queues `risk_scoring.tasks.recompute_score`, which inserts a new `RiskScoreSnapshot` (never updates one); a daily task re-scores everyone because scores decay over time. Scores run 0-100, higher = riskier; algorithm `v1` is documented at the top of `apps/risk_scoring/scoring.py`. `email_opened` never counts, and training completion is reported separately as a compliance metric, not blended into the score. Snapshots are browsable (read-only) in `/admin/risk_scoring/riskscoresnapshot/`. JSON endpoints for the coming dashboard (staff with view permission; Department Managers see only their departments): `/analytics/departments/`, `/analytics/departments/<id>/trend/?weeks=12`, `/analytics/campaigns/<id>/`, `/analytics/employees/<id>/history/`. After changing the algorithm, run `python manage.py shell -c "from apps.risk_scoring.tasks import recompute_all_scores; recompute_all_scores.delay('<version>')"`.

## Campaign workflow & roles (Phase 3)

`setup_groups` creates five groups: **Security Admin** (everything, incl. approving campaigns), **Campaign Manager** (create/edit/submit campaigns, cannot approve), **Training Manager** (training models), **Report Viewer** (view-only), **Department Manager** (change access scoped to departments listed in `Department.managers`; assign a user there *and* to the group). Campaign lifecycle: `Draft → Submit for approval → Approve (Security Admin only) → Launch` (manually, or automatically at `scheduled_at` via Celery Beat every 5 min). Launch syncs the target department's non-exempt employees into a Gophish group first, and is capped by `CAMPAIGN_LAUNCH_RATE_LIMIT` per 24h. Each campaign row has a **Preview** link that renders the landing page HTML fetched from Gophish.

**Governance pages** (Manage sidebar, under *Governance*): Program & controls, Training assignments (extend/waive with a reason), Exemptions (reason + end date required), Users & access (roles, access review) and Audit log. After upgrading, run `docker compose run --rm django python manage.py setup_groups` so Security Admin receives the new `manage_user_access` permission.

## Phase 6 — Adoption and program maturity

### Management UI (`/manage/`)
Day-to-day work no longer needs Django admin. Signed-in staff get **Manage** in the top nav (permissions decide what shows): a campaign builder (draft → submit → approve/reject → launch, all on the one audited `launch_campaign()` path), a content library (email templates and landing pages, with a sandboxed preview), a template catalog with category/difficulty tags, smart (dynamic) audiences, training modules + a quiz-question editor, mandatory-training policies, employees and departments, CSV import, a reported-email triage queue, scheduled reports, a deliverability pre-flight, and API keys. Django admin stays as the advanced surface.

### Employee training portal (`/portal/`)
Employees are not Django users. They request a sign-in link by email (a signed, expiring token — the plan's magic-link fallback where there's no IdP), then see **only their own** assignments, take the training, answer the quiz (scored by the same `score_quiz()` the rest of the system uses), and get a printable certificate on passing. There's a "Report an email" form, and `/portal/learn/` is a public teachable-moment page for simulation landing pages to redirect to after a click.

### API (`/api/v1/`)
A read-and-draft JSON API authenticated with **API keys** (`Authorization: Api-Key sk_sim_…`). Create keys in **Manage → API keys**; the raw key is shown once. A key acts as its owner and is limited to explicit scopes, and it can never exceed the owner's own permissions (scope ∩ owner-permissions). Endpoints let a client **draft** content — training modules and quizzes, email templates, landing pages, and **draft** campaigns — and read scoped analytics. There is deliberately **no launch, approve, send or delete endpoint**: an automated tool can prepare a whole campaign, but a person still approves and launches it in the UI. `capture_passwords` is never accepted and always forced off (invariant #4).

```bash
# create a draft campaign (a person still has to approve + launch it)
curl -X POST https://<host>/api/v1/campaigns \
  -H "Authorization: Api-Key sk_sim_xxx" -H "Content-Type: application/json" \
  -d '{"name":"Q4 invoice test","template_name":"Invoice","landing_page_name":"Invoice page","landing_page_url":"https://<phish-host>/x","target_department":"Finance"}'
# → 201 {"id":…, "status":"draft", "note":"Created as a draft. A person must submit, approve and launch it in the dashboard."}

curl https://<host>/api/v1/analytics/summary -H "Authorization: Api-Key sk_sim_xxx"
```

### New scheduled tasks (Celery Beat)
Mandatory-training enrolment, overdue-training manager escalation, scheduled-report emails, and PII anonymization of long-deactivated employees — all daily and idempotent. See `CELERY_BEAT_SCHEDULE` and the new settings in `.env.example` (portal, coaching, SIEM webhook, cohort/retention).

### Phase 7
Production hardening and operability (admin MFA/SSO, throttling, backups, monitoring, real SMTP relay, data-governance) is written up in `phishing-training-platform-plan.md` → "Phase 7", and open operational items are tracked in `REQUIRES_ATTENTION.md`.

## End-to-end check (no email is sent)

`backend/e2e/run_e2e.py` drives the whole platform over real HTTP against the running stack — real Postgres, Redis, Celery worker and Gophish — and stops short of sending: content authoring in Gophish, draft campaign via the API, the approval workflow and role checks, the launch service with a guard client (real Gophish target-group sync, but the call that would email is intercepted, and it asserts Gophish gained no campaign), signed webhooks into ingestion (password stripped, idempotent), Celery scoring and training assignment, dashboard row scoping, the employee portal (magic link, quiz, certificate, report-a-phish), API keys and scopes, and reports plus the evidence package. It tags all of its data (`E2E`, `e2e_`, `@e2e.example`) and removes it first and last, so it is safe to re-run. It refuses to run unless `DEBUG` is on.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm -T -v "$PWD/backend:/app" django python -m e2e.run_e2e
```


## Sample content and slide courses

Training courses are decks of slides shown in the employee portal; the quiz follows the last slide. Authors build them in **Manage → Training** (add, edit, reorder slides; plain text, `- ` for bullets) and can preview the course and try the quiz without recording anything.

To load a sample library (four courses with quizzes, five simulated phishing emails, two landing pages, catalog entries and draft campaigns; nothing is sent or launched):

```bash
docker compose exec django python manage.py seed_sample_content
```

It is safe to re-run and never overwrites your edits. With DEBUG on it also creates a sample employee and prints their portal sign-in link. Email templates have a **Preview** link under **Manage → Content**.

**Builders and images.** Manage → Content has an email builder and a landing-page builder (layouts, logo, banner image, tracked button) with a side-by-side preview, and an image library. Uploaded images live in the `email-images` volume and nginx serves them read-only at `/i/`; set `EMAIL_IMAGE_BASE_URL` to your public sending domain (for example `https://send.example.com/i/`) so recipients' mail clients can load them.
