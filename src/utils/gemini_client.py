"""
Gemini client — thin wrapper over the single Gemini API key.

The whole system runs on ONE key (``GOOGLE_API_KEY``). It powers two things:

  * chat completions — JD distillation + resume bullet rephrasing.
    Served over the OpenAI-compatible endpoint (model ``gemini-flash-latest``).
  * embeddings — semantic retrieval of authentic resume bullets.
    Served over the native Gemini API (model ``gemini-embedding-001``),
    because the OpenAI-compatible endpoint does not expose embeddings.

Both are free tier. Rate limits are generous for chat (~5k req/min) but tight
for embeddings (~15 req/min), so embeddings are disk-cached in the vector store
and repeated calls for the same text never hit the API.

Every function degrades gracefully: on a missing key or any network error it
returns ``None`` / ``False`` and the caller falls back to its deterministic
regex / hash path. The system never crashes when the key is unavailable.
"""
from __future__ import annotations

import os
import json
import time
import urllib.request
from typing import Optional


# Verified-working model names for new-key users (grok-2 / gemini-2.0-flash are
# deprecated and return 404).
GEMINI_CHAT_MODEL = "gemini-flash-latest"
GEMINI_EMBED_MODEL = "gemini-embedding-001"

# OpenAI-compatible chat endpoint.
GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
# Native REST endpoint (needed for embeddings, which the OpenAI endpoint lacks).
GEMINI_NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta/models/"


def _api_key() -> Optional[str]:
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("OPENAI_API_KEY")


def chat_complete(prompt: str, system: str = "", temperature: float = 0.0,
                  max_tokens: int = 600, retries: int = 3) -> Optional[str]:
    """
    One-shot chat completion with transient-failure backoff.

    Gemini's free tier returns 503 "high demand" spikes and occasionally
    empty content. We retry up to `retries` times with exponential backoff
    and return None only if every attempt fails.
    """
    key = _api_key()
    if not key:
        return None
    last_err = None
    for attempt in range(retries):
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url=GEMINI_OPENAI_BASE)
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = client.chat.completions.create(
                model=GEMINI_CHAT_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = (resp.choices[0].message.content or "").strip()
            if content:
                return content
            last_err = "empty response"
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(min(2 ** attempt, 8))
    if last_err:
        return None
    return None


def embed(text: str) -> Optional[list[float]]:
    """
    Embed a single string via the native Gemini API. Returns None on any
    failure. The caller is responsible for disk-caching the result.
    """
    key = _api_key()
    if not key:
        return None
    try:
        url = f"{GEMINI_NATIVE_BASE}{GEMINI_EMBED_MODEL}:embedContent?key={key}"
        body = json.dumps({"content": {"parts": [{"text": text}]}}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
        )
        resp = json.load(urllib.request.urlopen(req, timeout=30))
        return resp["embedding"]["values"]
    except Exception:
        return None
