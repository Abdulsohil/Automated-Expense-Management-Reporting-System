# Automated Expense Management & Reporting System

A Python application that automates the full corporate expense lifecycle: claim submission, AI-assisted categorisation, policy compliance checking, approval workflow, spend analytics, and multi-format report export.

## 🚀 Live Demo

**[Try the Live App](https://automated-expense-management-reporting.streamlit.app/)**

> Replace this link with your own Streamlit URL after deploying.

## 👥 Team

**One Size Team** — team size: 2

| Member | Role |
| --- | --- |
| Shaik Abdul Sohial | Backend, policy engine, database |
| Mohamed Waseem | Streamlit UI, analytics, reporting |

## ✨ Features

- **Automated Claim Intake:** Submit expenses through a form, a receipt parser, or a bulk CSV import.
- **AI Categorisation:** Google Gemini classifies free-text expenses into one of nine categories; a keyword engine takes over when no API key is present.
- **Receipt Parsing:** Paste receipt text and the system extracts vendor, date, amount and category automatically.
- **Policy Compliance Engine:** Deterministic rules check category limits, receipt requirements, claim age, duplicate submissions, monthly caps and round-number anomalies.
- **Risk Scoring & Auto-Decision:** Every claim gets a 0-100 risk score and is routed to Approved, Pending or Rejected without manual triage.
- **Approval Workflow:** Managers filter, search, approve, reject or mark claims reimbursed; every action lands in an immutable audit log.
- **Analytics Dashboard:** KPI tiles, category split, monthly trend, top spenders, status mix and statistical outlier detection (z-score per category).
- **AI Spend Insights:** Natural-language commentary on the current spend selection, with a factual fallback when AI is offline.
- **Multi-Format Export:** Download the ledger as CSV, a five-sheet Excel workbook, a formatted PDF, or Markdown.
- **Command-Line Interface:** Every capability is scriptable through `main.py` for automation and CI.

## 🛠️ Tech Stack

- **Language:** Python 3.10+
- **Web Framework:** Streamlit
- **Database:** SQLite (parameterised SQL, indexed, with an audit table)
- **Data Analysis:** pandas
- **Charts:** Plotly Express
- **AI Integration:** Google Gemini API
- **Reporting:** openpyxl (Excel), ReportLab (PDF)
- **Configuration:** python-dotenv
- **Testing:** pytest

## 📂 Project Structure

```
Automated-Expense-Management-Reporting-System/
├── app.py                  # Streamlit web application (6 pages)
├── main.py                 # Command-line interface
├── config.py               # Categories, policy limits, paths, AI settings
├── database.py             # SQLite schema and all SQL statements
├── expense_manager.py      # Business logic: validate, submit, approve, import
├── policy_engine.py        # Deterministic compliance rules and risk scoring
├── ai_service.py           # Gemini wrapper + rule-based fallbacks
├── analytics.py            # pandas aggregations and anomaly detection
├── report_generator.py     # CSV / Excel / PDF / Markdown exports
├── logger.py               # Shared rotating-file logger
├── tests/
│   ├── test_policy_engine.py
│   ├── test_expense_manager.py
│   └── test_analytics_and_ai.py
├── sample_data/
│   ├── sample_expenses.csv
│   └── sample_receipt.txt
├── .streamlit/
│   ├── config.toml
│   └── secrets.toml.example
├── reports/
├── requirements.txt
├── .env.example
├── .gitignore
├── AI_DEVELOPMENT_NOTES.md
└── README.md
```

## ⚙️ Run Locally

### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/Automated-Expense-Management-Reporting-System.git
cd Automated-Expense-Management-Reporting-System
```

### 2. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 3. Configure the Gemini API key (optional)

Create a `.env` file in the project root:

```
GEMINI_API_KEY=your_gemini_api_key
```

The application runs fully without a key — the AI features switch to the deterministic rule-based engine and the UI says so. Never commit `.env`.

### 4. Run the application

```bash
python -m streamlit run app.py
```

Open the local URL shown in the terminal, then go to **Policy & Data → Load demo dataset** to populate 30 sample claims.

## 💻 Command-Line Usage

```bash
python main.py seed                        # load the demo dataset
python main.py add --name "Mohamed Waseem" --email waseem@example.com \
    --date 2026-10-05 --amount 1850 --description "Airport taxi to client site"
python main.py list --status Pending       # list pending claims
python main.py summary                     # analytics summary as JSON
python main.py insights                    # spend commentary
python main.py report --format xlsx        # write reports/expense_report_*.xlsx
python main.py approve 7 --note "Receipt verified"
python main.py reject 9 --note "Duplicate of claim 8"
```

## 🧪 Run Tests

```bash
python -m pytest tests/ -v
```

The suite covers policy rules, validation and submission, the SQLite layer, analytics aggregation, anomaly detection, the offline AI fallbacks and every export format.

## 📊 Policy Rules

| Rule | Behaviour |
| --- | --- |
| Category limit | Each of the nine categories has its own per-claim ceiling (`config.CATEGORY_LIMITS`). |
| Receipt requirement | Claims of Rs. 2,000 or more must carry a receipt reference. |
| Submission window | Claims older than 90 days, or dated in the future, are rejected. |
| Duplicate detection | Same employee + category + amount within a 3-day window is flagged. |
| Monthly cap | Per-employee monthly spend above Rs. 75,000 raises a warning. |
| Round-amount check | Round high-value amounts are flagged for receipt verification. |

Risk score = (violations × 35) + (warnings × 12), capped at 100. A score of 70 or more with violations present auto-rejects the claim.

All limits live in `config.py` and can be changed in one place.

## ☁️ Deploy to Streamlit Community Cloud

1. Push this repository to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io), create a new app pointing at `app.py`.
3. Under **Advanced settings → Secrets**, add:
   ```toml
   GEMINI_API_KEY = "your_gemini_api_key_here"
   ```
4. Deploy, then update the live link at the top of this README.

Note that Streamlit Cloud uses ephemeral storage, so the SQLite file resets when the app restarts. Use **Load demo dataset** to repopulate it.

## 🔐 Security

- The Gemini API key is read from environment variables or Streamlit secrets, never from source.
- `.env` and `.streamlit/secrets.toml` are excluded via `.gitignore`.
- Every SQL statement is parameterised, so user input cannot reach the query string.
- The database enforces `amount > 0` at the schema level as well as in validation.
- Every status change and deletion is written to an append-only audit log.
- AI output is advisory; the compliance decision always comes from the deterministic rule engine.

## 👨‍💻 Authors

**Shaik Abdul Sohial** · **Mohamed Waseem** — One Size Team

---

Built as a capstone project demonstrating AI-assisted development, layered architecture and test-driven Python.
