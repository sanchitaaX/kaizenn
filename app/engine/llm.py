"""LLM access for the engine, backed by OpenAI through app/llm.py.

Configure with OPENAI_API_KEY (and optionally OPENAI_MODEL, OPENAI_BASE_URL) in .env.
Without a key, or when a call fails, every helper returns None and callers fall back to
non-AI behaviour, so the app always works offline for the demo. The reason for the last
failure is kept in `last_error()` so callers and logs can show a clear message.
"""

from __future__ import annotations

import json
import logging

from app import llm as openai_client

log = logging.getLogger(__name__)

_last_error: str | None = None


def available() -> bool:
    return openai_client.is_configured()


def last_error() -> str | None:
    """Plain-English reason the most recent call returned None (None if it succeeded)."""
    return _last_error


def _json_system(system: str, schema: dict | None) -> str:
    # OpenAI's JSON mode requires the word "JSON" in the prompt, so say it explicitly.
    text = system.rstrip() + "\n\nRespond with a single JSON object and nothing else."
    if schema:
        text += " It must match this JSON schema:\n" + json.dumps(schema)
    return text


def complete_json(system: str, prompt: str, schema: dict | None = None, max_tokens: int = 4000) -> dict | None:
    """Ask the model for a JSON object matching `schema`. Returns None on any failure.

    `max_tokens` is accepted for compatibility with older callers and is not sent.
    """
    global _last_error
    _last_error = None
    if not available():
        _last_error = "OPENAI_API_KEY is not set, so the AI step was skipped"
        return None
    try:
        data = openai_client.chat_json(_json_system(system, schema), prompt)
    except openai_client.LLMError as exc:
        _last_error = f"OpenAI call failed, using the built-in fallback: {' '.join(str(exc).split())[:220]}"
        log.warning(_last_error)
        return None
    except Exception as exc:  # never let an AI hiccup break a scan or onboarding
        _last_error = f"Unexpected AI error, using the built-in fallback: {type(exc).__name__}"
        log.exception("LLM call failed")
        return None
    if not isinstance(data, dict):
        _last_error = "The model did not return a JSON object, using the built-in fallback"
        log.warning(_last_error)
        return None
    return data
