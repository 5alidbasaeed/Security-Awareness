"""
Forms for the management UI. Widgets carry the dashboard design-system classes
(.field / .select) so the CSP stays strict — no inline styles in templates.
Department Managers get their department choices scoped in __init__, the same
rule the admin and API enforce.
"""

import re

from django import forms
from django.conf import settings
from django.db.models import Q

from apps.campaigns.models import Campaign, EmailDraft, LandingDraft, SmartGroup
from apps.core.scoping import managed_departments, visible_departments
from apps.employees.models import Department, Employee
from apps.training.models import Quiz, TrainingModule, TrainingSlide

_TEXT = {"class": "field"}
_AREA = {"class": "field", "rows": 4}


def _friendly_empty_label(field):
    """Django's raw '---------' means nothing to the person choosing. A required dropdown asks for a
    choice; an optional one says what leaving it empty means."""
    blank = "Choose…" if field.required else "None"
    if isinstance(field, forms.ModelChoiceField):
        if field.empty_label == "---------":
            field.empty_label = blank
    elif isinstance(field, forms.ChoiceField):
        field.choices = [(value, blank if value == "" and label == "---------" else label) for value, label in field.choices]


def _style(form):
    for field in form.fields.values():
        _friendly_empty_label(field)
        widget = field.widget
        if isinstance(widget, forms.Select):
            widget.attrs.setdefault("class", "select")
        elif isinstance(widget, (forms.CheckboxInput,)):
            widget.attrs.setdefault("class", "checkbox")
        else:
            widget.attrs.setdefault("class", "field")


class ScopedModelForm(forms.ModelForm):
    """Scopes any Department field to what `user` may target (None = unscoped)."""

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None:
            managed = managed_departments(user)
            for field in self.fields.values():
                queryset = getattr(field, "queryset", None)
                if queryset is not None and queryset.model is Department:
                    field.queryset = managed if managed is not None else visible_departments(user)
        _style(self)


