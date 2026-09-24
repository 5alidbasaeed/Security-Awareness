"""
Management UI — create and edit the things that used to require Django admin.
Read-only reporting stays in apps.dashboard; this is the write side.

Two rules hold everywhere here, the same as the admin and the API:
  * every action goes through the existing services (create_draft_campaign,
    launch_campaign) and audit log — there is no second write path; and
  * launching/approving is a human action gated on real permissions. Content
    the API or the builder produced is inert until a person launches it.
"""

import base64
import re

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.clickjacking import xframe_options_sameorigin

from apps.campaigns.images import CONTENT_TYPES, STORED_NAME, ImageRejected, image_root, save_upload
from apps.campaigns.models import Campaign, EmailDraft, EmailImage, LandingDraft
from apps.campaigns.services import CampaignLaunchError, create_draft_campaign, launch_campaign
from apps.core.audit import log_action
from apps.core.scoping import visible_campaigns, visible_departments, visible_employees
from apps.employees.models import Department
from apps.engine.factory import get_client
from apps.training.models import TrainingModule, TrainingSlide

from .access import manage_access
from .forms import (
    CampaignForm,
    DepartmentForm,
    EmailDraftForm,
    EmployeeForm,
    LandingDraftForm,
    LandingPageForm,
    MailSettingsForm,
    QuizForm,
    SlideForm,
    TestEmailForm,
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
            ("Reported emails", "manage:reported", "Triage suspicious emails employees reported.", can("intake.view_reportedemail")),
            ("Scheduled reports", "manage:schedules", "Email reports to leadership on a schedule.", can("reporting.view_reportschedule")),
            ("Mail settings", "manage:mail-settings", "The SMTP relay simulation emails are sent through.", can("campaigns.approve_campaign")),
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
                actor=request.user, name=c["name"], template_name=c["email"], landing_page_name=c["landing_page"],
                landing_page_url=settings.PHISH_SERVER_URL, training_module=c["training_module"],
                scheduled_at=c["scheduled_at"],
            )
            form.apply_to(campaign)  # audience (department, smart group or everyone) and copy addresses
            campaign.save()
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
    before = (campaign.name, campaign.template_name, campaign.landing_page_name,
              campaign.target_department_id, campaign.target_smart_group_id, campaign.scheduled_at, campaign.copy_to)
    if request.method == "POST":
        form = CampaignForm(request.POST, instance=campaign, user=request.user)
        if form.is_valid():
            saved = form.save(commit=False)
            form.apply_to(saved)
            after = (saved.name, saved.template_name, saved.landing_page_name,
                     saved.target_department_id, saved.target_smart_group_id, saved.scheduled_at, saved.copy_to)
            # Editing approved content resets it to Draft: same rule as the admin.
            if saved.status in (Campaign.Status.PENDING_APPROVAL, Campaign.Status.APPROVED) and before != after:
                saved.status = Campaign.Status.DRAFT
                saved.submitted_by = saved.submitted_at = None
                saved.approved_by = saved.approved_at = None
                messages.warning(request, "Edited after submission: back to Draft, needs approval again.")
            saved.save()
            log_action(actor=request.user, action="campaign_updated", target_description=str(saved))
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
        engine_templates, pages, profiles = client.list_email_templates(), client.list_landing_pages(), client.list_sending_profiles()
    except Exception as exc:  # noqa: BLE001 — Gophish may be unreachable; show a message, not a 500
        engine_templates, pages, profiles, error = [], [], [], str(exc)
    drafts = list(EmailDraft.objects.all())
    built = {d.name for d in drafts}
    landing_drafts = list(LandingDraft.objects.all())
    built_pages = {d.name for d in landing_drafts}
    return render(request, "manage/content.html", {
        "active": "manage", "drafts": drafts, "engine_only": [t for t in engine_templates if t["name"] not in built],
        "landing_drafts": landing_drafts, "pages_engine_only": [p for p in pages if p["name"] not in built_pages],
        "profiles": profiles, "engine_error": error, "image_count": EmailImage.objects.count(),
    })


