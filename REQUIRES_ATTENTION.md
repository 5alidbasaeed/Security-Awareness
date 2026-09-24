# Requires Attention

Status of the 2026-09-24 code review. Most findings are now fixed (list at the bottom). This file keeps what is still open and what a deployment has to do because of the fixes.

---

## Still open

### 1. Training reminder emails can't leave the server — **Medium**
- **Where:** `docker-compose.yml`. `django`, `celery-worker` and `celery-beat` are only on the `internal` network, which is `internal: true` and has no outbound access.
- **Problem:** as soon as `DJANGO_EMAIL_BACKEND` points at a real external SMTP server, every reminder will fail. The failure is logged and retried every hour, so nothing crashes, but no email is ever sent.
- **Why not fixed:** it needs a deployment decision: which relay, which mail provider, and the credentials for it. Gophish's own sending profile has the same dependency.
- **Suggested fix:** add an SMTP relay container on both networks (the pattern Gophish already uses) that forwards to the organization's mail provider, and point `EMAIL_HOST` at it. Don't give the Django containers outbound access instead: that would weaken invariant #8.

### 2. Not yet verified against a full Gophish build — **Medium**
The admin-API isolation fix (fixed #1 below) was verified live on the real `docker-compose.yml` networks, with a stand-in container in place of Gophish. It could not be run against a real Gophish build: this review's sandbox blocks plain-HTTP `apt` downloads, so the Gophish image doesn't build here. The launch-adoption fix (fixed #2) calls Gophish's `/api/campaigns/summary`, which has also only been tested against the fake client.
- **Do once on a real host:** `docker compose up -d --build`. Then run README checks 1–6 (check 6 is new), and launch one test campaign to confirm `find_campaign` parses the real `/summary` response.

---

## Deployment actions required by the fixes

1. **Re-run `python manage.py setup_groups`** after deploying. Employee CSV export now needs the new `employees.export_employee_data` permission, and until the command runs even Security Admins can't export.
2. **Gophish campaign names now end in `[#<id>]`**, for example `Q3 Payroll Update [#12]`. This is how a launch that timed out is recognized and not sent again. Don't rename campaigns inside Gophish.
3. **New optional settings** (documented in `.env.example`): `RECONCILE_WINDOW_DAYS` (default 30), and `INTERNAL_SUBNET` / `INTERNAL_DYNAMIC_RANGE` / `GOPHISH_INTERNAL_IP`. Change the network ones only if `172.28.0.0/24` collides with a network on the host.
4. **Local dev:** `docker-compose.dev.yml` still publishes the Gophish admin UI on `localhost:3333`. It sets `GOPHISH_ADMIN_LISTEN=0.0.0.0:3333` for that, in dev only.

---

## Known limitations (tracked in `CLAUDE.md`)
- The launch rate-limit check isn't atomic: two simultaneous launches can go one over the limit.
- Webhook and sync `external_id` equality has only been proven in unit tests, not against real Gophish events.
- MFA on admin login is out of scope by decision (invariant #7). Revisit before real use.
- Upstream Gophish vs. a vetted fork (open decision #4) is still unresolved.
- On phone-width screens the dashboard nav and the wider tables scroll sideways inside their own boxes. The page itself never overflows. This is acceptable, not a bug.

---

## Fixed in this review

**First round** (commit `1f8396d`): webhook 500s and aborted reconciliation on malformed timestamps; `/analytics/` API scoping; over-long audit/report text causing database errors; webhook tests depending on `.env`.

**Second round** (commit `3b62595`):

| # | Finding | Fix |
|---|---|---|
| 1 | Nginx could reach Gophish's admin API (invariant #8) | Admin listener bound to Gophish's internal-network IP; verified: refused from Nginx, reachable from the internal network; README check 6 added |
| 2 | A launch that timed out could email everyone twice | Gophish campaigns named `<name> [#<id>]`; an existing one is adopted instead of re-sent |
| 3 | Department Managers could export employee data | New `export_employee_data` permission, Security Admin only |
| 4 | Department Managers could exempt their own staff | `is_exempt` is read-only for them |
| 5 | Department Managers could clear an employee's department | The field is required for them |
| 6 | Rescheduling kept the approval | `scheduled_at` changes send the campaign back to Draft |
| 7 | Webhook and sync stored different metadata shapes | Both store Gophish's `details` field |
| 8 | Reconciliation polled every campaign forever | Stops after `RECONCILE_WINDOW_DAYS` (default 30) |
| 9 | Reminders for deactivated modules | Filtered out |
| 10 | Health check returned 500 on some database errors | Any database error is now a 503 |
| 11 | Lint rules depended on the installed ruff version | Rules pinned in `pyproject.toml`; import order fixed |

**Frontend review** (this commit; all 9 dashboard pages checked at desktop and phone width, light and dark: no console errors, no CSP violations, no page overflow):

| Finding | Fix |
|---|---|
| Any week-over-week change, even −0.3, was labelled "improving"/"worsening", contradicting the employee trend and PDF reports, which need a 5-point move | One shared `analytics.change_direction()`; tile now reads e.g. "▼ −0.3 vs last week · stagnant" |
| Score chart's curve overshot, drawing scores no snapshot ever had | Monotone interpolation |
| A shaded last table row covered the card's rounded corners and bottom border | Cards clip their contents |
| Dark mode: shaded rows were darker than the card and looked like holes | Separate `--row-alt` token, lighter than the card in dark mode |
| "What drives this score" rows were double-indented | List moved out of the padded card body |
