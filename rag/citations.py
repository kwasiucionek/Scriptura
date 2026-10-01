"""Eksport cytowań z odpowiedzi do BibTeX / RIS.

Wejście: payload źródeł (chunks, verses, ane, patristics) + treść odpowiedzi (tylko źródła
przywołane jako [n] / [Pn] / [An]; tylko przy pustej historycznej odpowiedzi — wszystkie).
Dokumenty biblioteki
uzupełniane z bazy (journal, volume, pages, doi). Autorzy normalizowani do „Nazwisko, Imię”
niezależnie od formy w bazie. Ojcowie: @incollection w Ante-Nicene / Nicene and Post-Nicene
Fathers (tom, red. Schaff, rok serii) z listą przywołanych stron; ANE: @misc (eBL).
"""

import re
import unicodedata

from rag.access import visible_documents
from rag.quotes import check_citations

_TYPE_BIB = {
    "article": "article",
    "book": "book",
    "script": "book",
    "blog": "online",
    "video": "misc",
    "lexicon": "book",
    "review": "article",
}
_TYPE_RIS = {
    "article": "JOUR",
    "book": "BOOK",
    "script": "BOOK",
    "blog": "ELEC",
    "video": "GEN",
    "lexicon": "BOOK",
    "review": "JOUR",
}
_SERIES = {  # seria -> (pełna nazwa, rok pierwszego wydania, redaktor)
    "ANF": ("Ante-Nicene Fathers", 1885, "Alexander Roberts and James Donaldson"),
    "NPNF1": ("Nicene and Post-Nicene Fathers, Series I", 1886, "Philip Schaff"),
    "NPNF2": (
        "Nicene and Post-Nicene Fathers, Series II",
        1890,
        "Philip Schaff and Henry Wace",
    ),
}
_ANON = (
    "anonim",
    "list ",
    "pseudo-",
    "councils",
    "various",
)  # „autorzy”, którzy są tytułem dzieła / anonimem
_STOP = {"this", "that", "from", "with", "czyli", "oraz", "jako", "przez"}


def _ascii(text: str) -> str:
    t = unicodedata.normalize("NFKD", text)
    return "".join(c for c in t if not unicodedata.combining(c) and c.isascii())


