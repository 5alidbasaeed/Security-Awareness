from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class TrainingModule(models.Model):
    """
    A single piece of assignable security-awareness training. The course itself is a deck of
    TrainingSlides shown in the portal, followed directly by the quiz. `content_url` is
    optional and only for modules whose content still lives elsewhere (an external LMS/video).
    """

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    content_url = models.URLField(
        blank=True, help_text="Optional. Only for training hosted elsewhere; leave blank when the module has slides."
    )
    duration_minutes = models.PositiveIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.title


class TrainingSlide(models.Model):
    """
    One slide of a module's course. Plain text only (no HTML): the body is paragraphs
    separated by blank lines, and lines starting with "- " become bullets. `callout` is an
    optional highlighted takeaway shown under the body.
    """

    module = models.ForeignKey(TrainingModule, on_delete=models.CASCADE, related_name="slides")
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, help_text='Blank line = new paragraph. Start a line with "- " for a bullet.')
    callout = models.CharField(max_length=300, blank=True, help_text="Optional highlighted takeaway.")

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.module}: {self.title}"


class Quiz(models.Model):
    module = models.OneToOneField(TrainingModule, on_delete=models.CASCADE, related_name="quiz")
    passing_score_percent = models.PositiveIntegerField(default=80)

    class Meta:
        verbose_name_plural = "quizzes"

    def __str__(self):
        return f"Quiz for {self.module}"


class QuizQuestion(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text[:80]


class QuizChoice(models.Model):
    question = models.ForeignKey(QuizQuestion, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=300)
    is_correct = models.BooleanField(default=False)

    def __str__(self):
        return self.text


class TrainingAssignment(models.Model):
    """
    One employee's assignment of one module. Created automatically on a
    simulation failure (see signals.py) or manually by staff via the admin.
    """

    employee = models.ForeignKey("employees.Employee", on_delete=models.CASCADE, related_name="training_assignments")
    module = models.ForeignKey(TrainingModule, on_delete=models.PROTECT, related_name="assignments")
    assigned_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_reminded_at = models.DateTimeField(null=True, blank=True)
    waived_at = models.DateTimeField(
        null=True, blank=True,
        help_text="A documented exception: the assignment no longer counts as outstanding or overdue, and is never counted as completed.",
    )
    waived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    waiver_reason = models.CharField(max_length=300, blank=True)
    triggered_by_event = models.ForeignKey(
        "events.Event",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="training_assignments",
        help_text="The simulation-failure event that triggered this assignment, if auto-created.",
    )

    class Meta:
        indexes = [models.Index(fields=["employee", "module"])]

    def save(self, *args, **kwargs):
        # Without a due date the "overdue" compliance metric can never fire.
        if self._state.adding and self.due_at is None:
            self.due_at = timezone.now() + timedelta(days=settings.TRAINING_DUE_DAYS)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} — {self.module}"


class TrainingExtension(models.Model):
    """
    One recorded extension of an assignment's due date. Append-only: it is the evidence that a
    deadline moved, why, and who allowed it, and it lets a past report be reproduced with the due
    date that applied at the time instead of one rewritten later.
    """

    assignment = models.ForeignKey(TrainingAssignment, on_delete=models.CASCADE, related_name="extensions")
    previous_due_at = models.DateTimeField(null=True, blank=True)
    new_due_at = models.DateTimeField()
    reason = models.CharField(max_length=300)
    extended_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    extended_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["extended_at", "pk"]

    def __str__(self):
        return f"{self.assignment} - due {self.new_due_at:%Y-%m-%d}"


class QuizAttempt(models.Model):
    assignment = models.ForeignKey(TrainingAssignment, on_delete=models.CASCADE, related_name="quiz_attempts")
    score_percent = models.PositiveIntegerField()
    passed = models.BooleanField()
    completed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.assignment} — {self.score_percent}% ({'passed' if self.passed else 'failed'})"


class TrainingPolicy(models.Model):
    """
    Mandatory training that isn't triggered by a failed simulation — e.g. annual
    awareness training for everyone, or onboarding training for new hires. A daily
    task (tasks.enforce_training_policies) enrols every active employee in scope who
    hasn't had this module assigned within `repeat_every_days`.
    """

    name = models.CharField(max_length=200)
    module = models.ForeignKey(TrainingModule, on_delete=models.PROTECT, related_name="policies")
    department = models.ForeignKey(
        "employees.Department", on_delete=models.CASCADE, null=True, blank=True, related_name="training_policies",
        help_text="Blank = everyone.",
    )
    repeat_every_days = models.PositiveIntegerField(default=365, help_text="Re-enrol after this many days (365 = annual).")
    due_days = models.PositiveIntegerField(default=30, help_text="Days each person has to complete it.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "training policies"

    def __str__(self):
        return self.name
