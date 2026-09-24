"""
API-key authentication and the endpoint decorator every API view uses.

Two independent checks gate every write, so a key is never more capable than
intended AND never more capable than its owner:
  1. the key must carry the scope for the operation, and
  2. the key's owner must hold the Django permission that scope maps to.

The decorator sets request.user to the key's owner, so the rest of the stack
(RBAC queries, row scoping, audit logging) treats an API call exactly like that
user acting in the UI — there is no separate, weaker code path to keep in sync.
"""

import functools
import json

from django.http import JsonResponse

from .models import SCOPE_REQUIRED_PERMISSION, ApiKey, hash_key


def _unauthorized(detail):
    return JsonResponse({"error": detail}, status=401)


def _forbidden(detail):
    return JsonResponse({"error": detail}, status=403)


def authenticate(request) -> ApiKey | None:
    """Returns the live ApiKey for the request's credential, or None."""
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "api-key" or not value:
        return None
    key = ApiKey.objects.filter(hashed_key=hash_key(value.strip())).select_related("owner").first()
    if key is None or not key.is_active or not key.owner.is_active:
        return None
    return key


def api_endpoint(*, scope: str | None = None, methods=("GET",)):
    """
    Wraps a JSON API view: authenticates the key, enforces the method, the scope
    and the owner's Django permission, and injects request.api_key / request.user.
    Pass scope=None for an endpoint that only needs a valid key (e.g. whoami).
    """

    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            key = authenticate(request)
            if key is None:
                return _unauthorized("Provide a valid key as 'Authorization: Api-Key <key>'.")
            if request.method not in methods:
                return JsonResponse({"error": f"Method {request.method} not allowed."}, status=405)
            if scope is not None:
                if not key.has_scope(scope):
                    return _forbidden(f"This key lacks the '{scope}' scope.")
                required_perm = SCOPE_REQUIRED_PERMISSION.get(scope)
                if required_perm and not key.owner.has_perm(required_perm):
                    return _forbidden(f"The key's owner lacks permission for '{scope}'.")

            request.api_key = key
            request.user = key.owner  # the call now runs as this user for RBAC, scoping and audit
            key.touch()
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def parse_json_body(request):
    """Returns (data, error_response). Bodyless GETs return ({}, None)."""
    if not request.body:
        return {}, None
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return None, JsonResponse({"error": "Request body is not valid JSON."}, status=400)
    if not isinstance(data, dict):
        return None, JsonResponse({"error": "Request body must be a JSON object."}, status=400)
    return data, None
