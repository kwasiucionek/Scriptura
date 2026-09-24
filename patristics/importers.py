"""Import tomów CCEL (ThML) do bazy patrystycznej.

Tom -> parse_volume -> dzieła (div1) -> pasaże + PatRef. Autor dzieła: z tabeli VOLUMES (mapa
tom -> autorzy) i heurystyki po tytule dzieła („Irenaeus Against Heresies” -> Irenaeus), z polską
formą nazwiska z AUTHORS_PL. Nieznany autor = pusty (do uzupełnienia w adminie).
"""

import json
import re
import urllib.request
from pathlib import Path

from django.db import transaction

from patristics.models import PatPassage, PatRef, PatWork
from patristics.thml import WorkDraft

CCEL_XML = "https://www.ccel.org/ccel/schaff/{ccel_id}.xml"

# tom -> (seria, numer, autorzy w kolejności występowania)
VOLUMES: dict[str, tuple[str, int, list[str]]] = {
    "anf01": ("ANF", 1, ["Clement of Rome", "Mathetes", "Polycarp", "Ignatius", "Barnabas", "Papias", "Justin Martyr", "Irenaeus"]),
    "anf02": ("ANF", 2, ["Hermas", "Tatian", "Theophilus", "Athenagoras", "Clement of Alexandria"]),
    "anf03": ("ANF", 3, ["Tertullian"]),
    "anf04": ("ANF", 4, ["Tertullian", "Minucius Felix", "Commodian", "Origen"]),
    "anf05": ("ANF", 5, ["Hippolytus", "Cyprian", "Caius", "Novatian"]),
    "anf06": ("ANF", 6, ["Gregory Thaumaturgus", "Dionysius", "Julius Africanus", "Anatolius", "Methodius", "Arnobius"]),
    "anf07": ("ANF", 7, ["Lactantius", "Venantius", "Asterius", "Victorinus", "Dionysius"]),
    "anf08": ("ANF", 8, ["Various"]),
    "anf09": ("ANF", 9, ["Origen", "Various"]),
    "npnf101": ("NPNF1", 1, ["Augustine"]), "npnf102": ("NPNF1", 2, ["Augustine"]), "npnf103": ("NPNF1", 3, ["Augustine"]),
    "npnf104": ("NPNF1", 4, ["Augustine"]), "npnf105": ("NPNF1", 5, ["Augustine"]), "npnf106": ("NPNF1", 6, ["Augustine"]),
    "npnf107": ("NPNF1", 7, ["Augustine"]), "npnf108": ("NPNF1", 8, ["Augustine"]),
    "npnf109": ("NPNF1", 9, ["Chrysostom"]), "npnf110": ("NPNF1", 10, ["Chrysostom"]), "npnf111": ("NPNF1", 11, ["Chrysostom"]),
    "npnf112": ("NPNF1", 12, ["Chrysostom"]), "npnf113": ("NPNF1", 13, ["Chrysostom"]), "npnf114": ("NPNF1", 14, ["Chrysostom"]),
    "npnf201": ("NPNF2", 1, ["Eusebius"]), "npnf202": ("NPNF2", 2, ["Socrates", "Sozomen"]), "npnf203": ("NPNF2", 3, ["Theodoret", "Jerome", "Gennadius", "Rufinus"]),
    "npnf204": ("NPNF2", 4, ["Athanasius"]), "npnf205": ("NPNF2", 5, ["Gregory of Nyssa"]), "npnf206": ("NPNF2", 6, ["Jerome"]),
    "npnf207": ("NPNF2", 7, ["Cyril of Jerusalem", "Gregory Nazianzen"]), "npnf208": ("NPNF2", 8, ["Basil"]), "npnf209": ("NPNF2", 9, ["Hilary of Poitiers", "John of Damascus"]),
    "npnf210": ("NPNF2", 10, ["Ambrose"]), "npnf211": ("NPNF2", 11, ["Sulpitius Severus", "Vincent of Lerins", "John Cassian"]), "npnf212": ("NPNF2", 12, ["Leo the Great", "Gregory the Great"]),
    "npnf213": ("NPNF2", 13, ["Gregory the Great", "Ephraim Syrus", "Aphrahat"]), "npnf214": ("NPNF2", 14, ["Councils"]),
}  # fmt: skip

