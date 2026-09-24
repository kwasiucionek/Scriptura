import json
import re

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client

from library.models import Chunk, Document
from rag.access import GROUP_LICENSED, access_for_user
from rag.models import Conversation

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
    ev = _ask(Client(HTTP_HOST="localhost"), "efod kapłański", mode="scientific")
    src = ev[0][1]["chunks"]
    assert [s["title"] for s in src] == ["Otwarty"]
    assert ev[-1][1]["mode"] == "popular"  # anonimowy nie dostaje trybu naukowego
    assert "conversation_id" not in ev[-1][1]


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


def test_rate_limit_for_anonymous(settings, docs):
    from django.core.cache import cache

    cache.clear()
    settings.ALLOW_ANONYMOUS = True
    settings.RATE_LIMIT_ANON = 2
    c = Client(HTTP_HOST="localhost", REMOTE_ADDR="10.0.0.9")
    for _ in range(2):
        assert (
            c.post(
                "/ask/stream",
                data=json.dumps({"question": "efod kapłański"}),
                content_type="application/json",
            ).status_code
            == 200
        )
    r = c.post(
        "/ask/stream",
        data=json.dumps({"question": "efod kapłański"}),
        content_type="application/json",
    )
    assert r.status_code == 429
    other = Client(HTTP_HOST="localhost", REMOTE_ADDR="10.0.0.10")
    assert (
        other.post(
            "/ask/stream",
            data=json.dumps({"question": "efod kapłański"}),
            content_type="application/json",
        ).status_code
        == 200
    )
    long_q = "x" * (settings.QUESTION_MAX_CHARS + 1)
    assert (
        other.post(
            "/ask/stream",
            data=json.dumps({"question": long_q}),
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