def _save_with_uploads(request, form):
    """form.save() plus any files chosen in the form's UPLOADS pairs. Returns None (with the
    error attached to the form) if an upload is rejected, so nothing half-saved is left behind."""
    saved = form.save(commit=False)
    for upload_field, image_field in form.UPLOADS:
        uploaded = form.cleaned_data.get(upload_field)
        if uploaded:
            try:
                setattr(saved, image_field, save_upload(uploaded, request.user))
            except ImageRejected as exc:
                form.add_error(upload_field, str(exc))
                return None
    saved.save()
    return saved


def _editor_html(form) -> str:
    """The composer's starting content. Anything echoed back after a failed save is re-sanitised first,
    so the page never renders raw submitted markup."""
    from apps.campaigns.richtext import sanitize_email_html, to_editor_html

    value = form["body_html"].value() or ""
    return to_editor_html(sanitize_email_html(value) if form.is_bound else value)


@manage_access("campaigns.change_campaign")
def template_edit(request, pk=None):
    """The email builder. Saving compiles the fields to email HTML and pushes it to the engine,
    then returns here so the preview beside the form shows exactly what was saved."""
    draft = get_object_or_404(EmailDraft, pk=pk) if pk else None
    if request.method == "POST":
        form = EmailDraftForm(request.POST, request.FILES, instance=draft)
        saved = _save_with_uploads(request, form) if form.is_valid() else None
        if saved is not None:
            try:
                get_client().upsert_email_template(
                    name=saved.name, subject=saved.subject, html=saved.render_html(), text=saved.render_text(),
                )
            except Exception as exc:  # noqa: BLE001 — the draft is kept; only the engine push failed
                messages.error(request, f"Saved here, but the phishing engine could not be updated: {exc}")
            else:
                log_action(actor=request.user, action="email_template_updated" if pk else "email_template_drafted",
                           target_description=saved.name)
                messages.success(request, "Email saved.")
            return redirect("manage:template-edit", pk=saved.pk)
    else:
        form = EmailDraftForm(instance=draft)
    return render(request, "manage/email_form.html", {
        "active": "manage", "form": form, "draft": draft,
        "title": f"Edit “{draft.name}”" if draft else "New phishing email",
        "preview_url": reverse("manage:email-preview", args=[draft.name]) if draft else None,
        "editor_html": _editor_html(form),
        "images": EmailImage.objects.all(),
    })


@manage_access("campaigns.change_campaign", methods=("POST",))
def image_upload_json(request):
    """Used by the email composer's image button: upload one picture, get back its id and address."""
    uploaded = request.FILES.get("file")
    if uploaded is None:
        return JsonResponse({"error": "Choose an image."}, status=400)
    try:
        image = save_upload(uploaded, request.user)
    except ImageRejected as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    log_action(actor=request.user, action="email_image_uploaded", target_description=image.original_name)
    return JsonResponse({"id": image.pk, "name": image.original_name, "url": reverse("manage:image-file", args=[image.pk])})


@manage_access("campaigns.change_campaign")
def landing_edit(request, pk=None):
    """The landing-page builder. Saving compiles the page and pushes it to the engine (passwords
    are never captured; the adapter forces that off), then returns here with the preview."""
    draft = get_object_or_404(LandingDraft, pk=pk) if pk else None
    if request.method == "POST":
        form = LandingDraftForm(request.POST, request.FILES, instance=draft)
        saved = _save_with_uploads(request, form) if form.is_valid() else None
        if saved is not None:
            try:
                get_client().upsert_landing_page(
                    name=saved.name, html=saved.render_html(), capture_credentials=True,
                    redirect_url=saved.engine_redirect_url(),
                )
            except Exception as exc:  # noqa: BLE001 — the draft is kept; only the engine push failed
                messages.error(request, f"Saved here, but the phishing engine could not be updated: {exc}")
            else:
                log_action(actor=request.user, action="landing_page_updated" if pk else "landing_page_drafted",
                           target_description=saved.name)
                messages.success(request, "Landing page saved. Passwords are never captured.")
            return redirect("manage:landing-edit", pk=saved.pk)
    else:
        form = LandingDraftForm(instance=draft)
    return render(request, "manage/template_form.html", {
        "active": "manage", "form": form, "draft": draft,
        "title": f"Edit “{draft.name}”" if draft else "New landing page",
        "preview_url": reverse("manage:page-preview", args=[draft.name]) if draft else None,
        "subtitle": "This is what someone sees after clicking. The form is a real form, but passwords are stripped "
                    "and never stored; only that a submission happened is recorded.",
    })


