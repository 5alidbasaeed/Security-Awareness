"""
Read-and-draft JSON API. Everything here either reads data or drafts content
for a human to review. There is deliberately NO endpoint to launch, approve,
send or delete a campaign — that boundary is what makes an API key safe to hand
to an automated client: it can prepare a whole campaign (email template, landing
page, draft) but a person still has to approve and launch it in the dashboard.
"""

from django.db import transaction
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime

from apps.campaigns.services import create_draft_campaign
from apps.core.audit import log_action
from apps.core.scoping import visible_campaigns, visible_departments
from apps.engine.factory import get_client
from apps.training.models import Quiz, QuizChoice, QuizQuestion, TrainingModule

from .auth import api_endpoint, parse_json_body
from .models import Scope


def _require(data, *fields):
    missing = [f for f in fields if not data.get(f)]
    return f"Missing required field(s): {', '.join(missing)}." if missing else None


def _audit(request, action, target):
    log_action(actor=request.user, action=action, target_description=target, via="api", api_key=request.api_key.prefix)


@api_endpoint(scope=None)
def whoami(request):
    key = request.api_key
    return JsonResponse({
        "key": key.name, "prefix": key.prefix, "owner": key.owner.get_username(),
        "scopes": key.scopes, "expires_at": key.expires_at.isoformat() if key.expires_at else None,
    })


# --- Training ---------------------------------------------------------------------------


@api_endpoint(scope=Scope.TRAINING_WRITE, methods=("GET", "POST"))
def training_modules(request):
    if request.method == "GET":
        rows = [
            {"id": m.id, "title": m.title, "content_url": m.content_url, "is_active": m.is_active,
             "has_quiz": hasattr(m, "quiz")}
            for m in TrainingModule.objects.all().order_by("title")
        ]
        return JsonResponse({"training_modules": rows})

    data, error = parse_json_body(request)
    if error:
        return error
    missing = _require(data, "title", "content_url")
    if missing:
        return JsonResponse({"error": missing}, status=400)

    try:
        with transaction.atomic():
            module = TrainingModule.objects.create(
                title=data["title"][:200],
                description=data.get("description", ""),
                content_url=data["content_url"],
                duration_minutes=int(data.get("duration_minutes") or 10),
                is_active=bool(data.get("is_active", True)),
            )
            quiz_error = _build_quiz(module, data.get("quiz"))
            if quiz_error:
                raise _InvalidQuiz(quiz_error)  # raising (not returning) rolls the module back too
    except _InvalidQuiz as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    _audit(request, "training_module_created", module.title)
    return JsonResponse({"id": module.id, "title": module.title, "has_quiz": hasattr(module, "quiz")}, status=201)


class _InvalidQuiz(Exception):
    pass


def _build_quiz(module, quiz_data) -> str | None:
    """Creates a quiz with questions/choices from nested JSON. Returns an error string or None."""
    if not quiz_data:
        return None
    questions = quiz_data.get("questions") or []
    if not questions:
        return "A quiz needs at least one question."
    quiz = Quiz.objects.create(module=module, passing_score_percent=int(quiz_data.get("passing_score_percent") or 80))
    for i, q in enumerate(questions):
        text = (q or {}).get("text")
        choices = (q or {}).get("choices") or []
        if not text or not choices:
            return f"Question {i + 1} needs text and at least one choice."
        if not any(c.get("is_correct") for c in choices):
            return f"Question {i + 1} needs at least one correct choice."
        question = QuizQuestion.objects.create(quiz=quiz, text=text, order=i)
        for choice in choices:
            if not choice.get("text"):
                return f"A choice in question {i + 1} has no text."
            QuizChoice.objects.create(
                question=question, text=choice["text"][:300], is_correct=bool(choice.get("is_correct"))
            )
    return None


# --- Email templates and landing pages (drafted in the engine) --------------------------


