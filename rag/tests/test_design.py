"""Offline contracts for the reading desk, markup, and self-hosted visual assets."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import resolve, reverse


class PageMarkup(HTMLParser):
    """Catch invalid nesting before the browser silently repairs the reading grid."""

    VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self, markup):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(markup)
        self.close()
        assert not self.stack, f"Unclosed tags: {self.stack}"

    def handle_starttag(self, tag, attrs):
        if tag == "label":
            assert not any(n["tag"] == "label" for n in self.stack), "Nested label"
        node = {
            "tag": tag,
            "attrs": dict(attrs),
            "text": "",
            "parents": list(self.stack),
        }
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        assert self.stack, f"Unexpected </{tag}>"
        assert self.stack[-1]["tag"] == tag, (
            f"Expected </{self.stack[-1]['tag']}>, found </{tag}>"
        )
        self.stack.pop()

    def handle_data(self, data):
        for node in self.stack:
            node["text"] += data

    def by_id(self, value):
        return next(n for n in self.nodes if n["attrs"].get("id") == value)

    def with_class(self, value):
        return [n for n in self.nodes if value in n["attrs"].get("class", "").split()]


def asset_path(name: str) -> Path:
    path = finders.find(name)
    assert isinstance(path, str), f"Missing static asset: {name}"
    return Path(path)


def render_desk(authenticated=False, demo=False):
    # RequestFactory omits middleware; tests attach its dynamic request fields.
    request: Any = RequestFactory().get(reverse("rag:index"))
    request.resolver_match = resolve(request.path)
    user = SimpleNamespace(
        is_authenticated=authenticated,
        is_superuser=authenticated,
        get_username="czytelnik",
    )
    request.user = user
    markup = render_to_string(
        "rag/index.html",
        {
            "user": user,
            "examples_json": json.dumps([{"title": "Logos", "q": "Co oznacza Logos?"}]),
            "works": [{"code": "WLC"}, {"code": "SBLGNT"}],
            "default_works": ["WLC"],
            "authors": [{"name": "Autor <tekst>"}],
            "default_mode": "scientific" if authenticated else "popular",
            "allowed_modes": ["popular", "scientific"]
            if authenticated
            else ["popular"],
            "demo_token_required": demo,
        },
        request=request,
    )
    return markup, PageMarkup(markup)


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("demo", [False, True])
def test_desk_has_valid_markup_and_separate_reading_regions(authenticated, demo):
    _, page = render_desk(authenticated, demo)
    main = page.by_id("main-content")
    assert main["tag"] == "main" and main["attrs"]["tabindex"] == "-1"
    for name in ["thread", "form", "sources"]:
        assert main in page.by_id(name)["parents"]
    welcome = page.by_id("welcome")
    opening = page.with_class("welcome-opening")[0]
    assert welcome in opening["parents"]
    assert page.with_class("folio-line")[0] not in opening["parents"]
    assert "Czytaj głębiej." in welcome["text"]
    assert "Od hebrajskiego słowa do jego interpretacji." in welcome["text"]
    assert (
        "Wybierz początek lektury" in page.with_class("suggestions-heading")[0]["text"]
    )
    ids = [n["attrs"]["id"] for n in page.nodes if "id" in n["attrs"]]
    assert len(ids) == len(set(ids))
    assert page.by_id("token")["attrs"]["type"] == ("password" if demo else "hidden")


@pytest.mark.parametrize("authenticated", [False, True])
def test_reading_options_preserve_permissions_defaults_and_csrf(authenticated):
    markup, page = render_desk(authenticated)
    options = page.with_class("context-options")[0]
    assert options["tag"] == "details" and "open" not in options["attrs"]
    for name in ["include_ane", "include_patristics", "author"]:
        assert options in page.by_id(name)["parents"]
        assert any(
            n["tag"] == "label" and n["attrs"].get("for") == name for n in page.nodes
        )
    modes = [n for n in page.nodes if n["attrs"].get("name") == "mode"]
    assert [n["attrs"]["value"] for n in modes] == (
        ["popular", "scientific"] if authenticated else ["popular"]
    )
    checked = [n["attrs"]["value"] for n in modes if "checked" in n["attrs"]]
    assert checked == ["scientific" if authenticated else "popular"]
    assert (
        any(n["attrs"].get("id") == "personal_only" for n in page.nodes)
        == authenticated
    )
    works = [n for n in page.nodes if n["attrs"].get("name") == "works"]
    assert [n["attrs"]["value"] for n in works if "checked" in n["attrs"]] == ["WLC"]
    assert any(n["attrs"].get("name") == "csrfmiddlewaretoken" for n in page.nodes)
    assert "Autor &lt;tekst&gt;" in markup


def test_desk_links_and_decoration_are_accessible_and_local():
    _, page = render_desk()
    assert page.with_class("skip-link")[0]["attrs"]["href"] == "#main-content"
    links = [n["attrs"].get("href") for n in page.nodes if n["tag"] == "a"]
    assert reverse("corpus:index") in links and reverse("pages:corpus") in links
    illustration = page.with_class("codex")[0]
    assert illustration["attrs"]["aria-hidden"] == "true"
    image = next(
        n for n in page.nodes if n["tag"] == "img" and illustration in n["parents"]
    )
    assert image["attrs"]["alt"] == ""
    assert image["attrs"]["src"].endswith("images/scriptura-codex.svg")
    assert page.by_id("q")["attrs"]["required"] is None
    assert page.by_id("ask")["attrs"]["type"] == "submit"


@pytest.mark.parametrize("authenticated", [False, True])
def test_question_shortcut_and_source_count_have_accessible_markup(authenticated):
    _, page = render_desk(authenticated)
    shortcut = page.with_class("ask-jump")[0]
    assert shortcut["tag"] == "a" and shortcut["attrs"]["href"] == "#q"
    assert "Zadaj pytanie" in shortcut["text"]
    languages = page.with_class("script-band")[0]
    assert languages["attrs"]["role"] == "group"
    assert languages["attrs"]["aria-label"] == "Języki tekstów biblijnych"
    counter = page.with_class("source-count")[0]
    value = page.by_id("source-count")
    assert counter in value["parents"] and value["text"] == "0"
    label = next(n for n in page.with_class("sr-only") if counter in n["parents"])
    assert label["text"].strip() == "Liczba źródeł:"
    assert value not in label["parents"]
    for node in page.nodes:
        if node["tag"] in {"div", "span"} and not node["attrs"].get("role"):
            assert "aria-label" not in node["attrs"]
            assert "aria-labelledby" not in node["attrs"]


@pytest.mark.parametrize(
    "template",
    ["pages/about.html", "pages/guide.html", "rag/login.html", "corpus/index.html"],
)
def test_shared_pages_keep_a_focusable_main_and_book_identity(template):
    request: Any = RequestFactory().get("/")
    request.user = SimpleNamespace(is_authenticated=False)
    page = PageMarkup(render_to_string(template, request=request))
    assert page.by_id("main-content")["tag"] == "main"
    assert page.with_class("brand-mark")[0]["tag"] == "svg"
    assert page.with_class("skip-link")[0]["attrs"]["href"] == "#main-content"


def test_parallel_text_keeps_table_in_a_named_keyboard_scroll_region():
    request: Any = RequestFactory().get(reverse("corpus:reference"))
    request.user = SimpleNamespace(is_authenticated=False)
    page = PageMarkup(render_to_string("corpus/reference.html", request=request))
    region = page.with_class("parallel-scroll")[0]
    assert region["attrs"]["role"] == "region"
    assert region["attrs"]["tabindex"] == "0"
    assert region["attrs"]["aria-label"] == "Teksty biblijne w układzie równoległym"
    table = page.with_class("parallel")[0]
    assert table["tag"] == "table" and region in table["parents"]


def test_visual_assets_are_available_without_cdn_or_runtime_dependencies():
    for asset in [
        "site.css",
        "workbench.css",
        "images/scriptura-mark.svg",
        "images/scriptura-codex.svg",
        "fonts/scriptura-serif.woff2",
        "fonts/scriptura-serif-italic.woff2",
        "fonts/OFL.txt",
    ]:
        assert asset_path(asset).stat().st_size > 0, asset
    for name in ["scriptura-mark.svg", "scriptura-codex.svg"]:
        root = ElementTree.parse(asset_path(f"images/{name}")).getroot()
        assert root.tag.endswith("}svg")
        for element in root.iter():
            assert not element.tag.endswith("}script")
            assert all(
                not value.startswith(("http:", "https:", "//"))
                for key, value in element.attrib.items()
                if key.endswith("href")
            )
    for asset in ["site.css", "workbench.css"]:
        css = asset_path(asset).read_text()
        assert not re.search(r"https?://|@import", css)
        for target in re.findall(r"url\(['\"]?([^)'\"]+)", css):
            assert (asset_path(asset).parent / target).is_file()
    for font in ["scriptura-serif.woff2", "scriptura-serif-italic.woff2"]:
        assert asset_path(f"fonts/{font}").read_bytes()[:4] == b"wOF2"
    assert (
        "SIL OPEN FONT LICENSE"
        in (settings.BASE_DIR / "static/fonts/OFL.txt").read_text()
    )
