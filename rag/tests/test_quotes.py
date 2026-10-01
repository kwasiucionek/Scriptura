import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rag.quotes import check_citations, extract_candidates, verify_quotes

VERSES = [
    (
        "Mk 16,9 (SBLGNT)",
        "Ἀναστὰς δὲ πρωῒ πρώτῃ σαββάτου ἐφάνη πρῶτον Μαρίᾳ τῇ Μαγδαληνῇ, παρ’ ἧς ἐκβεβλήκει ἑπτὰ δαιμόνια.",
    ),
    (
        "Mk 16,15 (BG1632)",
        "I rzekł im: Idąc na wszystek świat, każcie Ewangieliję wszystkiemu stworzeniu.",
    ),
]
CHUNKS = [
    (
        "[1] Majewski, Język fenicki",
        "Podobieństwa w zakresie fonetyki, morfologii, składni i leksyki są na tyle duże, że można mówić o dialektach jednego języka kananejskiego.",
    )
]


def test_extract_candidates():
    ans = 'Werset „Idąc na wszystek świat, każcie Ewangieliję" (Mk 16,15 BG1632). Mk 16,9 (SBLGNT): Ἀναστὰς δὲ πρωῒ πρώτῃ σαββάτου ἐφάνη πρῶτον Μαρίᾳ. Mk 16,10: ἐκείνη πορευθεῖσα ἀπήγγειλεν τοῖς.'
    c = extract_candidates(ans)
    assert c[0].startswith("Idąc na wszystek")
    assert any(x.startswith("Ἀναστὰς") for x in c) and any(
        x.startswith("ἐκείνη") for x in c
    )


def test_verified_and_altered():
    ans = (
        "Mk 16,9 (SBLGNT): Ἀναστὰς δὲ πρωῒ πρώτῃ σαββάτου ἐφάνη πρῶτον Μαρίᾳ τῇ Μαγδαληνῇ, παρ’ ἧς ἐκβεβλήκει ἑπτὰ δαιμόνια.\n"
        "Majewski pisze, że „podobieństwa w zakresie fonetyki, morfologii, składni i leksyki są na tyle duże” [1]. "
        "Tekst mówi: „Idąc na cały świat, głoście Ewangelię wszelkiemu stworzeniu” (Mk 16,15 BG1632). "
        "Sam dodam „zupełnie własne zdanie modelu, którego nie ma nigdzie w korpusie”."
    )
    r = verify_quotes(ans, VERSES, CHUNKS)
    assert r.verified == 2  # grecki werset + cytat z chunka
    assert len(r.altered) == 1 and r.altered[0].ref.startswith(
        "Mk 16,15"
    )  # parafraza BG podana jako cytat
    assert r.checked == 4
    assert len(r.unverified) == 1
    assert r.unverified[0].quote.startswith("zupełnie własne zdanie")


def test_original_language_run_is_checked():
    verses = [("Rdz 1,1 (WLC)", "בְּרֵאשִׁ֖ית בָּרָ֣א אֱלֹהִ֑ים אֵ֥ת הַשָּׁמַ֖יִם וְאֵ֥ת הָאָֽרֶץ")]
    ans = "Rdz 1,1 w brzmieniu hebrajskim (WLC) to: בְּרֵאשִׁ֖ית בָּרָ֣א אֱלֹהִ֑ים אֵ֥ת הַשָּׁמַ֖יִם וְאֵ֥ת הָאָֽרֶץ."
    r = verify_quotes(ans, verses, [])
    assert r.checked == 1 and r.verified == 1
    altered = verify_quotes(
        "Tekst: בְּרֵאשִׁית בָּרָא אֱלֹהִים אֵת הַשָּׁמַיִם וְאֵת הָאָרֶץ וְהַכֹּל", verses, []
    )
    assert (
        altered.checked == 1
    )  # bez akcentów i z dodanym słowem: zgodny lub zmieniony, ale sprawdzony


