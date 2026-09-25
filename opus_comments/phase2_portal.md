# Phase 2: employee sign-in and training portal

Tested 2026-09-25. Laptop width only (1366×768): the app is designed for laptops, so phone widths are out of scope. **Email is not configured**: dev uses Django's console backend, so every email check below means "queued and rendered", never "delivered".

## How it was tested
1. **Scripted end-to-end run** with CSRF enforced, inside a rolled-back transaction: 46 checks. 43/46 at first; **46/46 after the fixes**.
2. **Real UI walkthrough at 1366×768** as a dedicated QA employee (`qa.portal@demo.example`) with one module of each kind: slides + quiz, slides only (overdue), external link + quiz, no content, and one waived. Sign-in form → emailed link (read from the worker's console output) → home → course with mouse and arrow keys → quiz (fail, then pass) → certificate → other module types → report a phish → sign-out → Back button → invalid link. Light and dark mode.
3. **Background email:** sign-in link task and the training-reminder task, both captured and read.
4. **Tests:** `apps/portal/tests/test_phase2_review.py`, 7 new tests. The portal and training suites pass (65).

## What passed
| Area | Result |
|---|---|
| Sign-in form | Browser blocks empty and malformed addresses; the email field is focused on load and has `autocomplete=email`; mixed-case addresses match; same "Check your email" page whether or not the address exists; POST without CSRF → 403 |
| Throttling | 5 requests for one address → exactly 3 emails; the extra requests look identical to the sender |
| Sign-in email | Correct recipient, subject and a working `/portal/enter/` link (console backend) |
| Tokens | Tampered, garbage, expired or deactivated → the same "This link has expired" page (reveals nothing); valid → signed in; session id rotated (no session fixation) |
| Session | 8 h lifetime; cookie HttpOnly and SameSite=Lax; deactivating the employee ends it immediately |
| Mixed sessions | Portal sign-in doesn't disturb a staff session in the same browser; a second employee's link switches employee; portal sign-out leaves the staff session alone; a portal session gives no staff access |
| Employee isolation | Another employee's assignment → 404 on the assignment, course, quiz and certificate pages; waived → hidden and 404 |
| Course | Next/Back links and arrow keys; progress bar with an accessible label; last slide offers "Continue to the quiz"; `?s=` out of range handled; slide text escaped |
| Quiz | Answer choices correctly labelled for screen readers; correct answers not in the HTML; a choice from another question doesn't score; a fail shows "You scored 0%, you need 100%" and keeps the attempt history; a pass leads to the certificate and is audit-logged; nothing accepted after completion |
| Certificate | Name, module, date, score; print CSS hides navigation |
| Other module types | External link opens in a new tab with `noopener noreferrer`; slides-only waits until the course is opened (after fix P3) |
| Report a phish | Empty form → clear error; a report whose subject contains `<img onerror>` / `<script>` is saved and a success message shown (escaping in the admin triage queue is covered in Phase 3) |
| Learn page | Public, no navigation, sets no cookie, records nothing |
| Headers | Strict CSP, cannot be framed; signed-in pages `no-store` (after fix P5) |
| Accessibility basics | `lang="en"`, one `h1`, a `main` landmark, visible focus outline, sensible tab order, correct dark-mode colours |

## Defects found and fixed
| # | Severity | Defect | Fix |
|---|---|---|---|
| P1 | Medium | **Home listed completed training above outstanding work** (Postgres sorts NULL last). | Outstanding first, soonest due first. `portal/views.py` |
| P2 | High (evidence) | **The quiz could be passed without opening the course.** Only the GET enforced it; a direct POST was scored and completed the assignment. | The POST redirects to the course until it has been opened. |
| P3 | High (evidence) | **A slides-only module could be completed in one click without opening the course.** | Completion refused until the course is opened; the button is replaced by "Open the course first". |
| P4 | Low (visual, all pages) | **Buttons overlapped the last line of text above them.** `.button` had no `display`, so `<a class="button">` stayed inline. | `.button` is `inline-flex`, with a 12 px gap after a paragraph. `dashboard/static/dashboard/app.css` |
| P5 | Medium (privacy) | **Portal pages were cacheable.** No `Cache-Control`, so on a shared laptop pressing Back after sign-out showed the previous employee's name, training and quiz (seen in the browser). The staff dashboard already sent `no-store`. | `portal_required` adds `never_cache`. `portal/access.py` |
| P6 | Medium (evidence) | **A module with no content could be "completed"** and produce a certificate for training that doesn't exist. | No completion without content; the employee sees "This training isn't ready yet". |
| P7 | Low | **Reminders were sent for modules with nothing to take** (no slides, no link, no quiz), so employees were chased for training they can't complete. | The reminder task skips those modules. `training/tasks.py` |
| P8 | Low | **Reminders never said when the training was due** or that it was overdue. | Reminder text states "Please complete it by …" or "It was due on … and is now overdue". |

## Known limitations confirmed (unchanged)
- **Magic links are reusable until they expire (72 h).** A forwarded link signs the recipient in as that employee. Every reminder email carries a fresh link.
- **External-link modules:** the quiz can be taken without opening the external content, and "Mark as started" is self-reported. The platform can't verify external viewing.
- **"Course opened" means the first slide was viewed**, not every slide.
- **The notes field has no `maxlength`**, so text over 5,000 characters is cut on the server without telling the reporter.
- **A signed-in employee who opens the sign-in page** still sees the form instead of being sent home.
- **No "skip to content" link** in the portal (the dashboard has one).

## Gaps (awareness / GRC view), not built
| # | Gap | Why it matters | Suggested approach |
|---|---|---|---|
| PG1 | **No SSO; long-lived reusable links** | A forwarded email lets someone else do the training as you, which undermines the completion evidence. | SSO/OIDC as planned. Meanwhile: one-time links, a shorter lifetime, and a confirm button on the landing page so mail link scanners don't use them up. |
| PG2 | **Quiz rigour** | Unlimited attempts with the score shown, and choices always in the same order, so it's quick to guess your way to a pass. | Shuffle choices; a random subset from a question bank; a cooldown after N fails. |
| PG3 | **No per-question feedback after a failed quiz** | Learners don't find out which answers were wrong or why, which wastes the most teachable moment in the course. | Show which questions were missed, with a short explanation per question, after each attempt. |
| PG4 | **Certificate can't be verified** | Reference is `<id>-<timestamp>`. HR or an auditor can't check it's genuine. | A signed code plus a staff-side verify page. |
| PG5 | **No policy acknowledgement** | ISO 27001 A.5.10/A.6.3 and NCA ECC expect a yearly acknowledgement of the security or acceptable-use policy. | An "acknowledge policy vX" assignment type with a timestamped record, reported like training. |
| PG6 | **No closed loop on reports** | The employee gets no reference number and never hears the verdict ("this was real, thank you" or "this was a simulation"). Feedback is what keeps people reporting. | Show "Your reports" with status in the portal, and optionally email the verdict. |
| PG7 | **Employees can't see their own record** | Points exist (`engagement.points`) but only staff see them; nor can employees see their simulation history. | "Your points" and "your simulation results" on the portal home. |
| PG8 | **One reminder email per item** | Three outstanding items mean three emails a day, each with its own sign-in link. Noisy, and it spreads more links around. | One daily digest listing everything outstanding. |
| PG9 | **No Arabic / RTL** | If the workforce is Arabic-speaking (the NCA ECC mapping suggests Saudi Arabia), training in a second language is less effective. | Django i18n for the portal chrome, plus per-module language variants. |
| PG10 | **Generic learn page** | The best moment to teach is right after the click, showing the red flags in *that* email. | Pass the template (not the person) to the learn page and show its red flags. |
| PG11 | **Report form is free text only** | Triage can't see headers or links. | `.eml`/`.msg` upload (size-capped, stored inert), or the mail add-in API path. |
| PG12 | **Employees can't reach the portal until a network decision is made** | Django is on the internal network only (REQUIRES_ATTENTION.md item 3). | Expose only `/portal/` through Nginx with TLS, or require VPN. |

## Test data left in the dev database
QA employee `qa.portal@demo.example` with four "QA …" modules and assignments, one completed quiz attempt, and one report (subject starts `QA-REPORT`). Kept for the Phase 3 triage check; removal is listed in `notes_for_next_phases.md`.
