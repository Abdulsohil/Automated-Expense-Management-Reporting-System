"""AI layer backed by Google Gemini, with a deterministic offline fallback.

Nothing in the application depends on the AI being available. When no
``GEMINI_API_KEY`` is configured, or the API call fails, every function here
degrades to a keyword / heuristic implementation and reports
``source = "rule-based"`` so the UI can be honest about where an answer came
from.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from config import (
    AI_TIMEOUT_SECONDS,
    CATEGORIES,
    CATEGORY_KEYWORDS,
    GEMINI_MODEL,
    get_gemini_api_key,
)
from logger import get_logger

logger = get_logger(__name__)


class AIService:
    """Thin wrapper around the Gemini API with graceful degradation."""

    def __init__(self, api_key: Optional[str] = None, model_name: str = GEMINI_MODEL):
        """Configure the client if an API key and the SDK are both present.

        Args:
            api_key: Explicit key; falls back to the configured environment key.
            model_name: Gemini model identifier.
        """
        self.api_key = (api_key if api_key is not None else get_gemini_api_key()) or ""
        self.model_name = model_name
        self._model = None
        if self.api_key:
            self._init_model()

    def _init_model(self) -> None:
        """Instantiate the Gemini model, swallowing any import/auth failure."""
        try:
            import google.generativeai as genai

            genai.configure(api_key=self.api_key)
            self._model = genai.GenerativeModel(self.model_name)
            logger.info("Gemini model '%s' initialised", self.model_name)
        except Exception as exc:  # pragma: no cover - depends on external SDK
            logger.warning("Gemini unavailable (%s); using rule-based fallback", exc)
            self._model = None

    @property
    def is_available(self) -> bool:
        """``True`` when live AI calls can be made."""
        return self._model is not None

    def _generate(self, prompt: str) -> Optional[str]:
        """Send a prompt to Gemini and return the raw text, or ``None`` on failure."""
        if not self._model:
            return None
        try:
            response = self._model.generate_content(
                prompt,
                request_options={"timeout": AI_TIMEOUT_SECONDS},
            )
            return (response.text or "").strip()
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("Gemini call failed: %s", exc)
            return None

    # ------------------------------------------------------------------ #
    # Categorisation
    # ------------------------------------------------------------------ #
    def categorize(self, description: str, vendor: str = "") -> Dict[str, Any]:
        """Classify a free-text expense into one of the configured categories.

        Args:
            description: What the employee typed.
            vendor: Merchant name, when known.

        Returns:
            Dict with ``category``, ``confidence`` (0-1), ``reason`` and
            ``source`` (``"gemini"`` or ``"rule-based"``).
        """
        text = f"{vendor} {description}".strip()
        if self.is_available:
            prompt = (
                "Classify this business expense into exactly one category.\n"
                f"Categories: {', '.join(CATEGORIES)}\n"
                f"Expense: {text}\n"
                'Reply with JSON only: {"category": "...", "confidence": 0.0, '
                '"reason": "one short sentence"}'
            )
            raw = self._generate(prompt)
            parsed = _extract_json(raw)
            if parsed and parsed.get("category") in CATEGORIES:
                return {
                    "category": parsed["category"],
                    "confidence": float(parsed.get("confidence", 0.8)),
                    "reason": str(parsed.get("reason", "")),
                    "source": "gemini",
                }
        return rule_based_categorize(text)

    # ------------------------------------------------------------------ #
    # Receipt parsing
    # ------------------------------------------------------------------ #
    def parse_receipt_text(self, receipt_text: str) -> Dict[str, Any]:
        """Extract vendor, date and amount from pasted receipt text.

        Args:
            receipt_text: Raw text copied from a receipt or invoice.

        Returns:
            Dict with ``vendor``, ``date``, ``amount``, ``category`` and
            ``source`` keys. Unknown fields come back empty.
        """
        if self.is_available:
            prompt = (
                "Extract structured data from this receipt text.\n"
                f"{receipt_text[:4000]}\n"
                'Reply with JSON only: {"vendor": "", "date": "YYYY-MM-DD", '
                '"amount": 0.0, "category": "one of: '
                f"{', '.join(CATEGORIES)}\"}}"
            )
            raw = self._generate(prompt)
            parsed = _extract_json(raw)
            if parsed:
                parsed["source"] = "gemini"
                parsed.setdefault("vendor", "")
                parsed.setdefault("date", "")
                parsed.setdefault("amount", 0.0)
                parsed.setdefault("category", "Miscellaneous")
                return parsed
        return rule_based_parse_receipt(receipt_text)

    # ------------------------------------------------------------------ #
    # Narrative insights
    # ------------------------------------------------------------------ #
    def spend_insights(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Turn an analytics summary into short management commentary.

        Args:
            summary: Output of :func:`analytics.build_summary`.

        Returns:
            Dict with ``insights`` (list of strings) and ``source``.
        """
        if self.is_available:
            prompt = (
                "You are a finance analyst. Given this expense summary JSON, "
                "write 4 concise, specific bullet insights for a manager. "
                "Mention numbers. No preamble.\n"
                f"{json.dumps(summary, default=str)[:4000]}"
            )
            raw = self._generate(prompt)
            if raw:
                bullets = [
                    line.lstrip("-*• ").strip()
                    for line in raw.splitlines()
                    if line.strip()
                ]
                if bullets:
                    return {"insights": bullets[:6], "source": "gemini"}
        return rule_based_insights(summary)

    def explain_violation(self, expense: Dict[str, Any], violations: List[str]) -> str:
        """Write a short, plain-English note explaining why a claim was flagged."""
        if not violations:
            return "This claim meets every configured policy rule."
        if self.is_available:
            prompt = (
                "Explain to an employee, in 2 polite sentences, why this expense "
                "claim was flagged and what to do next.\n"
                f"Claim: {json.dumps(expense, default=str)}\n"
                f"Violations: {violations}"
            )
            raw = self._generate(prompt)
            if raw:
                return raw
        return (
            "This claim was flagged because: "
            + "; ".join(violations)
            + ". Please correct the details or attach supporting documents and resubmit."
        )


