"""SQLite persistence layer.

This module owns the schema and every raw SQL statement in the project. Higher
layers (:mod:`expense_manager`, :mod:`analytics`) talk to these functions only,
which keeps the storage engine swappable.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from config import DATABASE_PATH
from logger import get_logger

logger = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    department    TEXT    NOT NULL DEFAULT 'General',
    manager_email TEXT,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_email  TEXT    NOT NULL,
    employee_name   TEXT    NOT NULL,
    expense_date    TEXT    NOT NULL,
    category        TEXT    NOT NULL,
    vendor          TEXT    NOT NULL DEFAULT '',
    description     TEXT    NOT NULL DEFAULT '',
    amount          REAL    NOT NULL CHECK (amount > 0),
    currency        TEXT    NOT NULL DEFAULT 'INR',
    receipt_ref     TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'Pending',
    flags           TEXT    NOT NULL DEFAULT '',
    risk_score      INTEGER NOT NULL DEFAULT 0,
    ai_notes        TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id  INTEGER,
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'system',
    details     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expenses_employee ON expenses (employee_email);
CREATE INDEX IF NOT EXISTS idx_expenses_date     ON expenses (expense_date);
CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses (category);
"""


def _now() -> str:
    """Return the current timestamp as an ISO-8601 string (second precision)."""
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def get_connection(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection with row access by column name.

    The connection is committed on clean exit and rolled back on any exception.

    Args:
        db_path: Optional override of the configured database file. Tests use
            this to point at a temporary file.
    """
    path = Path(db_path or DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    """Create the schema if it does not already exist."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
    logger.info("Database ready at %s", db_path or DATABASE_PATH)


# --------------------------------------------------------------------------- #
# Expenses
# --------------------------------------------------------------------------- #
def insert_expense(expense: Dict[str, Any], db_path: Optional[Path] = None) -> int:
    """Insert one expense row.

    Args:
        expense: Mapping with the expense fields. Missing optional fields are
            defaulted.
        db_path: Optional database override.

    Returns:
        The primary key of the newly created row.
    """
    now = _now()
    payload = {
        "employee_email": expense["employee_email"],
        "employee_name": expense["employee_name"],
        "expense_date": expense["expense_date"],
        "category": expense["category"],
        "vendor": expense.get("vendor", ""),
        "description": expense.get("description", ""),
        "amount": float(expense["amount"]),
        "currency": expense.get("currency", "INR"),
        "receipt_ref": expense.get("receipt_ref", ""),
        "status": expense.get("status", "Pending"),
        "flags": expense.get("flags", ""),
        "risk_score": int(expense.get("risk_score", 0)),
        "ai_notes": expense.get("ai_notes", ""),
        "created_at": now,
        "updated_at": now,
    }
    columns = ", ".join(payload)
    placeholders = ", ".join(f":{key}" for key in payload)
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            f"INSERT INTO expenses ({columns}) VALUES ({placeholders})", payload
        )
        expense_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO audit_log (expense_id, action, actor, details, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (expense_id, "CREATE", payload["employee_email"],
             f"{payload['category']} {payload['amount']}", now),
        )
    logger.info("Inserted expense #%s for %s", expense_id, payload["employee_email"])
    return expense_id


def fetch_expenses(
    employee_email: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Return expenses matching the supplied filters.

    Every filter is optional; omitted filters are not applied. All comparisons
    are parameterised, so user input never reaches the SQL string.
    """
    query = "SELECT * FROM expenses WHERE 1 = 1"
    params: List[Any] = []
    if employee_email:
        query += " AND employee_email = ?"
        params.append(employee_email)
    if category:
        query += " AND category = ?"
        params.append(category)
    if status:
        query += " AND status = ?"
        params.append(status)
    if start_date:
        query += " AND expense_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND expense_date <= ?"
        params.append(end_date)
    query += " ORDER BY expense_date DESC, id DESC"

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_expense(expense_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Return a single expense by id, or ``None`` when it does not exist."""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    return dict(row) if row else None


def update_status(
    expense_id: int,
    status: str,
    actor: str = "system",
    note: str = "",
    db_path: Optional[Path] = None,
) -> bool:
    """Change an expense's workflow status and write an audit entry.

    Returns:
        ``True`` when a row was updated, ``False`` when the id was not found.
    """
    now = _now()
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "UPDATE expenses SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, expense_id),
        )
        if cursor.rowcount == 0:
            return False
        conn.execute(
            "INSERT INTO audit_log (expense_id, action, actor, details, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (expense_id, f"STATUS->{status}", actor, note, now),
        )
    logger.info("Expense #%s moved to %s by %s", expense_id, status, actor)
    return True


def delete_expense(expense_id: int, actor: str = "system",
                   db_path: Optional[Path] = None) -> bool:
    """Delete an expense and record the deletion in the audit log."""
    with get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        if cursor.rowcount == 0:
            return False
        conn.execute(
            "INSERT INTO audit_log (expense_id, action, actor, details, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (expense_id, "DELETE", actor, "", _now()),
        )
    return True


def fetch_audit_log(limit: int = 100, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return the most recent audit entries, newest first."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# Employees
# --------------------------------------------------------------------------- #
def upsert_employee(
    name: str,
    email: str,
    department: str = "General",
    manager_email: str = "",
    db_path: Optional[Path] = None,
) -> None:
    """Insert an employee, or update the existing record with the same email."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO employees (name, email, department, manager_email, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                name = excluded.name,
                department = excluded.department,
                manager_email = excluded.manager_email
            """,
            (name, email, department, manager_email, _now()),
        )


def fetch_employees(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return every registered employee ordered by name."""
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM employees ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def reset_database(db_path: Optional[Path] = None) -> None:
    """Drop every table and rebuild an empty schema. Used by tests and the CLI."""
    with get_connection(db_path) as conn:
        for table in ("expenses", "employees", "audit_log"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    init_db(db_path)
    logger.warning("Database reset at %s", db_path or DATABASE_PATH)
