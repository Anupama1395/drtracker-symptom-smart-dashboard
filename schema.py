# schema.py — table definitions + storage (CSV for readability, SQLite for querying)
import os
import sqlite3
from datetime import date, datetime

import pandas as pd

SYMPTOMS = ["nausea", "vomiting", "mouth_sores", "pain",
            "swallowing_difficulty", "fatigue", "other"]

# One row per patient
patients_cols = ["patient_id", "name", "dob", "diagnosis",
                 "diagnosis_date",    # date of the biopsy that confirmed the cancer
                 "first_visit_date",  # first consult at this practice
                 "treatment_start"]

# One row per clinic encounter
visits_cols = ["visit_id", "patient_id", "visit_date",
               "visit_type",          # "first consult" | "follow-up" | "treatment review"
               "provider", "notes"]

# One row per procedure (done or planned)
procedures_cols = ["procedure_id", "patient_id", "procedure", "date",
                   "status"]          # "completed" | "scheduled" | "cancelled"

# One row per medication period (end_date empty = still active)
medications_cols = ["patient_id", "drug", "dose", "start_date", "end_date",
                    "prescribed_by", "reason"]

# One row per raw submission, exactly as the patient wrote it. Never edited.
reports_cols = ["report_id", "patient_id", "reported_at",
                "source",             # "free_text" | "upload"
                "raw_text"]

# One row per symptom mention the parser extracts from a report
events_cols = [
    "event_id", "report_id", "patient_id",
    "symptom",        # one of SYMPTOMS
    "severity",       # 0-10, NaN if not stated (0 = patient denied the symptom)
    "frequency",      # number of episodes, NaN if not stated
    "onset",          # date the symptom happened (may differ from reported_at)
    "related_med",    # drug name if patient linked it, else None
    "confidence",     # 0-1 from the parser
    "extracted_by",   # "llm" | "rule" | "import" | "manual"
    "needs_review",   # True if vague / conflicting / low confidence / uploaded
    "flags",          # which checks fired, ";"-separated
]

# One row per measurement
vitals_cols = ["patient_id", "measured_at", "kind", "value"]

COLUMNS = {
    "patients": patients_cols,
    "visits": visits_cols,
    "procedures": procedures_cols,
    "medications": medications_cols,
    "reports": reports_cols,
    "events": events_cols,
    "vitals": vitals_cols,
}
TABLES = list(COLUMNS)
DB_NAME = "drtracker.db"


def empty_store():
    return {name: pd.DataFrame(columns=cols) for name, cols in COLUMNS.items()}


def _iso(value):
    """Timestamp/datetime/date -> ISO text ('2026-10-05' or '2026-10-05T12:00:00'); anything else unchanged."""
    if isinstance(value, (pd.Timestamp, datetime, date)) and not pd.isna(value):
        if isinstance(value, datetime) or isinstance(value, pd.Timestamp):
            value = pd.Timestamp(value)
            return value.date().isoformat() if value == value.normalize() else value.isoformat(timespec="seconds")
        return value.isoformat()
    return value


def storable(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of df with every date-like value as ISO text. Callers (e.g. the dashboard) may have
    converted columns to datetimes for display; storage always gets one consistent text format."""
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]) or out[col].dtype == object:
            out[col] = out[col].map(_iso).astype(object)
    return out


def save_sqlite(store, path):
    """Mirror every table into SQLite so the queries can be plain SQL."""
    with sqlite3.connect(path) as conn:
        for name, df in store.items():
            storable(df).to_sql(name, conn, if_exists="replace", index=False)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_events_pso ON events(patient_id, symptom, onset)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_reports_p ON reports(patient_id, reported_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_visits_p ON visits(patient_id, visit_date)")


def save(store, folder="data"):
    os.makedirs(folder, exist_ok=True)
    store = {name: storable(df) for name, df in store.items()}
    save_sqlite(store, os.path.join(folder, DB_NAME))     # database first: if it fails, CSVs stay untouched
    for name, df in store.items():
        df.to_csv(os.path.join(folder, f"{name}.csv"), index=False)


def load(folder="data"):
    """Load every table; a missing CSV (e.g. data from an older version) comes back empty."""
    store = {}
    for name, cols in COLUMNS.items():
        path = os.path.join(folder, f"{name}.csv")
        store[name] = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame(columns=cols)
    return store


def connect(folder="data"):
    return sqlite3.connect(os.path.join(folder, DB_NAME))
