"""Offline template/JavaScript regressions; no browser or external services.

JavaScript checks use the installed Node runtime and a deliberately small DOM
stub. They are skipped when Node is unavailable; template checks still run.
"""

import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.template.loader import render_to_string
from django.test import RequestFactory


@pytest.fixture
def interface_page():
    request = RequestFactory().get("/")
    request.user = SimpleNamespace(
        is_authenticated=True, is_superuser=True, get_username="tester"
    )
    return render_to_string(
        "rag/index.html",
        {
            "user": request.user,
            "examples_json": "[]",
            "works": [],
            "authors": [],
            "default_mode": "popular",
            "allowed_modes": ["popular", "scientific"],
        },
        request=request,
    )


# Only the APIs touched by the page script are implemented. In particular, source
# IDs are resolved from the currently rendered panel, not from saved messages.
DOM_STUB = r"""
const assert = require('node:assert/strict');
const nodes = new Map();
let scrolledSource = null;
class Element {
  constructor(id = '') {
    this.id = id; this.children = []; this.dataset = {}; this.attrs = {};
    this.value = ''; this.textContent = ''; this._html = '';
    this.classList = { toggle() {} };
  }
  set innerHTML(html) { this._html = html; this.refs = null; this.show = null; this.answer = null; }
  get innerHTML() { return this._html; }
  appendChild(el) { this.children.push(el); }
  replaceChildren(...els) { this.children = els; }
  setAttribute(k, v) { this.attrs[k] = v; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener() {}
  remove() {}
  focus() {}
  click() {}
  scrollIntoView() { scrolledSource = this.id; }
  closest() { return this.group ??= new Element(); }
  querySelector(selector) {
    if (selector === '.answer') return this.answer ??= new Element();
    if (selector === '.show-sources') {
      if (!this._html.includes('show-sources')) return null;
      return this.show ??= new Element();
    }
    if (selector === 'details') return this.details ??= new Element();
    throw new Error('Unexpected selector: ' + selector);
  }
  querySelectorAll(selector) {
    if (selector === 'details.grp') return [];
    if (selector === '.show-sources') return this.children.flatMap(el => {
      const b = el.querySelector('.show-sources'); return b ? [b] : [];
    });
    if (selector === '.ref') {
      return this.refs ??= [...this._html.matchAll(/<button[^>]*class="ref[^>]*data-n="(\d+)"[^>]*>/g)].map(m => {
        const el = new Element(); el.dataset.n = m[1];
        el.dataset.kind = /data-kind="([PA])"/.exec(m[0])?.[1];
        return el;
      });
    }
    throw new Error('Unexpected selectorAll: ' + selector);
  }
}
for (const id of ['welcome', 'sources', 'source-count', 'thread', 'form', 'q',
                  'ask', 'ask-label', 'clear', 'status', 'token', 'chips', 'author']) {
  nodes.set(id, new Element(id));
}
nodes.get('sources').innerHTML = '<div>initial empty sources</div>';
const csrfInput = { value: 'masked-csrf-token' };
const document = {
  getElementById(id) {
    if (id.startsWith('src-')) {
      return nodes.get('sources').innerHTML.includes(`id="${id}"`) ? new Element(id) : null;
    }
    return nodes.get(id) || null;
  },
  createElement() { return new Element(); },
  querySelector(selector) {
    if (selector === '#form input[name=csrfmiddlewaretoken]') return csrfInput;
    if (selector === 'input[name=mode]:checked') return { value: 'popular' };
    throw new Error('Unexpected document selector: ' + selector);
  },
  querySelectorAll(selector) {
    if (selector === 'input[name=works]:checked') return [];
    throw new Error('Unexpected document selectorAll: ' + selector);
  },
};
const localStorage = { getItem() { return null; }, setItem() {} };
const location = { search: '', pathname: '/' };
const history = { replaceState() {} };
const window = {};
URL.createObjectURL = () => 'blob:offline';
URL.revokeObjectURL = () => {};
const alert = message => { throw new Error(message); };
let fetch = async () => { throw new Error('Unexpected fetch'); };
function sourceSet(title) {
  return {
    chunks: [{ n: 1, title, authors: ['Autor'], snippet: 'fragment' }],
    verses: [], related: [],
    patristics: [{ author: title + ' P', ref: 'I.1', text_en: 'Patristic text' }],
    ane: [{ text: title + ' A', ref: 'I.2', translation_en: 'ANE text' }],
  };
}
"""


