# Phase 3: persisted review actions (row-level), reviewer identity, append-only log, undo.
from datetime import datetime

import pandas as pd
import pytest

import pipeline_runner
from review import apply_action, undo_last
from schema import load, load_corrections, load_review_log

T1, T2, T3 = datetime(2026, 10, 12, 9, 0), datetime(2026, 10, 12, 9, 5), datetime(2026, 10, 12, 9, 10)


@pytest.fixture
def folder(tmp_path):
    pipeline_runner.main(["--folder", str(tmp_path)])          # LLM off in tests -> everything pending
    return str(tmp_path)


def ev(folder, eid):
    e = load(folder)["events"]
    return e[e["event_id"].astype(str) == str(eid)].iloc[0]


def pending(folder, symptom=None):
    e = load(folder)["events"]
    e = e[e["review_status"] == "pending"]
    return (e[e["symptom"] == symptom] if symptom else e).iloc[0]["event_id"]


def test_accept_is_saved_with_reviewer_and_logged(folder):
    eid = pending(folder)
    assert apply_action("accept", folder, eid, "Dr. Test", T1) == "accept"
    e = ev(folder, eid)
    assert (e["review_status"], e["reviewed_by"], e["needs_review"]) == ("accepted", "Dr. Test", False)
    log = load_review_log(folder)
    assert log.iloc[-1][["action", "from_status", "to_status", "reviewed_by"]].tolist() == \
        ["accept", "pending", "accepted", "Dr. Test"]
    assert load_corrections(folder).empty                      # accept changes no field


def test_edit_logs_corrections_with_reviewer(folder):
    eid = pending(folder, "nausea")
    apply_action("edit", folder, eid, "NP Lee", T1, symptom="nausea", severity=7, frequency=2)
    e = ev(folder, eid)
    assert (e["severity"], e["frequency"], e["extracted_by"], e["reviewed_by"]) == (7, 2, "manual", "NP Lee")
    assert (load_corrections(folder)["reviewed_by"] == "NP Lee").all()


def test_invalid_edit_saves_nothing(folder):
    eid = pending(folder, "nausea")
    with pytest.raises(ValueError, match="Not saved"):
        apply_action("edit", folder, eid, "NP Lee", T1, symptom="nausea", severity=12, frequency=None)
    assert ev(folder, eid)["review_status"] == "pending"
    assert load_review_log(folder).empty and load_corrections(folder).empty


def test_reviewer_name_is_required(folder):
    with pytest.raises(ValueError, match="reviewer"):
        apply_action("accept", folder, pending(folder), "  ", T1)


def test_undo_edit_restores_values_with_reversal_rows(folder):
    eid = pending(folder, "nausea")
    before = ev(folder, eid)
    apply_action("edit", folder, eid, "NP Lee", T1, symptom="pain", severity=9, frequency=None)
    msg = undo_last(folder, eid, "Dr. Test", T2)
    after = ev(folder, eid)
    assert "Undid edit" in msg
    assert after["symptom"] == before["symptom"] and after["extracted_by"] == before["extracted_by"]
    assert (pd.isna(after["severity"]) and pd.isna(before["severity"])) or after["severity"] == before["severity"]
    assert after["review_status"] == "pending" and after["needs_review"]
    corr = load_corrections(folder)
    reversal = corr[corr["corrected_at"] == "2026-10-12T09:05:00"]
    assert set(reversal["field"]) == set(corr[corr["corrected_at"] == "2026-10-12T09:00:00"]["field"])
    assert load_review_log(folder)["action"].tolist() == ["edit", "undo"]


def test_undo_accept_and_dismiss_reopen_the_item(folder):
    a, d = load(folder)["events"].query("review_status == 'pending'")["event_id"].tolist()[:2]
    apply_action("accept", folder, a, "Dr. Test", T1)
    apply_action("dismiss", folder, d, "Dr. Test", T1)
    undo_last(folder, a, "Dr. Test", T2)
    undo_last(folder, d, "Dr. Test", T2)
    assert ev(folder, a)["review_status"] == ev(folder, d)["review_status"] == "pending"


def test_undo_twice_has_nothing_left(folder):
    eid = pending(folder)
    apply_action("accept", folder, eid, "Dr. Test", T1)
    undo_last(folder, eid, "Dr. Test", T2)
    with pytest.raises(ValueError, match="Nothing to undo"):
        undo_last(folder, eid, "Dr. Test", T3)


def test_log_is_append_only_and_raw_text_untouched(folder):
    raw = load(folder)["reports"].copy()
    eid = pending(folder)
    apply_action("accept", folder, eid, "A", T1)
    undo_last(folder, eid, "B", T2)
    apply_action("dismiss", folder, eid, "C", T3)
    log = load_review_log(folder)
    assert log["log_id"].tolist() == [1, 2, 3] and log["reviewed_by"].tolist() == ["A", "B", "C"]
    pd.testing.assert_frame_equal(load(folder)["reports"], raw)
