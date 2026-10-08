"""Modern workspace copy, accessible controls and no decorative assets."""

from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.urls import reverse


class Document(HTMLParser):
    void = {
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
        self.errors = []
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = {
            "tag": tag,
            "attrs": dict(attrs),
            "text": "",
            "parent": self.stack[-1] if self.stack else None,
        }
        self.nodes.append(node)
        if tag not in self.void:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1]["tag"] != tag:
            self.errors.append(f"Unexpected closing tag: {tag}")
        else:
            self.stack.pop()

    def handle_data(self, data):
        for node in self.stack:
            node["text"] += data

    def by_id(self, ident):
        return next(node for node in self.nodes if node["attrs"].get("id") == ident)

    def by_class(self, name):
        return next(
            node
            for node in self.nodes
            if name in node["attrs"].get("class", "").split()
        )


@pytest.mark.django_db
@pytest.mark.parametrize("member", [False, True])
def test_workspace_has_valid_nested_html_and_restored_copy(
    client, settings, django_user_model, member
):
    settings.ALLOW_ANONYMOUS = True
    settings.ANONYMOUS_POPULAR_ONLY = False
    if member:
        client.force_login(django_user_model.objects.create_user(username="reader"))
    response = client.get(reverse("rag:index"))
    assert response.status_code == 200
    doc = Document(response.content.decode())
    assert not doc.errors
    assert not doc.stack
    title = next(node for node in doc.nodes if node["tag"] == "title")
    assert title["text"] == "Scriptura — asystent teologii biblijnej"
    h1 = [node for node in doc.nodes if node["tag"] == "h1"]
    assert len(h1) == 1
    assert " ".join(h1[0]["text"].split()) == "Czytaj głębiej. Pytaj u źródła."
    copy = doc.by_class("welcome-copy")
    assert "Od hebrajskiego słowa do jego interpretacji." in copy["text"]
    assert "Każda lektura ma swój margines" in doc.by_id("sources")["text"]
    assert len([node for node in doc.nodes if node["tag"] == "img"]) == 1
    assert not any(
        node["tag"] in {"svg", "figure", "object", "iframe"} for node in doc.nodes
    )
    ids = [node["attrs"]["id"] for node in doc.nodes if "id" in node["attrs"]]
    assert len(ids) == len(set(ids))
    assert doc.by_class("skip-link")["attrs"]["href"] == "#main-content"
    assert doc.by_id("main-content")["tag"] == "main"
    assert doc.by_id("q")["attrs"]["required"] is None
    assert doc.by_id("q")["attrs"]["minlength"] == "3"
    assert doc.by_id("q")["attrs"]["rows"] == "1"
    assert doc.nodes.index(doc.by_id("question-index")) < doc.nodes.index(
        doc.by_class("script-band")
    )
    label = doc.by_class("composer-label")
    assert label["attrs"]["for"] == "q"
    assert label["text"] == "Twoje pytanie do tekstu"
    assert doc.by_class("mode-options")["tag"] == "fieldset"
    assert doc.by_id("status")["attrs"]["role"] == "status"
    for ident, content_class in [("question-index", "chips"), (None, "opts")]:
        details = doc.by_id(ident) if ident else doc.by_class("context-options")
        assert details["tag"] == "details" and "open" in details["attrs"]
        assert doc.by_class(content_class)["parent"] is details
        children = [node for node in doc.nodes if node["parent"] is details]
        assert children[0]["tag"] == "summary" and children[0]["text"].strip()
    for ident in ["include_ane", "include_patristics"]:
        select = doc.by_id(ident)
        options = [node for node in doc.nodes if node["parent"] is select]
        assert [(node["attrs"]["value"], node["text"]) for node in options] == [
            ("auto", "automatycznie"),
            ("on", "zawsze"),
            ("off", "nigdy"),
        ]
    modes = [
        node["attrs"]["value"]
        for node in doc.nodes
        if node["attrs"].get("name") == "mode"
    ]
    assert modes == ["popular", "scientific"]
    assert (
        any(node["attrs"].get("id") == "personal_only" for node in doc.nodes) == member
    )
    assert any(node["attrs"].get("id") == "history-box" for node in doc.nodes) == member
    if member:
        personal_label = doc.by_id("personal_only")["parent"]
        assert personal_label["parent"] is doc.by_class("composer-settings")
    if not member:
        assert (
            "Goście korzystają wyłącznie z publicznych źródeł."
            in doc.by_class("guest-note")["text"]
        )


def test_workspace_styles_keep_readable_type_and_no_external_artwork():
    root = Path(django_settings.BASE_DIR)
    for name in ["site.css", "workbench.css"]:
        text = (root / "static" / name).read_text()
        assert "@font-face" not in text
        assert "url(" not in text and "@import" not in text
    workbench = (root / "static/workbench.css").read_text()
    assert "font: 400 18px/1.85 var(--sans)" in workbench
    assert "prefers-reduced-motion" in workbench
    assert "'SBL Hebrew', 'Ezra SIL', 'Noto Serif Hebrew', serif" in workbench
    assert "forced-colors" in workbench
    template = (root / "templates/rag/index.html").read_text()
    assert "getComputedStyle(t).overflowY" in template
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in template
