"""Procesor kontekstu: dane wspólne dla wszystkich szablonów (stopka, pasek, kontakt).

`site_commit` czytany z .git bez subprocessu — na serwerze repo jest klonem,
więc stopka pokazuje, która wersja jest wdrożona (odróżnia lokalne od produkcji).
"""

from functools import lru_cache

from django.conf import settings

from rag.access import access_for_user


@lru_cache(maxsize=1)
def git_commit() -> str:
    head = settings.BASE_DIR / ".git" / "HEAD"
    try:
        ref = head.read_text(encoding="utf-8").strip()
        if ref.startswith("ref:"):
            ref_path = settings.BASE_DIR / ".git" / ref.split(" ", 1)[1].strip()
            if ref_path.exists():
                return ref_path.read_text(encoding="utf-8").strip()[:7]
            packed = settings.BASE_DIR / ".git" / "packed-refs"
            if packed.exists():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref.split(" ", 1)[1].strip()):
                        return line.split(" ", 1)[0][:7]
            return ""
        return ref[:7]
    except OSError:
        return ""


def site(request) -> dict:
    user = request.user if request.user.is_authenticated else None
    return {
        "site_commit": git_commit(),
        "site_github_url": settings.SITE_GITHUB_URL,
        "site_contact_email": settings.SITE_CONTACT_EMAIL,
        "site_host": request.get_host(),
        "access_levels": access_for_user(user),
        "allow_anonymous": settings.ALLOW_ANONYMOUS,
    }
