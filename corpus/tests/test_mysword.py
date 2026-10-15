"""Import przekładu z modułu MySword i dostęp do dzieła przed publikacją.

Dane testowe są syntetyczne (znaczniki jak w modułach MySword, tekst wymyślony) — repozytorium
jest publiczne, a tekst przekładu chroniony.
"""

import sqlite3

import pytest
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.urls import reverse

from corpus.importers.mysword import link_siglum, parse_scripture, sanitize
from corpus.models import VerseNote, VerseText, Work
from corpus.services.text import work_access_q
from rag.access import access_for_user

VERSE = (
    '<TS><span class="green"><em>Tytuł perykopy</em><Ts> <CM>Pierwsze słowa'
    '<RF q=[1]><em>słowa</em>, דָּבָר (<span class="tl">dawar</span>), por. '
    "<a href='b1.1.27'>Rdz 1:27</a>.<Rf> drugie słowa wersetu."
    "<RF q=ⓐ><a href='b19.8.3'>Ps 8:3</a>; <a href='b10.15.13-b10.17.22'>2Sm 15:13-17:22</a><Rf>"
)


def test_parse_scripture_splits_text_markup_notes():
    text, markup, notes = parse_scripture(VERSE)
    assert text == "Pierwsze słowa drugie słowa wersetu."
    assert markup.startswith('<span class="pericope">Tytuł perykopy</span>')
    assert '<sup class="fn">1</sup>' in markup and '<sup class="xr">ⓐ</sup>' in markup
    assert [(n.kind, n.label) for n in notes] == [("note", "1"), ("xref", "ⓐ")]
    assert '<i class="tl">dawar</i>' in notes[0].html
    assert 'href="/text/ref/?q=Rdz+1%2C27"' in notes[0].html
    assert notes[1].text == "Ps 8:3; 2Sm 15:13-17:22"


def test_parse_poetry_jesus_and_added_words():
    raw = "<CM><PI0><PF1>Wers pierwszy,<PI0><PF1>wers drugi <FI>dodane<Fi> <FR>słowa Jezusa<Fr>"
    text, markup, _ = parse_scripture(raw)
    assert text == "Wers pierwszy, wers drugi dodane słowa Jezusa"
    assert markup == (
        'Wers pierwszy,<br>wers drugi <i>dodane</i> <span class="jesus">słowa Jezusa</span>'
    )


def test_sanitize_drops_unknown_markup():
    out = sanitize(
        '<script>alert(1)</script><img src=x onerror=y><a href="javascript:x">z</a>'
        "<div style='x'>tekst</div><b>ok</b>"
    )
    assert "<script" not in out and "<img" not in out and "javascript" not in out
    assert out == "alert(1)ztekst<b>ok</b>"


def test_link_siglum():
    assert link_siglum("b19.104.2-32") == "Ps 104,2-32"
    assert link_siglum("b10.15.13-b10.17.22") == "2 Sm 15,13-17,22"
    assert link_siglum("b43.1.1") == "J 1,1"
    assert link_siglum("b99.1.1") == "" and link_siglum("x") == ""


# --- import i dostęp -----------------------------------------------------------------------


def _module(path):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE Bible (Book INTEGER, Chapter INTEGER, Verse INTEGER, Scripture TEXT)"
    )
    con.execute(
        "CREATE TABLE Details (Title TEXT, Abbreviation TEXT, Version TEXT, VersionDate TEXT)"
    )
    con.execute("INSERT INTO Details VALUES ('Biblia', 'TEST', '1.1.2', '2026-03-28')")
    con.executemany(
        "INSERT INTO Bible VALUES (?, ?, ?, ?)",
        [
            (1, 1, 1, VERSE),
            (50, 2, 6, "<CM>Werset testowy,<RF q=[3]>uwaga<Rf>"),
            (50, 3, 1, "<CM>Werset pierwszy."),
            (50, 3, 3, "<CM>Werset trzeci."),
            (50, 3, None, "<CM>Werset bez numeru."),  # jedyna luka w rozdziale: 3,2
        ],
    )
    con.commit()
    con.close()


@pytest.mark.django_db
def test_import_mysword_and_private_access(tmp_path, client):
    path = tmp_path / "test.bbl.mybible"
    _module(path)
    call_command("import_mysword", "--file", str(path), "--code", "SNPD")
    work = Work.objects.get(code="SNPD")
    assert work.access == "private" and "v1.1.2" in work.notes
    assert (
        VerseText.objects.get(work=work, verse__osis_id="Phil.3.2").text
        == "Werset bez numeru."
    )
    vt = VerseText.objects.get(work=work, verse__osis_id="Gen.1.1")
    assert vt.text == "Pierwsze słowa drugie słowa wersetu."
    assert VerseNote.objects.filter(verse_text__work=work).count() == 3

    url = reverse("corpus:reference")
    params = {"q": "Rdz 1,1", "works": "SNPD"}
    assert "Pierwsze słowa" not in client.get(url, params).content.decode()

    user = User.objects.create_user("tlumacz", password="x")
    user.groups.add(Group.objects.create(name="scriptura-dzielo-SNPD"))
    assert access_for_user(user) == ["open", "work:SNPD"]
    assert (
        Work.objects.filter(work_access_q(access_for_user(user))).get().code == "SNPD"
    )
    client.force_login(user)
    page = client.get(url, params).content.decode()
    assert "Tytuł perykopy" in page and "przypisy (1)" in page and "dawar" in page

    # ponowny import zastępuje tekst i przypisy (bez duplikatów)
    call_command("import_mysword", "--file", str(path), "--code", "SNPD")
    assert VerseNote.objects.filter(verse_text__work=work).count() == 3
