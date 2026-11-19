# services/data.py — load the store and prepare it for display (dates parsed, review columns ensured).
# No Streamlit here: the app caches load_data() keyed on the database file's version.
import os
from dataclasses import dataclass

import pandas as pd

from review import ensure_review_columns
from schema import DB_NAME, load


@dataclass
class Data:
    ev_all: pd.DataFrame      # every event, incl. dismissed (review history only)
    ev: pd.DataFrame          # events shown on cards, charts and the banner (dismissed removed)
    meds: pd.DataFrame
    vit: pd.DataFrame
    pts: pd.DataFrame
    reps: pd.DataFrame
    visits: pd.DataFrame
    procs: pd.DataFrame
    as_of: pd.Timestamp       # "today" for synthetic data = the latest thing anyone recorded


def db_version(folder="data") -> tuple:
    """Changes whenever the database is written (full save or a single review click)."""
    path = os.path.abspath(os.path.join(folder, DB_NAME))
    if not os.path.exists(path):
        csvs = [os.path.join(folder, f) for f in os.listdir(folder)] if os.path.isdir(folder) else []
        return (path, max((os.stat(p).st_mtime_ns for p in csvs), default=0))
    st_ = os.stat(path)
    return (path, st_.st_mtime_ns, st_.st_size)


def prepare(store: dict) -> Data | None:
    ev, meds, vit, pts, reps, visits, procs = (
        store[k].copy() for k in ["events", "medications", "vitals", "patients", "reports", "visits", "procedures"])
    if ev.empty or pts.empty:
        return None
    ev["onset"] = pd.to_datetime(ev["onset"])
    ev["flags"] = ev["flags"].fillna("").astype(str)
    ev = ensure_review_columns(ev)
    reps["reported_at"] = pd.to_datetime(reps["reported_at"])
    vit["measured_at"] = pd.to_datetime(vit["measured_at"])
    for col in ["start_date", "end_date"]:
        meds[col] = pd.to_datetime(meds[col])
    visits["visit_date"] = pd.to_datetime(visits["visit_date"])
    procs["date"] = pd.to_datetime(procs["date"])
    for col in ["diagnosis_date", "first_visit_date", "treatment_start", "dob"]:
        pts[col] = pd.to_datetime(pts[col])
    as_of = max(reps["reported_at"].max(), vit["measured_at"].max()
                if len(vit) else reps["reported_at"].max()).normalize()
    return Data(ev_all=ev, ev=ev[ev["review_status"] != "dismissed"], meds=meds, vit=vit, pts=pts,
                reps=reps, visits=visits, procs=procs, as_of=as_of)


def load_data(folder="data") -> Data | None:
    return prepare(load(folder))
