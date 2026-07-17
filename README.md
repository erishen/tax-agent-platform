# Tax Agent Platform

Enterprise tax advisory platform demonstrating Enterprise interview knowledge points (Q1-Q9) via FastAPI + RAG + Multi-Agent + Multi-tenancy.

## Knowledge Points Mapping

| Q | Topic | Module | Key Pattern |
|---|-------|--------|-------------|
| Q1 | Pydantic + Type Hints | `models.py` | Pydantic validation replaces manual checks → auto Swagger docs + testability |
| Q2 | Code Quality / CI/CD | `.github/workflows/ci.yml`, `tests/` | Ruff lint + pytest CI pipeline |
| Q3 | AI-Assisted Dev | — | Vibe Coding methodology (Figma MCP + Serena MCP + Playwright MCP) |
| Q4 | Async + Timeout | `api/dashboard.py`, `app.py` | `asyncio.gather` + `wait_for` timeout + SSE streaming |
| Q5 | RAG | `rag/`, `data/corpus/` | Semantic chunking → **dense-vector recall (local Ollama embedding, vector/hybrid)** → Cross-Encoder rerank → timeliness filter → dedup by doc_id |
| Q6 | Tool Management + DI | `api/`, `tools/registry.py` | RESTful tool CRUD + `Depends()` injection |
| Q7 | LLM Stability | `llm/client.py` | Structured output + tenacity retry + circuit breaker + guardrails + token billing |
| Q8 | Multi-Tenancy | `middleware/tenant.py`, `app.py`, `tools/registry.py` | `contextvars` isolation + token-bucket rate limit + **per-tenant RAG corpus / tool registry** + RLS filter |
| Q9 | Multi-Agent | `agents/` | PSE (Planner/Specialist/Evaluator) + workflow engine + evidence-driven eval |

## Architecture

```
Request → TenantMiddleware (Q8: API key → tenant → contextvars)
  → API Endpoint (Q6: Depends injects RAG + Tools)
    → PSEWorkflow
      → PlannerAgent (Q7: LLMClient.structured_call)
      → SpecialistAgent (Q5: RAGPipeline + Q6: ToolRegistry)
      → EvaluatorAgent (Q7: Guardrails + Q9: evidence-driven)
    → PARTIAL/FAIL → auto-retry (max 3)
  → SSE Response (Q4: real-time streaming)
```

## Quick Start

```bash
# Install dependencies
make install

# Run development server
make run

# Run tests
make test

# Lint check
make lint
```

## API Endpoints

| Method | Path | Tag | Description |
|--------|------|-----|-------------|
| GET | `/dashboard/{user_id}` | Dashboard | Q4: Async concurrent microservice calls with timeout |
| POST | `/tools/` | AI Tools | Q6: Register new Agent tool |
| GET | `/tools/` | AI Tools | Q6: List available tools |
| POST | `/agent/run?goal=...` | Agent | Q9: Run PSE workflow (returns `token_usage`) |
| GET | `/agent/stream?goal=...` | Agent | Q4/Q9: SSE real-time agent execution (result event carries `token_usage`) |
| GET | `/rag/query?question=...` | RAG | Q5: Two-stage retrieval + rerank + dedup |
| GET | `/rag/documents` | RAG | List all knowledge-base documents with active/superseded status |
| POST | `/rag/reload` | RAG | Hot-reload the corpus from disk (no restart needed) |
| GET | `/llm/status` | LLM | Q7: Circuit breaker + guardrails status |
| GET | `/tenant/me` | Tenant | Q8: Current tenant context |
| GET | `/health` | System | Health check |

## Knowledge Base Corpus (RAG)

The regulation knowledge base lives under `data/corpus/` as Markdown files with a
tiny frontmatter (zero-dependency, regex-parsed):

```markdown
---
doc_id: vat-2024
title: VAT Regulations 2024
effective_date: 2024-01-01
source: State Taxation Administration
---

## VAT Rate Changes
Software services are subject to 6% VAT rate effective from January 2024 ...
```

- **Persistence**: loaded from disk at startup and on `POST /rag/reload`; survives
  restarts and is editable in place.
