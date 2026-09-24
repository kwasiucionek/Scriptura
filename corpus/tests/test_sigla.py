import pytest

from corpus.sigla import extract, format_ref, ordinal_range, parse


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mk 1,1-8", ["Mk 1,1-8"]),
        ("Mk 1,1-2,5", ["Mk 1,1-2,5"]),
        ("Mk 1,1.5.9-12", ["Mk 1,1", "Mk 1,5", "Mk 1,9-12"]),
        ("J 3,16n", ["J 3,16-17"]),
        ("J 3,16nn", ["J 3,16nn"]),
        ("Mk 1,1a", ["Mk 1,1"]),
        ("Rdz 1–3", ["Rdz 1-3"]),
        ("Ps 23", ["Ps 23"]),
        ("Mt 5,3-12; 7,21", ["Mt 5,3-12", "Mt 7,21"]),
        ("Mk 1:1-8", ["Mk 1,1-8"]),
        ("1 Kor 13", ["1 Kor 13"]),
        ("1Kor 13,4-7", ["1 Kor 13,4-7"]),
        ("I Kor 13", ["1 Kor 13"]),
        ("1 J 4,8", ["1 J 4,8"]),
        ("1 Mojż 1,1", ["Rdz 1,1"]),
        ("Obj 21,4", ["Ap 21,4"]),
        ("Kazn 3,1", ["Koh 3,1"]),
        ("Łk 1,1–4", ["Łk 1,1-4"]),
    ],
)
def test_parse(text, expected):
    assert [format_ref(r) for r in parse(text)] == expected


def test_ordinal_ranges():
    (ref,) = parse("Mk 1,1-8")
    assert ordinal_range(ref) == (48_001_001, 48_001_008)
    (ref,) = parse("Ps 23")
    assert ordinal_range(ref) == (23_023_000, 23_023_999)
    (ref,) = parse("J 3,16nn")
    assert ordinal_range(ref) == (50_003_016, 50_003_999)
    (ref,) = parse("Rdz 1-3")
    assert ordinal_range(ref) == (1_001_000, 1_003_999)


def test_extract_from_free_text():
    txt = (
        "Por. Łk 1, 1 Kor 13,13 i Ap 21,1-4. Zob. Mt 5,3-12; 7,21 oraz Mk 1,1.5 "
        "(por. Rz 8,28n). W 2010 r. wydano 3 tomy. Jan 3,16."
    )
    found = [(m.text, [format_ref(r) for r in m.refs]) for m in extract(txt)]
    assert found == [
        ("Łk 1", ["Łk 1"]),
        ("1 Kor 13,13", ["1 Kor 13,13"]),
        ("Ap 21,1-4", ["Ap 21,1-4"]),
        ("Mt 5,3-12; 7,21", ["Mt 5,3-12", "Mt 7,21"]),
        ("Mk 1,1.5", ["Mk 1,1", "Mk 1,5"]),
        ("Rz 8,28n", ["Rz 8,28-29"]),
        ("Jan 3,16", ["J 3,16"]),
    ]


def test_extract_ignores_plain_words():
    assert extract("Ja mam 3 koty i 2 psy. Da 5 zł.") == []


def test_reversed_ranges_are_rejected():
    assert parse("1 Sm 8–2") == []
    assert extract("Gli oracoli divini in 1 Sam 8–2 Re 25, Roma 2002") == []
    assert parse("Mk 1,8-3") == []
    assert [format_ref(r) for r in parse("Mk 1,1-8; 5,3")] == ["Mk 1,1-8", "Mk 5,3"]
    assert parse("Mk 3,1-2,5") == []
    assert parse("Wj 35-401012") == []
    assert parse("Wj 401012") == []
    assert parse("Ps 119,1776") == []
    assert [format_ref(r) for r in parse("Ps 119,176")] == ["Ps 119,176"]
    assert [format_ref(r) for r in parse("Ps 150")] == ["Ps 150"]