def test_tradition_quotes_are_reported_not_silently_skipped():
    from rag.quotes import verify_quotes

    answer = "Barnaba mówi o „przekształceniu” wierzących [P1]. Stasiak pisze, że „kobieta jest partnerką mężczyzny” [3]."
    rep = verify_quotes(
        answer,
        [],
        [("[3] Stasiak", "Kobieta jest partnerką mężczyzny w takim samym stopniu.")],
    )
    assert rep.tradition == 1 and rep.verified == 1 and rep.altered == []
    assert rep.checked == 2 and rep.unverified[0].reason == "invalid_citation"


@pytest.mark.parametrize(
    "marker", ["[2]", "[99]", "[P1]", "[A1]", "[1, 2]", "[1–2]", "[P]", "[A]"]
)
def test_quote_must_match_every_cited_source(marker):
    quote = "Podobieństwa w zakresie fonetyki, morfologii, składni i leksyki"
    chunks = CHUNKS + [
        (
            "[2] Inny autor",
            "To źródło opisuje budowę łodzi podczas potopu w Mezopotamii.",
        )
    ]
    report = verify_quotes(f"Autor pisze: „{quote}” {marker}.", VERSES, chunks)
    assert report.verified == 0
    assert report.checked == 1
    assert len(report.unverified) == 1
    assert report.unverified[0].citations or marker in ("[P]", "[A]")


def test_identical_quote_at_two_positions_and_following_sentence():
    quote = "Podobieństwa w zakresie fonetyki, morfologii, składni i leksyki"
    report = verify_quotes(f"„{quote}” [1]. „{quote}” [9].", [], CHUNKS)
    assert report.verified == 1 and report.checked == 2
    assert report.unverified[0].citations == ["[9]"]
    report = verify_quotes(f"„{quote}”. Osobna teza [P9].", [], CHUNKS)
    assert report.verified == 1 and report.tradition == 0


@pytest.mark.parametrize("marker", ["[1]", "[1].", ". [1]"])
def test_shared_sentence_citation_does_not_verify_quote_from_another_source(marker):
    quote_x = "Podobieństwa w zakresie fonetyki, morfologii, składni i leksyki"
    quote_y = "Build a boat, abandon wealth and seek life."
    report = verify_quotes(
        f"Autor pisze „{quote_x}” oraz „{quote_y}” {marker}",
        [],
        [("[1] Gilgamesz", quote_y), ("[2] Majewski", quote_x)],
    )
    assert report.checked == 2 and report.verified == 1
    assert len(report.unverified) == 1
    assert report.unverified[0].quote == quote_x
    assert report.unverified[0].citations == ["[1]"]
    assert report.unverified[0].reason == "source_mismatch"


@pytest.mark.parametrize("boundary", [". ", "! ", "? ", "\n"])
def test_shared_citation_never_crosses_sentence_boundary(boundary):
    quote_x = "Podobieństwa w zakresie fonetyki, morfologii, składni i leksyki"
    quote_y = "Build a boat, abandon wealth and seek life."
    report = verify_quotes(
        f"Autor pisze „{quote_x}”{boundary}Inny autor mówi „{quote_y}” [1].",
        [],
        [("[1] Gilgamesz", quote_y), ("[2] Majewski", quote_x)],
    )
    assert report.checked == report.verified == 2
    assert report.unverified == report.altered == []


def test_individual_citations_take_precedence_over_shared_sentence_citation():
    quote_x = "Podobieństwa w zakresie fonetyki, morfologii, składni i leksyki"
    quote_y = "Build a boat, abandon wealth and seek life."
    report = verify_quotes(
        f"Autor pisze „{quote_x}” [2] oraz „{quote_y}” [1].",
        [],
        [("[1] Gilgamesz", quote_y), ("[2] Majewski", quote_x)],
    )
    assert report.checked == report.verified == 2
    assert report.unverified == report.altered == []


