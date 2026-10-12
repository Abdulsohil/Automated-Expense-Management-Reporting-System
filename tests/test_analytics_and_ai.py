"""Tests for the analytics layer, the offline AI fallbacks and report exports."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analytics  # noqa: E402
import report_generator as reports  # noqa: E402
from ai_service import (  # noqa: E402
    AIService,
    _extract_json,
    rule_based_categorize,
    rule_based_insights,
    rule_based_parse_receipt,
)

ROWS = [
    {"id": 1, "employee_email": "a@x.com", "employee_name": "Abdul",
     "expense_date": "2026-09-01", "category": "Travel", "vendor": "Uber",
     "description": "cab", "amount": 1000.0, "currency": "INR", "receipt_ref": "R1",
     "status": "Approved", "flags": "", "risk_score": 0, "ai_notes": "",
     "created_at": "", "updated_at": ""},
    {"id": 2, "employee_email": "a@x.com", "employee_name": "Abdul",
     "expense_date": "2026-09-15", "category": "Travel", "vendor": "Ola",
     "description": "cab", "amount": 1200.0, "currency": "INR", "receipt_ref": "R2",
     "status": "Approved", "flags": "", "risk_score": 0, "ai_notes": "",
     "created_at": "", "updated_at": ""},
    {"id": 3, "employee_email": "a@x.com", "employee_name": "Abdul",
     "expense_date": "2026-10-01", "category": "Travel", "vendor": "IndiGo",
     "description": "flight", "amount": 9000.0, "currency": "INR", "receipt_ref": "R3",
     "status": "Pending", "flags": "Perfectly round high-value amount",
     "risk_score": 12, "ai_notes": "", "created_at": "", "updated_at": ""},
    {"id": 4, "employee_email": "b@x.com", "employee_name": "Waseem",
     "expense_date": "2026-10-02", "category": "Meals", "vendor": "Swiggy",
     "description": "lunch", "amount": 600.0, "currency": "INR", "receipt_ref": "R4",
     "status": "Approved", "flags": "", "risk_score": 0, "ai_notes": "",
     "created_at": "", "updated_at": ""},
]


class TestDataFrame:
    def test_empty_input_returns_empty_frame_with_columns(self):
        frame = analytics.to_dataframe([])
        assert frame.empty
        assert "month" in frame.columns

    def test_derived_columns_are_added(self):
        frame = analytics.to_dataframe(ROWS)
        assert frame.loc[0, "month"] == "2026-09"
        assert frame["flagged"].sum() == 1

    def test_unparseable_dates_are_dropped(self):
        frame = analytics.to_dataframe(ROWS + [dict(ROWS[0], id=9, expense_date="junk")])
        assert len(frame) == len(ROWS)


class TestAggregations:
    def test_totals_by_category(self):
        totals = analytics.totals_by(analytics.to_dataframe(ROWS), "category")
        assert totals["Travel"] == 11200.0
        assert totals["Meals"] == 600.0

    def test_monthly_trend_is_sorted(self):
        trend = analytics.monthly_trend(analytics.to_dataframe(ROWS))
        assert list(trend["month"]) == ["2026-09", "2026-10"]

    def test_top_spenders_ranks_correctly(self):
        spenders = analytics.top_spenders(analytics.to_dataframe(ROWS))
        assert spenders.iloc[0]["employee_name"] == "Abdul"

    def test_anomaly_detection_finds_the_outlier(self):
        anomalies = analytics.detect_anomalies(analytics.to_dataframe(ROWS), z_threshold=1.0)
        assert 3 in list(anomalies["id"])

    def test_small_category_is_skipped_by_anomaly_detection(self):
        anomalies = analytics.detect_anomalies(analytics.to_dataframe(ROWS[3:]))
        assert anomalies.empty


class TestSummary:
    def test_summary_totals(self):
        summary = analytics.build_summary(ROWS)
        assert summary["total_claims"] == 4
        assert summary["total_amount"] == 11800.0
        assert summary["flagged_claims"] == 1
        assert summary["pending_claims"] == 1

    def test_summary_identifies_highest_claim(self):
        assert analytics.build_summary(ROWS)["highest_claim"]["amount"] == 9000.0

    def test_empty_summary_is_safe(self):
        summary = analytics.build_summary([])
        assert summary["total_claims"] == 0
        assert summary["highest_claim"] is None


class TestRuleBasedAI:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Uber ride to the airport", "Travel"),
            ("Marriott hotel stay", "Accommodation"),
            ("Swiggy team lunch", "Meals"),
            ("JetBrains annual licence", "Software"),
            ("Coursera certification course", "Training"),
            ("Airtel broadband bill", "Telecom"),
            ("something entirely unrelated", "Miscellaneous"),
        ],
    )
    def test_keyword_categorisation(self, text, expected):
        assert rule_based_categorize(text)["category"] == expected

    def test_categorisation_reports_its_source(self):
        assert rule_based_categorize("uber")["source"] == "rule-based"

    def test_receipt_parsing_extracts_fields(self):
        parsed = rule_based_parse_receipt(
            "SkyLine Hotels Pvt Ltd\nInvoice No: INV-2026-0142\n"
            "Date: 2026-09-14\nTotal: Rs. 9,408.00\n"
        )
        assert parsed["vendor"].startswith("SkyLine")
        assert parsed["date"] == "2026-09-14"
        assert parsed["amount"] == 9408.0

    def test_receipt_parsing_handles_empty_text(self):
        parsed = rule_based_parse_receipt("")
        assert parsed["amount"] == 0.0
        assert parsed["vendor"] == ""

    def test_insights_mention_totals(self):
        bullets = rule_based_insights(analytics.build_summary(ROWS))["insights"]
        assert any("11,800" in b for b in bullets)

    def test_service_without_key_is_unavailable(self):
        service = AIService(api_key="")
        assert service.is_available is False
        assert service.categorize("uber ride")["source"] == "rule-based"

    def test_explain_violation_without_ai(self):
        service = AIService(api_key="")
        assert "meets every configured policy rule" in service.explain_violation({}, [])
        assert "Over limit" in service.explain_violation({}, ["Over limit"])

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ('```json\n{"category": "Travel"}\n```', {"category": "Travel"}),
            ("no json here", None),
            (None, None),
            ("{broken json", None),
        ],
    )
    def test_json_extraction(self, raw, expected):
        assert _extract_json(raw) == expected


class TestReports:
    def test_csv_export_has_a_header_row(self):
        content = reports.to_csv_bytes(ROWS).decode("utf-8")
        assert content.startswith("id,expense_date")
        assert "Abdul" in content

    def test_excel_export_is_a_valid_workbook(self):
        data = reports.to_excel_bytes(ROWS)
        assert data[:2] == b"PK"  # xlsx files are zip archives
        assert len(data) > 1000

    def test_markdown_export_contains_metrics(self):
        markdown = reports.to_markdown(ROWS, insights=["Spend is concentrated in Travel."])
        assert "Key Metrics" in markdown
        assert "Spend is concentrated in Travel." in markdown

    def test_saved_report_lands_on_disk(self, tmp_path):
        path = reports.save_report(ROWS, "csv", out_dir=tmp_path)
        assert path.exists() and path.suffix == ".csv"

    def test_unsupported_format_raises(self, tmp_path):
        with pytest.raises(ValueError):
            reports.save_report(ROWS, "docx", out_dir=tmp_path)

    def test_pdf_export_when_reportlab_present(self, tmp_path):
        pytest.importorskip("reportlab")
        data = reports.to_pdf_bytes(ROWS, insights=["Travel dominates spend."])
        assert data[:4] == b"%PDF"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
