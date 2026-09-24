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

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.forms import modelform_factory
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.clickjacking import xframe_options_sameorigin

from apps.campaigns.audience import campaign_audience
from apps.campaigns.email_checks import check_email
from apps.campaigns.images import CONTENT_TYPES, STORED_NAME, ImageRejected, image_root, save_upload
from apps.campaigns.models import Campaign, CampaignTemplate, EmailDraft, EmailImage, LandingDraft, SmartGroup
from apps.campaigns.richtext import sanitize_email_html, to_editor_html
from apps.campaigns.services import CampaignLaunchError, create_draft_campaign, launch_campaign
from apps.core.audit import log_action
from apps.core.scoping import visible_campaigns, visible_departments, visible_employees
from apps.employees.imports import import_employees, parse_csv
from apps.employees.models import Department
from apps.engagement.deliverability import check_domain
from apps.engine.factory import get_client
from apps.intake.models import ReportedEmail
from apps.reporting.models import ReportSchedule
from apps.training.models import Quiz, QuizChoice, QuizQuestion, TrainingModule, TrainingPolicy, TrainingSlide
from apps.training.scoring import score_quiz

from .access import manage_access
from .forms import (
    CampaignForm,
    ClonedLandingForm,
    CloneLandingForm,
    DepartmentForm,
    EmailDraftForm,
    EmployeeForm,
    LandingDraftForm,
    LandingPageForm,
    MailSettingsForm,
    QuizForm,
    ReportScheduleForm,
    SlideForm,
    TestEmailForm,
    TrainingModuleForm,
    TrainingPolicyForm,
)

APPROVED_CONTENT_FIELDS = {"name", "template_name", "landing_page_name", "landing_page_url", "target_department", "scheduled_at"}


def _page(request, queryset, per_page=20):
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


def _simple_edit(request, form_class, instance, *, action, back, title, success="Saved.", form_kwargs=None,
                 stamp_creator=False, style=False):
    """Shared create/edit flow for the plain one-form pages: validate, save, audit, redirect to `back`.

    `action` is either one audit action, or a (created, updated) pair chosen by whether `instance` exists.
    """
    kwargs = form_kwargs or {}
    if request.method == "POST":
        form = form_class(request.POST, instance=instance, **kwargs)
        if form.is_valid():
            obj = form.save(commit=not stamp_creator)
            if stamp_creator:
                if obj.created_by_id is None:
                    obj.created_by = request.user
                obj.save()
            audit_action = action if isinstance(action, str) else action[instance is not None]
            log_action(actor=request.user, action=audit_action, target_description=str(obj))
            messages.success(request, success)
            return redirect(back)
    else:
        form = form_class(instance=instance, **kwargs)
    if style:
        _apply_field_classes(form)
    return render(request, "manage/simple_form.html", {
        "active": "manage", "form": form, "title": title(instance), "back": reverse(back),
    })


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
            ("Program & controls", "manage:governance", "Control health, the standards this supports, and the policy settings in force.", can("core.view_auditlogentry")),
            ("Training assignments", "manage:assignments", "Who owes what. Extend a due date or record a waiver, with a reason.", can("training.view_trainingassignment")),
            ("Exemptions", "manage:exemptions", "Who is excluded from simulations, why, and until when.", can("employees.view_employee")),
            ("Users & access", "manage:users", "Roles, dormant accounts and the periodic access review.", can("core.manage_user_access")),
            ("Audit log", "manage:audit-log", "Every launch, approval, change and export, with who and when.", can("core.view_auditlogentry")),
        ],
    })


# --- campaigns --------------------------------------------------------------------------


