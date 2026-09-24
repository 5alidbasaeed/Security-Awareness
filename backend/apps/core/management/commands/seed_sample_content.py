"""
Create a sample content library: slide-based training courses with quizzes, simulated phishing
emails and landing pages (in the phishing engine), catalog entries, and draft campaigns.

Safe to run repeatedly: anything that already exists is left alone, so edits you make are never
overwritten. Nothing is sent and no campaign is launched: campaigns are created as drafts (one is
waiting for approval so the approval queue has something in it). With DEBUG on it also creates one
sample employee, assigns them the courses, and prints their portal sign-in link.
"""

import struct
import uuid
import zlib

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.campaigns.images import image_root
from apps.campaigns.models import Campaign, CampaignTemplate, EmailDraft, EmailImage, LandingDraft
from apps.core import sample_content as content
from apps.employees.models import Department, Employee
from apps.engine.factory import get_client
from apps.training.models import Quiz, QuizChoice, QuizQuestion, TrainingAssignment, TrainingModule, TrainingSlide

# Where Gophish serves landing pages. Replace with the real phishing domain in production.
PHISH_SERVER_URL = "http://localhost:8080"
SAMPLE_EMAIL = "sample.employee@demo.example"


class Command(BaseCommand):
    help = "Create sample training courses, phishing emails, landing pages and draft campaigns."

    def handle(self, *args, **options):
        modules = self._courses()
        self._engine_content()
        self._catalog_and_campaigns(modules)
        if settings.DEBUG:
            self._sample_employee(modules)
        self.stdout.write(self.style.SUCCESS("Sample content ready."))

    # -- training ----------------------------------------------------------------------

    @transaction.atomic
    def _courses(self):
        modules = {}
        for course in content.COURSES:
            module, _ = TrainingModule.objects.get_or_create(
                title=course["title"],
                defaults={"description": course["description"], "duration_minutes": course["minutes"], "content_url": ""},
            )
            modules[course["title"]] = module
            if module.slides.exists():
                self.stdout.write(f"  course exists: {module.title}")
                continue
            # New, or an existing module with no slides yet (for example from the demo data): fill it in.
            for order, (title, body, callout) in enumerate(course["slides"]):
                TrainingSlide.objects.create(module=module, order=order, title=title, body=body, callout=callout)
            quiz, _ = Quiz.objects.get_or_create(module=module, defaults={"passing_score_percent": course["pass_mark"]})
            if not quiz.questions.exists():
                for order, (text, choices, right) in enumerate(course["questions"]):
                    question = QuizQuestion.objects.create(quiz=quiz, text=text, order=order)
                    for index, choice in enumerate(choices):
                        QuizChoice.objects.create(question=question, text=choice, is_correct=(index == right))
            self.stdout.write(f"  course filled: {module.title} ({len(course['slides'])} slides)")
        return modules

    # -- phishing engine content --------------------------------------------------------

    def _engine_content(self) -> bool:
        banners = {
            "Sample banner (blue)": self._banner("Sample banner (blue)", (11, 95, 255), (96, 165, 250)),
            "Sample banner (teal)": self._banner("Sample banner (teal)", (15, 118, 110), (94, 234, 212)),
        }
        drafts = [self._draft(template, banners) for template in content.EMAIL_TEMPLATES]
        pages = [self._landing(page) for page in content.LANDING_PAGES]
        try:
            client = get_client()
            for page in pages:
                client.upsert_landing_page(
                    name=page.name, html=page.render_html(), capture_credentials=True, redirect_url=page.engine_redirect_url(),
                )
            for draft in drafts:
                client.upsert_email_template(name=draft.name, subject=draft.subject, html=draft.render_html(), text=draft.render_text())
        except Exception as exc:  # noqa: BLE001 — engine down: still seed everything Django owns
            self.stdout.write(self.style.WARNING(f"  Could not reach the phishing engine, skipped emails and landing pages: {exc}"))
            return False
        self.stdout.write(f"  engine: {len(drafts)} emails, {len(pages)} landing pages")
        return True

    @staticmethod
    def _banner(name, top, bottom) -> EmailImage:
        """A generated gradient banner, so there is a picture to see without uploading anything."""
        existing = EmailImage.objects.filter(original_name=name).first()
        if existing:
            return existing
        width, height = 600, 140
        rows = b"".join(
            b"\x00" + bytes(channel for _ in range(width) for channel in (
                round(top[i] + (bottom[i] - top[i]) * y / (height - 1)) for i in range(3)))
            for y in range(height)
        )

        def chunk(kind, data):
            body = kind + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
        stored = f"{uuid.uuid4().hex}.png"
        image_root().mkdir(parents=True, exist_ok=True)
        (image_root() / stored).write_bytes(png)
        return EmailImage.objects.create(stored_name=stored, original_name=name, size=len(png))

    @staticmethod
    def _landing(page) -> LandingDraft:
        """The builder record for a sample landing page; an existing one is left alone."""
        fields = ("layout", "brand_name", "accent_color", "heading", "subtext", "username_label", "ask_password", "button_label", "footer_note")
        draft, _ = LandingDraft.objects.get_or_create(name=page["name"], defaults={f: page[f] for f in fields})
        return draft

    @staticmethod
    def _draft(template, banners) -> EmailDraft:
        """The composed record for a sample email. An existing one is left alone so edits survive a re-run."""
        body = template["body_html"]
        if template.get("hero"):
            banner = banners[template["hero"]]
            body = body.replace("{hero}", f'<img src="{banner.public_url}" alt="" style="max-width:100%;height:auto;border:0">')
        defaults = {"subject": template["subject"], "layout": template["layout"], "brand_name": template["brand_name"], "body_html": body}
        draft, _ = EmailDraft.objects.get_or_create(name=template["name"], defaults=defaults)
        return draft

    # -- catalog and campaigns ----------------------------------------------------------

    @transaction.atomic
    def _catalog_and_campaigns(self, modules):
        for template in content.EMAIL_TEMPLATES:
            CampaignTemplate.objects.get_or_create(
                name=template["name"],
                defaults={
                    "category": template["category"], "difficulty": template["difficulty"],
                    "template_name": template["name"], "landing_page_name": template["landing"],
                    "landing_page_url": PHISH_SERVER_URL,
                },
            )
        for name, email, page, course, status in content.CAMPAIGNS:
            Campaign.objects.get_or_create(
                name=name,
                defaults={
                    "template_name": email, "landing_page_name": page, "landing_page_url": PHISH_SERVER_URL,
                    "training_module": modules[course], "status": status,
                },
            )
        self.stdout.write(f"  catalog: {len(content.EMAIL_TEMPLATES)} entries, campaigns: {len(content.CAMPAIGNS)} (none launched)")

    # -- dev-only sample employee -------------------------------------------------------

    def _sample_employee(self, modules):
        from apps.portal.views import magic_link

        department, _ = Department.objects.get_or_create(name="Sample Team")
        employee, _ = Employee.objects.get_or_create(
            email=SAMPLE_EMAIL, defaults={"full_name": "Sam Sample", "department": department},
        )
        for module in modules.values():
            if not TrainingAssignment.objects.filter(employee=employee, module=module).exists():
                TrainingAssignment.objects.create(employee=employee, module=module)
        self.stdout.write(f"\n  Sample employee: {employee.email}\n  Portal sign-in link (valid for a few days):\n  {magic_link(employee)}\n")
