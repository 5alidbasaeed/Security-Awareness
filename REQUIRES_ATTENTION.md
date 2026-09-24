# Requires Attention

Issues found in the code review of 2026-09-24 that were **not** fixed in that pass. Each needs either a live test against the running stack or a decision from the project owner. The fixes that were made are recorded in `CLAUDE.md` under "Code review + refactor pass".

Priority: **High** = security or data-integrity risk · **Medium** = wrong behavior in a real deployment · **Low** = cleanup or edge case.

---

## Needs a fix and a live test

### 1. Nginx can reach Gophish's admin API — **High**
- **Where:** `gophish/config.json.example` (`admin_server.listen_url: "0.0.0.0:3333"`), `docker-compose.yml` (the `gophish` service is on both `public` and `internal`), and the `nginx/conf.d/` comment.
- **Problem:** the admin API listens on every network interface, and the Gophish container shares the `public` network with Nginx. So Nginx, or anything else on `public`, can reach `gophish:3333`. This breaks invariant #8, which exists because CVE GO-2026-4455 leaks the admin API key. The Nginx config comment says there is "no route" to the admin port, which is wrong. README check #4 only tests from the host machine, so it never caught this.
- **Suggested fix:** give the `internal` network a fixed subnet and give `gophish` a static IP on it. Bind `admin_server.listen_url` to that IP, templated through `gophish/entrypoint.sh`. Keep the phishing listener on `0.0.0.0:80`.
- **Verify:** `docker compose exec nginx wget -qO- -T 3 http://gophish:3333` fails, `docker compose exec django curl http://gophish:3333` still gets a response, and the phishing pages still load through Nginx. Add the Nginx check to the README's "Verifying the network isolation" section.

### 2. Training reminder emails can't leave the server — **Medium**
- **Where:** `docker-compose.yml`. `django`, `celery-worker` and `celery-beat` are only on the `internal` network, which is `internal: true` and has no outbound access.
- **Problem:** as soon as `DJANGO_EMAIL_BACKEND` points at a real external SMTP server, every reminder will fail. The failure is logged and retried every hour, so nothing crashes, but no email is ever sent.
- **Suggested fix:** add an SMTP relay container that is on both networks, the same pattern Gophish already uses, and point Django at it. Don't give the Django containers outbound access instead: that would weaken invariant #8.

### 3. A timeout during launch can send a campaign twice — **Medium**
- **Where:** `backend/apps/campaigns/services.py::launch_campaign`.
- **Problem:** if `create_campaign` times out after Gophish has already created and sent the campaign, Django raises an error and the campaign stays **Approved**. The next click on Launch, or the next scheduler run, creates and sends it a second time to the same employees.
- **Suggested fix:** before creating, look up an existing Gophish campaign with the same name (or a unique name tag) and adopt it if found. Alternatively, mark the campaign as "launching" before calling Gophish, so a failed launch needs a person to reconcile it instead of retrying automatically.

---

## Needs a decision

### 4. Department Managers can export their employees and mark them exempt — **Medium**
- **Where:** `backend/apps/employees/admin.py` (`export_as_csv`) and `backend/apps/core/management/commands/setup_groups.py`.
- **Problem:** the code comment says employee CSV export is "Admin-only", but the check is `employees.change_employee`, and Department Managers have that permission. They can export their own department's employee list. The same permission lets them set `is_exempt`, which removes employees from campaigns.
- **Decide:** is this intended? If not, check a dedicated permission for export (for example `employees.export_employee`) and make `is_exempt` read-only for Department Managers.

### 5. Department Managers can remove an employee from every department — **Low**
- **Where:** `backend/apps/core/admin_mixins.py::DepartmentScopedAdminMixin`.
- **Problem:** the department dropdown only offers departments the manager manages, but it can still be left blank. Blanking it moves the employee out of the manager's view and out of every campaign. The effect is similar to marking them exempt, but without the exempt flag showing it.
- **Decide / fix:** make the field required for department-scoped users (set `required=True` on the form field in `formfield_for_foreignkey`).

### 6. Some edits after approval keep the campaign approved — **Low**
- **Where:** `backend/apps/campaigns/admin.py` (`APPROVED_CONTENT_FIELDS`).
- **Problem:** changing `scheduled_at` or `training_module` on an approved campaign keeps the approval. That means the send time can be moved, including to "now", without anyone approving it again.
- **Decide:** should rescheduling require re-approval? If so, add `scheduled_at` to `APPROVED_CONTENT_FIELDS`.

---

## Smaller issues

### 7. Webhook and sync store event details in different shapes — **Low**
- **Where:** `backend/apps/events/views.py` stores only `details`; `backend/apps/events/tasks.py` stores the whole Gophish timeline entry.
- **Problem:** the same kind of event has a different `Event.metadata` layout depending on which path recorded it. Nothing reads these fields today, but anything that does later will have to handle both.
- **Suggested fix:** store the same subset from both paths.

### 8. Reconciliation runs forever on every launched campaign — **Low**
- **Where:** `backend/apps/events/tasks.py::reconcile_all_active_campaigns`.
- **Problem:** there is no completed or closed state for a campaign, so every launched campaign is polled every 15 minutes indefinitely. The number of Gophish API calls grows with every campaign ever launched.
- **Suggested fix:** add a terminal campaign state, or stop reconciling after a fixed number of days since launch.

### 9. Reminders go out for deactivated training modules — **Low**
- **Where:** `backend/apps/training/tasks.py::send_training_reminders`.
- **Problem:** the query doesn't filter on `module__is_active`, so employees keep getting reminders for a module an admin has switched off.

### 10. The health check can return a 500 instead of 503 — **Low**
- **Where:** `backend/config/health.py`.
- **Problem:** the database check only catches `OperationalError`. Any other database error becomes a 500, where a 503 with an error message is expected.

### 11. The linter reports about 50 style warnings — **Low**
- **Where:** run `ruff check backend --exclude "*/migrations/*"`.
- **Problem:** mostly import order (`I001`), unused `noqa` comments (`RUF100`), and mutable class attributes on Django admin classes (`RUF012`, which is normal for Django). None are correctness bugs. Clean them up with `ruff check --fix` and consider ignoring `RUF012` for `admin.py` files.

---

## Already known (tracked in `CLAUDE.md`, listed here for completeness)
- The launch rate-limit check isn't atomic: two simultaneous launches can go one over the limit.
- The webhook and sync paths build `external_id` from Gophish's `time` string. They have only been shown to match in unit tests, not against real Gophish events.
- MFA on admin login is out of scope by decision (invariant #7). Revisit before real use.
- Upstream Gophish vs. a vetted fork (open decision #4) is still unresolved.
