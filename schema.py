# schema.py — table definitions + storage.
# SQLite (data/symptomlens.db) is the source of truth. CSVs are a readable export written on full saves
# (and by `python export_csv.py`); row-level updates (review clicks) go to SQLite only.
import os
import shutil
import sqlite3
from contextlib import closing
import tempfile
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
    "review_status",  # "pending" | "accepted" | "edited" | "dismissed"; empty = never needed review
    "reviewed_at",    # when a clinician acted on it
    "reviewed_by",    # who acted on it (name typed in the sidebar)
    "model",          # LLM that produced it (empty for regex / manual / import)
    "prompt_version", # prompt version used, so results can be reproduced
    "parse_ms",       # how long parsing the report took
]

# Audit log of clinician edits: one row per changed field. Kept outside the main store
# (data/corrections.csv + its own SQLite table) so it survives being appended to, never rewritten.
corrections_cols = ["event_id", "field", "old_value", "new_value", "corrected_at", "reviewed_by"]

# Append-only log of every review action (accept / edit / dismiss / undo). Never updated or deleted.
review_log_cols = ["log_id", "event_id", "action", "from_status", "to_status", "reviewed_by", "at",
                   "details"]   # JSON: previous extracted_by for edits, {"undoes": log_id} for undo

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
DB_NAME = "symptomlens.db"


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


# Columns that must come back as integers (CSV/SQLite turn int + NULL into float: 1 -> 1.0)
INT_COLS = {"events": ["frequency", "parse_ms"], "reports": ["report_id"], "review_log": ["log_id"]}
BOOL_COLS = {"events": ["needs_review"]}
EXTRA_TABLES = {"corrections": corrections_cols, "review_log": review_log_cols}


def _normalise(name, df):
    """Same dtypes whether a table came from SQLite or CSV; old data gains new columns as empty."""
    cols = COLUMNS.get(name) or EXTRA_TABLES.get(name, [])
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[cols + [c for c in df.columns if c not in cols]]
    for c in INT_COLS.get(name, []):
        num = pd.to_numeric(df[c], errors="coerce")
        if num.notna().sum() == df[c].notna().sum():          # only cast when every value is numeric
            df[c] = num.round().astype("Int64")
    for c in BOOL_COLS.get(name, []):
        df[c] = df[c].astype(str).str.lower().isin(["true", "1", "1.0"])
    return df


def _write_tables(conn, tables):
    for name, df in tables.items():
        storable(df).to_sql(name, conn, if_exists="replace", index=False)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_events_pso ON events(patient_id, symptom, onset)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_reports_p ON reports(patient_id, reported_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_visits_p ON visits(patient_id, visit_date)")


def save_sqlite(store, path):
    """Write the store into SQLite atomically: build a copy (keeping tables not in the store, e.g.
    corrections and review_log), then swap it in. A crash mid-write leaves the old file intact."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(suffix=".db", dir=folder)
    os.close(fd)
    try:
        if os.path.exists(path):
            shutil.copyfile(path, tmp)
        conn = sqlite3.connect(tmp)
        try:
            with conn:                       # commits (or rolls back) the transaction
                _write_tables(conn, store)
        finally:
            conn.close()                     # Windows can't rename a file that is still open
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def export_csv(store, folder="data"):
    for name, df in store.items():
        storable(df).to_csv(os.path.join(folder, f"{name}.csv"), index=False)


def save(store, folder="data"):
    """Full save of the main tables: SQLite first (atomic), then the CSV export."""
    os.makedirs(folder, exist_ok=True)
    save_sqlite(store, os.path.join(folder, DB_NAME))
    export_csv(store, folder)


def _db_tables(path):
    if not os.path.exists(path):
        return set()
    with closing(sqlite3.connect(path)) as conn, conn:   # close too: Windows locks open files
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _read(folder, name, cols):
    db = os.path.join(folder, DB_NAME)
    if name in _db_tables(db):
        with closing(sqlite3.connect(db)) as conn, conn:   # close too: Windows locks open files
            df = pd.read_sql(f'SELECT * FROM "{name}"', conn)
    elif os.path.exists(csv := os.path.join(folder, f"{name}.csv")):
        df = pd.read_csv(csv)                                  # older data without a database
    else:
        df = pd.DataFrame(columns=cols)
    return _normalise(name, df)


def load(folder="data"):
    """Every main table, from SQLite (falls back to the CSVs for older data). Missing = empty."""
    return {name: _read(folder, name, cols) for name, cols in COLUMNS.items()}


# ---------------- row-level writes (review clicks, logs): no full rewrite ----------------
def _sql_value(v):
    """Python-native value for sqlite3: numpy ints/bools would otherwise be stored as raw bytes."""
    if v is None or v is pd.NA or (not isinstance(v, str) and pd.isna(v)):
        return None
    if hasattr(v, "item"):                       # numpy scalar -> int / float / bool
        v = v.item()
    if isinstance(v, bool):
        return int(v)
    return _iso(v)


def update_rows(table, key_col, key_value, fields: dict, folder="data"):
    """UPDATE one table's matching rows in a single transaction."""
    if not fields:
        return 0
    vals = [_sql_value(v) for v in fields.values()]
    sets = ", ".join(f'"{c}" = ?' for c in fields)
    with closing(sqlite3.connect(os.path.join(folder, DB_NAME))) as conn, conn:   # close too: Windows locks open files
        existing = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        for c in fields:
            if c not in existing:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}"')
        cur = conn.execute(f'UPDATE "{table}" SET {sets} WHERE CAST("{key_col}" AS TEXT) = ?',
                           vals + [str(key_value)])
        return cur.rowcount


def append_rows(table, df, folder="data"):
    """INSERT rows (append-only logs). Creates the table if it doesn't exist yet."""
    if df is None or len(df) == 0:
        return
    os.makedirs(folder, exist_ok=True)
    with closing(sqlite3.connect(os.path.join(folder, DB_NAME))) as conn, conn:   # close too: Windows locks open files
        storable(df).to_sql(table, conn, if_exists="append", index=False)


def _load_extra(name, folder):
    return _read(folder, name, EXTRA_TABLES[name])


def _save_extra(name, df, folder):
    os.makedirs(folder, exist_ok=True)
    with closing(sqlite3.connect(os.path.join(folder, DB_NAME))) as conn, conn:   # close too: Windows locks open files
        storable(df).to_sql(name, conn, if_exists="replace", index=False)
    storable(df).to_csv(os.path.join(folder, f"{name}.csv"), index=False)


def load_corrections(folder="data"):
    return _load_extra("corrections", folder)


def save_corrections(corrections, folder="data"):
    _save_extra("corrections", corrections, folder)


def load_review_log(folder="data"):
    return _load_extra("review_log", folder)


def clear_review_history(folder="data"):
    """Used only when synthetic data is regenerated: old logs would point at events that no longer exist.
    Tables are dropped (not emptied) so the next append creates them with the right column types."""
    with closing(sqlite3.connect(os.path.join(folder, DB_NAME))) as conn, conn:   # close too: Windows locks open files
        for name in EXTRA_TABLES:
            conn.execute(f'DROP TABLE IF EXISTS "{name}"')
    for name in EXTRA_TABLES:
        if os.path.exists(csv := os.path.join(folder, f"{name}.csv")):
            os.remove(csv)


def connect(folder="data"):
    return sqlite3.connect(os.path.join(folder, DB_NAME))
