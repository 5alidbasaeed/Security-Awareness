# Review comments

End-to-end GRC / awareness review, 2026-09-25. All three phases are done. Laptop width only (the app is designed for laptops). Email delivery is not configured, so every email step was checked as rendered output, never as delivered.

| File | Contents |
|---|---|
| [TEST_PLAN.md](TEST_PLAN.md) | The 3-phase plan, the five passes run in each phase, and the Phase 1 results table |
| [phase1_dashboard_defects.md](phase1_dashboard_defects.md) | Dashboard defects D1–D3 |
| [phase1_dashboard_gaps.md](phase1_dashboard_gaps.md) | Dashboard gaps G1–G8 |
| [phase2_portal.md](phase2_portal.md) | Employee portal: what passed, defects P1–P8, gaps PG1–PG12 |
| [phase3_admin_console.md](phase3_admin_console.md) | Admin console: what passed, defects A1–A6, observations, gaps AG1–AG9 |
| [notes_for_next_phases.md](notes_for_next_phases.md) | Test accounts, how to re-run the checks, what is still untested |

## Defects fixed, by severity
| Severity | Defects |
|---|---|
| Critical | **A1** content edits after approval bypassed the approval (UI and API) · **A2** pasted or API landing-page HTML wasn't sanitised, so real passwords could be sent elsewhere (invariant #4) · **A5** a 4-row CSV import could offboard 46 of 49 employees; CSV exemptions skipped the reason requirement |
| High | **D1** waived training counted as overdue · **D2** offboarded staff in current risk figures · **P2/P3** training completed without opening the course · **A4** Department Managers could change organisation-wide content |
| Medium | **D3** Training Manager locked out · **P1** portal listed finished work first · **P5** portal pages cacheable after sign-out on a shared laptop · **P6** "completing" a module with no content · **A3** API-key expiry date 500 / silently never-expiring key · **A6** training figures disagreed between pages once someone left |
| Low | **P4** buttons overlapping text (every page) · **P7** reminders for modules with nothing to take · **P8** reminders without a due date |

Every fix has a regression test: `apps/dashboard/tests/test_phase1_review.py`, `apps/portal/tests/test_phase2_review.py`, `apps/manage/tests/test_phase3_review.py`.

## Top gaps to decide on
1. **Four-eyes approval and a record of what was approved** (AG1–AG3). The approval is the main control on a tool that sends simulated attacks.
2. **SSO or one-time links for the portal** (PG1). Reusable 72-hour links weaken the training evidence.
3. **Data-freshness indicator** on the dashboard (G1). A stalled Gophish webhook currently goes unnoticed.
4. **Results by lure type and difficulty** (G2), and **closing the loop on reported emails** (G3, PG6).
5. **Real SMTP relay**, then one live send of each email type (AG9).