- **Hot reload**: the RAG page "Knowledge Base" panel has a Reload button — edit the
  corpus, no server restart required.
- **Retrieval**: two-stage — first a dense-vector coarse recall (default `hybrid`:
  local Ollama `nomic-embed-text` cosine similarity blended with lexical overlap to
  harden Chinese recall; switchable to `vector` for pure dense), then Cross-Encoder
  rerank, de-duplicated by `doc_id` so a multi-chunk document contributes at most one
  entry to the top-k. Embeddings run on local Ollama — no cloud API key needed.
- **Token accounting**: every LLM call accumulates token usage; `/agent/run` and
  `/agent/stream` responses include `token_usage` (prompt/completion/total/calls/cost/
  provider). Unit prices come from the env vars below and can be overridden in `.env`.

## Multi-Tenant Data Isolation (Q8)

Tenant isolation is **real data separation** (each tenant gets its own in-memory RAG pipeline and tool registry) and is now protected by **gateway-level API-key auth** — the tenant is derived from the key, so clients cannot spoof `X-Tenant-ID`. Still demo-grade; see **Security & Privacy**.

- **Per-tenant RAG corpus**: `get_rag_pipeline()` builds a separate in-memory pipeline
  per `X-Tenant-ID`. If `data/corpus/<tenant_id>/` exists and contains `.md` files, that
  tenant loads its own private corpus; otherwise it falls back to the shared
  `data/corpus/` root. Example: `acme-us` loads its own
  `data/corpus/acme-us/us-federal-cit-2024.md` (US federal tax), while `acme-cn`/`demo`
  load the shared China-tax corpus.
- **Per-tenant tool registry**: `get_tool_registry()` keeps an independent
  `ToolRegistry` per tenant (each seeded with default tools). A tool registered under one
  tenant via `POST /tools/` is invisible to others.
- **SQL Row-Level Security (RLS)**: `apply_tenant_filter()` auto-appends
  `AND tenant_id = :tenant_id` to queries so a future DB-backed store enforces isolation
  (covered by unit tests).
- **Front-end switching**: the front-end sends the tenant's API key in the
  `X-API-Key` header — read from `localStorage` or, at build time, from
  `VITE_TAX_API_KEY` (see `web/.env.example`), with a non-secret dev demo key
  as fallback. The tenant identity is **derived server-side from the key**; the
  client never asserts `X-Tenant-ID`. The "Tenant" page switcher swaps the
  stored key.

```bash
# acme-cn (key tax_demo_acme_cn_8f3a) loads the shared China-tax corpus (8 docs)
curl -H "X-API-Key: tax_demo_acme_cn_8f3a" http://localhost:8001/rag/documents
# acme-us (key tax_demo_acme_us_2b9e) loads only its own US federal corpus (isolation check)
curl -H "X-API-Key: tax_demo_acme_us_2b9e" http://localhost:8001/rag/documents
```

## Security & Privacy

This project is an interview-demo (Q1-Q9) and the following limits **must** be
addressed before any production use:

- **Gateway-level tenant API-key auth (implemented)**: Tenant identity is now
  **derived from an API key**, not claimed by the client. Requests must carry
  an `X-API-Key` header, `Authorization: Bearer <key>`, or a `?api_key=` query
  param (SSE). The middleware reverse-looks-up the tenant from the key and uses
  it as the **authoritative identity**, ignoring any client-supplied
  `X-Tenant-ID` — this closes the tenant-spoofing hole. Missing/invalid keys
  return 401 when `TAX_AUTH_REQUIRED=true` (default); `/health`, `/docs`,
  `/openapi.json`, `/redoc` are unauthenticated. The key map lives in
  `TAX_TENANT_API_KEYS` (JSON or `tenant:key|...` pipe format). Set
  `TAX_AUTH_REQUIRED=false` to fall back to the legacy client-claimed
  `X-Tenant-ID` (dev only — re-opens the spoofing risk).
  Demo dev keys are in `.env.example`
  (e.g. `acme-cn=tax_demo_acme_cn_8f3a`). The front-end reads its key from
  `localStorage` or, at build time, from `VITE_TAX_API_KEY` (see
  `web/.env.example`), falling back to the same dev demo keys.
  **In production, issue per-tenant keys server-side — never ship real keys in
  a SPA** (the bundled fallback keys are non-secret dev placeholders only).
