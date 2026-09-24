"""Slide-based courses: the deck is shown in the portal, the quiz follows it, and the content is escaped."""

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.urls import reverse

from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.employees.tests.factories import EmployeeFactory
from apps.portal.templatetags.course_extras import slide_body
from apps.portal.tests.test_portal import quiz_for, sign_in
from apps.training.models import TrainingSlide
from apps.training.tests.factories import TrainingAssignmentFactory

pytestmark = pytest.mark.django_db


def deck(module, count=3):
    return [TrainingSlide.objects.create(module=module, order=i, title=f"Slide {i + 1}", body="- point one\n- point two",
                                         callout="Remember this") for i in range(count)]


def assignment_with_course(client, slides=3):
    employee = EmployeeFactory()
    item = TrainingAssignmentFactory(employee=employee)
    deck(item.module, slides)
    sign_in(client, employee)
    return item


def test_slides_render_in_order_with_progress_and_navigation(client):
    item = assignment_with_course(client)

    first = client.get(reverse("portal:course", args=[item.pk]))
    assert b"Slide 1 of 3" in first.content and b"Slide 1</h2>" in first.content and b"Remember this" in first.content
    assert b'id="slide-prev"' not in first.content and b'id="slide-next"' in first.content

    last = client.get(reverse("portal:course", args=[item.pk]) + "?s=3")
    assert b"Slide 3 of 3" in last.content and b'id="slide-next"' not in last.content


def test_the_last_slide_leads_straight_into_the_quiz(client):
    item = assignment_with_course(client)
    quiz_for(item.module)

    last = client.get(reverse("portal:course", args=[item.pk]) + "?s=3")

    assert reverse("portal:submit-quiz", args=[item.pk]).encode() in last.content
    assert b"Continue to the quiz" in last.content


def test_opening_the_course_marks_the_assignment_started(client):
    item = assignment_with_course(client)
    assert item.started_at is None

    client.get(reverse("portal:course", args=[item.pk]))

    item.refresh_from_db()
    assert item.started_at is not None


def test_out_of_range_or_junk_slide_numbers_are_clamped_not_errors(client):
    item = assignment_with_course(client)
    url = reverse("portal:course", args=[item.pk])

    assert b"Slide 3 of 3" in client.get(url + "?s=99").content
    assert b"Slide 1 of 3" in client.get(url + "?s=-4").content
    assert b"Slide 1 of 3" in client.get(url + "?s=abc").content


def test_the_quiz_needs_the_course_first_and_then_completes_the_assignment(client):
    item = assignment_with_course(client)
    _, right, _ = quiz_for(item.module)
    quiz_url = reverse("portal:submit-quiz", args=[item.pk])

    assert client.get(quiz_url).url == reverse("portal:course", args=[item.pk])  # not started: course first

    client.get(reverse("portal:course", args=[item.pk]))
    assert client.get(quiz_url).status_code == 200

    question_id = item.module.quiz.questions.get().pk
    passed = client.post(quiz_url, {f"q{question_id}": right.pk})
    item.refresh_from_db()
    assert passed.url == reverse("portal:certificate", args=[item.pk]) and item.completed_at is not None


def test_a_failed_quiz_sends_you_back_to_the_quiz(client):
    item = assignment_with_course(client)
    _, _, wrong = quiz_for(item.module)
    client.get(reverse("portal:course", args=[item.pk]))

    response = client.post(reverse("portal:submit-quiz", args=[item.pk]), {f"q{item.module.quiz.questions.get().pk}": wrong.pk})

    assert response.url == reverse("portal:submit-quiz", args=[item.pk])


def test_someone_elses_course_is_a_404(client):
    item = TrainingAssignmentFactory()
    deck(item.module)
    sign_in(client, EmployeeFactory())

    assert client.get(reverse("portal:course", args=[item.pk])).status_code == 404


