""" Tool management with dependency injection.

Demonstrates:
- RESTful API design for tool CRUD
- FastAPI Depends() for service injection
- Tool discovery (GET /tools) and dynamic registration (POST /tools)
"""

from __future__ import annotations

import time
from typing import Any

from tax_agent_platform.models import ToolCreate, ToolInfo


class ToolRegistry:
    """In-memory tool registry — supports tool discovery and dynamic registration.

    In production, this would be backed by a database.
    The registry is injected via FastAPI Depends(), making it easy to swap
    for a database-backed implementation in tests.
    """

    def __init__(self, seed_defaults: bool = True) -> None:
        self._tools: dict[int, dict[str, Any]] = {}
        self._counter: int = 0
        if seed_defaults:
            self._seed_default_tools()

    def _seed_default_tools(self) -> None:
        """Pre-register example tools for demo purposes."""
        defaults = [
            ToolCreate(tool_name="calculate_tax",
                description="Calculate tax amount based on income, deductions, and applicable tax brackets",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "income": {"type": "number", "description": "Annual income"},
                        "deductions": {"type": "number", "description": "Total deductions"},
                        "year": {"type": "integer", "description": "Tax year"},
                    },
                    "required": ["income", "year"],
                },
                endpoint_url="http://localhost:8001/tools/calculate_tax/call",),
            ToolCreate(tool_name="search_regulation",
                description="Search tax regulations by keyword, category, and effective date range",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "Search keyword"},
                        "category": {"type": "string", "enum": ["VAT", "CIT", "IIT", "Stamp Duty"]},
                        "year_from": {"type": "integer"},
                        "year_to": {"type": "integer"},
                    },
                    "required": ["keyword"],
                },
                endpoint_url="http://localhost:8001/tools/search_regulation/call",),
            ToolCreate(tool_name="validate_invoice",
                description="Validate invoice format, tax ID, and compliance with local tax regulations",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "invoice_number": {"type": "string"},
                        "tax_id": {"type": "string"},
                        "amount": {"type": "number"},
                    },
                    "required": ["invoice_number", "tax_id"],
                },
                endpoint_url="http://localhost:8001/tools/validate_invoice/call",),
            ToolCreate(tool_name="generate_report",
                description="Generate compliance or audit report for a given tax period and entity",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string"},
                        "period_start": {"type": "string", "format": "date"},
                        "period_end": {"type": "string", "format": "date"},
                        "report_type": {"type": "string", "enum": ["compliance", "audit", "summary"]},
                    },
                    "required": ["entity_id", "period_start", "period_end"],
                },
                endpoint_url="http://localhost:8001/tools/generate_report/call",),
        ]
        for tool in defaults:
            self.register(tool)

    def register(self, req: ToolCreate) -> ToolInfo:
        """Register a new tool. Raises ValueError if tool_name already exists."""
        for tool in self._tools.values():
            if tool["tool_name"] == req.tool_name:
                raise ValueError(f"Tool '{req.tool_name}' already registered")

        self._counter += 1
        record = {
            "id": self._counter,
            "tool_name": req.tool_name,
            "description": req.description,
            "parameters_schema": req.parameters_schema,
            "endpoint_url": req.endpoint_url,
            "created_at": time.time(),
        }
        self._tools[self._counter] = record
        return ToolInfo(id=record["id"],
            tool_name=record["tool_name"],
            description=record["description"],
            created_at=record["created_at"],)

    def list_tools(self) -> list[ToolInfo]:
        """List all registered tools — supports Agent tool discovery."""
        return [
            ToolInfo(id=t["id"], tool_name=t["tool_name"], description=t["description"], created_at=t["created_at"])
            for t in self._tools.values()
        ]

    def get_tool(self, tool_name: str) -> ToolInfo | None:
        """Find a tool by name — for LLM to locate tools at runtime."""
        for t in self._tools.values():
            if t["tool_name"] == tool_name:
                return ToolInfo(id=t["id"], tool_name=t["tool_name"], description=t["description"], created_at=t["created_at"])
        return None

    def get_parameters_schema(self, tool_name: str) -> dict[str, Any] | None:
        """Get the JSON Schema for a tool's parameters — for LLM function calling."""
        for t in self._tools.values():
            if t["tool_name"] == tool_name:
                return t["parameters_schema"]
        return None


# Per-tenant registries (data isolation). Each tenant gets its own
# ToolRegistry (seeded with the default demo tools), so a tool registered
# under one tenant is invisible to others. Keyed by tenant id, with
# "_shared" used when no tenant context is present (tests, tooling).
_registries: dict[str, ToolRegistry] = {}


def get_tool_registry() -> ToolRegistry:
    """FastAPI dependency: lazy-initialized, per-tenant ToolRegistry."""
    from tax_agent_platform.middleware.tenant import get_current_tenant_id

    key = get_current_tenant_id() or "_shared"
    if key not in _registries:
        _registries[key] = ToolRegistry()
    return _registries[key]
