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
`id, name, gophish_campaign_id (plain field, not FK), template_name, target_department_id (FK, nullable), status, scheduled_at, launched_at, created_by_id (FK → auth user)`

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

**`training_trainingmodule`** / **`training_quiz`** / **`training_assignment`**
`assignment`: `id, employee_id (FK), module_id (FK), assigned_at, due_at, started_at (nullable), completed_at (nullable), triggered_by_event_id (FK -> events_event, nullable)` — links a training assignment back to the failing event that caused it, without making the event mutable.

**`core_auditlogentry`** (pulled into Phase 1 per the plan)
`id, actor_id (FK -> auth user), action, target_description, occurred_at, metadata (jsonb)` — covers campaign launches, target-list changes, data exports at minimum.

## Migration conventions

- One migration per logical schema change; don't bundle unrelated model changes into one migration file.
- Any migration that drops a column, drops a table, or narrows a type is treated as a destructive operation — confirm with the user before writing/running it, per the project's general safety rules around irreversible actions. Prefer additive migrations (add nullable column, backfill, then constrain) over one-shot destructive changes.
- Never write a migration that adds a column resembling `password`/`credential`/`secret` to any table — if a task seems to need this, stop and flag it; it violates a hard project invariant.
- Index new foreign keys and any column used in a `WHERE`/`ORDER BY` on the dashboard's hot paths (`events_event.occurred_at`, `riskscoresnapshot.computed_at`) at the time you add the column, not as an afterthought.
