# Dr.Tracker: patient-reported symptoms, from free text to a 30-second pre-visit view

This is Track B (Patient-Reported Data: Store, Manage & Chart). It is modelled on the **oncology demo**: patients on head-and-neck chemoradiation write daily notes about nausea, vomiting, mouth sores, pain, swallowing and fatigue.

The prototype has four parts:

1. **Generate:** 5 fictional patients over 5 weeks, about 166 free-text reports, daily weight readings, and an antiemetic switch on day 21 for the two patients who get worse.
2. **Parse:** each report goes to a local LLM (Ollama, `llama3.1:8b`, JSON output). Pydantic then validates the output against the schema, and deterministic rule checks run on it. If the LLM fails, a regex parser takes over.
3. **Store:** structured events go to CSV, mirrored into SQLite (`data/drtracker.db`). The queries are plain SQL.
4. **Show:** a Streamlit pre-visit dashboard answering *"what changed since the last visit?"*

All data is synthetic. No real patient information is used anywhere.

## How to run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ollama pull llama3.1:8b              # optional: without it the regex fallback is used and everything is flagged for review

python pipeline_runner.py            # generate the data, parse every report, save CSV + SQLite (python pipeline_runner.py 3 = first 3 reports only)
python Queries.py                    # the SQL queries
python evaluate.py                   # parser accuracy: LLM vs regex
streamlit run app.py                 # dashboard
```

`data/` is committed, so the dashboard opens straight away without re-running the parser.

## Files

| File | What it does |
|---|---|
| `schema.py` | Table definitions, CSV and SQLite storage |
| `Synthetic_datagenerator.py` | Seeded synthetic patients, medications, reports, weights, and "silver" symptom labels |
| `parser.py` | LLM extraction, Pydantic validation, rule checks, regex fallback |
| `pipeline_runner.py` | Runs the whole pipeline and flags same-day conflicts |
| `Queries.py` | Three SQL queries |
| `evaluate.py`, `gold.json` | Parser evaluation against 30 hand-labelled reports and the silver labels |
| `app.py` | Pre-visit dashboard |

## Schema

The schema is event-based: one row per symptom mention, not one row per report.

```
patients     (patient_id, name, dob, diagnosis, treatment_start)
medications  (patient_id, drug, dose, start_date, end_date)            -- one row per medication period
reports      (report_id, patient_id, reported_at, source, raw_text)    -- exactly what the patient wrote, never edited
events       (event_id, report_id -> reports, patient_id, symptom, severity 0-10, frequency,
              onset, related_med, confidence, extracted_by, needs_review, flags)
