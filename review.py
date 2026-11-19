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
from schema import (append_rows, corrections_cols, load, load_corrections, load_review_log,
                    review_log_cols, update_rows)

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
    if "reviewed_by" not in ev.columns:
        ev["reviewed_by"] = None
    for col in ["reviewed_at", "reviewed_by", "extracted_by", "symptom", "flags"]:
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


def accept(store: dict, event_id, now: datetime | None = None, reviewer: str | None = None) -> dict:
    ev = ensure_review_columns(store["events"])
    i = _row(ev, event_id)
    ev.loc[i, ["review_status", "needs_review", "reviewed_at", "reviewed_by"]] = ["accepted", False, _now(now), reviewer]
    store["events"] = ev
    return store


def dismiss(store: dict, event_id, now: datetime | None = None, reviewer: str | None = None) -> dict:
    """Dismissed = the clinician says this event isn't real. It stays in the table (audit trail)
    but the dashboard leaves it out of tiles, charts and the banner."""
    ev = ensure_review_columns(store["events"])
    i = _row(ev, event_id)
    ev.loc[i, ["review_status", "needs_review", "reviewed_at", "reviewed_by"]] = ["dismissed", False, _now(now), reviewer]
    store["events"] = ev
    return store


def _clean(v):
    """Form input -> None / int; empty or NaN means 'not stated'."""
    if v is None or v is pd.NA or (isinstance(v, str) and not v.strip()) or (isinstance(v, float) and pd.isna(v)):
        return None
    return int(v) if float(v).is_integer() else float(v)


def edit(store: dict, corrections: pd.DataFrame, event_id, symptom, severity, frequency,
         now: datetime | None = None, reviewer: str | None = None) -> tuple[dict, pd.DataFrame]:
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
                        "new_value": new[field], "corrected_at": stamp, "reviewed_by": reviewer})
    if not log:                                    # nothing actually changed: that's an accept
        return accept(store, event_id, now, reviewer), corrections

    for field in EDITABLE:
        ev.loc[i, field] = new[field]
    ev.loc[i, ["extracted_by", "review_status", "needs_review", "reviewed_at", "reviewed_by"]] = \
        ["manual", "edited", False, stamp, reviewer]
    store["events"] = ev
    new_rows = pd.DataFrame(log, columns=corrections_cols)
    corrections = pd.concat([corrections, new_rows], ignore_index=True) if len(corrections) else new_rows
    return store, corrections


# ---------------- persisted actions: one event row + append-only logs ----------------
PERSISTED = ["symptom", "severity", "frequency", "extracted_by", "needs_review",
             "review_status", "reviewed_at", "reviewed_by"]


def _persist_event(row, folder):
    update_rows("events", "event_id", row["event_id"], {c: row[c] for c in PERSISTED}, folder)


def _log(folder, event_id, action, from_status, to_status, reviewer, at, details=None):
    log = load_review_log(folder)
    next_id = int(pd.to_numeric(log["log_id"]).max()) + 1 if len(log) else 1
    append_rows("review_log", pd.DataFrame([{
        "log_id": next_id, "event_id": event_id, "action": action, "from_status": from_status,
        "to_status": to_status, "reviewed_by": reviewer, "at": at,
        "details": json.dumps(details or {})}], columns=review_log_cols), folder)
    return next_id


def apply_action(action: str, folder: str, event_id, reviewer: str, now: datetime | None = None,
                 symptom=None, severity=None, frequency=None) -> str:
    """Accept / edit / dismiss one event and save it: updates only that event's row, appends any
    field corrections and one review_log entry. Raises ValueError (nothing saved) on invalid input."""
    if not (reviewer or "").strip():
        raise ValueError("Enter your name as reviewer first (sidebar), so the decision is attributed.")
    reviewer = reviewer.strip()
    store = load(folder)
    ev = ensure_review_columns(store["events"])
    i = _row(ev, event_id)
    before = ev.loc[i].copy()
    corrections = load_corrections(folder)
    n0 = len(corrections)
    if action == "accept":
        store = accept(store, event_id, now, reviewer)
    elif action == "dismiss":
        store = dismiss(store, event_id, now, reviewer)
    elif action == "edit":
        store, corrections = edit(store, corrections, event_id, symptom, severity, frequency, now, reviewer)
    else:
        raise ValueError(f"Unknown action {action!r}")
    after = ensure_review_columns(store["events"]).loc[i]
    done = {"accepted": "accept", "edited": "edit", "dismissed": "dismiss"}[after["review_status"]]
    _persist_event(after, folder)
    append_rows("corrections", corrections.iloc[n0:], folder)
    _log(folder, before["event_id"], done, before["review_status"], after["review_status"], reviewer,
         after["reviewed_at"], {"extracted_by": before["extracted_by"]} if done == "edit" else None)
    return done


def undo_last(folder: str, event_id, reviewer: str, now: datetime | None = None) -> str:
    """Revert the most recent not-yet-undone action on an event. Nothing is deleted: the event gets
    its previous values back, reversing rows go into corrections, and an 'undo' entry into review_log."""
    if not (reviewer or "").strip():
        raise ValueError("Enter your name as reviewer first (sidebar), so the undo is attributed.")
    log = load_review_log(folder)
    log = log[log["event_id"].astype(str) == str(event_id)]
    undone = {json.loads(d or "{}").get("undoes") for d in log.loc[log["action"] == "undo", "details"]}
    open_actions = log[(log["action"] != "undo") & ~log["log_id"].isin(undone)]
    if open_actions.empty:
        raise ValueError(f"Nothing to undo for event {event_id}.")
    last = open_actions.sort_values("log_id").iloc[-1]
    stamp = _now(now)

    store = load(folder)
    ev = ensure_review_columns(store["events"])
    i = _row(ev, event_id)
    reversal = []
    if last["action"] == "edit":
        corr = load_corrections(folder)
        changed = corr[(corr["event_id"].astype(str) == str(event_id)) & (corr["corrected_at"] == last["at"])]
        for _, c in changed.iterrows():
            old = c["old_value"] if c["field"] == "symptom" else _clean(c["old_value"])
            reversal.append({"event_id": ev.loc[i, "event_id"], "field": c["field"], "old_value": ev.loc[i, c["field"]],
                             "new_value": old, "corrected_at": stamp, "reviewed_by": reviewer.strip()})
            ev.loc[i, c["field"]] = old
        ev.loc[i, "extracted_by"] = json.loads(last["details"] or "{}").get("extracted_by", ev.loc[i, "extracted_by"])
    restored = last["from_status"] if isinstance(last["from_status"], str) else None
    ev.loc[i, ["review_status", "needs_review", "reviewed_at", "reviewed_by"]] = \
        [restored, restored == "pending", stamp, reviewer.strip()]
    store["events"] = ev
    _persist_event(ev.loc[i], folder)
    if reversal:
        append_rows("corrections", pd.DataFrame(reversal, columns=corrections_cols), folder)
    _log(folder, ev.loc[i, "event_id"], "undo", last["to_status"], restored, reviewer.strip(), stamp,
         {"undoes": int(last["log_id"])})
    return f"Undid {last['action']} on event {event_id}"


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
