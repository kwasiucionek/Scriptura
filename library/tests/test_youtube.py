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


class _FakeYDL:
    """yt_dlp.YoutubeDL bez sieci: film -> kanał, zakładki kanału -> lista filmów."""

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False, process=True):
        if "watch?v=" in url:
            return {"channel_url": "https://www.youtube.com/channel/UC123"}
        if url.endswith("/videos"):
            return {"entries": [
                {"id": "AAAAAAAAAAA", "title": "Wykład o Pięcioksięgu", "duration": 3600},
                {"id": "BBBBBBBBBBB", "title": "Zapowiedź", "duration": 90},
                {"id": "C0IBaOqKaXw", "title": "Czy Jahwe ma ojca?", "duration": 2400},
            ]}  # fmt: skip
        if url.endswith("/streams"):
            return {
                "entries": [{"id": "AAAAAAAAAAA", "title": "dubel", "duration": 3600}]
            }
        raise AssertionError(url)


def test_channel_videos_from_video_link(monkeypatch):
    import sys
    import types

    monkeypatch.setitem(
        sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_FakeYDL)
    )
    out = youtube.channel_videos("https://youtu.be/C0IBaOqKaXw", min_minutes=10)
    assert [v.video_id for v in out] == [
        "AAAAAAAAAAA",
        "C0IBaOqKaXw",
    ]  # bez zapowiedzi i dubli
    assert (
        youtube.channel_videos("UC-kanał/videos", limit=1)[0].video_id == "AAAAAAAAAAA"
    )


def test_harvest_channel_open_with_consent(monkeypatch, tmp_path, settings):
    import pytest
    from django.core.management import CommandError, call_command

    from library.harvest import manifest

    settings.DATA_DIR = tmp_path
    known = tmp_path / "known.jsonl"
    manifest.write(
        known, [youtube.to_entry(_video([youtube.Caption(0, "x")]), "M", tmp_path)]
    )
    monkeypatch.setattr(
        youtube,
        "channel_videos",
        lambda *a, **k: [
            youtube.ChannelVideo("AAAAAAAAAAA", "W", 3600),
            youtube.ChannelVideo("C0IBaOqKaXw", "J", 2400),
        ],
    )

    def fake_fetch(vid):
        v = _video([youtube.Caption(0, "dzień dobry")])
        v.video_id, v.title = vid, f"Wykład {vid}"
        return v

    monkeypatch.setattr(youtube, "fetch", fake_fetch)
    out = tmp_path / "new.jsonl"
    with pytest.raises(CommandError):  # open bez opisu zgody
        call_command(
            "harvest",
            "youtube",
            "--channel",
            "x",
            "--author",
            "Marcin Majewski",
            "--access",
            "open",
        )
    call_command(
        "harvest", "youtube", "--channel", "https://youtu.be/C0IBaOqKaXw", "--author", "Marcin Majewski",
        "--access", "open", "--license", "za zgodą autora (19.10.2026)",
        "--skip-manifest", str(known), "--out", str(out),
    )  # fmt: skip
    [e] = manifest.read(out)
    assert e.source_id == "AAAAAAAAAAA" and e.access == "open"
    assert e.license == "za zgodą autora (19.10.2026)"


def test_harvest_retries_429_and_appends_to_out(monkeypatch, tmp_path, settings):
    from django.core.management import call_command

    from library.harvest import manifest
    from library.management.commands import harvest as cmd

    settings.DATA_DIR = tmp_path
    monkeypatch.setattr(cmd.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class Limited(Exception):
        code = 429

    def fake_fetch(vid):
        calls["n"] += 1
        if vid == "AAAAAAAAAAA" and calls["n"] == 1:
            raise Limited("HTTP Error 429: Too Many Requests")
        if vid == "BBBBBBBBBBB" and calls["n"] < 5:
            raise Limited("HTTP Error 429: Too Many Requests")
        v = _video([youtube.Caption(0, "tekst")])
        v.video_id = vid
        return v

    monkeypatch.setattr(youtube, "fetch", fake_fetch)
    out = tmp_path / "yt.jsonl"
    args = ["harvest", "youtube", "--video", "AAAAAAAAAAA", "BBBBBBBBBBB", "--author", "M",
            "--retries", "1", "--out", str(out)]  # fmt: skip
    call_command(*args)
    assert [e.source_id for e in manifest.read(out)] == [
        "AAAAAAAAAAA"
    ]  # B: limit po ponowieniu
    call_command(
        *args
    )  # drugie uruchomienie dobiera brakujący, nie nadpisuje zebranych
    assert [e.source_id for e in manifest.read(out)] == ["AAAAAAAAAAA", "BBBBBBBBBBB"]
