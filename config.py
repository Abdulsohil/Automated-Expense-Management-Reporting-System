"""Central configuration for the Automated Expense Management & Reporting System.

All tunable values (categories, policy limits, file paths, AI settings) live here so
that the rest of the codebase never hard-codes business rules.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

try:  # python-dotenv is optional at runtime (Streamlit Cloud uses st.secrets)
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv missing is not fatal
    pass


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
REPORTS_DIR: Path = BASE_DIR / "reports"
SAMPLE_DATA_DIR: Path = BASE_DIR / "sample_data"
LOG_DIR: Path = BASE_DIR / "logs"

for _directory in (DATA_DIR, REPORTS_DIR, LOG_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

DATABASE_PATH: Path = Path(os.getenv("EXPENSE_DB_PATH", DATA_DIR / "expenses.db"))


# --------------------------------------------------------------------------- #
# Business configuration
# --------------------------------------------------------------------------- #
CURRENCY: str = os.getenv("CURRENCY", "INR")
CURRENCY_SYMBOL: str = {"INR": "Rs.", "USD": "$", "EUR": "EUR "}.get(CURRENCY, "")

CATEGORIES: List[str] = [
    "Travel",
    "Accommodation",
    "Meals",
    "Office Supplies",
    "Software",
    "Training",
    "Client Entertainment",
    "Telecom",
    "Miscellaneous",
]

STATUSES: List[str] = ["Pending", "Approved", "Rejected", "Reimbursed"]

#: Maximum amount allowed per single claim, by category.
CATEGORY_LIMITS: Dict[str, float] = {
    "Travel": 25000.0,
    "Accommodation": 12000.0,
    "Meals": 2500.0,
    "Office Supplies": 8000.0,
    "Software": 50000.0,
    "Training": 60000.0,
    "Client Entertainment": 15000.0,
    "Telecom": 3000.0,
    "Miscellaneous": 5000.0,
}

#: Claims at or above this amount must have a receipt reference attached.
RECEIPT_REQUIRED_ABOVE: float = 2000.0

#: Claims older than this many days are rejected as stale.
MAX_CLAIM_AGE_DAYS: int = 90

#: Two claims by the same employee are treated as duplicates when the amount
#: matches, the category matches and the dates fall within this window.
DUPLICATE_WINDOW_DAYS: int = 3

#: Monthly spend ceiling per employee before the claim is escalated.
MONTHLY_EMPLOYEE_CAP: float = 75000.0

#: Keyword -> category map used by the offline (no-AI) categoriser.
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "Travel": ["uber", "ola", "taxi", "cab", "flight", "airline", "indigo", "train",
               "irctc", "fuel", "petrol", "toll", "mileage", "rapido"],
    "Accommodation": ["hotel", "stay", "lodge", "airbnb", "oyo", "marriott", "resort"],
    "Meals": ["restaurant", "lunch", "dinner", "breakfast", "cafe", "coffee",
              "swiggy", "zomato", "food", "snack", "canteen"],
    "Office Supplies": ["stationery", "printer", "paper", "toner", "desk", "chair",
                        "notebook", "pen", "cartridge"],
    "Software": ["license", "subscription", "saas", "aws", "azure", "github",
                 "jetbrains", "figma", "slack", "zoom", "adobe", "openai"],
    "Training": ["course", "certification", "udemy", "coursera", "workshop",
                 "conference", "seminar", "exam", "bootcamp"],
    "Client Entertainment": ["client dinner", "client lunch", "event", "hospitality",
                             "gift", "entertainment"],
    "Telecom": ["mobile", "internet", "broadband", "sim", "recharge", "airtel",
                "jio", "vodafone", "data plan"],
}


# --------------------------------------------------------------------------- #
# AI configuration
# --------------------------------------------------------------------------- #
def get_gemini_api_key() -> str:
    """Return the Gemini API key from the environment or Streamlit secrets.

    Returns:
        The API key, or an empty string when none is configured. An empty string
        makes :mod:`ai_service` fall back to its deterministic rule-based engine.
    """
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key:
        return key
    try:  # Streamlit Cloud stores secrets outside the environment
        import streamlit as st

        return str(st.secrets.get("GEMINI_API_KEY", "")).strip()
    except Exception:
        return ""


GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
AI_TIMEOUT_SECONDS: int = int(os.getenv("AI_TIMEOUT_SECONDS", "30"))

APP_TITLE: str = "Automated Expense Management & Reporting System"
APP_VERSION: str = "1.0.0"