- **PII egress to third-party LLM**: the user's `goal` (a tax question that may
  contain sensitive info like revenue or income) is sent to the configured
  external OpenAI-compatible LLM (`TAX_PRIMARY_BASE_URL`). Direct identifiers
  (ID card / phone / email / bank card / credit code) are scrubbed before
  egress (see `llm/client.py:redact_pii`); for stronger guarantees production
  needs a DPA, a private/on-prem model, or fuller redaction. Retrieval
  embeddings run on local Ollama and do not leave the box.
- **Logs contain user input**: `agents` used to log the full `goal` and step
  descriptions at INFO (now truncated — only length/summary is kept).
  `llm/client` still logs raw exceptions; if logs are shipped off-box they may
  leak call details. Tighten log levels and shipping scope in production.
- **Error responses are sanitized**: `/agent/run` and the SSE stream now return
  a **generic error message** (`workflow execution failed; see server logs for
  details`); internal exception details stay in server logs only, never in the
  client response.
- **Cross-user boundary**: `/dashboard/{user_id}` currently returns mock data
  and is **not tenant-scoped**; wire it to `apply_tenant_filter()` (already
  implemented, only enforces on a DB-backed store) before using real data.
- **CORS**: no `CORSMiddleware` is configured; the front-end uses the Vite dev
  proxy (same-origin). For cross-origin deployment set `allow_origins`
  explicitly — never `*`.
- **Secrets**: `.env` is git-ignored; never commit real keys. Copy
  `.env.example` to `.env` and fill in secrets.

## Testing with curl

```bash
# Q8: Multi-tenancy — auth requires an API key (tenant is derived from it)
curl -H "X-API-Key: tax_demo_acme_cn_8f3a" http://localhost:8001/tenant/me

# Q4: Async dashboard
curl -H "X-API-Key: tax_demo_acme_cn_8f3a" http://localhost:8001/dashboard/1

# Q6: Register a tool
curl -X POST http://localhost:8001/tools/ \
  -H "X-API-Key: tax_demo_acme_cn_8f3a" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"tax_calculator","description":"Calculate VAT/CIT amounts","endpoint_url":"http://tax-service/calculate"}'

# Q9: Run PSE agent workflow
curl -H "X-API-Key: tax_demo_acme_cn_8f3a" "http://localhost:8001/agent/run?goal=VAT+rate+for+software"

# Q5: RAG query
curl -H "X-API-Key: tax_demo_acme_cn_8f3a" "http://localhost:8001/rag/query?question=software+VAT+rate"

# Q5: List knowledge-base documents (with active status)
curl -H "X-API-Key: tax_demo_acme_cn_8f3a" http://localhost:8001/rag/documents

# Q5: Hot-reload the corpus
curl -X POST -H "X-API-Key: tax_demo_acme_cn_8f3a" http://localhost:8001/rag/reload

# Q4/Q9: SSE stream
curl -N -H "X-API-Key: tax_demo_acme_cn_8f3a" "http://localhost:8001/agent/stream?goal=CIT+deduction"
```

## Project Structure

