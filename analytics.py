"""Pandas-based analytics over the expense ledger.

Every function accepts the plain list of dicts returned by
:func:`database.fetch_expenses` so the analytics layer stays independent of the
storage engine.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from logger import get_logger

logger = get_logger(__name__)

EXPECTED_COLUMNS = [
    "id", "employee_email", "employee_name", "expense_date", "category",
    "vendor", "description", "amount", "currency", "receipt_ref", "status",
    "flags", "risk_score", "ai_notes", "created_at", "updated_at",
]


def to_dataframe(expenses: List[Dict[str, Any]]) -> pd.DataFrame:
    """Convert raw expense rows into a typed DataFrame.

    Adds the derived columns ``month`` (YYYY-MM), ``weekday`` and ``flagged``.
    An empty input returns an empty frame with the full column set, so callers
    never have to special-case "no data".
    """
    if not expenses:
        return pd.DataFrame(columns=EXPECTED_COLUMNS + ["month", "weekday", "flagged"])

    frame = pd.DataFrame(expenses)
    for column in EXPECTED_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""

    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0)
    frame["risk_score"] = pd.to_numeric(frame["risk_score"], errors="coerce").fillna(0)
    frame["expense_date"] = pd.to_datetime(frame["expense_date"], errors="coerce")
    frame = frame.dropna(subset=["expense_date"])
    frame["month"] = frame["expense_date"].dt.strftime("%Y-%m")
    frame["weekday"] = frame["expense_date"].dt.day_name()
    frame["flagged"] = frame["flags"].astype(str).str.strip().ne("")
    return frame


def totals_by(frame: pd.DataFrame, column: str) -> Dict[str, float]:
    """Return ``{value: summed amount}`` for the given column, highest first."""
    if frame.empty or column not in frame.columns:
        return {}
    grouped = frame.groupby(column)["amount"].sum().sort_values(ascending=False)
    return {str(key): round(float(value), 2) for key, value in grouped.items()}


def monthly_trend(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a month-by-month table of total amount and claim count."""
    if frame.empty:
        return pd.DataFrame(columns=["month", "amount", "claims"])
    trend = (
        frame.groupby("month")
        .agg(amount=("amount", "sum"), claims=("id", "count"))
        .reset_index()
        .sort_values("month")
    )
    trend["amount"] = trend["amount"].round(2)
    return trend


def top_spenders(frame: pd.DataFrame, limit: int = 5) -> pd.DataFrame:
    """Return the highest-claiming employees with their totals and averages."""
    if frame.empty:
        return pd.DataFrame(columns=["employee_name", "amount", "claims", "average"])
    result = (
        frame.groupby("employee_name")
        .agg(amount=("amount", "sum"), claims=("id", "count"))
        .reset_index()
    )
    result["average"] = (result["amount"] / result["claims"]).round(2)
    result["amount"] = result["amount"].round(2)
    return result.sort_values("amount", ascending=False).head(limit)


def detect_anomalies(frame: pd.DataFrame, z_threshold: float = 2.0) -> pd.DataFrame:
    """Flag statistically unusual claims within each category.

    A claim is anomalous when its amount is more than ``z_threshold`` standard
    deviations above the mean for its category. Categories with fewer than three
    claims are skipped, because the statistics are not meaningful there.

    Returns:
        The anomalous rows with an added ``z_score`` column, most extreme first.
    """
    if frame.empty:
        return pd.DataFrame(columns=list(frame.columns) + ["z_score"])

    chunks = []
    for category, group in frame.groupby("category"):
        if len(group) < 3:
            continue
        std = group["amount"].std(ddof=0)
        if not std or pd.isna(std) or std == 0:
            continue
        scored = group.copy()
        scored["z_score"] = ((scored["amount"] - group["amount"].mean()) / std).round(2)
        chunks.append(scored[scored["z_score"] >= z_threshold])

    if not chunks:
        return pd.DataFrame(columns=list(frame.columns) + ["z_score"])
    return pd.concat(chunks).sort_values("z_score", ascending=False)


def build_summary(expenses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Produce the headline summary consumed by the dashboard and reports.

    Returns:
        Dict with totals, per-category / per-employee / per-status breakdowns,
        the monthly trend and counts of flagged and pending claims.
    """
    frame = to_dataframe(expenses)
    if frame.empty:
        return {
            "total_claims": 0, "total_amount": 0.0, "average_claim": 0.0,
            "by_category": {}, "by_employee": {}, "by_status": {},
            "monthly_trend": [], "flagged_claims": 0, "pending_claims": 0,
            "highest_claim": None,
        }

    total_amount = float(frame["amount"].sum())
    highest = frame.loc[frame["amount"].idxmax()]
    return {
        "total_claims": int(len(frame)),
        "total_amount": round(total_amount, 2),
        "average_claim": round(total_amount / len(frame), 2),
        "by_category": totals_by(frame, "category"),
        "by_employee": totals_by(frame, "employee_name"),
        "by_status": totals_by(frame, "status"),
        "monthly_trend": monthly_trend(frame).to_dict("records"),
        "flagged_claims": int(frame["flagged"].sum()),
        "pending_claims": int((frame["status"] == "Pending").sum()),
        "highest_claim": {
            "id": int(highest["id"]) if str(highest["id"]).isdigit() else highest["id"],
            "employee": highest["employee_name"],
            "category": highest["category"],
            "amount": round(float(highest["amount"]), 2),
            "date": highest["expense_date"].strftime("%Y-%m-%d"),
        },
    }
