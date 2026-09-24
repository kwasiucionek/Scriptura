import json

from library.harvest import youtube

DESCRIPTION = (
    "Odcinek nt. kontrowersyjnego fragmentu z Księgi Powtórzonego Prawa 32,8-9. Tematyka: Pwt 32,8; 5Moj 32,8; politeizm.\n"
    "W ODCINKU: [00:00](https://www.youtube.com/watch?v=C0IBaOqKaXw) - Wstęp\n"
    "[02:21](https://www.youtube.com/watch?v=C0IBaOqKaXw&t=141s) - LXX i Qumran\n"
    "🌐 Moja strona internetowa: https://majewskimarcin.pl\n"
    "ŹRÓDŁA:\nBiblia internetowa: [www.biblia-internetowa.pl](https://www.biblia-internetowa.pl)\n"
    "M.S. Heiser, Deuteronomy 32:8 and the Sons of God, Bibliotheca Sacra 158 (2001) 52-74.\n"
    "J. Joosten, A Note on the Text of Deuteronomy XXXII 8, VT 57 (2007) 548-555."
)


def _video(caps):
    return youtube.VideoInfo(
        video_id="C0IBaOqKaXw", title="Czy Jahwe ma ojca?", channel="Kanał", upload_date="20250912",
        description=DESCRIPTION, chapters=[(0.0, "Wstęp"), (141.0, "LXX i Qumran")],
        captions=caps, caption_kind="napisy automatyczne", tags=["Pwt 32,8", "politeizm", "henoteizm"],
    )  # fmt: skip


def test_parse_and_build_text(tmp_path):
    raw = json.dumps({
        "events": [
            {"tStartMs": 0, "segs": [{"utf8": "dzień dobry"}]},
            {"tStartMs": 30000, "segs": [{"utf8": "mówimy o Powtórzonego Prawa 32 8"}]},
            {"tStartMs": 65000, "segs": [{"utf8": "Septuaginta czyta synów Bożych"}]},
            {"tStartMs": 141000, "segs": [{"utf8": "w Qumran 4QDeut"}, {"utf8": " ma bene elohim"}]},
            {"tStartMs": 200000, "segs": [{"utf8": "\n"}]},
        ]
    })  # fmt: skip
    caps = youtube.parse_json3(raw)
    assert [c.start for c in caps] == [0.0, 30.0, 65.0, 141.0]
    v = _video(caps)
    text = youtube.build_text(v)
    assert text.startswith(
        "# Czy Jahwe ma ojca?\n\n## Opis odcinka\n\nOdcinek nt. kontrowersyjnego fragmentu z Księgi Powtórzonego Prawa 32,8-9 (Pwt 32,8-9)."
    )
    assert (
        "W ODCINKU" not in text and "[00:00](" not in text and "Moja strona" not in text
    )
    assert (
        "## Źródła podane w odcinku\n\n- Biblia internetowa: www.biblia-internetowa.pl (https://www.biblia-internetowa.pl)\n- M.S. Heiser, Deuteronomy 32:8"
        in text
    )
    assert "Słowa kluczowe: Pwt 32,8, politeizm, henoteizm" in text
    assert (
        "## [00:00] Wstęp\n\n[00:00] dzień dobry mówimy o Powtórzonego Prawa 32 8 (Pwt 32,8)"
        in text
    )
    assert "[01:05] Septuaginta czyta synów Bożych" in text  # nowy akapit po 60 s
    assert "## [02:21] LXX i Qumran\n\n[02:21] w Qumran 4QDeut ma bene elohim" in text
    e = youtube.to_entry(v, "Marcin Majewski", tmp_path)
    assert (
        e.doc_type == "video"
        and e.access == "licensed"
        and e.year == 2025
        and e.url.endswith("C0IBaOqKaXw")
    )
    assert (
        e.local_file.endswith("youtube/C0IBaOqKaXw.md")
        and "napisy automatyczne" in e.note
    )
    assert (
        youtube.video_id("https://www.youtube.com/watch?v=C0IBaOqKaXw&t=141s")
        == "C0IBaOqKaXw"
    )
    assert youtube.video_id("https://youtu.be/xLqiH9_p9P0") == "xLqiH9_p9P0"
    assert youtube.mmss(3725) == "1:02:05"


def test_source_url_deep_link():
    from library.search import ChunkHit
    from rag.service import source_url

    def hit(doc_type, section, text, url="https://www.youtube.com/watch?v=C0IBaOqKaXw"):
        return ChunkHit(
            chunk_id=1,
            document_id=1,
            order=0,
            title="t",
            authors=[],
            citation="",
            doc_type=doc_type,
            year=None,
            url=url,
            section=section,
            text=text,
            sigla=[],
            score=1.0,
            access="open",
        )

    assert (
        source_url(hit("video", "[02:21] LXX i Qumran", "…"))
        == "https://www.youtube.com/watch?v=C0IBaOqKaXw&t=141s"
    )
    assert (
        source_url(
            hit("video", "[02:21] LXX i Qumran", "[02:21] LXX i Qumran [03:40] tekst")
        )
        == "https://www.youtube.com/watch?v=C0IBaOqKaXw&t=220s"
    )
    assert (
        source_url(hit("video", "", "[1:02:05] tekst"))
        == "https://www.youtube.com/watch?v=C0IBaOqKaXw&t=3725s"
    )
    assert (
        source_url(hit("article", "[02:21] x", ""))
        == "https://www.youtube.com/watch?v=C0IBaOqKaXw"
    )
