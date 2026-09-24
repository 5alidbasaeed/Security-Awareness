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
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.audit import log_action
from apps.employees.models import Employee
from apps.training.models import QuizAttempt
from apps.training.scoring import score_quiz

from .access import portal_login, portal_logout, portal_required
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
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        employee = Employee.objects.filter(email__iexact=email, is_active=True).first() if email else None
        if employee is not None:
            try:
                send_link(employee)
            except Exception:  # noqa: BLE001 — never reveal delivery failures (or whether the address exists)
                logger.exception("portal: could not email sign-in link")
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
    assignments = request.employee.training_assignments.select_related("module").order_by("completed_at", "due_at")
    return render(request, "portal/home.html", {
        "employee": request.employee, "assignments": assignments, "now": timezone.now(),
    })


def _own_assignment(request, pk):
    # Always filtered through the signed-in employee: another person's assignment is a 404.
    return get_object_or_404(request.employee.training_assignments.select_related("module"), pk=pk)


@portal_required
@require_http_methods(["GET", "POST"])
def assignment(request, pk):
    item = _own_assignment(request, pk)
    quiz = _quiz(item.module)

    if request.method == "POST" and request.POST.get("action") == "start" and item.started_at is None:
        item.started_at = timezone.now()
        item.save(update_fields=["started_at"])

    if request.method == "POST" and request.POST.get("action") == "complete" and quiz is None:
        _complete(request, item, score=None)
        return redirect("portal:certificate", pk=item.pk)

    return render(request, "portal/assignment.html", {
        "item": item, "quiz": quiz,
        "questions": quiz.questions.prefetch_related("choices") if quiz else [],
        "attempts": item.quiz_attempts.order_by("-completed_at"),
    })


@portal_required
@require_POST
def submit_quiz(request, pk):
    item = _own_assignment(request, pk)
    quiz = _quiz(item.module)
    if quiz is None or item.completed_at is not None:
        return redirect("portal:assignment", pk=item.pk)

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
    return redirect("portal:assignment", pk=item.pk)


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
