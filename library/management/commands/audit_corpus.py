"""Audyt zaindeksowanych dokumentów PO TREŚCI (tytuł + początek tekstu), nie po metadanych.

    python manage.py audit_corpus                        # raport dla wszystkich dokumentów
    python manage.py audit_corpus --documents 61 62 --apply
    python manage.py audit_corpus --apply --delete       # poprawia rejestr/język/typ i USUWA nieistotne

Model ocenia: czy to teologia/biblistyka (czy imiennik autora), rejestr, język, typ.
--apply zapisuje rejestr/język/typ; --delete dodatkowo usuwa dokumenty relevant=false
(z pewnością >= CURATE_REVIEW_CONFIDENCE) i reindeksuje. Raport zawsze w data/manifests/audit-<data>.md.
"""

from datetime import datetime

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from library.curate import chat_json, chat_json_per_item
from library.models import Chunk, Document

AUDIT_PROMPT = """Jesteś kuratorem korpusu RAG z zakresu teologii biblijnej. Dla każdego dokumentu (tytuł, autorzy,
początek tekstu) zwróć JSON — listę obiektów w tej samej kolejności:
{{"i": <numer>, "relevant": true|false, "register": "scientific|popular|mixed", "language": "pl|en|it|de|fr|la|other",
  "kind": "article|review|report|editorial|book|other", "confidence": 0.0-1.0, "reason": "<jedno zdanie po polsku>"}}
relevant=false, gdy tekst dotyczy innej dziedziny niż teologia/biblistyka/religioznawstwo/historia Kościoła/
filologia biblijna (praca imiennika) albo jest bez wartości merytorycznej (sprawozdanie, wstępniak, spis treści).
Recenzje książek teologicznych są relevant. Oceniaj po treści. Zwróć TYLKO JSON.

DOKUMENTY:
{items}"""


class Command(BaseCommand):
    help = "Audyt korpusu po treści: istotność, rejestr, język, typ (model)"

    def add_arguments(self, parser):
        parser.add_argument("--documents", nargs="*", type=int)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--delete", action="store_true", help="z --apply: usuń dokumenty nieistotne"
        )
        parser.add_argument("--batch", type=int, default=6)

    def handle(self, *args, **o):
        qs = Document.objects.prefetch_related("authors").order_by("id")
        if o["documents"]:
            qs = qs.filter(id__in=o["documents"])
        docs = list(qs)
        lines = [f"# Audyt korpusu — {datetime.now():%Y-%m-%d %H:%M}\n"]
        to_delete: list[int] = []
        changed = 0
        for start in range(0, len(docs), o["batch"]):
            batch = docs[start : start + o["batch"]]
            items = []
            for n, d in enumerate(batch, start=1):
                text = " ".join(
                    c.text
                    for c in Chunk.objects.filter(document=d).order_by("order")[:2]
                )[:1500]
                authors = ", ".join(a.name for a in d.authors.all())
                items.append(
                    f"{n}. [{d.id}] {d.title}\n   autorzy: {authors or '?'}; typ: {d.doc_type}; język: {d.language}\n   tekst: {text}"
                )
            try:
                rows = chat_json(AUDIT_PROMPT.format(items="\n".join(items)))
            except Exception as exc:
                lines.append(
                    f"- ⚠ partia od #{batch[0].id}: model nie odpowiedział ({str(exc)[:120]}) — pozycja po pozycji"
                )
                rows = chat_json_per_item(AUDIT_PROMPT, items)
                if not rows:
                    lines.append(f"- ⚠ partia od #{batch[0].id}: pominięta w całości")
                    continue
            rows = [r for r in rows if isinstance(r, dict)]
            by_i = {}
            for r in rows:
                try:
                    by_i[int(r.get("i", 0))] = r
                except (TypeError, ValueError):
                    pass
            for n, d in enumerate(batch, start=1):
                # model zwraca numer pozycji ALBO id dokumentu; przy równej liczbie wyników — kolejność
                r = by_i.get(n) or by_i.get(d.id)
                if r is None and len(rows) == len(batch):
                    r = rows[n - 1]
                if not r:
                    lines.append(
                        f"- ⚠ #{d.id} {d.title[:70]} — brak oceny w odpowiedzi modelu"
                    )
                    continue
                conf = float(r.get("confidence", 0.5) or 0.5)
                relevant = bool(r.get("relevant", True))
                mark = (
                    "✔"
                    if relevant
                    else ("✖" if conf >= settings.CURATE_REVIEW_CONFIDENCE else "?")
                )
                lines.append(
                    f"- {mark} #{d.id} {d.title[:70]} — {r.get('register', '?')}/{r.get('language', '?')}/{r.get('kind', '?')} ({conf:.2f}): {r.get('reason', '')}"
                )
                if not relevant and conf >= settings.CURATE_REVIEW_CONFIDENCE:
                    to_delete.append(d.id)
                    continue
                if o["apply"]:
                    upd = {}
                    if (
                        r.get("register") in ("scientific", "popular", "mixed")
                        and r["register"] != d.register
                    ):
                        upd["register"] = r["register"]
                    lang = r.get("language", "")
                    if lang and lang != "other" and lang != d.language:
                        upd["language"] = lang
                    if (
                        r.get("kind") in ("article", "book", "lexicon", "review")
                        and r["kind"] != d.doc_type
                    ):
                        upd["doc_type"] = r["kind"]
                    if upd:
                        Document.objects.filter(id=d.id).update(**upd)
                        changed += 1
                        lines.append(f"    zmieniono: {upd}")
        if to_delete:
            lines.append(f"\nNieistotne (do usunięcia): {to_delete}")
            if o["apply"] and o["delete"]:
                Document.objects.filter(id__in=to_delete).delete()
                lines.append(f"Usunięto {len(to_delete)} dokumentów.")
                if settings.SEARCH_BACKEND == "opensearch":
                    call_command("reindex_chunks", "--recreate", "--reuse-vectors")
        out = settings.MANIFESTS_DIR / f"audit-{datetime.now():%Y-%m-%d}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
        self.stdout.write("\n".join(lines))
        self.stdout.write(
            self.style.SUCCESS(
                f"dokumentów: {len(docs)}, zmienionych: {changed}, nieistotnych: {len(to_delete)} — raport: {out}"
            )
        )
