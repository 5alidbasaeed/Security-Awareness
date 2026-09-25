"""
Governance pages of the admin console: audit log, access review, exemption register, training
assignment control and the program/framework overview.

Same rules as the rest of /manage/: every page is gated on a real permission, row-scoping comes from
core.scoping, and every change is written to the audit log with the reason the person gave. Nothing
here edits the immutable event log or the risk scores.
"""

import csv
from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import dateformat, timezone

from apps.core import governance
from apps.core.audit import log_action
from apps.core.csv_safe import csv_safe
from apps.core.models import AuditLogEntry
from apps.core.scoping import managed_departments, visible_assignments, visible_departments, visible_employees
from apps.risk_scoring import analytics
from apps.training.models import TrainingAssignment, TrainingExtension, TrainingModule

from .access import manage_access
from .forms_governance import ExtendForm, ManualAssignForm, UserAccessForm, WaiveForm

EXPORT_ROW_CAP = 50_000


def _day(value) -> str:
    """The product's one date style: Sep 15, 2026."""
    return dateformat.format(value, "M j, Y")


def _page(request, queryset, per_page=25):
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


# --- audit log --------------------------------------------------------------------------


def _audit_queryset(params):
    qs = AuditLogEntry.objects.select_related("actor")
    if action := params.get("action", "").strip():
        qs = qs.filter(action=action)
    if actor := params.get("actor", "").strip():
        qs = qs.filter(actor__username=actor) if actor != "system" else qs.filter(actor__isnull=True)
    if q := params.get("q", "").strip():
        qs = qs.filter(Q(target_description__icontains=q) | Q(action__icontains=q) | Q(actor__username__icontains=q))
    for key, lookup in (("from", "occurred_at__date__gte"), ("to", "occurred_at__date__lte")):
        value = params.get(key, "").strip()
        if value:
            try:
                qs = qs.filter(**{lookup: datetime.strptime(value, "%Y-%m-%d").date()})
            except ValueError:
                pass  # a mistyped date just isn't applied
    return qs


@manage_access("core.view_auditlogentry", methods=("GET",))
def audit_log(request):
    users = get_user_model().objects.filter(audit_log_entries__isnull=False).distinct().order_by("username")
    return render(request, "manage/audit_log.html", {
        "active": "manage", "page": _page(request, _audit_queryset(request.GET), 50),
        "actions": AuditLogEntry.objects.order_by().values_list("action", flat=True).distinct().order_by("action"),
        "actors": users.values_list("username", flat=True),
        "f": {k: request.GET.get(k, "") for k in ("q", "action", "actor", "from", "to")},
        "can_export": request.user.has_perm("core.change_auditlogentry"),
        "query": request.GET.urlencode(),
    })


@manage_access("core.change_auditlogentry", methods=("POST",))
def audit_log_export(request):
    # Same bar as the Django-admin export: the audit trail is itself sensitive, so Security Admin only.
    entries = list(_audit_queryset(request.POST).order_by("-occurred_at")[:EXPORT_ROW_CAP])
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="audit_log.csv"'
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(["occurred_at", "actor", "action", "target", "details"])
    for e in entries:
        writer.writerow(csv_safe(v) for v in (
            e.occurred_at.isoformat(), e.actor.get_username() if e.actor else "system", e.action, e.target_description,
            "; ".join(f"{k}={v}" for k, v in (e.metadata or {}).items()),
        ))
    log_action(actor=request.user, action="audit_log_exported", target_description=f"{len(entries)} entries", via="console")
    return response


# --- users and access -------------------------------------------------------------------


def _last_access_review():
    return AuditLogEntry.objects.filter(action="access_review_completed").select_related("actor").first()


@manage_access("core.manage_user_access", methods=("GET",))
def users(request):
    rows = governance.user_role_rows()
    last = _last_access_review()
    overdue = last is None or last.occurred_at < timezone.now() - timedelta(days=governance.DORMANT_ACCOUNT_DAYS)
    return render(request, "manage/users.html", {
        "active": "manage", "rows": rows, "last_review": last, "review_overdue": overdue,
        "review_days": governance.DORMANT_ACCOUNT_DAYS,
        "dormant_count": sum(1 for r in rows if r["dormant"]),
    })


