"""
Portal session handling. The portal is not Django auth: a valid magic link puts
the employee's id in the session, and every portal view loads that one employee
and shows only their data. No staff surface is reachable from here, and no
employee can see another employee's assignments.
"""

import functools

from django.shortcuts import redirect
from django.views.decorators.cache import never_cache

from apps.employees.models import Employee

SESSION_KEY = "portal_employee_id"


def current_employee(request):
    eid = request.session.get(SESSION_KEY)
    if not eid:
        return None
    return Employee.objects.filter(pk=eid, is_active=True).first()


def portal_login(request, employee):
    request.session[SESSION_KEY] = employee.pk
    request.session.set_expiry(60 * 60 * 8)  # a working day; they re-request a link after


def portal_logout(request):
    request.session.pop(SESSION_KEY, None)


def portal_required(view):
    # never_cache: an employee's pages must not be replayable from the browser cache (Back button)
    # after they sign out on a shared laptop, exactly like the staff dashboard.
    @functools.wraps(view)
    @never_cache
    def wrapper(request, *args, **kwargs):
        employee = current_employee(request)
        if employee is None:
            return redirect("portal:login")
        request.employee = employee
        return view(request, *args, **kwargs)

    return wrapper
