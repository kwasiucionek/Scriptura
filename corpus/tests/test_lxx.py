from corpus.importers.lxx import parse_tokens, psalm_to_mt


def test_psalm_mapping():
    assert psalm_to_mt(1, 1) == (1, 1)
    assert psalm_to_mt(9, 21) == (9, 21) and psalm_to_mt(9, 22) == (10, 1)
    assert psalm_to_mt(22, 1) == (23, 1)
    assert psalm_to_mt(113, 8) == (114, 8) and psalm_to_mt(113, 9) == (115, 1)
    assert psalm_to_mt(114, 9) == (116, 9) and psalm_to_mt(115, 1) == (116, 10)
    assert psalm_to_mt(146, 11) == (147, 11) and psalm_to_mt(147, 1) == (147, 12)
    assert psalm_to_mt(150, 6) == (150, 6) and psalm_to_mt(151, 1) is None


def test_parse_tokens():
    text = "ἀόρατος<S>701318</S><m>lxx.A.NSM</m><S>517</S><S>70518</S> ἀκατασκεύαστος<S>700529</S><m>lxx.A.NSM</m> <i>[/8]</i> καὶ<S>707031</S><m>lxx.C</m><S>2532</S><S>72518</S>"
    toks = parse_tokens(
        text, {"701318": "ἀόρατος", "700529": "ἀκατασκεύαστος", "707031": "καί"}
    )
    assert [(t.surface, t.lemma, t.strong, t.morph, t.pos) for t in toks] == [
        ("ἀόρατος", "ἀόρατος", "G517", "A.NSM", "A"),
        ("ἀκατασκεύαστος", "ἀκατασκεύαστος", "", "A.NSM", "A"),
        ("καὶ", "καί", "G2532", "C", "C"),
    ]


def test_step_parse_and_strong_normalization(tmp_path):
    from corpus.importers.step import normalize_strong, parse_file

    assert (
        normalize_strong("H0001") == "H1"
        and normalize_strong("H1254a") == "H1254a"
        and normalize_strong("G3056") == "G3056"
    )
    f = tmp_path / "TBESH.txt"
    f.write_text(
        "TBESH - naglowek\n\tlicencja\neStrong#\tdStrong\tuStrong\tHebrew\tTransliteration\tMorph\tGloss\tMeaning\n\n"
        "H0001\tH0001G =\tH0001G\tאָב\tav\tH:N-M\tfather\t1) father of an individual<br>2) of God\n"
        "H0001\tH0001H =\tH2438H\tאָב\tav\tH:N-M\t(Huram)-abi\tA man\n"
        "H1254a\tH1254A =\tH1254A\tבָּרָא\tba.ra\tH:V\tto create\t1) to create, shape, form\n"
        "H0002\tH0002 =\tH0001G\tאַב\tav\tA:N-M\tfather\tAramaic of av\n",
        encoding="utf-8",
    )
    rows = list(parse_file(f))
    assert [r["strong"] for r in rows] == ["H1", "H1254a", "H2"]
    assert rows[0]["gloss"] == "father" and rows[0]["meaning"].startswith(
        "1) father of an individual; 2) of God"
    )
    assert rows[2]["language"] == "arc" and rows[1]["lemma_norm"] == "ברא"


def test_xref_parse_and_related(db, tmp_path):
    from corpus.books import BOOKS
    from corpus.importers import xref
    from corpus.models import Book, Verse, VerseLink, VerseText, Work, WorkKind
    from corpus.services.text import related_verses

    order = {b.osis: b.order for b in BOOKS}
    f = tmp_path / "cross_references.txt"
    f.write_text(
        "From Verse\tTo Verse\tVotes\nGen.1.1\tJohn.1.1-John.1.3\t120\nGen.1.1\tPs.33.6\t45\n"
        "Gen.1.1\tGen.1.2\t3\nGen.1.1\tFoo.1.1\t9\nGen.1.2\tGen.1.1-3\t2\n",
        encoding="utf-8",
    )
    rows = list(xref.parse_file(f))
    assert [r[3] for r in rows] == [120, 45, 3, 2]
    assert (
        rows[0][1] == order["John"] * 10**6 + 1001
        and rows[0][2] == order["John"] * 10**6 + 1003
    )
    assert rows[3][2] == order["Gen"] * 10**6 + 1003  # "Gen.1.1-3" -> ten sam rozdział
    VerseLink.objects.bulk_create(
        [
            VerseLink(from_ordinal=a, to_start=b, to_end=c, votes=v)
            for a, b, c, v in rows
        ]
    )

    books = {}
    for osis, abbr, name in [
        ("Gen", "Rdz", "Rodzaju"),
        ("John", "J", "Jana"),
        ("Ps", "Ps", "Psalmów"),
    ]:
        spec = next(b for b in BOOKS if b.osis == osis)
        books[osis] = Book.objects.create(
            osis=osis,
            order=spec.order,
            testament=spec.testament,
            abbr=abbr,
            name_pl=name,
        )
    w = Work.objects.create(
        code="BG1632", name="BG", language="pl", kind=WorkKind.TRANSLATION
    )
    for osis, ch, vs, txt in [
        ("Gen", 1, 1, "Na początku"), ("Gen", 1, 2, "A ziemia"),
        ("John", 1, 1, "Na początku było Słowo"), ("Ps", 33, 6, "Słowem Pańskim"),
    ]:  # fmt: skip
        b = books[osis]
        v = Verse.objects.create(
            book=b,
            chapter=ch,
            verse=vs,
            ordinal=b.order * 10**6 + ch * 1000 + vs,
            osis_id=f"{osis}.{ch}.{vs}",
        )
        VerseText.objects.create(verse=v, work=w, text=txt)
    rel = related_verses(
        [(order["Gen"] * 10**6 + 1001, order["Gen"] * 10**6 + 1001)], [w], limit=5
    )
    assert [r.ref for r in rel][:2] == ["J 1,1-3", "Ps 33,6"] and rel[
        0
    ].text.startswith("Na początku było")
    assert len(rel) == 3
