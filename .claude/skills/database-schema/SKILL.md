---
name: database-schema
description: Concrete Postgres schema reference for the phishing-simulation platform — event log, risk-score snapshots, campaign/employee/training tables, indexing and migration conventions. Load before schema design or migration work in this project.
---

# Database schema reference — phishing-simulation platform (Postgres, Django ORM)

Django owns this schema. Gophish's MySQL database is a completely separate system, never joined, never queried from here — any table here that references a Gophish object stores a plain string/int reference field, not a foreign key.

## Core tables (target shape — adjust field types to real Django model needs, keep the constraints)

**`employees_department`**
`id, name, manager_employee_id (nullable FK → employee)`

**`employees_employee`**
`id, email (unique), full_name, department_id (FK), is_exempt (bool), created_at`

**`campaigns_campaign`**
`id, name, gophish_campaign_id (plain field, not FK, unique — see below), template_name, landing_page_name, landing_page_url, target_department_id (FK, nullable), status, scheduled_at, launched_at, created_by_id (FK → auth user)`

`gophish_campaign_id` is `unique=True` (nulls allowed) — it's the join key back to Gophish for webhook/reconciliation event attribution; two Django rows sharing one Gophish campaign ID would silently misattribute events. `landing_page_name` and `landing_page_url` are distinct fields — Gophish's create-campaign API needs a landing page *name* (an existing Gophish object reference, like `template_name`) separately from the redirect *URL*; conflating them was a real Phase 1 bug (reusing the target group name as the page name), fixed after live review.

**`events_event`** — append-only, the most important table in the schema
```
id                bigserial PK
event_type        varchar   -- email_sent, email_delivered, email_opened, link_clicked,
                             -- credential_attempt, phishing_reported, training_assigned,
                             -- training_started, training_completed, quiz_completed
employee_id       FK -> employees_employee
campaign_id       FK -> campaigns_campaign
source            varchar   -- gophish / django / manual
external_id       varchar   -- Gophish's event/result id; NULL for source=django/manual
occurred_at       timestamptz
recorded_at       timestamptz  default now()
metadata          jsonb     -- never contains a password-like field, see backend-conventions skill
```
Constraints: `UNIQUE (source, external_id) WHERE external_id IS NOT NULL` — this is what makes webhook redelivery idempotent at the DB layer, not just in application code. Index `(employee_id, occurred_at)` and `(campaign_id, event_type)` for the dashboard/report queries that will hit this table hardest.

No `updated_at` column on this table — that's a deliberate signal that rows are never updated. If a future change adds one, that's a design regression, not a convenience.

**`risk_scoring_riskscoresnapshot`**
```
id                 bigserial PK
employee_id        FK -> employees_employee
score              numeric
computed_at        timestamptz default now()
algorithm_version  varchar
contributing_metrics jsonb
```
No unique constraint on `employee_id` — multiple rows per employee over time are expected. "Current score" = most recent row per employee (`DISTINCT ON (employee_id) ... ORDER BY employee_id, computed_at DESC`), not a field that gets updated. Index `(employee_id, computed_at)`.

**`training_trainingmodule`**
`id, title, description, content_url, duration_minutes, is_active` — content lives off-platform (external LMS/video link) for now, this isn't a content authoring tool.

**`training_quiz`**
`id, module_id (OneToOne -> training_trainingmodule), passing_score_percent`

**`training_quizquestion`** / **`training_quizchoice`**
`quizquestion`: `id, quiz_id (FK), text, order`. `quizchoice`: `id, question_id (FK), text, is_correct`. Scored by the pure function `apps.training.scoring.score_quiz(quiz, answers)` — no DB write, directly unit-testable.

**`training_trainingassignment`** (built, Phase 2)
`id, employee_id (FK), module_id (FK), assigned_at, due_at (nullable), started_at (nullable), completed_at (nullable), last_reminded_at (nullable), triggered_by_event_id (FK -> events_event, nullable)` — `triggered_by_event_id` links a training assignment back to the failing event that caused it, without making the event mutable. Auto-created by a `post_save` signal on `Event` (see `apps.training.signals`) when a qualifying event (`link_clicked`/`credential_attempt`) fires on a campaign with a `training_module` set; deduped against an already-outstanding assignment for the same employee+module. Index `(employee_id, module_id)`.

**`training_quizattempt`**
`id, assignment_id (FK), score_percent, passed, completed_at` — staff-recorded for now (no employee self-service quiz UI yet); recording a passing attempt marks the assignment's `completed_at`.

**`campaigns_campaign.training_module_id`** (added Phase 2, FK -> `training_trainingmodule`, nullable) — which module to auto-assign when this campaign's simulation is failed. All of the FKs above use Django's lazy string reference form (`"employees.Employee"`, `"events.Event"`, `"training.TrainingModule"`), not a direct model import — see the `backend-conventions` skill for why (avoids a real circular import between campaigns/training/events).

**`core_auditlogentry`** (pulled into Phase 1 per the plan)
`id, actor_id (FK -> auth user), action, target_description, occurred_at, metadata (jsonb)` — covers campaign launches, target-list changes, data exports at minimum.

## Migration conventions

- One migration per logical schema change; don't bundle unrelated model changes into one migration file.
- Any migration that drops a column, drops a table, or narrows a type is treated as a destructive operation — confirm with the user before writing/running it, per the project's general safety rules around irreversible actions. Prefer additive migrations (add nullable column, backfill, then constrain) over one-shot destructive changes.
- Never write a migration that adds a column resembling `password`/`credential`/`secret` to any table — if a task seems to need this, stop and flag it; it violates a hard project invariant.
- Index new foreign keys and any column used in a `WHERE`/`ORDER BY` on the dashboard's hot paths (`events_event.occurred_at`, `riskscoresnapshot.computed_at`) at the time you add the column, not as an afterthought.

## Phase 4 additions

- `risk_scoring_riskscoresnapshot` as specced above, built. Append-only via `core.managers.AppendOnlyQuerySet` (shared with `events`) plus a `save()` guard. Employee FK is CASCADE (derived data, unlike the event log's PROTECT). "Current score" = `risk_scoring.services.latest_snapshots()`. Tests that need a backdated `computed_at` must use raw SQL (auto_now_add + blocked `update()`).

## Phase 3 additions

- `campaigns_campaign.status` values: `draft`, `pending_approval`, `approved`, `launched`. New nullable FKs to auth user: `submitted_by`/`approved_by` (+ `submitted_at`/`approved_at`). `scheduled_at` (Phase 1 column) is now acted on: an `approved` campaign with `scheduled_at <= now` is launched by the scheduler. Meta permission `approve_campaign`.
- `employees_department.managers` — M2M to auth user (login/permission fact: who may manage this department in the admin). Distinct from the existing `manager` FK to Employee (org-chart fact). Don't conflate them.
- The launch rate limit is computed from `core_auditlogentry` rows with `action="campaign_launched"` in the trailing 24h — no separate counter table.
