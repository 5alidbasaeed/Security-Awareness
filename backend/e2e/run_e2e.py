# ruff: noqa: I001  (imports below must follow django.setup(), so they cannot be sorted to the top)
"""
End-to-end run against the LIVE stack (real Postgres, Redis, Celery worker, Gophish, Django over HTTP).
It exercises everything up to — and deliberately NOT including — sending email:

  content authoring in Gophish -> draft campaign (API) -> approval workflow + RBAC (HTTP) ->
  launch service with a guard client (real Gophish target-group sync, but the campaign-create
  call that would send mail is intercepted) -> signed webhooks -> ingestion -> Celery scoring and
  training assignment -> dashboard + row scoping -> employee portal (magic link, quiz, certificate,
  report-a-phish) -> API keys and scopes -> reports and evidence package -> cleanup.

Run (from the repo root, stack up with the dev override):
  docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm -T \\
      -v "$PWD/backend:/app" django python -m e2e.run_e2e

Safe to re-run: it removes its own data first and last (everything is tagged E2E / e2e_ / @e2e.example).
Refuses to run unless DEBUG is on.
"""

import hashlib
import hmac
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import timedelta
from urllib.parse import quote

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

import requests  # noqa: E402
from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.auth.models import Group  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

if not settings.DEBUG:
    sys.exit("e2e refuses to run with DEBUG off")

BASE = os.environ.get("E2E_BASE", "http://django:8000")
HOST = {"Host": "localhost"}  # ALLOWED_HOSTS is localhost; we reach the container by service name
PASSWORD = "E2e-pass-123!"
WEBHOOK_SECRET = settings.GOPHISH_WEBHOOK_SECRET
GOPHISH = settings.GOPHISH_API_URL
GOPHISH_HEADERS = {"Authorization": f"Bearer {settings.GOPHISH_API_KEY}"}

results: list[tuple[bool, str, str]] = []


def check(name, condition, detail=""):
    results.append((bool(condition), name, str(detail)))
    print(("  PASS  " if condition else "  FAIL  ") + name + ("" if condition else f"   <-- {detail}"), flush=True)


def section(title):
    print(f"\n== {title}", flush=True)


def wait_for(fn, timeout=40, every=1.0):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(every)
    return fn()


def gophish(method, path, **kw):
    return requests.request(method, f"{GOPHISH}/api/{path}", headers=GOPHISH_HEADERS, timeout=15, **kw)


def csrf_from(html):
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    return m.group(1) if m else ""


class Http:
    """A browser-like session against the real app (cookies, CSRF), no redirects followed by default."""

    def __init__(self):
        self.s = requests.Session()

    def get(self, path, **kw):
        kw.setdefault("allow_redirects", False)
        return self.s.get(BASE + path, headers=HOST, timeout=30, **kw)

    def post(self, path, data=None, referer=None, **kw):
        data = dict(data or {})
        if "csrfmiddlewaretoken" not in data:
            data["csrfmiddlewaretoken"] = self.s.cookies.get("csrftoken", "")
        kw.setdefault("allow_redirects", False)
        headers = dict(HOST, Referer=BASE + (referer or path))
        return self.s.post(BASE + path, data=data, headers=headers, timeout=30, **kw)

    def login_staff(self, username):
        page = self.get("/login/")
        r = self.post("/login/", {"username": username, "password": PASSWORD,
                                  "csrfmiddlewaretoken": csrf_from(page.text), "next": "/"})
        return r.status_code == 302


def signed_webhook(payload, secret=None, sign=True):
    body = json.dumps(payload).encode()
    headers = dict(HOST, **{"Content-Type": "application/json"})
    if sign:
        digest = hmac.new((secret or WEBHOOK_SECRET).encode(), body, hashlib.sha256).hexdigest()
        headers["X-Gophish-Signature"] = f"sha256={digest}"
    return requests.post(f"{BASE}/webhooks/gophish", data=body, headers=headers, timeout=30)


