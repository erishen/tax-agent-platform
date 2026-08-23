""" Tool management API routes with dependency injection."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from tax_agent_platform.models import ToolCreate, ToolInfo
from tax_agent_platform.tools.registry import ToolRegistry, get_tool_registry

router = APIRouter(prefix="/tools", tags=["AI Tools"])


@router.post("/", response_model=ToolInfo, summary="Register a new Agent tool")
async def register_tool(request: ToolCreate,
    service: ToolRegistry = Depends(get_tool_registry),) -> ToolInfo:
    """Dynamically register a new tool for Agent use.

    LLM discovers tools via GET /tools and understands when to call them
    from the `description` and `parameters_schema` fields.
    """
    try:
        return service.register(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/", response_model=list[ToolInfo], summary="List all available tools")
async def list_tools(service: ToolRegistry = Depends(get_tool_registry),) -> list[ToolInfo]:
    """Return all registered tools.

    Agent orchestration frameworks call this to build System Prompt
    tool definitions for LLM function calling.
    """
    return service.list_tools()
