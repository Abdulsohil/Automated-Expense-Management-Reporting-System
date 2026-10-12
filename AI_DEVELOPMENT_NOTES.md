# AI Development Notes

How AI was used while building the **Automated Expense Management & Reporting System**, and how AI works inside the finished product.

**Team:** One Size Team (size 2) — Shaik Abdul Sohial, Mohamed Waseem

---

## 1. Where AI sits in the product

The system has two clearly separated decision layers:

| Layer | Implementation | Who decides |
| --- | --- | --- |
| **Compliance** | `policy_engine.py` — pure Python rules | Deterministic code only |
| **Assistance** | `ai_service.py` — Google Gemini | AI, advisory only |

This separation was a deliberate design decision. An approval decision has to be reproducible and explainable to an auditor, so no AI call can influence whether a claim passes policy. AI is used only where ambiguity is genuine: reading free text, guessing a category, and writing commentary.

### AI features

1. **Expense categorisation** (`AIService.categorize`) — classifies a free-text description plus vendor into one of nine categories, returning a confidence score and a reason.
2. **Receipt parsing** (`AIService.parse_receipt_text`) — extracts vendor, date, amount and category from pasted receipt text.
3. **Spend insights** (`AIService.spend_insights`) — turns an analytics summary into four manager-ready bullets.
4. **Violation explanation** (`AIService.explain_violation`) — rewrites raw rule messages as a polite note to the employee.

### Graceful degradation

Every AI function has a deterministic twin:

| AI function | Fallback | Technique |
| --- | --- | --- |
| `categorize` | `rule_based_categorize` | Keyword map in `config.CATEGORY_KEYWORDS`, scored by hit count |
| `parse_receipt_text` | `rule_based_parse_receipt` | Regex for total/amount and three date formats |
| `spend_insights` | `rule_based_insights` | Facts read straight off the pandas summary |
| `explain_violation` | Inline template | Joins the rule messages into a sentence |

The app never crashes or blocks on a missing key, a quota error or a network timeout; it reports `source: "rule-based"` in the UI so the user always knows which engine answered. This also means the full test suite runs offline with no API credits.

### Prompt design

Prompts are short, constrained and ask for JSON:

```
Classify this business expense into exactly one category.
Categories: Travel, Accommodation, Meals, ...
Expense: Uber ride to the airport
Reply with JSON only: {"category": "...", "confidence": 0.0, "reason": "one short sentence"}
```

Because Gemini often wraps JSON in markdown fences, `_extract_json` locates the first `{` and last `}` rather than trusting the whole response to parse. A returned category that is not in the allowed list is discarded and the keyword engine runs instead — the model is never allowed to invent a category.

---

## 2. How AI assisted development

### Planning
AI was used to turn the one-line project title into a feature breakdown, which became the module layout: config → database → policy → AI → analytics → reporting → UI. The key architectural question we put to AI was whether policy checks should be AI-driven; the answer (and our conclusion) was no, for auditability.

### Code generation
AI drafted the boilerplate-heavy parts: the SQLite schema and CRUD functions, the ReportLab table styling, and the Plotly chart configuration. Each draft was reviewed and edited — for example, the first database draft used f-string SQL interpolation, which we replaced with parameterised queries throughout.

### Test generation
The test suite was expanded with AI assistance, particularly the edge cases we had not thought of:
- a claim dated in the future, not just one that is too old
- a category with fewer than three claims, where a z-score is meaningless
- `_extract_json` receiving `None`, a markdown fence, and malformed JSON
- a CSV import where one row is broken and the rest must still import

### Review
AI reviewed each module for missing docstrings, unhandled exceptions and type-hint gaps. Fixes that came out of review:
- `to_dataframe` returned a bare empty frame for empty input, so every caller needed a null check; it now returns an empty frame carrying the full column set.
- `detect_anomalies` divided by a zero standard deviation when every claim in a category had the same amount.
- The logger crashed on read-only filesystems; file logging is now optional.

---

## 3. What AI got wrong

Being specific here matters more than listing the wins.

1. **Hallucinated a pandas API.** An early analytics draft called `DataFrame.groupby(...).agg(amount=("amount", "total"))`. `"total"` is not an aggregation function. Caught by the first test run.
2. **Over-trusted the model's output.** The first categorisation draft stored whatever string Gemini returned. Gemini occasionally returns "Transportation", which is not a configured category and would have broken the category-limit lookup. We now validate against `CATEGORIES` before accepting.
3. **Suggested SQL string interpolation** for the dynamic filters in `fetch_expenses`, which is an injection risk. Rewritten to append `?` placeholders and collect parameters in a list.
4. **Static demo dates.** The generated sample CSV used fixed dates. Because the policy engine rejects claims older than 90 days, the demo dataset would silently become all-rejected over time. `seed_sample_data` now rebases the sample dates so the newest claim lands on today.
5. **Suggested `localStorage`-style session persistence** for the Streamlit state, which does not survive a rerun. Replaced with `st.cache_resource` for the manager and `st.session_state` for form values.

---

## 4. Verification

- `python -m pytest tests/ -v` — 70+ tests across policy rules, validation, persistence, analytics, AI fallbacks and all four export formats.
- Every module import-checked, and the Streamlit app booted headless to confirm a clean start.
- The export tests check real file signatures (`PK` for xlsx, `%PDF` for pdf) rather than just that bytes were produced.
- All AI paths are tested through their fallbacks, so the suite is deterministic and needs no API key.

---

## 5. Takeaway

AI was most valuable for breadth — schema boilerplate, chart configuration, edge-case test ideas — and least reliable on anything requiring judgement about correctness or security. The pattern that worked was: ask AI for a draft, then review it specifically for the things it tends to get wrong (invented APIs, unvalidated external output, string-built SQL). The same principle shaped the product itself, which is why AI advises in this system but never decides.
