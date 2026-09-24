from datetime import timedelta

from django import forms
from django.utils import timezone

from .services import ReportError, allowed_kinds, validate_period


class GenerateReportForm(forms.Form):
    kind = forms.ChoiceField(label="Report", widget=forms.Select(attrs={"class": "select"}))
    period_start = forms.DateField(label="From", widget=forms.DateInput(attrs={"type": "date", "class": "field"}))
    period_end = forms.DateField(label="To", widget=forms.DateInput(attrs={"type": "date", "class": "field"}))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.specs = allowed_kinds(user)
        self.fields["kind"].choices = [(spec.key, spec.label) for spec in self.specs]
        if not self.is_bound:
            today = timezone.now().date()
            last_month_end = today.replace(day=1) - timedelta(days=1)
            self.initial.setdefault("period_start", last_month_end.replace(day=1))
            self.initial.setdefault("period_end", last_month_end)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("period_start"), cleaned.get("period_end")
        if start and end:
            try:
                validate_period(start, end)
            except ReportError as exc:
                raise forms.ValidationError(str(exc)) from exc
        return cleaned
