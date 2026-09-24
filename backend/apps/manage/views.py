"""
Management UI — create and edit the things that used to require Django admin.
Read-only reporting stays in apps.dashboard; this is the write side.

Two rules hold everywhere here, the same as the admin and the API:
  * every action goes through the existing services (create_draft_campaign,
    launch_campaign) and audit log — there is no second write path; and
  * launching/approving is a human action gated on real permissions. Content
    the API or the builder produced is inert until a person launches it.
"""

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.campaigns.services import CampaignLaunchError, create_draft_campaign, launch_campaign
from apps.core.audit import log_action
from apps.core.scoping import visible_campaigns, visible_departments, visible_employees
from apps.employees.models import Department
from apps.engine.factory import get_client
from apps.training.models import TrainingModule

from .access import manage_access
from .forms import (
    CampaignForm,
    DepartmentForm,
    EmailTemplateForm,
    EmployeeForm,
    LandingPageForm,
    QuizForm,
    TrainingModuleForm,
)

APPROVED_CONTENT_FIELDS = {"name", "template_name", "landing_page_name", "landing_page_url", "target_department", "scheduled_at"}


def _page(request, queryset, per_page=20):
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


# --- hub --------------------------------------------------------------------------------


@manage_access(methods=("GET",))
def index(request):
    can = request.user.has_perm
    pending = visible_campaigns(request.user).filter(status=Campaign.Status.PENDING_APPROVAL).count()
    return render(request, "manage/index.html", {
        "active": "manage",
        "pending_approvals": pending,
        "cards": [
            ("Campaigns", "manage:campaigns", "Build, submit, approve and launch simulations.", can("campaigns.change_campaign")),
            ("Content library", "manage:content", "Email templates and landing pages.", can("campaigns.change_campaign")),
            ("Training", "manage:training", "Modules and quizzes.", can("training.change_trainingmodule")),
            ("Employees", "manage:employees", "People and their departments.", can("employees.change_employee")),
            ("Departments", "manage:departments", "Organizational units.", can("employees.view_department")),
            ("API keys", "manage:api-keys", "Keys for drafting content programmatically.", request.user.is_staff),
        ],
    })


# --- campaigns --------------------------------------------------------------------------


@manage_access("campaigns.view_campaign", methods=("GET",))
def campaigns(request):
    status = request.GET.get("status", "")
    qs = visible_campaigns(request.user).select_related("target_department").order_by("-id")
    if status:
        qs = qs.filter(status=status)
    return render(request, "manage/campaigns.html", {
        "active": "manage", "page": _page(request, qs), "status": status,
        "statuses": Campaign.Status.choices, "can_approve": request.user.has_perm("campaigns.approve_campaign"),
    })


def _campaign_form_page(request, form, *, title, campaign=None):
    return render(request, "manage/campaign_form.html", {
        "active": "manage", "form": form, "title": title, "campaign": campaign,
    })


@manage_access("campaigns.add_campaign")
def campaign_new(request):
    if request.method == "POST":
        form = CampaignForm(request.POST, user=request.user)
        if form.is_valid():
            c = form.cleaned_data
            campaign = create_draft_campaign(
                actor=request.user, name=c["name"], template_name=c["template_name"],
                landing_page_name=c["landing_page_name"], landing_page_url=c["landing_page_url"],
                target_department=c["target_department"], training_module=c["training_module"],
                scheduled_at=c["scheduled_at"],
            )
            messages.success(request, f"Draft campaign “{campaign.name}” created.")
            return redirect("manage:campaigns")
    else:
        form = CampaignForm(user=request.user)
    return _campaign_form_page(request, form, title="New campaign")