# --- image library ----------------------------------------------------------------------


@manage_access("campaigns.change_campaign")
def images(request):
    if request.method == "POST":
        uploaded = request.FILES.getlist("files")
        if not uploaded:
            messages.error(request, "Choose at least one image.")
        for item in uploaded:
            try:
                image = save_upload(item, request.user)
            except ImageRejected as exc:
                messages.error(request, f"{item.name}: {exc}")
            else:
                log_action(actor=request.user, action="email_image_uploaded", target_description=image.original_name)
                messages.success(request, f"Uploaded {image.original_name}.")
        return redirect("manage:images")
    library = list(EmailImage.objects.all())
    return render(request, "manage/images.html", {
        "active": "manage", "images": library, "used": {i.pk for i in library if _image_in_use(i)},
    })


@manage_access("campaigns.change_campaign", methods=("GET",))
def image_file(request, pk):
    """Staff-only copy of an uploaded image, so the library can show thumbnails on this origin."""
    image = get_object_or_404(EmailImage, pk=pk)
    if not STORED_NAME.match(image.stored_name):
        raise Http404
    try:
        data = (image_root() / image.stored_name).read_bytes()
    except OSError:
        raise Http404 from None
    return HttpResponse(data, content_type=CONTENT_TYPES[image.stored_name.rsplit(".", 1)[1]])


def _image_in_use(image) -> bool:
    """Referenced by a builder logo/banner, or placed inside an email's message. Removing it would
    break emails that were already sent, whose pictures load from the public address."""
    return (
        EmailDraft.objects.filter(logo_image=image).exists() or EmailDraft.objects.filter(hero_image=image).exists()
        or EmailDraft.objects.filter(body_html__contains=image.stored_name).exists()
        or LandingDraft.objects.filter(logo_image=image).exists()
    )


@manage_access("campaigns.change_campaign", methods=("POST",))
def image_delete(request, pk):
    image = get_object_or_404(EmailImage, pk=pk)
    if _image_in_use(image):
        messages.error(request, "That image is used by an email or landing page. Replace it there first.")
        return redirect("manage:images")
    if STORED_NAME.match(image.stored_name):
        (image_root() / image.stored_name).unlink(missing_ok=True)
    log_action(actor=request.user, action="email_image_deleted", target_description=image.original_name)
    image.delete()
    messages.success(request, "Image removed.")
    return redirect("manage:images")


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


@xframe_options_sameorigin  # shown in an iframe beside the builder form
@manage_access("campaigns.change_campaign", methods=("GET",))
def page_preview(request, name):
    try:
        html = get_client().get_landing_page_html(name)
    except Exception as exc:  # noqa: BLE001
        return HttpResponse(f"Preview unavailable: {exc}", status=502, content_type="text/plain")
    response = HttpResponse(_inline_images(html))
    response["Content-Security-Policy"] = "sandbox"  # author-controlled HTML, opaque origin, no scripts/forms
    return response


# --- training ---------------------------------------------------------------------------


@manage_access("training.view_trainingmodule", methods=("GET",))
def training(request):
    modules = TrainingModule.objects.annotate(slide_count=Count("slides", distinct=True)).order_by("title")
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
        "slides": module.slides.all() if module else [],
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


# --- reported-email triage --------------------------------------------------------------


@manage_access("intake.view_reportedemail", methods=("GET",))
def reported(request):
    from apps.intake.models import ReportedEmail

    verdict = request.GET.get("verdict", "new")
    qs = ReportedEmail.objects.select_related("reporter", "reporter__department", "triaged_by")
    qs = qs.filter(reporter__in=visible_employees(request.user))
    if verdict:
        qs = qs.filter(verdict=verdict)
    return render(request, "manage/reported.html", {
        "active": "manage", "page": _page(request, qs), "verdict": verdict,
        "verdicts": ReportedEmail.Verdict.choices,
        "can_triage": request.user.has_perm("intake.change_reportedemail"),
    })


