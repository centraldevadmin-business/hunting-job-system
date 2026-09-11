"""
Vector store — index authentic bullets for semantic retrieval vs. a JD.

Bullets are the ONLY allowed source material for a resume. We embed them with
Gemini embeddings (free tier) and retrieve the top-k most relevant bullets for
a given job description.

Embeddings are cached to disk (keyed by content hash) so we never re-embed the
same bullet twice — this keeps API usage minimal and cost at $0.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from src.db.repository import _project_root


class VectorStore:
    """
    A minimal embedding-backed store.

    Uses Gemini embeddings via the OpenAI-compatible client (free tier).
    Embeddings are cached in data/cache/embeddings.npz keyed by content hash.
    """

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings
        self._client = None
        self._cache_dir = (
            _project_root() / "data" / "cache"
            if settings is None
            else _project_root() / settings.get("paths", {}).get("cache", "data/cache")
        )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path = self._cache_dir / "embeddings.npz"

    def _get_client(self):
        """Lazily build the OpenAI-compatible client (Gemini embeddings)."""
        if self._client is None:
            import os
            from openai import OpenAI

            api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("OPENAI_API_KEY")
            # Gemini embeddings are served over an OpenAI-compatible endpoint.
            # Override OPENAI_BASE_URL / GOOGLE_API_KEY in .env to point at it.
            base_url = os.environ.get(
                "OPENAI_BASE_URL",
                "https://openai.google.com/v1",  # Gemini OpenAI-compatible base
            )
            self._client = OpenAI(api_key=api_key, base_url=base_url)
        return self._client

    def embed(self, text: str) -> list[float]:
        """
        Embed a single text string, using the disk cache when possible.

        Uses Gemini embeddings (free tier) when an API key is configured.
        Falls back to a deterministic hashing embedding (no API, no cost)
        so the system works fully offline. The fallback is stable: the same
        text always maps to the same vector, so retrieval still ranks
        similar content together.
        """
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = self._load_cache()
        if key in cached:
            return cached[key]

        emb = self._embed_with_key(text)
        if emb is None:
            emb = self._hash_embedding(text)

        cached[key] = emb
        self._save_cache(cached)
        return emb

    def _embed_with_key(self, text: str) -> Optional[list[float]]:
        """Try Gemini embeddings; return None if no key is available.

        Embeddings are only served over the native Gemini REST endpoint
        (the OpenAI-compatible endpoint returns 404 for embedContent), so we
        delegate to the shared gemini_client helper.
        """
        try:
            from src.utils.gemini_client import embed
            return embed(text)
        except Exception:
            return None

    @staticmethod
    def _hash_embedding(text: str, dim: int = 256) -> list[float]:
        """
        Deterministic hashing embedding (no API, no cost).

        Tokens are hashed into a fixed-dimentional vector. Similar texts
        share tokens and therefore land closer together in cosine space.
        This is a stand-in for real embeddings — it never calls a paid API.
        """
        vec = [0.0] * dim
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        for tok in tokens:
            h = int(hashlib.sha256(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            sign = 1.0 if (h // dim) % 2 == 0 else -1.0
            vec[idx] += sign
        norm = sum(x * x for x in vec) ** 0.5
        if norm == 0:
            return [0.0] * dim
        return [x / norm for x in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def _load_cache(self) -> dict[str, list[float]]:
        if not self._cache_path.exists():
            return {}
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
            return {k: [float(x) for x in v] for k, v in raw.items()}
        except (json.JSONDecodeError, ValueError):
            return {}

    def _save_cache(self, cache: dict[str, list[float]]) -> None:
        serializable = {k: [float(x) for x in v] for k, v in cache.items()}
        self._cache_path.write_text(json.dumps(serializable), encoding="utf-8")