# --------------------------------------------------------------------------- #
# Deterministic fallbacks (also used directly by the test suite)
# --------------------------------------------------------------------------- #
def rule_based_categorize(text: str) -> Dict[str, Any]:
    """Keyword-match an expense description to a category.

    Args:
        text: Combined vendor and description text.

    Returns:
        The same shape as :meth:`AIService.categorize`, with
        ``source = "rule-based"``.
    """
    lowered = text.lower()
    best_category = "Miscellaneous"
    best_hits: List[str] = []

    for category, keywords in CATEGORY_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in lowered]
        if len(hits) > len(best_hits):
            best_category, best_hits = category, hits

    confidence = 0.35 if not best_hits else min(0.95, 0.6 + 0.1 * len(best_hits))
    reason = (
        f"Matched keyword(s): {', '.join(best_hits)}"
        if best_hits
        else "No category keyword matched; defaulted to Miscellaneous"
    )
    return {
        "category": best_category,
        "confidence": round(confidence, 2),
        "reason": reason,
        "source": "rule-based",
    }


_AMOUNT_RE = re.compile(
    r"(?:total|amount|grand\s*total|net\s*payable|paid)\D{0,12}"
    r"([0-9]{1,3}(?:[,\s][0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)",
    re.IGNORECASE,
)
_ANY_AMOUNT_RE = re.compile(r"(?:rs\.?|inr|₹|\$)\s*([0-9][0-9,\.]*)", re.IGNORECASE)
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), ("y", "m", "d")),
    (re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b"), ("d", "m", "y")),
    (re.compile(r"\b(\d{2})-(\d{2})-(\d{4})\b"), ("d", "m", "y")),
]


def rule_based_parse_receipt(receipt_text: str) -> Dict[str, Any]:
    """Extract vendor, date and amount from receipt text using regex only.

    Args:
        receipt_text: Raw receipt or invoice text.

    Returns:
        Dict with ``vendor``, ``date``, ``amount``, ``category`` and
        ``source = "rule-based"``.
    """
    text = receipt_text or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    vendor = lines[0][:80] if lines else ""

    amount = 0.0
    match = _AMOUNT_RE.search(text) or _ANY_AMOUNT_RE.search(text)
    if match:
        try:
            amount = float(match.group(1).replace(",", "").replace(" ", ""))
        except ValueError:
            amount = 0.0

    parsed_date = ""
    for pattern, order in _DATE_PATTERNS:
        found = pattern.search(text)
        if found:
            parts = dict(zip(order, found.groups()))
            parsed_date = f"{parts['y']}-{parts['m']}-{parts['d']}"
            break

    category = rule_based_categorize(text)["category"]
    return {
        "vendor": vendor,
        "date": parsed_date,
        "amount": amount,
        "category": category,
        "source": "rule-based",
    }


def rule_based_insights(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Generate factual insight bullets straight from the analytics summary."""
    insights: List[str] = []
    total = summary.get("total_amount", 0)
    count = summary.get("total_claims", 0)
    insights.append(f"{count} claims recorded for a total of {total:,.2f}.")

    by_category = summary.get("by_category", {})
    if by_category:
        top_cat, top_val = max(by_category.items(), key=lambda kv: kv[1])
        share = (top_val / total * 100) if total else 0
        insights.append(
            f"{top_cat} is the largest category at {top_val:,.2f} ({share:.1f}% of spend)."
        )

    by_employee = summary.get("by_employee", {})
    if by_employee:
        top_emp, emp_val = max(by_employee.items(), key=lambda kv: kv[1])
        insights.append(f"Highest claimant is {top_emp} with {emp_val:,.2f}.")

    flagged = summary.get("flagged_claims", 0)
    if flagged:
        insights.append(
            f"{flagged} claim(s) carry policy flags and need manual review."
        )
    else:
        insights.append("No policy violations detected in the current selection.")

    pending = summary.get("by_status", {}).get("Pending", 0)
    if pending:
        insights.append(f"{pending:,.2f} is still awaiting approval.")

    return {"insights": insights, "source": "rule-based"}


def _extract_json(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Pull the first JSON object out of a model response.

    Gemini often wraps JSON in markdown fences, so the braces are located
    manually rather than trusting the whole string to parse.
    """
    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
