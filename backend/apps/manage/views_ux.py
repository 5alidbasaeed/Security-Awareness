"""
Usability helpers for the campaign, content and training pages: a live "what will this do" summary
beside the campaign form, and one-click duplicate / retire actions. Same rules as the rest of /manage/:
real permissions, row scoping through core.scoping, an audit entry for every change.
"""

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.campaigns.audience import _base, resolve_smart_group
from apps.campaigns.models import EmailDraft, LandingDraft, SmartGroup
from apps.core.audit import log_action
from apps.core.scoping import managed_departments, visible_departments
from apps.employees.models import Employee
from apps.engine.factory import get_client
from apps.training.models import Quiz, QuizChoice, QuizQuestion, TrainingModule, TrainingSlide

from .access import manage_access, org_wide_content

NAME_MAX = 200


def module_readiness(module, slide_count, question_count):
    """(label, level, detail) for a training module: can an employee actually take it?"""
    if not module.is_active:
        return ("Retired", "", "Not offered to anyone new.")
    if not (slide_count or module.content_url):
        return ("No content", "high", "It has no slides and no link, so an employee would open an empty page.")
    if hasattr(module, "quiz") and not question_count:
        return ("Quiz is empty", "medium", "A quiz with no questions is skipped: people only confirm completion.")
    return ("Ready", "low", "")


def _unique_name(model, base, field="name"):
    """'Copy of X', then 'Copy of X (2)', ... unique, and always within the field's length limit
    (the stem is shortened to make room for the suffix, however many digits it has)."""
    stem = f"Copy of {base}"
    candidate, n = stem[:NAME_MAX], 1
    while model.objects.filter(**{field: candidate}).exists():
        n += 1
        suffix = f" ({n})"
        candidate = stem[: NAME_MAX - len(suffix)] + suffix
    return candidate


def _save_copy(copy, model, source_name):
    """Save a duplicated draft under a fresh unique name. Two people duplicating the same item at once
    can pick the same name; the unique constraint catches it and the loser simply tries the next one."""
    for _ in range(5):
        copy.name = _unique_name(model, source_name)
        try:
            with transaction.atomic():
                copy.save()
            return copy
        except IntegrityError:
            continue
    raise IntegrityError(f"Could not find a free name for a copy of {source_name}")


# --- campaign form: live summary --------------------------------------------------------


def _audience_summary(user, value):
    """Who a chosen audience would reach right now, and who is left out and why."""
    kind, _, ident = (value or "").partition(":")
    managed = managed_departments(user)
    if kind == "dept" and ident.isdigit():
        department = visible_departments(user).filter(pk=int(ident)).first()
        if department is None:
            return None
        pool, label = Employee.objects.filter(department=department), department.name
        eligible = _base(department).count()
    elif value == "all" and managed is None:
        pool, label, eligible = Employee.objects.all(), "Everyone", _base(None).count()
    elif kind == "smart" and ident.isdigit() and managed is None:
        group = SmartGroup.objects.filter(pk=int(ident)).select_related("department").first()
        if group is None:
            return None
        return {"label": group.name, "recipients": resolve_smart_group(group).count(), "inactive": None, "exempt": None}
    else:
        return None
    inactive = pool.filter(is_active=False).count()
    exempt = pool.filter(is_active=True).count() - _base_within(pool)
    return {"label": label, "recipients": eligible, "inactive": inactive, "exempt": max(exempt, 0)}


def _base_within(pool):
    from apps.employees.exemptions import not_exempt_q

    return pool.filter(not_exempt_q(), is_active=True).count()


@manage_access("campaigns.change_campaign", methods=("GET",))
def campaign_summary(request):
    """The panel beside the campaign form. Refreshed whenever a choice changes; touches nothing."""
    email_name = request.GET.get("email", "")
    page_name = request.GET.get("landing_page", "")
    email = EmailDraft.objects.filter(name=email_name).first() if email_name else None
    page = LandingDraft.objects.filter(name=page_name).first() if page_name else None
    audience = _audience_summary(request.user, request.GET.get("audience", ""))
    module_id = request.GET.get("training_module", "")
    module = TrainingModule.objects.filter(pk=int(module_id)).first() if module_id.isdigit() else None

    problems = []
    if not email_name:
        problems.append("Choose the email people will receive.")
    elif email is None:
        problems.append("That email is only in the phishing engine, so it can't be previewed here.")
    if not page_name:
        problems.append("Choose where people land after clicking.")
    if not request.GET.get("audience"):
        problems.append("Choose who receives it.")
    elif audience is None:
        problems.append("That audience isn't available to you.")
    elif audience["recipients"] == 0:
        problems.append("Nobody is eligible in that audience, so it can't be submitted for approval.")
    if module is not None:
        label, level, detail = module_readiness(module, module.slides.count(), 0)
        if label == "No content":
            problems.append(f"“{module.title}” has no content yet. {detail}")
    return render(request, "manage/_campaign_summary.html", {
        "audience": audience, "email": email, "page": page, "module": module, "problems": problems,
        "email_preview": reverse("manage:email-preview", args=[email.name]) if email else None,
        "page_preview": reverse("manage:page-preview", args=[page.name]) if page else None,
    })


