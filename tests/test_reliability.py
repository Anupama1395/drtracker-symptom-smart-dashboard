# Phase 1: data survives pipeline runs, storage round-trips cleanly, duplicates and regex edge cases.
import os
import sqlite3
from contextlib import closing
from datetime import datetime

import pandas as pd
import pytest

import pipeline_runner
from ingest import DuplicateReportError, ingest_text
from parser import regex_parse
from review import accept, edit
from schema import DB_NAME, load, load_corrections, save, save_corrections

regex_fn = lambda t: (regex_parse(t), "rule")


@pytest.fixture
def folder(tmp_path):
    pipeline_runner.main(["--folder", str(tmp_path)])              # first run: generate + parse
    return str(tmp_path)


def by_sym(text):
    return {e.symptom: e for e in regex_parse(text).events}


def test_first_run_generates_and_parses(folder):
    s = load(folder)
    assert len(s["reports"]) > 100 and set(s["events"]["report_id"]) == set(s["reports"]["report_id"])


def test_uploads_reviews_and_corrections_survive_parse_and_reparse(folder):
    s = load(folder)
    rid, new = ingest_text(s, "P002", "Nausea about 7/10 after lunch.", reported_at=datetime(2026, 10, 11, 12),
                           folder=folder, parse_fn=regex_fn)
    s = load(folder)
    reviewed_id = s["events"][s["events"]["review_status"] == "pending"].iloc[0]["event_id"]
    s = accept(s, reviewed_id)
    edited_id = new.iloc[0]["event_id"]
    s, corr = edit(s, load_corrections(folder), edited_id, "nausea", 6, None)
    save(s, folder); save_corrections(corr, folder)

    assert pipeline_runner.main(["--folder", folder]) == 0           # nothing new to parse
    assert pipeline_runner.main(["--reparse", "--folder", folder]) == 0

    s = load(folder)
    ev = s["events"].set_index(s["events"]["event_id"].astype(str))
    assert ev.loc[str(reviewed_id), "review_status"] == "accepted"
    assert (ev.loc[str(edited_id), "review_status"], ev.loc[str(edited_id), "severity"]) == ("edited", 6)
    assert rid in set(s["reports"]["report_id"])
    assert len(load_corrections(folder)) == 1


def test_generate_refuses_when_work_would_be_lost(folder):
    s = load(folder)
    save(accept(s, s["events"].iloc[0]["event_id"]), folder)
    assert pipeline_runner.main(["--generate", "--folder", folder]) == 1   # stdin isn't a terminal: cancelled
    assert (load(folder)["events"]["review_status"] == "accepted").any()
    assert pipeline_runner.main(["--generate", "--yes", "--folder", folder]) == 0
    assert not (load(folder)["events"]["review_status"] == "accepted").any()


def test_counts_stay_integers_after_save_and_load(folder):
    s = load(folder)
    assert str(s["events"]["frequency"].dtype) == "Int64"
    save(s, folder)
    raw = pd.read_csv(os.path.join(folder, "events.csv"), dtype=str)["frequency"].dropna()
    assert not raw.str.contains(r"\.").any()                        # "1", never "1.0"


def test_full_save_keeps_logs_and_is_atomic(folder):
    s = load(folder)
    s, corr = edit(s, load_corrections(folder), s["events"].iloc[0]["event_id"], "pain", 4, None)
    save(s, folder); save_corrections(corr, folder)
    logged = len(corr)
    assert logged >= 1
    save(load(folder), folder)                                       # another full save
    with closing(sqlite3.connect(os.path.join(folder, DB_NAME))) as conn, conn:
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == logged
    assert not [f for f in os.listdir(folder) if f.endswith(".db") and f != DB_NAME]   # no temp files left


def test_duplicate_upload_is_refused(folder):
    s = load(folder)
    when = datetime(2026, 10, 11, 12)
    ingest_text(s, "P003", "Pain about 5/10 today.", reported_at=when, folder=folder, parse_fn=regex_fn)
    with pytest.raises(DuplicateReportError):
        ingest_text(load(folder), "P003", "pain about 5/10 today", reported_at=when, folder=folder, parse_fn=regex_fn)
    ingest_text(load(folder), "P003", "Pain about 5/10 today.", reported_at=datetime(2026, 10, 12, 12),
                folder=folder, parse_fn=regex_fn)                    # another day is fine


def test_number_belongs_to_the_nearest_symptom_before_it():
    ev = by_sym("Throat pain around 8/10, worse when swallowing.")
    assert ev["pain"].severity == 8 and ev["swallowing_difficulty"].severity is None


def test_vague_phrase_kept_alongside_other_symptoms():
    assert set(by_sym("Felt off all day. Nausea about 4/10.")) == {"nausea", "other"}


def test_one_severity_word_mapping():
    from parser import SEVERITY_WORDS, SYSTEM
    assert by_sym("Nausea was bad today.")["nausea"].severity == SEVERITY_WORDS["bad"]
    assert all(w in SYSTEM for w in SEVERITY_WORDS)                 # the LLM gets the same table
