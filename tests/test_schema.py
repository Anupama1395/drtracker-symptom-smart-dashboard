# Schema, storage, generator, upload and FHIR-import tests. Everything runs in a temp folder;
# the LLM is never called (uploads are parsed with the regex parser).
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

import Synthetic_datagenerator as gen
from importer import import_from_source, merge_into_store
from ingest import extract_text, flag_conflicts, ingest_text
from parser import regex_parse
from schema import COLUMNS, empty_store, load, save

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "samples" / "fhir_bundle_P006.json"
regex_fn = lambda text: (regex_parse(text), "rule")


@pytest.fixture
def generated(tmp_path):
    gen.main(str(tmp_path))
    return tmp_path, load(str(tmp_path))


# ---------------- schema + storage ----------------
def test_empty_store_has_every_table_and_column():
    store = empty_store()
    assert set(store) == {"patients", "visits", "procedures", "medications", "reports", "events", "vitals"}
    for name, df in store.items():
        assert list(df.columns) == COLUMNS[name]


def test_save_load_roundtrip_and_sqlite_mirror(generated):
    folder, store = generated
    again = load(str(folder))
    for name in COLUMNS:
        assert len(again[name]) == len(store[name])
    with sqlite3.connect(folder / "drtracker.db") as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(COLUMNS) <= tables


def test_load_missing_folder_gives_empty_tables(tmp_path):
    store = load(str(tmp_path / "nothing_here"))
    assert all(df.empty for df in store.values())


def test_flag_conflicts_handles_empty_data():
    assert flag_conflicts(empty_store()["events"]).empty


def test_flag_conflicts_keeps_both_rows_and_flags_them():
    events = pd.DataFrame([
        {"event_id": 1, "report_id": 1, "patient_id": "P1", "symptom": "nausea", "severity": 8.0,
         "onset": "2026-10-01", "needs_review": False, "flags": ""},
        {"event_id": 2, "report_id": 2, "patient_id": "P1", "symptom": "nausea", "severity": 0.0,
         "onset": "2026-10-01", "needs_review": False, "flags": ""},
        {"event_id": 3, "report_id": 3, "patient_id": "P1", "symptom": "pain", "severity": 5.0,
         "onset": "2026-10-01", "needs_review": False, "flags": ""},
    ])
    out = flag_conflicts(events)
    assert len(out) == 3
    assert out.loc[:1, "needs_review"].all() and not out.loc[2, "needs_review"]
    assert all("same_day_conflict" in f for f in out.loc[:1, "flags"])


# ---------------- generator: history is consistent ----------------
def test_patient_timeline_is_in_order(generated):
    _, s = generated
    for _, p in s["patients"].iterrows():
        dx, first, start = (pd.Timestamp(p[c]) for c in ["diagnosis_date", "first_visit_date", "treatment_start"])
        assert dx < first < start
        pre = s["procedures"][(s["procedures"]["patient_id"] == p["patient_id"])
                              & ~s["procedures"]["procedure"].str.contains("PEG")]
        assert (pd.to_datetime(pre["date"]) >= dx).all() and (pd.to_datetime(pre["date"]) < start).all()
        v = s["visits"][s["visits"]["patient_id"] == p["patient_id"]]
        assert pd.to_datetime(v["visit_date"]).min() == first
        assert set(v["visit_type"]) <= {"first consult", "follow-up", "treatment review"}


def test_worsening_patients_switch_antiemetic(generated):
    _, s = generated
    m = s["medications"]
    for pid in gen.WORSENING:
        ond = m[(m["patient_id"] == pid) & (m["drug"] == "ondansetron")].iloc[0]
        ola = m[(m["patient_id"] == pid) & (m["drug"] == "olanzapine")].iloc[0]
        assert pd.Timestamp(ond["end_date"]) < pd.Timestamp(ola["start_date"])
        assert "nausea" in ola["reason"] and isinstance(ola["prescribed_by"], str)


def test_generator_is_deterministic(tmp_path):
    gen.main(str(tmp_path / "a")); gen.main(str(tmp_path / "b"))
    for name in ["reports", "visits", "procedures"]:
        pd.testing.assert_frame_equal(load(str(tmp_path / "a"))[name], load(str(tmp_path / "b"))[name])


# ---------------- upload path ----------------
def make_pdf(text: str) -> bytes:
    """Smallest valid one-page PDF containing `text` (built by hand, no PDF writer needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def test_extract_text_from_txt_and_pdf():
    assert extract_text("note.txt", b"Nausea about 5/10.\n\n") == "Nausea about 5/10."
    assert "Nausea about 5/10" in extract_text("note.pdf", make_pdf("Nausea about 5/10 today"))


@pytest.mark.parametrize("name, data", [("scan.png", b"\x89PNG"), ("empty.txt", b"   \n")])
def test_extract_text_rejects_bad_files(name, data):
    with pytest.raises(ValueError):
        extract_text(name, data)


def test_upload_adds_report_and_events_for_review(generated):
    folder, store = generated
    n_reports = len(store["reports"])
    text = extract_text("note.pdf", make_pdf("Threw up twice last night, nausea 7/10"))
    rid, new = ingest_text(store, "P002", text, source="upload",
                           reported_at=datetime(2026, 10, 9, 12), folder=str(folder), parse_fn=regex_fn)

    saved = load(str(folder))                                     # persisted, not just in memory
    assert len(saved["reports"]) == n_reports + 1
    rep = saved["reports"].iloc[-1]
    assert (rep["report_id"], rep["patient_id"], rep["source"]) == (rid, "P002", "upload")
    assert set(new["symptom"]) == {"vomiting", "nausea"}
    assert new["needs_review"].all() and new["flags"].str.contains("upload").all()
    assert (new["onset"] == "2026-10-08").all()                   # "last night" -> day before
    assert not any(Path(folder).glob("*.pdf"))                    # the file itself is never stored


# ---------------- FHIR import ----------------
def test_fhir_bundle_maps_onto_our_tables():
    rows = import_from_source(BUNDLE)
    assert {k: len(v) for k, v in rows.items()} == {
        "patients": 1, "visits": 3, "procedures": 3, "medications": 2, "events": 1, "vitals": 2}
    p = rows["patients"].iloc[0]
    assert (p["patient_id"], p["name"], p["diagnosis"]) == ("P006", "Morgan Ellis", "Oropharyngeal SCC")
    assert (p["diagnosis_date"], p["first_visit_date"], p["treatment_start"]) == ("2026-07-28", "2026-08-06", "2026-09-07")
    assert rows["procedures"].set_index("procedure")["status"]["Feeding tube (PEG) placement"] == "scheduled"
    assert rows["events"].iloc[0]["extracted_by"] == "import"   # hemoglobin is skipped, not guessed


def test_fhir_import_twice_does_not_duplicate(generated):
    _, store = generated
    rows = import_from_source(BUNDLE)
    once = merge_into_store(store, rows)
    twice = merge_into_store(once, import_from_source(BUNDLE))
    for name in rows:
        assert len(twice[name]) == len(once[name])
    assert "P006" in set(twice["patients"]["patient_id"])


def test_importer_rejects_unknown_input():
    with pytest.raises(ValueError):
        import_from_source({"resourceType": "Patient"})
    with pytest.raises(ValueError):
        import_from_source(BUNDLE, kind="salesforce")


# ---------------- dashboard on empty data ----------------
def test_dashboard_shows_guidance_instead_of_crashing_on_empty_data(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.chdir(tmp_path)                                   # no data/ folder here
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert "pipeline_runner.py" in at.warning[0].value
