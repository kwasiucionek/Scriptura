import json
import re

import pytest
from django.test import Client

from corpus.importers.base import VerseRecord, import_records
from corpus.models import Work, WorkKind
from library.models import Author, Chunk, Document
from rag import service

pytestmark = pytest.mark.django_db


@pytest.fixture
def data():
    w = Work.objects.create(
        code="BG1632", name="BG", language="pl", kind=WorkKind.TRANSLATION
    )
    import_records(
        w,
        iter(
            [
                VerseRecord("Mark", 16, v, f"Werset {v} rozdziału szesnastego.")
                for v in range(1, 21)
            ]
        ),
    )
    a = Author.objects.create(name="Jan Testowy", slug="jan-testowy")
    d = Document.objects.create(
        title="O zakończeniu Marka", year=2024, journal="Test", access="open"
    )
    d.authors.add(a)
    Chunk.objects.create(
        document=d,
        order=0,
        section="1",
        text="Dłuższe zakończenie Mk 16,9-20 jest dodatkiem.",
        sigla=[{"ref": "Mk 16,9-20", "start": 48016009, "end": 48016020}],
    )
    Chunk.objects.create(
        document=d,
        order=1,
        section="2",
        text="Tajemnica mesjańska od Mk 1,34.",
        sigla=[{"ref": "Mk 1,34", "start": 48001034, "end": 48001034}],
    )
    priv = Document.objects.create(title="Prywatne", access="private")
    Chunk.objects.create(
        document=priv, order=0, text="Tajemnica mesjańska prywatnie.", sigla=[]
    )
    return d


def _events(resp):
    body = b"".join(resp.streaming_content).decode()
    return [
        (e, json.loads(d)) for e, d in re.findall(r"event: (\w+)\ndata: (.*)\n", body)
    ]


def test_ask_with_siglum_filters_chunks_and_adds_verses(data):
    c = Client(HTTP_HOST="localhost")
    ev = _events(
        c.post(
            "/ask/stream",
            data=json.dumps({"question": "Co wiadomo o Mk 16,9-20?"}),
            content_type="application/json",
        )
    )
    assert [e for e, _ in ev][0] == "sources" and ev[-1][0] == "done"
    src = ev[0][1]
    assert [s["section"] for s in src["chunks"]] == ["1"]
    assert len(src["verses"]) == 12 and src["verses"][0]["ref"] == "Mk 16,9"
    assert ev[-1][1]["citations"] == [1]


def test_private_documents_are_invisible_by_default(data):
    hits = service.retrieve("tajemnica mesjańska", k=5)
    assert [h.title for h in hits] == ["O zakończeniu Marka"]


def test_verify_refs(data):
    ok, bad = service.verify_refs("Zob. Mk 16,8 oraz Rdz 1,1.")
    assert ok == ["Mk 16,8"] and bad == ["Rdz 1,1"]


def test_refusal_when_nothing_found(data):
    c = Client(HTTP_HOST="localhost")
    ev = _events(
        c.post(
            "/ask/stream",
            data=json.dumps({"question": "Ile kosztuje bilet do muzeum?"}),
            content_type="application/json",
        )
    )
    assert ev[-1][1]["refused"] is True


def test_demo_token(settings, data):
    settings.DEMO_TOKEN = "sekret"
    c = Client(HTTP_HOST="localhost")
    assert (
        c.post("/ask/stream", data="{}", content_type="application/json").status_code
        == 401
    )
    r = c.post(
        "/ask/stream",
        data=json.dumps({"question": "Mk 16,8"}),
        content_type="application/json",
        HTTP_X_DEMO_TOKEN="sekret",
    )
    assert r.status_code == 200


