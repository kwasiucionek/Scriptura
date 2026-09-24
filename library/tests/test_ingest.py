from pathlib import Path

from library.ingest import build_chunks, chunk_section, clean_text, sigla_for
from library.search import rrf


def test_clean_text_keeps_sigla_and_strips_footnote_marks():
    assert (
        clean_text("według Marka12 w Mk 16,8 i J 3:16")
        == "według Marka w Mk 16,8 i J 3:16"
    )
    assert clean_text("prze-\nniesienie") == "przeniesienie"


def test_sigla_for():
    refs = [s["ref"] for s in sigla_for("por. Mk 14,28; 16,7 oraz Mk 1,34 i Rdz 1–3")]
    assert refs == ["Mk 14,28", "Mk 16,7", "Mk 1,34", "Rdz 1-3"]


def test_chunk_section_respects_max_and_merges_tail():
    text = "\n\n".join(
        f"Akapit {i}. " + "Zdanie testowe numer jeden. " * 8 for i in range(12)
    )
    chunks = chunk_section("T", text, max_chars=800)
    assert all(len(c) <= 1000 for c in chunks)
    assert len(chunks) >= 3


def test_build_chunks_markdown(tmp_path: Path):
    p = tmp_path / "a.md"
    p.write_text(
        "# Tytuł\n\n## 1. Sekcja\n\n"
        + "Tekst o Mk 16,8. " * 40
        + "\n\n## 2. Druga\n\n"
        + "O Rdz 1,1. " * 40,
        encoding="utf-8",
    )
    drafts = build_chunks(p)
    assert [d.section for d in drafts] == ["1. Sekcja", "2. Druga"]
    assert drafts[0].text.startswith(
        "Tytuł\n"
    )  # krótki tytuł doklejony do pierwszej sekcji
    assert [s["ref"] for s in drafts[0].sigla] == ["Mk 16,8"]


def test_rrf():
    scores = rrf([["a", "b", "c"], ["b", "a"]])
    assert sorted(scores, key=scores.get, reverse=True)[:2] == ["a", "b"] or sorted(
        scores, key=scores.get, reverse=True
    )[:2] == ["b", "a"]
    assert scores["c"] < scores["a"]


def test_is_heading_rejects_footnotes_and_accepts_headings():
    from library.ingest import is_heading

    assert is_heading("3. EFOD (HEBR. ĒPÔD)")
    assert is_heading("2.1 Teoria przekładu")
    assert is_heading("WYROCZNIA EFODU ORAZ URIM I TUMMIM")
    assert not is_heading(
        "1010 W. Brueggemann, The Kerygma of the Priestly Writers, jw., 408."
    )
    assert not is_heading(
        "76 W skrócie: plaga pierwsza dotknęła Nil i zabiła mnóstwo ryb, jednocześnie wy­"
    )
    assert not is_heading(
        "12 P.K. McCarter, I Samuel. A New Translation, New York 1980, 237."
    )
    assert not is_heading("Por. Rdz 1,1 oraz Wj 40.")


def test_strip_running_headers():
    from library.ingest import Page, strip_running_headers

    bodies = ["Efod był szatą.", "Urim i tummim.", "Kapłan losował.", "Dawid nosił efod.",
              "Samuel w Szilo.", "Arka w Jerozolimie.", "Lniany efod.", "Wyrocznia milczy."]  # fmt: skip
    pages = [
        Page(i, f"{96 + i} WYROCZNIA EFODU ORAZ URIM\n{b}\nAkapit dalszy.")
        for i, b in enumerate(bodies, 1)
    ]
    out = strip_running_headers(pages)
    assert all("WYROCZNIA" not in p.text for p in out)
    assert all(b in out[i].text for i, b in enumerate(bodies))
