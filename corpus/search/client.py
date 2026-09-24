"""Klient OpenSearch konfigurowany z ustawień (OPENSEARCH_*)."""

from functools import lru_cache

from django.conf import settings
from opensearchpy import OpenSearch


@lru_cache(maxsize=1)
def get_client() -> OpenSearch:
    auth = None
    if settings.OPENSEARCH_USER:
        auth = (settings.OPENSEARCH_USER, settings.OPENSEARCH_PASSWORD)
    return OpenSearch(
        hosts=[settings.OPENSEARCH_URL],
        http_auth=auth,
        use_ssl=settings.OPENSEARCH_URL.startswith("https"),
        verify_certs=settings.OPENSEARCH_VERIFY_CERTS,
        timeout=30,
    )


def index_name(kind: str) -> str:
    """'verses' -> 'scriptura-verses'"""
    return f"{settings.OPENSEARCH_INDEX_PREFIX}-{kind}"