def split_name(name: str) -> tuple[str, str]:
    """'Marcin Majewski' | 'Majewski, Marcin' -> ('Majewski', 'Marcin'); jeden wyraz -> (wyraz, '')."""
    name = " ".join(name.split())
    if "," in name:
        last, _, first = name.partition(",")
        return last.strip(), first.strip()
    parts = name.split(" ")
    return (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (name, "")


def canonical_name(name: str) -> str:
    last, first = split_name(name)
    return f"{last}, {first}" if first else last


def _key(surname: str, year, title: str, used: set[str]) -> str:
    surname = re.sub(r"[^a-z]", "", _ascii(surname).lower()) or "anon"
    word = next(
        (w for w in re.findall(r"[a-z]{4,}", _ascii(title).lower()) if w not in _STOP),
        "work",
    )
    base = f"{surname}{year or ''}{word}"
    key, i = base, 1
    while key in used:
        i += 1
        key = f"{base}{i}"
    used.add(key)
    return key


def cited_numbers(answer: str) -> tuple[set[int], set[int], set[int]]:
    """(literatura, Ojcowie, ANE), using the same marker grammar as quote checks.

    Availability is checked later against sources and SQL ACL. Ignore complete
    malformed markers, including any partial refs retained for diagnostics.
    """
    lit: set[int] = set()
    pat: set[int] = set()
    ane: set[int] = set()
    for citation in check_citations(answer or "", set()):
        if citation.reason not in ("", "missing_source"):
            continue
        for ref in citation.refs:
            label = ref[1:-1]  # canonical [n], [Pn], [An] from the shared parser
            if label.startswith("P"):
                pat.add(int(label[1:]))
            elif label.startswith("A"):
                ane.add(int(label[1:]))
            else:
                lit.add(int(label))
    return lit, pat, ane


def validate_sources(sources: dict, answer: str) -> None:
    """Validate untrusted export JSON before DB queries or formatters."""
    if not isinstance(sources, dict) or not isinstance(answer, str):
        raise ValueError("sources musi być obiektem, answer tekstem")
    for kind in ("chunks", "patristics", "ane", "verses"):
        rows = sources.get(kind, [])
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError(f"{kind} musi być listą (maks. 1000 źródeł)")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Źródło musi być obiektem")
            for key in (
                "title",
                "doc_type",
                "url",
                "author",
                "work",
                "ref",
                "text",
                "license",
            ):
                if key in row and not isinstance(row[key], str):
                    raise ValueError(f"{key} musi być tekstem")
            if "authors" in row and (
                not isinstance(row["authors"], list)
                or not all(isinstance(a, str) for a in row["authors"])
            ):
                raise ValueError("authors musi być listą tekstów")
            for key in ("document_id", "n", "year"):
                value = row.get(key)
                if value is not None and (
                    type(value) is not int or not 0 < value <= 2**63 - 1
                ):
                    raise ValueError(f"Niepoprawne {key}")


def build_entries(sources: dict, answer: str = "", *, user=None) -> list[dict]:
    """Library metadata comes ONLY from currently accessible SQL documents.

    Missing/deleted/hidden IDs (including chunks without IDs) are omitted, never
    replaced by client-supplied metadata. The caller must pass the requesting user.
    Only an empty/blank historical answer exports all accessible supplied sources;
    a nonempty answer with no valid markers exports nothing.
    """
    validate_sources(sources, answer)
    lit_n, pat_n, ane_n = cited_numbers(answer)
    chunks = sources.get("chunks") or []
    patristics = sources.get("patristics") or []
    ane = sources.get("ane") or []
    if answer.strip():
        chunks = [c for c in chunks if c.get("n") in lit_n]
        patristics = [h for i, h in enumerate(patristics, start=1) if i in pat_n]
        ane = [h for i, h in enumerate(ane, start=1) if i in ane_n]
    docs = {
        d.id: d
        for d in visible_documents(user)
        .filter(id__in=[c.get("document_id") for c in chunks if c.get("document_id")])
        .prefetch_related("authors")
    }
    used: set[str] = set()
    out: list[dict] = []
    seen: set = set()

    for c in chunks:
        did = c.get("document_id")
        d = docs.get(did)
        if d is None or did in seen:
            continue
        seen.add(did)
        raw_authors = [a.name for a in d.authors.all()]
        authors = [canonical_name(a) for a in raw_authors]
        title = d.title
        year = d.year
        out.append({
            "type": d.doc_type or "article",
            "key": _key(split_name(raw_authors[0])[0] if raw_authors else "", year, title, used),
            "authors": authors, "title": title, "year": year,
            "journal": d.journal, "volume": d.volume, "pages": d.pages,
            "doi": d.doi, "url": d.url or "", "note": "",
            "editors": "", "booktitle": "",
        })  # fmt: skip

    grouped: dict[tuple, dict] = {}
    for (
        h
    ) in patristics:  # jedno dzieło = jeden wpis; strony przywołanych pasaży zebrane
        k = (h.get("author"), h.get("work"))
        m = re.search(r"\((ANF|NPNF[12]) (\d+)(?:, s\. ([^)]+))?\)", h.get("ref", ""))
        series, vol, page = (m.group(1), m.group(2), m.group(3)) if m else ("", "", "")
        e = grouped.get(k)
        if not e:
            author = h.get("author", "") or ""
            anon = not author or author.lower().startswith(_ANON)
            name, year, editors = _SERIES.get(
                series, ("Christian Classics Ethereal Library", None, "")
            )
            e = grouped[k] = {
                "type": "patristic", "key": _key("" if anon else re.split(r"\s+z\s+|\s+(?:of|the)\s+", author)[0].replace(" ", ""), year, h.get("work", ""), used),
                "authors": [] if anon else [author], "title": h.get("work", ""), "year": year,
                "journal": f"{series} {vol}".strip(), "volume": vol, "pages": "", "doi": "", "url": h.get("url", ""),
                "note": "", "editors": editors, "booktitle": f"{name}, vol. {vol}" if vol else name, "_pages": [],
            }  # fmt: skip
            if anon and author:
                e["note"] = author  # np. „List Barnaby” jako opis anonimowego dzieła
        if page and page not in e["_pages"]:
            e["_pages"].append(page)
    for e in grouped.values():
        pages = e.pop("_pages")
        e["pages"] = ", ".join(pages)
        e["note"] = "; ".join(
            x
            for x in [
                e["note"],
                "Christian Classics Ethereal Library (domena publiczna)",
            ]
            if x
        )
        out.append(e)

    seen_ane: set = set()
    for h in ane:
        k = h.get("text")
        if k in seen_ane:
            continue
        seen_ane.add(k)
        out.append({
            "type": "ane", "key": _key("", None, h.get("text", "ane"), used),
            "authors": [], "title": h.get("text", ""), "year": None,
            "journal": "electronic Babylonian Library (LMU München)", "volume": "", "pages": "", "doi": "",
            "url": h.get("url", ""), "note": h.get("license", ""), "editors": "", "booktitle": "",
        })  # fmt: skip
    return out


def _bib_escape(s: str) -> str:
    return (s or "").replace("{", "\\{").replace("}", "\\}").replace("&", "\\&")


def to_bibtex(entries: list[dict]) -> str:
    out = []
    for e in entries:
        kind = {"patristic": "incollection", "ane": "misc"}.get(
            e["type"]
        ) or _TYPE_BIB.get(e["type"], "misc")
        fields = [
            ("author", " and ".join(e["authors"])),
            ("title", e["title"]),
            ("year", e["year"]),
        ]
        if e["type"] in ("article", "review"):
            fields += [
                ("journal", e["journal"]),
                ("volume", e["volume"]),
                ("pages", e["pages"]),
            ]
        elif e["type"] == "patristic":
            fields += [
                ("booktitle", e["booktitle"]),
                ("editor", e["editors"]),
                ("volume", e["volume"]),
                ("pages", e["pages"]),
                ("note", e["note"]),
            ]
        elif e["type"] == "ane":
            fields += [("howpublished", e["journal"]), ("note", e["note"])]
        else:
            fields += [("publisher", e["journal"]), ("pages", e["pages"])]
        fields += [("doi", e["doi"]), ("url", e["url"])]
        body = ",\n".join(f"  {k} = {{{_bib_escape(str(v))}}}" for k, v in fields if v)
        out.append(f"@{kind}{{{e['key']},\n{body}\n}}")
    return "\n\n".join(out) + "\n"


def to_ris(entries: list[dict]) -> str:
    out = []
    for e in entries:
        ty = {"patristic": "CHAP", "ane": "GEN"}.get(e["type"]) or _TYPE_RIS.get(
            e["type"], "GEN"
        )
        lines = (
            [f"TY  - {ty}"]
            + [f"AU  - {a}" for a in e["authors"]]
            + [f"TI  - {e['title']}"]
        )
        if e["year"]:
            lines.append(f"PY  - {e['year']}")
        if e["type"] == "patristic":
            if e["booktitle"]:
                lines.append(f"T2  - {e['booktitle']}")
            lines += [
                f"A2  - {ed.strip()}"
                for ed in e["editors"].split(" and ")
                if ed.strip()
            ]
        elif e["journal"]:
            lines.append(
                f"{'T2' if e['type'] in ('article', 'review') else 'PB'}  - {e['journal']}"
            )
        if e["volume"]:
            lines.append(f"VL  - {e['volume']}")
        if e["pages"]:
            if e["type"] == "patristic":
                lines.append(f"SP  - {e['pages']}")
            else:
                a, _, b = e["pages"].partition("-")
                lines.append(f"SP  - {a.strip()}")
                if b:
                    lines.append(f"EP  - {b.strip('– ')}")
        if e["doi"]:
            lines.append(f"DO  - {e['doi']}")
        if e["url"]:
            lines.append(f"UR  - {e['url']}")
        if e["note"]:
            lines.append(f"N1  - {e['note']}")
        lines.append("ER  - ")
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"
