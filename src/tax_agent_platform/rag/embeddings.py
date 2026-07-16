"""RAG embeddings — turn text into dense vectors for semantic retrieval.

Two embedders are provided:

* :class:`OllamaEmbedder` — calls a local Ollama server's ``/api/embed``
  endpoint (no external API key, no cloud round-trip). This is the default
  in this project because the demo machine already runs Ollama with
  ``nomic-embed-text`` installed.
* :class:`HashEmbedder` — a dependency-free, deterministic lexical fallback
  (hashing trick + L2 normalization). It is NOT semantically rich, but it
  always works offline and keeps tests reproducible when Ollama is down.

:func:`get_default_embedder` wires the two together: it tries Ollama first and
transparently degrades to :class:`HashEmbedder` if the server is unreachable,
so the app never crashes on a missing embedding backend.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import urllib.error
import urllib.request
from typing import List

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434/api/embed"
DEFAULT_EMBED_MODEL = "nomic-embed-text"


class Embedder:
    """Base interface: map a list of texts to a list of equal-length vectors."""

    dim: int = 0

    def embed(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError


class HashEmbedder(Embedder):
    """Deterministic, offline lexical embedder (hashing trick).

    Splits text into Latin tokens + CJK characters, hashes each into a fixed
    dimensional vector, sums counts, then L2-normalizes. Two texts sharing
    many tokens get a high cosine similarity — good enough for a fallback and
    fully reproducible (no model, no network).
    """

    def __init__(self, dim: int = 256, seed: int = 0) -> None:
        self.dim = dim
        self.seed = seed

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        tokens = re.findall(r"[a-z0-9]+|[一-鿿]", text.lower())
        for tok in tokens:
            h = int(hashlib.md5(f"{self.seed}:{tok}".encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class OllamaEmbedder(Embedder):
    """Embed via a local Ollama ``/api/embed`` endpoint.

    The server is probed lazily on the first ``embed`` call. If it is
    unreachable (or returns a bad payload), the embedder records that failure
    once and permanently delegates to :attr:`fallback` for the rest of the
    process — so we never mix two vector spaces and never pay the network
    timeout on every call after the first miss.
    """

    def __init__(
        self,
        model: str = DEFAULT_EMBED_MODEL,
        url: str = DEFAULT_OLLAMA_URL,
        fallback: Embedder | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.model = model
        self.url = url
        self.timeout = timeout
        self._fallback = fallback or HashEmbedder()
        self.dim = 0
        self._mode: str | None = None  # "ollama" | "fallback"

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if self._mode is None:
            try:
                vectors = self._call_ollama(texts)
                self._mode = "ollama"
                if not self.dim:
                    self.dim = len(vectors[0])
                return vectors
            except Exception as exc:  # noqa: BLE001 — server down / bad payload
                logger.warning(
                    "Ollama embed (%s) unavailable, using fallback embedder: %s",
                    self.model, exc,
                )
                self._mode = "fallback"
                self.dim = self._fallback.dim
                return self._fallback.embed(texts)
        if self._mode == "ollama":
            return self._call_ollama(texts)
        return self._fallback.embed(texts)

    def _call_ollama(self, texts: List[str]) -> List[List[float]]:
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        vectors = data.get("embeddings")
        if not vectors:
            raise ValueError(f"Ollama returned no embeddings: {data!r}")
        return [[float(x) for x in v] for v in vectors]


def get_default_embedder() -> Embedder:
    """Build the production embedder: Ollama (nomic-embed-text) with hash fallback.

    Model / URL come from settings (``TAX_EMBEDDING_MODEL`` / ``TAX_EMBEDDING_URL``)
    so they can be overridden without code changes, e.g. to switch to
    ``snowflake-arctic-embed2`` for higher recall quality.
    """
    from tax_agent_platform.config import settings

    return OllamaEmbedder(
        model=settings.embedding_model,
        url=settings.embedding_url,
        timeout=settings.embedding_timeout,
    )


def cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors (assumes non-negative magnitude)."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    # When both vectors are L2-normalized (our chunks + queries are), the
    # denominator is 1.0; guard it anyway for safety.
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
