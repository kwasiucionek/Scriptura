from corpus.normalize import normalize_greek, normalize_hebrew, normalize_polish


def test_greek():
    assert normalize_greek("Ἀρχὴ") == "αρχη"
    assert normalize_greek("λόγος") == "λογοσ"
    assert normalize_greek("ᾠδή") == "ωδη"


def test_hebrew():
    assert normalize_hebrew("בְּ/רֵאשִׁ֖ית") == "בראשית"
    assert normalize_hebrew("הָאָֽרֶץ׃") == "הארץ"


def test_polish():
    assert normalize_polish("Na początku stworzył Bóg niebo, i ziemię.") == (
        "na początku stworzył bóg niebo i ziemię"
    )
