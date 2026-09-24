import json
import re

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from library.models import Chunk, Document

pytestmark = pytest.mark.django_db

MD = b"# Jerycho\n\nMury Jerycha zostaly zniszczone ok. 1550 r. p.n.e., 300 lat przed czasami Jozuego (Joz 6). Archeologia pokazuje warstwe zniszczenia, ale nie daje podpisu sprawcy.\n"


def _events(resp):
    body = b"".join(resp.streaming_content).decode()
    return [
        (e, json.loads(d)) for e, d in re.findall(r"event: (\w+)\ndata: (.*)\n", body)
    ]


def _ask(client, q, **kw):
    return _events(
        client.post(
            "/ask/stream",
            data=json.dumps({"question": q, **kw}),
            content_type="application/json",
        )
    )


def test_upload_personal_and_retrieve_only_for_owner(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    Document.objects.create(title="Otwarty o Jerychu", access="open")
    Chunk.objects.create(
        document=Document.objects.get(title="Otwarty o Jerychu"),
        order=0,
        text="Mury Jerycha zniszczone — ujecie otwarte (Joz 6).",
        sigla=[],
    )
    owner = User.objects.create_user("ja", password="x")
    other = User.objects.create_user("obcy", password="x")

    c = Client(HTTP_HOST="localhost")
    c.force_login(owner)
    r = c.post(
        "/account/",
        {
            "action": "upload",
            "title": "Webinar: Jerycho",
            "author": "",
            "doc_type": "script",
            "register": "popular",
            "file": SimpleUploadedFile("webinar.md", MD, content_type="text/markdown"),
        },
    )
    assert r.status_code == 302
    doc = owner.documents.get()
    assert (
        doc.access == "personal"
        and doc.chunk_count >= 1
        and doc.authors.first().name == "ja"
    )
    assert (tmp_path / "library" / "users" / str(owner.id)).exists()

    # dublet tego samego pliku odrzucony
    r = c.post(
        "/account/",
        {
            "action": "upload",
            "title": "Znowu",
            "doc_type": "script",
            "register": "popular",
            "file": SimpleUploadedFile("w2.md", MD),
        },
    )
    assert "już w Twoich materiałach" in r.content.decode()

    # właściciel widzi materiał w RAG (razem z open), a z personal_only tylko swój
    ev = _ask(c, "mury Jerycha zniszczone", mode="popular")
    titles = {s["title"] for s in ev[0][1]["chunks"]}
    assert "Webinar: Jerycho" in titles and "Otwarty o Jerychu" in titles
    assert any(
        s["personal"] for s in ev[0][1]["chunks"] if s["title"] == "Webinar: Jerycho"
    )
    ev = _ask(c, "mury Jerycha zniszczone", mode="popular", personal_only=True)
    assert {s["title"] for s in ev[0][1]["chunks"]} == {"Webinar: Jerycho"}

    # inny użytkownik i anonim nie widzą
    o = Client(HTTP_HOST="localhost")
    o.force_login(other)
    assert "Webinar: Jerycho" not in {
        s["title"] for s in _ask(o, "mury Jerycha zniszczone")[0][1]["chunks"]
    }
    anon = Client(HTTP_HOST="localhost")
    assert "Webinar: Jerycho" not in {
        s["title"] for s in _ask(anon, "mury Jerycha zniszczone")[0][1]["chunks"]
    }
    assert o.get("/account/").content.decode().count("Webinar") == 0

    # usunięcie
    r = c.post("/account/", {"action": "delete", "doc_id": doc.id})
    assert r.status_code == 302 and owner.documents.count() == 0


def test_profile_and_password_change():
    u = User.objects.create_user("anna", password="stare-haslo-123")
    c = Client(HTTP_HOST="localhost")
    c.force_login(u)
    c.post(
        "/account/",
        {
            "action": "profile",
            "first_name": "Anna",
            "last_name": "K",
            "email": "a@x.pl",
        },
    )
    u.refresh_from_db()
    assert u.first_name == "Anna" and u.email == "a@x.pl"
    r = c.post(
        "/account/",
        {
            "action": "password",
            "old_password": "stare-haslo-123",
            "new_password1": "Nowe-haslo-987",
            "new_password2": "Nowe-haslo-987",
        },
    )
    assert r.status_code == 302
    u.refresh_from_db()
    assert u.check_password("Nowe-haslo-987")
    assert (
        Client(HTTP_HOST="localhost").get("/account/").status_code == 302
    )  # anonim -> login


def test_share_and_unshare_personal(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    owner = User.objects.create_user("autor", password="x")
    reader = User.objects.create_user("czytelnik", password="x")
    from django.contrib.auth.models import Group

    from rag.access import GROUP_LICENSED

    reader.groups.add(Group.objects.create(name=GROUP_LICENSED))
    c = Client(HTTP_HOST="localhost")
    c.force_login(owner)
    c.post(
        "/account/",
        {
            "action": "upload",
            "title": "Mój webinar",
            "doc_type": "script",
            "register": "popular",
            "file": SimpleUploadedFile("w.md", MD),
        },
    )
    doc = owner.documents.get()
    r = Client(HTTP_HOST="localhost")
    r.force_login(reader)
    assert "Mój webinar" not in {
        s["title"] for s in _ask(r, "mury Jerycha zniszczone")[0][1]["chunks"]
    }

    # bez akceptacji oświadczenia — odmowa
    c.post(
        "/account/",
        {
            "action": "share",
            "doc_id": doc.id,
            "register": "scientific",
            "license": "za zgodą autora",
        },
    )
    doc.refresh_from_db()
    assert doc.access == "personal"
    # z akceptacją — licensed + Consent
    c.post(
        "/account/",
        {
            "action": "share",
            "doc_id": doc.id,
            "register": "scientific",
            "license": "za zgodą autora",
            "accept": "on",
        },
    )
    doc.refresh_from_db()
    assert (
        doc.access == "licensed" and doc.register == "scientific" and doc.owner == owner
    )
    assert doc.consents.filter(revoked_at__isnull=True).count() == 1
    assert "Mój webinar" in {
        s["title"]
        for s in _ask(r, "mury Jerycha zniszczone", mode="scientific")[0][1]["chunks"]
    }
    # anonim (tylko open) nadal nie widzi
    assert "Mój webinar" not in {
        s["title"]
        for s in _ask(Client(HTTP_HOST="localhost"), "mury Jerycha zniszczone")[0][1][
            "chunks"
        ]
    }
    # wycofanie
    c.post("/account/", {"action": "unshare", "doc_id": doc.id})
    doc.refresh_from_db()
    assert doc.access == "personal" and doc.consents.get().revoked_at is not None
    assert "Mój webinar" not in {
        s["title"] for s in _ask(r, "mury Jerycha zniszczone")[0][1]["chunks"]
    }
