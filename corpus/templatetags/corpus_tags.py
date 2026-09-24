"""Tagi szablonów: rendering tokenów z linkami do konkordancji."""

from django import template
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from corpus.models import VerseText

register = template.Library()

RTL_LANGUAGES = {"hbo", "arc"}


@register.simple_tag
def render_tokens(vt: VerseText) -> str:
    """Tekst wersetu jako klikalne tokeny (oryginały) lub zwykły tekst (przekłady)."""
    tokens = list(vt.tokens.all())
    if not tokens:
        return format_html('<span class="plain">{}</span>', vt.text)

    url = reverse("corpus:concordance")
    parts = []
    for t in tokens:
        if t.lemma:
            href = f"{url}?lemma={t.lemma}"
            title = f"{t.lemma} · {t.morph}" if t.morph else t.lemma
        elif t.strong:
            href = f"{url}?strong={t.strong}"
            title = f"{t.strong} · {t.morph}" if t.morph else t.strong
        else:
            href = f"{url}?form={t.surface}"
            title = t.morph
        parts.append((href, title, t.surface))
    return format_html_join(
        " ",
        '<a class="tok" hx-get="{}" hx-target="#panel" hx-push-url="true" title="{}">{}</a>',
        parts,
    )


@register.filter
def dir_for(language: str) -> str:
    return "rtl" if language in RTL_LANGUAGES else "ltr"


@register.filter
def dict_get(d: dict, key: str):
    return d.get(key) if d else None


@register.filter
def highlight(text: str, needle: str) -> str:
    """Podświetl formę w tekście (escape + <mark>)."""
    from django.utils.html import escape
    from django.utils.safestring import mark_safe

    safe = escape(text)
    if needle:
        safe = safe.replace(escape(needle), f"<mark>{escape(needle)}</mark>", 1)
    return mark_safe(safe)
