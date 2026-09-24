import re

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

NAME_NO_SLASH = RegexValidator(r"^[^/\\]+$", "Names can't contain / or \\.")


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
    copy_to = models.TextField(
        blank=True, help_text="Extra addresses (not employees) that get their own copy of the email at launch, comma-separated."
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

    def copy_addresses(self) -> list[str]:
        return [a for a in re.split(r"[,;\s]+", self.copy_to or "") if a]


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


class EmailDraft(models.Model):
    """
    The email builder's source of truth. The engine (Gophish) only ever holds the compiled HTML,
    so the builder fields live here and are re-compiled and pushed through the adapter on every
    save. `name` is the Gophish template name (a plain reference, never a FK: invariant #2).
    """

    class Layout(models.TextChoices):
        MINIMAL = "minimal", "Plain email (like Outlook)"
        CORPORATE = "corporate", "Branded notice (blue header bar)"
        ALERT = "alert", "Urgent alert (red header bar)"

    name = models.CharField(max_length=200, unique=True, validators=[NAME_NO_SLASH], help_text="Also the template name in the phishing engine.")
    subject = models.CharField(max_length=300)
    layout = models.CharField(max_length=20, choices=Layout.choices, default=Layout.MINIMAL)
    body_html = models.TextField(blank=True, help_text="The composed message (sanitised). Takes over from the older text fields.")
    brand_name = models.CharField(max_length=100, blank=True, help_text="Shown in the header bar when there is no logo, e.g. IT Service Desk.")
    logo_image = models.ForeignKey("campaigns.EmailImage", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    logo_url = models.URLField(blank=True, help_text="Or an image URL the recipient's mail client can reach. An uploaded logo wins.")
    hero_image = models.ForeignKey(
        "campaigns.EmailImage", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Optional banner image shown under the header.",
    )
    heading = models.CharField(max_length=200, blank=True)
    body = models.TextField(help_text='Blank line = new paragraph. Start a line with "- " for a bullet. {{.FirstName}} is filled in per person.')
    button_label = models.CharField(max_length=80, blank=True, help_text="The call to action. Leave blank for no button. Clicks are tracked automatically.")
    footer_note = models.CharField(max_length=300, blank=True, help_text="Small print, e.g. Sent by People Team. Please do not reply.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def logo_src(self) -> str:
        return self.logo_image.public_url if self.logo_image else self.logo_url

    def render_html(self) -> str:
        from .email_render import render_email

        return render_email(
            layout=self.layout, logo_url=self.logo_src, hero_url=self.hero_image.public_url if self.hero_image else "",
            heading=self.heading, body=self.body, body_html=self.body_html,
            button_label=self.button_label, footer_note=self.footer_note, brand_name=self.brand_name,
        )

    def render_text(self) -> str:
        from .email_render import render_plain_text

        return render_plain_text(heading=self.heading, body=self.body, button_label=self.button_label,
                                 footer_note=self.footer_note, body_html=self.body_html)


class EmailImage(models.Model):
    """An uploaded picture for the builders. The bytes live on the shared volume (see images.py)."""

    stored_name = models.CharField(max_length=64, unique=True)
    original_name = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name

    @property
    def public_url(self) -> str:
        from .images import public_url

        return public_url(self.stored_name)


class LandingDraft(models.Model):
    """
    The landing-page builder's source of truth (the engine holds only the compiled HTML), same
    idea as EmailDraft. The form is always a native <form> with a real password input so that
    Gophish's capture_passwords=false strips it (invariant #4); passwords are never stored.
    """

    class Layout(models.TextChoices):
        SIGN_IN = "signin", "Sign-in card"
        DOCUMENT = "document", "Document viewer"
        VERIFY = "verify", "Account verification (split screen)"

    name = models.CharField(max_length=200, unique=True, validators=[NAME_NO_SLASH], help_text="Also the landing-page name in the phishing engine.")
    layout = models.CharField(max_length=20, choices=Layout.choices, default=Layout.SIGN_IN)
    brand_name = models.CharField(max_length=100, blank=True, help_text="Shown when there is no logo.")
    logo_image = models.ForeignKey(EmailImage, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    logo_url = models.URLField(blank=True, help_text="Or a logo image URL. An uploaded logo wins.")
    accent_color = models.CharField(
        max_length=7, default="#0b5fff", help_text="Button and highlight colour, like #0b5fff.",
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use a colour like #0b5fff.")],
    )
    heading = models.CharField(max_length=200, default="Sign in")
    subtext = models.CharField(max_length=300, blank=True)
    username_label = models.CharField(max_length=60, default="Work email")
    ask_password = models.BooleanField(default=True, help_text="Show a password field. It is never captured or stored.")
    button_label = models.CharField(max_length=60, default="Sign in")
    footer_note = models.CharField(max_length=300, blank=True)
    redirect_url = models.URLField(blank=True, help_text="Where to send people after they submit. Blank = the teachable-moment page.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def logo_src(self) -> str:
        return self.logo_image.public_url if self.logo_image else self.logo_url

    def engine_redirect_url(self) -> str:
        from .landing_render import learn_url

        return self.redirect_url or learn_url()

    def render_html(self) -> str:
        from .landing_render import render_landing

        return render_landing(
            layout=self.layout, brand_name=self.brand_name, logo_url=self.logo_src, accent=self.accent_color,
            heading=self.heading, subtext=self.subtext, username_label=self.username_label,
            ask_password=self.ask_password, button_label=self.button_label, footer_note=self.footer_note,
        )