@manage_access("campaigns.change_campaign")
def campaign_edit(request, pk):
    campaign = get_object_or_404(visible_campaigns(request.user), pk=pk)
    if campaign.status == Campaign.Status.LAUNCHED:
        messages.error(request, "A launched campaign can't be edited.")
        return redirect("manage:campaigns")
    if request.method == "POST":
        form = CampaignForm(request.POST, instance=campaign, user=request.user)
        if form.is_valid():
            # Editing approved content resets it to Draft — same rule as the admin.
            if campaign.status in (Campaign.Status.PENDING_APPROVAL, Campaign.Status.APPROVED) and (
                APPROVED_CONTENT_FIELDS & set(form.changed_data)
            ):
                form.instance.status = Campaign.Status.DRAFT
                form.instance.submitted_by = form.instance.submitted_at = None
                form.instance.approved_by = form.instance.approved_at = None
                messages.warning(request, "Edited after submission — back to Draft, needs approval again.")
            form.save()
            log_action(actor=request.user, action="campaign_updated", target_description=str(campaign))
            messages.success(request, "Saved.")
            return redirect("manage:campaigns")
    else:
        form = CampaignForm(instance=campaign, user=request.user)
    return _campaign_form_page(request, form, title=f"Edit “{campaign.name}”", campaign=campaign)


@manage_access("campaigns.change_campaign", methods=("POST",))
def campaign_submit(request, pk):
    campaign = get_object_or_404(visible_campaigns(request.user), pk=pk)
    if campaign.status != Campaign.Status.DRAFT:
        messages.error(request, "Only a draft can be submitted.")
    else:
        campaign.status = Campaign.Status.PENDING_APPROVAL
        campaign.submitted_by, campaign.submitted_at = request.user, timezone.now()
        campaign.save()
        log_action(actor=request.user, action="campaign_submitted", target_description=str(campaign))
        messages.success(request, f"“{campaign.name}” submitted for approval.")
    return redirect("manage:campaigns")


@manage_access("campaigns.approve_campaign", methods=("POST",))
def campaign_approve(request, pk):
    campaign = get_object_or_404(visible_campaigns(request.user), pk=pk)
    if campaign.status != Campaign.Status.PENDING_APPROVAL:
        messages.error(request, "Only a pending campaign can be approved.")
    else:
        # Separation of duties is enforced by the approve_campaign permission (Campaign Managers,
        # who submit, don't hold it) — the same control the Django admin uses. We deliberately do
        # not also block a Security Admin from approving their own submission, so a lone admin can
        # still run a campaign; changing that is a plan-doc decision, not a silent one here.
        campaign.status = Campaign.Status.APPROVED
        campaign.approved_by, campaign.approved_at = request.user, timezone.now()
        campaign.save()
        log_action(actor=request.user, action="campaign_approved", target_description=str(campaign))
        messages.success(request, f"“{campaign.name}” approved. It can now be launched.")
    return redirect("manage:campaigns")


@manage_access("campaigns.approve_campaign", methods=("POST",))
def campaign_reject(request, pk):
    campaign = get_object_or_404(visible_campaigns(request.user), pk=pk)
    reason = request.POST.get("reason", "").strip()
    if campaign.status != Campaign.Status.PENDING_APPROVAL:
        messages.error(request, "Only a pending campaign can be rejected.")
    else:
        campaign.status = Campaign.Status.DRAFT
        campaign.submitted_by = campaign.submitted_at = None
        campaign.save()
        log_action(actor=request.user, action="campaign_rejected", target_description=str(campaign), reason=reason)
        messages.success(request, f"“{campaign.name}” sent back to Draft.")
    return redirect("manage:campaigns")


@manage_access("campaigns.change_campaign", methods=("POST",))
def campaign_launch(request, pk):
    campaign = get_object_or_404(visible_campaigns(request.user), pk=pk)
    try:
        launch_campaign(campaign, actor=request.user, client=get_client())
    except CampaignLaunchError as exc:
        messages.error(request, str(exc))
    except Exception as exc:  # noqa: BLE001 — surface any adapter failure, don't swallow it
        messages.error(request, f"Launch failed: {exc}")
    else:
        campaign.refresh_from_db()
        messages.success(request, f"Launched “{campaign.name}”.")
    return redirect("manage:campaigns")


# --- content library --------------------------------------------------------------------


@manage_access("campaigns.change_campaign", methods=("GET",))
def content(request):
    client = get_client()
    error = None
    try:
        templates, pages, profiles = client.list_email_templates(), client.list_landing_pages(), client.list_sending_profiles()
    except Exception as exc:  # noqa: BLE001 — Gophish may be unreachable; show a message, not a 500
        templates, pages, profiles, error = [], [], [], str(exc)
    return render(request, "manage/content.html", {
        "active": "manage", "templates": templates, "pages": pages, "profiles": profiles, "engine_error": error,
    })