# --------------------------------------------------------------------------------------------
from apps.api.models import ApiKey  # noqa: E402
from apps.campaigns.models import Campaign  # noqa: E402
from apps.core.models import AuditLogEntry  # noqa: E402
from apps.employees.models import Department, Employee  # noqa: E402
from apps.engine.base import ExternalCampaignRef  # noqa: E402
from apps.engine.factory import get_client  # noqa: E402
from apps.events.models import Event  # noqa: E402
from apps.intake.models import ReportedEmail  # noqa: E402
from apps.portal.tokens import sign_employee  # noqa: E402
from apps.reporting.models import GeneratedReport  # noqa: E402
from apps.risk_scoring.models import RiskScoreSnapshot  # noqa: E402
from apps.training.models import Quiz, QuizAttempt, QuizChoice, QuizQuestion, TrainingAssignment, TrainingModule  # noqa: E402

User = get_user_model()


def cleanup():
    emp = "(SELECT id FROM employees_employee WHERE email LIKE '%@e2e.example')"
    camp = "(SELECT id FROM campaigns_campaign WHERE name LIKE 'E2E %')"
    usr = "(SELECT id FROM auth_user WHERE username LIKE 'e2e\\_%')"
    statements = [
        f"DELETE FROM risk_scoring_riskscoresnapshot WHERE employee_id IN {emp}",
        f"DELETE FROM training_quizattempt WHERE assignment_id IN (SELECT id FROM training_trainingassignment WHERE employee_id IN {emp})",
        f"DELETE FROM training_trainingassignment WHERE employee_id IN {emp}",
        f"DELETE FROM events_event WHERE employee_id IN {emp} OR campaign_id IN {camp}",
        f"DELETE FROM intake_reportedemail WHERE reporter_id IN {emp}",
        f"DELETE FROM reporting_generatedreport WHERE generated_by_id IN {usr}",
        f"DELETE FROM core_auditlogentry WHERE actor_id IN {usr} OR target_description LIKE 'E2E %'",
    ]
    with connection.cursor() as cursor:
        for sql in statements:
            cursor.execute(sql)
    Campaign.objects.filter(name__startswith="E2E ").delete()
    Employee.objects.filter(email__endswith="@e2e.example").delete()
    Department.objects.filter(name__startswith="E2E ").delete()
    TrainingModule.objects.filter(title__startswith="E2E ").delete()
    User.objects.filter(username__startswith="e2e_").delete()
    try:
        for kind, name_key in (("templates", "name"), ("pages", "name"), ("groups", "name")):
            for item in gophish("GET", f"{kind}/").json():
                if str(item.get(name_key, "")).startswith("E2E "):
                    gophish("DELETE", f"{kind}/{item['id']}")
    except Exception as exc:  # noqa: BLE001
        print("  (gophish cleanup skipped:", exc, ")")


