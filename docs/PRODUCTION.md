# From prototype to production: what SymptomLens would need

SymptomLens is an assessment prototype. It runs on one machine, with synthetic data and one user at a time. This page describes what a real deployment would have to add. **None of it is built**, and I make no compliance claims (HIPAA, GDPR or any other) about the prototype.

## 1. Who can see and do what
- **Sign-in:** through the practice's identity provider (OIDC / SAML single sign-on), not app-specific passwords. Streamlit supports OIDC sign-in via `st.login`.
- **Roles:**

  | Role | Can |
  |---|---|
  | Clinician | View their own patients; accept, edit, dismiss and undo review items |
  | Reviewer (e.g. nurse) | Work the review inbox; cannot change medications or history |
  | Admin | Manage users, imports and integrations; no default access to patient content |

- **Least access:** patient lists are scoped to the clinician's panel, and the cross-patient inbox respects the same scope.
- **Identity is recorded:** the "Reviewer name" box becomes the signed-in identity, so `reviewed_by` can't be typed or faked.

## 2. Storage
- **Database:** **PostgreSQL** (managed, encrypted at rest, point-in-time recovery) replaces SQLite.
  - The schema is already relational and maps one to one: `patients`, `visits`, `procedures`, `medications`, `reports`, `events`, `vitals`, `corrections` and `review_log`.
  - Schema changes go through migrations (e.g. Alembic). No more "add the column if it's missing" at runtime.
- **Writes:** every write is a transaction.
  - Review actions are already row-level (`update_rows` / `append_rows`). In Postgres they become `UPDATE … WHERE event_id = $1 AND review_status = 'pending'`, so two reviewers can't overwrite each other's decisions.
  - The prototype's full-table saves (`save()`) are replaced by inserts only: new reports and their events.
- **Append-only audit:** the database role the app uses can't run UPDATE or DELETE on `review_log` and `corrections`.
- **Raw reports are immutable:** `reports.raw_text` can be inserted but never updated, enforced by the database rather than by code convention.

## 3. Audit and retention
- **Access logging:** log who *viewed* which patient, not just who changed what.
- **Retention:** a retention policy for raw reports, uploaded text and logs, agreed with the practice. Uploaded files are already never stored, only their text.
- **Backups:** encrypted, regularly restored as a test, with a documented recovery target.

## 4. The LLM in production
- **Where it runs:** self-hosted inference (Ollama or vLLM on a GPU host inside the practice's network) keeps patient text in-house. Any hosted API would need a data-processing agreement and a decision that its terms are acceptable, made by the practice and its compliance team.
- **What already carries over** from the prototype:
  - Schema-constrained output, per-event validation with one retry, and the regex fallback.
  - Confidence based on evidence.
  - `model` and `prompt_version` stored on every event, and the response cache.
- **What's added:**
  - **Monitoring:** fallback rate, validation-failure rate, share sent to review, and parse time, with alerts when any of them shift.
  - **Regression gate:** a prompt or model change ships only if `evaluate.py` on gold + clinician corrections doesn't get worse.
  - **Re-parsing:** after a model change, only unreviewed parser output is re-parsed (`pipeline_runner.py --reparse` already works this way).

## 5. Getting data in (EHR / FHIR)
- **Import:** `importer.py` already maps a FHIR R4 Bundle onto the tables. In production the Bundle comes from the EHR's FHIR API, e.g. `GET [base]/Patient/{id}/$everything` with SMART on FHIR backend-services auth.
  - Paging, rate limits and retries need handling.
  - Imports are de-duplicated by resource ID, as `merge_into_store` does now.
- **Vendor differences:** EHRs differ, and some practices only offer HL7v2 feeds or CSV exports. Each becomes another entry in `IMPORTERS`.
- **Sending data back to the EHR** (e.g. reviewed symptom scores as FHIR Observations) would be a separate, opt-in piece of work.

## 6. Configuration, secrets, deployment
- **Configuration:** separate dev, staging and prod environments. All settings come from environment variables (`SYMPTOMLENS_MODEL` already works this way); nothing is hard-coded.
- **Secrets:** database credentials, OIDC client secrets and EHR keys live in a secret manager, never in the repo or `.streamlit/secrets.toml` on shared disks.
- **Release process:** CI runs the test suite and the evaluation gate on every change (`.github/workflows/tests.yml` runs the tests today). Deploys use container images with health checks; `scripts/restart.sh` already health-checks the app.
- **Public demo:** must stay **synthetic-only**, with uploads limited in size and number, and resetting regularly.

## 7. Clinical safety (needs clinicians, not code)
- **Thresholds:** the −5% weight alert, the conflict gap of 4 points and the review threshold of 0.6 are placeholders. Clinicians would set them per practice.
- **Severity:** severity words and the 0–10 scale would be mapped to CTCAE grades with clinical input.
- **Scope:** the dashboard summarises what patients reported. It does not diagnose or recommend treatment, and the product's wording and training must make that clear.