def run_script(page, checks):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed; offline JavaScript checks unavailable")
    probe = subprocess.run(
        [node, "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    if probe.returncode != 0:
        pytest.skip(f"Node cannot start: {probe.stderr.strip()}")
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", page, re.S)
    script = next(s for s in scripts if "const EXAMPLES =" in s)
    result = subprocess.run(
        [node, "-"],
        input=DOM_STUB
        + "\n"
        + script
        + "\n(async () => {\n"
        + checks
        + "\n})().catch(error => { console.error(error); process.exitCode = 1; });",
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_old_answer_citations_and_export_use_matching_sources(interface_page):
    run_script(
        interface_page,
        r"""
const first = addBot(), second = addBot();
const firstSources = sourceSet('Pierwsze źródło');
const secondSources = sourceSet('Drugie źródło');
finishBot(first, { answer: 'Pierwsza [1] [P1] [A1]', citations: [1] }, firstSources);
finishBot(second, { answer: 'Druga [1]', citations: [1] }, secondSources);
selectBot(second);
assert.ok($('sources').innerHTML.includes('Drugie źródło'));
for (const ref of first.querySelectorAll('.ref')) {
  ref.onclick();
  assert.equal(activeBot, first);
  assert.ok($('sources').innerHTML.includes('Pierwsze źródło'));
  assert.ok(!$('sources').innerHTML.includes('Drugie źródło'));
  assert.equal(scrolledSource, 'src-' + (ref.dataset.kind || '') + ref.dataset.n);
}
assert.equal(first.querySelector('.show-sources').attrs['aria-pressed'], 'true');
assert.equal(second.querySelector('.show-sources').attrs['aria-pressed'], 'false');
const requests = [];
fetch = async (url, options) => {
  requests.push({ url, options }); return { ok: true, blob: async () => ({}) };
};
for (const format of ['bib', 'ris']) {
  await exportCitations(format);
  const req = requests.at(-1);
  assert.equal(req.url, '/export/citations');
  assert.deepEqual(JSON.parse(req.options.body), {
    sources: firstSources, answer: 'Pierwsza [1] [P1] [A1]', format,
  });
  assert.equal(req.options.headers['X-CSRFToken'], csrfInput.value);
}
second.querySelector('.show-sources').onclick();
await exportCitations('bib');
assert.deepEqual(JSON.parse(requests.at(-1).options.body).sources, secondSources);
$('clear').onclick();
await exportCitations('bib');
assert.equal(requests.length, 3);
assert.equal(activeBot, null);
assert.equal($('sources').innerHTML, emptySources);
""",
    )


def test_history_selects_last_assistant_even_with_trailing_user(interface_page):
    run_script(
        interface_page,
        r"""
const old = addBot();
finishBot(old, { answer: 'Poprzedni ekran [1]' }, sourceSet('Poprzedni ekran'));
selectBot(old);
const messages = [
  { role: 'user', content: 'Pytanie pierwsze' },
  { role: 'assistant', content: 'Pierwsza [1]', meta: {
    result: { answer: 'Pierwsza [1]', citations: [1] }, sources: sourceSet('Historia 1'),
  } },
  { role: 'user', content: 'Pytanie drugie' },
  { role: 'assistant', content: 'Druga [1]', meta: {
    result: { citations: [1] }, sources: sourceSet('Historia 2'),
  } },
  { role: 'user', content: 'Bez odpowiedzi' },
];
fetch = async () => ({ ok: true, json: async () => ({ id: 7, messages }) });
await openConversation(7);
assert.equal(conversationId, 7);
assert.equal(answerState.get(activeBot).result.answer, 'Druga [1]');
assert.ok($('sources').innerHTML.includes('Historia 2'));
const requests = [];
fetch = async (url, options) => {
  requests.push(JSON.parse(options.body)); return { ok: true, blob: async () => ({}) };
};
await exportCitations('ris');
assert.equal(requests[0].answer, 'Druga [1]');
assert.equal(requests[0].sources.chunks[0].title, 'Historia 2');
const first = $('thread').children[1];
first.querySelectorAll('.ref')[0].onclick();
await exportCitations('bib');
assert.equal(requests[1].answer, 'Pierwsza [1]');
assert.equal(requests[1].sources.chunks[0].title, 'Historia 1');
// A legacy answer without sources must never retain an older answer's panel.
fetch = async () => ({ ok: true, json: async () => ({ id: 8, messages: [
  { role: 'assistant', content: 'Stara odpowiedź' },
] }) });
await openConversation(8);
assert.deepEqual(answerState.get(activeBot).sources, {});
assert.equal($('source-count').textContent, 0);
assert.ok(!$('sources').innerHTML.includes('Historia 1'));
fetch = async () => ({ ok: true, json: async () => ({ id: 9, messages: [] }) });
await openConversation(9);
assert.equal(activeBot, null);
assert.equal($('sources').innerHTML, emptySources);
""",
    )


def test_stream_keeps_answer_source_pair_and_sends_csrf(interface_page):
    run_script(
        interface_page,
        r"""
const requests = [];
const sources = sourceSet('Strumień');
fetch = async (url, options) => {
  requests.push({ url, options });
  if (url === '/export/citations') return { ok: true, blob: async () => ({}) };
  const body = new TextEncoder().encode([
    'event: sources\ndata: ' + JSON.stringify(sources) + '\n\n',
    'event: delta\ndata: ' + JSON.stringify({ text: 'Odpowiedź [1]' }) + '\n\n',
    'event: done\ndata: ' + JSON.stringify({ answer: 'Odpowiedź [1]', citations: [1] }) + '\n\n',
  ].join(''));
  let sent = false;
  return { ok: true, body: { getReader() { return { read: async () => {
    if (sent) return { done: true }; sent = true; return { value: body, done: false };
  } }; } } };
};
$('q').value = 'Pytanie testowe';
await submit();
assert.equal(requests[0].url, '/ask/stream');
assert.equal(requests[0].options.headers['X-CSRFToken'], csrfInput.value);
assert.equal(answerState.get(activeBot).result.answer, 'Odpowiedź [1]');
assert.ok($('sources').innerHTML.includes('Strumień'));
await exportCitations('bib');
assert.deepEqual(JSON.parse(requests[1].options.body), {
  sources, answer: 'Odpowiedź [1]', format: 'bib',
});
// Candidates are not exportable before a matching final answer exists.
const pending = addBot();
answerState.set(pending, { sources, result: null });
selectBot(pending);
assert.ok(!$('sources').innerHTML.includes('exportCitations'));
await exportCitations('bib');
assert.equal(requests.length, 2);
""",
    )


def test_optional_verification_warnings_are_escaped(interface_page):
    run_script(
        interface_page,
        r"""
const bot = addBot();
finishBot(bot, {
  answer: 'Test', quotes_checked: 2, quotes_verified: 1,
  quotes_unverified: [{ quote: '<img src=x>', reason: '<brak zgodności>' }],
  unverified_citations: ['[99]', '[P9]', '[A9]', '<script>'],
});
assert.ok(bot.innerHTML.includes('cytat niepotwierdzony w kontekście'));
assert.ok(bot.innerHTML.includes('&lt;img src=x&gt;'));
assert.ok(bot.innerHTML.includes('&lt;brak zgodności&gt;'));
assert.ok(bot.innerHTML.includes('przypisy spoza kontekstu: [99], [P9], [A9], &lt;script&gt;'));
assert.ok(bot.innerHTML.includes('badge warn">cytaty zgodne z korpusem: 1/2'));
assert.ok(!bot.innerHTML.includes('<img'));
finishBot(bot, { answer: 'Starsza odpowiedź', quotes_checked: 1, quotes_verified: 1 });
assert.ok(!bot.innerHTML.includes('cytat niepotwierdzony'));
assert.ok(!bot.innerHTML.includes('przypisy spoza kontekstu'));
assert.ok(bot.innerHTML.includes('badge ok">cytaty zgodne z korpusem: 1/1'));
finishBot(bot, { answer: 'Test', quotes_unverified: ['Niepotwierdzony tekst'] });
assert.ok(bot.innerHTML.includes('title="Niepotwierdzony tekst"'));
""",
    )


@pytest.mark.parametrize("authenticated", [False, True])
def test_rendered_csrf_token_is_valid_for_json_post(settings, authenticated):
    settings.CSRF_USE_SESSIONS = False
    request = RequestFactory().get("/")
    request.user = SimpleNamespace(is_authenticated=authenticated, is_superuser=True)
    page = render_to_string(
        "rag/index.html",
        {"user": request.user, "examples_json": "[]"},
        request=request,
    )
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page)[1]
    post = RequestFactory().post(
        "/ask/stream",
        data=json.dumps({"question": "Test"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    post.COOKIES[settings.CSRF_COOKIE_NAME] = request.META["CSRF_COOKIE"]
    middleware = CsrfViewMiddleware(lambda req: HttpResponse())
    middleware.process_request(post)
    assert middleware.process_view(post, lambda req: HttpResponse(), (), {}) is None


def test_reference_related_links_target_panel():
    page = render_to_string(
        "corpus/partials/reference.html",
        {"related": [{"ref": "Rdz 1,1", "work": "BG", "text": "Test", "votes": 1}]},
    )
    assert 'hx-target="#panel"' in page
    assert "#results" not in page


def test_personal_only_copy_limits_literature_not_all_context(interface_page):
    assert "Literatura: tylko moje materiały" in interface_page
    assert "Ogranicza tylko literaturę" in interface_page
    for template in ("rag/account.html", "pages/guide.html"):
        page = render_to_string(template)
        assert "Literatura: tylko moje materiały" in page
        assert "Biblia oraz teksty ANE i patrystyczne nadal mogą być kontekstem" in page


def test_canonical_verified_citations_control_all_source_highlights(interface_page):
    run_script(
        interface_page,
        r"""
const bot = addBot();
const sources = sourceSet('Kontekst');
sources.chunks.push({ n: 2, title: 'Literatura 2', authors: [] });
sources.patristics.push({ author: 'Ojciec 2', text_en: 'Original 2' });
sources.ane.push({ text: 'ANE 2', translation_en: 'Original 2' });
const result = {
  answer: 'Lista [1, 2] i zakresy [P1–P2] [A1-A2]',
  citations: [1, 2], verified_citations: ['[2]', '[P2]', '[A2]'],
};
finishBot(bot, result, sources);
selectBot(bot);
const classes = id => new RegExp('<div class="([^"]*)" id="src-' + id + '"').exec($('sources').innerHTML)[1];
for (const id of ['1', 'P1', 'A1']) assert.ok(!classes(id).includes('hit'));
for (const id of ['2', 'P2', 'A2']) assert.ok(classes(id).includes('hit'));
// An explicit empty canonical list is authoritative, not a legacy fallback.
finishBot(bot, { ...result, verified_citations: [] }, sources);
selectBot(bot);
assert.ok(!$('sources').innerHTML.includes('src hit'));
// Older saved answers still highlight expanded P/A markers.
finishBot(bot, { answer: result.answer, citations: [1, 2] }, sources);
selectBot(bot);
for (const id of ['1', '2', 'P1', 'P2', 'A1', 'A2']) assert.ok(classes(id).includes('hit'));
""",
    )


def test_list_and_range_links_expand_using_service_contract(interface_page):
    from dataclasses import asdict

    from rag.quotes import check_citations

    markers = [
        "[1, 2; 3]",
        "[P1–P2]",
        "[A1—A2]",
        "[P1, 2–3]",
        "[P1, A2]",
        "[3-1]",
        "[1-201]",
        "[P1-A2]",
        "[P]",
        "[1, nonsense]",
        "[1, 99]",
    ]
    available = {"[1]", "[2]", "[3]", "[P1]", "[P2]", "[P3]", "[A1]", "[A2]"}
    checks = [asdict(check) for check in check_citations(" ".join(markers), available)]
    run_script(
        interface_page,
        "const checks = "
        + json.dumps(checks)
        + ";\n"
        + r"""
for (const check of checks) {
  const expected = check.reason && check.reason !== 'missing_source' ? [] : check.refs;
  assert.deepEqual(citationRefs(check.marker, checks), expected);
  assert.deepEqual(citationRefs(check.marker), expected);
  const html = linkRefs(check.marker, checks);
  const buttons = [...html.matchAll(/<button[^>]*>(\[[PA]?\d+\])<\/button>/g)].map(m => m[1]);
  assert.deepEqual(buttons, expected);
  if (!expected.length) assert.equal(html, check.marker);
}
assert.ok(linkRefs('<img src=x> [P1–P2]', checks).includes('&lt;img src=x&gt;'));
assert.ok(!linkRefs('<img src=x> [P1–P2]', checks).includes('<img'));
// The reported expansion wins over frontend parsing for new results.
assert.deepEqual(citationRefs('[1, 2]', [{ marker: '[1, 2]', refs: ['[2]'], reason: '' }]), ['[2]']);
const old = addBot(), latest = addBot();
const sources = sourceSet('Starsze źródła');
sources.patristics.push({ author: 'Ojciec 2', text_en: 'Original 2' });
finishBot(old, {
  answer: '[P1–P2]', verified_citations: ['[P1]', '[P2]'],
  citation_checks: checks,
}, sources);
finishBot(latest, { answer: '[1]' }, sourceSet('Nowsze źródła'));
selectBot(latest);
const refs = old.querySelectorAll('.ref');
assert.equal(refs.length, 2);
refs[1].onclick();
assert.equal(activeBot, old);
assert.equal(scrolledSource, 'src-P2');
assert.ok($('sources').innerHTML.includes('Starsze źródła'));
""",
    )


def test_tradition_quotes_still_display_verification_warnings(interface_page):
    assert "nieweryfikowane dosłownie" not in interface_page
    assert "cytaty z przypisem do Ojców / ANE też podlegają kontroli" in interface_page
    run_script(
        interface_page,
        r"""
const bot = addBot();
finishBot(bot, {
  answer: 'Zmyślony przekład [P1]', quotes_tradition: 1,
  quotes_checked: 1, quotes_verified: 0,
  quotes_unverified: [{ quote: 'Zmyślony przekład', reason: 'translation_or_mismatch' }],
});
assert.ok(bot.innerHTML.includes('cytatów z przypisem do Ojców / ANE: 1'));
assert.ok(bot.innerHTML.includes('cytat niepotwierdzony w kontekście'));
assert.ok(bot.innerHTML.includes('przekład lub różnica względem oryginału'));
assert.ok(bot.innerHTML.includes('badge warn">cytaty zgodne z korpusem: 0/1'));
""",
    )


@pytest.mark.parametrize(
    ("status", "label", "can_retry"),
    [
        ("pending", "Oczekuje na indeksowanie", True),
        ("failed", "Błąd indeksowania — można ponowić", True),
        ("indexed", "Indeks aktualny", False),
        ("not_required", "Indeks zewnętrzny niewymagany", False),
        ("unknown", "Stan indeksowania nieznany", False),
    ],
)
@pytest.mark.parametrize("has_error", [False, True])
def test_account_index_status_and_safe_retry_form(status, label, can_retry, has_error):
    request = RequestFactory().get("/account/")
    request.user = SimpleNamespace(is_authenticated=True, is_superuser=True)
    document = SimpleNamespace(
        id=42,
        title="Mój dokument",
        note="<script>private note</script>",
        authors=SimpleNamespace(all=[]),
        access="personal",
        chunk_count=3,
        index_status=status,
        index_error="Internal exception password=TOP_SECRET /srv/private"
        if has_error
        else "",
    )
    page = render_to_string(
        "rag/account.html", {"user": request.user, "docs": [document]}, request=request
    )
    assert label in page
    assert (
        "Szczegóły techniczne są ukryte." in page
        if has_error
        else "Szczegóły techniczne są ukryte." not in page
    )
    assert "Internal exception" not in page
    assert "TOP_SECRET" not in page
    assert "/srv/private" not in page
    assert "&lt;script&gt;private note&lt;/script&gt;" in page
    assert "<script>private note</script>" not in page
    retry_forms = [
        form
        for form in re.findall(r"<form\b[^>]*>.*?</form>", page, re.S)
        if 'name="action" value="retry_index"' in form
    ]
    assert len(retry_forms) == int(can_retry)
    if can_retry:
        form = retry_forms[0]
        assert 'method="post"' in form
        assert 'name="doc_id" value="42"' in form
        assert re.search(r'name="csrfmiddlewaretoken" value="[A-Za-z0-9]{64}"', form)
