# Phase 3: admin console (`/manage/`)

Tested 2026-09-25 at laptop width (1366×768). A fake phishing engine was used for everything that would reach Gophish, and all state-changing scripts ran in rolled-back transactions. Nothing was sent, and demo data wasn't changed. Email is not configured, so no email step claims delivery.

## How it was tested
1. **GET access matrix:** all 82 admin-console routes × 5 roles + anonymous, filled with real object IDs.
2. **POST authorization sweep:** every route × 5 roles, each in its own rolled-back transaction, checking that a refused request (403/404/405) never wrote an audit entry (i.e. changed nothing).
3. **Campaign lifecycle end-to-end:** Campaign Manager creates → submits → can't approve → can't launch while pending → Security Admin rejection without a reason is refused → approves → edit after approval resets to Draft → re-approve → Department Manager sees it → launch (fake engine) → exempt and offboarded people are **not** synced to the engine → launched campaign can't be edited or deleted → a second launch creates no second engine campaign → every step audited. **14/14.**
4. **Workflow and hostile-input checks:** CSV import, CSV exports, triage escaping, governance reasons, user-access guardrails, image uploads, mail settings, website-clone address checks. **13/19 at first (all 6 failures in the CSV import); 19/19 after the fixes.** Re-run on the rebuilt images: 18/18 (the triage-verdict check needs the QA report deleted during cleanup).
5. **Browser:** 27 admin pages as Security Admin: console/CSP errors, horizontal overflow, overlapping buttons, unlabelled form fields, headings.
6. **Tests:** `apps/manage/tests/test_phase3_review.py` (15 new), plus one existing import fixture updated. **Full suite: 614 passed** on the rebuilt images, including every new test from all three phases.

## What passed
| Area | Result |
|---|---|
| Access | Anonymous → sign-in everywhere. Each role only reaches what its permissions allow; a Department Manager gets 404 on other departments' campaigns and employees. POST-only actions return 405 to GET. No 5xx anywhere. |
| Refusals are clean | Every 403/404/405 in the POST sweep left the audit log untouched |
| Approval workflow | Campaign Manager can't approve; nothing launches before approval; rejection needs a reason; a campaign edit after approval resets it; launched campaigns can't be edited or deleted; one engine campaign per launch |
| Audience | A Department Manager can only target their own departments, never "Everyone" or a smart group. Exempt and offboarded people are never synced to Gophish. |
| Governance | Exemption needs a reason (form); waiver needs a reason; due-date extension can't go into the past; you can't edit your own access or a superuser's |
| Exports | Employee and audit-log CSVs neutralise spreadsheet formulas |
| Triage | Reporter-supplied HTML is shown as text in the queue and in the audit log (checked in a real browser) |
| Image library | SVG, an HTML file renamed `.png`, and files over 1 MB are all refused |
| Mail settings | CR/LF in username or return address refused; the password is never shown back (empty field, `autocomplete=new-password`) |
| Website cloning | All 16 attempts blocked: localhost, 127.0.0.1 in decimal, hex and IPv4-mapped IPv6, `[::1]`, cloud metadata (169.254.169.254, `metadata.google.internal`), private ranges, **the stack's own container names** (`postgres`, `gophish`, `django`), `file:`, `ftp:`, `javascript:`, credentials in the URL |
| API keys | Owners see and revoke only their own keys; Security Admin sees all |
| Browser | 27 pages: no overflow, no overlapping buttons, every field labelled, no CSP violations, email/landing previews render in their sandboxed frames |

