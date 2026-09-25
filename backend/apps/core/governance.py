"""
The governance view of the program: which standards the platform's evidence supports, the policy
settings currently in force, and a live control-health check.

Two rules keep this honest. The mapping to frameworks is *indicative* (the compliance owner
confirms it against their own control library), and nothing here claims a control is "compliant":
the platform produces evidence, and the health checks only say whether that evidence is current.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import dateformat, timezone

DORMANT_ACCOUNT_DAYS = 90
SIMULATION_STALE_DAYS = 90
EXEMPTION_EXPIRY_WARNING_DAYS = 30
STUCK_APPROVAL_DAYS = 7

# (framework, reference, requirement in plain words, where this platform produces the evidence)
FRAMEWORK_CONTROLS = [
    ("ISO/IEC 27001:2022", "A.6.3", "Personnel receive security awareness, education and training, kept up to date.",
     "Training modules, assignments and completion; the evidence package."),
    ("SOC 2 (2017 TSC)", "CC1.4, CC2.2", "Staff are competent and are told their security responsibilities.",
     "Training completion by department; the audit log of who assigned or waived what."),
    ("NIST SP 800-53 r5", "AT-2, AT-2(3), AT-4", "Awareness training incl. social-engineering exercises, with training records kept.",
     "Simulation results per campaign; every assignment with dates and outcome."),
    ("PCI DSS v4.0", "12.6.1 to 12.6.3", "A formal awareness program, reviewed every 12 months, covering phishing and social engineering.",
     "Recurring training policies; simulation coverage; the annual executive summary."),
    ("HIPAA Security Rule", "164.308(a)(5)", "Security awareness and training for the whole workforce.",
     "Workforce training completion and the assignment register."),
    ("NCA ECC-1:2018", "1-10", "A cybersecurity awareness and training program, applied to all staff and measured.",
     "Program targets, training completion, simulation metrics and their history."),
]


def _count(n, singular, plural=None) -> str:
    """'1 campaign' / '3 campaigns', never 'campaign(s)'."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _day(value) -> str:
    """The product's one date style: Sep 15, 2026."""
    return dateformat.format(value, "M j, Y")


def _setting(name, default=None):
    return getattr(settings, name, default)


def policy_settings() -> list[dict]:
    """The settings that define the program's rules, as currently configured. Read-only by design:
    they are changed through the deployment (with its own change control), never from this screen."""
    retention = _setting("PII_RETENTION_DAYS", 0)
    cohort = _setting("MIN_REPORTING_COHORT", 1)
    return [
        {"group": "Simulation guardrails", "name": "Campaign launches allowed per 24 hours", "value": _setting("CAMPAIGN_LAUNCH_RATE_LIMIT"),
         "env": "CAMPAIGN_LAUNCH_RATE_LIMIT", "why": "Caps how many simulations one mistake or one account can send."},
        {"group": "Simulation guardrails", "name": "Approval before launch",
         "value": "Required, by someone other than the submitter" if _setting("REQUIRE_SEPARATE_APPROVER", True)
         else "Required, but the submitter may approve (break-glass)", "env": "REQUIRE_SEPARATE_APPROVER",
         "why": "Segregation of duties: nobody both requests and authorises a simulation. Any change to the email, "
                "landing page, audience or schedule sends it back for approval."},
        {"group": "Training", "name": "Days to complete auto-assigned training", "value": _setting("TRAINING_DUE_DAYS"),
         "env": "TRAINING_DUE_DAYS", "why": "The due date every failure-triggered assignment starts with."},
        {"group": "Training", "name": "Training-portal sign-in link lifetime", "value": f"{_setting('PORTAL_LINK_MAX_AGE_SECONDS', 0) // 3600} hours",
         "env": "PORTAL_LINK_MAX_AGE_SECONDS", "why": "How long an emailed magic link works."},
        {"group": "Program targets", "name": "Maximum failure rate", "value": _pct(_setting("TARGET_MAX_FAILURE_RATE")), "env": "TARGET_MAX_FAILURE_RATE",
         "why": "Share of tested people who click or submit that the program tolerates."},
        {"group": "Program targets", "name": "Minimum report rate", "value": _pct(_setting("TARGET_MIN_REPORT_RATE")), "env": "TARGET_MIN_REPORT_RATE",
         "why": "Share of tested people who should report the email."},
        {"group": "Program targets", "name": "Minimum training completion", "value": _pct(_setting("TARGET_MIN_TRAINING_COMPLETION")),
         "env": "TARGET_MIN_TRAINING_COMPLETION", "why": "Share of assigned training that should be done."},
        {"group": "Program targets", "name": "Minimum coverage", "value": _pct(_setting("TARGET_MIN_COVERAGE")), "env": "TARGET_MIN_COVERAGE",
         "why": "Share of eligible people who should be tested in the period."},
        {"group": "Data protection", "name": "Personal-data retention after offboarding",
         "value": f"{retention} days" if retention else "Kept indefinitely", "env": "PII_RETENTION_DAYS",
         "why": "After this, a deactivated person's name and email are anonymised; the event history stays intact."},
        {"group": "Data protection", "name": "Smallest group reported on", "value": f"{cohort} people" if cohort > 1 else "No minimum",
         "env": "MIN_REPORTING_COHORT", "why": "Groups smaller than this are hidden so results can't identify a person."},
        {"group": "Access", "name": "Admin sign-in", "value": "Password only (no MFA)", "env": None,
         "why": "A recorded, accepted risk. Compensate with a VPN, a short account list and the access review."},
        {"group": "Scoring", "name": "Risk-score algorithm", "value": _algorithm_version(), "env": None,
         "why": "Scores are versioned and recomputable; changing the algorithm never rewrites history."},
    ]