def test_shared_citation_applies_to_all_quotes_in_a_sentence():
    quotes = [
        "Pierwszy cytat opisuje składnię języka",
        "Drugi cytat przedstawia jego fonetykę",
        "Trzeci cytat analizuje morfologię",
    ]
    report = verify_quotes(
        "Autor pisze " + " oraz ".join(f"„{q}”" for q in quotes) + " [1].",
        [],
        [("[1] Autor", "\n".join(quotes))],
    )
    assert report.checked == report.verified == 3
    assert report.unverified == report.altered == []


def test_citations_before_quotes_and_after_sentence_punctuation():
    report = verify_quotes(
        "Według [9]: „Podobieństwa w zakresie fonetyki”.", [], CHUNKS
    )
    assert report.unverified[0].reason == "invalid_citation"
    report = verify_quotes("„Podobieństwa w zakresie fonetyki”. [9]", [], CHUNKS)
    assert report.unverified[0].reason == "invalid_citation"


def test_tradition_checks_exact_text_but_does_not_certify_translation():
    source = "Build a boat, abandon wealth and seek life."
    tradition = [
        ("[A1] Gilgamesz", source),
        ("[P1] Barnaba", "Other unrelated original text."),
    ]
    exact = verify_quotes(f"„{source}” [A1].", [], [], tradition=tradition)
    assert exact.verified == exact.checked == exact.tradition == 1
    translated = verify_quotes(
        "„Zbuduj łódź, porzuć bogactwa i szukaj życia” [A1].",
        [],
        [],
        tradition=tradition,
    )
    assert (
        translated.verified == 0
        and translated.unverified[0].reason == "translation_or_mismatch"
    )
    wrong = verify_quotes(f"„{source}” [P1].", [], [], tradition=tradition)
    assert wrong.verified == 0 and wrong.unverified


def test_citation_lists_ranges_and_missing_markers():
    available = {"[1]", "[2]", "[P1]", "[P2]", "[A1]"}
    checks = check_citations(
        "Teza [1, 2–3] [P1–P3] [A1, A9] [P] [A] [0] [P1, 2].", available
    )
    assert checks[0].refs == ["[1]", "[2]", "[3]"]
    assert checks[0].missing == ["[3]"]
    assert checks[1].missing == ["[P3]"]
    assert checks[2].missing == ["[A9]"]
    assert all(c.status == "unverified" for c in checks[:-1])
    assert checks[-1].refs == ["[P1]", "[P2]"] and checks[-1].status == "ok"


@pytest.mark.parametrize(
    "marker",
    ["[3–1]", "[1–999999999]", "[P1–A2]", "[1, nope]", "[999999999999999999999]"],
)
def test_invalid_citation_ranges_are_bounded_and_reported(marker):
    checks = check_citations(marker, set())
    assert len(checks) == 1 and checks[0].status == "unverified"
    assert checks[0].reason


@pytest.fixture
def verse_access_data(db, settings):
    from corpus.importers.base import TokenRecord, VerseRecord, import_records
    from corpus.models import Lexeme, Verse, VerseLink, Work

    works = []
    for level in ("open", "licensed", "private", "personal"):
        w = Work.objects.create(
            code=f"TEST_{level}",
            name=level,
            language="hbo",
            kind="original",
            access=level,
        )
        records = [
            VerseRecord(
                "Gen",
                1,
                n,
                f"{level} tekst źródłowy {n}",
                [TokenRecord("חסד", lemma="חסד", strong="H2617")],
            )
            for n in (1, 2)
        ]
        import_records(w, iter(records))
        works.append(w)
    settings.RAG_VERSE_WORKS = [w.code for w in works]
    settings.RAG_RELATED_VERSES = 4
    settings.RAG_ACCESS = ["open"]
    verses = list(Verse.objects.order_by("ordinal"))
    VerseLink.objects.create(
        from_ordinal=verses[0].ordinal,
        to_start=verses[1].ordinal,
        to_end=verses[1].ordinal,
        votes=10,
    )
    lx = Lexeme.objects.create(
        strong="H2617",
        language="hbo",
        lemma="חסד",
        lemma_norm="חסד",
        transliteration="hesed",
        gloss="kindness",
    )
    return works, lx