## Defects found and fixed
| # | Severity | Defect | Fix |
|---|---|---|---|
| A1 | **Critical (approval bypass)** | **Changing an email or landing page after approval kept the approval.** Content is shared by name, and campaigns only reference it. A Campaign Manager could get a campaign approved, then rewrite its email (or the landing page) and launch something the approver never saw. The **read-and-draft API**, which by design can never approve or launch, could do the same by re-posting a template with the same name. | New `campaigns.services.reset_approvals_using()`: every content save (email builder, landing builder, cloned page save and re-fetch, pasted-HTML page, API email/landing endpoints) sends pending and approved campaigns using that content back to Draft, audited as `campaign_approval_reset` with the reason. Launched campaigns are history and untouched. The editor sees "N campaigns went back to Draft". |
| A2 | **Critical (invariant #4)** | **Pasted landing-page HTML (Manage → Paste your own HTML) and the API's `landing-pages` endpoint weren't sanitised.** Only the clone path used the allow-list. A `<script>` could read the password field and send it to an outside server, or a `<form action="https://elsewhere">` could post it there, so real passwords could leave even though Gophish's `capture_passwords=false` is set. | Both paths now run `sanitize_cloned_html()`: no scripts, forms forced to post back to the engine, password inputs kept native so the engine strips them. The API also runs email HTML through the composer's `sanitize_email_html()` and returns the sanitiser's warnings. |
| A3 | Medium | **API-key expiry date:** an impossible date (`2026-02-30`) → HTTP 500; a malformed one (`not-a-date`) silently created a key that **never expires**; a past date created a dead key. | Validated: anything that isn't a future date is refused with a message. |
| A4 | High (least privilege) | **Department Managers could change organisation-wide content**: rewrite or duplicate any email or landing page, clone websites, upload or delete images, edit the template catalog and smart groups. That changes other departments' campaigns and, after A1, sends their approvals back to Draft. | New `org_wide_content` guard: Department Managers can read the library (to pick content for their own campaigns) but any change is a 403. The edit, new, duplicate, upload and remove controls are hidden from them. |
| A5 | **Critical (data integrity)** | **CSV import:** (a) with "Deactivate anyone not in this file" ticked, a **4-row file offboarded 46 of 49 employees**; (b) a row rejected for a missing name still got that person deactivated; (c) `is_exempt=yes` created exemptions with **no reason, no end date and no one recorded as setting them**, bypassing the rule the governance pages enforce; (d) invalid addresses (`not-an-email`) were imported; (e) deactivations were audited only as a count. | (a) The import is refused and fully rolled back if it would deactivate more than `IMPORT_MAX_DEACTIVATE_PERCENT` (default 10%) of active staff. (b) A rejected row still counts as "in the file". (c) A new `exempt_reason` column is required to exempt someone; `exempt_set_by` is recorded; set and ended exemptions are audited per person. (d) Addresses validated. (e) Each deactivation audited per person (`via=import`). New departments created by a file are listed on the result page so typos are visible. **Behaviour change:** a CSV that marks someone exempt without a reason used to be accepted and flagged for follow-up; it's now a row error. |
| A6 | Medium (reported numbers) | **Follow-on from Phase 1 D2:** once the Overview counted current staff only, the dashboard Training page, the admin Assignments page and the governance "Training is being completed" check still counted leavers' open training. With one leaver, the Overview said 60.0% complete and the Training page said 52.9%. | All three now count current staff only; a test checks the Overview, Training page and governance check agree. |

## Observations (not changed)
- **A Security Admin can approve their own submission.** This is documented in the code as deliberate, so a lone admin can run a campaign. See gap AG1.
- **Exemption reasons are visible to every role with `view_employee`**, including Training Manager and Report Viewer. Reasons can be sensitive (e.g. medical leave).
- **The CSV import still auto-creates departments** from any unknown name. It's convenient, but typos create departments. They're now listed on the result page.
- **The course preview page has no `h1`**, and the API-key expiry date input has no `min` (the server now validates).
- **The Django admin (`/admin/`)** was covered by the existing smoke test: every admin page × every role, never a 5xx. Email and landing drafts aren't registered there, so A1 has no side door through it.

## Gaps (awareness / GRC view), not built
| # | Gap | Why it matters | Suggested approach |
|---|---|---|---|
| AG1 | **No enforced four-eyes approval** | ISO 27001 A.5.3 (segregation of duties) and most audit programmes expect the approver to be someone other than the author, for an action that sends simulated attacks to the whole organisation. | A setting (`REQUIRE_SEPARATE_APPROVER`, on by default) that blocks approving your own submission, with a documented break-glass path. |
| AG2 | **The approval doesn't record what was approved** | After A1, content changes reset approval, but the audit entry still says only who and when, not which version. An auditor can't prove what was signed off. | Store a hash (or snapshot) of the compiled email, landing page and audience on approval, and show it in the audit log and evidence package. |
| AG3 | **Approving without looking** | "Approve" is one click on the list row; the previews are only on the edit form. | An approval page showing the rendered email, landing page, audience size and schedule, with Approve and Reject there. |
| AG4 | **No send windows or blackout dates** | Simulations landing during payroll week, holidays or a real incident cause complaints and noise for the SOC. | Allowed send hours, blackout dates and an "incident freeze" switch that blocks launches org-wide. |
| AG5 | **No SOC / helpdesk heads-up** | When a campaign launches, the SOC and helpdesk should know so they don't escalate reports as a real incident. | An optional notification to a SOC address or webhook on launch (the SIEM webhook exists; add a human-readable notice). |
| AG6 | **No MFA for admin accounts** | An admin account can launch simulated attacks org-wide. MFA is out of scope by decision (invariant #7), but it's the largest remaining control gap. | Revisit before any production use (TOTP via django-otp, or SSO). |
| AG7 | **Staff accounts are created only in Django admin** | Joiner and leaver handling for admin accounts lives outside the governed console; the access review covers them, creation doesn't. | Create/disable staff accounts in Users & access, with a role and reason, audited. |
| AG8 | **No retention period set** | The governance page already flags it: offboarded staff's personal data is kept indefinitely. | Set `PII_RETENTION_DAYS` per your data-protection policy (the anonymisation job exists). |
| AG9 | **Real email delivery untested** | No SMTP relay is configured. Sign-in links, reminders, coaching, escalations, scheduled reports and the simulations themselves have only been checked as rendered output. | Deploy the relay (REQUIRES_ATTENTION.md item 1), then send one of each email type to a test mailbox. |
