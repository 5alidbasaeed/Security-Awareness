from apps.employees.models import Department


def managed_departments(user):
    """
    None if `user` is not row-scoped (superuser, Security Admin, Report
    Viewer, ...); otherwise the queryset of departments a Department Manager
    may see. Shared by the admin mixin and the analytics API so the two can
    never disagree about who sees what.
    """
    if user.is_superuser or not user.groups.filter(name="Department Manager").exists():
        return None
    return Department.objects.filter(managers=user)
