# Internal Phishing Simulation & Security Awareness Training Platform

## Overview

An internal, self-hosted platform (similar in concept to KnowBe4 / Proofpoint Security Awareness / Hoxhunt) for running simulated phishing campaigns against company employees, auto-assigning security awareness training on failure, and reporting on organizational risk over time.

**Scope**: Internal company tool, not a commercial multi-tenant SaaS product. Target scale: hundreds to a few thousand employees.

## Goals

- Send realistic simulated phishing emails to employees
- Track opens, link clicks, and (safely, without storing real credentials) credential-submission attempts
- Auto-assign short training modules + quizzes when an employee fails a simulation
- Track training completion
- Provide dashboards with per-user and per-department risk scores and trends over time
- Produce exportable reports for compliance/audit purposes (e.g., ISO 27001, SOC 2 evidence)

## Non-Goals (for MVP)

- Commercial multi-tenant SaaS
- SMS/voice phishing (vishing/smishing) simulation (revisit as optional in Phase 6.4)
- MFA/session-cookie capture (advanced red-team style attack simulation)
- Multi-org/reseller support

## Architecture Decision: Hybrid Build

Rather than building the phishing-simulation engine from scratch, the plan uses:

- **[Gophish](https://getgophish.com/)** as the phishing engine — handles campaign scheduling, template editor, tracking pixels/links, landing pages, and exposes a full REST API + signed webhooks.
- **Custom-built Django application** as the source of truth for all platform/business logic: employees, departments, campaign metadata, training modules, quizzes, training assignments, risk scoring, dashboards, compliance reports, audit logs.

**Coupling principle**: Django must never read or write Gophish's database directly, and must never assume Gophish's internal schema. All communication happens through Gophish's REST API and webhooks. Gophish IDs (campaign ID, group ID, template ID) are stored in Django as plain external-reference fields (e.g. `gophish_campaign_id`), not as database-level foreign keys, since the two systems use separate database engines.

**Engine replaceability**: All calls to Gophish should go through a thin adapter/interface (e.g. `PhishingEngineClient`) rather than being called ad hoc throughout the codebase. If Gophish is ever replaced by another engine, only the adapter implementation needs to change — the event model, risk scoring, and dashboards are engine-agnostic by design.

Rationale: Gophish already solves the hardest and most security-sensitive part (click/credential tracking mechanics, landing page hosting, deliverability-friendly sending) with a project that has a proven track record. Building that from scratch would duplicate effort and introduce new risk in exactly the part of the system most likely to have subtle bugs (e.g., tracking token collisions, spam-filter evasion).

## Event Architecture

A normalized, append-only event log sits between Gophish and Django's analytics/risk system. This decouples risk scoring from Gophish's internals and allows historical scores to be recalculated if the scoring algorithm changes later.

**Ingestion**: Gophish supports signed webhooks (HMAC-SHA256, `X-Gophish-Signature` header) for real-time event delivery — this is the primary ingestion path. A periodic Celery Beat job polls Gophish's REST API as a reconciliation fallback in case a webhook delivery is missed, not as the primary mechanism.

**Event types**: `email_sent`, `email_delivered`, `email_opened`, `link_clicked`, `credential_attempt`, `phishing_reported`, `training_assigned`, `training_started`, `training_completed`, `quiz_completed`.

**Event model fields**:
- `event_type`
- `employee_id` (FK to Django's Employee)
- `campaign_id` (FK to Django's Campaign, which holds `gophish_campaign_id`)
- `source` (`gophish` / `django` / `manual`)
- `external_id` (Gophish's event/result ID, used for idempotency — webhooks can be delivered more than once)
- `occurred_at` (timestamp from the source system)
- `recorded_at` (timestamp when ingested)
- `metadata` (JSONB — event-specific detail, with credential data explicitly excluded; see Credential Handling)

Events are immutable and append-only — never updated or deleted. This is what makes retroactive risk-score recalculation possible.

## Risk Scoring

Risk scores must not be a single hard-coded formula baked into the schema. Retain raw events indefinitely (or per a defined retention policy) and store scores as versioned snapshots (`employee_id`, `score`, `computed_at`, `algorithm_version`, `contributing_metrics` JSONB), so scores can be recomputed from history when the algorithm changes.

**Weighting guidance**:
- `credential_attempt` — highest weight; it's actual risky behavior, not just engagement
- `phishing_reported` — strongest *positive* signal; should offset or cancel a click in the same campaign, since it reflects correct behavior even after initial engagement
- `link_clicked` — moderate-high weight
- Repeat failures should compound (e.g., a multiplier or recency-weighted decay), not simply average with past results — a repeat offender should score worse than a one-off average suggests
- `training_completed` / `quiz_completed` — track as separate compliance metrics, not folded directly into the risk score, since completing training is a process metric, not a behavior metric
- `email_opened` — **exclude from scoring or weight near zero**. Many email clients (Outlook privacy protections, Apple Mail Mail Privacy Protection, Gmail image proxying) prefetch tracking pixels regardless of whether a human opened the email, making this signal unreliable for measuring behavior. Use it only for delivery/engagement diagnostics.
- Track trend direction (improving vs. stagnant) over time per employee/department, not just a current snapshot score.

## Credential Handling

The platform must never store real passwords submitted during simulated campaigns.

**Mechanism**: Configure Gophish landing pages with `capture_credentials=true`, `capture_passwords=false`. With this configuration, Gophish's client-side script removes `type="password"` inputs from the form before submission — the password value never reaches the Gophish server. This does not capture other fields (e.g., username), which is useful for identifying who submitted data.

**Caveat**: this protection relies on the landing page using a standard HTML `<form>` with a real `type="password"` input. A cloned login page that submits via JavaScript instead of a native form can bypass this stripping. Any imported/cloned landing page template must be manually inspected to confirm the password field is a standard password input before use in a real campaign.

**Defense in depth**: Django's event-ingestion code (processing Gophish webhooks) should never persist any field named or resembling `password`, even if one somehow appears in a payload — strip it explicitly before storing `metadata`/raw payload, rather than relying solely on Gophish-side configuration.

## Authentication

Login/authentication for the Django app must be built on an established library, not custom-built from scratch — authentication is exactly the kind of code where hand-rolled mistakes turn into real security holes.

**Recommended library: `django-allauth`** (MIT license, free, actively maintained). Covers both authentication needs the platform has with a single library:

- **Admin login**: `allauth.mfa` provides multi-factor authentication out of the box — TOTP authenticator-app codes, backup recovery codes, and WebAuthn/passkey support. Given an admin account on this platform can launch simulated attacks against the whole company, admin logins should require MFA, not just a password.
- **Employee login (for completing training)**: `django-allauth`'s social/OIDC provider support allows employees to log in using the company's existing identity provider (e.g., Microsoft 365/Azure AD or Google Workspace) instead of creating a new username/password for this platform. This is standard single sign-on (SSO) — the employee is redirected to the login screen they already know, then returned to the platform already authenticated. This meaningfully improves training completion rates versus asking employees to remember another password.

**Lighter alternative**: if the full feature set of `django-allauth` (email verification, password reset flows, profile linking, etc.) isn't needed, `mozilla-django-oidc` is a smaller library that handles only the "log in via the company's identity provider" piece, with MFA handled separately (e.g., via `django-otp`).

Either way, the principle is the same: authentication is implemented via a well-established, actively maintained library, configured for this platform's two login flows, rather than built as custom code.

## Security Isolation

- Gophish's public-facing phishing listener (email tracking, link redirects, landing pages) is reachable from the internet.
- Gophish's admin UI/API is **not** internet-facing — bind it to an internal Docker network only, reachable by the Django app and by admins via VPN/SSH tunnel, never through the public reverse proxy.
- Django's admin/dashboard is internal/VPN-restricted, not just protected by application-level login.
- PostgreSQL and Redis publish no ports to the host — reachable only via the internal Docker network by service name.
- Docker Compose should define at least two networks: a `public` network (Nginx + Gophish's phishing listener only) and an `internal` network (Django, Celery, Postgres, Redis, Gophish's admin/API port).
- A host-level firewall (ufw/iptables) should back this up as defense in depth, since a misconfigured `ports:` entry in Compose can accidentally expose an internal service.

This isolation is not optional hardening — see the Gophish version note below for why it's currently a hard requirement.

## Gophish Version & Security Management

- **Do not use `:latest`.** Pin an exact, tested Gophish version/image digest in Docker Compose.
- **Current status (verified, as this materially affects the plan)**: the upstream `gophish/gophish` project has had no commits since September 2024. A HIGH-severity (CVSS 7.6) incorrect-access-control vulnerability was disclosed in February 2026 (GO-2026-4455) affecting all released versions up to 0.12.1 — the admin dashboard exposes each user's long-lived API key directly in the rendered page HTML/JavaScript on every login. This is unpatched upstream.
- A community fork (`gophish-ng`) has picked up maintenance and includes unrelated fixes (e.g., a stored-XSS fix), but any fork needs its own trust/provenance evaluation before being used for a security-sensitive internal tool — don't adopt it purely because it's more active.
- **Given the above, network-isolating Gophish's admin/API (see Security Isolation) is a required mitigation, not optional hardening** — with the admin interface reachable only internally/via VPN, the API-key-exposure issue is far less exploitable, since it requires access to the rendered admin page itself.
- Store the Gophish API key and any webhook secret in the deployment's secret store (e.g., Docker secrets, environment file excluded from version control, or a secrets manager) — never in application code or committed config.
- Monitor `gophish/gophish` (or the chosen fork) and Go vulnerability databases (osv.dev, GitHub Security Advisories) periodically for new advisories, given the project's inconsistent maintenance history.
- **Database backend**: Gophish's default database is SQLite; MySQL is supported for more robust production use. Gophish does **not** support PostgreSQL as its backend. Recommended: Gophish on its own MySQL instance, Django on PostgreSQL — two separate database engines, reinforcing that the two systems' data must never be joined at the database level.

## Tech Stack

| Layer | Choice | License | Notes |
|---|---|---|---|
| Custom app backend | Django | BSD-3-Clause (free) | Chosen over Flask specifically for this product because the admin-panel/ORM/auth scaffolding saves significant time on CRUD-heavy screens (users, templates, campaign metadata) |
| Phishing engine | Gophish (pinned version; upstream or vetted fork — see Gophish Version & Security Management) | MIT (free) | REST API + signed webhooks; also handles landing pages and click/credential-attempt tracking |
| Gophish's own database | MySQL | Free (MySQL Community Edition, GPLv2) | Gophish supports SQLite (default) or MySQL; PostgreSQL is not supported by Gophish itself |
| Application database | PostgreSQL | PostgreSQL License (permissive, free) | Relational reporting needs (joins/aggregations across users, departments, campaigns, training events, the event log) make this the right category of DB |
| Task queue / scheduler | Celery + Celery Beat | BSD-3-Clause (free) | Handles scheduled training reminder emails, reconciliation polling of Gophish, and other async jobs |
| Queue broker / cache | Redis (v8+) | AGPLv3 (OSI-approved open source as of Redis 8; free for internal use) | Backs Celery only — not used as a primary datastore. Some Redis 7.4.x builds carried a temporary non-OSI license that only restricted offering Redis as a competing managed service, never internal use; Redis 8 reverted to AGPLv3. Valkey (BSD-3, drop-in compatible) is a zero-ambiguity alternative if the organization has a blanket policy against copyleft licenses even for internal tools |
| Charts / dashboard viz | Chart.js | MIT (free) | Per-user and per-department risk trend charts |
| Frontend | Django templates + HTMX + Chart.js | — | Appropriate for this scale and traffic pattern; avoids the complexity of a separate SPA + API layer for the internal-tool UI. React would only be justified by highly interactive real-time UI needs or parallel frontend teams, neither of which applies here |
| Reverse proxy / TLS | Nginx + Let's Encrypt (Certbot) | Free | Routes public landing-page traffic and internal dashboard traffic separately; enforces the public/internal network split |
| Containerization | Docker + Docker Compose | Apache 2.0 (free) | Docker Engine/Compose CLI are free regardless of company size (Docker *Desktop* licensing restrictions don't apply to a Linux server deployment) |
| Email sending | Amazon SES or SendGrid (via Gophish's SMTP profile) | Usage-based, not license-based | Free tier typically sufficient at internal-tool scale; only real recurring cost in the whole stack |

**License summary**: every software component is free and open-source. The only potential recurring cost is email-sending volume through a transactional email provider, which is usage-based, not license-based.

## Deployment Architecture (Single Server)

Designed to run entirely on one VPS/VM (e.g., 4 vCPU / 8GB RAM), with services logically isolated via Docker containers and Docker networks rather than physically separated across multiple servers — appropriate for this workload's bursty, low-concurrency traffic pattern. This capacity is sufficient for hundreds to a few thousand employees at typical campaign frequencies.

```
One Linux Server (Docker Compose)
├── nginx (reverse proxy + TLS termination — public network)
├── django-app (admin panel, dashboard, training/quiz logic — internal network)
├── celery-worker + celery-beat (scheduled reminders, Gophish reconciliation polling — internal network)
├── gophish (phishing listener on public network; admin/API on internal network ONLY)
├── mysql (Gophish's own database — internal network, no published ports)
├── postgres (Django's application database — internal network, no published ports)
└── redis (Celery broker — internal network, no published ports)
```

Add basic resource monitoring from day one (even simple `docker stats` logging on a cron, or a lightweight node_exporter) so a live campaign send doesn't silently overwhelm the box with no visibility until something breaks mid-campaign.

## Scaling Triggers (Future)

Move off single-server only when one of these occurs:

- DB becomes an I/O bottleneck (reporting queries interfere with send/track operations) → give Postgres its own server first
- Sending volume grows significantly (e.g., multiple orgs, or commercialization) → separate Celery workers/Redis from the dashboard app
- A data residency / compliance requirement mandates DB isolation
- Zero-downtime deploy requirements for the admin app without risking the public tracking endpoint

## Development Phases

1. **Phase 0 — Infrastructure proof**: Docker Compose skeleton with the network split (public/internal) already in place; Postgres, MySQL, Redis, Django, Gophish, Nginx running; test sending domain with SPF/DKIM/DMARC configured; test email sent through the company's real mail security gateway and confirmed landing in inbox, not junk; Gophish version pinned and admin/API confirmed unreachable from the public network.
2. **Phase 1 — Core simulation loop**: Employee management; campaign management; Gophish API/webhook integration; event log implementation; campaign sending; tracking; basic dashboard. **Also includes, moved up from later phases**: basic role separation (admin vs. viewer, via Django's built-in permissions) and basic audit logging for campaign launch, target-list changes, and data export — these are cheap now and very costly to add retroactively once real campaigns have already run without an audit trail.
3. **Phase 2 — Training loop**: Training modules; quizzes; automatic assignment after simulation failure; completion tracking; reminders via Celery Beat.
4. **Phase 3 — Management/security hardening**: Granular RBAC (Security Admin, Campaign Manager, Training Manager, Report Viewer, Department Manager); departments; exemption lists; campaign approval workflow; scheduling; expanded audit log UI; send-rate limiting; landing-page dry-run/preview before launch.
5. **Phase 4 — Analytics** *(built)*: Full risk-scoring model (click rate, credential-attempt rate, report rate, training completion, repeat failures, weighted per the Risk Scoring section above); department and individual trend views. Delivered as versioned, append-only score snapshots (algorithm `v1`), event-triggered plus daily recompute, and a department-scoped analytics API.
6. **Phase 4.1 — Custom dashboard** *(built)*: read-only staff dashboard (Django templates + HTMX + Chart.js, self-hosted, strict CSP): overview, campaigns and funnel, employees, departments, training. Management stays in Django admin.
7. **Phase 5 — Reporting** *(built)*: CSV/PDF export; compliance evidence packages; historical report generation. Delivered as an immutable, hashed, audit-logged report archive; "as of" reports recomputed from the event log so any past date is reproducible; a ZIP evidence package with a SHA-256 manifest; a monthly executive summary generated automatically; aggregate reports for report roles, individual-level reports Security-Admin-only, Department Managers scoped to their departments.
8. **Phase 6 — Adoption and program maturity** *(planned; see below)*.

### Phase 6 — Adoption and program maturity

Inputs: two items carried over from earlier phases, plus the gaps found in the competitive comparison below. Sub-phases are ordered by value; each ends with a review pass like Phases 0-5.

**6.1 Self-service (carried over)**
- **Employee training portal.** Employees sign in (OIDC/SSO via the company IdP; a magic-link fallback if no IdP is available) and see only their own data: assigned training with due dates, a start/complete flow, and the quiz. Quiz scoring reuses the existing pure `score_quiz()` function; a passing attempt completes the assignment; a completion certificate is issued. Done when an employee can go from the reminder email to a completed, scored assignment without staff involvement, and cannot see anyone else's data.
- **Campaign builder in the dashboard.** A guided create-campaign flow (template, landing page, audience, schedule, training module) that reads the available Gophish templates, pages and sending profiles through `PhishingEngineClient` (new list methods), previews the landing page, and submits for approval; plus an approval queue where a Security Admin approves or rejects with a reason. It must use the existing `launch_campaign()` path, permissions and audit log, never a second launch path. Done when a Campaign Manager can create, submit and schedule a campaign, and a Security Admin can approve it, without opening Django admin.

**6.2 Close the biggest competitive gaps**
- **Employee import and directory sync.** CSV import first, then IdP/HRIS sync (Entra ID / Google Workspace / Okta / LDAP) for joiners, movers and leavers, with offboarding that deactivates rather than deletes (event history is immutable). New hires optionally get a baseline campaign.
- **Report button and teachable moment.** A phish-report button (Outlook/Gmail add-in or a forward-to mailbox) feeding `phishing_reported`, plus an immediate "this was a simulation, here is what to look for" page after a click. Real reported emails go to a simple triage queue. The report button is the strongest positive signal the scoring model already rewards but nothing can currently produce.
- **Template library, recurring and adaptive campaigns.** Curated templates with category and difficulty tags and rotation (addresses the "content decay" risk), recurring/drip schedules with randomized send times, and smart groups (dynamic audiences such as repeat clickers, new hires, high-risk) with difficulty that adapts to each person.
- **Policy-driven training and escalation.** Annual/mandatory awareness enrolment (not only failure-driven), configurable due dates, and manager escalation for overdue or repeat-failure employees.
- **Scheduled reports and a read-only API.** Emailed monthly reports to named recipients, and a token-authenticated read-only reporting API for BI/SIEM use.

**6.3 Engagement and integrations**
- Real-time coaching messages (Slack/Teams/email) at the moment of failure; gamification (badges, streaks, team leaderboards, points for reporting) designed to reward reporting rather than shame failure; SIEM/webhook export; a baseline-versus-current improvement (ROI) report; localization including right-to-left languages.

**6.4 Advanced and optional**
- QR-code, attachment and reply-to (BEC) simulations; smishing/vishing (would reverse a current Non-Goal, so decide deliberately); privacy controls (retention policy, anonymization, minimum cohort size before a department is reported); deliverability pre-flight checks (SPF/DKIM/DMARC verification and a seed-inbox test).

**Before real use (not a feature phase, but blocks production):** the Phase 0 domain/SPF/DKIM/DMARC and mail-gateway validation; a decision on admin MFA/SSO (currently dropped, see CLAUDE.md invariant #7); HSTS and proxy-SSL settings for the production settings module; the upstream-versus-fork decision for Gophish; VPN/tunnel access to the internal network.

## Competitive Gap Analysis (input to Phase 6)

Compared against KnowBe4, Hoxhunt, Proofpoint Security Awareness, Cofense and Sophos Phish Threat. Rows marked † are stated on the vendors' own documentation or comparison pages retrieved 2026-09-24 (links below); the others reflect general knowledge of the category and should be re-verified before committing scope.

| Capability | What leading platforms do | This platform today | Planned |
|---|---|---|---|
| Report-a-phish button + triage | KnowBe4 Phish Alert Button with PhishER prioritization†; Cofense Reporter and Triage†; Hoxhunt rewards reporting of real threats† | `phishing_reported` event and scoring credit exist, but no button and no intake | 6.2 |
| Just-in-time coaching | KnowBe4 Real-Time Coaching delivers a contextual tip in Teams, Slack, Google Chat or email at the moment of risky behavior† | Training assigned on failure, hourly email reminders | 6.2 (teachable-moment page), 6.3 (chat) |
| Adaptive, personalized campaigns | Hoxhunt individual learning paths and difficulty that adapts to skill, role and location, roughly every 10 days†; KnowBe4 AI-recommended campaigns and Smart Groups† | Manual campaigns to one department | 6.2 |
| Template library and realism | KnowBe4 large template library†; Proofpoint simulations based on live threat data† | Templates are hand-made in Gophish | 6.2 (library, rotation) |
| Gamification | Hoxhunt badges, streaks, leaderboards† | None | 6.3 |
| Training content | Large multi-language libraries (Cofense courses in 30+ languages†); SCORM/xAPI hosting | One external link per module, staff-recorded completion | 6.1 (portal), 6.2 (policy-driven), 6.3 (localization) |
| Risk model | KnowBe4 SmartRisk score at user, group and organization level†; Proofpoint AI-driven user risk scoring† | Versioned simulation-only score with trends and drivers (comparable, and more auditable) | Done (Phase 4); external signals not planned |
| Directory sync | AD/Entra/Okta/Google sync for joiners, movers and leavers | Manual entry in admin; no import at all | 6.2 |
| Employee self-service | Employee portal with own progress and certificates | None (staff-recorded) | 6.1 |
| Campaign creation UX | Wizard with template picker, audience and scheduling | Django admin plus a hand-made Gophish setup | 6.1 |
| Reporting | Compliance dashboards†, scheduled reports, API | On-demand PDF/CSV, evidence package, monthly archive, as-of history | Done (Phase 5); scheduled email and API in 6.2 |
| Integrations | SIEM, chat, ticketing; tight coupling to the vendor's email security (Proofpoint, Sophos Central)† | Signed webhooks in only | 6.3 |
| Other channels | QR, attachments, reply-to, SMS, voice | Email links only | 6.4 (optional) |
| Privacy and governance | Retention and anonymization options | Audit log, RBAC, no retention policy | 6.4 |

Where this platform is already ahead: full ownership of data and the event log, immutable and reproducible "as of" history, a transparent versioned risk algorithm, no per-seat cost, and network isolation as a hard requirement. Deliberately not pursued: multi-tenancy/reseller support, a bundled commercial content library (license one instead), and AI-generated templates.

Sources: [KnowBe4 Phish Alert Button manual](https://support.knowbe4.com/hc/en-us/articles/208969608-Phish-Alert-Button-PAB-Product-Manual), [PhishER manual](https://support.knowbe4.com/hc/en-us/articles/360010802673-PhishER-Product-Manual), [Real-Time Coaching](https://support.knowbe4.com/hc/en-us/articles/6654432814355-SecurityCoach-Product-Manual), [SmartRisk Engine FAQ](https://support.knowbe4.com/hc/en-us/articles/40003728753171-FAQ-SmartRisk-Engine-and-Risk-Score-Guide), [Hoxhunt phishing training](https://hoxhunt.com/product/phishing-training), [KnowBe4 vs Proofpoint (Hoxhunt)](https://hoxhunt.com/blog/knowbe4-vs-proofpoint), [Cofense and Sophos comparison (Guardey)](https://www.guardey.com/phishing-simulation-software/).

## Key Risks / Open Questions

- **Gophish maintenance status**: upstream is inactive (no commits since Sept 2024) with an unpatched HIGH-severity access-control vulnerability (GO-2026-4455, Feb 2026) exposing the admin API key in rendered dashboard HTML. Mitigated by strict network isolation of the admin/API (see Security Isolation), but should be periodically re-evaluated — either for a patched release or a vetted, trustworthy fork.
- **Deliverability**: the sending domain will need to be trusted by whatever mail security gateway is in place, or simulated emails get blocked — validate this in Phase 0 before building further.
- **Content decay**: phishing templates need regular refresh or employees start recognizing the platform's fingerprint.
- **Cloned landing pages and JS-based forms**: password-stripping protection can be bypassed by non-standard form submission; each imported template needs manual verification.

## Overall Readiness Assessment

The architecture is sound and does not require a redesign. Before writing code, resolve five decisions (none of which are large engineering efforts):

1. Event log schema (append-only, immutable) — decide before Phase 1's event-sync code is written.
2. Credential-capture configuration (`capture_passwords=false`) + a no-password-persistence rule in the ingestion code — decide before any landing page is configured for a real test.
3. Network isolation topology (which Docker network each service sits on) — decide before the Phase 0 Compose file is written.
4. Gophish version to pin, and upstream-plus-isolation vs. a vetted fork, given the unpatched CVE — decide before Phase 0.
5. Move basic audit logging and basic role separation into Phase 1 instead of Phase 3.

None of these block starting Phase 0 today.
