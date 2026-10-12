"""Deterministic company-policy rule engine.

The engine is intentionally free of AI calls: the same claim always produces the
same verdict, which is what an auditor needs. :mod:`ai_service` adds an advisory
narrative layer on top of these results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from config import (
    CATEGORY_LIMITS,
    DUPLICATE_WINDOW_DAYS,
    MAX_CLAIM_AGE_DAYS,
    MONTHLY_EMPLOYEE_CAP,
    RECEIPT_REQUIRED_ABOVE,
)
from logger import get_logger

logger = get_logger(__name__)


@dataclass
class PolicyResult:
    """Outcome of evaluating one claim against company policy.

    Attributes:
        compliant: ``True`` when no blocking violation was found.
        violations: Blocking rule breaches that prevent auto-approval.
        warnings: Non-blocking observations for the approver.
        risk_score: 0-100 score; higher means more scrutiny required.
        auto_decision: ``"Approved"``, ``"Pending"`` or ``"Rejected"``.
    """

    compliant: bool = True
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    risk_score: int = 0
    auto_decision: str = "Approved"

    @property
    def flags(self) -> str:
        """All violations and warnings joined into one storable string."""
        return "; ".join(self.violations + self.warnings)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable view of the result."""
        return {
            "compliant": self.compliant,
            "violations": self.violations,
            "warnings": self.warnings,
            "risk_score": self.risk_score,
            "auto_decision": self.auto_decision,
        }


def _parse_date(value: Any) -> date:
    """Coerce a string or date-like value into a :class:`datetime.date`.

    Raises:
        ValueError: If the value cannot be interpreted as a date.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _parse_amount(value: Any) -> float:
    """Coerce an amount into a float, returning ``0.0`` when it is unusable.

    The engine must never crash on malformed input: an unparseable amount
    becomes zero, which the "amount must be greater than zero" rule then
    reports as a violation.
    """
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def check_amount_limit(category: str, amount: float) -> Optional[str]:
    """Return a violation message when the claim exceeds its category ceiling."""
    amount = _parse_amount(amount)
    limit = CATEGORY_LIMITS.get(category)
    if limit is not None and amount > limit:
        return (
            f"Amount {amount:,.2f} exceeds the {category} limit of {limit:,.2f}"
        )
    return None


def check_receipt(amount: float, receipt_ref: str) -> Optional[str]:
    """Return a violation message when a high-value claim has no receipt."""
    if _parse_amount(amount) >= RECEIPT_REQUIRED_ABOVE and not str(receipt_ref).strip():
        return (
            f"Receipt reference is mandatory for claims of "
            f"{RECEIPT_REQUIRED_ABOVE:,.2f} and above"
        )
    return None


def check_claim_age(expense_date: date, today: Optional[date] = None) -> Optional[str]:
    """Return a violation message for future-dated or stale claims."""
    today = today or date.today()
    if expense_date > today:
        return "Expense date is in the future"
    if (today - expense_date).days > MAX_CLAIM_AGE_DAYS:
        return (
            f"Claim is older than the {MAX_CLAIM_AGE_DAYS}-day submission window"
        )
    return None


def find_duplicates(
    expense: Dict[str, Any], existing: List[Dict[str, Any]]
) -> List[int]:
    """Return ids of existing claims that look like duplicates of ``expense``.

    A duplicate is same employee, same category, same amount (to the paisa) and
    an expense date within :data:`config.DUPLICATE_WINDOW_DAYS`.
    """
    try:
        new_date = _parse_date(expense["expense_date"])
    except ValueError:
        return []

    matches: List[int] = []
    for row in existing:
        if row.get("id") == expense.get("id"):
            continue
        if row.get("employee_email") != expense.get("employee_email"):
            continue
        if row.get("category") != expense.get("category"):
            continue
        if abs(_parse_amount(row.get("amount", 0)) - _parse_amount(expense.get("amount", 0))) > 0.01:
            continue
        try:
            other_date = _parse_date(row["expense_date"])
        except (ValueError, KeyError):
            continue
        if abs((other_date - new_date).days) <= DUPLICATE_WINDOW_DAYS:
            matches.append(int(row["id"]))
    return matches


def check_monthly_cap(
    expense: Dict[str, Any], existing: List[Dict[str, Any]]
) -> Optional[str]:
    """Return a warning when the employee's monthly spend crosses the cap."""
    try:
        new_date = _parse_date(expense["expense_date"])
    except ValueError:
        return None

    month_total = _parse_amount(expense.get("amount", 0))
    for row in existing:
        if row.get("employee_email") != expense.get("employee_email"):
            continue
        if row.get("status") == "Rejected":
            continue
        try:
            other = _parse_date(row["expense_date"])
        except (ValueError, KeyError):
            continue
        if (other.year, other.month) == (new_date.year, new_date.month):
            month_total += _parse_amount(row.get("amount", 0))

    if month_total > MONTHLY_EMPLOYEE_CAP:
        return (
            f"Monthly spend {month_total:,.2f} exceeds the per-employee cap of "
            f"{MONTHLY_EMPLOYEE_CAP:,.2f}"
        )
    return None


