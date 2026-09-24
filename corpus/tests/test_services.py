import pytest

from corpus.importers.base import TokenRecord, VerseRecord, import_records
from corpus.models import Work, WorkKind
from corpus.services import text as svc

pytestmark = pytest.mark.django_db


@pytest.fixture
def corpus():
    gnt = Work.objects.create(
        code="SBLGNT", name="GNT", language="grc", kind=WorkKind.ORIGINAL
    )
    pl = Work.objects.create(
        code="BG1632", name="BG", language="pl", kind=WorkKind.TRANSLATION
    )
    import_records(
        gnt,
        iter(
            [
                VerseRecord(
                    "John",
                    1,
                    1,
                    "Ἐν ἀρχῇ ἦν ὁ λόγος",
                    [
                        TokenRecord("Ἐν", lemma="ἐν", pos="P-"),
                        TokenRecord("ἀρχῇ", lemma="ἀρχή", pos="N-"),
                        TokenRecord("ἦν", lemma="εἰμί", pos="V-"),
                        TokenRecord("ὁ", lemma="ὁ", pos="RA"),
                        TokenRecord("λόγος", lemma="λόγος", pos="N-"),
                    ],
                ),
                VerseRecord(
                    "John",
                    1,
                    2,
                    "οὗτος ἦν ἐν ἀρχῇ",
                    [TokenRecord("ἀρχῇ", lemma="ἀρχή")],
                ),
            ]
        ),
    )
    import_records(
        pl,
        iter(
            [
                VerseRecord("John", 1, 1, "Na początku było Słowo."),
                VerseRecord("John", 1, 2, "To było na początku u Boga."),
                VerseRecord("John", 1, 3, "Wszystko przez nie się stało."),
            ]
        ),
    )
    return gnt, pl


def test_parallel(corpus):
    rows = svc.parallel(svc.resolve("J 1,1-2"), svc.active_works())
    assert [str(r.verse) for r in rows] == ["J 1,1", "J 1,2"]
    assert set(rows[0].texts) == {"SBLGNT", "BG1632"}
    assert [t.lemma for t in rows[0].texts["SBLGNT"].tokens.all()][:2] == ["ἐν", "ἀρχή"]


def test_concordance_by_lemma(corpus):
    res = svc.concordance(lemma="αρχη")  # bez akcentów też trafia
    assert res.total == 2
    assert res.by_book == [{"abbr": "J", "order": 50, "n": 2}]


def test_lexical_words_and_phrase(corpus):
    works = svc.active_works()
    assert svc.lexical("początku", works).total == 2
    assert svc.lexical('"na początku było"', works).total == 1
    assert svc.lexical("początku -boga", works).total == 1
    r = svc.lexical("początku", works)
    assert "<mark>początku</mark>" in r.hits[0].html
    assert r.by_book == [{"abbr": "J", "n": 2}]


def test_lexical_cross_corpus(corpus):
    works = svc.active_works(["BG1632"])
    hits = svc.lexical("lemma:λόγος początku", works).hits
    assert [h.ref for h in hits] == ["J 1,1"]


def test_resolve_error():
    with pytest.raises(svc.ReferenceError):
        svc.resolve("nic takiego")