def _pct(value):
    return f"{value}%" if value is not None else "Not set"


def _algorithm_version():
    from apps.risk_scoring.scoring import ALGORITHM_VERSION

    return ALGORITHM_VERSION


def control_health(now=None) -> list[dict]:
    """Live checks over the program's own data. Each is ok / attention / info, with the number behind it."""
    from apps.campaigns.models import Campaign
    from apps.core.models import AuditLogEntry
    from apps.employees.models import Employee
    from apps.reporting.models import GeneratedReport
    from apps.risk_scoring import analytics
    from apps.training.models import TrainingAssignment

    now = now or timezone.now()
    today = timezone.localdate()
    checks = []

    def add(label, status, detail, link=None):
        checks.append({"label": label, "status": status, "detail": detail, "link": link})

    # Simulation cadence
    last = Campaign.objects.filter(status=Campaign.Status.LAUNCHED).order_by("-launched_at").first()
    launched_at = last.launched_at if last else None
    if last is None:
        add("Simulations are being run", "attention", "No simulation has been launched yet.", "manage:campaigns")
    elif launched_at and launched_at < now - timedelta(days=SIMULATION_STALE_DAYS):
        add("Simulations are being run", "attention", f"The last launch was on {_day(launched_at)}, over {SIMULATION_STALE_DAYS} days ago.", "manage:campaigns")
    else:
        add("Simulations are being run", "ok", f"Last launch: {_day(launched_at)}." if launched_at else "A simulation has been launched.", "manage:campaigns")

    stuck = Campaign.objects.filter(status=Campaign.Status.PENDING_APPROVAL, submitted_at__lt=now - timedelta(days=STUCK_APPROVAL_DAYS)).count()
    add("Approvals are not stuck", "attention" if stuck else "ok",
        f"{_count(stuck, 'campaign')} waiting more than {STUCK_APPROVAL_DAYS} days." if stuck else "Nothing has waited more than a week.", "manage:campaigns")
    if _setting("REQUIRE_SEPARATE_APPROVER", True):
        add("Approvals are independent", "ok", "The person who submits a campaign cannot approve it.", "manage:campaigns")
    else:
        add("Approvals are independent", "attention",
            "Break-glass is on: a submitter can approve their own campaign (REQUIRE_SEPARATE_APPROVER=false).", "manage:campaigns")

    # Training
    compliance = analytics.training_compliance(TrainingAssignment.objects.filter(employee__is_active=True))
    target = _setting("TARGET_MIN_TRAINING_COMPLETION")
    rate = compliance["completion_rate_percent"]
    if compliance["assigned"] == 0:
        add("Training is being completed", "info", "Nothing is assigned right now.", "manage:assignments")
    elif target is not None and rate is not None and rate < target:
        add("Training is being completed", "attention", f"{rate}% complete against a {target}% target; {compliance['overdue']} overdue.", "manage:assignments")
    else:
        add("Training is being completed", "ok", f"{rate}% complete; {compliance['overdue']} overdue." if compliance["overdue"] else f"{rate}% complete; none overdue.", "manage:assignments")
    if compliance["waived"]:
        add("Waivers in force", "info", f"{_count(compliance['waived'], 'assignment')} waived; each has a recorded reason and approver.", "manage:assignments")

    # Exemptions
    exempt = Employee.objects.filter(is_exempt=True, is_active=True)
    no_reason = exempt.filter(exempt_reason="").count()
    open_ended = exempt.filter(exempt_until__isnull=True).count()
    expiring = exempt.filter(exempt_until__gte=today, exempt_until__lte=today + timedelta(days=EXEMPTION_EXPIRY_WARNING_DAYS)).count()
    add("Every exemption is justified", "attention" if no_reason else "ok",
        f"{_count(no_reason, 'exempt employee has', 'exempt employees have')} no reason recorded." if no_reason else (f"{_count(exempt.count(), 'exemption')}, all with a reason." if exempt.exists() else "Nobody is exempt."), "manage:exemptions")
    add("Exemptions are time-boxed", "attention" if open_ended else "ok",
        f"{_count(open_ended, 'exemption has', 'exemptions have')} no end date." if open_ended else "Every exemption has an end date.", "manage:exemptions")
    if expiring:
        add("Exemptions ending soon", "info", f"{expiring} end within {EXEMPTION_EXPIRY_WARNING_DAYS} days and will then return to the simulation pool.", "manage:exemptions")

    # Access
    user_model = get_user_model()
    staff = user_model.objects.filter(is_staff=True, is_active=True)
    dormant = staff.filter(Q(last_login__lt=now - timedelta(days=DORMANT_ACCOUNT_DAYS)) | Q(last_login__isnull=True, date_joined__lt=now - timedelta(days=DORMANT_ACCOUNT_DAYS))).count()
    no_role = staff.filter(is_superuser=False, groups__isnull=True).count()
    add("No dormant privileged accounts", "attention" if dormant else "ok",
        f"{_count(dormant, 'staff account')} unused for {DORMANT_ACCOUNT_DAYS}+ days." if dormant else f"All {staff.count()} active staff accounts have signed in recently.", "manage:users")
    review = AuditLogEntry.objects.filter(action="access_review_completed").first()
    if review is None:
        add("Access is reviewed periodically", "attention", "No access review has been recorded yet.", "manage:users")
    elif review.occurred_at < now - timedelta(days=DORMANT_ACCOUNT_DAYS):
        add("Access is reviewed periodically", "attention", f"The last review was on {_day(review.occurred_at)}, over {DORMANT_ACCOUNT_DAYS} days ago.", "manage:users")
    else:
        add("Access is reviewed periodically", "ok", f"Last reviewed on {_day(review.occurred_at)}.", "manage:users")
    if no_role:
        add("Every staff account has a role", "attention", f"{_count(no_role, 'staff account has', 'staff accounts have')} no role.", "manage:users")

    # Data protection and evidence
    retention = _setting("PII_RETENTION_DAYS", 0)
    add("Retention period is set", "ok" if retention else "attention",
        f"Personal data is anonymised {retention} days after offboarding." if retention else "Personal data of offboarded staff is kept indefinitely. Set a retention period (PII_RETENTION_DAYS) in the deployment settings.")
    audit_recent = AuditLogEntry.objects.filter(occurred_at__gte=now - timedelta(days=30)).count()
    add("Administrative actions are logged", "ok" if audit_recent else "info", f"{audit_recent} recorded in the last 30 days.", "manage:audit-log")
    last_report = GeneratedReport.objects.order_by("-generated_at").first()
    add("Evidence is being archived", "ok" if last_report else "info",
        f"Last report generated {_day(last_report.generated_at)}." if last_report else "No report has been generated yet.",
        "reporting:index")
    add("Admin sign-in uses MFA", "attention", "Not enabled: a recorded, accepted risk. Compensate with network restriction and regular access review.")
    return checks


def summarise(checks) -> dict:
    counts = {"ok": 0, "attention": 0, "info": 0}
    for check in checks:
        counts[check["status"]] += 1
    return counts


def user_role_rows():
    """Every staff-capable account with the facts an access review needs."""
    user_model = get_user_model()
    now = timezone.now()
    users = (
        user_model.objects.filter(Q(is_staff=True) | Q(groups__isnull=False)).distinct()
        .prefetch_related("groups", "managed_departments_as_user").order_by("username")
    )
    rows = []
    for user in users:
        reference = user.last_login or user.date_joined
        rows.append({
            "user": user,
            "roles": sorted(g.name for g in user.groups.all()),
            "departments": sorted(d.name for d in user.managed_departments_as_user.all()),
            "dormant": user.is_active and reference < now - timedelta(days=DORMANT_ACCOUNT_DAYS),
        })
    return rows


def count_active_security_admins(exclude_pk=None) -> int:
    user_model = get_user_model()
    qs = user_model.objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(groups__name="Security Admin")).distinct()
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.count()


__all__ = ["FRAMEWORK_CONTROLS", "policy_settings", "control_health", "summarise", "user_role_rows", "count_active_security_admins"]