@manage_access("core.manage_user_access")
def user_edit(request, pk):
    target = get_object_or_404(get_user_model(), pk=pk)
    if target.pk == request.user.pk:
        messages.error(request, "You can't change your own access. Ask another Security Admin.")
        return redirect("manage:users")
    if target.is_superuser:
        messages.error(request, "Superuser accounts are managed in Django admin, not here.")
        return redirect("manage:users")

    form = UserAccessForm(request.POST or None, user=target)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        roles_after = sorted(g.name for g in data["roles"])
        before = {
            "active": target.is_active, "staff": target.is_staff,
            "roles": sorted(g.name for g in target.groups.filter(name__in=form.fields["roles"].queryset.values_list("name", flat=True))),
            "departments": sorted(d.name for d in target.managed_departments_as_user.all()),
        }
        after = {
            "active": data["is_active"], "staff": data["is_staff"], "roles": roles_after,
            "departments": sorted(d.name for d in data["departments"]),
        }
        was_admin = before["active"] and "Security Admin" in before["roles"]
        stays_admin = after["active"] and "Security Admin" in after["roles"]
        if was_admin and not stays_admin and governance.count_active_security_admins(exclude_pk=target.pk) == 0:
            form.add_error(None, "That would leave no active Security Admin. Make someone else one first.")
        elif before == after:
            messages.info(request, "Nothing changed.")
            return redirect("manage:users")
        else:
            with transaction.atomic():
                target.is_active, target.is_staff = data["is_active"], data["is_staff"]
                target.save(update_fields=["is_active", "is_staff"])
                # Only the five console roles are managed here; any other group the account has is left alone.
                others = list(target.groups.exclude(name__in=form.fields["roles"].queryset.values_list("name", flat=True)))
                target.groups.set(others + list(data["roles"]))
                target.managed_departments_as_user.set(data["departments"])
                log_action(actor=request.user, action="user_access_changed", target_description=target.get_username(),
                           before=before, after=after, reason=data["reason"])
            messages.success(request, f"Updated access for {target.get_username()}.")
            return redirect("manage:users")

    return render(request, "manage/user_form.html", {
        "active": "manage", "form": form, "target": target, "back": "manage:users",
    })


@manage_access("core.manage_user_access", methods=("POST",))
def access_review_export(request):
    rows = governance.user_role_rows()
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="access_review_{timezone.localdate()}.csv"'
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(["username", "full_name", "email", "active", "console_access", "superuser", "roles", "departments",
                     "last_login", "date_joined", "dormant"])
    for r in rows:
        u = r["user"]
        writer.writerow(csv_safe(v) for v in (
            u.get_username(), u.get_full_name(), u.email, u.is_active, u.is_staff, u.is_superuser, "; ".join(r["roles"]),
            "; ".join(r["departments"]), u.last_login.isoformat() if u.last_login else "never", u.date_joined.date().isoformat(),
            r["dormant"],
        ))
    log_action(actor=request.user, action="access_review_exported", target_description=f"{len(rows)} accounts")
    return response


@manage_access("core.manage_user_access", methods=("POST",))
def access_review_complete(request):
    rows = governance.user_role_rows()
    note = (request.POST.get("note") or "").strip()[:300]
    log_action(actor=request.user, action="access_review_completed", target_description=f"{len(rows)} accounts reviewed",
               accounts=len(rows), dormant=sum(1 for r in rows if r["dormant"]), note=note)
    messages.success(request, "Access review recorded. It is due again in 90 days.")
    return redirect("manage:users")


# --- exemption register -----------------------------------------------------------------


