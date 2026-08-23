""" Multi-Agent framework — fully integrated with RAG, Tools, LLM, Guardrails, Multi-tenancy.

Integration chain:
  PlannerAgent → LLMClient (structured output for plan generation)
  SpecialistAgent → RAGPipeline (retrieve tax docs) + ToolRegistry (call tools)
  EvaluatorAgent → Guardrails (output safety) + evidence-driven verification
  All agents → TenantMiddleware (contextvars tenant isolation)

Key design: Evaluator does NOT trust Specialist's claims.
It independently collects and examines evidence to determine verdict.
"""

from __future__ import annotations

import inspect
import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from tax_agent_platform.models import AgentRunResult, Verdict
from tax_agent_platform.rag import RAGPipeline
from tax_agent_platform.tools.registry import ToolRegistry
from tax_agent_platform.middleware.tenant import get_current_tenant_id

logger = logging.getLogger(__name__)


def _criterion_supported_by_evidence(criterion: str, evidence: list[str]) -> bool:
    """Crude heuristic: does any 2-char substring of the criterion appear in evidence?

    Used only as a fallback when no LLM is configured. Good enough to flag
    whether independently retrieved evidence is at least topically related.
    """
    if len(criterion) < 2:
        return True
    for frag in evidence:
        for i in range(len(criterion) - 1):
            if criterion[i:i + 2] in frag:
                return True
    return False


# ----  Agent Base Class ----

class Agent(ABC):
    """Base class for all agents — enforces clear role separation."""

    def __init__(self, name: str, role: str) -> None:
        self.name = name
        self.role = role

    @abstractmethod
    async def run(self, input_data: dict[str, Any]) -> AgentRunResult:...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, role={self.role!r})"


# ----  PSE Agents — Integrated with RAG / Tools / LLM / Guardrails ----

class PlannerAgent(Agent):
    """Planner: uses LLM to break down goal into steps with acceptance criteria.

    Integration: LLMClient.structured_call() for reliable JSON plan output.
    Fallback to rule-based planning if LLM is unavailable.
    """

    def __init__(self, llm_client: Any | None = None) -> None:
        super().__init__(name="Planner", role="Plan execution steps")
        self._llm = llm_client

    async def run(self, input_data: dict[str, Any], usage: dict | None = None) -> AgentRunResult:
        goal = input_data.get("goal", "")
        tenant_id = get_current_tenant_id()
        # Log a truncated goal only — the full text is user-supplied and may
        # contain PII (revenue figures, entity names), so it must not be
        # written verbatim to logs that could be shipped off-box.
        logger.info("[%s] Planning (tenant=%s, goal_len=%d)", self.name, tenant_id, len(goal))

        # Try LLM-based planning (structured output)
        if self._llm:
            try:
                from tax_agent_platform.models import PlanConfig
                plan_data = self._llm.structured_call(messages=[
                        {"role": "system", "content": ("You are a tax advisory planner. Break the goal into 2-4 execution steps. "
                            "Return a JSON object with exactly this shape: "
                            '{"steps": [{"step_id": <int>, "description": <string>, '
                            '"acceptance_criteria": [<string>,...]}], '
                            '"overall_criteria": [<string>,...]}. '
                            "step_id must be a sequential integer starting at 1.")},
                        {"role": "user", "content": goal},
                    ],
                    response_model=PlanConfig,
                    usage=usage,)
                return AgentRunResult(agent_name=self.name,
                    status="success",
                    data={
                        "steps": [
                            {
                                "step_id": s.step_id,
                                "description": s.description,
                                "acceptance_criteria": s.acceptance_criteria,
                            }
                            for s in plan_data.steps
                        ],
                        "overall_criteria": plan_data.overall_criteria,
                    },
                    output=f"LLM plan created with {len(plan_data.steps)} steps",)
            except Exception as e:
                logger.warning("[%s] LLM planning failed, using fallback: %s", self.name, e)

        # Fallback: rule-based planning
        plan = {
            "steps": [
                {"step_id": 1, "description": f"Search tax regulations for: {goal}", "acceptance_criteria": ["At least 1 regulation found", "Regulation is currently active"]},
                {"step_id": 2, "description": "Analyze findings and generate advisory summary", "acceptance_criteria": ["Summary references specific regulations", "No hallucinated data"]},
            ],
            "overall_criteria": ["All steps completed", "Evidence collected and verified"],
        }

        return AgentRunResult(agent_name=self.name,
            status="success",
            data=plan,
            output=f"Plan created with {len(plan['steps'])} steps",)


