"""OpenAI client used by the engine and the chat (Chat Completions API over httpx).

Configure with OPENAI_API_KEY, OPENAI_MODEL and optionally OPENAI_BASE_URL in .env.
"""

import json
import threading

import httpx

from app.config import settings


class LLMError(RuntimeError):
    """A failed model call. `status_code` and `code` are set when OpenAI answered with an error
    (for example 429 / "insufficient_quota"); `reason` is a short plain label for the chat."""

    def __init__(self, message: str, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code

    @property
    def reason(self) -> str:
        if self.status_code == 429 or self.code in ("insufficient_quota", "rate_limit_exceeded"):
            return "quota"
        if self.status_code in (401, 403):
            return "key"
        return "unavailable"


# Tokens the last successful call on this thread used (prompt + answer), as the provider counted them.
last_usage = threading.local()


def is_configured() -> bool:
    return bool(settings.llm_api_key)


def _post(messages: list[dict], *, json_mode: bool, timeout: float, model: str | None = None,
          effort: str | None = None, max_tokens: int | None = None) -> str:
    if not is_configured():
        raise LLMError("OPENAI_API_KEY is not set. Add it to .env or use replay mode.", code="no_key")

    body = {"model": model or settings.llm_model, "messages": messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if effort and ("gpt-oss" in body["model"] or "gemini" in body["model"]):
        body["reasoning_effort"] = effort
    if max_tokens:
        body["max_completion_tokens"] = max_tokens

    try:
        res = httpx.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json=body,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise LLMError(f"Could not reach OpenAI: {exc.__class__.__name__}") from exc

    if res.status_code != 200:
        code = None
        try:
            data = res.json()
            if isinstance(data, list):  # Gemini wraps errors in a list
                data = data[0] if data and isinstance(data[0], dict) else {}
            err = data.get("error") or {}
            code = (err.get("code") or err.get("type") or err.get("status")) if isinstance(err, dict) else None
        except ValueError:
            pass
        raise LLMError(f"The model returned {res.status_code}: {res.text[:600]}", status_code=res.status_code, code=code)

    try:
        data = res.json()
        last_usage.tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
        return data["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMError("OpenAI sent a reply in an unexpected format.") from exc


def chat(system: str, user: str, *, json_mode: bool = False, timeout: float = 90.0, model: str | None = None,
         effort: str | None = None, max_tokens: int | None = None) -> str:
    """Send one system + user message and return the assistant's text."""
    return _post([{"role": "system", "content": system}, {"role": "user", "content": user}],
                 json_mode=json_mode, timeout=timeout, model=model, effort=effort, max_tokens=max_tokens)


def chat_messages(system: str, messages: list[dict], *, timeout: float = 40.0) -> str:
    """Send a system prompt plus a conversation ([{role: user|assistant, content}]) and return the reply."""
    return _post([{"role": "system", "content": system}, *messages], json_mode=False, timeout=timeout,
                 model=settings.llm_fast_model or None)


def chat_json(system: str, user: str, **kwargs) -> dict:
    """Like chat(), but asks for a JSON object and parses it."""
    text = chat(system, user, json_mode=True, **kwargs)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model did not return valid JSON: {text[:200]}") from exc