@manage_access("intake.change_reportedemail", methods=("POST",))
def reported_triage(request, pk):
    from apps.intake.models import ReportedEmail

    report = get_object_or_404(ReportedEmail.objects.filter(reporter__in=visible_employees(request.user)), pk=pk)
    verdict = request.POST.get("verdict")
    if verdict not in ReportedEmail.Verdict.values or verdict == ReportedEmail.Verdict.NEW:
        messages.error(request, "Choose a verdict.")
    else:
        report.verdict = verdict
        report.triage_notes = (request.POST.get("triage_notes") or "").strip()
        report.triaged_by, report.triaged_at = request.user, timezone.now()
        report.save()
        log_action(actor=request.user, action="reported_email_triaged", target_description=str(report), verdict=verdict)
        messages.success(request, f"Marked as {report.get_verdict_display().lower()}.")
    return redirect(f"{reverse('manage:reported')}?verdict=new")


# --- training policies ------------------------------------------------------------------


@manage_access("training.view_trainingpolicy", methods=("GET",))
def policies(request):
    from apps.training.models import TrainingPolicy

    return render(request, "manage/policies.html", {
        "active": "manage", "policies": TrainingPolicy.objects.select_related("module", "department").order_by("name"),
    })


@manage_access("training.change_trainingpolicy")
def policy_edit(request, pk=None):
    from apps.training.models import TrainingPolicy

    from .forms import TrainingPolicyForm

    policy = get_object_or_404(TrainingPolicy, pk=pk) if pk else None
    if request.method == "POST":
        form = TrainingPolicyForm(request.POST, instance=policy)
        if form.is_valid():
            policy = form.save()
            log_action(actor=request.user, action="training_policy_updated" if pk else "training_policy_created",
                       target_description=str(policy))
            messages.success(request, "Saved. People in scope are enrolled by the next daily run.")
            return redirect("manage:policies")
    else:
        form = TrainingPolicyForm(instance=policy)
    return render(request, "manage/simple_form.html", {
        "active": "manage", "form": form, "title": f"Edit {policy}" if policy else "New training policy",
        "back": reverse("manage:policies"),
    })


# --- scheduled reports ------------------------------------------------------------------


@manage_access("reporting.view_reportschedule", methods=("GET",))
def schedules(request):
    from apps.reporting.models import ReportSchedule

    return render(request, "manage/schedules.html", {"active": "manage", "schedules": ReportSchedule.objects.order_by("name")})


@manage_access("reporting.change_reportschedule")
def schedule_edit(request, pk=None):
    from apps.reporting.models import ReportSchedule

    from .forms import ReportScheduleForm

    schedule = get_object_or_404(ReportSchedule, pk=pk) if pk else None
    if request.method == "POST":
        form = ReportScheduleForm(request.POST, instance=schedule)
        if form.is_valid():
            schedule = form.save(commit=False)
            if schedule.created_by_id is None:
                schedule.created_by = request.user
            schedule.save()
            log_action(actor=request.user, action="report_schedule_saved", target_description=str(schedule))
            messages.success(request, "Saved.")
            return redirect("manage:schedules")
    else:
        form = ReportScheduleForm(instance=schedule)
    return render(request, "manage/simple_form.html", {
        "active": "manage", "form": form, "title": f"Edit {schedule}" if schedule else "New scheduled report",
        "back": reverse("manage:schedules"),
    })


# --- template catalog & smart groups ----------------------------------------------------


@manage_access("campaigns.view_campaigntemplate", methods=("GET",))
def catalog(request):
    from apps.campaigns.models import CampaignTemplate, SmartGroup

    return render(request, "manage/catalog.html", {
        "active": "manage",
        "templates": CampaignTemplate.objects.all(),
        "smart_groups": SmartGroup.objects.select_related("department").all(),
        "can_edit": request.user.has_perm("campaigns.change_campaigntemplate"),
    })


