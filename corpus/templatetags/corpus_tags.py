"""Tagi szablonów: rendering tokenów z linkami do konkordancji."""

import html
import re
from urllib.parse import urlencode

from django import template
from django.urls import reverse
from django.utils.html import escape, format_html, format_html_join
from django.utils.safestring import mark_safe

from corpus.models import VerseText

register = template.Library()

RTL_LANGUAGES = {"hbo", "arc"}


def render_notes(vt: VerseText) -> str:
    """Przypisy i odsyłacze wersetu jako rozwijany blok (HTML przypisów oczyszczony przy imporcie)."""
    notes = list(vt.notes.all())
    if not notes:
        return ""
    n_notes = sum(1 for n in notes if n.kind == "note")
    label = " · ".join(
        x
        for x in (
            f"przypisy ({n_notes})" if n_notes else "",
            f"odsyłacze ({len(notes) - n_notes})" if len(notes) > n_notes else "",
        )
        if x
    )
    items = format_html_join(
        "",
        '<li class="{}"><sup>{}</sup> {}</li>',
        (
            (n.kind, n.label, mark_safe(n.html))  # noqa: S308 — biała lista z importu
            for n in notes
        ),
    )
    return format_html(
        '<details class="vnotes"><summary>{}</summary><ul>{}</ul></details>',
        label,
        items,
    )


@register.simple_tag
def render_tokens(vt: VerseText) -> str:
    """Tekst wersetu: oryginał jako klikalne tokeny, przekład z modułu jako oczyszczony HTML
    z przypisami, pozostałe przekłady jako zwykły tekst."""
    tokens = list(vt.tokens.all())
    if not tokens and vt.markup:
        return format_html(
            '<span class="plain">{}</span>{}',
            mark_safe(vt.markup),  # noqa: S308 — biała lista znaczników z importu
            mark_safe(render_notes(vt)),  # noqa: S308
        )
    if not tokens:
        return format_html('<span class="plain">{}</span>', vt.text)

    url = reverse("corpus:concordance")
    parts = []
    for t in tokens:
        if t.strong:  # hasło słownika: definicje, użycie, wystąpienia
            href = reverse("corpus:lexicon", args=[t.strong])
            title = f"{t.lemma or t.strong} · {t.strong}" + (
                f" · {t.morph}" if t.morph else ""
            )
        elif t.lemma:
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


_DEF_REF = re.compile(r"\[\[([^|\]]{1,60})\|([^\]]{0,80})\]\]")
_DEF_TAGS = re.compile(r"&lt;(/?)([bi])&gt;")
_STRONG_NUM = re.compile(r"\b([GH])(\d{1,5})\b")


@register.filter
def lexicon_definition(text: str) -> str:
    """Definicja STEP (po step.clean_definition) jako bezpieczny HTML: najpierw escape całości,
    potem przywrócenie <b>/<i> i odsyłaczy [[Php.2.6|…]] jako linków do tekstu (Flp 2,6)."""
    from corpus.books import step_ref_to_siglum

    url = reverse("corpus:reference")
    safe = _DEF_TAGS.sub(r"<\1\2>", escape(text or ""))

    def ref(m: re.Match) -> str:
        siglum = step_ref_to_siglum(html.unescape(m.group(1)))
        label = siglum or m.group(2) or m.group(1)
        if not siglum:
            return label
        q = urlencode({"q": siglum})
        return f'<a href="{url}?{q}">{escape(label)}</a>'

    safe = _DEF_REF.sub(ref, safe)
    # niezamknięte <b>/<i> nie mogą rozlać się poza definicję
    for tag in ("b", "i"):
        safe += f"</{tag}>" * max(0, safe.count(f"<{tag}>") - safe.count(f"</{tag}>"))
    return mark_safe(safe)  # noqa: S308 — tylko przywrócone, znane znaczniki


@register.filter
def strong_links(text: str) -> str:
    """„from G726;” -> link do hasła G726 (pochodzenie słowa wg Stronga)."""
    safe = escape(text or "")
    return mark_safe(  # noqa: S308
        _STRONG_NUM.sub(
            lambda m: (
                f'<a href="{reverse("corpus:lexicon", args=[m.group(1) + m.group(2)])}">'
                f"{m.group(0)}</a>"
            ),
            safe,
        )
    )


@register.simple_tag
def works_attribution(works, label: str = "Teksty") -> str:
    """Nota o prawach dla dzieł widocznych na stronie (obiekty Work albo kody), bez powtórzeń."""
    from corpus.models import Work

    items = [w for w in (works or []) if w]
    codes = [w for w in items if isinstance(w, str)]
    objs = [w for w in items if not isinstance(w, str)]
    if codes:
        objs += list(Work.objects.filter(code__in=codes))
    seen, rows = set(), []
    for w in objs:
        if w.code in seen:
            continue
        seen.add(w.code)
        rows.append((w.code, w.attribution_text))
    if not rows:
        return ""
    return format_html(
        '<p class="muted attribution">{}: {}</p>',
        label,
        format_html_join(" · ", "<b>{}</b> — {}", rows),
    )
