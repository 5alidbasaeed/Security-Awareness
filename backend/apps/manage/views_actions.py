"""
Extra row actions for the existing management pages (export, pause/resume, delete). Same rules as the
rest of /manage/: a real permission on every action, row scoping from core.scoping, and an audit entry.
"""

import csv

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from apps.core.audit import log_action
from apps.core.csv_safe import csv_safe
from apps.core.scoping import visible_employees
from apps.reporting.models import ReportSchedule
from apps.training.models import TrainingPolicy

from .access import manage_access
from .views import _employee_filters


@manage_access("employees.export_employee_data", methods=("POST",))
def employees_export(request):
    # Naming everyone is the most sensitive export here, so it has its own permission (Security Admin only).
    people = _employee_filters(visible_employees(request.user).select_related("department", "exempt_set_by"), request.POST)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="employees_{timezone.localdate()}.csv"'
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(["full_name", "email", "department", "active", "exempt", "exempt_reason", "exempt_until", "exempt_set_by"])
    count = 0
    for e in people.order_by("full_name"):
        writer.writerow(csv_safe(v) for v in (
            e.full_name, e.email, e.department or "", e.is_active, e.is_exempt, e.exempt_reason,
            e.exempt_until or "", e.exempt_set_by.get_username() if e.exempt_set_by else "",
        ))
        count += 1
    log_action(actor=request.user, action="employees_exported", target_description=f"{count} employees", via="console")
    return response


@manage_access("training.change_trainingpolicy", methods=("POST",))
def policy_toggle(request, pk):
    policy = get_object_or_404(TrainingPolicy, pk=pk)
    policy.is_active = not policy.is_active
    policy.save(update_fields=["is_active"])
    log_action(actor=request.user, action="training_policy_resumed" if policy.is_active else "training_policy_paused",
               target_description=policy.name)
    state = "active" if policy.is_active else "paused: nobody new is enrolled"
    messages.success(request, f"“{policy.name}” is now {state}.")
    return redirect("manage:policies")


@manage_access("reporting.change_reportschedule", methods=("POST",))
def schedule_toggle(request, pk):
    schedule = get_object_or_404(ReportSchedule, pk=pk)
    schedule.is_active = not schedule.is_active
    schedule.save(update_fields=["is_active"])
    log_action(actor=request.user, action="report_schedule_resumed" if schedule.is_active else "report_schedule_paused",
               target_description=schedule.name)
    messages.success(request, f"“{schedule.name}” is now {'active' if schedule.is_active else 'paused'}.")
    return redirect("manage:schedules")


@manage_access("reporting.delete_reportschedule", methods=("POST",))
def schedule_delete(request, pk):
    schedule = get_object_or_404(ReportSchedule, pk=pk)
    name, recipients = schedule.name, ", ".join(schedule.recipient_list())
    schedule.delete()
    log_action(actor=request.user, action="report_schedule_deleted", target_description=name, recipients=recipients)
    messages.success(request, f"Deleted the schedule “{name}”.")
    return redirect("manage:schedules")
