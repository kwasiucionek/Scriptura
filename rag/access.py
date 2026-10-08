"""Uprawnienia użytkownika: poziomy dostępu do źródeł i dozwolone tryby.

Grupy Django (nadawane w adminie):
  scriptura-licensed  -> widzi także dokumenty access=licensed (zgody autorów)
  scriptura-private   -> widzi także access=private (demo prywatne dla właściciela praw)
Każdy zalogowany widzi `open`. Anonimowy (gdy ALLOW_ANONYMOUS): wyłącznie `open`.
Anonimowy domyślnie może używać obu trybów; ANONYMOUS_POPULAR_ONLY=True ogranicza
go do trybu popularnego, nie zmieniając dostępu do źródeł.
Superużytkownik: wszystkie poziomy wspólne. Materiały personal: tylko właściciel.
Właściciel widzi swoje dokumenty niezależnie od ich poziomu access.
"""

from django.conf import settings
from django.db.models import Q

GROUP_LICENSED = "scriptura-licensed"
GROUP_PRIVATE = "scriptura-private"


def access_for_user(user) -> list[str]:
    if user is None or not user.is_authenticated:
        return ["open"]
    if user.is_superuser:
        return ["open", "licensed", "private"]
    names = set(user.groups.values_list("name", flat=True))
    levels = ["open"]
    if GROUP_LICENSED in names:
        levels.append("licensed")
    if GROUP_PRIVATE in names:
        levels.append("private")
    return levels


def document_access_q(
    access: list[str] | None = None,
    user_id: int | None = None,
    *,
    prefix: str = "",
    personal_only: bool = False,
) -> Q:
    """SQL ACL: allowed non-personal levels OR ownership (at every access level).

    `None` means open-only; [] grants no shared access. Personal documents never
    become visible through a group, including to superusers. personal_only selects
    all the user's own materials, including those they have shared as licensed.
    """
    own = Q(**{f"{prefix}owner_id": user_id}) if user_id else Q(pk__in=[])
    if personal_only:
        return own
    levels = [a for a in (["open"] if access is None else access) if a != "personal"]
    return Q(**{f"{prefix}access__in": levels}) | own


def visible_documents(user):
    from library.models import Document

    user_id = user.pk if user is not None and user.is_authenticated else None
    return Document.objects.filter(document_access_q(access_for_user(user), user_id))


def allowed_modes(user) -> list[str]:
    if user is None or not user.is_authenticated:
        return (
            ["popular"]
            if settings.ANONYMOUS_POPULAR_ONLY
            else ["popular", "scientific"]
        )
    return ["popular", "scientific"]
