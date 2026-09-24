"""Uprawnienia użytkownika: poziomy dostępu do źródeł i dozwolone tryby.

Grupy Django (nadawane w adminie):
  scriptura-licensed  -> widzi także dokumenty access=licensed (zgody autorów)
  scriptura-private   -> widzi także access=private (demo prywatne dla właściciela praw)
Każdy zalogowany widzi `open`. Anonimowy (gdy ALLOW_ANONYMOUS): tylko `open`, tylko tryb popularny.
Superużytkownik: wszystko.
"""

from django.conf import settings

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


def allowed_modes(user) -> list[str]:
    if user is None or not user.is_authenticated:
        return (
            ["popular"]
            if settings.ANONYMOUS_POPULAR_ONLY
            else ["popular", "scientific"]
        )
    return ["popular", "scientific"]
