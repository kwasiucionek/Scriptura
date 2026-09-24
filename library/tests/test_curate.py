from library import curate as cur
from library.harvest.manifest import ManifestEntry


def _e(title, access="open", doi="", year=2020, lang="pl"):
    return ManifestEntry(
        "openalex",
        f"W{abs(hash(title)) % 10**6}",
        title,
        doi=doi,
        year=year,
        access=access,
        language=lang,
    )


def test_rule_decisions_noise_and_duplicates(settings):
    settings.LLM_BACKEND = "echo"
    entries = [
        _e("Sprawiedliwość Boża a usprawiedliwienie z wiary w Liście do Rzymian"),
        _e("Kościół w czasie pandemii"),
        _e("Report from the Symposium Organized by Joseph Ratzinger Foundation"),
        _e("Franz Posset, Johann Reuchlin (1455–1522). A Theological Biography"),
        _e(
            "Franz Posset, Johann Reuchlin (1455-1522). A Theological Biography (Arbeiten)"
        ),
        _e("Inny tytuł", doi="10.1/x"),
        _e("Zupełnie inny tytuł", doi="10.1/x"),
    ]
    rep = cur.curate(entries, "Test", use_llm=False)
    acc = [d.access for d in rep.decisions]
    assert acc == ["open", "skip", "skip", "open", "skip", "open", "skip"]
    assert rep.decisions[4].duplicate_of == 3 and rep.decisions[6].duplicate_of == 5
    cur.apply(entries, rep)
    assert entries[1].access == "skip" and entries[0].access == "open"
    assert entries[4].note.startswith("kurator: dublet")


def test_llm_decisions_thresholds(settings, monkeypatch):
    settings.LLM_BACKEND = "ollama"
    settings.CURATE_REVIEW_CONFIDENCE = 0.5
    entries = [
        _e("Efekty leczenia nadciśnienia u seniorów"),
        _e("Symbolika krwi w Biblii"),
        _e("Coś niejasnego"),
    ]
    fake = [
        {
            "i": 1,
            "relevant": False,
            "kind": "article",
            "register": "scientific",
            "language": "pl",
            "confidence": 0.95,
            "reason": "medycyna",
        },
        {
            "i": 2,
            "relevant": True,
            "kind": "article",
            "register": "scientific",
            "language": "pl",
            "confidence": 0.9,
            "reason": "biblistyka",
            "journal_ok": False,
            "journal_fix": "Wrocławski Przegląd Teologiczny",
        },
        {
            "i": 3,
            "relevant": True,
            "kind": "article",
            "register": "popular",
            "language": "pl",
            "confidence": 0.4,
            "reason": "niepewne",
        },
    ]
    monkeypatch.setattr(cur, "chat_json", lambda prompt, num_predict=4000: fake)
    rep = cur.curate(entries, "Test")
    assert [d.access for d in rep.decisions] == ["skip", "open", "review"]
    cur.apply(entries, rep)
    assert entries[2].access == "review" and entries[2].register == "popular"
    assert entries[1].access == "open"  # licencja/dostęp nietknięte przez kuratora
    assert (
        entries[1].journal == "Wrocławski Przegląd Teologiczny"
        and "poprawione" in entries[1].note
    )


def test_extract_json_variants():
    from library.curate import _extract_json

    assert _extract_json('```json\n[{"i": 1}]\n```') == [{"i": 1}]
    assert _extract_json('Oto wynik: [{"i": 2, "relevant": true}] koniec') == [
        {"i": 2, "relevant": True}
    ]
    assert _extract_json('{"items": [{"i": 3}]}') == [
        {"i": 3}
    ]  # lista w obiekcie -> lista
    import pytest

    with pytest.raises(ValueError):
        _extract_json("")


def test_batch_failure_falls_back_to_per_item(settings, monkeypatch):
    settings.LLM_BACKEND = "ollama"
    entries = [_e("Efekty leczenia nadciśnienia"), _e("Symbolika krwi w Biblii")]
    calls = {"n": 0}

    def fake_chat(prompt, num_predict=4000, attempts=2):
        calls["n"] += 1
        if "1. [" in prompt and "2. [" in prompt:  # cała partia -> błąd
            raise RuntimeError("timeout")
        rel = "Symbolika" in prompt
        return [
            {
                "i": 1,
                "relevant": rel,
                "kind": "article",
                "register": "scientific",
                "language": "pl",
                "confidence": 0.9,
                "reason": "x",
            }
        ]

    monkeypatch.setattr(cur, "chat_json", fake_chat)
    rep = cur.curate(entries, "Test")
    assert [d.access for d in rep.decisions] == ["skip", "open"]
    assert calls["n"] == 3  # 1 partia + 2 pojedyncze


def test_same_title_different_authors_is_not_duplicate(settings):
    settings.LLM_BACKEND = "echo"
    a = ManifestEntry(
        "openalex",
        "W1",
        "Tomasz Niemas, Perspektywa eschatologiczna proegzystencji",
        authors=["Marcin Majewski"],
    )
    b = ManifestEntry(
        "openalex",
        "W2",
        "Tomasz Niemas, Perspektywa eschatologiczna proegzystencji",
        authors=["Mariusz Rosik"],
    )
    c = ManifestEntry(
        "openalex",
        "W3",
        "Tomasz Niemas, Perspektywa eschatologiczna proegzystencji",
        authors=["Mariusz Rosik"],
    )
    rep = cur.curate([a, b, c], "Test", use_llm=False)
    assert [d.access for d in rep.decisions] == [
        "licensed",
        "licensed",
        "skip",
    ]  # c = dublet b (ten sam autor)


def test_ingest_title_dedup_respects_authors(db):
    from library.management.commands.ingest_manifest import _title_exists
    from library.models import Author, Document

    d = Document.objects.create(
        title="Tomasz Niemas, Perspektywa eschatologiczna proegzystencji wierzących"
    )
    d.authors.add(Author.objects.create(name="Marcin Majewski", slug="majewski"))
    assert _title_exists(
        "Tomasz Niemas, Perspektywa eschatologiczna proegzystencji", ["Marcin Majewski"]
    )
    assert not _title_exists(
        "Tomasz Niemas, Perspektywa eschatologiczna proegzystencji", ["Mariusz Rosik"]
    )
    assert _title_exists(
        "Tomasz Niemas, Perspektywa eschatologiczna proegzystencji"
    )  # bez autorów: ostrożnie