@pytest.mark.parametrize(
    "levels",
    [[], ["open"], ["open", "licensed"], ["open", "licensed", "private", "personal"]],
)
def test_all_rag_verse_paths_use_work_access(verse_access_data, monkeypatch, levels):
    from rag import service

    works, lx = verse_access_data
    allowed = set(levels) - {"personal"}
    direct = service.verses_for_question("Rdz 1,1", access=levels)
    assert {v.work for v in direct} == {w.code for w in works if w.access in allowed}
    total, lexical, _ = service.verses_for_lexeme(lx, works, access=levels)
    assert total == 2 * len(allowed)
    assert {v.work for v in lexical} == {w.code for w in works if w.access in allowed}
    monkeypatch.setattr(service, "lexemes_from_transliteration", lambda *a, **kw: [lx])
    examples = service.lexeme_verses_for_question("hesed", access=levels)
    assert {v.work for v in examples} == {w.code for w in works if w.access in allowed}
    related = service.related_for_question("Rdz 1,1", access=levels)
    assert all(
        r["work"] in {w.code for w in works if w.access in allowed} for r in related
    )
    if not allowed:
        assert direct == examples == related == lexical == []
    lines = service.lexicon_lines("hesed", [], access=levels)
    assert not total or f"{total} wystąpień" in lines[0]
    if not allowed:
        assert "wystąpień" not in lines[0]


@pytest.mark.parametrize("revoked_access", ["private", "personal"])
@pytest.mark.parametrize("explicit_works", [False, True])
def test_lexeme_verse_sql_rechecks_access_after_work_selection(
    verse_access_data,
    monkeypatch,
    settings,
    revoked_access,
    explicit_works,
):
    from corpus.models import Work
    from rag import service

    works, lx = verse_access_data
    work = next(w for w in works if w.access == "open")
    settings.RAG_VERSE_WORKS = [work.code]
    accessor = "visible_works" if explicit_works else "active_works"
    original = getattr(service.corpus_svc, accessor)

    def revoke_after_selection(*args, **kwargs):
        visible = original(*args, **kwargs)
        assert [w.pk for w in visible] == [work.pk]
        Work.objects.filter(pk=work.pk).update(access=revoked_access)
        assert visible[0].access == "open"  # the authorized Python object is stale
        return visible

    monkeypatch.setattr(service.corpus_svc, accessor, revoke_after_selection)
    _, verses, _ = service.verses_for_lexeme(
        lx,
        [work] if explicit_works else None,
        access=["open", "personal"],
    )
    assert Work.objects.get(pk=work.pk).access == revoked_access
    assert verses == []


def test_denied_or_empty_work_selection_never_falls_back(verse_access_data, settings):
    from rag import service

    settings.RAG_ACCESS = ["open", "licensed", "private"]
    for codes in (["TEST_private"], ["missing"], []):
        assert service.verses_for_question("Rdz 1,1", codes, access=["open"]) == []
        assert service.related_for_question("Rdz 1,1", codes, access=["open"]) == []
        assert service.lexeme_verses_for_question("hesed", codes, access=["open"]) == []
    assert {v.work for v in service.verses_for_question("Rdz 1,1")} == {
        "TEST_open",
        "TEST_licensed",
        "TEST_private",
    }


