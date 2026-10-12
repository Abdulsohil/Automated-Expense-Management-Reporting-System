"""Tests for validation, submission and the SQLite persistence layer."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database as db  # noqa: E402
from ai_service import AIService  # noqa: E402
from expense_manager import ExpenseManager, ValidationError  # noqa: E402

TODAY = date(2026, 10, 12)


@pytest.fixture()
def manager(tmp_path):
    """Return a manager backed by a throwaway database and the offline AI."""
    return ExpenseManager(db_path=tmp_path / "test.db", ai=AIService(api_key=""))


def claim(**overrides):
    """Return a valid baseline claim payload."""
    base = {
        "employee_name": "Shaik Abdul Sohial",
        "employee_email": "Sohial@Example.com",
        "expense_date": "2026-10-10",
        "amount": "1,250.50",
        "category": "Meals",
        "vendor": "Cafe Mocha",
        "description": "Client coffee meeting",
        "receipt_ref": "RCP-1",
    }
    base.update(overrides)
    return base


class TestValidation:
    def test_valid_claim_is_normalised(self):
        cleaned = ExpenseManager.validate(claim())
        assert cleaned["amount"] == 1250.50
        assert cleaned["employee_email"] == "sohial@example.com"
        assert cleaned["expense_date"] == "2026-10-10"

    def test_date_object_is_accepted(self):
        cleaned = ExpenseManager.validate(claim(expense_date=date(2026, 9, 1)))
        assert cleaned["expense_date"] == "2026-09-01"

    def test_missing_field_raises(self):
        with pytest.raises(ValidationError, match="Missing required field"):
            ExpenseManager.validate(claim(employee_name=""))

    def test_bad_email_raises(self):
        with pytest.raises(ValidationError, match="valid address"):
            ExpenseManager.validate(claim(employee_email="not-an-email"))

    def test_non_numeric_amount_raises(self):
        with pytest.raises(ValidationError, match="not a number"):
            ExpenseManager.validate(claim(amount="twelve"))

    def test_negative_amount_raises(self):
        with pytest.raises(ValidationError, match="greater than zero"):
            ExpenseManager.validate(claim(amount=-5))

    def test_bad_date_format_raises(self):
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            ExpenseManager.validate(claim(expense_date="10/10/2026"))

    def test_unknown_category_is_blanked_for_auto_detection(self):
        assert ExpenseManager.validate(claim(category="Spaceships"))["category"] == ""


class TestSubmission:
    def test_clean_claim_is_stored_and_approved(self, manager):
        expense_id, verdict = manager.submit(claim(), today=TODAY)
        stored = manager.get(expense_id)
        assert stored is not None
        assert stored["status"] == "Approved"
        assert verdict.compliant

    def test_auto_categorisation_fills_blank_category(self, manager):
        expense_id, _ = manager.submit(
            claim(category="", vendor="Uber", description="Airport taxi"), today=TODAY
        )
        stored = manager.get(expense_id)
        assert stored["category"] == "Travel"
        assert "Auto-categorised" in stored["ai_notes"]

    def test_duplicate_submission_is_flagged(self, manager):
        manager.submit(claim(), today=TODAY)
        _, verdict = manager.submit(claim(), today=TODAY)
        assert any("duplicate" in v.lower() for v in verdict.violations)

    def test_over_limit_claim_is_not_approved(self, manager):
        _, verdict = manager.submit(claim(amount=90_000), today=TODAY)
        assert verdict.auto_decision != "Approved"

    def test_employee_record_is_created(self, manager):
        manager.submit(claim(), today=TODAY)
        emails = [e["email"] for e in db.fetch_employees(db_path=manager.db_path)]
        assert "sohial@example.com" in emails


class TestWorkflow:
    def test_status_update_and_audit_entry(self, manager):
        expense_id, _ = manager.submit(claim(), today=TODAY)
        assert manager.set_status(expense_id, "Reimbursed", note="Paid in October run")
        assert manager.get(expense_id)["status"] == "Reimbursed"
        actions = [row["action"] for row in db.fetch_audit_log(db_path=manager.db_path)]
        assert "STATUS->Reimbursed" in actions

    def test_invalid_status_raises(self, manager):
        expense_id, _ = manager.submit(claim(), today=TODAY)
        with pytest.raises(ValidationError):
            manager.set_status(expense_id, "Maybe")

    def test_status_update_on_missing_id_returns_false(self, manager):
        assert manager.set_status(9999, "Approved") is False

    def test_delete_removes_the_claim(self, manager):
        expense_id, _ = manager.submit(claim(), today=TODAY)
        assert manager.delete(expense_id)
        assert manager.get(expense_id) is None

    def test_filters_narrow_the_result_set(self, manager):
        manager.submit(claim(), today=TODAY)
        manager.submit(
            claim(category="Travel", amount=800, receipt_ref="RCP-2",
                  expense_date="2026-10-09"),
            today=TODAY,
        )
        assert len(manager.list_expenses()) == 2
        assert len(manager.list_expenses(category="Travel")) == 1

    def test_explain_returns_text_for_flagged_claim(self, manager):
        expense_id, _ = manager.submit(claim(amount=90_000), today=TODAY)
        assert "flagged" in manager.explain(expense_id).lower()


class TestBulkImport:
    def test_sample_dataset_loads(self, manager):
        sample = Path(__file__).resolve().parents[1] / "sample_data" / "sample_expenses.csv"
        result = manager.seed_sample_data(sample, today=TODAY)
        assert result["imported"] >= 25
        assert result["failed"] == 0

    def test_seeding_twice_is_a_no_op(self, manager):
        sample = Path(__file__).resolve().parents[1] / "sample_data" / "sample_expenses.csv"
        manager.seed_sample_data(sample, today=TODAY)
        second = manager.seed_sample_data(sample, today=TODAY)
        assert second["imported"] == 0

    def test_bad_rows_are_reported_not_raised(self, manager, tmp_path):
        csv_path = tmp_path / "bad.csv"
        csv_path.write_text(
            "employee_name,employee_email,expense_date,category,vendor,"
            "description,amount,receipt_ref\n"
            "Valid User,valid@example.com,2026-10-10,Meals,Cafe,Lunch,500,RCP-9\n"
            "Broken User,broken@example.com,NOT-A-DATE,Meals,Cafe,Lunch,500,RCP-10\n",
            encoding="utf-8",
        )
        result = manager.import_csv(csv_path, today=TODAY)
        assert result["imported"] == 1
        assert result["failed"] == 1


def test_reset_database_clears_everything(manager):
    manager.submit(claim(), today=TODAY)
    db.reset_database(manager.db_path)
    assert manager.list_expenses() == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
