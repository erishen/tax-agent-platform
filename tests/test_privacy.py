"""Privacy hardening: PII minimization before egress to third-party LLM.

These tests exercise the redaction helpers directly (no network / no LLM), so
they stay fast and deterministic. They guard the property that direct
identifiers are scrubbed from user text before it leaves to the cloud, while
entity names and revenue figures are preserved so the advisor stays useful.
"""

from tax_agent_platform.llm.client import count_pii, redact_pii


def test_id_card_redacted():
    text = "客户身份证 110105199003071234 需要申报"
    out = redact_pii(text)
    assert "110105199003071234" not in out
    assert "[REDACTED]" in out
    # Entity name and the rest survive.
    assert "客户" in out and "需要申报" in out


def test_phone_redacted():
    text = "联系法人 13812345678 安排会面"
    out = redact_pii(text)
    assert "13812345678" not in out
    assert "法人" in out


def test_email_redacted():
    text = "发送报告到 tax.lead@example.com 即可"
    out = redact_pii(text)
    assert "tax.lead@example.com" not in out


def test_unified_social_credit_code_redacted():
    # 18-char alphanumeric credit code (no I/O/Z/S/V), uppercase.
    text = "统一社会信用代码 91110108MA01ABCD23 已变更"
    out = redact_pii(text)
    assert "91110108MA01ABCD23" not in out


def test_bank_card_redacted():
    text = "退款至对公账户 6222021234567890123"
    out = redact_pii(text)
    assert "6222021234567890123" not in out


def test_revenue_figures_preserved():
    # A plain revenue figure must NOT be redacted (it is not a direct identifier).
    text = "该公司去年利润 5200000 元，应补缴税款"
    out = redact_pii(text)
    assert "5200000" in out
    assert count_pii(text) == 0


def test_count_pii_counts_matches():
    text = "身份证 110105199003071234 电话 13812345678"
    assert count_pii(text) == 2


def test_plain_tax_question_untouched():
    text = "软件服务适用什么增值税税率？跨境数字服务有优惠吗？"
    assert redact_pii(text) == text
    assert count_pii(text) == 0
