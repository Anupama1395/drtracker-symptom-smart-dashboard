# ingest.py — turning text into stored reports + events. Shared by the batch pipeline
# (pipeline_runner.py) and the dashboard's document upload.
import io
import re
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd

from parser import Extraction, _norm, parse_report
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
            "review_status": "pending" if (e.needs_review or force_review) else None,
            "reviewed_at": None,
            "reviewed_by": None,
            "model": extraction.model,
            "prompt_version": extraction.prompt_version,
            "parse_ms": extraction.parse_ms,
        })
    return rows


def parse_context(store: dict, patient_id: str, reported_at) -> dict:
    """What the LLM gets besides the text: the report date and the medications active that day
    (oldest first), so "the new pill" can be resolved to a drug name."""
    day = pd.Timestamp(reported_at).normalize()
    meds = store["medications"]
    meds = meds[meds["patient_id"] == patient_id].copy()
    meds["start"], meds["end"] = pd.to_datetime(meds["start_date"]), pd.to_datetime(meds["end_date"])
    active = meds[(meds["start"].isna() | (meds["start"] <= day)) & (meds["end"].isna() | (meds["end"] >= day))]
    return {"date": day.date().isoformat(),
            "medications": [f"{m['drug']} (since {m['start'].date()})" if not pd.isna(m["start"]) else str(m["drug"])
                            for _, m in active.sort_values("start").iterrows()]}


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
            # Only rows not already flagged: re-running this (e.g. after an upload) must not
            # re-open a conflict a clinician has already reviewed.
            idx = grp.index[~grp["flags"].str.contains("same_day_conflict")]
            if len(idx) == 0:
                continue
            ev.loc[idx, "needs_review"] = True
            if "review_status" in ev.columns:
                ev.loc[idx, "review_status"] = "pending"
            ev.loc[idx, "flags"] = ev.loc[idx, "flags"].apply(
                lambda f: ";".join([x for x in [f, "same_day_conflict"] if x]))
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


class DuplicateReportError(ValueError):
    """The same note was already stored for this patient on this date."""
    def __init__(self, report_id):
        super().__init__(f"Duplicate: this note was already added as report {report_id}. Nothing was saved.")
        self.report_id = report_id


def find_duplicate(reports: pd.DataFrame, patient_id: str, text: str, reported_at) -> int | None:
    """Same patient + same date + same text (ignoring case, punctuation and spacing)."""
    if reports.empty:
        return None
    day = pd.Timestamp(reported_at).date()
    same = reports[(reports["patient_id"] == patient_id)
                   & (pd.to_datetime(reports["reported_at"]).dt.date == day)]
    key = _norm(text)
    for _, r in same.iterrows():
        if _norm(r["raw_text"]) == key:
            return int(r["report_id"])
    return None


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
    if (dup := find_duplicate(reports, patient_id, text, reported_at)) is not None:
        raise DuplicateReportError(dup)

    body = read_header(text)["body"]
    if parse_fn is parse_report:                         # the real parser also gets date + medications
        extraction, by = parse_report(body, context=parse_context(store, patient_id, reported_at))
    else:
        extraction, by = parse_fn(body)
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


DATE_LINE = re.compile(r"\bDate:\s*(\d{4}-\d{2}-\d{2})")


def split_by_date(text: str) -> list[tuple[str | None, str]]:
    """A note with several "Date: YYYY-MM-DD" sections -> one (date, text) per section. Each section
    keeps the shared header (Patient/DOB/...) so it stands on its own as a report."""
    dates = list(DATE_LINE.finditer(text))
    if len(dates) <= 1:
        return [(dates[0].group(1) if dates else None, text)]
    header = text[:dates[0].start()].rstrip()
    out = []
    for i, m in enumerate(dates):
        end = dates[i + 1].start() if i + 1 < len(dates) else len(text)
        out.append((m.group(1), (header + "\n" if header else "") + text[m.start():end].strip()))
    return out


def ingest_document(store: dict, patient_id: str, text: str, source: str = "upload",
                    reported_at: datetime | None = None, folder: str | None = "data",
                    parse_fn: Callable[[str], tuple[Extraction, str]] = parse_report) -> list[dict]:
    """Ingest a whole uploaded note: one report per "Date:" section. Returns one result per section:
    {"date", "report_id", "events" (DataFrame) | None, "error" (str) | None}. A duplicate section is
    reported and skipped; the others are still stored."""
    results = []
    sections = split_by_date(text)
    for day, section in sections:
        when = (datetime.combine(pd.Timestamp(day).date(), datetime.min.time()).replace(hour=12)
                if day and len(sections) > 1 else reported_at)
        try:
            rid, new = ingest_text(store, patient_id, section, source=source, reported_at=when,
                                   folder=folder, parse_fn=parse_fn)
            results.append({"date": day, "report_id": rid, "events": new, "error": None})
        except DuplicateReportError as err:
            results.append({"date": day, "report_id": err.report_id, "events": None, "error": str(err)})
    return results