def test_a_module_without_slides_falls_back_to_the_assignment_page(client):
    employee = EmployeeFactory()
    item = TrainingAssignmentFactory(employee=employee)
    sign_in(client, employee)

    assert client.get(reverse("portal:course", args=[item.pk])).url == reverse("portal:assignment", args=[item.pk])


def test_slide_text_is_escaped_so_authors_cannot_inject_markup():
    html = slide_body('<script>alert(1)</script>\n\n- <img src=x onerror=alert(1)>')

    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html and "<ul><li>" in html


def test_body_renders_paragraphs_and_bullets():
    html = slide_body("First line\ncontinues here\n\n- one\n- two\n\nAfter list")

    assert html == "<p>First line continues here</p><ul><li>one</li><li>two</li></ul><p>After list</p>"


# --- staff side --------------------------------------------------------------------------


def staff(client, django_user_model, group="Training Manager"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username="t", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)


def test_a_training_manager_can_add_reorder_and_remove_slides(client, django_user_model):
    staff(client, django_user_model)
    module = TrainingAssignmentFactory().module
    slides = deck(module, 2)

    client.post(reverse("manage:slide-new", args=[module.pk]), {"title": "Third", "body": "hi", "callout": ""})
    assert list(module.slides.values_list("title", flat=True)) == ["Slide 1", "Slide 2", "Third"]

    client.post(reverse("manage:slide-move", args=[module.pk, slides[1].pk, "up"]))
    assert list(module.slides.values_list("title", flat=True)) == ["Slide 2", "Slide 1", "Third"]

    client.post(reverse("manage:slide-delete", args=[module.pk, slides[0].pk]))
    assert list(module.slides.values_list("order", flat=True)) == [0, 1]  # renumbered, no gaps


def test_report_viewers_cannot_edit_slides(client, django_user_model):
    staff(client, django_user_model, group="Report Viewer")
    module = TrainingAssignmentFactory().module

    response = client.post(reverse("manage:slide-new", args=[module.pk]), {"title": "x", "body": "", "callout": ""})

    assert response.status_code == 403 and not module.slides.exists()


def test_staff_can_preview_the_course_and_try_the_quiz_without_saving_anything(client, django_user_model):
    staff(client, django_user_model)
    module = TrainingAssignmentFactory().module
    deck(module, 2)
    _, right, _ = quiz_for(module)
    question = module.quiz.questions.get()

    page = client.get(reverse("manage:module-preview", args=[module.pk]))
    assert page.status_code == 200 and b"nothing is recorded" in page.content

    result = client.post(reverse("manage:module-preview-quiz", args=[module.pk]), {f"q{question.pk}": right.pk})
    assert b"100%" in result.content
    assert not module.quiz.questions.get().choices.filter(pk=right.pk).exclude(is_correct=True).exists()
    from apps.training.models import QuizAttempt

    assert QuizAttempt.objects.count() == 0


def test_sample_content_seeds_courses_and_quizzes_and_is_idempotent(settings, monkeypatch, tmp_path):
    settings.EMAIL_IMAGE_ROOT = str(tmp_path)
    from apps.engine.tests.fakes import FakePhishingEngineClient

    monkeypatch.setattr("apps.core.management.commands.seed_sample_content.get_client", lambda: FakePhishingEngineClient())
    from apps.campaigns.models import Campaign
    from apps.training.models import TrainingModule

    settings.DEBUG = False
    call_command("seed_sample_content", verbosity=0)
    call_command("seed_sample_content", verbosity=0)

    modules = TrainingModule.objects.all()
    assert modules.count() == 4
    for module in modules:
        assert module.slides.count() >= 4 and module.quiz.questions.count() >= 3
        for question in module.quiz.questions.all():
            assert question.choices.filter(is_correct=True).count() == 1
    assert Campaign.objects.filter(name__startswith="Sample:").count() == 4
    assert not Campaign.objects.filter(status=Campaign.Status.LAUNCHED).exists()  # nothing launched
