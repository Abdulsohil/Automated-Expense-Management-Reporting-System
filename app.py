"""Streamlit front end for the Automated Expense Management & Reporting System.

Run locally with::

    python -m streamlit run app.py
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

import pandas as pd
import plotly.express as px
import streamlit as st

import analytics
import report_generator as reports
from ai_service import AIService
from config import (
    APP_TITLE,
    APP_VERSION,
    CATEGORIES,
    CURRENCY,
    CURRENCY_SYMBOL,
    SAMPLE_DATA_DIR,
    STATUSES,
)
from expense_manager import ExpenseManager, ValidationError
from policy_engine import policy_summary

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)

PLOT_SEQUENCE = px.colors.qualitative.Set2


# --------------------------------------------------------------------------- #
# Cached resources
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner=False)
def get_manager() -> ExpenseManager:
    """Return a process-wide :class:`ExpenseManager` (cached across reruns)."""
    return ExpenseManager(ai=AIService())


def money(value: float) -> str:
    """Format a number as a currency string."""
    return f"{CURRENCY_SYMBOL}{value:,.2f}"


def load_expenses(manager: ExpenseManager) -> List[Dict[str, Any]]:
    """Fetch every stored claim."""
    return manager.list_expenses()


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def render_sidebar(manager: ExpenseManager) -> str:
    """Draw the sidebar and return the selected page name."""
    with st.sidebar:
        st.title("💼 Expense System")
        st.caption(f"v{APP_VERSION} · currency: {CURRENCY}")

        page = st.radio(
            "Navigate",
            [
                "📊 Dashboard",
                "➕ Submit Expense",
                "🧾 Receipt Parser",
                "✅ Review & Approve",
                "📥 Reports & Export",
                "⚙️ Policy & Data",
            ],
            label_visibility="collapsed",
        )

        st.divider()
        if manager.ai.is_available:
            st.success("Gemini AI: connected")
        else:
            st.info("Gemini AI: offline — rule-based engine in use")
            st.caption("Add GEMINI_API_KEY to .env or Streamlit secrets to enable AI.")

        st.divider()
        st.caption("**Team — One Size Team**")
        st.caption("Shaik Abdul Sohial")
        st.caption("Mohamed Waseem")
    return page


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def page_dashboard(manager: ExpenseManager) -> None:
    """Analytics dashboard: KPIs, charts, anomalies and AI insights."""
    st.header("📊 Spend Dashboard")
    expenses = load_expenses(manager)

    if not expenses:
        st.info(
            "No expenses yet. Add one from **Submit Expense**, or load the demo "
            "dataset from **Policy & Data**."
        )
        return

    frame = analytics.to_dataframe(expenses)

    with st.expander("Filters", expanded=False):
        col1, col2, col3 = st.columns(3)
        min_date = frame["expense_date"].min().date()
        max_date = frame["expense_date"].max().date()
        date_range = col1.date_input(
            "Date range", value=(min_date, max_date),
            min_value=min_date, max_value=max_date,
        )
        picked_categories = col2.multiselect(
            "Categories", sorted(frame["category"].unique()),
            default=sorted(frame["category"].unique()),
        )
        picked_statuses = col3.multiselect(
            "Status", sorted(frame["status"].unique()),
            default=sorted(frame["status"].unique()),
        )

    if isinstance(date_range, tuple) and len(date_range) == 2:
        start, end = date_range
        frame = frame[
            (frame["expense_date"].dt.date >= start)
            & (frame["expense_date"].dt.date <= end)
        ]
    if picked_categories:
        frame = frame[frame["category"].isin(picked_categories)]
    if picked_statuses:
        frame = frame[frame["status"].isin(picked_statuses)]

    if frame.empty:
        st.warning("No claims match the selected filters.")
        return

    filtered = frame.to_dict("records")
    summary = analytics.build_summary(filtered)

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("Total spend", money(summary["total_amount"]))
    kpi2.metric("Claims", summary["total_claims"])
    kpi3.metric("Average claim", money(summary["average_claim"]))
    kpi4.metric(
        "Flagged", summary["flagged_claims"],
        delta=f"{summary['pending_claims']} pending", delta_color="off",
    )

    st.divider()
    left, right = st.columns(2)

    with left:
        st.subheader("Spend by category")
        category_frame = pd.DataFrame(
            list(summary["by_category"].items()), columns=["Category", "Amount"]
        )
        fig = px.pie(
            category_frame, names="Category", values="Amount", hole=0.45,
            color_discrete_sequence=PLOT_SEQUENCE,
        )
        # Horizontal labels inside the slices, and no label at all on slices too
        # small to hold one legibly - those are covered by the legend instead.
        fig.update_traces(
            textposition="inside",
            textinfo="percent+label",
            insidetextorientation="horizontal",
            texttemplate="%{label}<br>%{percent}",
            hovertemplate="%{label}: %{value:,.2f}<extra></extra>",
        )
        fig.update_layout(
            uniformtext_minsize=11,
            uniformtext_mode="hide",
            showlegend=True,
            legend=dict(orientation="h", yanchor="top", y=-0.05, x=0),
            margin=dict(t=10, b=10, l=10, r=10),
        )
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.subheader("Monthly trend")
        trend = analytics.monthly_trend(frame)
        trend["label"] = pd.to_datetime(trend["month"] + "-01").dt.strftime("%b %Y")
        fig = px.bar(
            trend, x="label", y="amount", text_auto=".3s",
            color_discrete_sequence=[PLOT_SEQUENCE[0]],
            hover_data={"claims": True},
        )
        # Without this Plotly reads "2026-09" as a timestamp and draws a
        # continuous time axis with meaningless intermediate tick labels.
        fig.update_layout(
            xaxis_type="category",
            xaxis_title="", yaxis_title=f"Amount ({CURRENCY})",
            margin=dict(t=10, b=10, l=10, r=10),
        )
        st.plotly_chart(fig, use_container_width=True)

    left2, right2 = st.columns(2)

    with left2:
        st.subheader("Top spenders")
        spenders = analytics.top_spenders(frame, limit=8)
        fig = px.bar(
            spenders.sort_values("amount"), x="amount", y="employee_name",
            orientation="h", text_auto=".2s",
            color_discrete_sequence=[PLOT_SEQUENCE[1]],
        )
        fig.update_layout(
            xaxis_title=f"Amount ({CURRENCY})", yaxis_title="",
            margin=dict(t=10, b=10, l=10, r=10),
        )
        st.plotly_chart(fig, use_container_width=True)

    with right2:
        st.subheader("Status mix")
        status_frame = pd.DataFrame(
            list(summary["by_status"].items()), columns=["Status", "Amount"]
        )
        fig = px.bar(
            status_frame, x="Status", y="Amount", color="Status",
            color_discrete_sequence=PLOT_SEQUENCE, text_auto=".2s",
        )
        fig.update_layout(
            showlegend=False, xaxis_title="", yaxis_title=f"Amount ({CURRENCY})",
            margin=dict(t=10, b=10, l=10, r=10),
        )
        st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("🔍 Statistical anomalies")
    anomalies = analytics.detect_anomalies(frame)
    if anomalies.empty:
        st.success("No claims deviate significantly from their category average.")
    else:
        st.warning(f"{len(anomalies)} claim(s) are unusually large for their category.")
        st.dataframe(
            anomalies[
                ["id", "expense_date", "employee_name", "category", "amount", "z_score"]
            ],
            use_container_width=True, hide_index=True,
        )

    st.subheader("🤖 Insights")
    if st.button("Generate insights", type="primary"):
        with st.spinner("Analysing spend…"):
            result = manager.ai.spend_insights(summary)
        for bullet in result["insights"]:
            st.markdown(f"- {bullet}")
        st.caption(f"Source: {result['source']}")


def page_submit(manager: ExpenseManager) -> None:
    """Manual claim submission form with live policy feedback."""
    st.header("➕ Submit an Expense")

    with st.form("submit_expense", clear_on_submit=False):
        col1, col2 = st.columns(2)
        name = col1.text_input("Employee name *", value=st.session_state.get("last_name", ""))
        email = col2.text_input("Employee email *", value=st.session_state.get("last_email", ""))

        col3, col4, col5 = st.columns(3)
        expense_date = col3.date_input("Expense date *", value=date.today(),
                                       max_value=date.today())
        amount = col4.number_input(f"Amount ({CURRENCY}) *", min_value=0.0,
                                   step=100.0, format="%.2f")
        category = col5.selectbox("Category", ["(auto-detect)"] + CATEGORIES)

        col6, col7 = st.columns(2)
        vendor = col6.text_input("Vendor / merchant")
        receipt_ref = col7.text_input("Receipt reference", placeholder="e.g. INV-2026-0142")
        description = st.text_area("Description", placeholder="Client visit — airport taxi")

        submitted = st.form_submit_button("Submit claim", type="primary")

    if not submitted:
        return

    payload = {
        "employee_name": name,
        "employee_email": email,
        "expense_date": expense_date,
        "amount": amount,
        "category": "" if category == "(auto-detect)" else category,
        "vendor": vendor,
        "description": description,
        "receipt_ref": receipt_ref,
    }

    try:
        expense_id, verdict = manager.submit(payload)
    except ValidationError as exc:
        st.error(str(exc))
        return

    st.session_state["last_name"] = name
    st.session_state["last_email"] = email

    stored = manager.get(expense_id)
    st.success(f"Claim #{expense_id} recorded as **{verdict.auto_decision}**.")
    st.caption(f"Category: {stored['category']} · Risk score: {verdict.risk_score}/100")

    if verdict.violations:
        st.error("**Policy violations**\n\n" + "\n".join(f"- {v}" for v in verdict.violations))
    if verdict.warnings:
        st.warning("**Warnings**\n\n" + "\n".join(f"- {w}" for w in verdict.warnings))
    if verdict.compliant and not verdict.warnings:
        st.info("This claim passed every policy rule.")

    if stored.get("ai_notes"):
        st.caption(stored["ai_notes"])

    if verdict.violations or verdict.warnings:
        with st.expander("Explain this in plain English"):
            st.write(manager.explain(expense_id))


def page_receipt(manager: ExpenseManager) -> None:
    """Paste receipt text and let the parser pre-fill a claim."""
    st.header("🧾 Receipt Parser")
    st.caption(
        "Paste the text from a receipt or invoice. Gemini extracts the fields when "
        "configured; otherwise a regex parser does the job."
    )

    sample = (
        "SkyLine Hotels Pvt Ltd\n"
        "GSTIN: 29AABCS1429B1ZQ\n"
        "Invoice No: INV-2026-0142\n"
        "Date: 2026-09-14\n"
        "Deluxe Room x 2 nights\n"
        "Subtotal: 8,400.00\n"
        "GST 12%: 1,008.00\n"
        "Total: Rs. 9,408.00\n"
    )
    receipt_text = st.text_area("Receipt text", value=sample, height=220)

    if st.button("Parse receipt", type="primary"):
        with st.spinner("Extracting fields…"):
            parsed = manager.ai.parse_receipt_text(receipt_text)
        st.session_state["parsed_receipt"] = parsed

    parsed = st.session_state.get("parsed_receipt")
    if not parsed:
        return

    st.success(f"Fields extracted ({parsed.get('source', 'unknown')}).")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Vendor", str(parsed.get("vendor", ""))[:22] or "—")
    col2.metric("Date", parsed.get("date") or "—")
    col3.metric("Amount", money(float(parsed.get("amount") or 0)))
    col4.metric("Category", parsed.get("category", "—"))

    st.divider()
    st.subheader("Confirm and submit")
    with st.form("receipt_submit"):
        col1, col2 = st.columns(2)
        name = col1.text_input("Employee name *", value=st.session_state.get("last_name", ""))
        email = col2.text_input("Employee email *", value=st.session_state.get("last_email", ""))

        col3, col4 = st.columns(2)
        try:
            default_date = date.fromisoformat(str(parsed.get("date"))[:10])
        except (ValueError, TypeError):
            default_date = date.today()
        expense_date = col3.date_input("Expense date", value=min(default_date, date.today()))
        amount = col4.number_input(
            f"Amount ({CURRENCY})", min_value=0.0,
            value=float(parsed.get("amount") or 0), step=100.0, format="%.2f",
        )

        guessed = parsed.get("category", "Miscellaneous")
        category = st.selectbox(
            "Category", CATEGORIES,
            index=CATEGORIES.index(guessed) if guessed in CATEGORIES else len(CATEGORIES) - 1,
        )
        receipt_ref = st.text_input("Receipt reference", value=str(parsed.get("vendor", ""))[:30])

        if st.form_submit_button("Submit parsed claim", type="primary"):
            try:
                expense_id, verdict = manager.submit(
                    {
                        "employee_name": name,
                        "employee_email": email,
                        "expense_date": expense_date,
                        "amount": amount,
                        "category": category,
                        "vendor": parsed.get("vendor", ""),
                        "description": "Submitted via receipt parser",
                        "receipt_ref": receipt_ref,
                    },
                    auto_categorize=False,
                )
            except ValidationError as exc:
                st.error(str(exc))
            else:
                st.success(f"Claim #{expense_id} recorded as {verdict.auto_decision}.")


def page_review(manager: ExpenseManager) -> None:
    """Claim register with approve / reject / reimburse actions."""
    st.header("✅ Review & Approve")
    expenses = load_expenses(manager)
    if not expenses:
        st.info("Nothing to review yet.")
        return

    col1, col2 = st.columns([1, 2])
    status_filter = col1.selectbox("Status", ["All"] + STATUSES)
    search = col2.text_input("Search employee, vendor or description").strip().lower()

    rows = expenses
    if status_filter != "All":
        rows = [r for r in rows if r["status"] == status_filter]
    if search:
        rows = [
            r for r in rows
            if search in f"{r['employee_name']} {r['employee_email']} "
                         f"{r['vendor']} {r['description']}".lower()
        ]

    st.caption(f"{len(rows)} claim(s)")
    if not rows:
        return

    display = analytics.to_dataframe(rows)
    display = display[
        ["id", "expense_date", "employee_name", "category", "amount",
         "status", "risk_score", "flags"]
    ].copy()
    display["expense_date"] = display["expense_date"].dt.strftime("%Y-%m-%d")
    st.dataframe(display, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Take action")
    ids = [int(r["id"]) for r in rows]
    col1, col2, col3 = st.columns([1, 1, 2])
    selected = col1.selectbox("Claim ID", ids)
    new_status = col2.selectbox("New status", STATUSES)
    note = col3.text_input("Approver note", placeholder="Optional comment for the audit log")

    action_col, explain_col = st.columns([1, 1])
    if action_col.button("Update status", type="primary"):
        manager.set_status(int(selected), new_status, actor="manager", note=note)
        st.success(f"Claim #{selected} is now {new_status}.")
        st.rerun()

    if explain_col.button("Explain flags"):
        st.info(manager.explain(int(selected)))


def page_reports(manager: ExpenseManager) -> None:
    """Report preview plus CSV / Excel / PDF / Markdown downloads."""
    st.header("📥 Reports & Export")
    expenses = load_expenses(manager)
    if not expenses:
        st.info("Add some expenses before generating a report.")
        return

    col1, col2 = st.columns(2)
    window = col1.selectbox(
        "Period", ["All time", "Last 30 days", "Last 90 days", "Current year"]
    )
    status_filter = col2.multiselect("Include statuses", STATUSES, default=STATUSES)

    frame = analytics.to_dataframe(expenses)
    today = date.today()
    if window == "Last 30 days":
        frame = frame[frame["expense_date"].dt.date >= today - timedelta(days=30)]
    elif window == "Last 90 days":
        frame = frame[frame["expense_date"].dt.date >= today - timedelta(days=90)]
    elif window == "Current year":
        frame = frame[frame["expense_date"].dt.year == today.year]
    if status_filter:
        frame = frame[frame["status"].isin(status_filter)]

    if frame.empty:
        st.warning("No claims in the selected period.")
        return

    selection = frame.to_dict("records")
    summary = analytics.build_summary(selection)

    col1, col2, col3 = st.columns(3)
    col1.metric("Claims in report", summary["total_claims"])
    col2.metric("Total", money(summary["total_amount"]))
    col3.metric("Flagged", summary["flagged_claims"])

    include_ai = st.checkbox("Include insight commentary in PDF / Markdown", value=True)
    insights: List[str] = []
    if include_ai:
        insights = manager.ai.spend_insights(summary)["insights"]
        with st.expander("Preview insights", expanded=False):
            for bullet in insights:
                st.markdown(f"- {bullet}")

    stamp = date.today().isoformat()
    col1, col2, col3, col4 = st.columns(4)
    col1.download_button(
        "⬇️ CSV", reports.to_csv_bytes(selection),
        file_name=f"expenses_{stamp}.csv", mime="text/csv", use_container_width=True,
    )
    col2.download_button(
        "⬇️ Excel", reports.to_excel_bytes(selection),
        file_name=f"expense_report_{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    try:
        pdf_bytes = reports.to_pdf_bytes(selection, insights)
        col3.download_button(
            "⬇️ PDF", pdf_bytes, file_name=f"expense_report_{stamp}.pdf",
            mime="application/pdf", use_container_width=True,
        )
    except RuntimeError as exc:
        col3.button("⬇️ PDF", disabled=True, use_container_width=True, help=str(exc))
    col4.download_button(
        "⬇️ Markdown", reports.to_markdown(selection, insights),
        file_name=f"expense_report_{stamp}.md", mime="text/markdown",
        use_container_width=True,
    )

    st.divider()
    st.subheader("Report preview")
    st.markdown(reports.to_markdown(selection, insights))


def page_policy(manager: ExpenseManager) -> None:
    """Show the active policy, the audit trail, and data utilities."""
    st.header("⚙️ Policy & Data")

    policy = policy_summary()
    st.subheader("Per-category claim limits")
    st.dataframe(
        pd.DataFrame(
            list(policy["category_limits"].items()), columns=["Category", f"Limit ({CURRENCY})"]
        ),
        use_container_width=True, hide_index=True,
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Receipt required above", money(policy["receipt_required_above"]))
    col2.metric("Submission window", f"{policy['max_claim_age_days']} days")
    col3.metric("Monthly cap / employee", money(policy["monthly_employee_cap"]))
    st.caption(
        f"Duplicate detection window: {policy['duplicate_window_days']} days · "
        f"claims dated before {policy['stale_cutoff']} are rejected as stale."
    )

    st.divider()
    st.subheader("Data utilities")
    col1, col2 = st.columns(2)

    with col1:
        if st.button("Load demo dataset"):
            result = manager.seed_sample_data(SAMPLE_DATA_DIR / "sample_expenses.csv")
            if result["imported"]:
                st.success(f"Imported {result['imported']} demo claims.")
                st.rerun()
            else:
                st.warning(result["errors"][0] if result["errors"] else "Nothing imported.")

        uploaded = st.file_uploader("Bulk import claims (CSV)", type=["csv"])
        if uploaded is not None and st.button("Import uploaded CSV"):
            temp_path = SAMPLE_DATA_DIR / "_uploaded.csv"
            temp_path.write_bytes(uploaded.getvalue())
            result = manager.import_csv(temp_path)
            st.success(f"Imported {result['imported']}, failed {result['failed']}.")
            for error in result["errors"][:10]:
                st.caption(error)
            st.rerun()

    with col2:
        st.warning("Danger zone")
        confirm = st.text_input("Type DELETE to wipe all data")
        if st.button("Reset database") and confirm == "DELETE":
            import database as db

            db.reset_database(manager.db_path)
            st.cache_resource.clear()
            st.success("Database reset.")
            st.rerun()

    st.divider()
    st.subheader("Audit trail")
    import database as db

    log = db.fetch_audit_log(limit=50, db_path=manager.db_path)
    if log:
        st.dataframe(pd.DataFrame(log), use_container_width=True, hide_index=True)
    else:
        st.caption("No audit entries yet.")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> None:
    """Route to the selected page."""
    manager = get_manager()
    page = render_sidebar(manager)

    if page.endswith("Dashboard"):
        page_dashboard(manager)
    elif page.endswith("Submit Expense"):
        page_submit(manager)
    elif page.endswith("Receipt Parser"):
        page_receipt(manager)
    elif page.endswith("Review & Approve"):
        page_review(manager)
    elif page.endswith("Reports & Export"):
        page_reports(manager)
    else:
        page_policy(manager)


if __name__ == "__main__":
    main()
