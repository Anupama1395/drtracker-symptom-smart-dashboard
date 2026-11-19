# review.py — clinician review actions on extracted events: accept, edit, dismiss.
#
# Rules:
#   * The raw report text is never touched; only the events extracted from it change.
#   * Every edit is validated with the same SymptomEvent Pydantic model the parser uses,
#     so a clinician can't save something the parser itself would reject (e.g. severity 12).
#   * Every changed field is logged to the corrections table (event_id, field, old, new, when).
#   * Corrections can be exported as gold labels (same format as gold.json) for evaluate.py.
import json
from datetime import datetime

import pandas as pd
from pydantic import ValidationError

from ingest import read_header
from parser import SymptomEvent
from schema import corrections_cols

STATUSES = ("pending", "accepted", "edited", "dismissed")
EDITABLE = ("symptom", "severity", "frequency")


def ensure_review_columns(events: pd.DataFrame) -> pd.DataFrame:
    """Data written before review existed has no review columns: everything flagged is pending."""
    ev = events.copy()
    if "review_status" not in ev.columns:
        ev["review_status"] = None
    if "reviewed_at" not in ev.columns:
        ev["reviewed_at"] = None
    flagged = ev["needs_review"].astype(str).str.lower().eq("true")
    status = ev["review_status"].astype(object)
    ev["review_status"] = status.where(status.notna(), flagged.map({True: "pending", False: None})).astype(object)
    # From CSV an all-empty column loads as float; text columns must be object so a timestamp
    # or "manual" can be written into them.
    for col in ["reviewed_at", "extracted_by", "symptom", "flags"]:
        if col in ev.columns:
            ev[col] = ev[col].astype(object)
    ev["needs_review"] = flagged
    return ev


def _now(now):
    return (now or datetime.now()).replace(microsecond=0).isoformat()


def _row(events, event_id):
    match = events.index[events["event_id"].astype(str) == str(event_id)]
    if len(match) != 1:
        raise ValueError(f"Event {event_id} not found")
    return match[0]


def accept(store: dict, event_id, now: datetime | None = None) -> dict:
    ev = ensure_review_columns(store["events"])
    i = _row(ev, event_id)
    ev.loc[i, ["review_status", "needs_review", "reviewed_at"]] = ["accepted", False, _now(now)]
    store["events"] = ev
    return store


def dismiss(store: dict, event_id, now: datetime | None = None) -> dict:
    """Dismissed = the clinician says this event isn't real. It stays in the table (audit trail)
    but the dashboard leaves it out of tiles, charts and the banner."""
    ev = ensure_review_columns(store["events"])
    i = _row(ev, event_id)
    ev.loc[i, ["review_status", "needs_review", "reviewed_at"]] = ["dismissed", False, _now(now)]
    store["events"] = ev
    return store


def _clean(v):
    """Form input -> None / int; empty or NaN means 'not stated'."""
    if v is None or v is pd.NA or (isinstance(v, str) and not v.strip()) or (isinstance(v, float) and pd.isna(v)):
        return None
    return int(v) if float(v).is_integer() else float(v)


def edit(store: dict, corrections: pd.DataFrame, event_id, symptom, severity, frequency,
         now: datetime | None = None) -> tuple[dict, pd.DataFrame]:
    """Validate with SymptomEvent, update the event, log each changed field.
    Raises ValueError (nothing saved) if the new values fail validation."""
    ev = ensure_review_columns(store["events"])
    i = _row(ev, event_id)
    new = {"symptom": symptom, "severity": _clean(severity), "frequency": _clean(frequency)}
    try:
        SymptomEvent(symptom=new["symptom"], severity=new["severity"], frequency=new["frequency"],
                     confidence=1.0, evidence="clinician edit")
    except ValidationError as err:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors())
        raise ValueError(f"Not saved — {problems}") from err

    stamp = _now(now)
    log = []
    for field in EDITABLE:
        old = _clean(ev.loc[i, field]) if field != "symptom" else ev.loc[i, field]
        if old != new[field]:
            log.append({"event_id": ev.loc[i, "event_id"], "field": field, "old_value": old,
                        "new_value": new[field], "corrected_at": stamp})
    if not log:                                    # nothing actually changed: that's an accept
        return accept(store, event_id, now), corrections

    for field in EDITABLE:
        ev.loc[i, field] = new[field]
    ev.loc[i, ["extracted_by", "review_status", "needs_review", "reviewed_at"]] = ["manual", "edited", False, stamp]
    store["events"] = ev
    new_rows = pd.DataFrame(log, columns=corrections_cols)
    corrections = pd.concat([corrections, new_rows], ignore_index=True) if len(corrections) else new_rows
    return store, corrections


def export_gold(store: dict, path: str = "gold_corrections.json") -> int:
    """Write clinician-verified reports in gold.json format. A report qualifies only when every
    event from it has a human decision (accepted / edited / dismissed): a half-reviewed report
    would put unchecked parser output into the 'truth'. Dismissed events are left out.
    Returns the number of reports written."""
    ev = ensure_review_columns(store["events"])
    ev = ev[ev["report_id"].notna()]
    reps = store["reports"].copy()
    reps["report_id"] = pd.to_numeric(reps["report_id"])
    ev["report_id"] = pd.to_numeric(ev["report_id"])
    gold = []
    for rid, g in ev.groupby("report_id"):
        if not g["review_status"].isin(["accepted", "edited", "dismissed"]).all():
            continue
        raw = reps.loc[reps["report_id"] == rid, "raw_text"]
        if raw.empty:
            continue
        kept = g[g["review_status"] != "dismissed"]
        gold.append({"text": read_header(str(raw.iloc[0]))["body"],     # what the parser actually reads
                     "report_id": int(rid),
                     "events": [{"symptom": r["symptom"], "severity": _clean(r["severity"]),
                                 "frequency": _clean(r["frequency"])} for _, r in kept.iterrows()]})
    with open(path, "w") as f:
        json.dump(gold, f, indent=1)
    return len(gold)
