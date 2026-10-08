"""Podstrony informacyjne renderują się dla anonima i zalogowanego; pasek i stopka są wspólne."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

PAGES = [
    "pages:about",
    "pages:guide",
    "pages:corpus",
    "pages:for_authors",
    "pages:author",
]


@pytest.mark.django_db
@pytest.mark.parametrize("name", PAGES)
def test_pages_render_anonymous(client, name):
    r = client.get(reverse(name))
    assert r.status_code == 200
    body = r.content.decode()
    assert 'class="topbar"' in body and 'class="site-footer"' in body
    assert "Zaloguj" in body


@pytest.mark.django_db
def test_corpus_page_for_superuser_shows_extended_levels(client):
    user = User.objects.create_superuser("root", "r@x.pl", "x")
    client.force_login(user)
    r = client.get(reverse("pages:corpus"))
    assert r.status_code == 200
    assert "licensed" in r.content.decode()
    assert "Wyloguj" in r.content.decode()


@pytest.mark.django_db
def test_chat_and_text_share_topbar(client):
    for url in [reverse("rag:index"), reverse("corpus:index"), reverse("rag:login")]:
        body = client.get(url).content.decode()
        assert 'class="mainnav"' in body, url


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("allow_anonymous", "anon_popular_only"),
    [(True, False), (True, True), (False, False), (False, True)],
)
def test_guide_and_login_describe_configured_guest_access(
    client, settings, allow_anonymous, anon_popular_only
):
    settings.ALLOW_ANONYMOUS = allow_anonymous
    settings.ANONYMOUS_POPULAR_ONLY = anon_popular_only
    guide = client.get(reverse("pages:guide"))
    login = client.get(reverse("rag:login"))
    for response in (guide, login):
        assert response.status_code == 200
        assert response.context["allow_anonymous"] is allow_anonymous
        assert response.context["anon_popular_only"] is anon_popular_only
    guide_body = guide.content.decode()
    login_body = login.content.decode()
    scientific_for_guests = "Dostępny również bez logowania"
    if not allow_anonymous:
        assert "zadawanie pytań wymaga logowania" in guide_body
        assert "Zadawanie pytań wymaga logowania" in login_body
        assert scientific_for_guests not in guide_body
        assert "Dostępny po zalogowaniu." in guide_body
        assert "Bez logowania możesz korzystać" not in login_body
        assert "Wróć bez logowania" not in login_body
    else:
        for body in (guide_body, login_body):
            assert "zawsze wyłącznie z publicznych źródeł (<code>open</code>)" in body
        assert "Wróć bez logowania" in login_body
        if anon_popular_only:
            assert "tylko w trybie popularnonaukowym" in guide_body
            assert "tylko z trybu popularnonaukowego" in login_body
            assert scientific_for_guests not in guide_body
            assert "Dostępny po zalogowaniu." in guide_body
            assert "Tryb naukowy jest dostępny po zalogowaniu." in login_body
        else:
            assert "w trybie popularnonaukowym lub naukowym" in guide_body
            assert "z trybu popularnonaukowego i naukowego" in login_body
            assert scientific_for_guests in guide_body
            assert "Dostępny po zalogowaniu." not in guide_body
            assert "Tryb naukowy jest dostępny po zalogowaniu." not in login_body


@pytest.mark.django_db
@pytest.mark.parametrize(("rate_anon", "rate_user"), [(17, 43), (0, 0)])
def test_guide_describes_configured_limits(client, settings, rate_anon, rate_user):
    settings.ALLOW_ANONYMOUS = True
    settings.RATE_LIMIT_ANON = rate_anon
    settings.RATE_LIMIT_USER = rate_user
    response = client.get(reverse("pages:guide"))
    body = response.content.decode()
    assert response.context["rate_anon"] == rate_anon
    assert response.context["rate_user"] == rate_user
    if rate_anon:
        assert f"{rate_anon} pytań na godzinę" in body
        assert f"{rate_user} pytań na godzinę" in body
    else:
        assert body.count("brak godzinnego limitu pytań") == 2
    assert "0 pytań na godzinę" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("authenticated", [False, True])
def test_account_copy_does_not_promise_full_corpus(client, settings, authenticated):
    settings.ALLOW_ANONYMOUS = True
    settings.ANONYMOUS_POPULAR_ONLY = False
    if authenticated:
        client.force_login(User.objects.create_user("reader"))
    for name in ("pages:guide", "rag:login"):
        response = client.get(reverse(name))
        body = response.content.decode()
        assert response.context["access_levels"] == ["open"]
        assert "Samo logowanie nie daje dostępu do pełnego korpusu." in body
        assert (
            "Dostęp do źródeł licencjonowanych i prywatnych we wspólnym korpusie "
            "zależy od nadanych uprawnień."
        ) in body


@pytest.mark.django_db
def test_umami_snippet_only_when_configured(client, settings):
    settings.UMAMI_WEBSITE_ID = ""
    assert "data-website-id" not in client.get(reverse("pages:about")).content.decode()
    settings.UMAMI_WEBSITE_ID = "abc-123"
    settings.UMAMI_SCRIPT_URL = "https://cloud.umami.is/script.js"
    for url in [reverse("pages:about"), reverse("rag:index")]:
        body = client.get(url).content.decode()
        assert (
            'data-website-id="abc-123"' in body and "cloud.umami.is/script.js" in body
        ), url
