from django.db import models


class TrainingModule(models.Model):
    """
    A single piece of assignable security-awareness training. Content lives
    off-platform for now (e.g. an external LMS/video link) — this isn't a
    content authoring tool, see CLAUDE.md for what's deferred.
    """

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    content_url = models.URLField(help_text="Where the employee actually takes the training (external for now).")
    duration_minutes = models.PositiveIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.title


class Quiz(models.Model):
    module = models.OneToOneField(TrainingModule, on_delete=models.CASCADE, related_name="quiz")
    passing_score_percent = models.PositiveIntegerField(default=80)

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

    def __str__(self):
        return f"{self.employee} — {self.module}"


class QuizAttempt(models.Model):
    assignment = models.ForeignKey(TrainingAssignment, on_delete=models.CASCADE, related_name="quiz_attempts")
    score_percent = models.PositiveIntegerField()
    passed = models.BooleanField()
    completed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.assignment} — {self.score_percent}% ({'passed' if self.passed else 'failed'})"
