"""
Dev-only demo data so the dashboard has something realistic to show: five
departments, ~50 people, five past campaigns, ten weeks of score history and
training assignments. Everything is tagged with @demo.example addresses so
`--reset` can remove it (event rows are append-only, so it deletes with SQL).
Refuses to run unless DEBUG is on — never point this at real data.
"""

import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.employees.models import Department, Employee
from apps.events.models import Event
from apps.risk_scoring.services import compute_and_store_snapshot
from apps.training.models import Quiz, TrainingAssignment, TrainingModule

DOMAIN = "demo.example"
DEPARTMENTS = ["Finance", "Engineering", "Sales", "People Ops", "Operations"]
FIRST = ["Ava", "Noah", "Mia", "Liam", "Zoe", "Ethan", "Lena", "Omar", "Priya", "Marcus", "Sofia", "Kenji", "Amara", "Diego", "Nora"]
LAST = ["Chen", "Okafor", "Silva", "Novak", "Haddad", "Brooks", "Tanaka", "Ivanov", "Mensah", "Larsen", "Reyes", "Kaur", "Duval"]
CAMPAIGNS = [
    ("Q3 Payroll Update", 70),
    ("Shared Document Alert", 56),
    ("Parcel Delivery Notice", 40),
    ("Password Expiry Notice", 21),
    ("Benefits Enrolment Reminder", 9),
]


class Command(BaseCommand):
    help = "Create (or --reset) demo data for the dashboard. Development only."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Remove all demo data instead of creating it.")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo_data only runs with DEBUG on — refusing to touch a real deployment.")
        with transaction.atomic():
            self._reset()
            if not options["reset"]:
                self._seed()
        self.stdout.write(self.style.SUCCESS("Demo data reset." if options["reset"] else "Demo data created."))

    # -- removal -------------------------------------------------------------

    def _reset(self):
        # Only module constants are interpolated below (never user input), hence the noqa markers.
        emp = f"(SELECT id FROM employees_employee WHERE email LIKE '%@{DOMAIN}')"
        camp = "(SELECT id FROM campaigns_campaign WHERE gophish_campaign_id LIKE 'demo-%')"
        statements = [
            f"DELETE FROM risk_scoring_riskscoresnapshot WHERE employee_id IN {emp}",
            f"DELETE FROM training_quizattempt WHERE assignment_id IN (SELECT id FROM training_trainingassignment WHERE employee_id IN {emp})",
            f"DELETE FROM training_trainingassignment WHERE employee_id IN {emp}",
            f"DELETE FROM events_event WHERE employee_id IN {emp} OR campaign_id IN {camp}",
        ]
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)  # noqa: S608 — statements built from constants above
        Campaign.objects.filter(gophish_campaign_id__startswith="demo-").delete()
        Employee.objects.filter(email__endswith=f"@{DOMAIN}").delete()
        Department.objects.filter(name__in=DEPARTMENTS, employees__isnull=True).delete()
        TrainingModule.objects.filter(title="Spotting Phishing Emails").delete()

    # -- creation ------------------------------------------------------------

    def _seed(self):
        rng = random.Random(7)
        now = timezone.now()
        module = TrainingModule.objects.create(
            title="Spotting Phishing Emails", content_url="https://training.example.com/phishing-101", duration_minutes=15
        )
        Quiz.objects.create(module=module, passing_score_percent=80)

        people_by_department = {}
        used = set()
        for name in DEPARTMENTS:
            department, _ = Department.objects.get_or_create(name=name)
            people = []
            for _ in range(rng.randint(8, 12)):
                while True:
                    first, last = rng.choice(FIRST), rng.choice(LAST)
                    if (first, last) not in used:
                        used.add((first, last))
                        break
                people.append(
                    Employee.objects.create(
                        email=f"{first}.{last}@{DOMAIN}".lower(),
                        full_name=f"{first} {last}",
                        department=department,
                        is_exempt=rng.random() < 0.04,
                    )
                )
            people_by_department[department] = people

        everyone_created = [p.pk for people in people_by_department.values() for p in people]
        Employee.objects.filter(pk__in=everyone_created).update(created_at=now - timedelta(days=120))

        departments = list(people_by_department)
        events, launches = [], []
        for index, (name, days_ago) in enumerate(CAMPAIGNS):
            department = departments[index % len(departments)]
            launched_at = now - timedelta(days=days_ago)
            campaign = Campaign.objects.create(
                name=name,
                gophish_campaign_id=f"demo-{index + 1}",
                template_name="Demo template",
                landing_page_name="Demo page",
                landing_page_url="https://phish.example.com/demo",
                target_department=department,
                training_module=module,
                status=Campaign.Status.LAUNCHED,
                launched_at=launched_at,
            )
            launches.append((campaign, launched_at))
            # Riskier departments fail more, so the demo has visible contrast.
            click_odds = 0.3 + 0.12 * (index % 3)
            for person in people_by_department[department]:
                if person.is_exempt:
                    continue
                t = launched_at
                stamps = [(Event.EventType.EMAIL_SENT, 0), (Event.EventType.EMAIL_DELIVERED, 1)]
                if rng.random() < 0.7:
                    stamps.append((Event.EventType.EMAIL_OPENED, rng.randint(5, 240)))
                if rng.random() < click_odds:
                    stamps.append((Event.EventType.LINK_CLICKED, rng.randint(10, 400)))
                    if rng.random() < 0.5:
                        stamps.append((Event.EventType.CREDENTIAL_ATTEMPT, rng.randint(11, 420)))
                elif rng.random() < 0.22:
                    stamps.append((Event.EventType.PHISHING_REPORTED, rng.randint(10, 300)))
                for event_type, minutes in stamps:
                    when = t + timedelta(minutes=minutes)
                    events.append(
                        Event(
                            event_type=event_type, employee=person, campaign=campaign, source=Event.Source.MANUAL,
                            occurred_at=when, metadata={},
                        )
                    )
        Event.objects.bulk_create(events)  # bulk_create: no signals, so no stray live recomputes

        # Training for everyone who failed, some already finished.
        failed = {(e.employee_id, e.campaign_id): e for e in events if e.event_type in (Event.EventType.LINK_CLICKED, Event.EventType.CREDENTIAL_ATTEMPT)}
        for (employee_id, _campaign_id), event in failed.items():
            assigned = event.occurred_at + timedelta(minutes=1)
            done = rng.random() < 0.55
            assignment = TrainingAssignment.objects.create(employee_id=employee_id, module=module, due_at=assigned + timedelta(days=14))
            updates = {"assigned_at": assigned}
            if done:
                updates["started_at"] = assigned + timedelta(days=2)
                updates["completed_at"] = assigned + timedelta(days=rng.randint(3, 10))
            TrainingAssignment.objects.filter(pk=assignment.pk).update(**updates)

        # Ten weeks of weekly snapshots, backdated so the trend charts have a history.
        everyone = [p for people in people_by_department.values() for p in people]
        with connection.cursor() as cursor:
            for weeks_ago in range(10, -1, -1):
                moment = now - timedelta(weeks=weeks_ago)
                for person in everyone:
                    snapshot = compute_and_store_snapshot(person, now=moment)
                    cursor.execute(
                        "UPDATE risk_scoring_riskscoresnapshot SET computed_at = %s WHERE id = %s", [moment, snapshot.pk]
                    )
