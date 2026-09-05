"""LLM client with structured output, retry/circuit-breaker, and guardrails.

Demonstrates:
1. Structured output validation (Pydantic re-parse of LLM JSON)
2. Exponential backoff retry via tenacity
3. Circuit breaker pattern for consecutive failures
4. Safety guardrails (input/output filtering)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from tax_agent_platform.config import settings
from tax_agent_platform.models import GuardrailResult

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# ---- Circuit Breaker ----


class CircuitBreaker:
    """Simple circuit breaker: opens after N consecutive failures, resets after cooldown."""

    def __init__(self, threshold: int = 5, cooldown: float = 60.0) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self.failure_count = 0
        self.last_failure_time: float | None = None
        self.is_open = False

    def record_success(self) -> None:
        self.failure_count = 0
        self.is_open = False

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.threshold:
            self.is_open = True
            logger.warning(
                "Circuit breaker OPENED after %d consecutive failures", self.failure_count
            )

    def can_proceed(self) -> bool:
        if not self.is_open:
            return True
        # Auto-reset after cooldown
        if self.last_failure_time and (time.time() - self.last_failure_time) > self.cooldown:
            self.is_open = False
            self.failure_count = 0
            logger.info("Circuit breaker RESET after cooldown")
            return True
        return False


# ---- Safety Guardrails ----

PROMPT_INJECTION_PATTERNS = [
    # High-signal role-swap / jailbreak phrasings only. Deliberately NOT a
    # bare "system:" or "you are now" match, because those substrings appear in
    # perfectly legitimate tax questions ("What is the tax system: VAT or CIT?",
    # "you are now required to file quarterly") and would be false-positive
    # blocked by the input guardrail.
    r"ignore\s+(previous|above|all|prior)\s+instructions",
    r"disregard\s+(your|the|all|previous)\s+(instructions|prompt|rules)",
    r"you\s+are\s+now\s+(a|an|my|our|the)\s",
    r"pretend\s+(to\s+be|you\s+are)",
    r"new\s+instructions\s*:",
]

# Sensitive-data patterns use word boundaries and specific compound forms so a
# normal tax answer is never blocked just for containing a common word.
# E.g. "trade secret" / "secretariat" must NOT trip the filter, but
# "client secret" / "secret key" / "api_key" must.
SENSITIVE_PATTERNS = [
    r"\bpasswords?\b",
    r"\bapi[ _-]?keys?\b",
    r"\bclient[ _-]?secrets?\b",
    r"\bsecret[ _-]?(key|token|id)s?\b",
    r"\bcredit[ _-]?cards?\b",
    r"\bssns?\b",
    r"\bsocial\s+security\s+(number|no)\b",
]


class Guardrails:
    """Multi-layer safety guardrails for LLM I/O."""

    @staticmethod
    def check_input(text: str) -> GuardrailResult:
        """Detect prompt injection attacks in user input."""
        text_lower = text.lower()
        for pattern in PROMPT_INJECTION_PATTERNS:
            if re.search(pattern, text_lower):
                return GuardrailResult(
                    passed=False,
                    reason="Potential prompt injection detected",
                    category="prompt_injection",
                )
        return GuardrailResult(passed=True)

    @staticmethod
    def check_output(text: str) -> GuardrailResult:
        """Filter LLM output for sensitive data leakage."""
        text_lower = text.lower()
        for pattern in SENSITIVE_PATTERNS:
            if re.search(pattern, text_lower):
                return GuardrailResult(
                    passed=False,
                    reason="Sensitive data pattern detected in output",
                    category="sensitive_data",
                )
        return GuardrailResult(passed=True)


# ---- PII minimization (egress control) ----
#
# User-supplied text (the tax question / goal) may contain direct identifiers.
# Before that text is sent to a THIRD-PARTY / cloud LLM endpoint, we scrub the
# highest-risk direct identifiers so they never leave the premises.
#
# Deliberately NARROW: entity names and revenue figures are KEPT, because the
# advisor needs them to give a concrete, tailored answer and they are far lower
# risk than government IDs / account numbers. This is defense-in-depth — the
# real control for sensitive workloads is a self-hosted model or explicit
# consent; redaction is the pragmatic minimum for a cloud-backed demo.
_PII_PATTERNS: list[tuple[str, str]] = [
    # Chinese resident identity card: 18 digits with embedded birth date.
    (r"\b\d{6}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b", "id_card"),
    # Mainland China mobile number.
    (r"\b1[3-9]\d{9}\b", "phone"),
    # Email address.
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "email"),
    # Unified Social Credit Code (18 chars; excludes I/O/Z/S/V). Requires at
    # least one letter so a pure-numeric 18-digit run (e.g. an ID card, matched
    # above) is NOT double-counted here.
    (r"\b(?=[0-9A-HJ-NPQRTUWXY]{0,17}[A-HJ-NPQRTUWXY])[0-9A-HJ-NPQRTUWXY]{18}\b", "credit_code"),
    # Bank card (UnionPay / Visa / Mastercard prefixes), 16-19 digits.
    (r"\b(?:62\d{14,17}|[456]\d{15,18})\b", "bank_card"),
]

_REDACTED = "[REDACTED]"


def redact_pii(text: str) -> str:
    """Scrub direct identifiers from text before it leaves to a third-party LLM."""
    for pattern, _label in _PII_PATTERNS:
        text = re.sub(pattern, _REDACTED, text)
    return text


def count_pii(text: str) -> int:
    """Count direct-identifier matches — for logging the fact without echoing values."""
    return sum(len(re.findall(pattern, text)) for pattern, _ in _PII_PATTERNS)


def _redact_user_messages(messages: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """Return (messages with user-role content redacted, total PII matches found)."""
    out: list[dict[str, str]] = []
    found = 0
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content", "")
            found += count_pii(content)
            out.append({**m, "content": redact_pii(content)})
        else:
            out.append(m)
    return out, found


# ---- LLM Client ----


class LLMClient:
    """LLM client with structured output, retry, circuit breaker, and guardrails.

    Supports two interchangeable OpenAI-compatible providers:
      - "primary" (default): uses TAX_PRIMARY_* settings
      - "secondary": uses TAX_SECONDARY_* settings
    """

    def __init__(self, provider: str = "primary") -> None:
        self._provider = provider
        if provider == "secondary":
            api_key = settings.secondary_api_key
            base_url = settings.secondary_base_url
            self._model = settings.secondary_model
            label = "SECONDARY"
        else:
            api_key = settings.primary_api_key
            base_url = settings.primary_base_url
            self._model = settings.primary_model
            label = "PRIMARY"

        if not api_key:
            raise RuntimeError(f"未设置 {label}_API_KEY。请在.env 中配置。")
        if not self._model:
            raise RuntimeError(f"未设置 {label}_MODEL。请在.env 中补充模型名。")

        self._client = OpenAI(api_key=api_key, base_url=base_url or None)
        self._breaker = CircuitBreaker(threshold=settings.circuit_breaker_threshold)
        self._guardrails = Guardrails()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((json.JSONDecodeError, ValidationError)),
    )
    def structured_call(
        self, messages: list[dict[str, str]], response_model: type[T], usage: dict | None = None
    ) -> T:
        """Call LLM and parse response into a Pydantic model (structured output).

        Key design:
        - Forces JSON mode via response_format
        - Re-validates with Pydantic (catches format hallucinations)
        - Retries on parse failure (exponential backoff)
        - Circuit breaker prevents cascading failures
        """
        if not self._breaker.can_proceed():
            raise RuntimeError("Circuit breaker is OPEN — LLM calls suspended")

        # Input guardrail
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        input_check = self._guardrails.check_input(user_msg)
        if not input_check.passed:
            raise ValueError(f"Input blocked by guardrail: {input_check.reason}")

        # PII minimization: scrub direct identifiers from user messages BEFORE
        # they egress to the third-party LLM. The guardrail above ran on the
        # original text (injection detection needs real content); this is a
        # separate egress control so IDs / account numbers never leave the box.
        sent_messages, pii_found = _redact_user_messages(messages)
        if pii_found:
            logger.info("Redacted %d PII token(s) from user input before LLM call", pii_found)

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=sent_messages,
                temperature=0.0,
                response_format={"type": "json_object"},
                timeout=settings.llm_timeout,
            )
        except Exception as exc:
            # A timeout, 5xx, or connection error is exactly the outage the
            # circuit breaker exists to catch. Record it so the breaker actually
            # opens and stops hammering a dead LLM (previously these failures
            # were never counted, so the breaker could never trip).
            self._breaker.record_failure()
            logger.error("LLM API call failed: %s", exc)
            raise

        raw = response.choices[0].message.content or ""
        self._accumulate_usage(response, usage)

        # Output guardrail
        output_check = self._guardrails.check_output(raw)
        if not output_check.passed:
            # Content-safety rejection, not an infra failure: do NOT trip the
            # circuit breaker (see simple_call for the rationale).
            raise ValueError(f"Output blocked by guardrail: {output_check.reason}")

        # Parse and validate with Pydantic
        try:
            parsed = response_model.model_validate_json(raw)
            self._breaker.record_success()
            return parsed
        except (json.JSONDecodeError, ValidationError) as e:
            self._breaker.record_failure()
            logger.warning("LLM output failed validation: %s", e)
            raise

    def simple_call(self, messages: list[dict[str, str]], usage: dict | None = None) -> str:
        """Simple text call without structured output parsing."""
        if not self._breaker.can_proceed():
            raise RuntimeError("Circuit breaker is OPEN")

        # Input guardrail — mirrors structured_call so the SAME safety gate
        # applies to the grounded-answer path (SpecialistAgent uses
        # simple_call). Previously only structured_call screened input, leaving
        # the Specialist's generation exposed to prompt injection that slipped
        # past the Planner.
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        input_check = self._guardrails.check_input(user_msg)
        if not input_check.passed:
            raise ValueError(f"Input blocked by guardrail: {input_check.reason}")

        # PII minimization — same egress control as structured_call: scrub
        # direct identifiers from user messages before they reach the cloud LLM.
        sent_messages, pii_found = _redact_user_messages(messages)
        if pii_found:
            logger.info("Redacted %d PII token(s) from user input before LLM call", pii_found)

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=sent_messages,
                temperature=0.0,
                timeout=settings.llm_timeout,
            )
        except Exception as exc:
            self._breaker.record_failure()
            logger.error("LLM API call failed: %s", exc)
            raise

        raw = response.choices[0].message.content or ""

        # Output guardrail — centralized screening of the generated answer so
        # the Evaluator's check is defense-in-depth, not the only gate.
        output_check = self._guardrails.check_output(raw)
        if not output_check.passed:
            # A content-safety rejection is NOT an infrastructure failure, so it
            # must NOT trip the circuit breaker. The breaker's job is to stop
            # hammering a dead LLM; if guardrail blocks counted as failures,
            # 5 consecutive blocked answers (e.g. the LLM keeps echoing a
            # sensitive keyword) would OPEN the breaker and wrongly suspend every
            # legitimate call. Guardrail blocks therefore leave the breaker neutral.
            raise ValueError(f"Output blocked by guardrail: {output_check.reason}")

        self._breaker.record_success()
        self._accumulate_usage(response, usage)
        return raw

    # ---- Token accounting ----

    def _accumulate_usage(self, response: object, usage: dict | None) -> None:
        """Add a single response's token usage into ``usage`` (no-op if None)."""
        if usage is None or response is None:
            return
        u = getattr(response, "usage", None)
        if u is None:
            return
        usage["prompt_tokens"] = usage.get("prompt_tokens", 0) + (u.prompt_tokens or 0)
        usage["completion_tokens"] = usage.get("completion_tokens", 0) + (u.completion_tokens or 0)
        usage["total_tokens"] = usage.get("total_tokens", 0) + (u.total_tokens or 0)
        usage["calls"] = usage.get("calls", 0) + 1

    def estimate_cost(self, usage: dict[str, int]) -> float:
        """Estimate cost (CNY) for an accumulated ``usage`` dict.

        Per-provider rates (CNY per 1K tokens) come from settings, overridable
        via env (``TAX_PRIMARY_PRICE_PROMPT`` / ``TAX_PRIMARY_PRICE_COMPLETION``,
        etc.), so real pricing can be set without touching code. Defaults are
        placeholders.
        """
        if self._provider == "secondary":
            price = {
                "prompt": settings.secondary_price_prompt,
                "completion": settings.secondary_price_completion,
            }
        else:
            price = {
                "prompt": settings.primary_price_prompt,
                "completion": settings.primary_price_completion,
            }
        return round(
            usage.get("prompt_tokens", 0) / 1000 * price["prompt"]
            + usage.get("completion_tokens", 0) / 1000 * price["completion"],
            6,
        )