@manage_access("campaigns.change_campaigntemplate")
def catalog_template_edit(request, pk=None):
    from apps.campaigns.models import CampaignTemplate

    obj = get_object_or_404(CampaignTemplate, pk=pk) if pk else None
    from django import forms as djf

    Form = djf.modelform_factory(CampaignTemplate, fields=[
        "name", "category", "difficulty", "template_name", "landing_page_name", "landing_page_url", "is_active"])
    if request.method == "POST":
        form = Form(request.POST, instance=obj)
        if form.is_valid():
            saved = form.save()
            log_action(actor=request.user, action="campaign_template_saved", target_description=str(saved))
            messages.success(request, "Saved.")
            return redirect("manage:catalog")
    else:
        form = Form(instance=obj)
    _apply_field_classes(form)
    return render(request, "manage/simple_form.html", {"active": "manage", "form": form,
                  "title": f"Edit {obj}" if obj else "New catalog template", "back": reverse("manage:catalog")})


@manage_access("campaigns.change_smartgroup")
def smart_group_edit(request, pk=None):
    from apps.campaigns.models import SmartGroup

    obj = get_object_or_404(SmartGroup, pk=pk) if pk else None
    from django import forms as djf

    Form = djf.modelform_factory(SmartGroup, fields=["name", "rule", "department", "new_hire_days"])
    if request.method == "POST":
        form = Form(request.POST, instance=obj)
        if form.is_valid():
            saved = form.save()
            log_action(actor=request.user, action="smart_group_saved", target_description=str(saved))
            messages.success(request, "Saved.")
            return redirect("manage:catalog")
    else:
        form = Form(instance=obj)
    _apply_field_classes(form)
    return render(request, "manage/simple_form.html", {"active": "manage", "form": form,
                  "title": f"Edit {obj}" if obj else "New smart group", "back": reverse("manage:catalog")})


def _apply_field_classes(form):
    from django import forms as djf

    for field in form.fields.values():
        widget = field.widget
        widget.attrs.setdefault("class", "select" if isinstance(widget, djf.Select) else "field")


# --- deliverability preflight -----------------------------------------------------------


@manage_access("campaigns.change_campaign", methods=("GET", "POST"))
def deliverability(request):
    from apps.engagement.deliverability import check_domain

    result = None
    if request.method == "POST":
        domain = (request.POST.get("domain") or "").strip()
        selector = (request.POST.get("dkim_selector") or "").strip() or None
        if domain:
            try:
                result = check_domain(domain, dkim_selector=selector)
            except Exception as exc:  # noqa: BLE001
                messages.error(request, f"Could not check that domain: {exc}")
    return render(request, "manage/deliverability.html", {"active": "manage", "result": result})


# --- course slides ----------------------------------------------------------------------


@manage_access("training.change_trainingmodule")
def slide_edit(request, pk, slide_pk=None):
    module = get_object_or_404(TrainingModule, pk=pk)
    slide = get_object_or_404(module.slides, pk=slide_pk) if slide_pk else None
    if request.method == "POST":
        form = SlideForm(request.POST, instance=slide)
        if form.is_valid():
            saved = form.save(commit=False)
            if slide is None:
                saved.module = module
                saved.order = module.slides.count()
            saved.save()
            log_action(actor=request.user, action="training_slide_saved", target_description=str(saved)[:200])
            messages.success(request, "Slide saved.")
            return redirect("manage:module-edit", pk=module.pk)
    else:
        form = SlideForm(instance=slide)
    return render(request, "manage/simple_form.html", {
        "active": "manage", "form": form, "back": reverse("manage:module-edit", args=[module.pk]),
        "title": f"Edit slide: {slide.title}" if slide else f"New slide for “{module.title}”",
    })


def _renumber(module_pk):
    for order, slide in enumerate(TrainingSlide.objects.filter(module_id=module_pk)):
        if slide.order != order:
            slide.order = order
            slide.save(update_fields=["order"])