@pytest.fixture
def isolated_ask(monkeypatch, settings):
    from rag import service

    hit = SimpleNamespace(
        document_id=1,
        title="Źródło",
        authors=[],
        citation="Autor",
        section="",
        doc_type="article",
        year=None,
        url="",
        sigla=[],
        text="Oryginalny tekst źródła o fonetyce języków starożytnych.",
        access="open",
        register="scientific",
        score=1,
    )
    monkeypatch.setattr(service, "retrieve", Mock(return_value=[hit]))
    monkeypatch.setattr(service, "expand_with_neighbors", lambda hits, *a: hits)
    monkeypatch.setattr(service, "diversify", lambda hits, *a: hits)
    monkeypatch.setattr(service, "verses_for_question", Mock(return_value=[]))
    monkeypatch.setattr(service, "lexeme_verses_for_question", Mock(return_value=[]))
    monkeypatch.setattr(service, "related_for_question", Mock(return_value=[]))
    monkeypatch.setattr(service, "lexicon_lines", Mock(return_value=[]))
    monkeypatch.setattr(service, "ane_for_question", Mock(return_value=[]))
    monkeypatch.setattr(service, "patristics_for_question", Mock(return_value=[]))
    monkeypatch.setattr(service, "verify_refs", lambda answer: ([], []))
    settings.RAG_NUM_CTX = 0
    return service


@pytest.mark.parametrize("marker", ["[1, nope]", "[1; P]", "[1, 3–2]"])
def test_ask_does_not_confirm_partial_invalid_marker(isolated_ask, monkeypatch, marker):
    from library.llm import Delta

    monkeypatch.setattr(
        isolated_ask,
        "llm_stream",
        lambda messages: iter([Delta(content=f"Teza {marker}."), Delta(done=True)]),
    )
    result = list(isolated_ask.ask("pytanie", access=["open"]))[-1][1]
    assert result["citations"] == []
    assert result["verified_citations"] == []
    assert marker in result["unverified_citations"]


def test_ask_reports_missing_citations_and_fabricated_quotes(isolated_ask, monkeypatch):
    from library.llm import Delta

    service = isolated_ask
    answer = "„Model całkowicie zmyślił ten nieistniejący fragment” [2]. Tezy [1–3] [P1, P2] [A1] [P] [A]."
    monkeypatch.setattr(
        service,
        "llm_stream",
        lambda messages: iter([Delta(content=answer), Delta(done=True)]),
    )
    events = list(
        service.ask("pytanie", access=["open"], user_id=7, personal_only=True)
    )
    result = events[-1][1]
    assert events[-1][0] == "done" and result["citations"] == [1]
    assert result["verified_citations"] == ["[1]"]
    assert set(result["unverified_citations"]) == {
        "[2]",
        "[3]",
        "[P1]",
        "[P2]",
        "[A1]",
        "[P]",
        "[A]",
    }
    assert result["quotes_checked"] == 1 and len(result["quotes_unverified"]) == 1
    assert result["quotes_verified"] == 0
    for fn in (
        service.verses_for_question,
        service.lexeme_verses_for_question,
        service.related_for_question,
        service.lexicon_lines,
    ):
        assert fn.call_args.kwargs["access"] == ["open"]
    assert service.retrieve.call_args.kwargs["personal_only"] is True
    assert service.ane_for_question.called and service.patristics_for_question.called


@pytest.mark.parametrize(
    "ending", [b'data: {"error": {"message": "GPU failed"}}\n\n', b""]
)
def test_openai_failure_becomes_rag_error_without_done(
    isolated_ask, monkeypatch, settings, ending
):
    from library import llm

    service = isolated_ask
    settings.LLM_BACKEND = "openai"
    settings.OPENAI_EXTRA_BODY = {}
    payload = b'data: {"choices": [{"delta": {"content": "Partial"}}]}\n\n' + ending
    monkeypatch.setattr(llm, "_openai_request", lambda *args: io.BytesIO(payload))
    events = list(service.ask("pytanie", access=["open"]))
    assert [e for e, _ in events] == ["sources", "delta", "error"]
    assert "OpenAI-compatible" in events[-1][1]["detail"]


def test_cited_quote_cannot_be_verified_by_a_verse_or_unrelated_chunk():
    report = verify_quotes(
        "„Idąc na wszystek świat, każcie Ewangieliję” [1].", VERSES, CHUNKS
    )
    assert report.verified == 0 and report.unverified


