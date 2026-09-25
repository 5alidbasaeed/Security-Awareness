# Phase 1: dashboard gaps (awareness / GRC view)

Nothing here is built. Ranked by value. Each needs a product decision first.

| # | Gap | Why it matters | Suggested approach |
|---|---|---|---|
| G1 | **No data-freshness indicator** | If Gophish webhooks stop, the dashboard goes stale with no warning, and evidence handed to an auditor can't show when it was current. | Record the last webhook and last reconciliation times. Show "Data as of …" in the header, with a warning when it's older than a threshold. |
| G2 | **No results by lure category or difficulty over time** | Which lure types people still fall for is the main question for an awareness lead. A failure rate that falls only because lures got easier isn't improvement. `CampaignTemplate.category` / `difficulty` exist but are only shown per campaign. | An Overview section and a Campaigns filter: failure and report rate by category and by difficulty, per period. |
| G3 | **Real reported phish barely on the dashboard** | Reporting real threats is the outcome the programme is for; simulations are the proxy. Today only the open-triage count appears. | Volume trend, median triage time, share confirmed malicious, top reporting departments. |
| G4 | **Only rolling date ranges** | Audits and board packs ask for "Q3" or "FY2026", not "last 90 days". | Add calendar quarter / year choices and a custom from–to range to the period selector. |
| G5 | **No CSV export of a filtered list** | Risk owners want the list they're looking at for follow-up. Reports only has fixed report types. | An "Export this view" link on Employees, Campaigns and Training that reuses the current filters, `csv_safe`, the export permissions and an audit entry. |
| G6 | **No new-joiner coverage** | Onboarding is a common control requirement and the time people are most exposed. | "Tested within N days of joining" rate. Needs a start date on `Employee` (the CSV import could supply it). |
| G7 | **No same-cohort improvement view** | Separates real behaviour change from staff turnover and changes in the lure mix. | For people who failed a test: outcome on their next test. |
| G8 | Training Manager lands on a 403 after login | Minor. They get a link to Training, but a role-aware landing page is cleaner. | After login, send each user to the first dashboard page they can open. |