@manage_access("campaigns.view_campaign", methods=("GET",))
def campaigns(request):
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    department = request.GET.get("department", "").strip()
    qs = visible_campaigns(request.user).select_related("target_department", "submitted_by", "approved_by").order_by("-id")
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(name__icontains=q)
    if department.isdigit():
        qs = qs.filter(target_department_id=int(department))
    page = _page(request, qs)
    for c in page:
        # The approver needs to know how many people this will reach before saying yes.
        c.recipient_count = None
        if c.status != Campaign.Status.LAUNCHED:
            try:
                c.recipient_count = campaign_audience(c)[0].count()
            except ValueError:
                pass
    return render(request, "manage/campaigns.html", {
        "active": "manage", "page": page, "status": status, "q": q, "department": department,
        "departments": visible_departments(request.user).order_by("name"),
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


def _has_recipients(campaign) -> bool:
    try:
        return campaign_audience(campaign)[0].exists()
    except ValueError:
        return False


@manage_access("campaigns.change_campaign", methods=("POST",))
def campaign_submit(request, pk):
    campaign = get_object_or_404(visible_campaigns(request.user), pk=pk)
    if campaign.status != Campaign.Status.DRAFT:
        messages.error(request, "Only a draft can be submitted.")
    elif not _has_recipients(campaign):
        messages.error(request, "Choose an audience with at least one eligible person (not exempt, not inactive) before submitting.")
    else:
        campaign.status = Campaign.Status.PENDING_APPROVAL
        campaign.submitted_by, campaign.submitted_at = request.user, timezone.now()
        campaign.save()
        log_action(actor=request.user, action="campaign_submitted", target_description=str(campaign))
        messages.success(request, f"“{campaign.name}” submitted for approval.")
    return redirect("manage:campaigns")


@manage_access("campaigns.add_campaign", methods=("POST",))
def campaign_duplicate(request, pk):
    source = get_object_or_404(visible_campaigns(request.user), pk=pk)
    copy = Campaign.objects.create(
        name=f"Copy of {source.name}"[:200], template_name=source.template_name,
        landing_page_name=source.landing_page_name, landing_page_url=source.landing_page_url,
        target_department=source.target_department, target_smart_group=source.target_smart_group,
        copy_to=source.copy_to, training_module=source.training_module, created_by=request.user,
    )
    log_action(actor=request.user, action="campaign_duplicated", target_description=f"{source} -> {copy}")
    messages.success(request, f"Created a draft copy: “{copy.name}”.")
    return redirect("manage:campaign-edit", pk=copy.pk)


@manage_access("campaigns.delete_campaign", methods=("POST",))
def campaign_delete(request, pk):
    campaign = get_object_or_404(visible_campaigns(request.user), pk=pk)
    if campaign.status == Campaign.Status.LAUNCHED or campaign.gophish_campaign_id:
        messages.error(request, "A launched campaign is part of the permanent record and can't be deleted.")
    else:
        description = str(campaign)
        campaign.delete()
        log_action(actor=request.user, action="campaign_deleted", target_description=description)
        messages.success(request, f"Deleted “{description}”.")
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
    elif not reason:
        messages.error(request, "Say why you are sending it back, so the author knows what to fix.")
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

    value = form["body_html"].value() or ""
    return to_editor_html(sanitize_email_html(value) if form.is_bound else value)


@manage_access("campaigns.change_campaign")
def template_edit(request, pk=None):
    """The email builder. Saving compiles the fields to email HTML and pushes it to the engine,
    then returns here so the preview beside the form shows exactly what was saved."""
    draft = get_object_or_404(EmailDraft, pk=pk) if pk else None
    if request.method == "POST" and request.POST.get("action") == "test" and draft is not None:
        return _send_email_test(request, draft)
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
        "checks": check_email(subject=draft.subject, html=draft.render_html(), text=draft.render_text()) if draft else [],
        "test_form": TestEmailForm(), "test_default": request.user.email,
    })


