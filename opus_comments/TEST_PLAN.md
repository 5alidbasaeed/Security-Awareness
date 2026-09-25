# End-to-End Test & Gap-Analysis Plan

Written 2026-09-25 from a GRC and awareness-programme point of view. Three phases, in this order:

| Phase | Surface | URL(s) | Status |
|---|---|---|---|
| 1 | Main dashboard (risk and programme reporting) | `/`, `/campaigns/`, `/employees/`, `/departments/`, `/training/`, `/login/` | **Done, see results below** |
| 2 | Employee sign-in and training portal | `/portal/` (magic link, course, quiz, certificate, report-a-phish, learn page) | **Done, see [phase2_portal.md](phase2_portal.md)** |
| 3 | Admin console | `/manage/` (campaigns, content, people, training, governance, settings) and `/admin/` | **Done, see [phase3_admin_console.md](phase3_admin_console.md)** |

Scope note added during the review: the app is designed for laptop use, so Phases 2 and 3 were tested at 1366×768 only. Email delivery isn't configured; email steps were checked as rendered output.

Each phase runs the same five passes:

1. **Access & scoping.** Every page as each of the five roles (Security Admin, Campaign Manager, Training Manager, Report Viewer, Department Manager) plus anonymous. Expect 302 to login for anonymous, 403 page (not 500) for roles without access, and Department Manager rows limited to their departments, including by URL guessing.
2. **Functional crawl.** Every link, filter, sort, period and pagination combination, plus hostile input (`?page=-1`, `?sort=;drop`, `?period=x`, NUL bytes, 10 kB query strings, non-numeric IDs). Nothing may 5xx.
3. **Data correctness.** Recompute the key numbers independently from the event log and compare them with what the page shows (failure/report rate, coverage, training completion, overdue, risk distribution). Check that the rules in CLAUDE.md hold: `email_opened` ignored, waived assignments out of the denominator, exempt/inactive people out of coverage, small cohorts suppressed.
4. **Browser pass.** Real browser: console errors, CSP violations, charts render, HTMX swaps, light/dark, phone width (no horizontal scroll), keyboard focus.
5. **Gap analysis.** What a senior GRC / awareness lead expects that isn't there, ranked by value.

Fixes go in with a regression test. Items that need a product decision are listed, not built.

---

## Phase 1: Main dashboard

### Scope
Overview, Campaigns (+ detail), Employees (+ detail), Departments (+ detail), Training, login/logout, 403 page.

### Test cases
| # | Case | Expectation |
|---|---|---|
| 1.1 | Anonymous GET on every page | 302 to `/login/?next=` |
| 1.2 | Each role on every page | 200, or the 403 page for roles without `view_riskscoresnapshot` |
| 1.3 | Department Manager opens another department's employee/campaign/department by ID | 404 |
| 1.4 | Every filter/sort/period value, valid and hostile | never 5xx |
| 1.5 | HTMX partial requests (`HX-Request`) | partial template, same numbers as the full page |
| 1.6 | POST/PUT to read-only pages | 405 |
| 1.7 | Headline numbers vs independent recompute | equal |
| 1.8 | Waived training | not in assigned, overdue or outstanding, anywhere on the dashboard |
| 1.9 | Offboarded (inactive) employees | not in current-posture numbers (headcount, average risk, high-risk list, distribution) |
| 1.10 | Browser: console, CSP, charts, dark mode, 375 px width | clean |
| 1.11 | Logout | POST-only, session ends |

### Results (2026-09-25)

**Run:** 5 roles + anonymous × 11 pages; 1,344 parameter combinations (valid and hostile, full-page and HTMX) as Security Admin and Department Manager; waived/offboarded cases in a rolled-back transaction on the demo data; browser pass on all pages at 375 px and desktop. Full suite: 592 passed, including 6 new in `apps/dashboard/tests/test_phase1_review.py`.