```
tax-agent-platform/
├── src/tax_agent_platform/
│   ├── app.py                 # Main FastAPI app (integration hub)
│   ├── config.py              # Q1: pydantic-settings
│   ├── models.py              # Q1/Q6/Q7/Q8/Q9: Pydantic models
│   ├── api/
│   │   ├── __init__.py        # Q6: Tool management routes
│   │   └── dashboard.py       # Q4: Async dashboard routes
│   ├── agents/
│   │   └── __init__.py        # Q9: PSE framework + workflow engine
│   ├── rag/
│   │   ├── __init__.py        # Q5: Chunking + retrieval + rerank + corpus load + dense store
│   │   └── embeddings.py      # Q5: Local Ollama embedding (with offline fallback)
│   ├── llm/
│   │   ├── __init__.py
│   │   └── client.py          # Q7: Structured output + retry + circuit breaker + guardrails
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── tenant.py          # Q8: contextvars + rate limit + RLS
│   └── tools/
│       ├── __init__.py
│       └── registry.py        # Q6: Tool registration + DI
├── tests/
│   ├── test_core.py           # Q2: Unit tests (models / guardrails / RLS)
│   ├── test_circuit_breaker.py# Q7: Circuit breaker (incl. guardrails don't trip it)
│   ├── test_tenant_isolation.py # Q8: per-tenant isolation
│   └── test_rag_vector.py     # Q5: Semantic vector recall
├── .github/workflows/
│   └── ci.yml                 # Q2: CI pipeline
├── web/                       # Front-end (Vite + React + TypeScript)
│   └── src/                   #   api.ts / pages / App.tsx
├── .env.example               # Env var sample (copy to .env to fill secrets)
├── Makefile
└── pyproject.toml
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TAX_PRIMARY_API_KEY` | `""` | LLM API key (default provider) |
| `TAX_PRIMARY_BASE_URL` | `https://api.openai.com/v1` | LLM API base URL (default, any OpenAI-compatible endpoint) |
| `TAX_PRIMARY_MODEL` | `gpt-4o-mini` | LLM model name (default) |
| `TAX_SECONDARY_API_KEY` | `""` | LLM API key (alternate / fail-over provider) |
| `TAX_SECONDARY_BASE_URL` | `https://api.openai.com/v1` | LLM API base URL (alternate) |
| `TAX_SECONDARY_MODEL` | `gpt-4o-mini` | LLM model name (alternate) |
| `TAX_LLM_PROVIDER` | `primary` | Default LLM provider (`primary` / `secondary`) |
| `TAX_MAX_RETRIES` | `3` | PSE workflow max retries |
| `TAX_CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive failures before circuit opens |
| `TAX_LLM_TIMEOUT` | `30.0` | LLM request timeout (seconds) |
| `TAX_RAG_TOP_K` | `20` | RAG coarse recall top-k |
| `TAX_RAG_RERANK_K` | `5` | RAG rerank top-k |
| `TAX_RAG_MODE` | `hybrid` | RAG recall mode: `hybrid` (vector + lexical blend, recommended for Chinese) / `vector` (pure dense) |
| `TAX_CHUNK_SIZE` | `512` | RAG chunk size (chars) for semantic chunking |
| `TAX_CHUNK_OVERLAP` | `64` | RAG chunk overlap (chars) |
| `TAX_EMBEDDING_MODEL` | `nomic-embed-text` | Local embedding model (needs `ollama pull`); swap to `snowflake-arctic-embed2` for higher recall |
| `TAX_EMBEDDING_URL` | `http://localhost:11434/api/embed` | Ollama embedding endpoint |
| `TAX_EMBEDDING_TIMEOUT` | `10.0` | Embedding request timeout (seconds) |
| `TAX_PRIMARY_PRICE_PROMPT` | `0.004` | primary input price (CNY / 1K tokens) |
| `TAX_PRIMARY_PRICE_COMPLETION` | `0.012` | primary output price (CNY / 1K tokens) |
| `TAX_SECONDARY_PRICE_PROMPT` | `0.001` | secondary input price |
| `TAX_SECONDARY_PRICE_COMPLETION` | `0.002` | secondary output price |
| `TAX_HOST` | `0.0.0.0` | Server host |
| `TAX_PORT` | `8001` | Server port |
| `TAX_AUTH_REQUIRED` | `true` | Gateway auth on/off; `false` falls back to legacy client-claimed `X-Tenant-ID` (dev only, re-opens spoofing) |
| `TAX_TENANT_API_KEYS` | `{}` | tenant→API-key map, JSON `{"acme-cn":"...","acme-us":"..."}` or pipe `acme-cn:k1\|acme-us:k2` |

> Copy `.env.example` to `.env` and fill in secrets; `.env` is git-ignored — never commit real keys.
