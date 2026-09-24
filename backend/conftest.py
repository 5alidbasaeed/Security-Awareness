import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _isolated_cache(settings):
    """
    The app cache is Redis in real runs (shared throttle counters across workers). Tests use an
    in-process cache that is emptied around every test, so counters never leak between tests
    and the suite doesn't depend on a running Redis.
    """
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    yield
    cache.clear()