def _send_email_test(request, draft):
    """
    Send ONE copy of this email to the person testing it, so what a real inbox does with it (spam
    folder, image blocking, clipped preview) can be seen before it goes anywhere near an audience.

    Recipients are limited to the sender's own address or an approved company domain — a test send
    still goes through the real relay and looks like a real phishing email, so it can't be aimed at
    anyone else — and capped per hour, so this page can't become a second way to spam people.
    """
    form = TestEmailForm(request.POST)
    back = redirect("manage:template-edit", pk=draft.pk)
    if not form.is_valid():
        messages.error(request, "Enter a valid email address to send the test to.")
        return back
    to = form.cleaned_data["to"]
    own = (request.user.email or "").lower()
    domain = to.rsplit("@", 1)[-1].lower()
    allowed_domains = {d.lower() for d in settings.TEST_EMAIL_ALLOWED_DOMAINS}
    if to.lower() != own and domain not in allowed_domains:
        messages.error(request, "Test messages can only go to your own address"
                       + ("" if own else " (add an email address to your account first)")
                       + " or to an approved company domain.")
        return back
    key = f"manage:test-email:{request.user.pk}"
    sent = cache.get(key, 0) + 1
    cache.set(key, sent, 3600)
    if sent > settings.TEST_EMAIL_LIMIT_PER_HOUR:
        messages.error(request, "That's enough test messages for this hour. Try again later.")
        return back
    try:
        get_client().send_test_email(
            profile_name=settings.GOPHISH_DEFAULT_SEND_PROFILE, to_email=to,
            template={"subject": draft.subject, "html": draft.render_html(), "text": draft.render_text()},
            url=settings.PORTAL_BASE_URL.rstrip("/") + reverse("portal:learn"),
        )
    except Exception as exc:  # noqa: BLE001 — the relay's own message is what helps here
        messages.error(request, f"The test message failed: {exc}")
        return back
    log_action(actor=request.user, action="email_test_sent", target_description=f"{draft.name} -> {to}")
    messages.success(request, f"Test sent to {to}. Its links go to the training page, not a real simulation.")
    return back


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
    if draft is not None and draft.layout == LandingDraft.Layout.CLONED:
        return _cloned_landing_edit(request, draft)
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


def _cloned_landing_edit(request, draft):
    """A cloned page's HTML isn't built from fields, so there's nothing to edit but where people go
    afterwards. "Fetch it again" re-runs the same clone (fresh SSRF check, fresh sanitising)."""
    if request.method == "POST" and request.POST.get("action") == "reclone":
        try:
            _clone_into(request, draft, draft.source_url)
        except Exception as exc:  # noqa: BLE001 — the engine's or clone.py's own message is what helps
            messages.error(request, f"Could not fetch the page again: {exc}")
        return redirect("manage:landing-edit", pk=draft.pk)
    if request.method == "POST":
        form = ClonedLandingForm(request.POST, instance=draft)
        if form.is_valid():
            saved = form.save()
            try:
                get_client().upsert_landing_page(
                    name=saved.name, html=saved.custom_html, capture_credentials=True,
                    redirect_url=saved.engine_redirect_url(),
                )
            except Exception as exc:  # noqa: BLE001
                messages.error(request, f"Saved here, but the phishing engine could not be updated: {exc}")
            else:
                log_action(actor=request.user, action="landing_page_updated", target_description=saved.name)
                messages.success(request, "Saved.")
            return redirect("manage:landing-edit", pk=saved.pk)
    else:
        form = ClonedLandingForm(instance=draft)
    return render(request, "manage/template_form.html", {
        "active": "manage", "form": form, "draft": draft, "title": f"Edit “{draft.name}”",
        "preview_url": reverse("manage:page-preview", args=[draft.name]), "cloned_from": draft.source_url,
        "subtitle": "A copy of a real page. It can't be edited here; passwords are stripped and never stored.",
    })


@manage_access("campaigns.change_campaign")
def landing_clone(request):
    """Copy a public website's sign-in page into a landing-page draft. The engine fetches it (never
    this server — Django has no route to the internet), the HTML is rebuilt from an allow-list
    (campaigns.clone), and the page is saved the same way as any other builder page."""
    if request.method == "POST":
        form = CloneLandingForm(request.POST)
        if form.is_valid():
            try:
                draft = _clone_into(request, LandingDraft(name=form.cleaned_data["name"]), form.cleaned_data["url"])
            except Exception as exc:  # noqa: BLE001 — the engine's or clone.py's own message is what helps
                form.add_error("url", str(exc))
            else:
                return redirect("manage:landing-edit", pk=draft.pk)
    else:
        form = CloneLandingForm()
    return render(request, "manage/simple_form.html", {
        "active": "manage", "form": form, "title": "Clone a login page", "back": reverse("manage:content"),
    })


