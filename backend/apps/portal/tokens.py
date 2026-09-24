"""
Magic-link tokens for the employee training portal. Employees are not Django
users (no password, no IdP available yet — see the plan's Authentication
section), so sign-in is a short-lived signed link emailed to the address on
file. TimestampSigner gives us expiry and tamper-detection without storing
anything; the token carries only the employee's id.
"""

from django.conf import settings
from django.core import signing

from apps.employees.models import Employee

_SALT = "portal.magic-link"


def sign_employee(employee: Employee) -> str:
    return signing.dumps({"eid": employee.pk}, salt=_SALT)


def employee_from_token(token: str) -> Employee | None:
    try:
        data = signing.loads(token, salt=_SALT, max_age=settings.PORTAL_LINK_MAX_AGE_SECONDS)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or "eid" not in data:
        return None
    return Employee.objects.filter(pk=data["eid"], is_active=True).first()
