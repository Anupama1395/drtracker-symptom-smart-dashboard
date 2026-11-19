# Clinician review actions: accept, edit (with validation), dismiss, audit log, gold export.
import json
from datetime import datetime

import pandas as pd
import pytest

import Synthetic_datagenerator as gen
from ingest import event_rows, flag_conflicts, ingest_text, typed_events
from parser import regex_parse
from review import accept, dismiss, edit, ensure_review_columns, export_gold
from schema import load, load_corrections, save, save_corrections

NOW = datetime(2026, 10, 12, 9, 30)


@pytest.fixture
def store(tmp_path):
    """Generated data + events as the pipeline stores them when the LLM is down (regex fallback,
    everything pending review), saved to a temp folder."""
    gen.main(str(tmp_path))
    s = load(str(tmp_path))
    rows = []
    for _, r in s["reports"].iterrows():
        rows += event_rows(r.report_id, r.patient_id, r.reported_at, regex_parse(r.raw_text), "rule",
                           len(rows) + 1, force_review=True)
    s["events"] = flag_conflicts(typed_events(rows, s["events"].columns))
    save(s, str(tmp_path))
    return tmp_path, load(str(tmp_path))


def event(s, eid):
    ev = s["events"]
    return ev[ev["event_id"].astype(str) == str(eid)].iloc[0]


def first_pending(s, symptom=None):
    ev = ensure_review_columns(s["events"])
    ev = ev[ev["review_status"] == "pending"]
    if symptom:
        ev = ev[ev["symptom"] == symptom]
    return ev.iloc[0]["event_id"]


def test_new_events_start_pending():
    """Flagged events from the pipeline are pending; unflagged ones need no review."""
    ext = regex_parse("Nausea about 4/10.")
    rows = event_rows(1, "P001", "2026-10-01T08:00:00", ext, "rule", 1)
    assert rows[0]["review_status"] is None                     # regex alone didn't flag it
    rows = event_rows(1, "P001", "2026-10-01T08:00:00", ext, "rule", 1, force_review=True)
    assert rows[0]["review_status"] == "pending"


def test_old_data_without_review_columns_is_upgraded():
    ev = pd.DataFrame({"event_id": [1, 2], "needs_review": [True, False]})
    out = ensure_review_columns(ev)
    assert out["review_status"].tolist()[0] == "pending" and pd.isna(out["review_status"].tolist()[1])


def test_accept(store):
    folder, s = store
    eid = first_pending(s)
    save(accept(s, eid, NOW), str(folder))
    e = event(load(str(folder)), eid)
    assert e["review_status"] == "accepted" and str(e["needs_review"]) == "False"
    assert e["reviewed_at"] == "2026-10-12T09:30:00"


def test_edit_updates_event_and_logs_each_changed_field(store):
    folder, s = store
    eid = first_pending(s, "nausea")
    before = event(s, eid)
    s2, corr = edit(s, load_corrections(str(folder)), eid, "nausea", 7, 2, NOW)
    save(s2, str(folder)); save_corrections(corr, str(folder))

    e = event(load(str(folder)), eid)
    assert (e["severity"], e["frequency"], e["extracted_by"], e["review_status"]) == (7, 2, "manual", "edited")
    log = load_corrections(str(folder))
    assert set(log["field"]) == {f for f, old, new in [("severity", before["severity"], 7),
                                                      ("frequency", before["frequency"], 2)]
                                 if pd.isna(old) or old != new}
    assert (log["event_id"].astype(str) == str(eid)).all() and (log["corrected_at"] == "2026-10-12T09:30:00").all()


def test_edit_can_change_symptom_and_clear_a_value(store):
    folder, s = store
    eid = first_pending(s, "nausea")
    s2, corr = edit(s, load_corrections(str(folder)), eid, "pain", None, None, NOW)
    e = event(s2, eid)
    assert e["symptom"] == "pain" and pd.isna(e["severity"]) and pd.isna(e["frequency"])
    assert "symptom" in set(corr["field"])


@pytest.mark.parametrize("severity, frequency, symptom", [(12, None, "nausea"), (-1, None, "nausea"),
                                                          (5, -3, "nausea"), (5, None, "headache")])
def test_edit_rejects_invalid_values_and_saves_nothing(store, severity, frequency, symptom):
    folder, s = store
    eid = first_pending(s, "nausea")
    before = event(s, eid).copy()
    with pytest.raises(ValueError, match="Not saved"):
        edit(s, load_corrections(str(folder)), eid, symptom, severity, frequency, NOW)
    after = event(s, eid)
    assert after["review_status"] == "pending" and after["extracted_by"] == before["extracted_by"]
    assert load_corrections(str(folder)).empty


def test_edit_with_no_change_counts_as_accept(store):
    folder, s = store
    eid = first_pending(s, "nausea")
    e = event(s, eid)
    s2, corr = edit(s, load_corrections(str(folder)), eid, e["symptom"], e["severity"], e["frequency"], NOW)
    assert event(s2, eid)["review_status"] == "accepted" and corr.empty


def test_dismiss(store):
    folder, s = store
    eid = first_pending(s)
    save(dismiss(s, eid, NOW), str(folder))
    e = event(load(str(folder)), eid)
    assert e["review_status"] == "dismissed" and str(e["needs_review"]) == "False"


def test_raw_report_text_never_changes(store):
    folder, s = store
    raw_before = load(str(folder))["reports"].copy()
    ids = ensure_review_columns(s["events"]).query("review_status == 'pending'")["event_id"].tolist()[:3]
    s = accept(s, ids[0], NOW)
    s, corr = edit(s, load_corrections(str(folder)), ids[1], "pain", 9, None, NOW)
    s = dismiss(s, ids[2], NOW)
    save(s, str(folder)); save_corrections(corr, str(folder))
    pd.testing.assert_frame_equal(load(str(folder))["reports"], raw_before)


def test_reviewed_conflict_is_not_reopened_by_a_later_upload(store):
    folder, s = store
    conflict = s["events"][s["events"]["flags"].str.contains("same_day_conflict")]
    eid = conflict.iloc[0]["event_id"]
    s = accept(s, eid, NOW)
    ingest_text(s, "P002", "Nausea about 3/10.", reported_at=datetime(2026, 10, 11, 12),
                folder=str(folder), parse_fn=lambda t: (regex_parse(t), "rule"))
    assert event(load(str(folder)), eid)["review_status"] == "accepted"


def test_export_gold_only_fully_reviewed_reports(store, tmp_path):
    folder, s = store
    ev = ensure_review_columns(s["events"])
    multi = ev[ev["review_status"] == "pending"].groupby("report_id").filter(lambda g: len(g) >= 2)
    rid = multi["report_id"].iloc[0]
    ids = multi[multi["report_id"] == rid]["event_id"].tolist()

    out = tmp_path / "gold_corrections.json"
    s = accept(s, ids[0], NOW)
    assert export_gold(s, str(out)) == 0                       # half-reviewed report is not exported
    for i in ids[1:-1]:
        s = accept(s, i, NOW)
    s = dismiss(s, ids[-1], NOW)
    assert export_gold(s, str(out)) == 1
    item = json.loads(out.read_text())[0]
    assert set(item) == {"text", "events", "report_id"}
    assert len(item["events"]) == len(ids) - 1                 # dismissed event left out
    assert set(item["events"][0]) == {"symptom", "severity", "frequency"}