def test_mode_filters_registers_and_changes_prompt(data, settings):
    from library.models import Document
    from rag import service

    Document.objects.filter(title="O zakończeniu Marka").update(register="popular")
    # soft (domyślnie): tryb nie zawęża zbioru źródeł, tylko poziom odpowiedzi i kolejność
    assert service.registers_for_mode("scientific") is None
    sci = service.retrieve(
        "tajemnica mesjańska", k=5, registers=service.registers_for_mode("scientific")
    )
    assert [h.title for h in sci] == ["O zakończeniu Marka"]

    def hit(i, title, score, register):
        return service.ChunkHit(
            chunk_id=i,
            document_id=i,
            order=0,
            title=title,
            authors=[],
            citation="",
            doc_type="article",
            year=None,
            url="",
            section="",
            text="",
            sigla=[],
            score=score,
            access="open",
            register=register,
        )

    ranked = service.apply_register_preference(
        [hit(1, "pop", 1.0, "popular"), hit(2, "sci", 0.9, "scientific")],
        "scientific",
        2,
    )
    assert [h.title for h in ranked] == ["sci", "pop"]  # 0.9*1.0 > 1.0*0.8
    # hard: tryb naukowy widzi tylko scientific/mixed
    settings.RAG_REGISTER_MODE = "hard"
    sci = service.retrieve(
        "tajemnica mesjańska", k=5, registers=service.registers_for_mode("scientific")
    )
    pop = service.retrieve(
        "tajemnica mesjańska", k=5, registers=service.registers_for_mode("popular")
    )
    assert sci == [] and [h.title for h in pop] == ["O zakończeniu Marka"]
    settings.RAG_REGISTER_MODE = "soft"
    msgs_pop = service.build_messages("q", [], [], mode="popular")
    msgs_sci = service.build_messages("q", [], [], mode="scientific")
    assert (
        "POPULARNONAUKOWY" in msgs_pop[0]["content"]
        and "NAUKOWY" in msgs_sci[0]["content"]
    )

    c = Client(HTTP_HOST="localhost")
    ev = _events(
        c.post(
            "/ask/stream",
            data=json.dumps(
                {"question": "Co wiadomo o Mk 16,9-20?", "mode": "popular"}
            ),
            content_type="application/json",
        )
    )
    assert ev[-1][1]["mode"] == "popular"


def test_expand_with_neighbors(data):
    from library.search import ChunkHit, expand_with_neighbors

    d = Document.objects.get(title="O zakończeniu Marka")
    hit = ChunkHit(chunk_id=0, document_id=d.id, order=1, title=d.title, authors=[], citation="", doc_type="article",
                   year=None, url="", section="2", text="Tajemnica mesjańska od Mk 1,34.", sigla=[], score=1.0, access="open")  # fmt: skip
    (out,) = expand_with_neighbors([hit], 1)
    assert (
        out.text.startswith("Dłuższe zakończenie") and "Tajemnica mesjańska" in out.text
    )
    assert expand_with_neighbors([hit], 0)[0].text.count("\n") == out.text.count(
        "\n"
    )  # radius 0 bez zmian (obiekt już rozszerzony)


def test_diversify_caps_per_document_and_merges_neighbors():
    from library.search import ChunkHit, diversify

    def hit(doc, order):
        return ChunkHit(
            chunk_id=doc * 100 + order,
            document_id=doc,
            order=order,
            title=f"d{doc}",
            authors=[],
            citation="",
            doc_type="article",
            year=None,
            url="",
            section="",
            text="",
            sigla=[],
            score=1.0,
            access="open",
        )

    hits = [
        hit(1, 5),
        hit(1, 6),
        hit(1, 9),
        hit(1, 20),
        hit(1, 30),
        hit(2, 1),
        hit(3, 4),
        hit(2, 2),
        hit(4, 7),
    ]
    out = diversify(hits, max_per_doc=3, k=6)
    assert [(h.document_id, h.order) for h in out] == [
        (1, 5),
        (1, 9),
        (1, 20),
        (2, 1),
        (3, 4),
        (4, 7),
    ]
    assert diversify(hits, 0, 4) == hits[:4]