@manage_access("campaigns.change_campaign")
def template_new(request):
    if request.method == "POST":
        form = EmailTemplateForm(request.POST)
        if form.is_valid():
            get_client().upsert_email_template(**{k: form.cleaned_data[k] for k in ("name", "subject", "html", "text")})
            log_action(actor=request.user, action="email_template_drafted", target_description=form.cleaned_data["name"])
            messages.success(request, "Email template saved.")
            return redirect("manage:content")
    else:
        form = EmailTemplateForm()
    return render(request, "manage/content_form.html", {"active": "manage", "form": form, "title": "New email template"})


@manage_access("campaigns.change_campaign")
def page_new(request):
    if request.method == "POST":
        form = LandingPageForm(request.POST)
        if form.is_valid():
            get_client().upsert_landing_page(
                name=form.cleaned_data["name"], html=form.cleaned_data["html"],
                capture_credentials=form.cleaned_data["capture_credentials"],
                redirect_url=form.cleaned_data["redirect_url"],
            )
            log_action(actor=request.user, action="landing_page_drafted", target_description=form.cleaned_data["name"])
            messages.success(request, "Landing page saved. Passwords are never captured.")
            return redirect("manage:content")
    else:
        form = LandingPageForm()
    return render(request, "manage/content_form.html", {"active": "manage", "form": form, "title": "New landing page"})


@manage_access("campaigns.change_campaign", methods=("GET",))
def page_preview(request, name):
    try:
        html = get_client().get_landing_page_html(name)
    except Exception as exc:  # noqa: BLE001
        return HttpResponse(f"Preview unavailable: {exc}", status=502, content_type="text/plain")
    response = HttpResponse(html)
    response["Content-Security-Policy"] = "sandbox"  # author-controlled HTML, opaque origin, no scripts/forms
    return response


# --- training ---------------------------------------------------------------------------


@manage_access("training.view_trainingmodule", methods=("GET",))
def training(request):
    modules = TrainingModule.objects.order_by("title")
    return render(request, "manage/training.html", {"active": "manage", "page": _page(request, modules)})


@manage_access("training.change_trainingmodule")
def module_edit(request, pk=None):
    module = get_object_or_404(TrainingModule, pk=pk) if pk else None
    quiz = getattr(module, "quiz", None) if module else None
    if request.method == "POST":
        form = TrainingModuleForm(request.POST, instance=module)
        quiz_form = QuizForm(request.POST, instance=quiz)
        if form.is_valid() and quiz_form.is_valid():
            module = form.save()
            action = "training_module_updated" if pk else "training_module_created"
            if request.POST.get("has_quiz"):
                q = quiz_form.save(commit=False)
                q.module = module
                q.save()
            elif quiz is not None:
                quiz.delete()
            log_action(actor=request.user, action=action, target_description=str(module))
            messages.success(request, "Saved.")
            return redirect("manage:training")
    else:
        form = TrainingModuleForm(instance=module)
        quiz_form = QuizForm(instance=quiz)
    return render(request, "manage/training_form.html", {
        "active": "manage", "form": form, "quiz_form": quiz_form, "module": module, "has_quiz": quiz is not None,
        "questions": quiz.questions.prefetch_related("choices") if quiz else [],
        "title": f"Edit “{module.title}”" if module else "New training module",
    })


# --- employees & departments ------------------------------------------------------------


@manage_access("employees.view_employee", methods=("GET",))
def employees(request):
    q = request.GET.get("q", "").strip()
    qs = visible_employees(request.user).select_related("department").order_by("full_name")
    if q:
        qs = qs.filter(full_name__icontains=q) | qs.filter(email__icontains=q)
    return render(request, "manage/employees.html", {"active": "manage", "page": _page(request, qs), "q": q})