@manage_access("employees.view_employee", methods=("GET",))
def exemptions(request):
    today = timezone.localdate()
    show = request.GET.get("show", "")
    qs = visible_employees(request.user).filter(is_exempt=True, is_active=True).select_related("department", "exempt_set_by")
    if show == "attention":
        qs = qs.filter(Q(exempt_reason="") | Q(exempt_until__isnull=True))
    elif show == "ending":
        qs = qs.filter(exempt_until__gte=today, exempt_until__lte=today + timedelta(days=governance.EXEMPTION_EXPIRY_WARNING_DAYS))
    qs = qs.order_by(F("exempt_until").asc(nulls_last=True), "full_name")
    page = _page(request, qs)
    for e in page:
        e.no_reason = not e.exempt_reason
        e.open_ended = e.exempt_until is None
        e.days_left = (e.exempt_until - today).days if e.exempt_until else None
    scoped = managed_departments(request.user) is not None
    recent = [] if scoped else AuditLogEntry.objects.filter(
        action__in=["exemption_lapsed", "exemption_ended"], occurred_at__gte=timezone.now() - timedelta(days=90),
    )[:10]
    return render(request, "manage/exemptions.html", {
        "active": "manage", "page": page, "show": show, "recent": recent,
        "can_end": request.user.has_perm("employees.change_employee") and not scoped,
        "total": visible_employees(request.user).filter(is_exempt=True, is_active=True).count(),
    })


@manage_access("employees.change_employee", methods=("POST",))
def exemption_end(request, pk):
    if managed_departments(request.user) is not None:
        # Same rule as the employee form: exemptions are outside a Department Manager's remit.
        return render(request, "dashboard/403.html", status=403)
    employee = get_object_or_404(visible_employees(request.user), pk=pk, is_exempt=True)
    reason, until = employee.exempt_reason, employee.exempt_until
    employee.is_exempt = False  # save() clears the reason, date and owner
    employee.save()
    log_action(actor=request.user, action="exemption_ended", target_description=str(employee),
               previous_reason=reason, exempt_until=str(until) if until else "")
    messages.success(request, f"{employee.full_name} is no longer exempt and will be included in future simulations.")
    return redirect("manage:exemptions")


# --- training assignments ---------------------------------------------------------------


@manage_access("training.view_trainingassignment", methods=("GET",))
def assignments(request):
    now = timezone.now()
    status = request.GET.get("status", "outstanding")
    q = request.GET.get("q", "").strip()
    module = request.GET.get("module", "").strip()
    base = visible_assignments(request.user).filter(employee__is_active=True)  # leavers owe nothing (dashboard rule)
    qs = base.select_related("employee", "employee__department", "module")
    if status == "outstanding":
        qs = qs.filter(completed_at__isnull=True, waived_at__isnull=True)
    elif status == "overdue":
        qs = qs.filter(completed_at__isnull=True, waived_at__isnull=True, due_at__lt=now)
    elif status == "completed":
        qs = qs.filter(completed_at__isnull=False)
    elif status == "waived":
        qs = qs.filter(waived_at__isnull=False)
    if q:
        qs = qs.filter(Q(employee__full_name__icontains=q) | Q(employee__email__icontains=q))
    if module.isdigit():
        qs = qs.filter(module_id=int(module))
    return render(request, "manage/assignments.html", {
        "active": "manage", "page": _page(request, qs.prefetch_related("extensions").order_by("due_at", "-assigned_at")),
        "status": status, "q": q, "module": module, "now": now,
        "statuses": [("outstanding", "Outstanding"), ("overdue", "Overdue"), ("completed", "Completed"), ("waived", "Waived"), ("all", "All")],
        "modules": TrainingModule.objects.order_by("title"),
        "compliance": analytics.training_compliance(base),
        "can_change": request.user.has_perm("training.change_trainingassignment"),
        "can_add": request.user.has_perm("training.add_trainingassignment"),
    })


def _open_assignment_or_redirect(request, pk):
    assignment = get_object_or_404(visible_assignments(request.user).select_related("employee", "module"), pk=pk)
    if assignment.completed_at or assignment.waived_at:
        messages.error(request, "That assignment is already completed or waived.")
        return assignment, redirect("manage:assignments")
    return assignment, None