def _clone_into(request, draft, url):
    """Fetch, sanitise and push. Raises without saving anything if any step fails. Re-checks the
    address itself (not just trusting the form that led here) — a stored URL could have started
    resolving somewhere internal since it was first cloned, and "fetch it again" calls this directly."""
    from apps.campaigns.clone import sanitize_cloned_html, validate_clone_url

    url = validate_clone_url(url)
    html, warnings = sanitize_cloned_html(get_client().import_site(url), url)
    draft.layout, draft.source_url, draft.custom_html = LandingDraft.Layout.CLONED, url, html
    get_client().upsert_landing_page(name=draft.name, html=html, capture_credentials=True, redirect_url=draft.engine_redirect_url())
    draft.save()
    log_action(actor=request.user, action="landing_page_cloned", target_description=f"{draft.name} <- {url}"[:500])
    for warning in warnings:
        messages.warning(request, warning)
    messages.success(request, "Page cloned. Check the preview before using it. Passwords are never captured.")
    return draft


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
    modules = TrainingModule.objects.annotate(
        slide_count=Count("slides", distinct=True),
        assigned_total=Count("assignments", filter=Q(assignments__waived_at__isnull=True), distinct=True),
        completed_total=Count(
            "assignments", filter=Q(assignments__completed_at__isnull=False, assignments__waived_at__isnull=True), distinct=True),
    ).order_by("title")
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


def _employee_filters(qs, params):
    """The employees page's filters, shared with its CSV export so the file is exactly what is on screen."""
    if q := params.get("q", "").strip():
        qs = qs.filter(Q(full_name__icontains=q) | Q(email__icontains=q))
    if (department := params.get("department", "").strip()).isdigit():
        qs = qs.filter(department_id=int(department))
    state = params.get("state", "").strip()
    if state == "active":
        qs = qs.filter(is_active=True)
    elif state == "inactive":
        qs = qs.filter(is_active=False)
    elif state == "exempt":
        qs = qs.filter(is_exempt=True, is_active=True)
    elif state == "no_department":
        qs = qs.filter(department__isnull=True)
    return qs


@manage_access("employees.view_employee", methods=("GET",))
def employees(request):
    q = request.GET.get("q", "").strip()
    department = request.GET.get("department", "").strip()
    state = request.GET.get("state", "").strip()
    qs = _employee_filters(visible_employees(request.user).select_related("department"), request.GET).order_by("full_name")
    return render(request, "manage/employees.html", {
        "active": "manage", "page": _page(request, qs), "q": q, "department": department, "state": state,
        "can_export": request.user.has_perm("employees.export_employee_data"),
        "departments": visible_departments(request.user).order_by("name"),
    })


@manage_access("employees.change_employee", methods=("POST",))
def employee_toggle_active(request, pk):
    employee = get_object_or_404(visible_employees(request.user), pk=pk)
    employee.is_active = not employee.is_active
    employee.save(update_fields=["is_active"])
    log_action(actor=request.user, action="employee_reactivated" if employee.is_active else "employee_deactivated",
               target_description=str(employee))
    messages.success(request, f"{employee.full_name} is now {'active' if employee.is_active else 'inactive and will not receive simulations'}.")
    return redirect("manage:employees")


@manage_access("employees.change_employee")
def employee_edit(request, pk=None):
    employee = get_object_or_404(visible_employees(request.user), pk=pk) if pk else None
    return _simple_edit(
        request, EmployeeForm, employee, form_kwargs={"user": request.user}, back="manage:employees",
        action=("employee_created", "employee_updated"),
        title=lambda e: f"Edit {e}" if e else "New employee",
    )


@manage_access("employees.view_department", methods=("GET",))
def departments(request):
    return render(request, "manage/departments.html", {
        "active": "manage", "departments": visible_departments(request.user).select_related("manager").annotate(
            employee_total=Count("employees", distinct=True),
            exempt_total=Count("employees", filter=Q(employees__is_exempt=True), distinct=True),
        ).order_by("name"),
        "can_edit": request.user.has_perm("employees.change_department"),
    })


@manage_access("employees.change_department")
def department_edit(request, pk=None):
    department = get_object_or_404(Department, pk=pk) if pk else None
    return _simple_edit(
        request, DepartmentForm, department, back="manage:departments",
        action=("department_created", "department_updated"),
        title=lambda d: f"Edit {d}" if d else "New department",
    )


@manage_access("employees.delete_department", methods=("POST",))
def department_delete(request, pk):
    department = get_object_or_404(Department, pk=pk)
    if department.employees.exists():
        messages.error(request, f"{department.name} still has employees. Move them first.")
    else:
        name = department.name
        department.delete()
        log_action(actor=request.user, action="department_deleted", target_description=name)
        messages.success(request, f"Deleted department “{name}”.")
    return redirect("manage:departments")


# --- employee CSV import ----------------------------------------------------------------