@manage_access("employees.change_employee")
def employee_edit(request, pk=None):
    employee = get_object_or_404(visible_employees(request.user), pk=pk) if pk else None
    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee, user=request.user)
        if form.is_valid():
            employee = form.save()
            log_action(actor=request.user, action="employee_updated" if pk else "employee_created",
                       target_description=str(employee))
            messages.success(request, "Saved.")
            return redirect("manage:employees")
    else:
        form = EmployeeForm(instance=employee, user=request.user)
    return render(request, "manage/simple_form.html", {
        "active": "manage", "form": form, "title": f"Edit {employee}" if employee else "New employee",
        "back": reverse("manage:employees"),
    })


@manage_access("employees.view_department", methods=("GET",))
def departments(request):
    return render(request, "manage/departments.html", {
        "active": "manage", "departments": visible_departments(request.user).order_by("name"),
        "can_edit": request.user.has_perm("employees.change_department"),
    })


@manage_access("employees.change_department")
def department_edit(request, pk=None):
    department = get_object_or_404(Department, pk=pk) if pk else None
    if request.method == "POST":
        form = DepartmentForm(request.POST, instance=department)
        if form.is_valid():
            department = form.save()
            log_action(actor=request.user, action="department_updated" if pk else "department_created",
                       target_description=str(department))
            messages.success(request, "Saved.")
            return redirect("manage:departments")
    else:
        form = DepartmentForm(instance=department)
    return render(request, "manage/simple_form.html", {
        "active": "manage", "form": form, "title": f"Edit {department}" if department else "New department",
        "back": reverse("manage:departments"),
    })


# --- employee CSV import ----------------------------------------------------------------


@manage_access("employees.add_employee", methods=("GET", "POST"))
def employee_import(request):
    from apps.employees.imports import import_employees, parse_csv

    result = None
    if request.method == "POST" and request.FILES.get("file"):
        try:
            text = request.FILES["file"].read().decode("utf-8-sig")
        except UnicodeDecodeError:
            messages.error(request, "That file isn't UTF-8 text. Export the CSV as UTF-8 and try again.")
            return redirect("manage:employee-import")
        rows, errors = parse_csv(text)
        if errors:
            messages.error(request, errors[0][1])
        else:
            result = import_employees(rows, deactivate_missing=bool(request.POST.get("deactivate_missing")))
            log_action(actor=request.user, action="employees_imported",
                       target_description=f"{result.created} created, {result.updated} updated, {result.deactivated} deactivated")
            if result.ok:
                messages.success(request, f"Imported: {result.created} new, {result.updated} updated, "
                                          f"{result.deactivated} deactivated.")
            else:
                messages.warning(request, f"Imported with {len(result.errors)} row error(s) — see below.")
    return render(request, "manage/employee_import.html", {"active": "manage", "result": result})


# --- quiz questions ---------------------------------------------------------------------


@manage_access("training.change_trainingmodule", methods=("POST",))
def question_add(request, pk):
    from apps.training.models import Quiz, QuizChoice, QuizQuestion

    module = get_object_or_404(TrainingModule, pk=pk)
    text = (request.POST.get("text") or "").strip()
    choices = [(request.POST.get(f"choice{i}") or "").strip() for i in range(1, 5)]
    correct = request.POST.get("correct")
    filled = [(i, c) for i, c in enumerate(choices, start=1) if c]
    if not text or len(filled) < 2:
        messages.error(request, "A question needs text and at least two answers.")
    elif not correct or not choices[int(correct) - 1]:
        messages.error(request, "Mark which answer is correct.")
    else:
        quiz, _ = Quiz.objects.get_or_create(module=module)
        question = QuizQuestion.objects.create(quiz=quiz, text=text, order=quiz.questions.count())
        for i, choice in filled:
            QuizChoice.objects.create(question=question, text=choice[:300], is_correct=(str(i) == correct))
        log_action(actor=request.user, action="quiz_question_added", target_description=f"{module}: {text[:80]}")
        messages.success(request, "Question added.")
    return redirect("manage:module-edit", pk=module.pk)


@manage_access("training.change_trainingmodule", methods=("POST",))
def question_delete(request, pk, question_pk):
    from apps.training.models import QuizQuestion

    question = get_object_or_404(QuizQuestion, pk=question_pk, quiz__module_id=pk)
    log_action(actor=request.user, action="quiz_question_deleted", target_description=question.text[:80])
    question.delete()
    messages.success(request, "Question removed.")
    return redirect("manage:module-edit", pk=pk)
