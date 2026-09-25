"""
Employee training portal: request a sign-in link, see your own assignments,
open the training, take the quiz, get a certificate. Scoring reuses the pure
training.scoring.score_quiz(); a passing attempt completes the assignment the
same way the admin's QuizAttempt save does.
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.audit import log_action
from apps.training.models import QuizAttempt
from apps.training.scoring import score_quiz

from .access import current_employee, portal_login, portal_logout, portal_required
from .tasks import send_portal_link
from .throttle import link_request_allowed
from .tokens import employee_from_token, sign_employee

logger = logging.getLogger(__name__)


def _quiz(module):
    """The module's quiz, or None if it has no questions: an empty quiz scores 0% and could
    otherwise never be passed, locking the employee out of completing their training."""
    quiz = getattr(module, "quiz", None)
    return quiz if quiz is not None and quiz.questions.exists() else None


def magic_link(employee) -> str:
    return settings.PORTAL_BASE_URL.rstrip("/") + reverse("portal:enter", args=[sign_employee(employee)])


def send_link(employee):
    send_mail(
        subject="Your security training sign-in link",
        message=(
            f"Hi {employee.full_name},\n\nUse this link to open your security awareness training. "
            f"It works for {settings.PORTAL_LINK_MAX_AGE_SECONDS // 3600} hours.\n\n{magic_link(employee)}\n\n"
            "If you didn't ask for this, you can ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[employee.email],
    )


@require_http_methods(["GET", "POST"])
def login(request):
    if request.method == "GET" and current_employee(request) is not None:
        return redirect("portal:home")  # already signed in: a sign-in form under a "Sign out" button makes no sense
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        allowed = link_request_allowed(request, email)
        if not allowed:
            # Count only; never log the address. The caller sees the same page either way.
            logger.warning("portal: sign-in link request throttled")
        if email and allowed:
            try:
                send_portal_link.delay(email)
            except Exception:  # noqa: BLE001 — broker down: same page either way, never reveal anything
                logger.exception("portal: could not queue sign-in link")
        # Same answer whether or not the address exists, so the form can't be used to list employees.
        return render(request, "portal/link_sent.html")
    return render(request, "portal/login.html")


def enter(request, token):
    employee = employee_from_token(token)
    if employee is None:
        return render(request, "portal/link_invalid.html", status=400)
    request.session.cycle_key()  # fresh session id on sign-in
    portal_login(request, employee)
    return redirect("portal:home")


@require_POST
def logout(request):
    portal_logout(request)
    return redirect("portal:login")


@portal_required
def home(request):
    # Outstanding first (soonest due first), then completed. Postgres sorts NULL last by default, which put
    # finished training above the work still to do.
    assignments = request.employee.training_assignments.filter(waived_at__isnull=True).select_related("module").order_by(
        F("completed_at").desc(nulls_first=True), F("due_at").asc(nulls_last=True)
    )
    return render(request, "portal/home.html", {
        "employee": request.employee, "assignments": assignments, "now": timezone.now(),
    })


def _own_assignment(request, pk):
    # Always filtered through the signed-in employee: another person's assignment is a 404.
    return get_object_or_404(request.employee.training_assignments.filter(waived_at__isnull=True).select_related("module"), pk=pk)


@portal_required
@require_http_methods(["GET", "POST"])
def assignment(request, pk):
    item = _own_assignment(request, pk)
    quiz = _quiz(item.module)

    if (request.method == "POST" and request.POST.get("action") == "start" and item.started_at is None
            and not item.module.slides.exists()):  # a slide course is started only by opening it (see course())
        item.started_at = timezone.now()
        item.save(update_fields=["started_at"])

    if request.method == "POST" and request.POST.get("action") == "complete" and quiz is None:
        if _course_not_opened(item):
            return redirect("portal:course", pk=item.pk)  # self-attesting a course you never opened isn't completion
        if _has_no_content(item.module):
            return redirect("portal:assignment", pk=item.pk)  # nothing to complete: a certificate here would be hollow
        _complete(request, item, score=None)
        return redirect("portal:certificate", pk=item.pk)

    return render(request, "portal/assignment.html", {
        "item": item, "quiz": quiz,
        "questions": quiz.questions.prefetch_related("choices") if quiz else [],
        "slide_count": item.module.slides.count(),
        "no_content": _has_no_content(item.module),
        "attempts": item.quiz_attempts.order_by("-completed_at"),
    })


def _has_no_content(module) -> bool:
    return not module.content_url and not module.slides.exists()


def _course_not_opened(item) -> bool:
    return item.started_at is None and item.module.slides.exists()


def _slide_number(request, total):
    raw = request.GET.get("s", "1")
    return min(max(int(raw) if raw.isdigit() else 1, 1), total)


@portal_required
def course(request, pk):
    """The module's slides, one per page (server-rendered; the position lives in ?s=). The
    last slide leads straight into the quiz. Opening the course marks the assignment started."""
    item = _own_assignment(request, pk)
    slides = list(item.module.slides.all())
    if not slides:
        return redirect("portal:assignment", pk=item.pk)
    if item.started_at is None:
        item.started_at = timezone.now()
        item.save(update_fields=["started_at"])
    number = _slide_number(request, len(slides))
    return render(request, "portal/course.html", {
        "item": item, "slide": slides[number - 1], "number": number, "total": len(slides),
        "prev": number - 1 if number > 1 else None, "next": number + 1 if number < len(slides) else None,
        "has_quiz": _quiz(item.module) is not None, "percent": round(number / len(slides) * 100),
        "back_url": reverse("portal:assignment", args=[item.pk]),
        "quiz_url": reverse("portal:submit-quiz", args=[item.pk]),
        "course_url": reverse("portal:course", args=[item.pk]),
        "finish_url": reverse("portal:assignment", args=[item.pk]),
    })


@portal_required
@require_http_methods(["GET", "POST"])
def submit_quiz(request, pk):
    """GET shows the quiz (straight after the course); POST scores it."""
    item = _own_assignment(request, pk)
    quiz = _quiz(item.module)
    if quiz is None or item.completed_at is not None:
        return redirect("portal:assignment", pk=item.pk)
    if _course_not_opened(item):
        return redirect("portal:course", pk=item.pk)  # take the course first (a direct POST included)
    if request.method == "GET":
        return render(request, "portal/quiz.html", {
            "item": item, "quiz": quiz, "questions": quiz.questions.prefetch_related("choices"),
            "attempts": item.quiz_attempts.order_by("-completed_at"),
        })

    answers = {}
    for question in quiz.questions.all():
        raw = request.POST.get(f"q{question.pk}")
        if raw and raw.isdigit():
            answers[question.pk] = int(raw)
    score, passed = score_quiz(quiz, answers)
    QuizAttempt.objects.create(assignment=item, score_percent=score, passed=passed)
    log_action(actor=None, action="quiz_attempted", target_description=str(item), score=score, passed=passed, via="portal")

    if passed:
        _complete(request, item, score=score)
        return redirect("portal:certificate", pk=item.pk)
    messages.error(request, f"You scored {score}%. You need {quiz.passing_score_percent}% to pass — have another go.")
    return redirect("portal:submit-quiz", pk=item.pk)


def _complete(request, item, *, score):
    if item.started_at is None:
        item.started_at = timezone.now()
    item.completed_at = timezone.now()
    item.save(update_fields=["started_at", "completed_at"])
    log_action(actor=None, action="training_assignment_completed", target_description=str(item), via="portal", score=score)


@portal_required
def certificate(request, pk):
    item = _own_assignment(request, pk)
    if item.completed_at is None:
        return redirect("portal:assignment", pk=item.pk)
    attempt = item.quiz_attempts.filter(passed=True).order_by("-completed_at").first()
    return render(request, "portal/certificate.html", {"item": item, "attempt": attempt, "employee": request.employee})


@portal_required
@require_http_methods(["GET", "POST"])
def report_email(request):
    from apps.intake.models import ReportedEmail

    if request.method == "POST":
        subject = (request.POST.get("subject") or "").strip()[:300]
        sender = (request.POST.get("sender") or "").strip()[:300]
        notes = (request.POST.get("notes") or "").strip()[:5000]
        if not (subject or sender or notes):
            messages.error(request, "Tell us at least the subject, the sender or what looked wrong.")
        else:
            ReportedEmail.objects.create(reporter=request.employee, subject=subject, sender=sender, notes=notes,
                                         source=ReportedEmail.Source.PORTAL)
            log_action(actor=None, action="email_reported", target_description=subject or sender or "(no subject)",
                       via="portal")
            messages.success(request, "Thanks — the security team will look at it. Reporting is exactly the right move.")
            return redirect("portal:home")
    return render(request, "portal/report.html")


def learn(request):
    """
    Teachable moment: where a simulation's landing page sends someone who just clicked or
    submitted. Public (no sign-in) and anonymous — it never records or shows who arrived.
    """
    return render(request, "portal/learn.html")
