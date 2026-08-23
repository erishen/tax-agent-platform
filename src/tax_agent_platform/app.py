"""Main FastAPI application — all modules fully integrated.

Integration chain:
  Request → TenantMiddleware (contextvars)
    → API Endpoint
      → PSEWorkflow(goal, rag=..., tools=..., llm=...) 
        → PlannerAgent (LLM structured output)
        → SpecialistAgent (RAG retrieval + Tool calls)
        → EvaluatorAgent (Guardrails + evidence-driven)
    → SSE Response (async streaming)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Depends
from sse_starlette.sse import EventSourceResponse

from tax_agent_platform.agents import PSEWorkflow
from tax_agent_platform.api import router as tools_router
from tax_agent_platform.api.dashboard import router as dashboard_router
from tax_agent_platform.middleware.tenant import (TenantMiddleware,
    get_current_tenant_id,
    set_current_tenant_id,)
from tax_agent_platform.rag import RAGPipeline, TaxDocument, load_documents_from_corpus
from tax_agent_platform.tools.registry import ToolRegistry, get_tool_registry

logger = logging.getLogger(__name__)

app = FastAPI(title="Tax Agent Platform",
    description="Enterprise tax advisory platform",
    version="0.1.0",)

#  Multi-tenancy middleware
app.add_middleware(TenantMiddleware)

#  Async dashboard endpoints
app.include_router(dashboard_router)

#  Tool management endpoints
app.include_router(tools_router)


# ---- Shared RAG pipeline (initialized once) ----

# Corpus lives under <project>/data/corpus as editable Markdown files.
CORPUS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "corpus"


def _resolve_corpus_dir(tenant_id: str | None) -> Path:
    """Return the corpus directory for a tenant (data isolation).

    Isolation strategy (in-memory RLS equivalent): if a tenant-specific
    directory ``data/corpus/<tenant_id>/`` exists and is non-empty, that
    tenant loads its own private corpus; otherwise it falls back to the
    shared ``data/corpus`` root. This gives real per-tenant separation
    without a database, and mirrors how :func:`apply_tenant_filter` would
    scope a SQL-backed store.
    """
    if tenant_id:
        tenant_dir = CORPUS_DIR / tenant_id
        if tenant_dir.is_dir() and any(tenant_dir.glob("*.md")):
            return tenant_dir
    return CORPUS_DIR


def _build_rag_pipeline(tenant_id: str | None = None) -> RAGPipeline:
    """Build the RAG pipeline from the on-disk corpus for a given tenant.

    Each ``.md`` file is a regulation with a small frontmatter
    (``doc_id``, ``title``, ``effective_date``, ``expiry_date``, ``source``).
    Falls back to :func:`_sample_documents` when no corpus is found, so the
    app still runs with zero configuration.
    """
    pipeline = RAGPipeline()
    corpus_dir = _resolve_corpus_dir(tenant_id)
    # ``load_documents_from_corpus`` globs ``*.md`` non-recursively, so tenant
    # subdirectories (data/corpus/<tenant>/) never leak into the shared root.
    docs = load_documents_from_corpus(corpus_dir)
    if not docs:
        logger.warning("No corpus documents loaded from %s; using built-in samples", corpus_dir)
        docs = _sample_documents()
    for d in docs:
        pipeline.add_document(d)
    logger.info("Built RAG pipeline for tenant=%s from %s (%d docs)", tenant_id, corpus_dir, len(docs))
    return pipeline


def _sample_documents() -> list[TaxDocument]:
    """Built-in fallback corpus (kept for zero-config startup)."""
    return [
        TaxDocument(doc_id="vat-2024",
            title="增值税规定 2024 / VAT Regulations 2024",
            content="## 增值税税率变化 / VAT Rate Changes\n"
                    "软件服务适用 6% 增值税税率，自 2024 年 1 月起施行。"
                    "跨境数字服务在特定条件下可适用较低税率。"
                    "月销售额低于 10 万元人民币的小规模纳税人免征增值税。\n"
                    "Software services are subject to 6% VAT rate effective from January 2024. "
                    "Cross-border digital services may qualify for reduced rates under specific conditions. "
                    "Small-scale taxpayers with monthly sales below 100K CNY are exempt from VAT.",
            effective_date=date(2024, 1, 1),
            source="State Taxation Administration",),
        TaxDocument(doc_id="vat-2022",
            title="增值税规定 2022（已废止）/ VAT Regulations 2022 (Superseded)",
            content="## 增值税税率 / VAT Rate\n"
                    "软件服务此前适用 13% 增值税税率，该规定已被 2024 年新规取代。\n"
                    "Software services were subject to 13% VAT rate. "
                    "This regulation has been superseded by 2024 regulations.",
            effective_date=date(2022, 1, 1),
            expiry_date=date(2023, 12, 31),
            source="State Taxation Administration",),
        TaxDocument(doc_id="cit-2024",
            title="企业所得税指引 2024 / Corporate Income Tax Guide 2024",
            content="## 企业所得税税率 / CIT Rates\n"
                    "标准税率 25%。高新技术企业 15%。小型微利企业前 100 万元利润适用 20%。"
                    "研发费用可享受 100% 加计扣除（在实际支出基础上再加计 100%）。\n"
                    "Standard rate: 25%. High-tech enterprises: 15%. Small low-profit enterprises: 20% on first 1M CNY. "
                    "R&D expenses qualify for 100% super deduction (additional 100% on top of actual expense).",
            effective_date=date(2024, 1, 1),
            source="State Taxation Administration",),
    ]


# Per-tenant RAG pipelines (data isolation). Each tenant gets its own
# in-memory pipeline built from its resolved corpus, so switching X-Tenant-ID
# yields a genuinely separate knowledge base. Keyed by tenant id, with
# "_shared" used when no tenant context is present (e.g. tests, tooling).
_rag_pipelines: dict[str, RAGPipeline] = {}


def get_rag_pipeline() -> RAGPipeline:
    """FastAPI dependency: lazy-initialized, per-tenant RAG pipeline."""
    tenant_id = get_current_tenant_id()
    key = tenant_id or "_shared"
    if key not in _rag_pipelines:
        _rag_pipelines[key] = _build_rag_pipeline(tenant_id)
    return _rag_pipelines[key]


def reload_rag_pipeline() -> int:
    """Hot-reload the current tenant's corpus without restarting the server.

    Rebuilds only the calling tenant's pipeline from disk and swaps it in.
    Returns the number of documents now loaded for that tenant.
    """
    tenant_id = get_current_tenant_id()
    key = tenant_id or "_shared"
    _rag_pipelines[key] = _build_rag_pipeline(tenant_id)
    return _rag_pipelines[key].document_count()


# ----  Shared LLM client (default provider from settings, e.g. "primary") ----

_llm_client: Any | None = None
_llm_init_done = False


def get_llm_client() -> Any | None:
    """FastAPI dependency: lazy LLM client for the configured provider.

    Returns None (never raises) when no API key / model is configured, so the
    Planner gracefully falls back to rule-based planning and the app keeps
    running. Configure TAX_PRIMARY_* (default provider) or TAX_SECONDARY_* in.env to enable real LLM-driven structured planning.
    """
    global _llm_client, _llm_init_done
    if not _llm_init_done:
        _llm_init_done = True
        from tax_agent_platform.config import settings
        from tax_agent_platform.llm.client import LLMClient
        try:
            _llm_client = LLMClient(provider=settings.llm_provider)
            logger.info("LLM client ready (provider=%s)", settings.llm_provider)
        except Exception as e:  # missing key/model → run without LLM
            logger.warning("LLM client unavailable (%s); Planner will use rule-based fallback", e)
            _llm_client = None
    return _llm_client


# ----  Integrated Agent workflow endpoint ----

def _run_workflow_in_thread(workflow: "PSEWorkflow",
    goal: str,
    tenant_id: str | None,
    on_event: Any = None,) -> dict[str, Any]:
    """Execute the (synchronous-blocking) workflow off the event loop.

    The OpenAI client is synchronous, so every LLM call blocks the caller for
    the full HTTP round-trip. Running the whole workflow on the event loop
    would freeze the server for the entire multi-step run and stall every other
    request — exactly the problem the SSE path already solves by offloading to
    a worker thread. This helper does the same for ``/agent/run`` so a long run
    never blocks the loop.

    The tenant contextvar is re-set here (inside the worker thread) because
    ``loop.run_in_executor`` does NOT copy the request's contextvars. Setting
    it before ``asyncio.run`` makes it visible to the coroutine (asyncio.run
    captures the current context), so agents that call ``get_current_tenant_id()``
    still see the correct tenant instead of ``None``.
    """
    set_current_tenant_id(tenant_id)
    try:
        return asyncio.run(workflow.run(goal, on_event=on_event))
    except Exception as exc:  # noqa: BLE001
        logger.error("Workflow execution failed (tenant=%s): %s", tenant_id, exc)
        return {"error": "workflow execution failed; see server logs for details"}


@app.post("/agent/run", tags=["Agent"])
async def run_agent_workflow(goal: str,
    rag: RAGPipeline = Depends(get_rag_pipeline),
    tools: ToolRegistry = Depends(get_tool_registry),
    llm: Any = Depends(get_llm_client),) -> dict[str, Any]:
    """Run PSE workflow with full integration: RAG + Tools + LLM + Guardrails + Multi-tenancy."""
    from tax_agent_platform.config import settings
    workflow = PSEWorkflow(max_retries=settings.max_retries,
        rag_pipeline=rag,
        tool_registry=tools,
        llm_client=llm,)
    # Offload the blocking workflow to a worker thread (see _run_workflow_in_thread).
    tenant_id = get_current_tenant_id()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _run_workflow_in_thread(workflow, goal, tenant_id))


@app.get("/agent/stream", tags=["Agent"])
async def stream_agent_workflow(goal: str,
    rag: RAGPipeline = Depends(get_rag_pipeline),
    tools: ToolRegistry = Depends(get_tool_registry),
    llm: Any = Depends(get_llm_client),) -> EventSourceResponse:
    """SSE stream for real-time PSE agent execution."""
    from tax_agent_platform.config import settings
    workflow = PSEWorkflow(max_retries=settings.max_retries,
        rag_pipeline=rag,
        tool_registry=tools,
        llm_client=llm,)

    async def event_generator():
        # Drive the SAME workflow.run used by /agent/run, emitting progress via
        # the on_event callback so the SSE stream stays in lock-step with REST.
        # A small minimum gap between emitted events keeps the stream perceptible
        # even when the model responds near-instantly (otherwise every event
        # arrives within a few ms and the log looks like a single non-streamed
        # dump instead of a live feed).
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        state: dict[str, Any] = {"steps": []}
        # Capture the tenant from the request context (set by the middleware on
        # this loop) so it can be re-applied inside the worker thread below.
        tenant_id = get_current_tenant_id()

        def on_event(event_type: str, payload: dict[str, Any]) -> None:
            if event_type == "planning":
                state["steps"] = payload.get("steps", [])
            # Bridge onto the main event loop from the worker thread
            # (call_soon_threadsafe is the only safe way to touch an
            # asyncio.Queue from another thread).
            loop.call_soon_threadsafe(queue.put_nowait, (event_type, payload))

        def _run_sync() -> dict[str, Any]:
            # The OpenAI client is *synchronous* and blocks the caller for
            # the full HTTP round-trip. If we awaited workflow.run on the
            # event loop directly, every LLM call would freeze the loop and
            # the SSE generator couldn't emit progress until the WHOLE run
            # finished — the client would see one giant dump at the end.
            # Running the workflow in a worker thread (with its own loop)
            # keeps those blocking calls off the SSE loop so events stream
            # live, the instant they happen.
            return _run_workflow_in_thread(workflow, goal, tenant_id, on_event=on_event)

        _last = 0.0
        MIN_GAP = 0.25

        async def _pace() -> None:
            nonlocal _last
            now = loop.time()
            if _last and now - _last < MIN_GAP:
                await asyncio.sleep(MIN_GAP - (now - _last))
            _last = loop.time()

        # Kick off the blocking workflow in a worker thread, but DON'T await
        # it here — otherwise the generator can't stream events until the run
        # finishes. The done-callback bridges the final result back via the
        # queue when the thread completes.
        fut = loop.run_in_executor(None, _run_sync)

        def _on_done(f: "asyncio.Future[dict[str, Any]]") -> None:
            try:
                res = f.result()
            except Exception as exc:  # noqa: BLE001
                logger.error("Workflow execution failed (SSE): %s", exc)
                res = {"error": "workflow execution failed; see server logs for details"}
            loop.call_soon_threadsafe(queue.put_nowait, ("__end__", res))

        fut.add_done_callback(_on_done)

        await _pace()
        yield {"event": "planning", "data": json.dumps({
            "status": "started", "goal": goal, "tenant": get_current_tenant_id(),
        })}

        while True:
            et, pl = await queue.get()
            if et == "__end__":
                # Ship the full result through the stream so the client does
                # not need a second REST call to /agent/run (avoids running the
                # agent twice and double-spending LLM tokens).
                await _pace()
                yield {"event": "result", "data": json.dumps(pl, ensure_ascii=False, default=str)}
                await _pace()
                yield {"event": "done", "data": json.dumps({
                    "status": "completed", "total_steps": len(state["steps"]),
                })}
                break
            if et == "planning":
                await _pace()
                yield {"event": "planning", "data": json.dumps({
                    "status": "completed", "output": pl.get("output"),
                })}
            elif et == "step_started":
                await _pace()
                yield {"event": "executing", "data": json.dumps({
                    "step": pl.get("step"), "status": "started",
                })}
            elif et == "step_executed":
                await _pace()
                yield {"event": "executing", "data": json.dumps({
                    "step": pl.get("step"), "status": "completed",
                    "evidence_count": pl.get("evidence_count"),
                })}
            elif et == "step_evaluated":
                await _pace()
                yield {"event": "evaluating", "data": json.dumps({
                    "step": pl.get("step"), "verdict": pl.get("verdict"),
                    "issues": pl.get("issues", []),
                })}

    return EventSourceResponse(event_generator())


# ----  RAG query endpoint ----

@app.get("/rag/query", tags=["RAG"])
async def rag_query(question: str,
    top_k: int = 5,
    active_only: bool = True,
    rag: RAGPipeline = Depends(get_rag_pipeline),) -> list[dict[str, Any]]:
    """Query the RAG pipeline: coarse recall → Cross-Encoder rerank → timeliness filter.

    ``active_only`` (default True) hides superseded regulations; set it to
    False to include them (e.g. compare the old 13% VAT rate vs the new 6%).
    """
    results = rag.query(question, top_k=top_k, active_only=active_only)
    return [{"doc_id": r.doc_id, "content": r.content, "score": round(r.score, 4), "metadata": r.metadata} for r in results]


@app.get("/rag/documents", tags=["RAG"])
async def rag_documents(rag: RAGPipeline = Depends(get_rag_pipeline),) -> list[dict[str, Any]]:
    """List all loaded knowledge-base documents with timeliness status.

    Lets the front-end knowledge-base panel show what the corpus actually
    contains, including which regulations are currently active vs superseded.
    """
    return rag.list_documents()


@app.post("/rag/reload", tags=["RAG"])
async def rag_reload() -> dict[str, Any]:
    """Hot-reload the corpus from disk without restarting the server.

    Call this after editing/adding ``.md`` files under ``data/corpus``.
    """
    count = reload_rag_pipeline()
    return {"status": "reloaded", "documents": count}


# ----  LLM health check ----

@app.get("/llm/status", tags=["LLM"])
async def llm_status() -> dict[str, Any]:
    """Check LLM client status: active provider + guardrails/circuit-breaker info."""
    from tax_agent_platform.config import settings
    provider = settings.llm_provider
    if provider == "secondary":
        model, base_url, key = settings.secondary_model, settings.secondary_base_url, settings.secondary_api_key
    else:
        model, base_url, key = settings.primary_model, settings.primary_base_url, settings.primary_api_key
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key_configured": bool(key),
        "llm_active": get_llm_client() is not None,
        "circuit_breaker_threshold": settings.circuit_breaker_threshold,
        "llm_timeout": settings.llm_timeout,
    }


# ----  Tenant info ----

@app.get("/tenant/me", tags=["Tenant"])
async def tenant_info() -> dict[str, Any]:
    """Get current tenant context (contextvars isolation)."""
    from tax_agent_platform.middleware.tenant import get_tenant
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return {"error": "No tenant context"}
    info = get_tenant(tenant_id)
    return {"tenant_id": tenant_id, "info": info.model_dump() if info else None}


@app.get("/health", tags=["System"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