def test_transliterated_term_hits_lexicon_and_verses(data):
    from corpus.books import BOOKS
    from corpus.models import Book, Lexeme, Token, Verse, VerseText, Work, WorkKind
    from rag import service

    Lexeme.objects.create(
        strong="H2617a",
        language="hbo",
        lemma="חֶסֶד",
        lemma_norm="חסד",
        transliteration="che.sed",
        translit_fold="hesed",
        morph="H:N-M",
        gloss="kindness",
        meaning="1) goodness, kindness, faithfulness",
    )
    spec = next(b for b in BOOKS if b.osis == "Exod")
    book, _ = Book.objects.get_or_create(
        osis="Exod",
        defaults={
            "order": spec.order,
            "testament": spec.testament,
            "abbr": "Wj",
            "name_pl": "Wyjścia",
        },
    )
    wlc, _ = Work.objects.get_or_create(
        code="WLC",
        defaults={"name": "WLC", "language": "hbo", "kind": WorkKind.ORIGINAL},
    )
    bg, _ = Work.objects.get_or_create(
        code="BG1632",
        defaults={"name": "BG", "language": "pl", "kind": WorkKind.TRANSLATION},
    )
    v = Verse.objects.create(
        book=book,
        chapter=34,
        verse=6,
        ordinal=spec.order * 10**6 + 34006,
        osis_id="Exod.34.6",
    )
    vt = VerseText.objects.create(verse=v, work=wlc, text="רַב־חֶסֶד וֶאֱמֶת")
    VerseText.objects.create(verse=v, work=bg, text="obfity w miłosierdzie i prawdę")
    Token.objects.create(
        verse_text=vt,
        position=1,
        surface="חֶסֶד",
        surface_norm="חסד",
        lemma="חֶסֶד",
        lemma_norm="חסד",
        strong="H2617a",
    )

    lx = service.lexemes_from_transliteration("Co znaczy hebrajskie słowo hesed?")
    assert [x.strong for x in lx] == ["H2617a"]
    assert (
        service.lexemes_from_transliteration("Czy Abraham istniał historycznie?") == []
    )
    lines = service.lexicon_lines("Co znaczy chesed w Biblii?", [])
    assert (
        lines
        and lines[0].startswith("H2617a חֶסֶד (che.sed) — kindness")
        and "1 wystąpień" in lines[0]
        and "Wj 1" in lines[0]
    )
    verses = service.lexeme_verses_for_question("Co znaczy hesed?", ["WLC", "BG1632"])
    assert {(x.ref, x.work) for x in verses} == {
        ("Wj 34,6", "WLC"),
        ("Wj 34,6", "BG1632"),
    }


def test_llm_reranker_accepts_all_zero_scores(settings, monkeypatch):
    from library import rerank
    from library.search import ChunkHit

    def hit(i):
        return ChunkHit(
            chunk_id=i,
            document_id=i,
            order=0,
            title=f"t{i}",
            authors=[],
            citation="",
            doc_type="article",
            year=None,
            url="",
            section="",
            text="x",
            sigla=[],
            score=1.0,
            access="open",
        )

    settings.RERANKER_BACKEND = "llm"
    hits = [hit(1), hit(2), hit(3)]
    monkeypatch.setattr(
        "library.curate.chat_json",
        lambda *a, **k: [{"i": 1, "s": 0}, {"i": 2, "s": 0}, {"i": 3, "s": 0}],
    )
    assert rerank.rerank("q", hits, 8) == []  # same zera = nic na temat, nie awaria
    monkeypatch.setattr(
        "library.curate.chat_json",
        lambda *a, **k: [{"i": 2, "s": 3}, {"i": 1, "s": 0.5}, {"i": 3, "s": 1}],
    )
    assert [h.chunk_id for h in rerank.rerank("q", hits, 8)] == [2, 3]
    monkeypatch.setattr("library.curate.chat_json", lambda *a, **k: [])
    assert (
        rerank.rerank("q", hits, 2) == hits[:2]
    )  # brak ocen = awaria -> kolejność RRF
