# Phase 6: the dashboard's logic, tested without Streamlit.
import pandas as pd
import pytest

import pipeline_runner
from services.data import db_version, load_data
from services.triage import default_last_visit, summarize, triage_all


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    folder = tmp_path_factory.mktemp("svc")
    pipeline_runner.main(["--folder", str(folder)])
    return load_data(str(folder)), str(folder)


def test_load_data_prepares_dates_and_hides_dismissed(data):
    d, _ = data
    assert pd.api.types.is_datetime64_any_dtype(d.ev["onset"]) and d.as_of == d.as_of.normalize()
    assert (d.ev["review_status"] != "dismissed").all() and len(d.ev_all) >= len(d.ev)


def test_default_last_visit_is_latest_visit_on_record(data):
    d, _ = data
    assert default_last_visit(d, "P001") == pd.Timestamp("2026-10-05")


def test_worsening_patients_are_alerts_on_weight(data):
    d, _ = data
    t = triage_all(d)
    assert set(t) == set(d.pts["patient_id"])
    for pid in ("P001", "P004"):                                    # generator's worsening patients
        assert t[pid]["status"] == "alert" and t[pid]["reason"].startswith("Weight -")
    assert all(t[p]["status"] != "alert" for p in ("P002", "P003", "P005"))


def test_summary_counts_and_window(data):
    d, _ = data
    lv = default_last_visit(d, "P001")
    s = summarize(d, "P001", lv)
    assert s["span"] == d.as_of - lv
    assert s["review"] == int((s["recent"]["review_status"] == "pending").sum())
    assert s["wt_pct"] < -0.05 and isinstance(s["gap"], int)


def test_db_version_changes_on_write(data):
    from review import apply_action
    d, folder = data
    v1 = db_version(folder)
    eid = d.ev_all[d.ev_all["review_status"] == "pending"].iloc[0]["event_id"]
    apply_action("accept", folder, eid, "Dr. Test")
    assert db_version(folder) != v1                                 # -> the app's cache reloads
