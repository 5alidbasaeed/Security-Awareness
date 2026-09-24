from django.contrib import admin
from django.utils import timezone

from apps.core.admin_mixins import AuditedAdminMixin
from apps.core.audit import log_action

from .models import (
    Quiz,
    QuizAttempt,
    QuizChoice,
    QuizQuestion,
    TrainingAssignment,
    TrainingModule,
    TrainingPolicy,
)


@admin.register(TrainingModule)
class TrainingModuleAdmin(AuditedAdminMixin, admin.ModelAdmin):
    audit_object_name = "training_module"
    list_display = ("title", "duration_minutes", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title",)


@admin.register(Quiz)
class QuizAdmin(AuditedAdminMixin, admin.ModelAdmin):
    audit_object_name = "quiz"
    list_display = ("module", "passing_score_percent")


class QuizChoiceInline(admin.TabularInline):
    model = QuizChoice
    extra = 2


@admin.register(QuizQuestion)
class QuizQuestionAdmin(AuditedAdminMixin, admin.ModelAdmin):
    audit_object_name = "quiz_question"
    list_display = ("quiz", "text", "order")
    list_filter = ("quiz",)
    inlines = [QuizChoiceInline]


@admin.register(TrainingAssignment)
class TrainingAssignmentAdmin(AuditedAdminMixin, admin.ModelAdmin):
    audit_object_name = "training_assignment"
    list_display = ("employee", "module", "assigned_at", "started_at", "completed_at", "triggered_by_event")
    list_filter = ("module",)
    search_fields = ("employee__email", "employee__full_name")
    actions = ["mark_started"]

    @admin.action(description="Mark selected assignments as started")
    def mark_started(self, request, queryset):
        # Bulk .update() bypasses save_model, so AuditedAdminMixin's
        # automatic logging doesn't fire here — log explicitly, same pattern
        # as CampaignAdmin.launch_campaign.
        to_update = list(queryset.filter(started_at__isnull=True))
        updated = queryset.filter(started_at__isnull=True).update(started_at=timezone.now())
        for assignment in to_update:
            log_action(actor=request.user, action="training_assignment_started", target_description=str(assignment))
        self.message_user(request, f"Marked {updated} assignment(s) as started.")


@admin.register(QuizAttempt)
class QuizAttemptAdmin(AuditedAdminMixin, admin.ModelAdmin):
    """
    Staff-recorded for now — there's no employee-facing quiz submission flow
    yet (needs the pending auth/SSO decision + custom dashboard). Recording
    a passing attempt here marks the assignment complete.
    """

    audit_object_name = "quiz_attempt"
    list_display = ("assignment", "score_percent", "passed", "completed_at")
    list_filter = ("passed",)

    def save_model(self, request, obj, form, change):
        # The score is the fact; "passed" must agree with the quiz's threshold
        # rather than trusting a checkbox that can contradict it.
        quiz = getattr(obj.assignment.module, "quiz", None)
        if quiz is not None:
            obj.passed = obj.score_percent >= quiz.passing_score_percent
        super().save_model(request, obj, form, change)
        if obj.passed and obj.assignment.completed_at is None:
            obj.assignment.completed_at = timezone.now()
            obj.assignment.save(update_fields=["completed_at"])
            log_action(
                actor=request.user,
                action="training_assignment_completed",
                target_description=str(obj.assignment),
            )


@admin.register(TrainingPolicy)
class TrainingPolicyAdmin(AuditedAdminMixin, admin.ModelAdmin):
    audit_object_name = "training_policy"
    list_display = ("name", "module", "department", "repeat_every_days", "due_days", "is_active")
    list_filter = ("is_active", "department")
