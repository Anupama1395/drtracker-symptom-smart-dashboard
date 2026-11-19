# schema.py — table definitions + storage (CSV for readability, SQLite for querying)
import os
import sqlite3
import pandas as pd

SYMPTOMS = ["nausea", "vomiting", "mouth_sores", "pain",
            "swallowing_difficulty", "fatigue", "other"]

# One row per patient
patients_cols = ["patient_id", "name", "dob", "diagnosis", "treatment_start"]

# One row per medication period (end_date = None if ongoing)
medications_cols = ["patient_id", "drug", "dose", "start_date", "end_date"]

# One row per raw submission, exactly as the patient wrote it. Never edited.
reports_cols = ["report_id", "patient_id", "reported_at", "source", "raw_text"]

# One row per symptom mention the parser extracts from a report
events_cols = [
    "event_id", "report_id", "patient_id",
    "symptom",        # one of SYMPTOMS
    "severity",       # 0-10, NaN if not stated (0 = patient denied the symptom)
    "frequency",      # number of episodes, NaN if not stated
    "onset",          # date the symptom happened (may differ from reported_at)
    "related_med",    # drug name if patient linked it, else None
    "confidence",     # 0-1 from the parser
    "extracted_by",   # "llm" | "rule" | "manual"
    "needs_review",   # True if vague / conflicting / low confidence
    "flags",          # which checks fired, ";"-separated
]

# One row per weight measurement
vitals_cols = ["patient_id", "measured_at", "kind", "value"]

TABLES = ["patients", "medications", "reports", "events", "vitals"]
DB_PATH = "data/drtracker.db"

def empty_store():
    return {
        "patients":    pd.DataFrame(columns=patients_cols),
        "medications": pd.DataFrame(columns=medications_cols),
        "reports":     pd.DataFrame(columns=reports_cols),
        "events":      pd.DataFrame(columns=events_cols),
        "vitals":      pd.DataFrame(columns=vitals_cols),
    }

def save_sqlite(store, path=DB_PATH):
    """Mirror every table into SQLite so the queries can be plain SQL."""
    with sqlite3.connect(path) as conn:
        for name, df in store.items():
            df.to_sql(name, conn, if_exists="replace", index=False)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_events_pso ON events(patient_id, symptom, onset)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_reports_p ON reports(patient_id, reported_at)")

def save(store, folder="data"):
    os.makedirs(folder, exist_ok=True)
    for name, df in store.items():
        df.to_csv(f"{folder}/{name}.csv", index=False)
    save_sqlite(store, os.path.join(folder, "drtracker.db"))

def load(folder="data"):
    return {name: pd.read_csv(f"{folder}/{name}.csv") for name in TABLES}

def connect(path=DB_PATH):
    return sqlite3.connect(path)
