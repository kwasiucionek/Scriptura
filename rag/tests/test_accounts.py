import json
import re
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client

from corpus.importers.base import VerseRecord, import_records
from corpus.models import Work
from library.models import Chunk, Document
from rag import service
from rag.access import GROUP_LICENSED, access_for_user
from rag.models import Conversation, Message

pytestmark = pytest.mark.django_db


@pytest.fixture
def docs():
    o = Document.objects.create(title="Otwarty", access="open")
    Chunk.objects.create(
        document=o, order=0, text="Efod był szatą kapłańską.", sigla=[]
    )
    lic = Document.objects.create(title="Za zgodą", access="licensed")
    Chunk.objects.create(
        document=lic, order=0, text="Efod w komentarzu licencjonowanym.", sigla=[]
    )
    owner = User.objects.create_user("material-owner")
    for title, access in (("Prywatny", "private"), ("Cudzy osobisty", "personal")):
        doc = Document.objects.create(
            title=title, access=access, owner=owner if access == "personal" else None
        )
        Chunk.objects.create(
            document=doc, order=0, text=f"Efod — materiał {access}.", sigla=[]
        )
    return owner


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


def test_access_levels():
    anon = access_for_user(None)
    u = User.objects.create_user("jan", password="x")
    assert anon == ["open"] and access_for_user(u) == ["open"]
    u.groups.add(Group.objects.create(name=GROUP_LICENSED))
    assert access_for_user(u) == ["open", "licensed"]
    su = User.objects.create_superuser("root", password="x")
    assert access_for_user(su) == ["open", "licensed", "private"]


def test_anonymous_sees_only_open_and_popular(docs, settings):
    settings.ALLOW_ANONYMOUS = True
    settings.ANONYMOUS_POPULAR_ONLY = True
    ev = _ask(Client(HTTP_HOST="localhost"), "efod kapłański", mode="scientific")
    src = ev[0][1]["chunks"]
    assert [s["title"] for s in src] == ["Otwarty"]
    assert ev[-1][1]["mode"] == "popular"  # anonimowy nie dostaje trybu naukowego
    assert "conversation_id" not in ev[-1][1]


@pytest.mark.parametrize("mode", ["popular", "scientific"])
def test_anonymous_preserves_mode_with_only_open_sources(docs, settings, mode):
    settings.ALLOW_ANONYMOUS = True
    settings.ANONYMOUS_POPULAR_ONLY = False
    ev = _ask(Client(HTTP_HOST="localhost"), "efod kapłański", mode=mode)
    assert ev[0][0] == "sources" and ev[-1][0] == "done"
    src = ev[0][1]["chunks"]
    assert [s["title"] for s in src] == ["Otwarty"]
    assert {s["access"] for s in src} == {"open"}
    assert not any(s["personal"] for s in src)
    assert ev[-1][1]["mode"] == mode
    assert ev[-1][1]["model"] == "echo"
    assert "conversation_id" not in ev[-1][1]
    assert not Conversation.objects.exists()
    assert not Message.objects.exists()


@pytest.mark.parametrize("mode", ["popular", "scientific"])
@pytest.mark.parametrize("personal_only", [False, True])
def test_anonymous_cannot_forge_access_or_conversation(
    docs, settings, monkeypatch, mode, personal_only
):
    settings.ALLOW_ANONYMOUS = True
    settings.ANONYMOUS_POPULAR_ONLY = False
    settings.RAG_ACCESS = ["open", "licensed", "private", "personal"]
    works = []
    for level in settings.RAG_ACCESS:
        work = Work.objects.create(
            code=level.upper(),
            name=level,
            language="pl",
            kind="translation",
            access=level,
        )
        import_records(work, iter([VerseRecord("John", 1, 1, f"Słowo {level}")]))
        works.append(work.code)
    owner = docs
    conversation = Conversation.objects.create(user=owner, title="Cudza rozmowa")
    Message.objects.create(
        conversation=conversation, role="user", content="Prywatne pytanie"
    )
    conversations_before = list(Conversation.objects.values())
    messages_before = list(Message.objects.values())
    ask = Mock(wraps=service.ask)
    monkeypatch.setattr(service, "ask", ask)

    ev = _ask(
        Client(HTTP_HOST="localhost"),
        "efod kapłański (J 1,1)",
        mode=mode,
        works=works,
        access=settings.RAG_ACCESS,
        user_id=owner.pk,
        owner_id=owner.pk,
        is_authenticated=True,
        is_superuser=True,
        personal_only=personal_only,
        conversation_id=conversation.pk,
    )
    assert ev[0][0] == "sources" and ev[-1][0] == "done"
    src = ev[0][1]
    assert [s["title"] for s in src["chunks"]] == ["Otwarty"]
    assert {s["access"] for s in src["chunks"]} == {"open"}
    assert not any(s["personal"] for s in src["chunks"])
    assert {s["work"] for s in src["verses"]} == {"OPEN"}
    assert ev[-1][1]["mode"] == mode
    assert "conversation_id" not in ev[-1][1]
    ask.assert_called_once()
    assert ask.call_args.kwargs["access"] == ["open"]
    assert ask.call_args.kwargs["user_id"] is None
    assert ask.call_args.kwargs["personal_only"] is False
    assert list(Conversation.objects.values()) == conversations_before
    assert list(Message.objects.values()) == messages_before