# --- duplicate ------------------------------------------------------------------------------


@manage_access("campaigns.change_campaign", methods=("POST",))
@org_wide_content
def email_duplicate(request, pk):
    source = get_object_or_404(EmailDraft, pk=pk)
    copy = EmailDraft.objects.get(pk=source.pk)
    copy.pk, copy.id = None, None
    _save_copy(copy, EmailDraft, source.name)
    try:
        get_client().upsert_email_template(name=copy.name, subject=copy.subject, html=copy.render_html(), text=copy.render_text())
    except Exception as exc:  # noqa: BLE001 — keep the copy; only the engine push failed
        messages.error(request, f"Copied here, but the phishing engine could not be updated: {exc}")
    else:
        messages.success(request, f"Copied to “{copy.name}”. Edit it below.")
    log_action(actor=request.user, action="email_template_duplicated", target_description=f"{source.name} -> {copy.name}")
    return redirect("manage:template-edit", pk=copy.pk)


@manage_access("campaigns.change_campaign", methods=("POST",))
@org_wide_content
def landing_duplicate(request, pk):
    source = get_object_or_404(LandingDraft, pk=pk)
    copy = LandingDraft.objects.get(pk=source.pk)
    copy.pk, copy.id = None, None
    _save_copy(copy, LandingDraft, source.name)
    html = copy.custom_html if copy.layout == LandingDraft.Layout.CLONED else copy.render_html()
    try:
        get_client().upsert_landing_page(name=copy.name, html=html, capture_credentials=True, redirect_url=copy.engine_redirect_url())
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"Copied here, but the phishing engine could not be updated: {exc}")
    else:
        messages.success(request, f"Copied to “{copy.name}”. Passwords are never captured.")
    log_action(actor=request.user, action="landing_page_duplicated", target_description=f"{source.name} -> {copy.name}")
    return redirect("manage:landing-edit", pk=copy.pk)


@manage_access("training.change_trainingmodule", methods=("POST",))
def module_duplicate(request, pk):
    source = get_object_or_404(TrainingModule, pk=pk)
    with transaction.atomic():
        copy = TrainingModule.objects.create(
            title=_unique_name(TrainingModule, source.title, "title"), description=source.description,
            content_url=source.content_url, duration_minutes=source.duration_minutes,
            is_active=False,  # a copy is a draft: not offered to anyone until someone has checked it
        )
        for slide in source.slides.all():
            TrainingSlide.objects.create(module=copy, order=slide.order, title=slide.title, body=slide.body, callout=slide.callout)
        quiz = getattr(source, "quiz", None)
        if quiz is not None:
            new_quiz = Quiz.objects.create(module=copy, passing_score_percent=quiz.passing_score_percent)
            for question in quiz.questions.prefetch_related("choices"):
                q = QuizQuestion.objects.create(quiz=new_quiz, text=question.text, order=question.order)
                for choice in question.choices.all():
                    QuizChoice.objects.create(question=q, text=choice.text, is_correct=choice.is_correct)
    log_action(actor=request.user, action="training_module_duplicated", target_description=f"{source.title} -> {copy.title}")
    messages.success(request, f"Copied to “{copy.title}”. It is retired until you turn it on, so nobody gets it by accident.")
    return redirect("manage:module-edit", pk=copy.pk)


@manage_access("training.change_trainingmodule", methods=("POST",))
def module_toggle(request, pk):
    module = get_object_or_404(TrainingModule, pk=pk)
    module.is_active = not module.is_active
    module.save(update_fields=["is_active"])
    log_action(actor=request.user, action="training_module_activated" if module.is_active else "training_module_retired",
               target_description=module.title)
    if module.is_active:
        messages.success(request, f"“{module.title}” is active again. Reminders resume for open assignments.")
    else:
        messages.success(request, f"“{module.title}” is retired: nobody new is assigned it, and open assignments stay "
                                  "open but get no more reminders or manager escalations.")
    return redirect("manage:training")
