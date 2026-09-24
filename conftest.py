"""Ustawienia wymuszone dla testów: backendy zastępcze niezależnie od .env."""

import pytest


@pytest.fixture(autouse=True)
def _offline_backends(settings):
    settings.SEARCH_BACKEND = "db"
    settings.RERANKER_BACKEND = "none"
    settings.LLM_BACKEND = "echo"
    settings.DEMO_TOKEN = ""
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    settings.WHITENOISE_AUTOREFRESH = True  # bez ostrzeżenia o braku staticfiles/
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
