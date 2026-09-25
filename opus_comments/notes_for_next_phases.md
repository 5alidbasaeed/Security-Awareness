# Notes: test accounts, re-running, what is still untested

## Test accounts (dev database only)
`qa_security_admin`, `qa_campaign_manager`, `qa_training_manager`, `qa_report_viewer`, `qa_department_manager` (manages **Engineering**). None has a password; sessions were created with `force_login` in the Django shell. Delete them before any real deployment:

```
docker compose exec django python manage.py shell -c "from django.contrib.auth.models import User; User.objects.filter(username__startswith='qa_').delete()"
```

The Phase 2 QA employee (`qa.portal@demo.example`), its four "QA …" modules, attempts and report were deleted after testing. Their audit-log entries remain, because the log is append-only.

## Re-running the checks
- Regression tests for this review: `docker compose exec django python -m pytest apps/dashboard/tests/test_phase1_review.py apps/portal/tests/test_phase2_review.py apps/manage/tests/test_phase3_review.py`
- Full suite: `docker compose exec django python -m pytest -q`
- The code is baked into the image (no bind mount). After editing, rebuild: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`

## Deployment actions from this review
- **New setting** `IMPORT_MAX_DEACTIVATE_PERCENT` (default 10), documented in `.env.example`.
- **Employee CSV format change:** a row that exempts someone needs an `exempt_reason` column value. Tell whoever maintains the HR export.
- **Existing approved campaigns** are unaffected until someone edits their email or landing page. From then on, any content change sends them back to Draft.

## Still untested (needs real infrastructure)
- **Email delivery of any kind:** no SMTP relay (REQUIRES_ATTENTION.md item 1).
- **A real Gophish send:** launches were tested against the fake engine; a real launch with a working sending profile has not been run in this review.
- **Link-scanner behaviour** (Defender Safe Links etc.) against portal sign-in links.
- **Employees reaching `/portal/`** over the real network path (REQUIRES_ATTENTION.md item 3).

## Left in the working tree
- `backend/apps/manage/_patch_ux.py`: an untracked helper script from an earlier session that edits source files in place. Not used at runtime. Confirm it isn't needed and delete it.
- Nothing from this review is committed.
