# Baseline before the improvement roadmap (2026-11-19)

Starting commit: `bbe876e` (same as origin/main). Python 3.13.5, Streamlit 1.67.0.

| Check | Result |
|---|---|
| `python -m pytest` | 59 passed |
| Ollama | Installed (0.40.16) but not usable: `~/.ollama/models` links to an unmounted external drive, so the server returns `mkdir ... file exists`. **No real LLM result has ever been produced.** |
| `python evaluate.py`, gold set (30) | LLM: unavailable. Regex: precision 0.84, recall 0.80, severity MAE 0.00 (n=17), frequency 9/10 |
| `python evaluate.py`, silver labels | precision 1.00, recall 1.00 on 166 reports (regex was written against these templates, so this is optimistic) |
| `python Queries.py` | Q1–Q3 run. Q2: nausea before→after olanzapine: P001 3.0→4.0, P004 3.0→4.5 |
| Dashboard, headless | Home + 9 patients (P001–P008, P096 incl. uploads): no exceptions, 0.2–0.5 s per rerun |
| Local data | 9 patients, 170 reports, 241 events, all `extracted_by = rule` (regex fallback) |

## Known problems at baseline
- `pipeline_runner.py` regenerates `data/` and so wipes uploads, imported patients, reviews and corrections.
- `save()` rewrites every CSV and the whole database on every click.
- After a reload, counts are stored as floats (`1` → `1.0`).
- Regex fallback: in "Throat pain 8/10, worse when swallowing", swallowing difficulty also gets the 8. "Felt off" is missed when the note has other symptoms.
- Severity words: the rule check accepts "bad/terrible/worse" as support, but only mild/moderate/severe have a mapping.
- One bad LLM event sends the whole report to regex; no retry; `format="json"` doesn't enforce the schema; no prompt examples; the model's own confidence is used; the evidence check is an exact substring match.
- No model or prompt version is stored, and LLM results aren't cached.
- Reviews have no reviewer name and no undo. Upload takes one file at a time. A note with several dates becomes one report.
- `app.py` (701 lines) recomputes triage for every patient on every rerun.