class CampaignForm(forms.ModelForm):
    """
    Create/edit a campaign as a draft, mostly from dropdowns: which email, which landing page,
    who gets it, what training follows a failure. Status, approval and the engine id are never
    editable here; they change through the workflow actions (submit/approve/launch).
    """

    field_order = ["name", "email", "landing_page", "audience", "copy_to", "training_module", "scheduled_at"]
    email = forms.ChoiceField(label="Email", help_text="Build these under Emails & pages.")
    landing_page = forms.ChoiceField(label="Landing page", help_text="Where people land when they click.")
    audience = forms.ChoiceField(label="Send to")
    copy_to = forms.CharField(
        required=False, label="Also send a copy to",
        help_text="Optional: addresses of people who aren't employees (like the security team), separated by commas. "
                  "Each gets their own copy when the campaign launches. Not counted in the results.",
    )

    class Meta:
        model = Campaign
        fields = ["name", "training_module", "scheduled_at"]
        labels = {"training_module": "If someone falls for it, assign", "scheduled_at": "Send at (optional)"}
        help_texts = {"scheduled_at": "Leave blank to launch it yourself once it's approved."}
        widgets = {"scheduled_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        campaign = self.instance if self.instance.pk else None

        def with_current(names, current):
            names = list(names)
            if current and current not in names:
                names.append(current)  # keep a value the library no longer lists rather than silently changing it
            return [("", "Choose...")] + [(n, n) for n in names]

        self.fields["email"].choices = with_current(EmailDraft.objects.values_list("name", flat=True), campaign and campaign.template_name)
        self.fields["landing_page"].choices = with_current(LandingDraft.objects.values_list("name", flat=True), campaign and campaign.landing_page_name)

        managed = managed_departments(user) if user is not None else None
        departments = visible_departments(user) if user is not None else Department.objects.all()
        groups = [("dept:%d" % d.pk, d.name) for d in departments]
        choices = [("", "Choose...")]
        if managed is None:  # org-wide roles can also target everyone or a smart group
            choices.append(("all", "Everyone (all active staff)"))
            choices.append(("Dynamic audiences", [("smart:%d" % g.pk, g.name) for g in SmartGroup.objects.exclude(rule=SmartGroup.Rule.ALL)]))
        choices.append(("Departments", groups))
        self.fields["audience"].choices = choices
        # Keep a module that was retired after this campaign chose it, or saving the form would silently
        # drop the campaign's follow-up training (same reason as with_current above).
        current_module = campaign.training_module_id if campaign else None
        self.fields["training_module"].queryset = TrainingModule.objects.filter(
            Q(is_active=True) | Q(pk=current_module) if current_module else Q(is_active=True)
        )
        self.fields["training_module"].required = False
        self.fields["training_module"].empty_label = "No training"
        self.fields["scheduled_at"].required = False

        if campaign:
            self.fields["email"].initial = campaign.template_name
            self.fields["landing_page"].initial = campaign.landing_page_name
            self.fields["copy_to"].initial = campaign.copy_to
            if campaign.target_smart_group_id:
                self.fields["audience"].initial = "smart:%d" % campaign.target_smart_group_id
            elif campaign.target_department_id:
                self.fields["audience"].initial = "dept:%d" % campaign.target_department_id
        _style(self)

    def clean_audience(self):
        value = self.cleaned_data["audience"]
        allowed = {value for value, _ in _flatten(self.fields["audience"].choices)} - {""}
        if value not in allowed:
            raise forms.ValidationError("Choose who this goes to.")
        return value

    def clean_copy_to(self):
        from django.core.exceptions import ValidationError
        from django.core.validators import validate_email

        addresses = []
        for raw in re.split(r"[,;\s]+", self.cleaned_data.get("copy_to") or ""):
            if not raw:
                continue
            try:
                validate_email(raw)
            except ValidationError:
                raise forms.ValidationError(f"“{raw}” isn't a valid email address.") from None
            if Employee.objects.filter(email__iexact=raw).exists():
                raise forms.ValidationError(f"{raw} is an employee. Add them to the audience instead.")
            if raw.lower() not in {a.lower() for a in addresses}:
                addresses.append(raw)
        if len(addresses) > 10:
            raise forms.ValidationError("At most 10 copy addresses.")
        return ", ".join(addresses)

    def apply_to(self, campaign):
        """Copy the dropdown choices onto the model fields (call before saving)."""
        c = self.cleaned_data
        campaign.template_name = c["email"]
        campaign.landing_page_name = c["landing_page"]
        campaign.copy_to = c["copy_to"]
        if not campaign.landing_page_url:
            campaign.landing_page_url = settings.PHISH_SERVER_URL
        kind, _, ident = c["audience"].partition(":")
        campaign.target_department = Department.objects.get(pk=ident) if kind == "dept" else None
        if kind == "smart":
            campaign.target_smart_group = SmartGroup.objects.get(pk=ident)
        elif c["audience"] == "all":
            everyone = SmartGroup.objects.filter(rule=SmartGroup.Rule.ALL, department=None).first()
            campaign.target_smart_group = everyone or SmartGroup.objects.create(
                name="Everyone", rule=SmartGroup.Rule.ALL, department=None)
        else:
            campaign.target_smart_group = None
        return campaign


def _flatten(choices):
    for value, label in choices:
        if isinstance(label, (list, tuple)):
            yield from _flatten(label)
        else:
            yield value, label


class TrainingModuleForm(forms.ModelForm):
    class Meta:
        model = TrainingModule
        fields = ["title", "description", "content_url", "duration_minutes", "is_active"]
        widgets = {"description": forms.Textarea(attrs=_AREA)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class QuizForm(forms.ModelForm):
    class Meta:
        model = Quiz
        fields = ["passing_score_percent"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class EmployeeForm(ScopedModelForm):
    class Meta:
        model = Employee
        fields = ["full_name", "email", "department", "is_exempt", "exempt_reason", "exempt_until"]
        widgets = {"exempt_until": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Match the admin: a Department Manager can't exempt their own people or move them out of scope.
        if self.user is not None and managed_departments(self.user) is not None:
            for name in ("is_exempt", "exempt_reason", "exempt_until"):
                self.fields[name].disabled = True
            self.instance._exemption_locked = True
            self.fields["department"].required = True

    def save(self, commit=True):
        employee = super().save(commit=False)
        # Whoever changes the justification owns it: the register shows who set each exemption.
        if employee.is_exempt and self.user is not None and any(
            f in self.changed_data for f in ("is_exempt", "exempt_reason", "exempt_until")
        ):
            employee.exempt_set_by = self.user
        if commit:
            employee.save()
        return employee


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name", "manager"]
        labels = {"manager": "Department head"}
        help_texts = {"manager": "The employee who leads it. They are told when their people are overdue on training. "
                                 "(Console sign-in access is set under Users & access.)"}

    def __init__(self, *args, **kwargs):
        kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["manager"].queryset = Employee.objects.filter(is_active=True).order_by("full_name")
        self.fields["manager"].required = False
        _style(self)


def _lock_name(form):
    """Campaigns and the phishing engine refer to an email/page by name, so it can't change once saved."""
    if form.instance.pk:
        form.fields["name"].disabled = True
        form.fields["name"].help_text = "Fixed once created: campaigns refer to it by this name."


class EmailDraftForm(forms.ModelForm):
    """The email composer: a name for your own reference, the subject, a style, and the message
    itself, written in the rich-text editor. The message is sanitised on the way in."""

    class Meta:
        model = EmailDraft
        fields = ["name", "subject", "layout", "brand_name", "body_html"]
        labels = {"name": "Name (for you)", "layout": "Style", "brand_name": "Header text"}
        help_texts = {
            "name": "Only used in this app to tell your emails apart.",
            "brand_name": "Shown in the coloured bar of the branded styles, e.g. IT Service Desk.",
        }
        widgets = {"body_html": forms.Textarea(attrs={"class": "compose-source", "rows": 1, "aria-label": "Message (rich-text editor)"})}

    UPLOADS = ()  # pictures are placed in the message from the composer, not attached to the form

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _lock_name(self)
        self.fields["body_html"].required = True
        self.fields["body_html"].label = "Message"
        _style(self)

    def clean_body_html(self):
        from apps.campaigns.richtext import sanitize_email_html

        html = sanitize_email_html(self.cleaned_data["body_html"])
        if not re.sub(r"<[^>]+>|&nbsp;|\s", "", html):
            raise forms.ValidationError("Write your message first.")
        return html


class LandingDraftForm(forms.ModelForm):
    class Meta:
        model = LandingDraft
        fields = ["name", "layout", "brand_name", "logo_image", "logo_url", "accent_color", "heading", "subtext",
                  "username_label", "ask_password", "button_label", "footer_note", "redirect_url"]

    logo_upload = forms.FileField(required=False, label="Upload a logo", help_text="PNG, JPEG or GIF, up to 1 MB.")
    UPLOADS = (("logo_upload", "logo_image"),)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _lock_name(self)
        self.fields["accent_color"].widget = forms.TextInput(attrs={"class": "field", "type": "color"})
        _style(self)


class CloneLandingForm(forms.Form):
    """The URL for a page to clone — see apps.campaigns.clone for what happens to it."""

    name = forms.CharField(max_length=200, label="Name", widget=forms.TextInput(attrs={"class": "field"}),
                           help_text="Only used in this app to tell your pages apart.")
    url = forms.CharField(max_length=1000, label="Web address of the page to clone",
                          widget=forms.URLInput(attrs={"class": "field", "placeholder": "https://..."}),
                          help_text="The sign-in page of a public website. It is fetched by the phishing engine, "
                                    "not by this server, and rebuilt with no scripts before it is saved.")

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if "/" in name or "\\" in name:
            raise forms.ValidationError("Names can't contain / or \\.")
        if LandingDraft.objects.filter(name=name).exists():
            raise forms.ValidationError("A landing page with that name already exists.")
        return name

    def clean_url(self):
        from apps.campaigns.clone import CloneError, validate_clone_url

        try:
            return validate_clone_url(self.cleaned_data["url"])
        except CloneError as exc:
            raise forms.ValidationError(str(exc)) from None


class ClonedLandingForm(forms.ModelForm):
    """What can still be changed on a cloned page: it isn't rebuilt from fields like the others, so
    only where people go afterwards is editable here — everything else needs a fresh clone."""

    class Meta:
        model = LandingDraft
        fields = ["name", "redirect_url"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _lock_name(self)
        _style(self)


class LandingPageForm(forms.Form):
    name = forms.CharField(max_length=200, widget=forms.TextInput(attrs=_TEXT))
    html = forms.CharField(widget=forms.Textarea(attrs={"class": "field", "rows": 12}), label="HTML")
    capture_credentials = forms.BooleanField(
        required=False, initial=True,
        help_text="Record that a username/email was submitted. Passwords are never captured or stored.",
    )
    redirect_url = forms.URLField(required=False, widget=forms.URLInput(attrs=_TEXT),
                                  help_text="Where to send the employee after submitting (e.g. a teachable-moment page).")


class TrainingPolicyForm(forms.ModelForm):
    class Meta:
        from apps.training.models import TrainingPolicy

        model = TrainingPolicy
        fields = ["name", "module", "department", "repeat_every_days", "due_days", "is_active"]

    def __init__(self, *args, **kwargs):
        kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["module"].queryset = TrainingModule.objects.filter(is_active=True)
        _style(self)
        self.fields["department"].empty_label = "Everyone"


class ReportScheduleForm(forms.ModelForm):
    class Meta:
        from apps.reporting.models import ReportSchedule

        model = ReportSchedule
        fields = ["name", "kind", "frequency", "recipients", "is_active"]
        widgets = {"recipients": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        from apps.reporting.services import KINDS

        kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        # Only aggregate reports can be emailed on a schedule — never ones that name people.
        self.fields["kind"].choices = [(k, s.label) for k, s in KINDS.items() if not s.employee_level]
        _style(self)

    def clean_recipients(self):
        from django.core.validators import validate_email

        value = self.cleaned_data["recipients"]
        addresses = [a for a in re.split(r"[\s,;]+", value) if a]
        if not addresses:
            raise forms.ValidationError("Add at least one email address.")
        for address in addresses:
            validate_email(address)
        return "\n".join(addresses)


class SlideForm(forms.ModelForm):
    class Meta:
        model = TrainingSlide
        fields = ["title", "body", "callout"]
        widgets = {"body": forms.Textarea(attrs={"class": "field", "rows": 8})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class MailSettingsForm(forms.Form):
    """The SMTP relay the phishing emails go out through (the engine's sending profile)."""

    host = forms.CharField(max_length=253, label="Host", widget=forms.TextInput(attrs={"class": "field"}),
                           help_text="The mail relay's name or IP address.")
    port = forms.IntegerField(min_value=1, max_value=65535, initial=25, label="Port", widget=forms.NumberInput(attrs={"class": "field"}))
    username = forms.CharField(max_length=254, required=False, label="Username", widget=forms.TextInput(attrs={"class": "field", "autocomplete": "off"}),
                               help_text="Leave blank if the relay doesn't ask for a login.")
    password = forms.CharField(required=False, label="Password", widget=forms.PasswordInput(attrs={"class": "field", "autocomplete": "new-password"}, render_value=False))
    from_address = forms.CharField(max_length=254, label="Return address", widget=forms.TextInput(attrs={"class": "field"}),
                                   help_text="What recipients see as the sender, like IT Service Desk <helpdesk@example.com>.")
    allow_self_signed = forms.BooleanField(required=False, label="Allow self-signed certificates",
                                           help_text="Accept the relay's certificate even if it isn't trusted or doesn't match its name.")

    def clean_host(self):
        host = self.cleaned_data["host"].strip()
        if not re.fullmatch(r"[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\]", host):
            raise forms.ValidationError("Enter just the host name or IP address, without a port or http://.")
        return host

    def clean_from_address(self):
        from email.utils import parseaddr

        from django.core.validators import validate_email

        value = self.cleaned_data["from_address"].strip()
        if re.search(r"[\x00-\x1f\x7f]", value):
            raise forms.ValidationError("Enter the address on a single line.")
        validate_email(parseaddr(value)[1])
        return value

    def clean_username(self):
        value = self.cleaned_data["username"].strip()
        if re.search(r"[\x00-\x1f\x7f]", value):
            raise forms.ValidationError("Enter the username on a single line.")
        return value


class TestEmailForm(forms.Form):
    to = forms.EmailField(label="Send a test message to", widget=forms.EmailInput(attrs={"class": "field"}))
