"""
Exemption governance. An exemption removes a person from every simulation, so it has to be justified
(a reason), owned (who set it, when) and time-boxed (`exempt_until`). This module enforces the last
part: an expired exemption never protects anyone, even before the scheduled job gets to tidy it up.
"""

from django.db.models import Q
from django.utils import timezone


def not_exempt_q(today=None) -> Q:
    """People who may be simulated: never exempt, or exempt only until a date that has passed."""
    today = today or timezone.localdate()
    return Q(is_exempt=False) | Q(exempt_until__lt=today)