@manage_access("training.change_trainingmodule", methods=("POST",))
def slide_delete(request, pk, slide_pk):
    slide = get_object_or_404(TrainingSlide, pk=slide_pk, module_id=pk)
    log_action(actor=request.user, action="training_slide_deleted", target_description=str(slide)[:200])
    slide.delete()
    _renumber(pk)
    messages.success(request, "Slide removed.")
    return redirect("manage:module-edit", pk=pk)


@manage_access("training.change_trainingmodule", methods=("POST",))
def slide_move(request, pk, slide_pk, direction):
    if direction not in ("up", "down"):
        raise Http404
    slides = list(TrainingSlide.objects.filter(module_id=pk))
    index = next((i for i, item in enumerate(slides) if item.pk == slide_pk), None)
    if index is None:
        raise Http404
    swap = index - 1 if direction == "up" else index + 1
    if 0 <= swap < len(slides):
        slides[index], slides[swap] = slides[swap], slides[index]
        for order, item in enumerate(slides):
            if item.order != order:
                item.order = order
                item.save(update_fields=["order"])
    return redirect("manage:module-edit", pk=pk)


@manage_access("training.view_trainingmodule", methods=("GET",))
def module_preview(request, pk):
    """Staff preview of the course exactly as an employee sees it. Nothing is recorded."""
    module = get_object_or_404(TrainingModule, pk=pk)
    slides = list(module.slides.all())
    if not slides:
        messages.error(request, "This module has no slides yet.")
        return redirect("manage:training")
    raw = request.GET.get("s", "1")
    number = min(max(int(raw) if raw.isdigit() else 1, 1), len(slides))
    quiz = getattr(module, "quiz", None)
    return render(request, "manage/module_preview.html", {
        "active": "manage", "module": module, "slide": slides[number - 1], "number": number, "total": len(slides),
        "prev": number - 1 if number > 1 else None, "next": number + 1 if number < len(slides) else None,
        "has_quiz": quiz is not None and quiz.questions.exists(), "percent": round(number / len(slides) * 100),
        "back_url": reverse("manage:training"), "quiz_url": reverse("manage:module-preview-quiz", args=[pk]),
        "course_url": reverse("manage:module-preview", args=[pk]), "finish_url": reverse("manage:training"),
    })


@manage_access("training.view_trainingmodule")
def module_preview_quiz(request, pk):
    """Try the quiz as an employee would. Scores it and shows the answers; saves nothing."""
    from apps.training.scoring import score_quiz

    module = get_object_or_404(TrainingModule, pk=pk)
    quiz = getattr(module, "quiz", None)
    if quiz is None or not quiz.questions.exists():
        messages.error(request, "This module has no quiz questions yet.")
        return redirect("manage:training")
    questions = list(quiz.questions.prefetch_related("choices"))
    result = None
    if request.method == "POST":
        answers = {}
        for question in questions:
            raw = request.POST.get(f"q{question.pk}")
            if raw and raw.isdigit():
                answers[question.pk] = int(raw)
        score, passed = score_quiz(quiz, answers)
        review = []
        for question in questions:
            chosen = answers.get(question.pk)
            review.append({
                "text": question.text,
                "correct": any(c.pk == chosen and c.is_correct for c in question.choices.all()),
                "right_answer": ", ".join(c.text for c in question.choices.all() if c.is_correct),
            })
        result = {"score": score, "passed": passed, "review": review}
    return render(request, "manage/module_preview_quiz.html", {
        "active": "manage", "module": module, "quiz": quiz, "questions": questions, "result": result,
        "has_slides": module.slides.exists(),
    })


def _inline_images(html: str) -> str:
    """For previews only: swap the public image addresses for the files themselves, so the preview
    shows the pictures even when the public sending domain isn't reachable from the staff browser."""
    base = re.escape(settings.EMAIL_IMAGE_BASE_URL.rstrip("/") + "/")

    def replace(match):
        name = match.group(1)
        try:
            data = (image_root() / name).read_bytes()
        except OSError:
            return match.group(0)
        return f"data:{CONTENT_TYPES[name.rsplit('.', 1)[1]]};base64,{base64.b64encode(data).decode()}"

    return re.sub(base + r"([0-9a-f]{32}\.(?:png|jpg|gif))", replace, html)


