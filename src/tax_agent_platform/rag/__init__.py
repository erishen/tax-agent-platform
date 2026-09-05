"""RAG module — chunking, vector retrieval, re-ranking, and timeliness.

Demonstrates:
1. Semantic chunking with overlap (vs fixed-length)
2. Two-stage retrieval: embedding coarse recall + Cross-Encoder re-ranking
3. Metadata filtering for document timeliness (effective_date / expiry_date)
"""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from tax_agent_platform.rag.embeddings import Embedder, cosine, get_default_embedder

logger = logging.getLogger(__name__)


def _l2_normalize(v: list[float]) -> list[float]:
    """L2-normalize a vector so cosine similarity equals a dot product."""
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0.0:
        return v
    return [x / norm for x in v]


# ----  Language-aware tokenization ----


def tokenize(text: str) -> set[str]:
    """Tokenize text for keyword-overlap scoring, supporting CJK + Latin.

    Chinese text has no whitespace, so ``str.split()`` collapses a whole
    sentence into a single token and never matches. Here we:
      - extract Latin/numeric words (lowercased), and
      - for each run of CJK characters, emit both unigrams and bigrams
        (a lightweight substitute for a real tokenizer / embedding).

    This lets a query like "软件增值税税率" match a chunk containing
    "增值税" / "税率" without pulling in a heavyweight dependency.
    """
    text = text.lower()
    tokens: set[str] = set()
    # Latin words and numbers (e.g. "vat", "6", "2024")
    for word in re.findall(r"[a-z0-9]+", text):
        tokens.add(word)
    # CJK runs → unigrams + bigrams
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for ch in run:
            tokens.add(ch)
        for i in range(len(run) - 1):
            tokens.add(run[i : i + 2])
    return tokens


# ----  Chunking Strategies ----


@dataclass
class Chunk:
    """A text chunk with metadata for retrieval."""

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    index: int = 0


def chunk_by_fixed_length(
    text: str,
    chunk_size: int = 512,
    overlap: int = 64,
) -> list[Chunk]:
    """Fixed-length chunking with overlap.

    Simple but can split mid-sentence. Overlap (10-20%) prevents
    information loss at chunk boundaries.
    """
    chunks = []
    start = 0
    idx = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(Chunk(content=text[start:end], index=idx))
        start += chunk_size - overlap
        idx += 1
    return chunks


def chunk_by_semantic(
    text: str,
    max_chunk_size: int = 512,
    overlap_sentences: int = 1,
) -> list[Chunk]:
    """Semantic chunking: split by Markdown headings / paragraphs, then by size.

    Preserves semantic boundaries (headings, paragraphs) better than
    fixed-length splitting. Falls back to size-based if a section is too long.
    """
    # Split by Markdown headings first
    sections = re.split(r"(?=#{1,3}\s)", text)
    chunks = []
    idx = 0
    current = ""

    for section in sections:
        if not section.strip():
            continue
        if len(current) + len(section) > max_chunk_size and current:
            chunks.append(Chunk(content=current.strip(), index=idx))
            # Keep overlap: last N sentences
            sentences = current.split("。")
            overlap_text = (
                "。".join(sentences[-overlap_sentences:]) if overlap_sentences > 0 else ""
            )
            current = overlap_text + section
            idx += 1
        else:
            current += section

    if current.strip():
        chunks.append(Chunk(content=current.strip(), index=idx))

    return chunks


# ----  Document with Timeliness Metadata ----


@dataclass
class TaxDocument:
    """A tax regulation document with effective/expiry dates."""

    doc_id: str
    title: str
    content: str
    effective_date: date | None = None
    expiry_date: date | None = None
    source: str = ""

    def is_active(self, on: date | None = None) -> bool:
        """Check if this regulation is currently in effect."""
        check_date = on or date.today()
        if self.effective_date and check_date < self.effective_date:
            return False
        return not (self.expiry_date and check_date > self.expiry_date)


# ----  Two-stage Retrieval ----


