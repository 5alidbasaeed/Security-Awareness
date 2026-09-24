from django.conf import settings
from django.db import models


class Campaign(models.Model):
    """
    Django-side campaign metadata. `gophish_campaign_id` is a plain
    reference field, never a FK — Gophish's own database is a separate
    system that's never joined against. See CLAUDE.md invariant #2.
    """

    class Status(models.TextChoices):
        DRAFT = "draft"
        PENDING_APPROVAL = "pending_approval"
        APPROVED = "approved"
        LAUNCHED = "launched"

    name = models.CharField(max_length=200)
    gophish_campaign_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    template_name = models.CharField(max_length=200, help_text="Must match an existing Gophish email template name.")
    landing_page_name = models.CharField(
        max_length=200, help_text="Must match an existing Gophish landing page name — distinct from the URL below."
    )
    landing_page_url = models.URLField(help_text="Landing page URL, per Gophish's create-campaign API.")
    target_department = models.ForeignKey(
        "employees.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="campaigns"
    )
    target_smart_group = models.ForeignKey(
        "campaigns.SmartGroup", on_delete=models.SET_NULL, null=True, blank=True, related_name="campaigns",
        help_text="Optional dynamic audience, computed at launch. Overrides the target department when set.",
    )
    training_module = models.ForeignKey(
        "training.TrainingModule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
        help_text="Auto-assigned to an employee who fails this campaign's simulation (clicks or submits data).",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    scheduled_at = models.DateTimeField(
        null=True, blank=True, help_text="If set and the campaign is Approved, launches automatically at this time."
    )
    launched_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="campaigns"
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_campaigns",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_campaigns",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [("approve_campaign", "Can approve a campaign for launch")]

    def __str__(self):
        return self.name


class CampaignTemplate(models.Model):
    """
    A catalog entry over the engine's email template + landing page, tagged with a
    category and difficulty so staff can organize content and rotate it (addresses the
    "content decay" risk — see the plan). It stores the Gophish template/page NAMES
    (invariant #2: no Gophish IDs as FKs), not the content itself.
    """

    class Difficulty(models.TextChoices):
        EASY = "easy", "Easy"
        MEDIUM = "medium", "Medium"
        HARD = "hard", "Hard"

    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True, help_text="e.g. Invoice, Delivery, IT, HR, Credential reset.")
    difficulty = models.CharField(max_length=10, choices=Difficulty.choices, default=Difficulty.MEDIUM)
    template_name = models.CharField(max_length=200, help_text="Gophish email template name.")
    landing_page_name = models.CharField(max_length=200, help_text="Gophish landing page name.")
    landing_page_url = models.URLField()
    times_used = models.PositiveIntegerField(default=0, editable=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_difficulty_display()})"


class SmartGroup(models.Model):
    """
    A dynamic audience computed at launch time, instead of a fixed department. Lets a
    campaign target, say, repeat clickers or new hires. The exempt/inactive filter is
    always applied on top, so a smart group can never reach someone who is exempt.
    """

    class Rule(models.TextChoices):
        REPEAT_CLICKERS = "repeat_clickers", "Repeat clickers (failed 2+ campaigns)"
        HIGH_RISK = "high_risk", "High current risk score"
        NEW_HIRES = "new_hires", "New hires (recently added)"
        ALL = "all", "Everyone active"

    name = models.CharField(max_length=200)
    rule = models.CharField(max_length=32, choices=Rule.choices)
    department = models.ForeignKey(
        "employees.Department", on_delete=models.CASCADE, null=True, blank=True, related_name="smart_groups",
        help_text="Optional: restrict the rule to one department.",
    )
    new_hire_days = models.PositiveIntegerField(default=30, help_text="For the new-hires rule.")

    def __str__(self):
        return self.name
