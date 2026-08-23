""" Multi-tenancy middleware — contextvars, auth, rate limiting, data isolation.

Demonstrates:
1. API-key gateway auth: tenant identity is DERIVED from a credential
   (X-API-Key / Authorization: Bearer / ?api_key=), never from a client-claimed
   X-Tenant-ID — closing the tenant-spoofing hole.
2. contextvars.ContextVar for async-safe tenant context propagation
3. Token-bucket rate limiting per tenant
4. Row-Level Security pattern (tenant_id filter on all queries)
"""

from __future__ import annotations

import contextvars
import logging
import time
from dataclasses import dataclass, field

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from tax_agent_platform.config import settings
from tax_agent_platform.models import TenantInfo

logger = logging.getLogger(__name__)

# ----  Tenant Context via contextvars ----

# ContextVar ensures tenant_id is correctly propagated across async coroutines
# without passing it through every function signature
_tenant_context: contextvars.ContextVar[str | None] = contextvars.ContextVar("tenant_id", default=None)


def get_current_tenant_id() -> str | None:
    """Get the current tenant ID from async context."""
    return _tenant_context.get()


def set_current_tenant_id(tenant_id: str | None) -> None:
    """Set the current tenant ID in async context."""
    _tenant_context.set(tenant_id)


# ----  In-memory tenant store ----

# In production: backed by database with encrypted connection strings
_TENANTS: dict[str, TenantInfo] = {
    "acme-cn": TenantInfo(tenant_id="acme-cn", name="ACME China", plan="enterprise", rate_limit=200),
    "acme-us": TenantInfo(tenant_id="acme-us", name="ACME US", plan="enterprise", rate_limit=200),
    "demo": TenantInfo(tenant_id="demo", name="Demo Corp", plan="standard", rate_limit=50),
}


def get_tenant(tenant_id: str) -> TenantInfo | None:
    """Look up tenant by ID."""
    return _TENANTS.get(tenant_id)


# ----  Token-bucket rate limiter ----

@dataclass
class TokenBucket:
    """Token bucket rate limiter for a single tenant."""
    rate: int  # tokens per minute
    capacity: int = 0
    tokens: float = 0
    last_refill: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.capacity = self.rate
        self.tokens = float(self.rate)

    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if allowed, False if rate limited."""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * (self.rate / 60.0))
        self.last_refill = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


class RateLimiter:
    """Per-tenant rate limiter using token buckets."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def is_allowed(self, tenant_id: str, tenant_info: TenantInfo) -> bool:
        if tenant_id not in self._buckets:
            self._buckets[tenant_id] = TokenBucket(rate=tenant_info.rate_limit)
        return self._buckets[tenant_id].consume()


# ----  Middleware ----

class TenantMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that:
    1. Authenticates the request via an API key and DERIVES the tenant from it
       (X-API-Key / Authorization: Bearer / ?api_key=). A client-claimed
       X-Tenant-ID is ignored when a key is present, so a caller cannot
       impersonate another tenant.
    2. When auth is disabled (dev mode), falls back to the legacy
       client-claimed X-Tenant-ID for backward compatibility.
    3. Sets tenant context via ContextVar (async-safe)
    4. Enforces per-tenant rate limiting
    5. Does NOT modify business logic — tenant_id is transparent via context
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._limiter = RateLimiter()

    @staticmethod
    def _extract_api_key(request: Request) -> str | None:
        """Pull the API key from header or SSE query param, in priority order."""
        auth = request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            return auth[7:].strip()
        key = request.headers.get("X-API-Key")
        if key:
            return key.strip()
        q = request.query_params.get("api_key")
        if q:
            return q.strip()
        return None

    @staticmethod
    def _tenant_for_key(api_key: str) -> str | None:
        """Reverse-lookup the tenant that owns this key (authoritative identity)."""
        mapping = settings.tenant_api_keys or {}
        for tenant_id, key in mapping.items():
            if key and key == api_key:
                return tenant_id
        return None

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Public endpoints: health check + API docs are unauthenticated.
        if request.url.path in ("/health", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)

        api_key = self._extract_api_key(request)

        if api_key:
            # Tenant is DERIVED from the key. Client-claimed X-Tenant-ID is
            # intentionally ignored so a caller cannot impersonate a tenant.
            tenant_id = self._tenant_for_key(api_key)
            if tenant_id is None:
                return Response(content='{"error": "Invalid API key"}',
                    status_code=401,
                    media_type="application/json",)
        else:
            # No key presented.
            if settings.auth_required:
                return Response(content='{"error": "API key required (X-API-Key / Authorization: Bearer / ?api_key=)"}',
                    status_code=401,
                    media_type="application/json",)
            # Dev fallback (auth disabled): trust the client-claimed tenant.
            tenant_id = request.headers.get("X-Tenant-ID") or request.query_params.get("tenant")
            if not tenant_id:
                return Response(content='{"error": "X-Tenant-ID header required"}',
                    status_code=400,
                    media_type="application/json",)

        # Validate tenant existence
        tenant_info = get_tenant(tenant_id)
        if not tenant_info:
            return Response(content=f'{{"error": "Unknown tenant: {tenant_id}"}}',
                status_code=403,
                media_type="application/json",)

        # Rate limiting (prevent noisy neighbor)
        if not self._limiter.is_allowed(tenant_id, tenant_info):
            return Response(content='{"error": "Rate limit exceeded"}', status_code=429, media_type="application/json")

        # Set tenant context — available in all downstream handlers via get_current_tenant_id()
        set_current_tenant_id(tenant_id)

        response = await call_next(request)

        # Clean up context
        set_current_tenant_id(None)

        return response


# ----  Row-Level Security pattern ----

def apply_tenant_filter(query: str, params: dict[str, object] | None = None) -> tuple[str, dict[str, object]]:
    """Automatically append tenant_id filter to SQL queries.

    In production, this would be integrated into SQLAlchemy's
    before_compile event or custom BaseQuery to enforce isolation.

    Usage:
        query, params = apply_tenant_filter("SELECT * FROM tax_records WHERE id = :id", {"id": 1})
        # query → "SELECT * FROM tax_records WHERE id = :id AND tenant_id = :tenant_id"
    """
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        raise RuntimeError("No tenant context — ensure TenantMiddleware is active")

    params = params or {}
    params["tenant_id"] = tenant_id
    return f"{query} AND tenant_id = :tenant_id", params