@dataclass
class RetrievalResult:
    """A single retrieval result with score."""

    doc_id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class SemanticVectorStore:
    """In-memory dense vector store for semantic retrieval.

    Production would use pgvector / Milvus / Qdrant, but for the demo we keep
    everything in memory: chunk embeddings are computed once (at
    ``add_document`` time) and stored L2-normalized, so retrieval is a single
    cosine pass over the query vector.
    """

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._docs: dict[str, TaxDocument] = {}
        self._chunks: dict[str, list[Chunk]] = {}
        self._embeddings: dict[str, list[list[float]]] = {}

    def add_document(self, doc: TaxDocument) -> None:
        self._docs[doc.doc_id] = doc
        # Use semantic chunking by default
        chunks = chunk_by_semantic(doc.content)
        self._chunks[doc.doc_id] = chunks
        # Batch-embed all chunks of this doc in ONE call (one Ollama round-trip)
        # and store them L2-normalized so cosine == dot product at query time.
        vectors = self._embedder.embed([c.content for c in chunks])
        self._embeddings[doc.doc_id] = [_l2_normalize(v) for v in vectors]

    def coarse_recall(
        self,
        query_vec: list[float],
        query_text: str = "",
        top_k: int = 20,
        active_only: bool = True,
        mode: str = "vector",
    ) -> list[RetrievalResult]:
        """Stage 1: dense-vector coarse recall via cosine similarity.

        ``query_vec`` is the L2-normalized embedding of the question. Returns
        ``top_k`` candidates.

        With ``mode="hybrid"`` a lexical-overlap score (the old keyword path)
        is blended in (``0.7 * cosine + 0.3 * overlap``) to harden recall on
        out-of-vocabulary CJK terms a small embedding model may miss.
        """
        results: list[RetrievalResult] = []
        query_terms = tokenize(query_text) if mode == "hybrid" else set()

        for doc_id, chunks in self._chunks.items():
            doc = self._docs[doc_id]
            #  Filter by timeliness
            if active_only and not doc.is_active():
                continue

            for chunk, vec in zip(chunks, self._embeddings.get(doc_id, [])):
                cos = cosine(query_vec, vec)
                score = cos
                if mode == "hybrid" and query_terms:
                    chunk_terms = tokenize(chunk.content)
                    overlap = len(query_terms & chunk_terms)
                    overlap_score = overlap / max(len(query_terms), 1)
                    score = 0.7 * cos + 0.3 * overlap_score
                results.append(
                    RetrievalResult(
                        doc_id=doc_id,
                        content=chunk.content,
                        score=score,
                        metadata={
                            "doc_title": doc.title,
                            "effective_date": str(doc.effective_date),
                            "source": doc.source,
                        },
                    )
                )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]


class ReRanker:
    """Stage 2: Cross-Encoder re-ranking.

    In production: use cross-encoder/ms-marco-MiniLM-L-6-v2
    from sentence-transformers. This mock uses a simple
    heuristic for demonstration.
    """

    def rerank(
        self, query: str, candidates: list[RetrievalResult], top_k: int = 5
    ) -> list[RetrievalResult]:
        """Re-rank candidates using a Cross-Encoder model.

        Cross-Encoder takes (query, document) pair and produces a
        relevance score through deep interaction, achieving higher
        precision than bi-encoder retrieval alone.

        Trade-off: higher accuracy but slower (O(n) model inferences).
        Typically recall 50-100 → rerank to 5-10 for LLM context.
        """
        # Mock: boost scores for documents with recent effective_date
        for candidate in candidates:
            boost = 0.0
            effective = candidate.metadata.get("effective_date", "")
            if effective and effective != "None":
                # More recent = higher boost
                try:
                    from datetime import datetime

                    eff_date = datetime.strptime(effective, "%Y-%m-%d").date()
                    days_old = (date.today() - eff_date).days
                    boost = max(0, 1.0 - days_old / 365.0) * 0.3
                except (ValueError, TypeError):
                    pass
            candidate.score += boost

        candidates.sort(key=lambda r: r.score, reverse=True)
        return candidates[:top_k]