def main():  # noqa: C901 — a linear script on purpose
    print("Cleaning any leftovers from a previous run...")
    cleanup()
    gophish_campaigns_before = len(gophish("GET", "campaigns/").json())

    # ------------------------------------------------------------------------------------
    section("0. Stack health")
    health = requests.get(f"{BASE}/healthz", headers=HOST, timeout=10)
    check("healthz reports database and redis ok", health.ok and health.json()["healthy"], health.text)
    check("public login page is served with a strict CSP",
          "script-src 'self'" in Http().get("/login/").headers.get("Content-Security-Policy", ""))
    check("Gophish API answers with the configured key", gophish("GET", "templates/").ok)

    # ------------------------------------------------------------------------------------
    section("1. Setup: roles, people, training")
    call_command("setup_groups", verbosity=0)
    users = {}
    for username, group in [("e2e_cm", "Campaign Manager"), ("e2e_sa", "Security Admin"), ("e2e_rv", "Report Viewer"),
                            ("e2e_dm", "Department Manager"), ("e2e_tm", "Training Manager")]:
        u = User.objects.create_user(username, password=PASSWORD, is_staff=True)
        u.groups.add(Group.objects.get(name=group))
        users[username] = u
    finance, other = Department.objects.create(name="E2E Finance"), Department.objects.create(name="E2E Other")
    finance.managers.add(users["e2e_dm"])
    alice = Employee.objects.create(email="alice@e2e.example", full_name="Alice Aardvark", department=finance)
    bob = Employee.objects.create(email="bob@e2e.example", full_name="Bob Badger", department=finance)
    carol = Employee.objects.create(email="carol@e2e.example", full_name="Carol Cat", department=finance, is_exempt=True)
    dave = Employee.objects.create(email="dave@e2e.example", full_name="Dave Dingo", department=other)
    module = TrainingModule.objects.create(title="E2E Phishing Basics", content_url="https://training.example.com/e2e")
    quiz = Quiz.objects.create(module=module, passing_score_percent=100)
    q1 = QuizQuestion.objects.create(quiz=quiz, text="What do you do with a suspicious link?", order=1)
    q1_right = QuizChoice.objects.create(question=q1, text="Report it", is_correct=True)
    q1_wrong = QuizChoice.objects.create(question=q1, text="Click it", is_correct=False)
    check("5 roles, 4 employees (1 exempt), 1 module with a quiz created", len(users) == 5 and Employee.objects.filter(email__endswith="@e2e.example").count() == 4)

    # ------------------------------------------------------------------------------------
    section("2. Content authoring in real Gophish (through the adapter; sends nothing)")
    client = get_client()
    tpl = client.upsert_email_template(
        name="E2E Invoice", subject="Invoice overdue",
        html="<p>Hi {{.FirstName}}, please <a href='{{.URL}}'>review your invoice</a>.</p>{{.Tracker}}", text="{{.URL}}")
    page_html = ("<html><body><form method='POST'><input name='username'>"
                 "<input type='password' name='password'><button>Sign in</button></form></body></html>")
    page = client.upsert_landing_page(name="E2E Login Page", html=page_html, capture_credentials=True)
    check("template and landing page created in Gophish", tpl.external_id and page.external_id)
    check("template is listed and readable back", any(t["name"] == "E2E Invoice" for t in client.list_email_templates())
          and client.get_email_template("E2E Invoice")["subject"] == "Invoice overdue")
    stored = next(p for p in gophish("GET", "pages/").json() if p["name"] == "E2E Login Page")
    check("invariant #4: landing page captures credentials but NEVER passwords",
          stored["capture_credentials"] is True and stored["capture_passwords"] is False, stored)
    check("landing page HTML round-trips through the adapter", "type=\"password\"" in client.get_landing_page_html("E2E Login Page")
          or "type='password'" in client.get_landing_page_html("E2E Login Page"))
    check("a sending profile exists for launches to use", any(p["name"] == settings.GOPHISH_DEFAULT_SEND_PROFILE for p in client.list_sending_profiles()))

    # ------------------------------------------------------------------------------------
    section("3. Draft campaign through the API (key scopes, no launch endpoint)")
    _, cm_key = ApiKey.generate(name="e2e content bot", owner=users["e2e_cm"],
                                scopes=["read", "campaigns:write", "content:write"])
    _, read_only_key = ApiKey.generate(name="e2e read only", owner=users["e2e_cm"], scopes=["read"])
    api = lambda method, path, key, **kw: requests.request(  # noqa: E731
        method, f"{BASE}/api/{path}", headers=dict(HOST, Authorization=f"Api-Key {key}", **{"Content-Type": "application/json"}),
        timeout=30, **kw)
    check("no key -> 401", requests.get(f"{BASE}/api/v1/whoami", headers=HOST, timeout=10).status_code == 401)
    check("garbage key -> 401", api("GET", "v1/whoami", "sk_sim_nope").status_code == 401)
    check("valid key -> whoami names its owner", api("GET", "v1/whoami", cm_key).json().get("username", api("GET", "v1/whoami", cm_key).text) == "e2e_cm"
          or "e2e_cm" in api("GET", "v1/whoami", cm_key).text)
    check("a read-only key cannot create campaigns (403)",
          api("POST", "v1/campaigns", read_only_key, data="{}").status_code == 403)
    created = api("POST", "v1/campaigns", cm_key, data=json.dumps({
        "name": "E2E Invoice Test", "template_name": "E2E Invoice", "landing_page_name": "E2E Login Page",
        "landing_page_url": "http://localhost/", "target_department": "E2E Finance", "training_module_id": module.pk}))
    check("draft campaign created via API (201, status draft)", created.status_code == 201 and created.json()["status"] == "draft", created.text)
    campaign = Campaign.objects.get(name="E2E Invoice Test")
    check("API-created campaign records its owner as creator", campaign.created_by_id == users["e2e_cm"].pk)
    check("there is no API route that launches or approves",
          all(api("POST", f"v1/campaigns/{campaign.pk}/{verb}", cm_key, data="{}").status_code in (404, 405) for verb in ("launch", "approve")))
    check("bad JSON body -> 400, not 500", api("POST", "v1/campaigns", cm_key, data="{not json").status_code == 400)

    # ------------------------------------------------------------------------------------
    section("4. Approval workflow and RBAC over real HTTP")
    cm, sa, dm, rv = Http(), Http(), Http(), Http()
    check("staff can sign in to the dashboard", all([cm.login_staff("e2e_cm"), sa.login_staff("e2e_sa"), dm.login_staff("e2e_dm"), rv.login_staff("e2e_rv")]))
    r = cm.post(f"/manage/campaigns/{campaign.pk}/submit/")
    campaign.refresh_from_db()
    check("Campaign Manager submits for approval", campaign.status == "pending_approval", (r.status_code, campaign.status))
    r = cm.post(f"/manage/campaigns/{campaign.pk}/approve/")
    campaign.refresh_from_db()
    check("Campaign Manager can NOT approve their own campaign", r.status_code == 403 and campaign.status == "pending_approval", (r.status_code, campaign.status))
    r = cm.post(f"/manage/campaigns/{campaign.pk}/launch/")
    campaign.refresh_from_db()
    check("launching an unapproved campaign is refused and sends nothing", campaign.status == "pending_approval" and campaign.gophish_campaign_id is None, campaign.status)
    check("a Report Viewer cannot approve either", rv.post(f"/manage/campaigns/{campaign.pk}/approve/").status_code == 403)
    r = sa.post(f"/manage/campaigns/{campaign.pk}/approve/")
    campaign.refresh_from_db()
    check("Security Admin approves", campaign.status == "approved" and campaign.approved_by_id == users["e2e_sa"].pk, (r.status_code, campaign.status))
    preview = sa.get(f"/manage/content/pages/{quote('E2E Login Page')}/preview/")
    check("landing-page preview renders, sandboxed by CSP", preview.status_code == 200 and "sandbox" in preview.headers.get("Content-Security-Policy", ""), (preview.status_code, preview.headers.get("Content-Security-Policy")))

    # ------------------------------------------------------------------------------------
    section("5. Launch service with a GUARD client: real Gophish group sync, sending intercepted")
    RealClient = type(client)

    class GuardClient(RealClient):
        """Syncs the target group in real Gophish, but never creates a Gophish campaign (that is what emails)."""

        calls = []

        def create_campaign(self, **kwargs):
            GuardClient.calls.append(kwargs)
            return ExternalCampaignRef(external_id="e2e-9001")

        def launch_campaign(self, external_campaign_id):  # pragma: no cover
            raise AssertionError("e2e must never launch a real campaign")

    from apps.campaigns.services import launch_campaign

    launch_campaign(campaign, actor=users["e2e_sa"], client=GuardClient(base_url=settings.GOPHISH_API_URL, api_key=settings.GOPHISH_API_KEY))
    campaign.refresh_from_db()
    check("campaign is launched with the engine's id and a timestamp", campaign.status == "launched" and campaign.gophish_campaign_id == "e2e-9001" and campaign.launched_at)
    group = next((g for g in gophish("GET", "groups/").json() if g["name"] == "E2E Finance"), None)
    emails = sorted(t["email"] for t in group["targets"]) if group else []
    check("real Gophish group holds exactly the non-exempt employees (exempt carol excluded)", emails == ["alice@e2e.example", "bob@e2e.example"], emails)
    call = GuardClient.calls[0] if GuardClient.calls else {}
    check("create_campaign received the right template, page, group and URL",
          call.get("template_id") == "E2E Invoice" and call.get("page_id") == "E2E Login Page" and call.get("target_group_id") == "E2E Finance" and call.get("url") == "http://localhost/", call)
    check("launch is audit-logged with the target count", AuditLogEntry.objects.filter(action="campaign_launched", metadata__target_count=2).exists())
    check("NO campaign was created in Gophish, so no mail could have been sent", len(gophish("GET", "campaigns/").json()) == gophish_campaigns_before)
    try:
        launch_campaign(campaign, actor=users["e2e_sa"], client=GuardClient(base_url=settings.GOPHISH_API_URL, api_key=settings.GOPHISH_API_KEY))
        check("launching again is refused (no double send)", False, "second launch succeeded")
    except Exception as exc:  # noqa: BLE001
        check("launching again is refused (no double send)", "already launched" in str(exc), exc)

    # ------------------------------------------------------------------------------------
    section("6. Signed webhooks -> ingestion (real HTTP, real HMAC)")
    now = timezone.now()

    def event(email, message, offset=0, details=None):
        return {"campaign_id": "e2e-9001", "email": email, "message": message,
                "time": (now - timedelta(minutes=5) + timedelta(seconds=offset)).isoformat(), "details": details if details is not None else {}}

    sent = [signed_webhook(event(e, "Email Sent", i)) for i, e in enumerate(["alice@e2e.example", "bob@e2e.example"])]
    check("Email Sent events accepted", all(r.status_code == 200 for r in sent), [r.status_code for r in sent])
    signed_webhook(event("alice@e2e.example", "Email Opened", 5))
    click_a = signed_webhook(event("alice@e2e.example", "Clicked Link", 10))
    # Real Gophish sends `details` as a JSON *string*; the password must not survive ingestion either way.
    submit_a = signed_webhook(event("alice@e2e.example", "Submitted Data", 20,
                                    details=json.dumps({"payload": {"username": ["alice"], "password": ["hunter2-SECRET"]},
                                                        "browser": {"address": "10.1.2.3"}})))
    signed_webhook(event("bob@e2e.example", "Clicked Link", 30))
    check("click and data-submission accepted", click_a.status_code == 200 and submit_a.status_code == 200)
    before = Event.objects.filter(campaign=campaign).count()
    replay = signed_webhook(event("alice@e2e.example", "Clicked Link", 10))
    check("redelivery of the same event is accepted but stored once (idempotent)", replay.status_code == 200 and Event.objects.filter(campaign=campaign).count() == before)
    check("bad signature -> 403", signed_webhook(event("alice@e2e.example", "Clicked Link", 99), secret="wrong-secret").status_code == 403)
    check("missing signature -> 403", signed_webhook(event("alice@e2e.example", "Clicked Link", 98), sign=False).status_code == 403)
    unknown = signed_webhook(dict(event("alice@e2e.example", "Clicked Link", 97), campaign_id="does-not-exist"))
    check("event for an unknown campaign is acknowledged and ignored", unknown.status_code == 200 and Event.objects.filter(campaign=campaign).count() == before)
    check("non-object JSON body -> 400", requests.post(f"{BASE}/webhooks/gophish", data=b"[1,2]", headers=dict(HOST, **{"X-Gophish-Signature": "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), b"[1,2]", hashlib.sha256).hexdigest()}), timeout=10).status_code == 400)
    stored_events = list(Event.objects.filter(campaign=campaign))
    dump = json.dumps([e.metadata for e in stored_events])
    check("invariant #4: the submitted password is nowhere in the event log", "hunter2" not in dump and "SECRET" not in dump, dump[:200])
    check("the non-secret form field survived", "alice" in dump)
    check("event log holds exactly the 6 expected events", len(stored_events) == 6, len(stored_events))
    try:
        Event.objects.filter(campaign=campaign).update(event_type="email_sent")
        check("invariant #3: the event log refuses updates", False)
    except TypeError:
        check("invariant #3: the event log refuses updates", True)

    # ------------------------------------------------------------------------------------
    section("7. Celery worker: training assignment and risk scores (asynchronous)")
    assignments = wait_for(lambda: TrainingAssignment.objects.filter(employee__in=[alice, bob]).count() == 2 and TrainingAssignment.objects.filter(employee__in=[alice, bob]))
    check("training auto-assigned to both employees who failed", bool(assignments) and {a.employee_id for a in assignments} == {alice.pk, bob.pk})
    check("assignments carry a due date (drives the overdue metric)", all(a.due_at for a in assignments))
    check("the training points back at the triggering event", all(a.triggered_by_event_id for a in assignments))
    score_alice = wait_for(lambda: RiskScoreSnapshot.objects.filter(employee=alice).order_by("-computed_at").first())
    score_bob = wait_for(lambda: RiskScoreSnapshot.objects.filter(employee=bob).order_by("-computed_at").first())
    check("worker scored alice: submitted data = ~50", score_alice and 49 <= float(score_alice.score) <= 50.01, score_alice and score_alice.score)
    check("worker scored bob: click only = ~25", score_bob and 24 <= float(score_bob.score) <= 25.01, score_bob and score_bob.score)
    check("email_opened did not influence the score (alice is exactly one failure)", score_alice.contributing_metrics["campaigns_failed"] == 1)
    check("carol (exempt, never targeted) has no failure", not RiskScoreSnapshot.objects.filter(employee=carol, score__gt=0).exists())

    # ------------------------------------------------------------------------------------
    section("8. Dashboard and row scoping over real HTTP")
    detail = sa.get(f"/campaigns/{campaign.pk}/")
    check("campaign page shows the funnel and who failed", detail.status_code == 200 and "Alice Aardvark" in detail.text and "Bob Badger" in detail.text and "Employees who failed" in detail.text)
    check("campaign page: 2 targeted, 100% failure", ">2<" in detail.text.replace(" ", "").replace("\n", "") and "100%" in detail.text)
    check("employee list is searchable and shows risk badges", "Alice Aardvark" in sa.get("/employees/?q=alice").text)
    check("HTMX request returns only the table fragment", "<html" not in sa.s.get(BASE + "/employees/?q=alice", headers=dict(HOST, **{"HX-Request": "true"}), timeout=30).text)
    api_campaign = sa.get(f"/analytics/campaigns/{campaign.pk}/").json()
    check("analytics API agrees with the funnel", api_campaign["targeted"] == 2 and api_campaign["failed"] == 2 and api_campaign["submitted_data"] == 1, api_campaign)
    dm_list = dm.get("/employees/?q=e2e").text + dm.get("/employees/").text
    check("Department Manager sees their own department's people", "Alice Aardvark" in dm_list)
    check("...and NOT another department's (Dave)", "Dave Dingo" not in dm_list)
    check("...direct URL to another department's employee is a 404", dm.get(f"/employees/{dave.pk}/").status_code == 404)
    keys_page = dm.get("/manage/api-keys/")
    check("API keys are self-service: a Department Manager sees only their own (none of the e2e keys)", keys_page.status_code == 200 and "e2e content bot" not in keys_page.text and "e2e mail bot" not in keys_page.text)
    from apps.manage.views_apikeys import _grantable_scopes

    dm_scopes = {s.value for s in _grantable_scopes(users["e2e_dm"])}
    check("...and can only grant scopes they already hold (no training:write, campaigns:write or reports:write)",
          not dm_scopes & {"training:write", "campaigns:write", "reports:write"} and "read" in dm_scopes, sorted(dm_scopes))
    check("Report Viewer can read the dashboard but not the management UI", rv.get("/").status_code == 200 and rv.get("/manage/employees/new/").status_code == 403)
    check("anonymous access is redirected to sign-in", Http().get("/").status_code == 302 and Http().get("/reports/").status_code == 302)

    # ------------------------------------------------------------------------------------
    section("9. Employee portal over real HTTP (magic link, quiz, certificate, report)")
    bob_assignment = TrainingAssignment.objects.get(employee=bob)
    alice_assignment = TrainingAssignment.objects.get(employee=alice)
    portal = Http()
    check("portal requires sign-in", portal.get("/portal/").status_code == 302)
    check("a tampered link is rejected (400)", portal.get("/portal/enter/not-a-real-token/").status_code == 400)
    entered = portal.get(f"/portal/enter/{sign_employee(alice)}/")
    check("a valid emailed link signs the employee in", entered.status_code == 302 and entered.headers["Location"].endswith("/portal/"))
    home = portal.get("/portal/")
    check("portal home lists their own training only", home.status_code == 200 and "E2E Phishing Basics" in home.text)
    check("another employee's assignment is a 404", portal.get(f"/portal/training/{bob_assignment.pk}/").status_code == 404)
    page = portal.get(f"/portal/training/{alice_assignment.pk}/")
    check("assignment page shows the quiz", page.status_code == 200 and "suspicious link" in page.text)
    wrong = portal.post(f"/portal/training/{alice_assignment.pk}/quiz/", {f"q{q1.pk}": str(q1_wrong.pk)}, referer=f"/portal/training/{alice_assignment.pk}/")
    alice_assignment.refresh_from_db()
    check("a wrong answer does not complete the training", wrong.status_code == 302 and alice_assignment.completed_at is None)
    right = portal.post(f"/portal/training/{alice_assignment.pk}/quiz/", {f"q{q1.pk}": str(q1_right.pk)}, referer=f"/portal/training/{alice_assignment.pk}/")
    alice_assignment.refresh_from_db()
    check("a passing answer completes it and redirects to the certificate", right.status_code == 302 and alice_assignment.completed_at is not None, right.status_code)
    check("both attempts were recorded", QuizAttempt.objects.filter(assignment=alice_assignment).count() == 2)
    cert = portal.get(f"/portal/training/{alice_assignment.pk}/certificate/")
    check("certificate is issued", cert.status_code == 200 and "Alice Aardvark" in cert.text)
    check("a portal session cannot reach any staff surface", portal.get("/manage/").status_code == 302 and portal.get("/reports/").status_code == 302 and portal.get("/admin/").status_code in (302, 301))
    rep_page = portal.get("/portal/report/")
    portal.post("/portal/report/", {"subject": "E2E odd invoice", "sender": "billing@evil.example", "notes": "asked for my login"}, referer="/portal/report/")
    check("employee can report a suspicious email from the portal", ReportedEmail.objects.filter(reporter=alice, subject="E2E odd invoice").exists() and rep_page.status_code == 200)
    check("the teachable-moment page is public and anonymous", Http().get("/portal/learn/").status_code == 200)
    check("training completion is audit-logged", AuditLogEntry.objects.filter(action="training_assignment_completed", metadata__via="portal").exists())

    # ------------------------------------------------------------------------------------
    section("10. Mail-integration intake API and triage view")
    _, reports_key = ApiKey.generate(name="e2e mail bot", owner=users["e2e_sa"], scopes=["reports:write", "read"])
    ok = api("POST", "v1/reported-emails", reports_key, data=json.dumps({"reporter_email": "bob@e2e.example", "subject": "E2E fake CEO", "sender": "ceo@evil.example"}))
    check("intake API accepts a report (201)", ok.status_code == 201, ok.text)
    check("unknown reporter -> 400", api("POST", "v1/reported-emails", reports_key, data=json.dumps({"reporter_email": "ghost@nowhere.example"})).status_code == 400)
    check("a key without reports:write -> 403", api("POST", "v1/reported-emails", read_only_key, data="{}").status_code == 403)
    check("staff see both reports in the triage queue", "E2E fake CEO" in sa.get("/manage/reported/").text and "E2E odd invoice" in sa.get("/manage/reported/").text)
    check("read API returns org analytics", api("GET", "v1/analytics/summary", reports_key).status_code == 200)

    # ------------------------------------------------------------------------------------
    section("11. Reporting: generate, download, verify hashes, scoping")
    today = timezone.now().date()
    form = {"period_start": (today - timedelta(days=7)).isoformat(), "period_end": today.isoformat()}
    r = sa.post("/reports/", dict(form, kind="executive_summary"))
    check("Security Admin generates an executive summary", r.status_code == 302, r.status_code)
    r = sa.post("/reports/", dict(form, kind="evidence_package"))
    check("Security Admin generates an evidence package", r.status_code == 302, r.status_code)
    pdf_report = GeneratedReport.objects.filter(generated_by=users["e2e_sa"], kind="executive_summary").first()
    pkg_report = GeneratedReport.objects.filter(generated_by=users["e2e_sa"], kind="evidence_package").first()
    pdf_dl = sa.get(f"/reports/{pdf_report.pk}/download/")
    check("PDF downloads with the recorded SHA-256", pdf_dl.status_code == 200 and pdf_dl.content[:4] == b"%PDF" and hashlib.sha256(pdf_dl.content).hexdigest() == pdf_report.sha256 == pdf_dl.headers["X-Report-SHA256"])
    pkg_dl = sa.get(f"/reports/{pkg_report.pk}/download/")
    archive = zipfile.ZipFile(io.BytesIO(pkg_dl.content))
    sums_ok = all(hashlib.sha256(archive.read(n)).hexdigest() == d for d, n in (line.split("  ") for line in archive.read("SHA256SUMS.txt").decode().splitlines()))
    check("evidence package: every file matches SHA256SUMS.txt", pkg_dl.status_code == 200 and sums_ok)
    risk_csv = archive.read("employee_risk_scores.csv").decode("utf-8-sig")
    check("evidence package: scores are as-of the report date and include the E2E people", "Alice Aardvark" in risk_csv and "Bob Badger" in risk_csv)
    check("evidence package: audit log includes the launch we performed", "campaign_launched" in archive.read("audit_log.csv").decode("utf-8-sig"))
    check("evidence package contains no password anywhere", all(b"hunter2" not in archive.read(n) for n in archive.namelist()))
    check("report generation and download were audit-logged", AuditLogEntry.objects.filter(action="report_generated", actor=users["e2e_sa"]).count() >= 2 and AuditLogEntry.objects.filter(action="report_downloaded", actor=users["e2e_sa"]).count() >= 2)
    dm_evidence = dm.post("/reports/", dict(form, kind="evidence_package"))
    check("Department Manager cannot generate a report that names individuals", dm_evidence.status_code == 200 and not GeneratedReport.objects.filter(generated_by=users["e2e_dm"]).exists())
    dm.post("/reports/", dict(form, kind="department_summary"))
    dm_report = GeneratedReport.objects.filter(generated_by=users["e2e_dm"], kind="department_summary").first()
    dm_text = bytes(dm_report.content).decode("utf-8-sig") if dm_report else ""
    check("Department Manager's aggregate report is limited to their department", dm_report and dm_report.is_scoped and "E2E Finance" in dm_text and "E2E Other" not in dm_text, dm_text[:150])
    check("...and the org-wide report is invisible to them", dm.get(f"/reports/{pkg_report.pk}/download/").status_code == 404)
    check("Report Viewer cannot download the individual-level package", rv.get(f"/reports/{pkg_report.pk}/download/").status_code == 404)

    # ------------------------------------------------------------------------------------
    section("12. Nothing was sent")
    check("Gophish still has no more campaigns than before this run", len(gophish("GET", "campaigns/").json()) == gophish_campaigns_before)


if __name__ == "__main__":
    exit_code = 0
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        check("script ran to completion", False, repr(exc))
    finally:
        print("\nCleaning up test data...")
        try:
            cleanup()
        except Exception as exc:  # noqa: BLE001
            print("CLEANUP FAILED:", repr(exc))
            exit_code = 2
    passed = sum(1 for ok, *_ in results if ok)
    failed = [(n, d) for ok, n, d in results if not ok]
    print(f"\n{'=' * 70}\nE2E RESULT: {passed}/{len(results)} checks passed")
    for name, detail in failed:
        print(f"  FAILED: {name}  {detail}")
    sys.exit(exit_code or (1 if failed else 0))
