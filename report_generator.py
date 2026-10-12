"""Export the expense ledger to CSV, Excel, PDF and Markdown.

Each builder returns raw ``bytes`` (or ``str`` for Markdown) so the same code
serves both Streamlit download buttons and the CLI, which simply writes the
bytes to disk.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from analytics import build_summary, monthly_trend, to_dataframe, top_spenders
from config import APP_TITLE, CURRENCY, REPORTS_DIR
from logger import get_logger

logger = get_logger(__name__)

REPORT_COLUMNS = [
    "id", "expense_date", "employee_name", "employee_email", "category",
    "vendor", "description", "amount", "currency", "status", "risk_score", "flags",
]


def _report_frame(expenses: List[Dict[str, Any]]) -> pd.DataFrame:
    """Return a tidy, human-readable frame limited to the report columns."""
    frame = to_dataframe(expenses)
    if frame.empty:
        return pd.DataFrame(columns=REPORT_COLUMNS)
    output = frame.copy()
    output["expense_date"] = output["expense_date"].dt.strftime("%Y-%m-%d")
    return output[[c for c in REPORT_COLUMNS if c in output.columns]]


def to_csv_bytes(expenses: List[Dict[str, Any]]) -> bytes:
    """Serialise the ledger as UTF-8 CSV bytes."""
    return _report_frame(expenses).to_csv(index=False).encode("utf-8")


def to_excel_bytes(expenses: List[Dict[str, Any]]) -> bytes:
    """Build a multi-sheet Excel workbook.

    Sheets: ``Expenses`` (full ledger), ``Summary`` (key metrics),
    ``By Category``, ``Monthly Trend`` and ``Top Spenders``.
    """
    frame = _report_frame(expenses)
    summary = build_summary(expenses)
    full = to_dataframe(expenses)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Expenses", index=False)

        pd.DataFrame(
            [
                ("Report generated", datetime.now().strftime("%Y-%m-%d %H:%M")),
                ("Currency", CURRENCY),
                ("Total claims", summary["total_claims"]),
                ("Total amount", summary["total_amount"]),
                ("Average claim", summary["average_claim"]),
                ("Pending claims", summary["pending_claims"]),
                ("Flagged claims", summary["flagged_claims"]),
            ],
            columns=["Metric", "Value"],
        ).to_excel(writer, sheet_name="Summary", index=False)

        pd.DataFrame(
            list(summary["by_category"].items()), columns=["Category", "Amount"]
        ).to_excel(writer, sheet_name="By Category", index=False)

        monthly_trend(full).to_excel(writer, sheet_name="Monthly Trend", index=False)
        top_spenders(full, limit=10).to_excel(
            writer, sheet_name="Top Spenders", index=False
        )

        for sheet in writer.sheets.values():
            for column_cells in sheet.columns:
                width = max(
                    (len(str(cell.value)) for cell in column_cells if cell.value), default=10
                )
                sheet.column_dimensions[column_cells[0].column_letter].width = min(
                    42, width + 2
                )

    return buffer.getvalue()


def to_markdown(expenses: List[Dict[str, Any]], insights: Optional[List[str]] = None) -> str:
    """Build a Markdown summary report, optionally including AI insights."""
    summary = build_summary(expenses)
    lines = [
        f"# {APP_TITLE} - Expense Report",
        "",
        f"*Generated on {datetime.now():%d %B %Y, %H:%M}*",
        "",
        "## Key Metrics",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Total claims | {summary['total_claims']} |",
        f"| Total amount | {summary['total_amount']:,.2f} {CURRENCY} |",
        f"| Average claim | {summary['average_claim']:,.2f} {CURRENCY} |",
        f"| Pending claims | {summary['pending_claims']} |",
        f"| Flagged claims | {summary['flagged_claims']} |",
        "",
    ]

    if summary["by_category"]:
        lines += ["## Spend by Category", "", "| Category | Amount |", "| --- | --- |"]
        lines += [f"| {k} | {v:,.2f} |" for k, v in summary["by_category"].items()]
        lines.append("")

    if summary["by_employee"]:
        lines += ["## Spend by Employee", "", "| Employee | Amount |", "| --- | --- |"]
        lines += [f"| {k} | {v:,.2f} |" for k, v in summary["by_employee"].items()]
        lines.append("")

    if insights:
        lines += ["## Insights", ""] + [f"- {item}" for item in insights] + [""]

    return "\n".join(lines)


def to_pdf_bytes(expenses: List[Dict[str, Any]], insights: Optional[List[str]] = None) -> bytes:
    """Render a formatted PDF report.

    Requires ``reportlab``.

    Raises:
        RuntimeError: If reportlab is not installed.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
        )
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "reportlab is required for PDF export. Install it with "
            "'pip install reportlab'."
        ) from exc

    summary = build_summary(expenses)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title="Expense Report",
    )
    styles = getSampleStyleSheet()
    heading = ParagraphStyle(
        "Heading", parent=styles["Heading1"], fontSize=16,
        textColor=colors.HexColor("#1f3b57"), spaceAfter=6,
    )
    sub = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=9,
                         textColor=colors.grey, spaceAfter=12)

    story: List[Any] = [
        Paragraph(f"{APP_TITLE}", heading),
        Paragraph(f"Expense report generated on {datetime.now():%d %B %Y, %H:%M}", sub),
    ]

    metrics = [
        ["Metric", "Value"],
        ["Total claims", f"{summary['total_claims']}"],
        ["Total amount", f"{summary['total_amount']:,.2f} {CURRENCY}"],
        ["Average claim", f"{summary['average_claim']:,.2f} {CURRENCY}"],
        ["Pending claims", f"{summary['pending_claims']}"],
        ["Flagged claims", f"{summary['flagged_claims']}"],
    ]
    metrics_table = Table(metrics, colWidths=[70 * mm, 95 * mm])
    metrics_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3b57")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bfccd9")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f8")]),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story += [metrics_table, Spacer(1, 10 * mm)]

    if summary["by_category"]:
        story.append(Paragraph("Spend by Category", styles["Heading2"]))
        rows = [["Category", f"Amount ({CURRENCY})"]] + [
            [k, f"{v:,.2f}"] for k, v in summary["by_category"].items()
        ]
        category_table = Table(rows, colWidths=[85 * mm, 80 * mm])
        category_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#34688f")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bfccd9")),
                    ("PADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story += [category_table, Spacer(1, 8 * mm)]

    if insights:
        story.append(Paragraph("Insights", styles["Heading2"]))
        for item in insights:
            story.append(Paragraph(f"• {item}", styles["Normal"]))
            story.append(Spacer(1, 2 * mm))
        story.append(Spacer(1, 6 * mm))

    frame = _report_frame(expenses)
    if not frame.empty:
        story.append(Paragraph("Claim Detail (most recent 25)", styles["Heading2"]))
        columns = ["id", "expense_date", "employee_name", "category", "amount", "status"]
        detail = [["ID", "Date", "Employee", "Category", "Amount", "Status"]]
        for _, row in frame[columns].head(25).iterrows():
            detail.append(
                [
                    str(row["id"]), str(row["expense_date"]),
                    str(row["employee_name"])[:22], str(row["category"]),
                    f"{float(row['amount']):,.2f}", str(row["status"]),
                ]
            )
        detail_table = Table(
            detail, colWidths=[12 * mm, 24 * mm, 42 * mm, 36 * mm, 28 * mm, 23 * mm]
        )
        detail_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#34688f")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("ALIGN", (4, 1), (4, -1), "RIGHT"),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bfccd9")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                     [colors.white, colors.HexColor("#f2f5f8")]),
                    ("PADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(detail_table)

    doc.build(story)
    return buffer.getvalue()


def save_report(
    expenses: List[Dict[str, Any]],
    fmt: str = "csv",
    insights: Optional[List[str]] = None,
    out_dir: Optional[Path] = None,
) -> Path:
    """Write a report to disk and return its path.

    Args:
        expenses: Rows to include.
        fmt: One of ``csv``, ``xlsx``, ``pdf``, ``md``.
        insights: Optional insight bullets for the PDF/Markdown reports.
        out_dir: Destination directory; defaults to ``reports/``.

    Raises:
        ValueError: On an unsupported format.
    """
    out_dir = Path(out_dir or REPORTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"expense_report_{stamp}.{fmt}"

    if fmt == "csv":
        path.write_bytes(to_csv_bytes(expenses))
    elif fmt == "xlsx":
        path.write_bytes(to_excel_bytes(expenses))
    elif fmt == "pdf":
        path.write_bytes(to_pdf_bytes(expenses, insights))
    elif fmt == "md":
        path.write_text(to_markdown(expenses, insights), encoding="utf-8")
    else:
        raise ValueError(f"Unsupported report format: {fmt}")

    logger.info("Report written to %s", path)
    return path