class RAGPipeline:
    """Full RAG pipeline: chunk → embed → recall → rerank → filter."""

    def __init__(self, embedder: Embedder | None = None) -> None:
        self.embedder = embedder or get_default_embedder()
        self.store = SemanticVectorStore(self.embedder)
        self.reranker = ReRanker()
        from tax_agent_platform.config import settings

        self.rag_mode = settings.rag_mode

    def add_document(self, doc: TaxDocument) -> None:
        self.store.add_document(doc)

    def query(
        self, question: str, top_k: int = 5, active_only: bool = True
    ) -> list[RetrievalResult]:
        """Two-stage retrieval: dense recall → Cross-Encoder rerank.

        Results are de-duplicated by ``doc_id`` (the highest-scored chunk per
        document is kept), so a document that spans several chunks contributes
        at most one entry to the top-k — cleaner for both the API and the UI.
        """
        # Embed the question once, then run cosine recall against chunk vectors.
        qvec = _l2_normalize(self.embedder.embed([question])[0])
        # Stage 1: Dense-vector coarse recall
        candidates = self.store.coarse_recall(
            qvec,
            query_text=question,
            top_k=50,
            active_only=active_only,
            mode=self.rag_mode,
        )
        if not candidates:
            return []

        # Stage 2: Re-rank (over-fetch, then collapse duplicates per doc)
        ranked = self.reranker.rerank(question, candidates, top_k=max(top_k, 1) * 4)
        best: dict[str, RetrievalResult] = {}
        for r in ranked:
            if r.doc_id not in best or r.score > best[r.doc_id].score:
                best[r.doc_id] = r
        deduped = sorted(best.values(), key=lambda r: r.score, reverse=True)
        return deduped[:top_k]

    def list_documents(self) -> list[dict[str, Any]]:
        """List all loaded knowledge-base documents with timeliness status.

        Used by the ``/rag/documents`` endpoint and the front-end knowledge
        base panel so users can see what the corpus actually contains.
        """
        out: list[dict[str, Any]] = []
        for doc_id, doc in self.store._docs.items():
            out.append(
                {
                    "doc_id": doc_id,
                    "title": doc.title,
                    "source": doc.source,
                    "effective_date": str(doc.effective_date) if doc.effective_date else None,
                    "expiry_date": str(doc.expiry_date) if doc.expiry_date else None,
                    "active": doc.is_active(),
                    "chunks": len(self.store._chunks.get(doc_id, [])),
                }
            )
        return out

    def document_count(self) -> int:
        """Number of documents currently loaded.

        Public accessor for ``/rag/reload`` (which reports how many documents
        were loaded) so callers don't reach into ``store._docs``.
        """
        return len(self.store._docs)


# ---- Corpus loading from Markdown files (persistence + extensibility) ----
#
# Documents live as ``*.md`` files in a corpus directory, each with a tiny
# ``---\nkey: value\n---`` frontmatter (no external YAML dependency) followed
# by the regulation text. This keeps the knowledge base editable and
# persistent across restarts, and makes adding new regulations a matter of
# dropping a file rather than touching Python code.

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _parse_markdown_corpus(text: str) -> tuple[dict[str, str], str]:
    """Parse a lightweight ``---\nkey: value\n---`` frontmatter + markdown body.

    Returns ``(frontmatter_dict, content)``. Unknown keys are ignored; missing
    frontmatter yields an empty dict and the raw text as content.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()
    fm_text, content = m.group(1), m.group(2)
    fm: dict[str, str] = {}
    for line in fm_text.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip()
    return fm, content.strip()


def _parse_date(value: str | None) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` date, tolerating empty / null markers."""
    if not value:
        return None
    value = value.strip()
    if value in ("None", "null", ""):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        logger.warning("Bad date in corpus frontmatter: %r", value)
        return None


def load_documents_from_corpus(directory: str | os.PathLike) -> list[TaxDocument]:
    """Load every ``*.md`` corpus file under *directory* as a TaxDocument.

    Each file uses a simple frontmatter (``doc_id``, ``title``,
    ``effective_date``, ``expiry_date``, ``source``) followed by the
    regulation text in markdown. Returns an empty list if the directory is
    missing, so callers can fall back to built-in samples.
    """
    directory = Path(directory)
    docs: list[TaxDocument] = []
    if not directory.is_dir():
        logger.warning("Corpus directory not found: %s", directory)
        return docs
    for path in sorted(directory.glob("*.md")):
        try:
            fm, content = _parse_markdown_corpus(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to read corpus %s: %s", path, e)
            continue
        if not content:
            continue
        doc_id = fm.get("doc_id") or path.stem
        if any(d.doc_id == doc_id for d in docs):
            logger.warning("Duplicate doc_id %r in %s; skipping", doc_id, path)
            continue
        docs.append(
            TaxDocument(
                doc_id=doc_id,
                title=fm.get("title", doc_id),
                content=content,
                effective_date=_parse_date(fm.get("effective_date")),
                expiry_date=_parse_date(fm.get("expiry_date")),
                source=fm.get("source", ""),
            )
        )
    logger.info("Loaded %d corpus documents from %s", len(docs), directory)
    return docs
