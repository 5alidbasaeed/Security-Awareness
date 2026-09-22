from django.contrib import admin
from django.utils import timezone

from .models import Quiz, QuizAttempt, QuizChoice, QuizQuestion, TrainingAssignment, TrainingModule


@admin.register(TrainingModule)
class TrainingModuleAdmin(admin.ModelAdmin):
    list_display = ("title", "duration_minutes", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title",)


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("module", "passing_score_percent")


class QuizChoiceInline(admin.TabularInline):
    model = QuizChoice
    extra = 2


@admin.register(QuizQuestion)
class QuizQuestionAdmin(admin.ModelAdmin):
    list_display = ("quiz", "text", "order")
    list_filter = ("quiz",)
    inlines = [QuizChoiceInline]


@admin.register(TrainingAssignment)
class TrainingAssignmentAdmin(admin.ModelAdmin):
    list_display = ("employee", "module", "assigned_at", "started_at", "completed_at", "triggered_by_event")
    list_filter = ("module",)
    search_fields = ("employee__email", "employee__full_name")
    actions = ["mark_started"]

    @admin.action(description="Mark selected assignments as started")
    def mark_started(self, request, queryset):
        updated = queryset.filter(started_at__isnull=True).update(started_at=timezone.now())
        self.message_user(request, f"Marked {updated} assignment(s) as started.")


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    """
    Staff-recorded for now — there's no employee-facing quiz submission flow
    yet (needs the pending auth/SSO decision + custom dashboard). Recording
    a passing attempt here marks the assignment complete.
    """

    list_display = ("assignment", "score_percent", "passed", "completed_at")
    list_filter = ("passed",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.passed and obj.assignment.completed_at is None:
            obj.assignment.completed_at = timezone.now()
            obj.assignment.save(update_fields=["completed_at"])
