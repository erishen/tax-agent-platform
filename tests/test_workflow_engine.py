"""Tests for the now-live WorkflowEngine branching and Evaluator independent audit.

These run fully offline (no LLM / network): the Evaluator falls back to the
heuristic verifier, and the planner uses rule-based planning.
"""

import asyncio

from tax_agent_platform.agents import (
    Agent,
    AgentRunResult,
    EvaluatorAgent,
    PSEWorkflow,
    WorkflowEngine,
    WorkflowStep,
)
from tax_agent_platform.models import Verdict


class _PassAgent(Agent):
    async def run(self, input_data):
        return AgentRunResult(
            agent_name="Pass",
            status="success",
            data={"verdict": Verdict.PASS.value},
            evidence=["e"],
            output="ok",
        )


class _FailAgent(Agent):
    async def run(self, input_data):
        return AgentRunResult(
            agent_name="Fail",
            status="success",
            data={"verdict": Verdict.FAIL.value},
            evidence=["e"],
            output="no",
        )


class _EchoVerifier(Agent):
    """Mimics an Evaluator: returns the executor's own verdict unmodified."""

    async def run(self, input_data):
        sr = input_data.get("specialist_result")
        verdict = (sr.data if sr else {}).get("verdict", Verdict.PASS.value)
        return AgentRunResult(
            agent_name="Verifier",
            status="success",
            data={
                "verdict": verdict,
                "issues": [],
                "verifications": [],
                "independent_evidence": [],
            },
            evidence=[],
            output="v",
        )


def _step(name, executor, step_id):
    return WorkflowStep(
        name=name,
        step={"step_id": step_id},
        executor=executor,
        verifier=_EchoVerifier("V", "r"),
    )


def test_workflowstep_success_branches_to_next_on_success():
    a = _step("a", _PassAgent("P", "r"), 1)
    b = _step("b", _PassAgent("P", "r"), 2)
    c = _step("c", _PassAgent("P", "r"), 3)
    a.next_on_success = b
    b.next_on_success = c

    engine = WorkflowEngine()
    engine.set_entry(a)
    out = asyncio.run(engine.run({"goal": "g"}))
    assert [r["step"]["step_id"] for r in out["results"]] == [1, 2, 3]
    assert all(r["verdict"] == Verdict.PASS.value for r in out["results"])


def test_workflowstep_failure_branches_to_next_on_failure():
    fail = _step("fail", _FailAgent("F", "r"), 1)
    success = _step("success", _PassAgent("P", "r"), 2)
    skip = _step("skip", _PassAgent("P", "r"), 3)
    fail.next_on_success = skip  # should NOT be taken
    fail.next_on_failure = success  # should be taken
    success.next_on_success = skip

    engine = WorkflowEngine()
    engine.set_entry(fail)
    out = asyncio.run(engine.run({"goal": "g"}))
    ids = [r["step"]["step_id"] for r in out["results"]]
    assert ids == [1, 2, 3]
    assert out["results"][0]["branch"] == "failure"


def test_evaluator_heuristic_verification_without_llm():
    # Evaluator with no LLM must still produce structured verifications
    evaluator = EvaluatorAgent(guardrails_enabled=True, llm_client=None, rag_pipeline=None)
    specialist = AgentRunResult(
        agent_name="Specialist",
        status="success",
        data={},
        evidence=["RAG:[vat-2024] score=0.3 → 软件服务适用 6% 增值税"],
        output="software VAT is 6%",
    )
    step = {
        "step_id": 1,
        "description": "增值税税率",
        "acceptance_criteria": ["确认软件服务适用 6% 增值税"],
    }
    result = asyncio.run(evaluator.run({"step": step, "specialist_result": specialist}))
    assert result.data["verdict"] in {v.value for v in Verdict}
    assert result.data["verifications"], "expected per-criterion verifications"
    assert result.data["verifications"][0]["verdict"] in {"SUPPORTED", "UNVERIFIED", "CONTRADICTED"}
    # No longer the old VERIFIED_ prefix trick
    assert not any(ev.startswith("VERIFIED_") for ev in result.evidence)


def test_pse_workflow_run_offline_uses_engine():
    # Full PSE loop without LLM: planner (rule-based) -> engine -> evaluator (heuristic)
    wf = PSEWorkflow(max_retries=1, rag_pipeline=None, tool_registry=None, llm_client=None)
    out = asyncio.run(wf.run("查询软件企业增值税税率"))
    assert out["total_steps"] >= 1
    assert len(out["results"]) == out["total_steps"]
    assert "summary" in out
    assert all("verdict" in r for r in out["results"])
