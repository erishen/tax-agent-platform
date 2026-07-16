"""Q5 (upgrade): dense-vector semantic retrieval.

Covers the move from TF-IDF keyword overlap to real cosine retrieval over
local Ollama embeddings, plus the offline HashEmbedder fallback and the
unchanged de-dup / timeliness behaviors.
"""

from __future__ import annotations

from datetime import date

from tax_agent_platform.rag import RAGPipeline, TaxDocument
from tax_agent_platform.rag.embeddings import (
    HashEmbedder,
    OllamaEmbedder,
    cosine,
    get_default_embedder,
)


def _vat_doc() -> TaxDocument:
    return TaxDocument(
        doc_id="vat-2024",
        title="增值税规定 2024",
        content="## 增值税税率\n软件服务适用 6% 增值税税率，自 2024 年 1 月起施行。"
                "Software services are subject to 6% VAT rate effective from January 2024.",
        effective_date=date(2024, 1, 1),
        source="STA",
    )


def _cit_doc() -> TaxDocument:
    return TaxDocument(
        doc_id="cit-2024",
        title="企业所得税指引 2024",
        content="## 企业所得税税率\n标准税率 25%。高新技术企业 15%。"
                "Standard CIT rate is 25%. High-tech enterprises pay 15%.",
        effective_date=date(2024, 1, 1),
        source="STA",
    )


def _superseded_vat_doc() -> TaxDocument:
    return TaxDocument(
        doc_id="vat-2022",
        title="增值税规定 2022（已废止）",
        content="软件服务此前适用 13% 增值税税率，该规定已被 2024 年新规取代。"
                "Software services were subject to 13% VAT rate; now superseded.",
        effective_date=date(2022, 1, 1),
        expiry_date=date(2023, 12, 31),
        source="STA",
    )


# ---- Deterministic offline tests (HashEmbedder, no network) ----

def test_vector_recall_ranks_relevant_doc_first():
    """A query sharing tokens with the VAT doc scores it above the CIT doc."""
    pipe = RAGPipeline(embedder=HashEmbedder())
    pipe.add_document(_vat_doc())
    pipe.add_document(_cit_doc())

    results = pipe.query("软件 增值税 税率", top_k=3)
    assert results, "expected at least one result"
    assert results[0].doc_id == "vat-2024"
    assert all(r.doc_id != "cit-2024" for r in results[:1]) or results[0].doc_id == "vat-2024"


def test_dedup_by_doc_id_no_duplicate_entries():
    """Multiple matching chunks of one doc collapse to a single top-k entry."""
    pipe = RAGPipeline(embedder=HashEmbedder())
    pipe.add_document(_vat_doc())
    pipe.add_document(_cit_doc())
    results = pipe.query("税率 rate", top_k=5)
    ids = [r.doc_id for r in results]
    assert len(ids) == len(set(ids)), "doc_id duplicated in results"


def test_active_only_hides_superseded_but_false_includes_it():
    """Timeliness filter behavior is preserved under vector retrieval."""
    pipe = RAGPipeline(embedder=HashEmbedder())
    pipe.add_document(_vat_doc())
    pipe.add_document(_superseded_vat_doc())

    active = pipe.query("软件 增值税 税率", top_k=5, active_only=True)
    assert all(r.doc_id != "vat-2022" for r in active)

    all_docs = pipe.query("软件 增值税 税率", top_k=5, active_only=False)
    assert "vat-2022" in [r.doc_id for r in all_docs]


# ---- Embedder behavioral tests ----

def test_default_embedder_returns_consistent_vectors():
    """get_default_embedder() works and yields equal-length vectors."""
    emb = get_default_embedder()
    vecs = emb.embed(["software VAT rate", "企业所得税"])
    assert len(vecs) == 2
    assert len(vecs[0]) == len(vecs[1])
    assert len(vecs[0]) > 0


def test_ollama_embedder_falls_back_when_unreachable():
    """An unreachable Ollama URL must transparently use the hash fallback."""
    emb = OllamaEmbedder(
        model="nomic-embed-text",
        url="http://127.0.0.1:9/api/embed",  # nothing listening here
        timeout=2.0,
    )
    vecs = emb.embed(["hello", "world"])
    assert len(vecs) == 2
    assert emb.dim > 0
    # Fallback path is deterministic and reproducible
    vecs2 = emb.embed(["hello", "world"])
    assert vecs == vecs2


def test_cosine_identical_is_one():
    v = [0.1, 0.2, -0.3, 0.4]
    assert abs(cosine(v, v) - 1.0) < 1e-9