@manage_access("employees.add_employee", methods=("GET", "POST"))
def employee_import(request):

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

    question = get_object_or_404(QuizQuestion, pk=question_pk, quiz__module_id=pk)
    log_action(actor=request.user, action="quiz_question_deleted", target_description=question.text[:80])
    question.delete()
    messages.success(request, "Question removed.")
    return redirect("manage:module-edit", pk=pk)


# --- reported-email triage --------------------------------------------------------------


@manage_access("intake.view_reportedemail", methods=("GET",))
def reported(request):

    verdict = request.GET.get("verdict", "new")
    qs = ReportedEmail.objects.select_related("reporter", "reporter__department", "triaged_by")
    qs = qs.filter(reporter__in=visible_employees(request.user))
    if verdict:
        qs = qs.filter(verdict=verdict)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(subject__icontains=q) | Q(sender__icontains=q) | Q(reporter__full_name__icontains=q))
    return render(request, "manage/reported.html", {
        "active": "manage", "page": _page(request, qs), "verdict": verdict, "q": q,
        "verdicts": ReportedEmail.Verdict.choices,
        "can_triage": request.user.has_perm("intake.change_reportedemail"),
    })


@manage_access("intake.change_reportedemail", methods=("POST",))
def reported_triage(request, pk):

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
    return render(request, "manage/policies.html", {
        "active": "manage", "policies": TrainingPolicy.objects.select_related("module", "department").order_by("name"),
    })


@manage_access("training.change_trainingpolicy")
def policy_edit(request, pk=None):
    policy = get_object_or_404(TrainingPolicy, pk=pk) if pk else None
    return _simple_edit(
        request, TrainingPolicyForm, policy, back="manage:policies",
        action=("training_policy_created", "training_policy_updated"),
        success="Saved. People in scope are enrolled by the next daily run.",
        title=lambda p: f"Edit {p}" if p else "New training policy",
    )


# --- scheduled reports ------------------------------------------------------------------


@manage_access("reporting.view_reportschedule", methods=("GET",))
def schedules(request):
    return render(request, "manage/schedules.html", {"active": "manage", "schedules": ReportSchedule.objects.order_by("name")})


@manage_access("reporting.change_reportschedule")
def schedule_edit(request, pk=None):
    schedule = get_object_or_404(ReportSchedule, pk=pk) if pk else None
    return _simple_edit(
        request, ReportScheduleForm, schedule, back="manage:schedules", action="report_schedule_saved",
        stamp_creator=True, title=lambda s: f"Edit {s}" if s else "New scheduled report",
    )


# --- template catalog & smart groups ----------------------------------------------------


@manage_access("campaigns.view_campaigntemplate", methods=("GET",))
def catalog(request):
    return render(request, "manage/catalog.html", {
        "active": "manage",
        "templates": CampaignTemplate.objects.all(),
        "smart_groups": SmartGroup.objects.select_related("department").all(),
        "can_edit": request.user.has_perm("campaigns.change_campaigntemplate"),
    })


@manage_access("campaigns.change_campaigntemplate")
def catalog_template_edit(request, pk=None):
    obj = get_object_or_404(CampaignTemplate, pk=pk) if pk else None
    form_class = modelform_factory(CampaignTemplate, fields=[
        "name", "category", "difficulty", "template_name", "landing_page_name", "landing_page_url", "is_active"])
    return _simple_edit(
        request, form_class, obj, back="manage:catalog", action="campaign_template_saved", style=True,
        title=lambda o: f"Edit {o}" if o else "New catalog template",
    )


@manage_access("campaigns.change_smartgroup")
def smart_group_edit(request, pk=None):
    obj = get_object_or_404(SmartGroup, pk=pk) if pk else None
    form_class = modelform_factory(SmartGroup, fields=["name", "rule", "department", "new_hire_days"])
    return _simple_edit(
        request, form_class, obj, back="manage:catalog", action="smart_group_saved", style=True,
        title=lambda o: f"Edit {o}" if o else "New smart group",
    )


def _apply_field_classes(form):
    for field in form.fields.values():
        widget = field.widget
        widget.attrs.setdefault("class", "select" if isinstance(widget, forms.Select) else "field")


# --- deliverability preflight -----------------------------------------------------------


@manage_access("campaigns.change_campaign", methods=("GET", "POST"))
def deliverability(request):

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
