"""Business-logic layer that ties storage, policy and AI together.

The Streamlit app and the CLI both call this module; neither touches SQL or the
AI client directly.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import database as db
from ai_service import AIService
from config import CATEGORIES, CURRENCY, STATUSES
from logger import get_logger
from policy_engine import PolicyResult, evaluate

logger = get_logger(__name__)


class ValidationError(ValueError):
    """Raised when a submitted claim is structurally invalid."""


class ExpenseManager:
    """Facade over the expense lifecycle: submit, review, decide, import."""

    def __init__(self, db_path: Optional[Path] = None, ai: Optional[AIService] = None):
        """Initialise the manager and make sure the schema exists.

        Args:
            db_path: Optional database override (tests pass a temp file).
            ai: Optional pre-built AI service; one is created when omitted.
        """
        self.db_path = db_path
        self.ai = ai if ai is not None else AIService()
        db.init_db(self.db_path)

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    @staticmethod
    def validate(expense: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise and validate raw claim input.

        Args:
            expense: Raw field mapping from the UI, CLI or CSV import.

        Returns:
            A cleaned copy with trimmed strings, a float amount and an
            ISO-formatted date.

        Raises:
            ValidationError: On missing required fields or unusable values.
        """
        required = ("employee_name", "employee_email", "expense_date", "amount")
        missing = [field for field in required if not str(expense.get(field, "")).strip()]
        if missing:
            raise ValidationError(f"Missing required field(s): {', '.join(missing)}")

        cleaned = dict(expense)
        cleaned["employee_name"] = str(expense["employee_name"]).strip()
        cleaned["employee_email"] = str(expense["employee_email"]).strip().lower()

        if "@" not in cleaned["employee_email"]:
            raise ValidationError("Employee email is not a valid address")

        try:
            cleaned["amount"] = round(float(str(expense["amount"]).replace(",", "")), 2)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Amount '{expense['amount']}' is not a number") from exc
        if cleaned["amount"] <= 0:
            raise ValidationError("Amount must be greater than zero")

        raw_date = expense["expense_date"]
        if isinstance(raw_date, (date, datetime)):
            cleaned["expense_date"] = raw_date.strftime("%Y-%m-%d")
        else:
            try:
                cleaned["expense_date"] = datetime.strptime(
                    str(raw_date)[:10], "%Y-%m-%d"
                ).strftime("%Y-%m-%d")
            except ValueError as exc:
                raise ValidationError(
                    f"Date '{raw_date}' must be in YYYY-MM-DD format"
                ) from exc

        category = str(expense.get("category", "")).strip()
        cleaned["category"] = category if category in CATEGORIES else ""
        cleaned["vendor"] = str(expense.get("vendor", "")).strip()
        cleaned["description"] = str(expense.get("description", "")).strip()
        cleaned["receipt_ref"] = str(expense.get("receipt_ref", "")).strip()
        cleaned["currency"] = str(expense.get("currency", CURRENCY)).strip() or CURRENCY
        return cleaned

    # ------------------------------------------------------------------ #
    # Submission
    # ------------------------------------------------------------------ #
    def submit(
        self,
        expense: Dict[str, Any],
        auto_categorize: bool = True,
        today: Optional[date] = None,
    ) -> Tuple[int, PolicyResult]:
        """Validate, categorise, policy-check and store one claim.

        Args:
            expense: Raw claim fields.
            auto_categorize: Fill a blank category using the AI/keyword engine.
            today: Reference date for the age checks (injectable for tests).

        Returns:
            Tuple of the new expense id and the policy result.
        """
        cleaned = self.validate(expense)

        if auto_categorize and not cleaned["category"]:
            guess = self.ai.categorize(cleaned["description"], cleaned["vendor"])
            cleaned["category"] = guess["category"]
            cleaned["ai_notes"] = (
                f"Auto-categorised as {guess['category']} "
                f"(confidence {guess['confidence']}, {guess['source']}): {guess['reason']}"
            )
        elif not cleaned["category"]:
            cleaned["category"] = "Miscellaneous"

        existing = db.fetch_expenses(
            employee_email=cleaned["employee_email"], db_path=self.db_path
        )
        verdict = evaluate(cleaned, existing, today=today)

        cleaned["flags"] = verdict.flags
        cleaned["risk_score"] = verdict.risk_score
        cleaned["status"] = verdict.auto_decision

        expense_id = db.insert_expense(cleaned, db_path=self.db_path)
        db.upsert_employee(
            cleaned["employee_name"], cleaned["employee_email"], db_path=self.db_path
        )
        logger.info(
            "Claim #%s submitted: %s %.2f -> %s",
            expense_id, cleaned["category"], cleaned["amount"], verdict.auto_decision,
        )
        return expense_id, verdict

    # ------------------------------------------------------------------ #
    # Retrieval and workflow
    # ------------------------------------------------------------------ #
    def list_expenses(self, **filters: Any) -> List[Dict[str, Any]]:
        """Return claims matching the given filters (see :func:`database.fetch_expenses`)."""
        return db.fetch_expenses(db_path=self.db_path, **filters)

    def get(self, expense_id: int) -> Optional[Dict[str, Any]]:
        """Return a single claim by id."""
        return db.get_expense(expense_id, db_path=self.db_path)

    def set_status(
        self, expense_id: int, status: str, actor: str = "manager", note: str = ""
    ) -> bool:
        """Approve, reject or mark a claim reimbursed.

        Raises:
            ValidationError: If ``status`` is not one of :data:`config.STATUSES`.
        """
        if status not in STATUSES:
            raise ValidationError(f"Status must be one of {STATUSES}")
        return db.update_status(
            expense_id, status, actor=actor, note=note, db_path=self.db_path
        )

    def delete(self, expense_id: int, actor: str = "admin") -> bool:
        """Permanently remove a claim."""
        return db.delete_expense(expense_id, actor=actor, db_path=self.db_path)

    def explain(self, expense_id: int) -> str:
        """Return a plain-English explanation of a claim's policy flags."""
        expense = self.get(expense_id)
        if not expense:
            return "Claim not found."
        flags = [f.strip() for f in str(expense.get("flags", "")).split(";") if f.strip()]
        return self.ai.explain_violation(expense, flags)

    # ------------------------------------------------------------------ #
    # Bulk import
    # ------------------------------------------------------------------ #
    def import_csv(
        self, csv_path: Path, auto_categorize: bool = True, today: Optional[date] = None
    ) -> Dict[str, Any]:
        """Bulk-load claims from a CSV file.

        Expected headers: ``employee_name, employee_email, expense_date,
        category, vendor, description, amount, receipt_ref``.

        Returns:
            Dict with ``imported``, ``failed`` and a list of per-row ``errors``.
        """
        imported, errors = 0, []
        with open(csv_path, newline="", encoding="utf-8") as handle:
            for line_no, row in enumerate(csv.DictReader(handle), start=2):
                try:
                    self.submit(row, auto_categorize=auto_categorize, today=today)
                    imported += 1
                except (ValidationError, KeyError) as exc:
                    errors.append(f"Row {line_no}: {exc}")
        logger.info("CSV import finished: %s imported, %s failed", imported, len(errors))
        return {"imported": imported, "failed": len(errors), "errors": errors}

    def seed_sample_data(self, sample_csv: Path, today: Optional[date] = None) -> Dict[str, Any]:
        """Load the bundled demo dataset, skipping if claims already exist.

        Demo dates are rebased so the newest sample claim lands on today. Without
        this the fixture would eventually age past the submission window and every
        demo claim would be auto-rejected as stale.
        """
        if self.list_expenses():
            return {"imported": 0, "failed": 0, "errors": ["Database already populated"]}

        reference = today or date.today()
        with open(sample_csv, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        parsed_dates = []
        for row in rows:
            try:
                parsed_dates.append(datetime.strptime(row["expense_date"], "%Y-%m-%d").date())
            except (KeyError, ValueError):
                parsed_dates.append(None)

        valid = [d for d in parsed_dates if d]
        shift = (reference - max(valid)).days if valid else 0

        imported, errors = 0, []
        for index, row in enumerate(rows):
            original = parsed_dates[index]
            if original:
                row = dict(row)
                row["expense_date"] = (original + timedelta(days=shift)).isoformat()
            try:
                self.submit(row, today=reference)
                imported += 1
            except (ValidationError, KeyError) as exc:
                errors.append(f"Row {index + 2}: {exc}")

        logger.info("Seeded %s demo claims (date shift %s days)", imported, shift)
        return {"imported": imported, "failed": len(errors), "errors": errors}