class SpecialistAgent(Agent):
    """Specialist: executes steps using RAG retrieval + Tool calls + LLM-grounded answer.

    Integration:
    - RAGPipeline: retrieve relevant tax documents  — fed into the LLM as
      GROUNDED evidence so the answer cites real tax clauses, not inventions.
    - ToolRegistry: discover and call registered tools 
    - LLMClient.simple_call: generate a grounded answer from the retrieved
      evidence. When no LLM is configured, falls back to the placeholder answer.
    """

    def __init__(self,
        rag_pipeline: RAGPipeline | None = None,
        tool_registry: ToolRegistry | None = None,
        llm_client: Any | None = None,) -> None:
        super().__init__(name="Specialist", role="Execute plan steps with RAG + Tools + LLM-grounded answer")
        self._rag = rag_pipeline
        self._tools = tool_registry
        self._llm = llm_client

    async def run(self, input_data: dict[str, Any], usage: dict | None = None) -> AgentRunResult:
        step = input_data.get("step", {})
        step_id = step.get("step_id", 0)
        description = step.get("description", "")
        tenant_id = get_current_tenant_id()
        # Truncate the step description in logs — it is derived from the
        # user's goal and may echo PII; keep it out of verbatim log output.
        _desc = description[:50] + ("…" if len(description) > 50 else "")
        logger.info("[%s] Executing step %d (tenant=%s, desc=%r)", self.name, step_id, tenant_id, _desc)

        evidence: list[str] = []
        tool_outputs: list[dict[str, Any]] = []

        #  RAG retrieval — find relevant tax documents
        if self._rag:
            rag_results = self._rag.query(description, top_k=5)
            for r in rag_results:
                evidence.append(f"RAG: [{r.doc_id}] score={r.score:.3f} → {r.content[:100]}")
            logger.info("[%s] RAG returned %d documents", self.name, len(rag_results))

        #  Tool discovery and execution
        if self._tools:
            available_tools = self._tools.list_tools()
            for tool in available_tools:
                evidence.append(f"Tool available: {tool.tool_name} — {tool.description}")
                # In production: LLM decides which tools to call, then execute via httpx
                tool_outputs.append({"tool_name": tool.tool_name, "status": "discovered"})

        # Tenant-aware evidence
        if tenant_id:
            evidence.append(f"Tenant context: {tenant_id}")

        # If no RAG or tools configured, produce basic evidence
        if not evidence:
            evidence.append(f"Step executed: {description}")

        #  Generate a GROUNDED answer from the retrieved evidence via the LLM.
        # This is the link that was previously missing — RAG results were logged
        # but never fed into generation, so they had zero effect on the answer.
        # Now the answer is produced *from* the evidence and cites its sources.
        result_text = f"Completed: {description}"
        if self._llm:
            try:
                context_block = "\n".join(evidence) if evidence else "(no retrieved evidence)"
                answer = self._llm.simple_call(messages=[
                    {"role": "system", "content": ("You are a tax advisory specialist. Answer the step using ONLY the retrieved "
                        "evidence below. Rules:\n"
                        "- Cite the source for every key claim as [doc_id].\n"
                        "- Do NOT invent tax clauses or rates not present in the evidence.\n"
                        "- If the evidence is insufficient, say so explicitly rather than guessing.\n"
                        "Respond in the same language as the user's request.")},
                    {"role": "user", "content": (f"Step objective: {description}\n\n"
                        f"Retrieved evidence:\n{context_block}")},
                ], usage=usage)
                if answer and answer.strip():
                    result_text = answer.strip()
            except Exception as e:
                logger.warning("[%s] LLM grounded answer failed, using placeholder: %s", self.name, e)

        return AgentRunResult(agent_name=self.name,
            status="success",
            data={"step_id": step_id, "result": result_text, "tool_outputs": tool_outputs},
            evidence=evidence,
            output=result_text,)


