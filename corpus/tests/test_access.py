from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse

from corpus.importers.base import TokenRecord, VerseRecord, import_records
from corpus.models import VerseLink, Work
from corpus.services import text as svc
from rag.access import GROUP_LICENSED

pytestmark = pytest.mark.django_db


@pytest.fixture
def works():
    out = []
    for level in ("open", "licensed", "private", "personal"):
        work = Work.objects.create(
            code=level.upper(),
            name=level,
            language="pl",
            kind="translation",
            access=level,
        )
        import_records(
            work,
            iter(
                [
                    VerseRecord(
                        "John",
                        1,
                        1,
                        f"Słowo {level}",
                        [TokenRecord("Słowo", lemma="słowo", strong="G1")],
                    ),
                    VerseRecord(
                        "John",
                        1,
                        2,
                        f"Słowo drugie {level}",
                        [TokenRecord("Słowo", lemma="słowo", strong="G1")],
                    ),
                ]
            ),
        )
        out.append(work)
    VerseLink.objects.create(
        from_ordinal=50001001, to_start=50001002, to_end=50001002, votes=10
    )
    return out


def test_safe_selection_contract(works):
    assert [w.code for w in svc.active_works()] == ["OPEN"]
    assert svc.active_works([]) == []
    assert svc.active_works(["LICENSED"]) == []
    assert svc.active_works(["unknown"]) == []
    assert svc.active_works(access=[]) == []
    assert [
        w.code
        for w in svc.active_works(access=["open", "licensed", "private", "personal"])
    ] == ["LICENSED", "OPEN", "PRIVATE"]
    assert [
        w.code
        for w in svc.visible_works(list(reversed(works)), access=["open", "licensed"])
    ] == ["LICENSED", "OPEN"]
    Work.objects.filter(pk=works[0].pk).update(access="private")
    assert svc.visible_works([works[0]]) == []  # stale in-memory access='open'


@pytest.mark.parametrize(
    "levels,expected",
    [(None, {"OPEN"}), ([], set()), (["open", "licensed"], {"OPEN", "LICENSED"})],
)
def test_all_text_services_enforce_acl_with_raw_work_objects(works, levels, expected):
    rows = svc.parallel(svc.resolve("J 1,1"), works, access=levels)
    assert set(rows[0].texts) == expected
    concordance = svc.concordance(lemma="słowo", access=levels)
    assert concordance.total == 2 * len(expected)
    assert {t.verse_text.work.code for t in concordance.tokens} == expected
    lexical = svc.lexical("słowo", works, access=levels)
    assert {h.work for h in lexical.hits} == expected
    assert lexical.total == 2 * len(expected)
    related = svc.related_verses([(50001001, 50001001)], works, access=levels)
    assert len(related) == 1
    assert related[0].work in expected if expected else related[0].text == ""


def test_empty_selection_never_falls_back(works):
    assert svc.parallel([], works) == []
    assert svc.parallel(svc.resolve("J 1,1"), [works[1]])[0].texts == {}
    assert svc.lexical("słowo", []).total == 0
    related = svc.related_verses([(50001001, 50001001)], [])
    assert related[0].text == "" and related[0].work == ""


def test_hidden_tokens_do_not_produce_public_cross_corpus_hits(works):
    secret = Work.objects.create(
        code="SECRET", name="secret", language="pl", kind="original", access="private"
    )
    import_records(
        secret,
        iter(
            [
                VerseRecord(
                    "John",
                    1,
                    1,
                    "Ukryty lemat",
                    [TokenRecord("Ukryty", lemma="tajny", strong="G999")],
                )
            ]
        ),
    )
    assert svc.lexical("G999", [works[0]]).total == 0
    assert svc.concordance(strong="G999").total == 0
    assert svc.lexical("G999", [works[0]], access=["open", "private"]).total == 1


def os_response(work):
    return {
        "hits": {
            "total": 1,
            "hits": [
                {
                    "_source": {
                        "ref": "J 1,1",
                        "osis_id": "John.1.1",
                        "ordinal": 50001001,
                        "work": work.code,
                        "language": "pl",
                        "text": "Słowo from index",
                    }
                }
            ],
        },
        "aggregations": {"by_work": {"buckets": [{"key": work.code, "doc_count": 1}]}},
    }


def test_opensearch_uses_current_allowed_codes_and_final_sql_check(
    works, monkeypatch, settings
):
    settings.SEARCH_BACKEND = "opensearch"
    calls = []

    def query(**kwargs):
        calls.append(kwargs["body"])
        Work.objects.filter(pk=works[0].pk).update(access="private")
        return os_response(works[0])

    monkeypatch.setattr(
        "corpus.search.client.get_client", lambda: SimpleNamespace(search=query)
    )
    result = svc.lexical("słowo", works)
    assert calls[0]["query"]["bool"]["filter"] == [{"terms": {"work": ["OPEN"]}}]
    assert result.total == 0 and result.hits == [] and result.by_work == []


def test_opensearch_empty_acl_does_not_query(works, monkeypatch, settings):
    settings.SEARCH_BACKEND = "opensearch"
    monkeypatch.setattr(
        "corpus.search.client.get_client",
        lambda: pytest.fail("no visible works, no search"),
    )
    assert svc.lexical("słowo", works, access=[]).total == 0


def test_text_views_filter_selectors_and_results(client, works):
    for name in ("corpus:index", "corpus:reference", "corpus:search"):
        response = client.get(
            reverse(name),
            {
                "q": "J 1,1" if name.endswith("reference") else "słowo",
                "works": "OPEN,LICENSED,PRIVATE,PERSONAL",
            },
        )
        assert response.status_code == 200
        assert [w.code for w in response.context["works"]] == ["OPEN"]
    response = client.get(reverse("corpus:search"), {"q": "słowo", "works": "LICENSED"})
    assert response.context["result"].total == 0
    assert response.context["works"] == []
    user = User.objects.create_user("licensed")
    user.groups.add(Group.objects.create(name=GROUP_LICENSED))
    client.force_login(user)
    response = client.get(
        reverse("corpus:search"), {"q": "słowo", "works": "OPEN,LICENSED,PRIVATE"}
    )
    assert {h.work for h in response.context["result"].hits} == {"OPEN", "LICENSED"}
    response = client.get(reverse("corpus:concordance"), {"lemma": "słowo"})
    assert response.context["result"].total == 4


@pytest.mark.parametrize("name", ["corpus:search", "corpus:concordance"])
@pytest.mark.parametrize("page", ["oops", "", "0", "-1", "1.5", "9" * 5000])
def test_bad_pages_return_400(client, name, page):
    assert client.get(reverse(name), {"page": page}).status_code == 400


@pytest.mark.parametrize("query", ["a NEAR/999999999999999999999 b", "x" * 4097])
def test_bad_lexical_parameters_return_400(client, works, query):
    assert (
        client.get(reverse("corpus:search"), {"q": query, "works": "OPEN"}).status_code
        == 400
    )


def test_bad_reference_returns_400(client):
    assert client.get(reverse("corpus:reference"), {"q": "nonsens"}).status_code == 400