@manage_access("training.change_trainingassignment")
def assignment_extend(request, pk):
    assignment, bounce = _open_assignment_or_redirect(request, pk)
    if bounce:
        return bounce
    form = ExtendForm(request.POST or None, assignment=assignment)
    if request.method == "POST" and form.is_valid():
        due = timezone.make_aware(datetime.combine(form.cleaned_data["new_due_date"], time(23, 59)))
        with transaction.atomic():
            TrainingExtension.objects.create(
                assignment=assignment, previous_due_at=assignment.due_at, new_due_at=due,
                reason=form.cleaned_data["reason"], extended_by=request.user,
            )
            previous = assignment.due_at
            assignment.due_at = due
            assignment.save(update_fields=["due_at"])
            log_action(actor=request.user, action="training_due_date_extended", target_description=str(assignment),
                       previous_due=str(previous.date()) if previous else "", new_due=str(due.date()), reason=form.cleaned_data["reason"])
        messages.success(request, f"Due date for {assignment.employee.full_name} moved to {_day(due)}.")
        return redirect("manage:assignments")
    return render(request, "manage/assignment_action.html", {
        "active": "manage", "form": form, "assignment": assignment, "verb": "Extend the due date",
        "intro": "Give this person more time. The original date and your reason are kept.", "submit": "Extend",
    })


@manage_access("training.change_trainingassignment")
def assignment_waive(request, pk):
    assignment, bounce = _open_assignment_or_redirect(request, pk)
    if bounce:
        return bounce
    form = WaiveForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        assignment.waived_at, assignment.waived_by, assignment.waiver_reason = timezone.now(), request.user, form.cleaned_data["reason"]
        assignment.save(update_fields=["waived_at", "waived_by", "waiver_reason"])
        log_action(actor=request.user, action="training_waived", target_description=str(assignment), reason=form.cleaned_data["reason"])
        messages.success(request, f"Waived {assignment.module.title} for {assignment.employee.full_name}.")
        return redirect("manage:assignments")
    return render(request, "manage/assignment_action.html", {
        "active": "manage", "form": form, "assignment": assignment, "verb": "Waive this assignment",
        "intro": "Record a documented exception (for example long leave, or the role no longer needs it). "
                 "It stops counting as owed. It is never counted as completed, and this can't be undone.",
        "submit": "Waive",
    })


@manage_access("training.add_trainingassignment")
def assignment_new(request):
    initial = {"module": request.GET.get("module")} if request.GET.get("module", "").isdigit() else None
    form = ManualAssignForm(request.POST or None, initial=initial, departments=visible_departments(request.user))
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        people = visible_employees(request.user).filter(is_active=True)
        people = people.filter(department=data["department"]) if data["department"] else people.filter(email__iexact=data["email"].strip())
        module, created, skipped = data["module"], 0, 0
        if not people.exists():
            form.add_error(None, "Nobody matched. Check the email, or that the department has active people.")
        else:
            due = timezone.now() + timedelta(days=data["due_days"])
            busy = set(TrainingAssignment.objects.filter(
                module=module, completed_at__isnull=True, waived_at__isnull=True).values_list("employee_id", flat=True))
            with transaction.atomic():
                for person in people:
                    if person.pk in busy:
                        skipped += 1
                        continue
                    TrainingAssignment.objects.create(employee=person, module=module, due_at=due)
                    created += 1
                log_action(actor=request.user, action="training_assigned_manually", target_description=module.title,
                           created=created, skipped_already_open=skipped,
                           scope=data["department"].name if data["department"] else data["email"])
            messages.success(request, f"Assigned {module.title} to {created} {'person' if created == 1 else 'people'}"
                                      + (f" ({skipped} already had it open)." if skipped else "."))
            return redirect("manage:assignments")
    return render(request, "manage/simple_form.html", {
        "active": "manage", "form": form, "title": "Assign training", "back": reverse("manage:assignments"),
    })


# --- governance overview ----------------------------------------------------------------


@manage_access("core.view_auditlogentry", methods=("GET",))
def governance_overview(request):
    checks = governance.control_health()
    groups = {}
    for setting in governance.policy_settings():
        groups.setdefault(setting["group"], []).append(setting)
    return render(request, "manage/governance.html", {
        "active": "manage", "checks": checks, "summary": governance.summarise(checks),
        "frameworks": governance.FRAMEWORK_CONTROLS, "policy_groups": groups,
        "can_reports": request.user.has_perm("reporting.generate_report"),
    })
