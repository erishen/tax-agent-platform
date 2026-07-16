"""Configuration management (pydantic-settings)."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment / .env.

    Supports two LLM providers (both OpenAI-compatible):
      - "agnes" (default): uses TAX_AGNES_* variables
      - "deepseek": uses TAX_OPENAI_* variables
    """

    # LLM — DeepSeek (alternate provider)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.deepseek.com/v1"
    openai_model: str = "deepseek-chat"

    # LLM — Agnes (default provider, OpenAI-compatible)
    agnes_key: str = ""
    agnes_base_url: str = "https://apihub.agnes-ai.com/v1"
    agnes_model: str = "agnes-2.0-flash"

    # Default provider: "agnes" | "deepseek"
    llm_provider: str = "agnes"

    # Agent
    max_retries: int = 3
    circuit_breaker_threshold: int = 5
    llm_timeout: float = 30.0

    # Token pricing (CNY per 1K tokens) — override via env, e.g.
    # TAX_AGNES_PRICE_PROMPT / TAX_AGNES_PRICE_COMPLETION. Defaults are
    # placeholders; set your real per-provider rates without touching code.
    agnes_price_prompt: float = 0.004
    agnes_price_completion: float = 0.012
    deepseek_price_prompt: float = 0.001
    deepseek_price_completion: float = 0.002

    # RAG
    rag_top_k: int = 20
    rag_rerank_k: int = 5
    chunk_size: int = 512
    chunk_overlap: int = 64
    # RAG retrieval mode: "vector" (dense only) | "hybrid" (vector + lexical blend).
    # Default is "hybrid": this corpus is Chinese-heavy and the local embedding
    # model (nomic-embed-text) is stronger on English, so blending a lexical
    # overlap score hardens Chinese recall without losing semantic matching.
    rag_mode: str = "hybrid"
    # Local embedding backend (Ollama). No API key / cloud needed.
    embedding_model: str = "nomic-embed-text"
    embedding_url: str = "http://localhost:11434/api/embed"
    embedding_timeout: float = 10.0

    # Server
    host: str = "0.0.0.0"
    port: int = 8001

    # Auth — gateway-level tenant API keys (Q8 hardening).
    # When auth_required is True, every non-public request MUST present a valid
    # API key. The tenant identity is DERIVED from the key — a client can never
    # impersonate another tenant by forging X-Tenant-ID. Key map is JSON:
    #   TAX_TENANT_API_KEYS={"acme-cn":"...","acme-us":"..."}
    # A key may also be supplied as a pipe list: "acme-cn:k1|acme-us:k2".
    # Fail-closed: required=True with no configured keys => all requests 401.
    auth_required: bool = True
    tenant_api_keys: dict[str, str] = Field(
        default_factory=dict,
        description="tenant_id -> api_key mapping (JSON or pipe format in env)",
    )

    model_config = {"env_prefix": "TAX_", "env_file": ".env"}

    @field_validator("tenant_api_keys", mode="before")
    @classmethod
    def _parse_api_keys(cls, v: object) -> dict[str, str]:
        """Accept either a JSON object or a pipe list ``tenant:key|tenant2:key2``."""
        if isinstance(v, dict):
            return {str(k): str(val) for k, val in v.items()}
        if not isinstance(v, str) or not v.strip():
            return {}
        s = v.strip()
        try:
            import json

            parsed = json.loads(s)
            if isinstance(parsed, dict):
                return {str(k): str(val) for k, val in parsed.items()}
        except (json.JSONDecodeError, ValueError):
            pass
        out: dict[str, str] = {}
        for part in s.split("|"):
            if ":" not in part:
                continue
            tid, key = part.split(":", 1)
            out[tid.strip()] = key.strip()
        return out


settings = Settings()
