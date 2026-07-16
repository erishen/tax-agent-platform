"""Q1: Pydantic models — clear code, type hints, testability.

Key practices demonstrated:
1. Static type checking via Pydantic validation (not manual data checks)
2. Explicit input/output contracts → auto-generated Swagger docs
3. Testability via quick mock construction (e.g. ToolCreate(**valid_data))
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---- Q4: Dashboard async models ----

class UserProfile(BaseModel):
    name: str
    role: str


class TaxRecord(BaseModel):
    id: int
    status: str


class Notification(BaseModel):
    message: str


class DashboardResponse(BaseModel):
    """Unified response model for async dashboard endpoint (Q4)."""
    profile: UserProfile | None = None
    recent_records: list[TaxRecord] | None = None
    notifications: list[Notification] | None = None


# ---- Q6: Tool management models ----

class ToolCreate(BaseModel):
    """Request model for registering a new Agent tool (Q6)."""
    tool_name: str = Field(..., description="Unique tool identifier, e.g. 'tax_calculator'")
    description: str = Field(..., description="Tool description for LLM to decide when to call")
    parameters_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema of tool parameters",
    )
    endpoint_url: str = Field(..., description="Microservice URL that executes this tool")


class ToolInfo(BaseModel):
    """Response model for a registered tool (Q6)."""
    id: int
    tool_name: str
    description: str
    created_at: float = Field(default_factory=time.time)


# ---- Q7: LLM structured output models ----

class LLMMessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class LLMRequest(BaseModel):
    messages: list[dict[str, str]]
    model: str | None = None
    temperature: float = 0.0
    max_tokens: int = 2048


class GuardrailResult(BaseModel):
    """Result of safety guardrail check (Q7)."""
    passed: bool
    reason: str | None = None
    category: str | None = None  # e.g. "prompt_injection", "sensitive_data", "off_topic"


# ---- Q9: Agent framework models ----

class Verdict(str, Enum):
    """Evaluator verdict types (Q9)."""
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"


class AgentRunResult(BaseModel):
    """Base result from any agent execution (Q9)."""
    agent_name: str
    status: str  # "success" | "failure"
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    output: str = ""


class CriterionVerification(BaseModel):
    """Independent verification of a single acceptance criterion (Q9)."""
    criterion: str
    verdict: str  # SUPPORTED | CONTRADICTED | UNVERIFIED
    reason: str = ""


class EvaluationResult(BaseModel):
    """Structured evaluation returned by the Evaluator LLM (Q9).

    The Evaluator does NOT trust the Specialist's self-assessment: it
    independently re-collects evidence and asks the LLM to audit each
    acceptance criterion against that evidence.
    """
    criteria: list[CriterionVerification] = Field(default_factory=list)
    overall_verdict: str = "PARTIAL"  # PASS | PARTIAL | FAIL
    independent_evidence: list[str] = Field(default_factory=list)


class WorkflowStepConfig(BaseModel):
    """Configuration for a workflow step (Q9)."""
    agent_name: str
    route_on_success: str | None = None
    route_on_failure: str | None = None


class WorkflowConfig(BaseModel):
    """Full workflow definition (Q9)."""
    name: str
    steps: list[WorkflowStepConfig]
    entry_step: str


# ---- Q7/Q9: LLM structured plan output (matches PlannerAgent prompt) ----

class PlanStep(BaseModel):
    """A single planned execution step produced by the Planner LLM (Q7)."""
    step_id: int
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)


class PlanConfig(BaseModel):
    """Structured plan returned by the Planner LLM (Q7).

    Field names deliberately match the JSON contract in PlannerAgent's prompt
    (steps[].step_id / description / acceptance_criteria, overall_criteria)
    so Pydantic validation of the LLM output succeeds instead of always
    falling back to rule-based planning.
    """
    steps: list[PlanStep]
    overall_criteria: list[str] = Field(default_factory=list)


# ---- Q8: Multi-tenancy models ----

class TenantInfo(BaseModel):
    """Tenant metadata for multi-tenancy (Q8)."""
    tenant_id: str
    name: str
    plan: str = "standard"  # standard | premium | enterprise
    rate_limit: int = 100  # requests per minute
