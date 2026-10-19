"""Harvest publikacji ze strony WordPress autora: dopasowanie plików do publikacji, odrzucenie
plików administracyjnych, kopie i części tego samego tytułu (dane syntetyczne, bez sieci)."""

import pytest

from library.harvest import wp_media

SITE = "https://autor.example"
MEDIA = [
    {"id": 1, "title": {"rendered": "Teksty_z_Ugarit_TOM_1_Mity"}, "source_url": f"{SITE}/u/Teksty_z_Ugarit_TOM_1_Mity.pdf", "date": ""},
    {"id": 2, "title": {"rendered": "Zwierzeta_czyste_i_nieczyste_w_Starym_Te"}, "source_url": f"{SITE}/u/Zwierzeta_czyste_i_nieczyste_w_Starym_Te.pdf", "date": ""},
    {"id": 3, "title": {"rendered": "Zwierzeta_czyste_i_nieczyste_w_Starym_Te (1)"}, "source_url": f"{SITE}/u/Zwierzeta_czyste_i_nieczyste_w_Starym_Te-1.pdf", "date": ""},
    {"id": 4, "title": {"rendered": "UMOWA_ZS 18-26.10.2026"}, "source_url": f"{SITE}/u/UMOWA_ZS-18-26.10.2026.pdf", "date": ""},
    {"id": 5, "title": {"rendered": "ubezpieczenie &#8211; WR"}, "source_url": f"{SITE}/u/ubezpieczenie-WR.pdf", "date": ""},
    {"id": 6, "title": {"rendered": "Świat i kosmos wg Biblii &#8211; Marcin Majewski"}, "source_url": f"{SITE}/u/Swiat-i-kosmos-wg-Biblii-Marcin-Majewski.pdf", "date": ""},
    {"id": 7, "title": {"rendered": "Świat_i_kosmos_wg_Biblii_-_Marcin_Majewski"}, "source_url": f"{SITE}/u/Swiat_i_kosmos_wg_Biblii_-_Marcin_Majewski.pdf", "date": ""},
    {"id": 8, "title": {"rendered": "Biblia_o_rodzinie"}, "source_url": f"{SITE}/u/Biblia_o_rodzinie.pdf", "date": ""},
]  # fmt: skip
POSTS = [
    {"id": 10, "title": {"rendered": "Teksty z Ugarit. TOM 1 &#8211; Mity"}, "link": f"{SITE}/publikacja/ugarit-1/", "content": {"rendered": "<p>Tom I serii (Kraków 2016).</p>"}, "class_list": ["publikacja", "kategoria-publikacji-ksiazki"]},
    {"id": 11, "title": {"rendered": "Zwierzęta czyste i nieczyste w Starym Testamencie. Część I: Omówienie"}, "link": f"{SITE}/publikacja/z1/", "content": {"rendered": ""}, "class_list": ["kategoria-publikacji-artykuly"]},
    {"id": 12, "title": {"rendered": "Zwierzęta czyste i nieczyste w Starym Testamencie. Część II. Interpretacje"}, "link": f"{SITE}/publikacja/z2/", "content": {"rendered": ""}, "class_list": ["kategoria-publikacji-artykuly"]},
]  # fmt: skip


@pytest.fixture
def fake_site(monkeypatch):
    def fake_get(url, timeout=30):
        return (MEDIA if "/media" in url else POSTS), {"X-WP-TotalPages": "1"}

    monkeypatch.setattr(wp_media, "_get", fake_get)


def test_entries_match_parts_skip_admin_copies_and_known(fake_site):
    out, skipped = wp_media.entries(
        SITE, "Marcin Majewski", access="open", license="za zgodą autora",
        known_titles={"Biblia o rodzinie"},
    )  # fmt: skip
    titles = [e.title for e in out]
    assert titles == [
        "Teksty z Ugarit. TOM 1 – Mity",
        "Zwierzęta czyste i nieczyste w Starym Testamencie. Część I: Omówienie",
        "Zwierzęta czyste i nieczyste w Starym Testamencie. Część II. Interpretacje",
        "Świat i kosmos wg Biblii – Marcin Majewski",
    ]
    ugarit = out[0]
    assert ugarit.doc_type == "book" and ugarit.year == 2016 and ugarit.access == "open"
    assert ugarit.url.endswith("/publikacja/ugarit-1/") and ugarit.pdf_url.endswith(
        ".pdf"
    )
    assert out[3].register == "popular" and "webinaru" in out[3].note
    assert sum("administracyjny" in s for s in skipped) == 2  # umowa, ubezpieczenie
    assert any("kopia pliku" in s for s in skipped)  # drugi plik „Świat i kosmos”
    assert any(
        "już w korpusie: Biblia_o_rodzinie" in s
        or "już w korpusie: Biblia o rodzinie" in s
        for s in skipped
    )


def test_harvest_wpmedia_requires_consent_for_open(fake_site, tmp_path, db):
    from django.core.management import CommandError, call_command

    from library.harvest import manifest

    with pytest.raises(CommandError):
        call_command(
            "harvest", "wpmedia", "--site", SITE, "--author", "M", "--access", "open"
        )
    out = tmp_path / "wp.jsonl"
    call_command(
        "harvest", "wpmedia", "--site", SITE, "--author", "M", "--access", "open",
        "--license", "za zgodą autora (19.10.2026)", "--out", str(out),
    )  # fmt: skip
    rows = manifest.read(out)
    assert len(rows) == 5 and {r.access for r in rows} == {"open"}
