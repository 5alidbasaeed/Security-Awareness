"""Forms for the governance pages: access review, training extensions/waivers, manual assignment."""

from datetime import timedelta

from django import forms
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.employees.models import Department
from apps.training.models import TrainingModule

from .forms import _style

ROLE_NAMES = ("Security Admin", "Campaign Manager", "Training Manager", "Report Viewer", "Department Manager")
ROLE_HELP = {
    "Security Admin": "Everything, including approving campaigns, mail settings and access.",
    "Campaign Manager": "Builds and launches campaigns; cannot approve their own.",
    "Training Manager": "Owns training content and assignments.",
    "Report Viewer": "Read-only, plus aggregate reports.",
    "Department Manager": "Sees and manages only the departments chosen below.",
}


class UserAccessForm(forms.Form):
    is_active = forms.BooleanField(required=False, label="Account is active",
                                   help_text="Untick to block sign-in without deleting the account or its history.")
    is_staff = forms.BooleanField(required=False, label="Can sign in to the admin console")
    roles = forms.ModelMultipleChoiceField(queryset=Group.objects.none(), required=False, widget=forms.CheckboxSelectMultiple,
                                           label="Roles")
    departments = forms.ModelMultipleChoiceField(queryset=Department.objects.none(), required=False, widget=forms.CheckboxSelectMultiple,
                                                 label="Departments (Department Manager only)")
    reason = forms.CharField(max_length=300, label="Reason for the change",
                             help_text="Kept in the audit log with the before and after.")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.target = user
        self.fields["roles"].queryset = Group.objects.filter(name__in=ROLE_NAMES).order_by("name")
        self.fields["roles"].label_from_instance = lambda g: f"{g.name}: {ROLE_HELP.get(g.name, '')}"
        self.fields["departments"].queryset = Department.objects.order_by("name")
        self.initial = {
            "is_active": user.is_active, "is_staff": user.is_staff,
            "roles": list(user.groups.filter(name__in=ROLE_NAMES)),
            "departments": list(user.managed_departments_as_user.all()),
        }
        _style(self)
        # _style adds text-field classes to the checkbox lists; they render fine without them.
        for name in ("roles", "departments"):
            self.fields[name].widget.attrs.pop("class", None)

    def clean(self):
        cleaned = super().clean()
        roles = {g.name for g in cleaned.get("roles") or []}
        if cleaned.get("departments") and "Department Manager" not in roles:
            self.add_error("departments", "Departments only apply to the Department Manager role. Add the role or clear the list.")
        if "Department Manager" in roles and not cleaned.get("departments"):
            self.add_error("departments", "A Department Manager with no department would see nothing. Choose at least one.")
        if cleaned.get("is_staff") is False and roles:
            self.add_error("is_staff", "Roles have no effect unless the person can sign in to the console.")
        return cleaned


class ExtendForm(forms.Form):
    new_due_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"), label="New due date")
    reason = forms.CharField(max_length=300, label="Reason", help_text="Required. Kept with the assignment and in the audit log.")

    def __init__(self, *args, assignment, **kwargs):
        super().__init__(*args, **kwargs)
        self.assignment = assignment
        _style(self)

    def clean_new_due_date(self):
        date = self.cleaned_data["new_due_date"]
        if date <= timezone.localdate():
            raise forms.ValidationError("Choose a date in the future.")
        current = self.assignment.due_at
        if current is not None and date <= timezone.localtime(current).date():
            raise forms.ValidationError("That is not later than the current due date.")
        if date > timezone.localdate() + timedelta(days=365):
            raise forms.ValidationError("An extension of more than a year is a different decision. Waive it instead, with a reason.")
        return date


class WaiveForm(forms.Form):
    reason = forms.CharField(max_length=300, label="Reason for the waiver",
                             help_text="Required. A waived assignment stops counting as owed and is reported separately.")

    def __init__(self, *args, **kwargs):
        kwargs.pop("assignment", None)
        super().__init__(*args, **kwargs)
        _style(self)


class ManualAssignForm(forms.Form):
    module = forms.ModelChoiceField(queryset=TrainingModule.objects.none(), label="Training module")
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False, label="Whole department",
                                        empty_label="No department — assign one person instead")
    email = forms.EmailField(required=False, label="Or one person (email)")
    due_days = forms.IntegerField(min_value=1, max_value=365, initial=14, label="Days to complete")

    def __init__(self, *args, departments, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["module"].queryset = TrainingModule.objects.filter(is_active=True).order_by("title")
        self.fields["department"].queryset = departments.order_by("name")
        _style(self)

    def clean(self):
        cleaned = super().clean()
        department, email = cleaned.get("department"), (cleaned.get("email") or "").strip()
        if bool(department) == bool(email):
            raise forms.ValidationError("Choose either a department or one person's email, not both and not neither.")
        return cleaned

