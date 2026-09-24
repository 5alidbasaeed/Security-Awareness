"""
Self-service API-key management. A staff user manages only their own keys, and
can grant a key only scopes whose required permission they themselves hold — so
a key can never exceed its owner, and creating one can't escalate the owner.
The raw key is shown exactly once, right after creation.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.api.models import SCOPE_REQUIRED_PERMISSION, ApiKey, Scope
from apps.core.audit import log_action

from .access import manage_access


def _grantable_scopes(user):
    """Scopes this user may grant — those whose required permission they hold."""
    return [s for s in Scope if user.has_perm(SCOPE_REQUIRED_PERMISSION[s])]


@manage_access(methods=("GET", "POST"))
def api_keys(request):
    grantable = _grantable_scopes(request.user)
    new_key = None

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        chosen = [s for s in request.POST.getlist("scopes") if s in {g.value for g in grantable}]
        expires_raw = request.POST.get("expires_at") or ""
        expires_at = None
        if expires_raw:
            day = parse_date(expires_raw)
            expires_at = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time())) if day else None
        if not name:
            messages.error(request, "Give the key a name.")
        elif not chosen:
            messages.error(request, "Choose at least one scope you're allowed to grant.")
        else:
            key, raw = ApiKey.generate(name=name, owner=request.user, scopes=chosen, expires_at=expires_at)
            log_action(actor=request.user, action="api_key_created", target_description=f"{key.name} ({key.prefix})",
                       scopes=chosen)
            new_key = raw
            messages.success(request, "Key created. Copy it now — it won't be shown again.")

    keys = ApiKey.objects.filter(owner=request.user)
    return render(request, "manage/api_keys.html", {
        "active": "manage", "keys": keys, "new_key": new_key,
        "grantable": grantable, "all_scopes": Scope.choices,
    })


@manage_access(methods=("POST",))
def api_key_revoke(request, pk):
    key = get_object_or_404(ApiKey, pk=pk, owner=request.user)
    if key.revoked_at is None:
        key.revoked_at = timezone.now()
        key.save(update_fields=["revoked_at"])
        log_action(actor=request.user, action="api_key_revoked", target_description=f"{key.name} ({key.prefix})")
        messages.success(request, f"Key “{key.name}” revoked.")
    return redirect("manage:api-keys")
