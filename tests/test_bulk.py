# Phase 4: bulk import of many notes (CLI + the function the dashboard uses).
import subprocess
import sys
from pathlib import Path

import pytest

import pipeline_runner
from bulk_import import main as bulk_main
from ingest import import_files
from parser import regex_parse
from schema import load

ROOT = Path(__file__).resolve().parents[1]
regex_fn = lambda t: (regex_parse(t), "rule")


def note(pid, name, date, body):
    return (f"Patient: {pid} ({name}) - synthetic test report\nDOB: 1970-01-01\n"
            f"Diagnosis: Laryngeal SCC, diagnosed 2026-07-20\nTreatment start: 2026-08-20 (radiation)\n"
            f"Medications: cisplatin 40 mg/m2 weekly, ondansetron 8 mg\n\nDate: {date}\n{body}\n").encode()


@pytest.fixture
def folder(tmp_path):
    pipeline_runner.main(["--folder", str(tmp_path / "data")])
    return str(tmp_path / "data")


def test_new_existing_duplicate_and_bad_files(folder):
    files = [("P201_A.txt", note("P201", "Ana Test", "2026-09-10", "Nausea about 5/10.")),
             ("P002_more.txt", note("P002", "Dana Okafor", "2026-10-11", "Threw up twice.")),
             ("scan.png", b"\x89PNG"),
             ("empty.txt", b"   ")]
    out = {r["file"]: r for r in import_files(files, folder=folder, parse_fn=regex_fn)}
    assert out["P201_A.txt"]["status"] == "added" and "new patient" in out["P201_A.txt"]["detail"]
    assert out["P002_more.txt"]["status"] == "added" and out["P002_more.txt"]["events"] >= 1
    assert out["scan.png"]["status"] == out["empty.txt"]["status"] == "failed"
    s = load(folder)
    assert "P201" in set(s["patients"]["patient_id"])
    again = import_files(files[:2], folder=folder, parse_fn=regex_fn)
    assert [r["status"] for r in again] == ["duplicate", "duplicate"]
    assert len(load(folder)["reports"]) == len(s["reports"])          # re-import adds nothing


def test_selected_patient_mismatch_is_refused(folder):
    out = import_files([("x.txt", note("P201", "Ana Test", "2026-09-10", "Pain 4/10."))], folder=folder,
                       selected_patient="P003", parse_fn=regex_fn)
    assert out[0]["status"] == "failed" and "P201" in out[0]["detail"]


def test_cli_imports_a_folder_of_generated_notes(folder, tmp_path):
    gen = tmp_path / "notes"
    subprocess.run([sys.executable, str(ROOT / "docs/tools/make_synthetic_docs.py"), "--start", "300",
                    "--count", "5", "--out", str(gen)], check=True, capture_output=True)
    before = len(load(folder)["patients"])
    assert bulk_main([str(gen), "--folder", folder]) == 0
    assert len(load(folder)["patients"]) == before + 5
    assert bulk_main([str(gen), "--folder", folder]) == 0            # all duplicates, still OK
    assert len(load(folder)["patients"]) == before + 5
