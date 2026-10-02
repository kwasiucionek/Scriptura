"""Jeden klient czatu dla całego projektu.

Backendy (LLM_BACKEND):
  ollama — natywne /api/chat (lokalnie lub Ollama Cloud z OLLAMA_API_KEY);
  openai — każdy serwer zgodny z API OpenAI /chat/completions: NVIDIA NIM
           (https://integrate.api.nvidia.com/v1), vLLM, OpenAI, Groq…;
  echo   — bez modelu (testy, demo offline): strumień zastępczy, chat() rzuca.

Wszystkie miejsca w projekcie (odpowiedź RAG, tłumaczenie zapytania, kurator,
reranker LLM, eval_bootstrap) wołają tylko `chat()` / `chat_stream()`; różnice
formatu (Ollama `options`/`think` vs OpenAI `max_tokens`/`chat_template_kwargs`)
zostają tutaj. Bez zależności — urllib, jak reszta projektu.

Tryb „thinking”: Ollama ma pole `think`; serwery vLLM/NIM sterują nim przez
`chat_template_kwargs.enable_thinking` (włączane ustawieniem OPENAI_THINKING_KWARGS,
bo zwykłe OpenAI odrzuca nieznane pola) i opcjonalnym `reasoning_budget`.
Treść rozumowania (Ollama `message.thinking`, OpenAI `delta.reasoning_content`
albo inline `<think>…</think>`) jest oddzielana od odpowiedzi i nie trafia do GUI.

Ollama odrzuca `think` (HTTP 400 „does not support thinking”) dla modeli bez
tej możliwości, np. PLLuM czy Bielik. Dlatego możliwości modelu są odczytywane
z /api/show (pole `capabilities`, wynik w pamięci podręcznej), a `think` jest
wysyłane tylko do modeli z „thinking”. Gdy /api/show nie odpowie (starsza Ollama),
zapytanie z `think` i tak zostanie ponowione bez niego po takim błędzie 400.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass

from django.conf import settings

log = logging.getLogger(__name__)

BACKENDS = ("ollama", "openai", "echo")
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)


@dataclass
class Delta:
    """Jeden fragment strumienia. Ostatni ma done=True i liczniki tokenów (0, gdy serwer ich nie podał)."""

    content: str = ""
    reasoning: str = ""
    done: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMHTTPError(RuntimeError):
    """Błąd HTTP serwera modelu wraz z treścią odpowiedzi (tam jest faktyczny powód)."""

    def __init__(self, backend: str, code: int, detail: str):
        self.backend = backend
        self.code = code
        self.detail = detail
        super().__init__(f"{backend} HTTP {code}: {detail}")


def enabled() -> bool:
    """Czy jest prawdziwy model (nie echo)."""
    return settings.LLM_BACKEND in ("ollama", "openai")


def model_name(model: str | None = None) -> str:
    """Nazwa modelu do metadanych odpowiedzi; `model` nadpisuje domyślny backendu."""
    if model:
        return model
    if settings.LLM_BACKEND == "ollama":
        return settings.OLLAMA_CHAT_MODEL
    if settings.LLM_BACKEND == "openai":
        return settings.OPENAI_CHAT_MODEL
    return "echo"


def strip_think(text: str) -> str:
    """Usuwa rozumowanie wpisane w treść (modele bez osobnego kanału reasoning)."""
    return _THINK_RE.sub("", text or "").strip()


def _http_error_detail(err: urllib.error.HTTPError) -> str:
    """Treść odpowiedzi błędu; dla JSON-a z polem `error` zwraca sam komunikat."""
    try:
        raw = err.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — strumień mógł być już zamknięty
        return err.reason or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()[:500]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):  # format OpenAI: {"error": {"message": ...}}
            return str(error.get("message") or error)
        if error:
            return str(error)
    return raw.strip()[:500]


# --- API publiczne ----------------------------------------------------------


def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    num_ctx: int | None = None,
    json_mode: bool = False,
    think: bool = False,
    timeout: int = 180,
) -> str:
    """Jedna odpowiedź (bez strumienia). Zwraca treść bez rozumowania."""
    if settings.LLM_BACKEND == "ollama":
        return _ollama_chat(
            messages, model, temperature, max_tokens, num_ctx, json_mode, think, timeout
        )
    if settings.LLM_BACKEND == "openai":
        return _openai_chat(
            messages, model, temperature, max_tokens, json_mode, think, timeout
        )
    raise RuntimeError("LLM_BACKEND=echo — brak modelu do wywołania")


def chat_stream(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    num_ctx: int | None = None,
    max_tokens: int | None = None,
    think: bool = False,
    timeout: int = 600,
) -> Iterator[Delta]:
    """Treść i rozumowanie osobno; done oznacza sukces, błędy OpenAI SSE rzucają LLMStreamError."""
    if settings.LLM_BACKEND == "ollama":
        return _ollama_stream(
            messages, model, temperature, max_tokens, num_ctx, think, timeout
        )
    if settings.LLM_BACKEND == "openai":
        return _openai_stream(messages, model, temperature, max_tokens, think, timeout)
    return _echo_stream(messages)


# --- Ollama ----------------------------------------------------------------


def _ollama_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if settings.OLLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {settings.OLLAMA_API_KEY}"
    return headers


@functools.lru_cache(maxsize=32)
def _ollama_capabilities(model: str) -> frozenset[str] | None:
    """Możliwości modelu z /api/show (completion, thinking, tools, vision…).

    None = nie udało się ustalić (starsza Ollama, brak połączenia); wtedy nic nie
    zakładamy, a ewentualny błąd „does not support thinking” obsłuży ponowienie.
    Wynik jest w pamięci podręcznej do restartu procesu — po `ollama create`
    z tą samą nazwą i innymi możliwościami trzeba zrestartować aplikację.
    """
    req = urllib.request.Request(
        f"{settings.OLLAMA_BASE_URL}/api/show",
        data=json.dumps({"model": model}).encode(),
        headers=_ollama_headers(),
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        log.warning("Ollama /api/show dla %s nie odpowiedziało: %s", model, exc)
        return None
    caps = data.get("capabilities")
    if not isinstance(caps, list):
        return None
    return frozenset(caps)


def _ollama_supports_thinking(model: str) -> bool | None:
    caps = _ollama_capabilities(model)
    if caps is None:
        return None
    return "thinking" in caps


def _ollama_request(body: dict, timeout: int):
    def send(payload: dict):
        req = urllib.request.Request(
            f"{settings.OLLAMA_BASE_URL}/api/chat",
            data=json.dumps(payload).encode(),
            headers=_ollama_headers(),
        )
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310

    try:
        return send(body)
    except urllib.error.HTTPError as err:
        detail = _http_error_detail(err)
        # zabezpieczenie, gdy /api/show nie dało odpowiedzi: ponów bez `think`
        if err.code == 400 and "think" in body and "support thinking" in detail:
            log.warning(
                "Model %s nie obsługuje think — ponawiam bez niego", body.get("model")
            )
            retry = {k: v for k, v in body.items() if k != "think"}
            try:
                return send(retry)
            except urllib.error.HTTPError as err2:
                raise LLMHTTPError(
                    "Ollama", err2.code, _http_error_detail(err2)
                ) from err2
        raise LLMHTTPError("Ollama", err.code, detail) from err


def _ollama_body(
    messages, model, temperature, max_tokens, num_ctx, think, stream
) -> dict:
    model = model or settings.OLLAMA_CHAT_MODEL
    options: dict = {"temperature": temperature}
    if num_ctx:
        options["num_ctx"] = num_ctx
    if max_tokens:
        options["num_predict"] = max_tokens
    body: dict = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "options": options,
    }
    supports = _ollama_supports_thinking(model)
    if supports is None:
        # możliwości nieznane — zachowanie jak dotąd; błąd 400 obsłuży ponowienie
        body["think"] = think
    elif supports:
        body["think"] = think
    elif think:
        log.info("Model %s nie obsługuje think — pole pominięte", model)
    return body


def _ollama_chat(
    messages, model, temperature, max_tokens, num_ctx, json_mode, think, timeout
) -> str:
    body = _ollama_body(messages, model, temperature, max_tokens, num_ctx, think, False)
    if json_mode:
        body["format"] = "json"
    with _ollama_request(body, timeout) as resp:
        return strip_think(json.load(resp)["message"].get("content", "") or "")


def _ollama_stream(
    messages, model, temperature, max_tokens, num_ctx, think, timeout
) -> Iterator[Delta]:
    body = _ollama_body(messages, model, temperature, max_tokens, num_ctx, think, True)
    with _ollama_request(body, timeout) as resp:
        for line in resp:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("error"):
                # błąd w trakcie strumienia (np. brak pamięci GPU)
                raise LLMHTTPError("Ollama", 500, str(rec["error"]))
            if rec.get("done"):
                yield Delta(
                    done=True,
                    prompt_tokens=rec.get("prompt_eval_count", 0),
                    completion_tokens=rec.get("eval_count", 0),
                )
                return
            msg = rec.get("message", {})
            yield Delta(
                content=msg.get("content", "") or "",
                reasoning=msg.get("thinking", "") or "",
            )
    yield Delta(done=True)


# --- OpenAI-compatible (NVIDIA NIM, vLLM, OpenAI…) ---------------------------


def _openai_request(body: dict, timeout: int):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if settings.OPENAI_API_KEY:
        headers["Authorization"] = f"Bearer {settings.OPENAI_API_KEY}"
    if body.get("stream"):
        headers["Accept"] = "text/event-stream"
    req = urllib.request.Request(
        f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
    except urllib.error.HTTPError as err:
        raise LLMHTTPError(
            "OpenAI-compatible", err.code, _http_error_detail(err)
        ) from err


def _openai_body(messages, model, temperature, max_tokens, think, stream) -> dict:
    body: dict = {
        "model": model or settings.OPENAI_CHAT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    if stream:
        body["stream_options"] = {"include_usage": True}
    if settings.OPENAI_THINKING_KWARGS:
        body["chat_template_kwargs"] = {"enable_thinking": bool(think)}
        if think and settings.LLM_REASONING_BUDGET > 0:
            body["reasoning_budget"] = settings.LLM_REASONING_BUDGET
    if settings.OPENAI_EXTRA_BODY:
        body.update(settings.OPENAI_EXTRA_BODY)
    return body


def _openai_chat(
    messages, model, temperature, max_tokens, json_mode, think, timeout
) -> str:
    body = _openai_body(messages, model, temperature, max_tokens, think, False)
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    with _openai_request(body, timeout) as resp:
        data = json.load(resp)
    choice = (data.get("choices") or [{}])[0]
    return strip_think(choice.get("message", {}).get("content", "") or "")


class LLMStreamError(RuntimeError):
    """A provider reported an error or did not complete its SSE stream."""


class _ThinkParser:
    """Hold only a possible tag prefix so tags may cross arbitrary delta boundaries."""

    def __init__(self):
        self.pending = ""
        self.in_think = False

    def feed(self, text: str, *, final: bool = False) -> tuple[str, str]:
        self.pending += text
        content, reasoning = [], []
        while self.pending:
            tag = "</think>" if self.in_think else "<think>"
            pos = self.pending.find(tag)
            target = reasoning if self.in_think else content
            if pos >= 0:
                target.append(self.pending[:pos])
                self.pending = self.pending[pos + len(tag) :]
                self.in_think = not self.in_think
                continue
            keep = 0
            if not final:
                for n in range(1, min(len(tag), len(self.pending) + 1)):
                    if self.pending.endswith(tag[:n]):
                        keep = n
            if keep:
                target.append(self.pending[:-keep])
                self.pending = self.pending[-keep:]
            else:
                target.append(self.pending)
                self.pending = ""
            break
        return "".join(content), "".join(reasoning)


def _sse_payloads(resp):
    data, event = [], ""
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield event, "\n".join(data)
            data, event = [], ""
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif line.startswith("event:"):
            event = line[6:].strip()
    if data:
        yield event, "\n".join(data)


def _openai_stream(
    messages, model, temperature, max_tokens, think, timeout
) -> Iterator[Delta]:
    """SSE: linie `data: {...}`, koniec `data: [DONE]`; usage w ostatnim rekordzie (bez choices).

    Bramka AI MLflow nie wysyła `[DONE]` — kończy strumień po rekordzie z `finish_reason`
    (i opcjonalnie usage). Taki koniec też jest kompletny; zerwane połączenie w trakcie
    generowania nie ma `finish_reason`, więc nadal jest błędem.
    """
    body = _openai_body(messages, model, temperature, max_tokens, think, True)
    prompt_tokens = completion_tokens = 0
    parser = _ThinkParser()
    completed = finished = False
    with _openai_request(body, timeout) as resp:
        for event, payload in _sse_payloads(resp):
            if payload.strip() == "[DONE]" and event != "error":
                completed = True
                break
            try:
                rec = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise LLMStreamError("OpenAI-compatible: invalid SSE data") from exc
            if not isinstance(rec, dict):
                raise LLMStreamError("OpenAI-compatible: invalid SSE record")
            if event == "error" or rec.get("error") is not None:
                error = rec.get("error", rec)
                detail = (
                    error.get("message", error) if isinstance(error, dict) else error
                )
                raise LLMStreamError(f"OpenAI-compatible stream error: {detail}")
            usage = rec.get("usage")
            if usage:
                prompt_tokens = usage.get("prompt_tokens", 0) or 0
                completion_tokens = usage.get("completion_tokens", 0) or 0
            choices = rec.get("choices") or []
            if not choices:
                continue
            finished = finished or bool(choices[0].get("finish_reason"))
            delta = choices[0].get("delta") or {}
            reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
            content, inline_reasoning = parser.feed(delta.get("content") or "")
            reasoning += inline_reasoning
            if content or reasoning:
                yield Delta(content=content, reasoning=reasoning)
    if not (completed or finished):
        raise LLMStreamError("OpenAI-compatible: premature EOF before [DONE]")
    if parser.in_think:
        raise LLMStreamError("OpenAI-compatible: unclosed <think> block")
    content, reasoning = parser.feed("", final=True)
    if content or reasoning:
        yield Delta(content=content, reasoning=reasoning)
    yield Delta(
        done=True, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )


# --- echo -------------------------------------------------------------------


def _echo_stream(messages: list[dict]) -> Iterator[Delta]:
    """Backend zastępczy bez LLM: streszcza, co zostało znalezione (testy, demo bez modelu)."""
    user = messages[-1]["content"]
    n_src = len(re.findall(r"^\[\d+\] ", user, re.M))
    text = (
        f"[tryb echo — bez modelu] Znaleziono {n_src} fragmentów literatury"
        + (" [1]" if n_src else "")
        + " oraz wersety z korpusu. Skonfiguruj LLM_BACKEND=ollama lub openai, aby uzyskać odpowiedź."
    )
    for word in text.split(" "):
        yield Delta(content=word + " ")
    yield Delta(done=True)
