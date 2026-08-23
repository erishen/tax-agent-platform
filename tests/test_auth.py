"""Gateway-level tenant API-key auth (hardening).

Verifies that tenant identity is DERIVED from a credential, never from a
client-claimed X-Tenant-ID, and that missing/invalid keys are rejected.
"""

import pytest
from fastapi.testclient import TestClient

from tax_agent_platform.app import app
from tax_agent_platform.config import settings

KEY_CN = "tax_demo_acme_cn_8f3a"
KEY_US = "tax_demo_acme_us_2b9e"
KEYS = {"acme-cn": KEY_CN, "acme-us": KEY_US}


@pytest.fixture
def client():
    return TestClient(app)


def test_missing_key_returns_401(client):
    """With auth required (default), no key at all => 401."""
    r = client.get("/tenant/me")
    assert r.status_code == 401
    assert "API key required" in r.json()["error"]


def test_invalid_key_returns_401(client, monkeypatch):
    monkeypatch.setattr(settings, "tenant_api_keys", KEYS)
    r = client.get("/tenant/me", headers={"X-API-Key": "not-a-real-key"})
    assert r.status_code == 401
    assert "Invalid API key" in r.json()["error"]


def test_valid_key_returns_200_and_derives_tenant(client, monkeypatch):
    """A valid key resolves to its tenant; /tenant/me echoes it."""
    monkeypatch.setattr(settings, "tenant_api_keys", KEYS)
    r = client.get("/tenant/me", headers={"X-API-Key": KEY_CN})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "acme-cn"


def test_spoofed_x_tenant_id_is_ignored(client, monkeypatch):
    """Claiming acme-us via header must NOT override the key's tenant (acme-cn)."""
    monkeypatch.setattr(settings, "tenant_api_keys", KEYS)
    r = client.get("/tenant/me",
        headers={"X-API-Key": KEY_CN, "X-Tenant-ID": "acme-us"},)
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "acme-cn"


def test_bearer_scheme_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "tenant_api_keys", KEYS)
    r = client.get("/tenant/me", headers={"Authorization": f"Bearer {KEY_US}"})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "acme-us"


def test_unknown_tenant_after_auth_returns_403(client, monkeypatch):
    """Key maps to a tenant that does not exist in the tenant store => 403."""
    monkeypatch.setattr(settings, "tenant_api_keys", {"ghost": "ghost-key"})
    r = client.get("/tenant/me", headers={"X-API-Key": "ghost-key"})
    assert r.status_code == 403


def test_legacy_fallback_when_auth_disabled(client, monkeypatch):
    """With auth_required=False, the legacy client-claimed X-Tenant-ID still works."""
    monkeypatch.setattr(settings, "auth_required", False)
    r = client.get("/tenant/me", headers={"X-Tenant-ID": "acme-cn"})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "acme-cn"


def test_health_is_public_without_key(client):
    """/health stays unauthenticated."""
    r = client.get("/health")
    assert r.status_code == 200


def test_rag_documents_accessible_under_valid_key(client, monkeypatch):
    """A real data endpoint returns 200 once authenticated via key."""
    monkeypatch.setattr(settings, "tenant_api_keys", KEYS)
    r = client.get("/rag/documents", headers={"X-API-Key": KEY_CN})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
