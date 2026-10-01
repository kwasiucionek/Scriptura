"""Wspólny klient LLM: kształt żądań dla ollama/openai, parsowanie SSE, rozdzielenie rozumowania."""

import io
import json

import pytest

from library import llm


class _Resp(io.BytesIO):
    """Udaje odpowiedź urlopen: iterowalna po liniach, kontekst-menedżer."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(monkeypatch, payload: bytes):
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.header_items())
        seen["body"] = json.loads(req.data)
        seen["timeout"] = timeout
        return _Resp(payload)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_openai_chat_request_and_think_strip(settings, monkeypatch):
    settings.LLM_BACKEND = "openai"
    settings.OPENAI_BASE_URL = "https://integrate.api.nvidia.com/v1/"
    settings.OPENAI_API_KEY = "nvapi-x"
    settings.OPENAI_CHAT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
    settings.OPENAI_THINKING_KWARGS = True
    settings.LLM_REASONING_BUDGET = 4096
    settings.OPENAI_EXTRA_BODY = {"top_p": 0.95}
    seen = _capture(
        monkeypatch,
        json.dumps(
            {"choices": [{"message": {"content": "<think>hmm</think>Odpowiedź."}}]}
        ).encode(),
    )
    out = llm.chat(
        [{"role": "user", "content": "q"}], max_tokens=50, json_mode=True, think=False
    )
    assert out == "Odpowiedź."
    assert seen["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer nvapi-x"
    b = seen["body"]
    assert b["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert b["max_tokens"] == 50 and b["stream"] is False
    assert b["response_format"] == {"type": "json_object"}
    assert b["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_budget" not in b  # tylko przy think=True
    assert b["top_p"] == 0.95
    assert llm.model_name() == "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_openai_stream_parses_sse_reasoning_and_usage(settings, monkeypatch):
    settings.LLM_BACKEND = "openai"
    settings.OPENAI_THINKING_KWARGS = True
    settings.LLM_REASONING_BUDGET = 1000
    settings.OPENAI_EXTRA_BODY = {}
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "myślę"}}]},
        {"choices": [{"delta": {"content": "Ala "}}]},
        {"choices": [{"delta": {"content": "ma kota."}}]},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5}},
    ]
    sse = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)
    sse += b"data: [DONE]\n\n"
    seen = _capture(monkeypatch, sse)
    deltas = list(llm.chat_stream([{"role": "user", "content": "q"}], think=True))
    assert "".join(d.content for d in deltas) == "Ala ma kota."
    assert "".join(d.reasoning for d in deltas) == "myślę"
    assert deltas[-1].done and deltas[-1].prompt_tokens == 12
    assert deltas[-1].completion_tokens == 5
    b = seen["body"]
    assert b["stream"] is True and b["stream_options"] == {"include_usage": True}
    assert b["chat_template_kwargs"] == {"enable_thinking": True}
    assert b["reasoning_budget"] == 1000
    assert seen["headers"]["Accept"] == "text/event-stream"


def test_openai_stream_inline_think_tags_go_to_reasoning(settings, monkeypatch):
    settings.LLM_BACKEND = "openai"
    settings.OPENAI_THINKING_KWARGS = False
    settings.OPENAI_EXTRA_BODY = {}
    chunks = [
        {"choices": [{"delta": {"content": "<think>a"}}]},
        {"choices": [{"delta": {"content": "b</think>Wynik"}}]},
    ]
    sse = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)
    sse += b"data: [DONE]\n\n"
    _capture(monkeypatch, sse)
    deltas = list(llm.chat_stream([{"role": "user", "content": "q"}]))
    assert "".join(d.content for d in deltas) == "Wynik"
    assert "".join(d.reasoning for d in deltas) == "ab"


def test_ollama_request_shape(settings, monkeypatch):
    settings.LLM_BACKEND = "ollama"
    settings.OLLAMA_BASE_URL = "http://ollama:11434"
    settings.OLLAMA_API_KEY = ""
    settings.OLLAMA_CHAT_MODEL = "gemma4:31b-cloud"
    seen = _capture(monkeypatch, json.dumps({"message": {"content": "ok"}}).encode())
    assert (
        llm.chat(
            [{"role": "user", "content": "q"}],
            model="inny",
            num_ctx=8192,
            max_tokens=10,
            json_mode=True,
        )
        == "ok"
    )
    assert seen["url"] == "http://ollama:11434/api/chat"
    assert "Authorization" not in seen["headers"]
    b = seen["body"]
    assert b["model"] == "inny" and b["format"] == "json" and b["think"] is False
    assert b["options"] == {"temperature": 0.0, "num_ctx": 8192, "num_predict": 10}


def test_ollama_stream_deltas(settings, monkeypatch):
    settings.LLM_BACKEND = "ollama"
    recs = [
        {"message": {"content": "", "thinking": "hm"}, "done": False},
        {"message": {"content": "Tak."}, "done": False},
        {"done": True, "prompt_eval_count": 7, "eval_count": 2},
    ]
    _capture(monkeypatch, b"".join(json.dumps(r).encode() + b"\n" for r in recs))
    deltas = list(llm.chat_stream([{"role": "user", "content": "q"}]))
    assert [d.content for d in deltas[:-1]] == ["", "Tak."]
    assert deltas[0].reasoning == "hm"
    assert deltas[-1].done and (
        deltas[-1].prompt_tokens,
        deltas[-1].completion_tokens,
    ) == (7, 2)


def test_echo_backend(settings):
    settings.LLM_BACKEND = "echo"
    assert not llm.enabled() and llm.model_name() == "echo"
    deltas = list(llm.chat_stream([{"role": "user", "content": "[1] x\n[2] y"}]))
    assert deltas[-1].done and "2 fragmentów" in "".join(d.content for d in deltas)
    with pytest.raises(RuntimeError):
        llm.chat([{"role": "user", "content": "q"}])


def _openai_sse(monkeypatch, settings, fragments, ending=b"data: [DONE]\n\n"):
    settings.LLM_BACKEND = "openai"
    settings.OPENAI_EXTRA_BODY = {}
    records = [{"choices": [{"delta": {"content": text}}]} for text in fragments]
    payload = (
        b"".join(b"data: " + json.dumps(r).encode() + b"\n\n" for r in records) + ending
    )
    _capture(monkeypatch, payload)
    return llm.chat_stream([{"role": "user", "content": "q"}])


def test_inline_think_same_chunk_and_multiple_blocks(monkeypatch, settings):
    deltas = list(
        _openai_sse(
            monkeypatch,
            settings,
            ["Przed<think>tajne</think>Po<think>drugie</think>Koniec"],
        )
    )
    assert "".join(d.content for d in deltas) == "PrzedPoKoniec"
    assert "".join(d.reasoning for d in deltas) == "tajnedrugie"
    assert sum(d.done for d in deltas) == 1


@pytest.mark.parametrize("cut", range(1, len("Before<think>secret</think>After")))
def test_inline_think_at_every_split_boundary(monkeypatch, settings, cut):
    text = "Before<think>secret</think>After"
    deltas = list(_openai_sse(monkeypatch, settings, [text[:cut], text[cut:]]))
    assert "".join(d.content for d in deltas) == "BeforeAfter"
    assert "".join(d.reasoning for d in deltas) == "secret"
    assert deltas[-1].done


def test_inline_think_one_character_deltas_and_literal_prefix(monkeypatch, settings):
    text = "Before<think>secret</think>After <thiX and <"
    deltas = list(_openai_sse(monkeypatch, settings, list(text)))
    assert "".join(d.content for d in deltas) == "BeforeAfter <thiX and <"
    assert "".join(d.reasoning for d in deltas) == "secret"


@pytest.mark.parametrize(
    "ending",
    [
        b'data: {"error": {"message": "GPU failed"}}\n\ndata: [DONE]\n\n',
        b'event: error\ndata: {"message": "GPU failed"}\n\n',
        b'data: {"error": "GPU failed"}\n\n',
        b"",
        b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n',
        b"data: broken-json\n\n",
    ],
)
def test_openai_stream_error_or_eof_never_yields_done(monkeypatch, settings, ending):
    stream = _openai_sse(monkeypatch, settings, ["Partial"], ending)
    assert next(stream).content == "Partial"
    with pytest.raises(llm.LLMStreamError):
        list(stream)


def test_unclosed_think_is_not_success(monkeypatch, settings):
    stream = _openai_sse(monkeypatch, settings, ["<think>secret"])
    assert next(stream).reasoning == "secret"
    with pytest.raises(llm.LLMStreamError, match="unclosed"):
        list(stream)


def test_sse_multiline_payload_comments_crlf_and_final_done(monkeypatch, settings):
    settings.LLM_BACKEND = "openai"
    settings.OPENAI_EXTRA_BODY = {}
    _capture(
        monkeypatch,
        b': ping\r\nevent: message\r\ndata: {"choices":\r\ndata: [{"delta": {"content": "ok"}}]}\r\n\r\ndata: [DONE]',
    )
    deltas = list(llm.chat_stream([{"role": "user", "content": "q"}]))
    assert "".join(d.content for d in deltas) == "ok"
    assert deltas[-1].done
