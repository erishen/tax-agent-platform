""" Multi-tenant data isolation — the real thing, not decoration.

These tests prove that RAG corpora and tool registries are genuinely
separated per tenant, and that the SQL Row-Level-Security helper actually
scopes queries. They guard against regressions back to the old shared-global
singletons where every tenant saw identical data.
"""

from __future__ import annotations

import tax_agent_platform.app as app_mod
from tax_agent_platform.middleware.tenant import (apply_tenant_filter,
    set_current_tenant_id,)
from tax_agent_platform.models import ToolCreate
from tax_agent_platform.tools.registry import _registries, get_tool_registry


def _reset_caches() -> None:
    app_mod._rag_pipelines.clear()
    _registries.clear()


# ---- RAG per-tenant isolation ----

def test_rag_pipeline_is_per_tenant_and_isolated():
    """acme-us has its own corpus dir → different docs from the shared default."""
    _reset_caches()
    try:
        set_current_tenant_id("acme-us")
        us = app_mod.get_rag_pipeline()
        us_ids = set(us.store._docs.keys())

        set_current_tenant_id("acme-cn")
        cn = app_mod.get_rag_pipeline()
        cn_ids = set(cn.store._docs.keys())

        # acme-us loads its private corpus (US federal doc present, CN docs absent)
        assert "us-federal-cit-2024" in us_ids
        assert "vat-2024" not in us_ids
        # acme-cn falls back to the shared corpus (CN docs present, US doc absent)
        assert "vat-2024" in cn_ids
        assert "us-federal-cit-2024" not in cn_ids
        # Different tenants get genuinely different knowledge bases
        assert us is not cn
        assert us_ids != cn_ids
    finally:
        set_current_tenant_id(None)
        _reset_caches()


def test_rag_reload_only_affects_calling_tenant():
    """Reloading one tenant must not rebuild another tenant's pipeline."""
    _reset_caches()
    try:
        set_current_tenant_id("acme-cn")
        cn_before = app_mod.get_rag_pipeline()

        set_current_tenant_id("acme-us")
        us_before = app_mod.get_rag_pipeline()
        app_mod.reload_rag_pipeline()  # reload acme-us only
        us_after = app_mod.get_rag_pipeline()

        assert us_after is not us_before  # acme-us rebuilt

        set_current_tenant_id("acme-cn")
        cn_after = app_mod.get_rag_pipeline()
        assert cn_after is cn_before  # acme-cn untouched
    finally:
        set_current_tenant_id(None)
        _reset_caches()


# ---- ToolRegistry per-tenant isolation ----

def test_tool_registry_is_per_tenant_and_isolated():
    """A tool registered under one tenant is invisible to another."""
    _reset_caches()
    try:
        set_current_tenant_id("acme-cn")
        reg_cn = get_tool_registry()
        reg_cn.register(ToolCreate(tool_name="cn_only_tool",
            description="A tool only ACME China registers",
            parameters_schema={"type": "object", "properties": {}},
            endpoint_url="http://localhost:8001/tools/cn_only_tool/call",))
        assert reg_cn.get_tool("cn_only_tool") is not None

        set_current_tenant_id("acme-us")
        reg_us = get_tool_registry()
        # Different registry instance; the CN-only tool must not leak here
        assert reg_us is not reg_cn
        assert reg_us.get_tool("cn_only_tool") is None
        # But each tenant still has its own seeded defaults
        assert reg_us.get_tool("calculate_tax") is not None
    finally:
        set_current_tenant_id(None)
        _reset_caches()


# ---- Workflow thread offload propagates tenant context ----

def test_run_workflow_in_thread_propagates_tenant():
    """The worker-thread runner must re-apply the tenant contextvar.

    loop.run_in_executor does NOT copy the request's contextvars, so without
    re-setting it inside the thread, agents calling get_current_tenant_id()
    would see None and log/evidence the wrong tenant.
    """
    from tax_agent_platform.app import _run_workflow_in_thread

    captured: dict[str, object] = {}

    class FakeWorkflow:
        async def run(self, goal, on_event=None):
            from tax_agent_platform.middleware.tenant import get_current_tenant_id

            tid = get_current_tenant_id()
            captured["tenant"] = tid
            return {"ok": True, "tenant": tid}

    set_current_tenant_id(None)
    try:
        result = _run_workflow_in_thread(FakeWorkflow(), "goal", "acme-cn")
        assert result["tenant"] == "acme-cn"
        assert captured["tenant"] == "acme-cn"
    finally:
        set_current_tenant_id(None)


# ---- SQL Row-Level-Security helper (no longer dead code) ----

def test_apply_tenant_filter_scopes_query():
    try:
        set_current_tenant_id("demo")
        query, params = apply_tenant_filter("SELECT * FROM tax_records WHERE id = :id", {"id": 1})
        assert query.endswith("AND tenant_id = :tenant_id")
        assert params == {"id": 1, "tenant_id": "demo"}
    finally:
        set_current_tenant_id(None)


def test_apply_tenant_filter_requires_context():
    import pytest

    set_current_tenant_id(None)
    with pytest.raises(RuntimeError):
        apply_tenant_filter("SELECT 1")