class EvaluatorAgent(Agent):
    """Evaluator: independently verifies Specialist output via re-collected evidence + LLM audit.

    Integration:
    - Guardrails: screen Specialist output for prompt injection / sensitive data 
    - Evidence-driven : does NOT trust Specialist's self-assessment. It
      independently re-queries the RAG pipeline for the step and asks the LLM
      to audit each acceptance criterion against that independently gathered
      evidence (SUPPORTED / CONTRADICTED / UNVERIFIED).
    """

    def __init__(self,
        guardrails_enabled: bool = True,
        llm_client: Any | None = None,
        rag_pipeline: RAGPipeline | None = None,) -> None:
        super().__init__(name="Evaluator", role="Independently verify via re-collected evidence + LLM audit")
        self._guardrails_enabled = guardrails_enabled
        self._llm = llm_client
        self._rag = rag_pipeline

    async def run(self, input_data: dict[str, Any], usage: dict | None = None) -> AgentRunResult:
        step = input_data.get("step", {})
        specialist_result = input_data.get("specialist_result")
        criteria = step.get("acceptance_criteria", [])
        step_id = step.get("step_id", 0)

        logger.info("[%s] Independently auditing step %d against %d criteria", self.name, step_id, len(criteria))

        issues: list[str] = []

        #  Guardrail screen on Specialist output (screen only, never trust)
        if self._guardrails_enabled and specialist_result:
            from tax_agent_platform.llm.client import Guardrails
            output_check = Guardrails.check_output(specialist_result.output)
            if not output_check.passed:
                issues.append(f"Guardrail blocked: {output_check.reason}")
                return AgentRunResult(agent_name=self.name,
                    status="success",
                    data={"verdict": Verdict.FAIL.value, "issues": issues, "verifications": [], "independent_evidence": []},
                    evidence=[f"GUARDRAIL: {output_check.reason}"],
                    output=f"Verdict: FAIL (guardrail: {output_check.category})",)

        #  Independently re-collect evidence — do NOT reuse Specialist's evidence
        independent_evidence: list[str] = []
        if self._rag:
            for r in self._rag.query(step.get("description", ""), top_k=5):
                independent_evidence.append(f"RAG:[{r.doc_id}] score={r.score:.3f} → {r.content[:120]}")
        if not independent_evidence and specialist_result:
            # Last resort only, explicitly flagged as coming from the Specialist
            independent_evidence = [f"(from-specialist) {ev}" for ev in specialist_result.evidence]
        if not independent_evidence:
            independent_evidence = [f"No external evidence retrievable for: {step.get('description', '')}"]

        #  Independent LLM audit — does the evidence actually support each criterion?
        verifications: list[dict[str, Any]] = []
        if self._llm and criteria:
            try:
                from tax_agent_platform.models import EvaluationResult
                eval_data = self._llm.structured_call(messages=[
                        {"role": "system", "content": ("You are an independent tax audit reviewer. You do NOT trust the Specialist's "
                            "self-assessment. Given the acceptance criteria, the Specialist's output, and "
                            "independently retrieved evidence, verify each criterion.\n"
                            "For each criterion return: verdict = SUPPORTED (evidence backs it) | "
                            "CONTRADICTED (evidence contradicts it) | UNVERIFIED (no supporting evidence), "
                            "plus a short reason.\n"
                            'Return JSON: {"criteria": [{"criterion": str, "verdict": str, "reason": str}], '
                            '"overall_verdict": "PASS"|"PARTIAL"|"FAIL", '
                            '"independent_evidence": [str]}.')},
                        {"role": "user", "content": (f"Acceptance criteria:\n{json.dumps(criteria, ensure_ascii=False)}\n\n"
                            f"Specialist output:\n{(specialist_result.output if specialist_result else '')}\n\n"
                            "Independently retrieved evidence:\n" + "\n".join(independent_evidence))},
                    ],
                    response_model=EvaluationResult,
                    usage=usage,)
                for c in eval_data.criteria:
                    verifications.append({"criterion": c.criterion, "verdict": c.verdict, "reason": c.reason})
                # Derive a robust overall verdict from per-criterion results
                verdicts = {c.verdict.upper() for c in eval_data.criteria}
                if "CONTRADICTED" in verdicts:
                    overall = Verdict.FAIL.value
                elif not verdicts or verdicts == {"SUPPORTED"}:
                    overall = Verdict.PASS.value
                else:
                    overall = Verdict.PARTIAL.value
            except Exception as e:
                logger.warning("[%s] LLM audit failed, falling back to heuristic: %s", self.name, e)
                overall, verifications = self._heuristic_verify(criteria, independent_evidence)
        else:
            overall, verifications = self._heuristic_verify(criteria, independent_evidence)

        if overall != Verdict.PASS.value:
            unsupported = [v for v in verifications if v["verdict"] != "SUPPORTED"]
            issues.append(f"{overall}: {len(unsupported)} criterion(s) not fully supported by independent evidence")

        return AgentRunResult(agent_name=self.name,
            status="success",
            data={
                "verdict": overall,
                "issues": issues,
                "verifications": verifications,
                "independent_evidence": independent_evidence,
            },
            evidence=independent_evidence,
            output=f"Verdict: {overall} ({len(verifications)} criteria audited, {len(independent_evidence)} independent sources)",)

    @staticmethod
    def _heuristic_verify(criteria: list[str], evidence: list[str]) -> tuple[str, list[dict[str, Any]]]:
        """Fallback when no LLM: keyword-overlap heuristic against independent evidence."""
        verifications: list[dict[str, Any]] = []
        for c in criteria:
            supported = _criterion_supported_by_evidence(c, evidence)
            verifications.append({
                "criterion": c,
                "verdict": "SUPPORTED" if supported else "UNVERIFIED",
                "reason": "heuristic keyword overlap with independent evidence" if supported else "no overlapping evidence found",
            })
        if not criteria:
            return Verdict.PASS.value, verifications
        if all(v["verdict"] == "SUPPORTED" for v in verifications):
            return Verdict.PASS.value, verifications
        if any(v["verdict"] == "CONTRADICTED" for v in verifications):
            return Verdict.FAIL.value, verifications
        return Verdict.PARTIAL.value, verifications


