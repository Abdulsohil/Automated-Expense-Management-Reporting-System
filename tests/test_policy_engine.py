"""Tests for the deterministic policy rule engine."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import CATEGORY_LIMITS, MAX_CLAIM_AGE_DAYS, RECEIPT_REQUIRED_ABOVE  # noqa: E402
from policy_engine import (  # noqa: E402
    check_amount_limit,
    check_claim_age,
    check_receipt,
    check_round_amount,
    evaluate,
    find_duplicates,
    policy_summary,
)

TODAY = date(2026, 10, 12)


def make_expense(**overrides):
    """Return a compliant baseline claim, with any field overridden."""
    base = {
        "employee_email": "waseem@example.com",
        "employee_name": "Mohamed Waseem",
        "expense_date": "2026-10-10",
        "category": "Meals",
        "amount": 450.0,
        "receipt_ref": "RCP-1",
        "status": "Pending",
    }
    base.update(overrides)
    return base


class TestIndividualRules:
    def test_amount_within_limit_passes(self):
        assert check_amount_limit("Meals", CATEGORY_LIMITS["Meals"] - 1) is None

    def test_amount_over_limit_is_flagged(self):
        message = check_amount_limit("Meals", CATEGORY_LIMITS["Meals"] + 1)
        assert message is not None and "exceeds" in message

    def test_unknown_category_has_no_limit(self):
        assert check_amount_limit("Unlisted Category", 999_999) is None

    def test_receipt_required_above_threshold(self):
        assert check_receipt(RECEIPT_REQUIRED_ABOVE + 1, "") is not None
        assert check_receipt(RECEIPT_REQUIRED_ABOVE + 1, "INV-1") is None

    def test_receipt_optional_below_threshold(self):
        assert check_receipt(RECEIPT_REQUIRED_ABOVE - 1, "") is None

    def test_future_date_is_rejected(self):
        assert check_claim_age(TODAY + timedelta(days=1), TODAY) == (
            "Expense date is in the future"
        )

    def test_stale_claim_is_rejected(self):
        stale = TODAY - timedelta(days=MAX_CLAIM_AGE_DAYS + 1)
        assert "older than" in check_claim_age(stale, TODAY)

    def test_recent_claim_passes_age_check(self):
        assert check_claim_age(TODAY - timedelta(days=5), TODAY) is None

    def test_round_amount_warning(self):
        assert check_round_amount(10000.0) is not None
        assert check_round_amount(10432.50) is None


class TestDuplicateDetection:
    def test_identical_claim_is_a_duplicate(self):
        existing = [make_expense(id=1)]
        assert find_duplicates(make_expense(id=2), existing) == [1]

    def test_different_amount_is_not_a_duplicate(self):
        existing = [make_expense(id=1, amount=451.0)]
        assert find_duplicates(make_expense(id=2), existing) == []

    def test_different_employee_is_not_a_duplicate(self):
        existing = [make_expense(id=1, employee_email="someone.else@example.com")]
        assert find_duplicates(make_expense(id=2), existing) == []

    def test_outside_window_is_not_a_duplicate(self):
        existing = [make_expense(id=1, expense_date="2026-09-01")]
        assert find_duplicates(make_expense(id=2), existing) == []


class TestEvaluate:
    def test_clean_claim_is_auto_approved(self):
        result = evaluate(make_expense(), [], today=TODAY)
        assert result.compliant
        assert result.auto_decision == "Approved"
        assert result.risk_score == 0

    def test_over_limit_claim_is_not_compliant(self):
        result = evaluate(make_expense(amount=99_999), [], today=TODAY)
        assert not result.compliant
        assert result.auto_decision in {"Pending", "Rejected"}
        assert result.risk_score > 0

    def test_multiple_violations_push_to_rejected(self):
        result = evaluate(
            make_expense(amount=99_999, receipt_ref="", expense_date="2025-01-01"),
            [],
            today=TODAY,
        )
        assert result.auto_decision == "Rejected"
        assert len(result.violations) >= 3

    def test_zero_amount_is_rejected(self):
        result = evaluate(make_expense(amount=0), [], today=TODAY)
        assert "Amount must be greater than zero" in result.violations

    def test_bad_date_is_reported(self):
        result = evaluate(make_expense(expense_date="not-a-date"), [], today=TODAY)
        assert any("date" in v.lower() for v in result.violations)

    def test_flags_string_contains_every_message(self):
        result = evaluate(make_expense(amount=99_999, receipt_ref=""), [], today=TODAY)
        for violation in result.violations:
            assert violation in result.flags

    def test_result_serialises(self):
        payload = evaluate(make_expense(), [], today=TODAY).to_dict()
        assert set(payload) == {
            "compliant", "violations", "warnings", "risk_score", "auto_decision"
        }


def test_policy_summary_exposes_configuration():
    summary = policy_summary()
    assert summary["category_limits"] == CATEGORY_LIMITS
    assert summary["max_claim_age_days"] == MAX_CLAIM_AGE_DAYS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
