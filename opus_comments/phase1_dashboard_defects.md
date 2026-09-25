# Phase 1: dashboard defects

Three defects found. All are fixed and covered by `backend/apps/dashboard/tests/test_phase1_review.py` (6 tests). The full suite passed (592) after the fixes.

---

## D1. Waived training assignments still counted as owed. Severity: High

**What was wrong.** A waiver is a documented exception: the assignment is neither owed nor done. `analytics.training_compliance` (the headline card) already applied that rule. Everything else on the dashboard ignored `waived_at`:

- `program_metrics.training_breakdown`: per-module table, overdue ageing, completion by department
- `program_metrics.annotate_activity`: `has_overdue` / `has_outstanding`, which drive the "Training overdue" attention count, the Employees "Training overdue" focus filter, and the row badge
- `program_metrics._compliance`: a campaign's "Follow-up training" block
- The Training page's Overdue / Outstanding / Completed filters
- The status badge on the Training table and the employee page, which said "Overdue" for a waived assignment

**How it was found.** In a rolled-back transaction on the demo data, one overdue assignment was waived. The card went from 4 overdue to 3. The module table, the ageing, the attention count and the Overdue list all stayed at 4, so the same page showed two different numbers.

**Fix.** Each of those places now applies `waived_at__isnull=True`, the same rule as `training_compliance`. Added a "Waived" option to the status filter and a "Waived" badge.

**Files.** `apps/risk_scoring/program_metrics.py`, `apps/dashboard/views.py`, `templates/dashboard/training.html`, `_training_table.html`, `employee_detail.html`.

---

## D2. Offboarded employees counted in today's risk posture. Severity: High

**What was wrong.** Offboarding sets `Employee.is_active=False` rather than deleting the record. The following still counted those people:

- `analytics.scope_summary`: headcount, average risk score, high-risk count, training debt. This feeds the Overview cards, the department pages and the `/analytics/` API.
- `program_metrics.risk_distribution`
- Overview "Highest risk" and "Top reporters"
- A department's "Highest risk in this department" list

A leaver could stay at the top of the high-risk list indefinitely and keep inflating the average.

**How it was found.** Deactivating the highest-scoring demo employee left the headcount at 48 and the average unchanged, and that person was still first in "Highest risk".

**Fix.** These figures now count current staff only. The person's history is kept: it still appears in per-campaign rates (the campaign happened) and on their own employee page, for audit. Coverage and the attention list already excluded inactive people.

**Files.** `apps/risk_scoring/analytics.py`, `apps/risk_scoring/program_metrics.py`, `apps/dashboard/views.py`.

---

## D3. Training Manager locked out of the whole dashboard. Severity: Medium

**What was wrong.** Every dashboard page required `risk_scoring.view_riskscoresnapshot`, which Training Manager doesn't have. They got the 403 page even on `/training/`, the page about their own work. They could only see training status through `/manage/` or `/admin/`.

**Fix.**
- `dashboard_access` takes an `also=` list of alternative permissions. `/training/` accepts `training.view_trainingassignment`.
- The nav only shows Overview / Campaigns / Employees / Departments to users with risk access.
- On the Training page, employee and department names link to their detail pages only for users who can open them.
- The 403 page links to Training for users who can see it.
- Risk scores stay hidden from Training Manager.

**Files.** `apps/dashboard/access.py`, `apps/dashboard/views.py`, `templates/dashboard/base.html`, `403.html`, `training.html`, `_training_table.html`.

---

## What passed without changes
- Logged-out users are sent to login from every page.
- Department Managers get 404 for other departments' employees, campaigns and departments, and no other department's names appear on list pages.
- 1,344 filter / sort / period / page combinations (valid and hostile, full page and HTMX) as Security Admin and Department Manager: no errors, none slower than 2 s.
- POST to read-only pages returns 405. Logout only accepts POST.
- In the browser: all charts render, no horizontal scroll at 375 px, no CSP violations, and the pages can't be framed (`frame-ancestors 'none'`).