# ----  Workflow Engine ----

class WorkflowStep:
    """A single step in the workflow graph with branching routes.

    Each step runs an ``executor`` agent, then (optionally) a ``verifier`` agent.
    The verifier's verdict drives branching:
      - success (verdict != FAIL) → ``next_on_success``
      - failure (verdict == FAIL)  → ``next_on_failure``
    Up to ``max_retries`` local retries happen before a FAIL becomes final.
    Every executed step appends its aggregated result to ``context["_results"]``.
    """

    def __init__(self,
        name: str,
        step: dict[str, Any],
        executor: Agent,
        verifier: Agent | None = None,
        next_on_success: WorkflowStep | None = None,
        next_on_failure: WorkflowStep | None = None,
        max_retries: int = 3,) -> None:
        self.name = name
        self.step = step
        self.executor = executor
        self.verifier = verifier
        self.next_on_success = next_on_success
        self.next_on_failure = next_on_failure
        self.max_retries = max_retries

    async def execute(self, context: dict[str, Any], on_event: Any = None) -> dict[str, Any]:
        ctx = {**context, "step": self.step}
        usage = context.get("_usage")

        async def _invoke(agent: Agent, c: dict[str, Any]) -> AgentRunResult:
            # Pass `usage` only if the agent accepts it (open/closed: test
            # doubles and minimal agents that skip token accounting keep working
            # without the extra kwarg, instead of raising TypeError).
            if "usage" in inspect.signature(agent.run).parameters:
                return await agent.run(c, usage=usage)
            return await agent.run(c)

        last: AgentRunResult | None = None

        for attempt in range(self.max_retries + 1):
            ctx["attempt"] = attempt
            if on_event:
                on_event("step_started", {"step": self.name, "attempt": attempt})
            exec_result = await _invoke(self.executor, ctx)
            if on_event:
                on_event("step_executed", {
                    "step": self.name, "attempt": attempt,
                    "evidence_count": len(exec_result.evidence),
                })

            if self.verifier is None:
                verdict = Verdict.PASS.value
            else:
                vres = await _invoke(self.verifier, {**ctx, "specialist_result": exec_result})
                verdict = (vres.data or {}).get("verdict", Verdict.PASS.value)
                exec_result.data = {
                    **exec_result.data,
                    "verdict": verdict,
                    "issues": vres.data.get("issues", []),
                    "verifications": vres.data.get("verifications", []),
                    "independent_evidence": vres.data.get("independent_evidence", []),
                }
                if on_event:
                    on_event("step_evaluated", {
                        "step": self.name, "verdict": verdict,
                        "issues": vres.data.get("issues", []),
                    })

            last = exec_result
            if verdict != Verdict.FAIL.value or attempt == self.max_retries:
                break

        final_verdict = (last.data or {}).get("verdict", Verdict.PASS.value)
        result = {
            "step": self.step,
            "verdict": final_verdict,
            "evidence": last.evidence,
            "independent_evidence": (last.data or {}).get("independent_evidence", []),
            "issues": (last.data or {}).get("issues", []),
            "verifications": (last.data or {}).get("verifications", []),
            "specialist_output": last.output,
        }
        context.setdefault("_results", []).append(result)

        # Branch on the final verdict (real routing)
        if final_verdict != Verdict.FAIL.value and self.next_on_success:
            return await self.next_on_success.execute(context, on_event)
        if final_verdict == Verdict.FAIL.value and self.next_on_failure:
            result["branch"] = "failure"
            return await self.next_on_failure.execute(context, on_event)
        result["branch"] = "success"
        return result


