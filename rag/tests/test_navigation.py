"""The shared menu uses only the supplied logo and preserves guest permissions."""

import struct
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse


class Tags(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.nodes = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name", ["rag:index", "pages:guide", "pages:about", "rag:login", "corpus:index"]
)
def test_shared_header_uses_user_logo_and_keeps_navigation(client, name):
    response = client.get(reverse(name))
    assert response.status_code == 200
    markup = response.content.decode()
    nodes = Tags(markup).nodes
    images = [attrs for tag, attrs in nodes if tag == "img"]
    assert len(images) == 1
    assert images[0]["class"] == "brand-logo"
    assert "/images/logo" in images[0]["src"] and images[0]["src"].endswith(".png")
    assert images[0]["alt"] == "Scriptura — strona główna"
    assert (images[0]["width"], images[0]["height"]) == ("320", "292")
    brand = next(
        attrs for tag, attrs in nodes if tag == "a" and attrs.get("class") == "brand"
    )
    assert brand["href"] == reverse("rag:index")
    nav = next(
        attrs
        for tag, attrs in nodes
        if tag == "nav" and attrs.get("class") == "mainnav"
    )
    assert nav["aria-label"] == "Nawigacja główna"
    current = [
        attrs
        for tag, attrs in nodes
        if tag == "a" and attrs.get("aria-current") == "page"
    ]
    expected = "corpus:index" if name == "corpus:index" else name
    if name != "rag:login":
        assert len(current) == 1 and current[0]["href"] == reverse(expected)
    assert "scriptura-codex" not in markup
    assert not any(tag in {"svg", "figure"} for tag, _ in nodes)
    assert ("workbench.css" in markup) == (name == "rag:index")
    assert "navigation.js" in markup


@pytest.mark.django_db
@pytest.mark.parametrize("popular_only", [False, True])
def test_guest_mode_picker_obeys_policy_without_exposing_personal_filters(
    client, settings, popular_only
):
    settings.ALLOW_ANONYMOUS = True
    settings.ANONYMOUS_POPULAR_ONLY = popular_only
    response = client.get(reverse("rag:index"))
    nodes = Tags(response.content.decode()).nodes
    modes = [
        attrs["value"]
        for tag, attrs in nodes
        if tag == "input" and attrs.get("name") == "mode"
    ]
    assert modes == (["popular"] if popular_only else ["popular", "scientific"])
    assert not any(attrs.get("id") == "personal_only" for _, attrs in nodes)


def test_menu_assets_are_local_and_use_only_user_logo_and_system_fonts():
    logo = finders.find("images/logo.png")
    css = finders.find("site.css")
    assert isinstance(logo, str) and isinstance(css, str)
    data = Path(logo).read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", data[16:24])
    assert 0 < width <= 4096 and 0 < height <= 4096
    assert len(data) < 2_000_000
    styles = Path(css).read_text()
    assert "font: 500 16px/1.4 var(--sans)" in styles
    assert "font-size: 15px" in styles
    workbench = finders.find("workbench.css")
    assert isinstance(workbench, str)
    for content in (styles, Path(workbench).read_text()):
        assert "@font-face" not in content
        assert "https://" not in content and "@import" not in content
        assert "url(" not in content
    assert finders.find("navigation.js")