vitals       (patient_id, measured_at, kind, value)                     -- weight
```

Why it is built this way:

- **Raw text is kept and never overwritten.** Every event points back to its report through `report_id`. A clinician can always see the patient's own words, and the parser can be re-run later without losing anything.
- **Events, not reports.** "Threw up twice, felt nauseous all morning" becomes two rows. Queries like "vomiting per week" are then simple filters and sums.
- **`onset` and `reported_at` are separate dates.** "Vomited last night", written this morning, belongs to yesterday.
- **Unknown is NULL, and NULL is not zero.** If the patient gives no number, `severity` is NULL. `0` means the patient explicitly denied the symptom ("no vomiting today"). Rows that are queried or shown in the dashboard keep this distinction.
- **`related_med` is filled only when the patient makes the link** ("after the chemo infusion", "after the new pill"). Questions about cause go through the `medications` table and dates instead (query 2).
- **The extra columns carry provenance:** `confidence`, `extracted_by` (llm / rule / manual) and `flags`, which lists the checks that fired.

## Vague and conflicting reports

| Case | Example | What happens |
|---|---|---|
| Vague | "Felt off all day, not sure why." | Stored as `symptom = other` with low confidence and `needs_review`. No symptom is invented. |
| No number given | "Swallowing is getting harder" | `severity = NULL`. The LLM is told never to guess a number. Rule check: a severity without a number or a severity word in the quoted evidence is set back to NULL and flagged `severity_unsupported`. |
| Severity word | "Pain is severe today" | Fixed mapping: mild = 3, moderate = 5, severe = 8. The LLM prompt and the regex both use it. |
| Negation | "No vomiting today. Nausea mild." | Vomiting stored as `severity = 0, frequency = 0` (denied), nausea as 3. Rule check: negation in the evidence together with severity > 0 gives `negation_conflict`. |
| Same-day contradiction | 08:00 "Nausea 8/10…", 21:00 "No nausea today, felt fine." | `flag_conflicts()` in `pipeline_runner.py`: same patient, symptom and day, with one report denying and one rating above 0, or severities 4 or more points apart. **Both rows are kept** and flagged `same_day_conflict`. The system does not choose which report is right; a clinician does. |
| Relative time | "last night", "yesterday" | `onset` = report date − 1 |

Everything flagged appears in the dashboard's **review queue**, next to the patient's raw text and the reason it was flagged.

## Parser and validation

The parser has four layers:

1. **LLM** (`call_llm`): local Llama 3.1 8B through Ollama, with `format="json"` and `temperature=0`. The system prompt defines the output shape and the rules: never invent severity, how to handle negation and relative time, and when to fill `related_med`. The LLM also has to return `evidence`, an exact quote from the report.
2. **Pydantic** (`SymptomEvent`): `symptom` must be one of 7 allowed values, `severity` must be 0–10, `confidence` 0–1, and `frequency` 0 or more. If the output breaks any of these, the regex fallback is used instead (step 4).
3. **Rule checks** (`rule_checks`): cheap deterministic checks for common LLM slips:
   - `evidence_not_in_text`: the model's quote doesn't appear in the report, which suggests it made something up.
   - `severity_unsupported`
   - `negation_conflict`
   - `frequency_implausible` (more than 20)

   Any flag, confidence below 0.6, or `other` means `needs_review`.
4. **Regex fallback** (`regex_parse`): used if the LLM is down, returns invalid JSON, or fails validation. Everything it produces is marked `needs_review`, so the pipeline never stops and never quietly trusts a weaker parser.

**Evaluation** (`python evaluate.py`):

- **Gold set:** 30 hand-labelled reports. Half follow the generator's templates. The other half are phrasings the generator never uses ("sick to my stomach", "food keeps getting stuck", "puked three times").
- **Silver labels:** each generator template knows which symptoms it mentions, so symptom precision and recall can be checked on every stored report for free. Those labels don't cover severity, so severity is measured on the gold set.

| Parser | Gold symptom precision | Gold recall | Severity MAE | Frequency exact |
|---|---|---|---|---|
| Regex baseline | 0.84 | 0.80 | 0.00 (n=17) | 9/10 |
| LLM (llama3.1:8b) | *run `evaluate.py` with Ollama up* | | | |

The regex baseline scores 1.00 on the silver labels, because it was written against the same templates. Its recall drops to 0.80 on wording it hasn't seen. That gap is the case for the LLM. The regex is still kept as a fallback because it is predictable and cannot hallucinate.

## Queries (`Queries.py`, SQL over SQLite)

1. **Vomiting episodes per patient per week:** `SUM(frequency)` grouped by Monday-start week. Denials are excluded. NULL counts stay unknown, so they don't drag the sum down to zero.
2. **Nausea in the 7 days before vs after a medication change:** joins `medications` to `events` and compares `AVG(severity)` in the two windows with `julianday()`. It also returns `n_scored_reports`, because a before/after average built on 3 reports shouldn't be over-read.
3. **Review queue per patient:** how many events are waiting for a human, how many are same-day conflicts, and how many came through the fallback.

## Dashboard: the 30-second view

The dashboard is built around one question: **what changed since the last visit?**

- **Banner:** new reports, vomiting episodes, weight change, items needing review, and the symptom that got most worse. If weight loss is over 5%, the banner also shows a warning to check nutrition.
- **Metric tiles:** nausea, vomiting, mouth sores and pain, each showing the mean since the last visit and the change from before it. Red means worse.
- **Severity chart:** daily maximum severity per symptom. Medication changes are dashed lines and the last visit is a red line, so a jump in nausea can be read against a drug switch.
- **Weight trend:** the brief's weight-loss alert, shown as a −5% line.
- **Review queue:** flagged items with the reason and the patient's raw text.
- **All raw reports:** the original messages, for traceability.

The "last visit" date is a sidebar control. In a real deployment it would come from the scheduling system.

## Where AI is used, and where it isn't

- **Used:** turning messy free text into structured fields. This is the part rules handle badly, as the regex recall on unseen wording shows.
- **Not used:** storage, queries, conflict detection, alerts, severity mapping, or anything else shown to the clinician as a number. That logic is deterministic and testable. The LLM never decides that something is clinically fine. Its output is validated, checked against the source text, and sent to a person when unsure.
- **AI coding assistants** (Claude) were used to write, debug and review code and to draft this README. Their output was checked by running the pipeline, the evaluation and a headless dashboard test, not taken on trust.

## Trade-offs and limits

- **A local 8B model instead of a hosted API:** free, and no data leaves the machine, which matters for patient data. The cost is accuracy and speed (about 166 calls take minutes on a laptop). For the demo I run the batch ahead of time and parse one or two reports live.
- **CSV plus SQLite instead of Postgres:** no setup, and everything sits in the repo. The schema is relational and would move to Postgres unchanged.
- **Synthetic data is cleaner than real patients.** The templates cover the hard cases on purpose, but real text will be messier. The gold set is small: 30 reports, labelled by one person.
- **Severity scale:** different symptoms on one 0–10 scale is a simplification. Real use would probably map to CTCAE grades, with clinical input.
- **No clinical claims.** The dashboard summarises what patients reported. It does not diagnose, and the thresholds (−5% weight, review rules) are placeholders a clinician would set.