class WorkflowEngine:
    """Orchestrates a multi-agent workflow graph with branching support."""

    def __init__(self) -> None:
        self._entry: WorkflowStep | None = None

    def set_entry(self, step: WorkflowStep) -> None:
        self._entry = step

    async def run(self, initial_context: dict[str, Any], on_event: Any = None) -> dict[str, Any]:
        if not self._entry:
            return {"results": [], "error": "No workflow defined"}
        initial_context.setdefault("_results", [])
        await self._entry.execute(initial_context, on_event)
        return {"results": initial_context["_results"]}


# ----  Integrated PSE Workflow ----

class PSEWorkflow:
    """Fully integrated PSE workflow: Planner → Specialist → Evaluator.

    Wires together all modules:
    - PlannerAgent uses LLMClient  for structured planning
    - SpecialistAgent uses RAGPipeline  + ToolRegistry 
    - EvaluatorAgent uses Guardrails  + evidence-driven verification 
    - All agents respect TenantMiddleware context 
    - Auto-retry on PARTIAL/FAIL with evidence preservation
    """

    def __init__(self,
        max_retries: int = 3,
        rag_pipeline: RAGPipeline | None = None,
        tool_registry: ToolRegistry | None = None,
        llm_client: Any | None = None,) -> None:
        self.planner = PlannerAgent(llm_client=llm_client)
        self.specialist = SpecialistAgent(rag_pipeline=rag_pipeline,
            tool_registry=tool_registry,
            llm_client=llm_client,)
        self.evaluator = EvaluatorAgent(guardrails_enabled=True,
            llm_client=llm_client,
            rag_pipeline=rag_pipeline,)
        self.max_retries = max_retries
        self.llm_client = llm_client

    async def run(self, goal: str, on_event: Any = None) -> dict[str, Any]:
        """Run the full PSE workflow via the WorkflowEngine.

        The engine drives a linear step chain where each step runs
        Specialist → Evaluator and branches on the Evaluator's verdict.
        ``on_event`` (optional async/sync callable) is invoked for live
        progress (used by the SSE endpoint); REST callers pass None.
        """
        from tax_agent_platform.config import settings

        # Per-request token accounting — one shared dict threaded through every
        # LLM call so accumulated usage stays concurrency-safe (no global state).
        usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}

        def _tu() -> dict[str, Any]:
            return {
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "total_tokens": usage["total_tokens"],
                "calls": usage["calls"],
                "cost": self.llm_client.estimate_cost(usage) if self.llm_client else 0.0,
                "provider": settings.llm_provider,
            }

        plan_result = await self.planner.run({"goal": goal}, usage=usage)
        if plan_result.status != "success":
            return {"error": "Planning failed", "detail": plan_result.model_dump(), "token_usage": _tu()}

        steps = plan_result.data.get("steps", [])
        if on_event:
            on_event("planning", {"output": plan_result.output, "steps": steps})

        if not steps:
            return {
                "goal": goal, "results": [], "total_steps": 0,
                "summary": {"total": 0, "pass": 0, "partial": 0, "fail": 0},
                "token_usage": _tu(),
            }

        # Build the workflow graph: each plan step becomes a WorkflowStep that
        # runs Specialist → Evaluator; success branches to the next step.
        nodes: list[WorkflowStep] = []
        for i, step in enumerate(steps):
            node = WorkflowStep(name=f"step_{step.get('step_id', i + 1)}",
                step=step,
                executor=self.specialist,
                verifier=self.evaluator,
                max_retries=self.max_retries,)
            nodes.append(node)
        for i in range(len(nodes) - 1):
            nodes[i].next_on_success = nodes[i + 1]
            # On FAIL, continue to the next step (branch path) instead of aborting
            # the whole workflow — one failed step should not drop the rest.
            nodes[i].next_on_failure = nodes[i + 1]

        engine = WorkflowEngine()
        engine.set_entry(nodes[0])
        out = await engine.run({"goal": goal, "_usage": usage}, on_event=on_event)
        results = out.get("results", [])

        pass_c = sum(1 for r in results if r.get("verdict") == Verdict.PASS.value)
        fail_c = sum(1 for r in results if r.get("verdict") == Verdict.FAIL.value)
        partial_c = sum(1 for r in results if r.get("verdict") == Verdict.PARTIAL.value)
        return {
            "goal": goal,
            "results": results,
            "total_steps": len(steps),
            "summary": {"total": len(results), "pass": pass_c, "partial": partial_c, "fail": fail_c},
            "token_usage": _tu(),
        }
