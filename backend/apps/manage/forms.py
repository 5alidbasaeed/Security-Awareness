"""
Forms for the management UI. Widgets carry the dashboard design-system classes
(.field / .select) so the CSP stays strict — no inline styles in templates.
Department Managers get their department choices scoped in __init__, the same
rule the admin and API enforce.
"""

from django import forms

from apps.campaigns.models import Campaign
from apps.core.scoping import managed_departments, visible_departments
from apps.employees.models import Department, Employee
from apps.training.models import Quiz, TrainingModule

_TEXT = {"class": "field"}
_AREA = {"class": "field", "rows": 4}
_SELECT = {"class": "select"}


def _style(form):
    for field in form.fields.values():
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


class CampaignForm(ScopedModelForm):
    """
    Create/edit a campaign as a draft. Only content fields — status, approval and
    the Gophish id are never editable here; they change through the workflow
    actions (submit/approve/launch), exactly as in the admin.
    """

    class Meta:
        model = Campaign
        fields = [
            "name", "template_name", "landing_page_name", "landing_page_url",
            "target_department", "training_module", "scheduled_at",
        ]
        widgets = {"scheduled_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["training_module"].queryset = TrainingModule.objects.filter(is_active=True)
        self.fields["training_module"].required = False
        self.fields["scheduled_at"].required = False
        self.fields["target_department"].required = False


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
        fields = ["full_name", "email", "department", "is_exempt"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Match the admin: a Department Manager can't exempt their own people or move them out of scope.
        if self.user is not None and managed_departments(self.user) is not None:
            self.fields["is_exempt"].disabled = True
            self.fields["department"].required = True


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        _style(self)


class EmailTemplateForm(forms.Form):
    name = forms.CharField(max_length=200, widget=forms.TextInput(attrs=_TEXT))
    subject = forms.CharField(max_length=300, widget=forms.TextInput(attrs=_TEXT))
    html = forms.CharField(widget=forms.Textarea(attrs={"class": "field", "rows": 12}), label="HTML body")
    text = forms.CharField(required=False, widget=forms.Textarea(attrs=_AREA), label="Plain-text body (optional)")


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
