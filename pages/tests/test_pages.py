"""Podstrony informacyjne renderują się dla anonima i zalogowanego; pasek i stopka są wspólne."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

PAGES = ["pages:about", "pages:guide", "pages:corpus", "pages:for_authors", "pages:author"]


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