AUTHORS_PL = {
    "Clement of Rome": "Klemens Rzymski", "Polycarp": "Polikarp ze Smyrny", "Ignatius": "Ignacy Antiocheński",
    "Justin Martyr": "Justyn Męczennik", "Irenaeus": "Ireneusz z Lyonu", "Hermas": "Hermas", "Tatian": "Tacjan",
    "Theophilus": "Teofil z Antiochii", "Athenagoras": "Atenagoras", "Clement of Alexandria": "Klemens Aleksandryjski",
    "Tertullian": "Tertulian", "Origen": "Orygenes", "Hippolytus": "Hipolit Rzymski", "Cyprian": "Cyprian z Kartaginy",
    "Novatian": "Nowacjan", "Gregory Thaumaturgus": "Grzegorz Cudotwórca", "Methodius": "Metody z Olimpu",
    "Lactantius": "Laktancjusz", "Augustine": "Augustyn z Hippony", "Chrysostom": "Jan Chryzostom",
    "Eusebius": "Euzebiusz z Cezarei", "Jerome": "Hieronim", "Athanasius": "Atanazy Wielki",
    "Gregory of Nyssa": "Grzegorz z Nyssy", "Cyril of Jerusalem": "Cyryl Jerozolimski", "Gregory Nazianzen": "Grzegorz z Nazjanzu",
    "Basil": "Bazyli Wielki", "Hilary of Poitiers": "Hilary z Poitiers", "John of Damascus": "Jan Damasceński",
    "Ambrose": "Ambroży z Mediolanu", "John Cassian": "Jan Kasjan", "Leo the Great": "Leon Wielki",
    "Gregory the Great": "Grzegorz Wielki", "Ephraim Syrus": "Efrem Syryjczyk", "Aphrahat": "Afrahat",
    "Theodoret": "Teodoret z Cyru", "Rufinus": "Rufin z Akwilei", "Socrates": "Sokrates Scholastyk", "Sozomen": "Sozomen",
    "Minucius Felix": "Minucjusz Feliks", "Barnabas": "Pseudo-Barnaba", "Papias": "Papiasz", "Mathetes": "Anonim (List do Diogneta)",
}  # fmt: skip


def fetch_volume(ccel_id: str, cache_dir: Path) -> bytes:
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / f"{ccel_id}.xml"
    if not dest.exists():
        req = urllib.request.Request(
            CCEL_XML.format(ccel_id=ccel_id),
            headers={"User-Agent": "scriptura-patristics/0.1"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310
            dest.write_bytes(resp.read())
    return dest.read_bytes()


def guess_author(title: str, candidates: list[str], current: str) -> str:
    """Autor dzieła z tytułu div1 („Irenaeus Against Heresies”, „The Epistles of Ignatius”), inaczej poprzedni."""
    t = _fold(title).lower()
    for c in candidates:
        key = _fold(c.split(" of ")[0].split(" the ")[0]).lower()
        if key and re.search(rf"\b{re.escape(key)}\b", t):
            return c
    return current or (candidates[0] if len(candidates) == 1 else "")


def _fold(text: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", text.replace("Æ", "AE").replace("æ", "ae"))
    return "".join(c for c in t if not unicodedata.combining(c))


@transaction.atomic
def save_volume(ccel_id: str, works: list[WorkDraft]) -> tuple[int, int]:
    series, volume, candidates = VOLUMES.get(ccel_id, ("CCEL", 0, []))
    PatWork.objects.filter(
        ccel_id=ccel_id
    ).delete()  # reimport tomu = od zera (stare podziały znikają)
    author = ""
    n_works = n_pass = 0
    for w in works:
        # w.author = nazwa z div1 (np. „Irenaeus”, „Justin Martyr”) -> kanoniczna z listy tomu
        author = guess_author(w.author or w.title, candidates, author)
        pw, _ = PatWork.objects.update_or_create(
            ccel_id=ccel_id, author=author, title=w.title[:300],
            defaults={"series": series, "volume": volume, "author_pl": AUTHORS_PL.get(author, ""),
                      "url": f"https://www.ccel.org/ccel/schaff/{ccel_id}.html"},
        )  # fmt: skip
        pw.passages.all().delete()
        objs = PatPassage.objects.bulk_create(
            [
                PatPassage(
                    work=pw,
                    order=i,
                    section=p.section[:300],
                    page=p.page[:16],
                    text_en=p.text,
                )
                for i, p in enumerate(w.passages)
            ]
        )
        refs = []
        for obj, draft in zip(objs, w.passages, strict=True):
            refs += [PatRef(passage=obj, start=s, end=e) for s, e in draft.refs]
        PatRef.objects.bulk_create(refs, batch_size=2000)
        n_works += 1
        n_pass += len(objs)
    return n_works, n_pass


def dump_authors(path: Path) -> None:
    path.write_text(
        json.dumps(AUTHORS_PL, ensure_ascii=False, indent=1), encoding="utf-8"
    )