| Case | Result |
|---|---|
| 1.1 Anonymous | Pass: all 302 to login |
| 1.2 Roles | Pass after fix (Training Manager was locked out of everything; see D3) |
| 1.3 Cross-department URL guessing | Pass: 404, and no names from other departments on any list page |
| 1.4 Filter/sort/period fuzzing | Pass: 0 non-200, none slower than 2 s |
| 1.5 HTMX partials | Pass: search pushes the URL, filters match the stat cards |
| 1.6 Non-GET | Pass: 405 (logout is POST-only too) |
| 1.7 Headline numbers | Pass after fixes D1/D2 |
| 1.8 Waived training | **Failed, fixed (D1)** |
| 1.9 Offboarded employees | **Failed, fixed (D2)** |
| 1.10 Browser | Pass: charts render, no overflow at 375 px, no CSP violations, `frame-ancestors 'none'` confirmed. **Not checked this run:** dark mode and keyboard focus order; carry into Phase 2. |
| 1.11 Logout | Pass |

#### Defects found and fixed
| # | Severity | Defect | Fix |
|---|---|---|---|
| D1 | High (reported numbers wrong) | Waived training assignments still counted as assigned/overdue in the per-module table, overdue ageing, department completion, "Training overdue" attention count, the Overdue/Outstanding filters, a campaign's follow-up training, and the row badge. Only the headline card excluded them, so the Training page contradicted itself (card 3 overdue, table 4). | `program_metrics._compliance`, `training_breakdown`, `annotate_activity` and the Training view now use the same rule as `analytics.training_compliance`. New "Waived" filter and badge. |
| D2 | High (reported numbers wrong) | Offboarded employees (`is_active=False`) stayed in the current-posture figures: headcount, average risk, risk distribution, Overview's "Highest risk" and "Top reporters", and a department's "Highest risk" list. A leaver could top the high-risk list indefinitely. | `analytics.scope_summary` and `risk_distribution` count current staff only (also fixes `/analytics/` API); lists filter `is_active`. Their history stays in per-campaign rates and on their own page for audit. |
| D3 | Medium (access) | Training Manager got 403 on the whole dashboard, including Training, the one page about their job. | `/training/` also opens with `training.view_trainingassignment`; nav hides pages they can't open, the 403 page links to Training, and names aren't linked to 403 pages. |

#### Gaps (awareness / GRC view), not built
Ranked by value. Each needs a product decision before building.

| # | Gap | Why it matters |
|---|---|---|
| G1 | **No data-freshness indicator** (last webhook received, last reconciliation run). | If Gophish webhooks stop, the dashboard goes stale with no warning, and evidence handed to an auditor can't show when it was current. |
| G2 | **No results by lure category or difficulty over time.** `CampaignTemplate.category`/`difficulty` exist but are only shown per campaign. | The main question in an awareness programme is which lure types people still fall for. A failure rate that falls only because lures got easier isn't improvement. |
| G3 | **Real reported phish are barely on the dashboard** (only a triage count). No volume trend, triage turnaround, or share confirmed malicious. | Reporting real threats is the outcome the programme exists for; simulations are the proxy. |
| G4 | **No custom date range or calendar quarter**, only rolling presets. | Audits and board packs ask for "Q3" or "FY2026", not "last 90 days". |
| G5 | **No CSV export of the filtered view** on dashboard lists (Reports has fixed report types only). | Risk owners want "this filtered list" for follow-up; today that means retyping it. |
| G6 | **No new-joiner coverage** (tested within N days of joining). | Onboarding is a common control requirement and when people are most exposed. |
| G7 | **No same-cohort improvement view** (how people who failed before did on their next test). | Separates real behaviour change from turnover and lure mix. |
| G8 | Training Manager still lands on a 403 after login (with a link to Training). | Minor: a role-aware landing page would be cleaner. |

#### Deployment note
The fixes were copied into the running `django` container and verified there. Rebuild (`docker compose up -d --build django celery-worker celery-beat`) so the image and the Celery workers pick them up.
