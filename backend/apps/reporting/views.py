from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.audit import log_action
from apps.dashboard.access import dashboard_access

from .forms import GenerateReportForm
from .models import GeneratedReport
from .services import ReportError, allowed_kinds, generate_report, visible_reports


def _forbidden(request):
    return render(request, "dashboard/403.html", status=403)


@dashboard_access(methods=("GET", "POST"))
def reports(request):
    if not allowed_kinds(request.user):
        return _forbidden(request)

    if request.method == "POST":
        form = GenerateReportForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                report = generate_report(
                    kind=form.cleaned_data["kind"],
                    user=request.user,
                    period_start=form.cleaned_data["period_start"],
                    period_end=form.cleaned_data["period_end"],
                )
            except ReportError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"Generated {report.filename}.")
                return redirect(reverse("reporting:index"))
    else:
        form = GenerateReportForm(user=request.user)

    page = Paginator(visible_reports(request.user), 15).get_page(request.GET.get("page"))
    return render(
        request,
        "reporting/reports.html",
        {"active": "reports", "form": form, "page": page, "kinds": form.specs},
    )


@dashboard_access
def download(request, report_id):
    report = get_object_or_404(visible_reports(request.user), pk=report_id)
    report = GeneratedReport.objects.get(pk=report.pk)  # the listing queryset defers the file bytes
    if not any(spec.key == report.kind for spec in allowed_kinds(request.user)):
        return _forbidden(request)

    log_action(
        actor=request.user,
        action="report_downloaded",
        target_description=report.filename,
        report_id=report.pk,
        sha256=report.sha256,
    )
    response = HttpResponse(bytes(report.content), content_type=report.content_type)
    response["Content-Disposition"] = f'attachment; filename="{report.filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Report-SHA256"] = report.sha256
    return response