def test_licensed_user_sees_licensed_and_history_is_saved(docs):
    u = User.objects.create_user("anna", password="x")
    u.groups.add(Group.objects.create(name=GROUP_LICENSED))
    c = Client(HTTP_HOST="localhost")
    c.force_login(u)
    ev = _ask(c, "efod kapłański", mode="scientific")
    assert {s["title"] for s in ev[0][1]["chunks"]} == {"Otwarty", "Za zgodą"}
    conv_id = ev[-1][1]["conversation_id"]
    conv = Conversation.objects.get(id=conv_id, user=u)
    assert [m.role for m in conv.messages.all()] == ["user", "assistant"]
    # druga tura w tej samej rozmowie
    ev2 = _ask(c, "a urim i tummim?", conversation_id=conv_id)
    assert ev2[-1][1]["conversation_id"] == conv_id and conv.messages.count() == 4
    lst = c.get("/conversations/").json()["conversations"]
    assert lst[0]["id"] == conv_id and lst[0]["title"].startswith("efod")
    det = c.get(f"/conversations/{conv_id}/").json()
    assert (
        len(det["messages"]) == 4
        and det["messages"][1]["meta"]["result"]["mode"] == "scientific"
    )
    other = Client(HTTP_HOST="localhost")
    other.force_login(User.objects.create_user("obcy", password="x"))
    assert other.get(f"/conversations/{conv_id}/").status_code == 404


def test_login_required_when_anonymous_disabled(settings):
    settings.ALLOW_ANONYMOUS = False
    c = Client(HTTP_HOST="localhost")
    assert c.get("/").status_code == 302
    assert (
        c.post(
            "/ask/stream",
            data=json.dumps({"question": "efod"}),
            content_type="application/json",
        ).status_code
        == 401
    )


@pytest.mark.parametrize("mode", ["popular", "scientific"])
def test_rate_limit_for_anonymous(settings, docs, mode):
    from django.core.cache import cache

    cache.clear()
    settings.ALLOW_ANONYMOUS = True
    settings.ANONYMOUS_POPULAR_ONLY = False
    settings.RATE_LIMIT_ANON = 2
    c = Client(HTTP_HOST="localhost", REMOTE_ADDR="10.0.0.9")
    for allowed_mode in ("popular", "scientific"):
        assert (
            c.post(
                "/ask/stream",
                data=json.dumps({"question": "efod kapłański", "mode": allowed_mode}),
                content_type="application/json",
            ).status_code
            == 200
        )
    r = c.post(
        "/ask/stream",
        data=json.dumps({"question": "efod kapłański", "mode": mode}),
        content_type="application/json",
    )
    assert r.status_code == 429
    other = Client(HTTP_HOST="localhost", REMOTE_ADDR="10.0.0.10")
    assert (
        other.post(
            "/ask/stream",
            data=json.dumps({"question": "efod kapłański", "mode": mode}),
            content_type="application/json",
        ).status_code
        == 200
    )
    long_q = "x" * (settings.QUESTION_MAX_CHARS + 1)
    assert (
        other.post(
            "/ask/stream",
            data=json.dumps({"question": long_q, "mode": mode}),
            content_type="application/json",
        ).status_code
        == 400
    )
    cache.clear()


def test_examples_depend_on_access(docs):
    from django.contrib.auth.models import Group, User

    from rag.access import GROUP_LICENSED

    anon = Client(HTTP_HOST="localhost").get("/").content.decode()
    assert "Ugarit i początki kultu" in anon and "Pwt 32,8-9" not in anon
    u = User.objects.create_user("lic", password="x")
    u.groups.add(Group.objects.create(name=GROUP_LICENSED))
    c = Client(HTTP_HOST="localhost")
    c.force_login(u)
    page = c.get("/").content.decode()
    assert "Pwt 32,8-9" in page and "Kodeksie Synajskim" in page