def check_weekend(expense_date: date, category: str) -> Optional[str]:
    """Return a warning for weekend claims in categories that are weekday-only."""
    weekday_only = {"Office Supplies", "Telecom", "Software"}
    if expense_date.weekday() >= 5 and category in weekday_only:
        return f"{category} claim dated on a weekend ({expense_date:%A})"
    return None


def check_round_amount(amount: float) -> Optional[str]:
    """Return a warning for suspiciously round high-value amounts."""
    amount = _parse_amount(amount)
    if amount >= 5000 and amount.is_integer() and amount % 1000 == 0:
        return "Perfectly round high-value amount - verify against the receipt"
    return None


def evaluate(
    expense: Dict[str, Any],
    existing: Optional[List[Dict[str, Any]]] = None,
    today: Optional[date] = None,
) -> PolicyResult:
    """Evaluate one claim against every policy rule.

    Args:
        expense: The claim being submitted or re-checked.
        existing: Previously stored claims, used for duplicate and cap checks.
        today: Reference date, injectable so tests stay deterministic.

    Returns:
        A :class:`PolicyResult` carrying violations, warnings, a risk score and
        the suggested workflow decision.
    """
    existing = existing or []
    result = PolicyResult()
    amount = _parse_amount(expense.get("amount", 0))
    category = str(expense.get("category", "Miscellaneous"))

    if amount <= 0:
        result.violations.append("Amount must be greater than zero")

    for violation in (
        check_amount_limit(category, amount),
        check_receipt(amount, expense.get("receipt_ref", "")),
    ):
        if violation:
            result.violations.append(violation)

    try:
        expense_date = _parse_date(expense["expense_date"])
    except (ValueError, KeyError):
        result.violations.append("Expense date is missing or badly formatted")
        expense_date = None

    if expense_date is not None:
        age_issue = check_claim_age(expense_date, today)
        if age_issue:
            result.violations.append(age_issue)
        weekend_issue = check_weekend(expense_date, category)
        if weekend_issue:
            result.warnings.append(weekend_issue)

    duplicates = find_duplicates(expense, existing)
    if duplicates:
        ids = ", ".join(f"#{d}" for d in duplicates)
        result.violations.append(f"Possible duplicate of {ids}")

    for warning in (
        check_monthly_cap(expense, existing),
        check_round_amount(amount),
    ):
        if warning:
            result.warnings.append(warning)

    # Risk score: violations weigh far more than warnings, capped at 100.
    result.risk_score = min(100, len(result.violations) * 35 + len(result.warnings) * 12)
    result.compliant = not result.violations

    if result.violations:
        result.auto_decision = "Rejected" if result.risk_score >= 70 else "Pending"
    elif result.warnings:
        result.auto_decision = "Pending"
    else:
        result.auto_decision = "Approved"

    logger.debug(
        "Policy verdict for %s %.2f -> %s (risk %s)",
        category, amount, result.auto_decision, result.risk_score,
    )
    return result


def policy_summary() -> Dict[str, Any]:
    """Return the active policy configuration for display in the UI."""
    return {
        "category_limits": CATEGORY_LIMITS,
        "receipt_required_above": RECEIPT_REQUIRED_ABOVE,
        "max_claim_age_days": MAX_CLAIM_AGE_DAYS,
        "duplicate_window_days": DUPLICATE_WINDOW_DAYS,
        "monthly_employee_cap": MONTHLY_EMPLOYEE_CAP,
        "stale_cutoff": str(date.today() - timedelta(days=MAX_CLAIM_AGE_DAYS)),
    }
