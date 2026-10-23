"""Wydawcy czasopism na stronie Korpus: warianty nazw scalone, brak wpisu = brak wydawcy."""

from library.venues import VENUES, canonical, journal_rows


def test_journal_rows_merge_variants_and_add_publisher():
    rows = journal_rows(
        [
            {"journal": "The Biblical Annals", "n": 777},
            {"journal": "Biblical Annals", "n": 2},
            {"journal": "Wrocławski Przegląd Teologiczny.", "n": 20},
            {"journal": "Rocznik Teologiczny", "n": 1},
            {"journal": "Nieznane Czasopismo", "n": 1},
        ]
    )
    assert [(r["journal"], r["n"]) for r in rows] == [
        ("The Biblical Annals", 779),
        ("Wrocławski Przegląd Teologiczny", 20),
        ("Nieznane Czasopismo", 1),
        ("Rocznik Teologiczny", 1),
    ]
    chat = next(r for r in rows if r["journal"] == "Rocznik Teologiczny")
    assert (
        chat["publisher"].startswith("Chrześcijańska Akademia")
        and "ewangelicka" in chat["note"]
    )
    unknown = next(r for r in rows if r["journal"] == "Nieznane Czasopismo")
    assert unknown["publisher"] == "" and unknown["note"] == ""


def test_canonical_names_point_to_known_venues():
    assert canonical(
        "The Person and the Challenges The Journal of Theology Education Canon Law"
    ) == ("The Person and the Challenges")
    assert canonical("Liturgia Sacra Liturgia - Musica – Ars") in VENUES
    assert canonical("Marcin Majewski") in VENUES
