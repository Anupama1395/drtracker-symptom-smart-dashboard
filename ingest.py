# ingest.py — turning text into stored reports + events. Shared by the batch pipeline
# (pipeline_runner.py) and the dashboard's document upload.
import io
import re
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd

from parser import Extraction, parse_report
from schema import save

MAX_UPLOAD_CHARS = 5000          # an upload is a patient note, not a medical record dump


def event_rows(report_id, patient_id, reported_at, extraction: Extraction, by: str,
               first_event_id: int, extra_flags: list[str] | None = None,
               force_review: bool = False) -> list[dict]:
    """One events-table row per SymptomEvent in an extraction."""
    reported = pd.to_datetime(reported_at)
    rows = []
    for i, e in enumerate(extraction.events):
        flags = list(e.flags) + list(extra_flags or [])
        rows.append({
            "event_id": first_event_id + i,
            "report_id": report_id,
            "patient_id": patient_id,
            "symptom": e.symptom,
            "severity": e.severity,
            "frequency": e.frequency,
            "onset": (reported + timedelta(days=e.onset_offset_days)).date().isoformat(),
            "related_med": e.related_med,
            "confidence": e.confidence,
            "extracted_by": by,
            "needs_review": bool(e.needs_review or force_review),
            "flags": ";".join(flags),
        })
    return rows


def flag_conflicts(events: pd.DataFrame) -> pd.DataFrame:
    """Same patient, same symptom, same day, reports that disagree -> both go to review.

    We never pick a winner: raw reports stay untouched and the clinician decides.
    Conflict = one report denies the symptom (severity 0) while another rates it > 0,
    or two severities on the same day are 4+ points apart.
    """
    ev = events.copy()
    if ev.empty:
        return ev
    ev["flags"] = ev["flags"].fillna("").astype(str)
    scored = ev[ev["severity"].notna()]
    for _, grp in scored.groupby(["patient_id", "symptom", "onset"]):
        if grp["report_id"].nunique() < 2:
            continue
        sev = grp["severity"].astype(float)
        if ((sev == 0).any() and (sev > 0).any()) or (sev.max() - sev.min() >= 4):
            idx = grp.index
            ev.loc[idx, "needs_review"] = True
            ev.loc[idx, "flags"] = ev.loc[idx, "flags"].apply(
                lambda f: f if "same_day_conflict" in f else ";".join([x for x in [f, "same_day_conflict"] if x]))
    return ev


def typed_events(rows: list[dict], columns) -> pd.DataFrame:
    events = pd.DataFrame(rows, columns=columns)
    events["severity"] = pd.to_numeric(events["severity"]).astype("Float64")
    events["frequency"] = pd.to_numeric(events["frequency"]).astype("Int64")
    events["needs_review"] = events["needs_review"].astype(bool)
    return events


# ---------------- document upload ----------------
def extract_text(filename: str, data: bytes) -> str:
    """Text from an uploaded .txt or .pdf. Only the text is kept, never the file."""
    name = filename.lower()
    if name.endswith(".txt"):
        text = data.decode("utf-8", errors="replace")
    elif name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    else:
        raise ValueError(f"Unsupported file type: {filename} (use .txt or .pdf)")
    text = " ".join(text.split())                       # collapse PDF line breaks / spacing
    if not text:
        raise ValueError("No text found in the file (scanned PDFs need OCR, which isn't supported).")
    return text[:MAX_UPLOAD_CHARS]


