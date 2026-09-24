from rag.quotes import extract_candidates, verify_quotes

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
    assert (
        r.checked == 3
    )  # własne zdanie modelu nie jest liczone (za daleko od korpusu)


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


def test_quotes_next_to_tradition_tags_are_skipped():
    from rag.quotes import verify_quotes

    answer = "Barnaba mówi o „przekształceniu” wierzących [P1]. Stasiak pisze, że „kobieta jest partnerką mężczyzny” [3]."
    rep = verify_quotes(
        answer,
        [],
        [("[3] Stasiak", "Kobieta jest partnerką mężczyzny w takim samym stopniu.")],
    )
    assert rep.tradition == 1 and rep.verified == 1 and rep.altered == []