@api_endpoint(scope=Scope.CONTENT_WRITE, methods=("GET", "POST"))
def email_templates(request):
    client = get_client()
    if request.method == "GET":
        return JsonResponse({"email_templates": client.list_email_templates()})

    data, error = parse_json_body(request)
    if error:
        return error
    missing = _require(data, "name", "subject", "html")
    if missing:
        return JsonResponse({"error": missing}, status=400)
    ref = client.upsert_email_template(
        name=data["name"], subject=data["subject"], html=data["html"], text=data.get("text", "")
    )
    _audit(request, "email_template_drafted", data["name"])
    return JsonResponse({"name": data["name"], "external_id": ref.external_id}, status=201)


@api_endpoint(scope=Scope.CONTENT_WRITE, methods=("GET", "POST"))
def landing_pages(request):
    client = get_client()
    if request.method == "GET":
        return JsonResponse({"landing_pages": client.list_landing_pages()})

    data, error = parse_json_body(request)
    if error:
        return error
    missing = _require(data, "name", "html")
    if missing:
        return JsonResponse({"error": missing}, status=400)
    # capture_passwords is never accepted — the adapter forces it off regardless (invariant #4).
    ref = client.upsert_landing_page(
        name=data["name"], html=data["html"],
        capture_credentials=bool(data.get("capture_credentials", True)),
        redirect_url=data.get("redirect_url", ""),
    )
    _audit(request, "landing_page_drafted", data["name"])
    return JsonResponse(
        {"name": data["name"], "external_id": ref.external_id, "capture_passwords": False}, status=201
    )


# --- Campaigns (draft only) -------------------------------------------------------------


@api_endpoint(scope=Scope.CAMPAIGNS_WRITE, methods=("GET", "POST"))
def campaigns(request):
    if request.method == "GET":
        rows = [
            {"id": c.id, "name": c.name, "status": c.status,
             "target_department": c.target_department.name if c.target_department else None}
            for c in visible_campaigns(request.user).select_related("target_department").order_by("-id")
        ]
        return JsonResponse({"campaigns": rows})

    data, error = parse_json_body(request)
    if error:
        return error
    missing = _require(data, "name", "template_name", "landing_page_name", "landing_page_url")
    if missing:
        return JsonResponse({"error": missing}, status=400)

    department = None
    if data.get("target_department"):
        department = visible_departments(request.user).filter(name=data["target_department"]).first()
        if department is None:
            return JsonResponse({"error": f"No department named {data['target_department']!r} you can target."}, status=400)

    module = None
    if data.get("training_module_id"):
        module = TrainingModule.objects.filter(pk=data["training_module_id"]).first()
        if module is None:
            return JsonResponse({"error": "Unknown training_module_id."}, status=400)

    scheduled_at = parse_datetime(data["scheduled_at"]) if data.get("scheduled_at") else None

    campaign = create_draft_campaign(
        actor=request.user, name=data["name"], template_name=data["template_name"],
        landing_page_name=data["landing_page_name"], landing_page_url=data["landing_page_url"],
        target_department=department, training_module=module, scheduled_at=scheduled_at,
    )
    return JsonResponse(
        {"id": campaign.id, "name": campaign.name, "status": campaign.status,
         "note": "Created as a draft. A person must submit, approve and launch it in the dashboard."},
        status=201,
    )


# --- Reported emails (report-button / mail-integration intake) --------------------------


@api_endpoint(scope=Scope.REPORTS_WRITE, methods=("POST",))
def reported_emails(request):
    from apps.employees.models import Employee
    from apps.intake.models import ReportedEmail

    data, error = parse_json_body(request)
    if error:
        return error
    missing = _require(data, "reporter_email")
    if missing:
        return JsonResponse({"error": missing}, status=400)
    reporter = Employee.objects.filter(email__iexact=data["reporter_email"], is_active=True).first()
    if reporter is None:
        return JsonResponse({"error": "reporter_email is not an active employee."}, status=400)
    report = ReportedEmail.objects.create(
        reporter=reporter, subject=str(data.get("subject", ""))[:300], sender=str(data.get("sender", ""))[:300],
        notes=str(data.get("notes", ""))[:5000], headers=str(data.get("headers", ""))[:20000],
        source=ReportedEmail.Source.API,
    )
    _audit(request, "email_reported", report.subject or report.sender or "(no subject)")
    return JsonResponse({"id": report.id, "verdict": report.verdict}, status=201)