@xframe_options_sameorigin  # shown in an iframe beside the builder form
@manage_access("campaigns.change_campaign", methods=("GET",))
def email_preview(request, name):
    """Sandboxed preview of a phishing email template, with sample values for Gophish's merge fields."""
    try:
        template = get_client().get_email_template(name)
    except Exception as exc:  # noqa: BLE001
        return HttpResponse(f"Preview unavailable: {exc}", status=502, content_type="text/plain")
    if template is None:
        raise Http404
    html = template["html"] or "<pre>" + escape(template["text"]) + "</pre>"
    for token, sample in (("{{.FirstName}}", "Alex"), ("{{.LastName}}", "Morgan"), ("{{.Email}}", "alex.morgan@example.com"),
                          ("{{.URL}}", "#"), ("{{.Tracker}}", ""), ("{{.TrackingURL}}", "#")):
        html = html.replace(token, sample)
    banner = (
        '<div style="font:14px system-ui;padding:12px 16px;border-bottom:1px solid #ddd;background:#f6f6f6">'
        f"<strong>Subject:</strong> {escape(template['subject'])}<br>"
        '<span style="color:#666">Preview only. Nothing was sent; links are disabled.</span></div>'
    )
    response = HttpResponse(_inline_images(banner + html))
    response["Content-Security-Policy"] = "sandbox"  # author-controlled HTML: opaque origin, no scripts
    return response


# --- mail settings ----------------------------------------------------------------------


@manage_access("campaigns.approve_campaign")  # Security Admin only: this decides where every simulation email is relayed
def mail_settings(request):
    profile_name = settings.GOPHISH_DEFAULT_SEND_PROFILE
    client = get_client()
    try:
        current = client.get_sending_profile(profile_name)
        engine_error = None
    except Exception as exc:  # noqa: BLE001 — engine unreachable: show it, don't 500
        current, engine_error = None, str(exc)
    test_form = TestEmailForm()

    if request.method == "POST" and request.POST.get("action") == "test":
        test_form = TestEmailForm(request.POST)
        if test_form.is_valid():
            to = test_form.cleaned_data["to"]
            try:
                client.send_test_email(profile_name=profile_name, to_email=to)
            except Exception as exc:  # noqa: BLE001 — surface the relay's own message
                messages.error(request, f"The test message failed: {exc}")
            else:
                log_action(actor=request.user, action="mail_test_sent", target_description=to)
                messages.success(request, f"Test message sent to {to}. Check that it arrived.")
            return redirect("manage:mail-settings")
        form = _mail_form(current)
    elif request.method == "POST":
        form = MailSettingsForm(request.POST)
        if form.is_valid():
            c = form.cleaned_data
            try:
                client.upsert_sending_profile(
                    name=profile_name, host=c["host"], port=c["port"], username=c["username"], password=c["password"],
                    from_address=c["from_address"], ignore_cert_errors=c["allow_self_signed"],
                )
            except Exception as exc:  # noqa: BLE001
                messages.error(request, f"Could not save to the phishing engine: {exc}")
            else:
                # Never log the password; only which settings changed.
                log_action(actor=request.user, action="mail_settings_updated", target_description=f"{c['host']}:{c['port']}",
                           username=c["username"], password_changed=bool(c["password"]), allow_self_signed=c["allow_self_signed"])
                messages.success(request, "Mail settings saved. Send a test message to check them.")
                return redirect("manage:mail-settings")
    else:
        form = _mail_form(current)
    return render(request, "manage/mail_settings.html", {
        "active": "manage", "form": form, "test_form": test_form, "current": current, "profile_name": profile_name,
        "engine_error": engine_error,
    })


def _mail_form(current):
    if not current:
        return MailSettingsForm()
    return MailSettingsForm(initial={
        "host": current["host"], "port": current["port"], "username": current["username"],
        "from_address": current["from_address"], "allow_self_signed": current["ignore_cert_errors"],
    })
