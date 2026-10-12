"""Command-line interface for the Automated Expense Management & Reporting System.

Examples::

    python main.py seed
    python main.py add --name "Mohamed Waseem" --email waseem@example.com \
        --date 2026-10-05 --amount 1850 --description "Airport taxi to client site"
    python main.py list --status Pending
    python main.py summary
    python main.py report --format xlsx
    python main.py approve 7 --note "Receipt verified"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import analytics
import report_generator as reports
from config import APP_TITLE, APP_VERSION, CATEGORIES, SAMPLE_DATA_DIR
from expense_manager import ExpenseManager, ValidationError
from logger import get_logger

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser with every sub-command."""
    parser = argparse.ArgumentParser(
        prog="expense-system", description=f"{APP_TITLE} (v{APP_VERSION})"
    )
    parser.add_argument("--db", type=Path, default=None, help="Path to an alternate database file")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Submit a new expense claim")
    add.add_argument("--name", required=True)
    add.add_argument("--email", required=True)
    add.add_argument("--date", required=True, help="YYYY-MM-DD")
    add.add_argument("--amount", required=True, type=float)
    add.add_argument("--category", choices=CATEGORIES, default="")
    add.add_argument("--vendor", default="")
    add.add_argument("--description", default="")
    add.add_argument("--receipt", default="", help="Receipt reference")

    listing = sub.add_parser("list", help="List stored claims")
    listing.add_argument("--status")
    listing.add_argument("--category")
    listing.add_argument("--employee", help="Employee email")
    listing.add_argument("--limit", type=int, default=25)

    sub.add_parser("summary", help="Print the analytics summary as JSON")

    insights = sub.add_parser("insights", help="Print spend insights")
    insights.add_argument("--json", action="store_true")

    report = sub.add_parser("report", help="Write a report file")
    report.add_argument("--format", choices=["csv", "xlsx", "pdf", "md"], default="csv")
    report.add_argument("--out", type=Path, default=None, help="Output directory")

    importer = sub.add_parser("import", help="Bulk import claims from CSV")
    importer.add_argument("csv_path", type=Path)

    sub.add_parser("seed", help="Load the bundled demo dataset")

    for name, status in (("approve", "Approved"), ("reject", "Rejected")):
        action = sub.add_parser(name, help=f"Mark a claim {status}")
        action.add_argument("expense_id", type=int)
        action.add_argument("--note", default="")
        action.set_defaults(target_status=status)

    reset = sub.add_parser("reset", help="Drop and recreate the database")
    reset.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Returns:
        A process exit code: 0 on success, 1 on a handled error.
    """
    args = build_parser().parse_args(argv)
    manager = ExpenseManager(db_path=args.db)

    if args.command == "add":
        try:
            expense_id, verdict = manager.submit(
                {
                    "employee_name": args.name,
                    "employee_email": args.email,
                    "expense_date": args.date,
                    "amount": args.amount,
                    "category": args.category,
                    "vendor": args.vendor,
                    "description": args.description,
                    "receipt_ref": args.receipt,
                }
            )
        except ValidationError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        stored = manager.get(expense_id)
        print(f"Claim #{expense_id} created — {stored['category']} — {verdict.auto_decision}")
        for violation in verdict.violations:
            print(f"  VIOLATION: {violation}")
        for warning in verdict.warnings:
            print(f"  WARNING:   {warning}")
        return 0

    if args.command == "list":
        rows = manager.list_expenses(
            status=args.status, category=args.category, employee_email=args.employee
        )[: args.limit]
        if not rows:
            print("No matching claims.")
            return 0
        print(f"{'ID':>4}  {'DATE':10}  {'EMPLOYEE':22}  {'CATEGORY':20}  {'AMOUNT':>12}  STATUS")
        print("-" * 92)
        for row in rows:
            print(
                f"{row['id']:>4}  {row['expense_date']:10}  "
                f"{row['employee_name'][:22]:22}  {row['category'][:20]:20}  "
                f"{row['amount']:>12,.2f}  {row['status']}"
            )
        return 0

    if args.command == "summary":
        print(json.dumps(analytics.build_summary(manager.list_expenses()), indent=2, default=str))
        return 0

    if args.command == "insights":
        summary = analytics.build_summary(manager.list_expenses())
        result = manager.ai.spend_insights(summary)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Insights ({result['source']}):")
            for bullet in result["insights"]:
                print(f"  - {bullet}")
        return 0

    if args.command == "report":
        expenses = manager.list_expenses()
        if not expenses:
            print("No expenses to report.", file=sys.stderr)
            return 1
        summary = analytics.build_summary(expenses)
        insights = manager.ai.spend_insights(summary)["insights"]
        try:
            path = reports.save_report(expenses, args.format, insights, out_dir=args.out)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Report written to {path}")
        return 0

    if args.command == "import":
        if not args.csv_path.exists():
            print(f"Error: {args.csv_path} does not exist", file=sys.stderr)
            return 1
        result = manager.import_csv(args.csv_path)
        print(f"Imported {result['imported']} claim(s); {result['failed']} failed.")
        for error in result["errors"]:
            print(f"  {error}")
        return 0

    if args.command == "seed":
        result = manager.seed_sample_data(SAMPLE_DATA_DIR / "sample_expenses.csv")
        print(f"Imported {result['imported']} demo claim(s); {result['failed']} failed.")
        for error in result["errors"]:
            print(f"  {error}")
        return 0

    if args.command in ("approve", "reject"):
        if manager.set_status(args.expense_id, args.target_status, actor="cli", note=args.note):
            print(f"Claim #{args.expense_id} is now {args.target_status}.")
            return 0
        print(f"Error: claim #{args.expense_id} not found", file=sys.stderr)
        return 1

    if args.command == "reset":
        if not args.yes:
            answer = input("This deletes every claim. Type DELETE to continue: ")
            if answer.strip() != "DELETE":
                print("Aborted.")
                return 1
        import database as db

        db.reset_database(args.db)
        print("Database reset.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