def test_unquoted_siglum_quote_with_citation_is_bound_to_source():
    report = verify_quotes(
        "Mk 16,15: Idąc na wszystek świat, każcie Ewangieliję [9].", VERSES, CHUNKS
    )
    assert report.verified == 0 and report.unverified[0].citations == ["[9]"]


def test_personal_only_keeps_biblical_and_comparative_context(
    isolated_ask, monkeypatch
):
    from ane.search import AneHit
    from library.llm import Delta
    from patristics.search import PatHit

    service = isolated_ask
    service.verses_for_question.return_value = [
        service.VerseSource("Rdz 1,1", "WLC", "Dostępny tekst biblijny")
    ]
    service.related_for_question.return_value = [
        {
            "ref": "Rdz 1,2",
            "work": "WLC",
            "votes": 1,
            "text": "Dostępny powiązany werset",
        }
    ]
    service.ane_for_question.return_value = [
        AneHit(
            1, "Gilgamesz", "Gilgamesz XI", "Build a boat and seek life.", "", "", "", 1
        )
    ]
    service.patristics_for_question.return_value = [
        PatHit(
            1,
            "Ireneusz",
            "Dzieło",
            "Ireneusz I",
            "The resurrection of the dead.",
            "",
            "",
            1,
        )
    ]
    model = Mock(
        return_value=iter(
            [
                Delta(
                    content="„Build a boat and seek life.” [A1]. „The resurrection of the dead.” [P1]."
                ),
                Delta(done=True),
            ]
        )
    )
    monkeypatch.setattr(service, "llm_stream", model)
    events = list(
        service.ask(
            "pytanie",
            access=["open"],
            user_id=7,
            personal_only=True,
            include_ane=True,
            include_patristics=True,
        )
    )
    sources, result = events[0][1], events[-1][1]
    assert (
        sources["verses"]
        and sources["related"]
        and sources["ane"]
        and sources["patristics"]
    )
    prompt = model.call_args.args[0][-1]["content"]
    assert (
        "Dostępny tekst biblijny" in prompt
        and "[A1] Gilgamesz" in prompt
        and "[P1] Ireneusz" in prompt
    )
    assert result["quotes_verified"] == 2 and result["quotes_checked"] == 2
    assert result["quotes_tradition"] == 2 and result["quotes_unverified"] == []
    assert result["verified_citations"] == ["[A1]", "[P1]"]


def test_citations_are_verified_against_context_after_budget_trimming(
    isolated_ask, monkeypatch, settings
):
    from ane.search import AneHit
    from library.llm import Delta
    from patristics.search import PatHit

    service = isolated_ask
    service.ane_for_question.return_value = [
        AneHit(1, "Gilgamesz", "Gilgamesz XI", "A" * 10000, "", "", "", 1)
    ]
    service.patristics_for_question.return_value = [
        PatHit(1, "Ireneusz", "Dzieło", "Ireneusz I", "P" * 10000, "", "", 1)
    ]
    settings.RAG_NUM_CTX = 3072
    settings.RAG_ANSWER_RESERVE_TOKENS = 2048
    monkeypatch.setattr(
        service,
        "llm_stream",
        lambda messages: iter(
            [
                Delta(
                    content="„Niezgodny cytat wymyślony przez model” [A1]. Inna teza [P1]."
                ),
                Delta(done=True),
            ]
        ),
    )
    events = list(service.ask("pytanie", access=["open"]))
    assert events[0][1]["ane"] == events[0][1]["patristics"] == []
    result = events[-1][1]
    assert (
        result["context_budget"]["dropped"]["ane"]
        == result["context_budget"]["dropped"]["patristics"]
        == 1
    )
    assert result["unverified_citations"] == ["[A1]", "[P1]"]
    assert result["quotes_unverified"][0]["reason"] == "invalid_citation"