def read_header(text: str) -> dict:
    """Pull the optional header from an uploaded note, e.g.

        Patient: P007 (Leo Martins)   DOB: 1966-12-03
        Diagnosis: Hypopharyngeal SCC, diagnosed 2026-08-20
        Treatment start: 2026-09-21   Medications: cisplatin 40 mg/m2 weekly, ondansetron 8 mg
        Date: 2026-10-14
        <the patient's own words>

    Plain regexes on fixed labels, no AI: identity and dates are never guessed. Missing labels
    come back as None. `body` is the part after "Date:" (what gets symptom-parsed)."""
    date_re = r"(\d{4}-\d{2}-\d{2})"
    grab = lambda pat: (m.groups() if (m := re.search(pat, text, re.I)) else None)
    pid = grab(r"Patient:\s*(P\d{3})(?:\s*\(([^)]+)\))?")
    dx = grab(rf"Diagnosis:\s*(.+?)(?:,\s*diagnosed\s*{date_re})?\s+(?:Treatment start|Medications|Date|DOB):")
    meds = grab(r"Medications:\s*(.+?)\s+(?:Date|Treatment start|Diagnosis|DOB):")
    date_m = re.search(rf"\bDate:\s*{date_re}", text)
    return {
        "patient_id": pid[0] if pid else None,
        "name": pid[1].strip() if pid and pid[1] else None,
        "dob": (grab(rf"DOB:\s*{date_re}") or [None])[0],
        "diagnosis": dx[0].strip() if dx else None,
        "diagnosis_date": dx[1] if dx else None,
        "treatment_start": (grab(rf"Treatment start:\s*{date_re}") or [None])[0],
        "medications": [m.strip() for m in meds[0].split(",") if m.strip()] if meds else [],
        "date": date_m.group(1) if date_m else None,
        "body": text[date_m.end():].strip() if date_m else text,
    }


def register_patient(store: dict, header: dict) -> dict:
    """Add a patient (and their listed medications) that only exists in an uploaded document."""
    pid = header["patient_id"]
    if not pid or not header["name"]:
        raise ValueError("A new patient needs at least 'Patient: P### (Name)' in the document.")
    patient = pd.DataFrame([{
        "patient_id": pid, "name": header["name"], "dob": header["dob"], "diagnosis": header["diagnosis"],
        "diagnosis_date": header["diagnosis_date"], "first_visit_date": None,
        "treatment_start": header["treatment_start"]}], columns=store["patients"].columns)
    meds = pd.DataFrame([{
        "patient_id": pid, "drug": m.split()[0].lower(), "dose": " ".join(m.split()[1:]) or None,
        "start_date": header["treatment_start"], "end_date": None,
        "prescribed_by": "from uploaded document", "reason": None}
        for m in header["medications"]], columns=store["medications"].columns)
    store["patients"] = pd.concat([store["patients"], patient], ignore_index=True)
    if len(meds):
        store["medications"] = pd.concat([store["medications"], meds], ignore_index=True)
    return store


def other_patient_ids(text: str, patient_id: str) -> list[str]:
    """Patient IDs (P###) written in the document that are NOT the selected patient.
    Filing a note on the wrong chart is a real clinical risk, so the upload refuses these."""
    return sorted(set(re.findall(r"\bP\d{3}\b", text)) - {patient_id})


def ingest_text(store: dict, patient_id: str, text: str, source: str = "upload",
                reported_at: datetime | None = None, folder: str | None = "data",
                parse_fn: Callable[[str], tuple[Extraction, str]] = parse_report):
    """Parse one piece of text, append it as a new report + events, save, and return
    (report_id, new_events_df). Uploaded events always go to the review queue.
    The full text is stored as the report; only the part after a "Date:" header is parsed."""
    reports, events = store["reports"], store["events"]
    report_id = int(pd.to_numeric(reports["report_id"]).max()) + 1 if len(reports) else 1
    first_event_id = int(pd.to_numeric(events["event_id"]).max()) + 1 if len(events) else 1
    reported_at = (reported_at or datetime.now()).replace(microsecond=0)

    extraction, by = parse_fn(read_header(text)["body"])
    new_report = pd.DataFrame([[report_id, patient_id, reported_at.isoformat(), source, text]],
                              columns=reports.columns)
    rows = event_rows(report_id, patient_id, reported_at, extraction, by, first_event_id,
                      extra_flags=[source] if source == "upload" else None,
                      force_review=(source == "upload"))
    new_events = typed_events(rows, events.columns)

    store["reports"] = pd.concat([reports, new_report], ignore_index=True)
    store["events"] = flag_conflicts(
        pd.concat([events, new_events], ignore_index=True) if len(events) else new_events)
    if folder:
        save(store, folder)
    return report_id, new_events
