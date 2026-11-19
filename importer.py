# importer.py — bring patient history in from an external system (EHR / CRM).
#
# STUB, on purpose. The interface is the point:
#
#     rows = import_from_source(source)          # -> {table_name: DataFrame} in our schema
#     store = merge_into_store(store, rows)       # de-duplicated append, then schema.save(store)
#
# One example source is implemented: a FHIR R4 Bundle read from a JSON file
# (Patient, Condition, Encounter, Procedure, MedicationRequest, Observation).
# A real integration would fetch the same Bundle from the practice's FHIR API
# (e.g. GET [base]/Patient/{id}/$everything with an OAuth token, SMART on FHIR);
# the mapping below would not change. See README "EHR import".
#
#   python importer.py samples/fhir_bundle_P006.json            # preview what would be imported
#   python importer.py samples/fhir_bundle_P006.json --save     # merge into data/ and save
import json
import sys
from pathlib import Path

import pandas as pd

from schema import COLUMNS, SYMPTOMS, empty_store, load, save

# FHIR Procedure.status -> our procedures.status
PROC_STATUS = {"completed": "completed", "preparation": "scheduled", "in-progress": "scheduled",
               "not-done": "cancelled", "stopped": "cancelled"}
BODY_WEIGHT_LOINC = "29463-7"

# Natural keys used to de-duplicate when the same record is imported twice
KEYS = {"patients": ["patient_id"], "visits": ["visit_id"], "procedures": ["procedure_id"],
        "medications": ["patient_id", "drug", "start_date"], "vitals": ["patient_id", "measured_at", "kind"],
        "events": ["event_id"]}


def _ref_id(ref: str | None) -> str | None:
    """'Patient/P006' -> 'P006'"""
    return ref.split("/")[-1] if ref else None


def _date(value: str | None) -> str | None:
    return value[:10] if value else None


def _text(concept: dict | None) -> str | None:
    if not concept:
        return None
    return concept.get("text") or next((c.get("display") for c in concept.get("coding", [])), None)


def import_fhir_bundle(bundle: dict) -> dict[str, pd.DataFrame]:
    """Map a FHIR R4 Bundle onto our tables. Unknown resource types are ignored, not guessed."""
    if bundle.get("resourceType") != "Bundle":
        raise ValueError("Not a FHIR Bundle")
    resources = [e["resource"] for e in bundle.get("entry", []) if "resource" in e]
    out = {name: [] for name in COLUMNS}

    conditions = {}
    for r in resources:
        if r["resourceType"] == "Condition":
            conditions[_ref_id(r["subject"]["reference"])] = (_text(r.get("code")), _date(r.get("onsetDateTime")))

    encounters = [r for r in resources if r["resourceType"] == "Encounter"]
    for r in resources:
        rt = r["resourceType"]
        if rt == "Patient":
            pid = r["id"]
            name = r.get("name", [{}])[0]
            dx, dx_date = conditions.get(pid, (None, None))
            first_visit = min((_date(e["period"]["start"]) for e in encounters
                               if _ref_id(e["subject"]["reference"]) == pid), default=None)
            start = next((_date(m.get("authoredOn")) for m in resources
                          if m["resourceType"] == "MedicationRequest"
                          and _ref_id(m["subject"]["reference"]) == pid
                          and "cisplatin" in (_text(m.get("medicationCodeableConcept")) or "").lower()), None)
            out["patients"].append({
                "patient_id": pid,
                "name": " ".join(name.get("given", []) + [name.get("family", "")]).strip(),
                "dob": r.get("birthDate"), "diagnosis": dx, "diagnosis_date": dx_date,
                "first_visit_date": first_visit, "treatment_start": start})
        elif rt == "Encounter":
            out["visits"].append({
                "visit_id": f"FHIR-{r['id']}", "patient_id": _ref_id(r["subject"]["reference"]),
                "visit_date": _date(r["period"]["start"]),
                "visit_type": _text((r.get("type") or [None])[0]),
                "provider": ((r.get("participant") or [{}])[0].get("individual") or {}).get("display"),
                "notes": _text((r.get("reasonCode") or [None])[0])})
        elif rt == "Procedure":
            out["procedures"].append({
                "procedure_id": f"FHIR-{r['id']}", "patient_id": _ref_id(r["subject"]["reference"]),
                "procedure": _text(r.get("code")),
                "date": _date(r.get("performedDateTime") or (r.get("performedPeriod") or {}).get("start")),
                "status": PROC_STATUS.get(r.get("status"), r.get("status"))})
        elif rt == "MedicationRequest":
            dosage = (r.get("dosageInstruction") or [{}])[0]
            validity = (r.get("dispenseRequest") or {}).get("validityPeriod") or {}
            out["medications"].append({
                "patient_id": _ref_id(r["subject"]["reference"]),
                "drug": _text(r.get("medicationCodeableConcept")), "dose": dosage.get("text"),
                "start_date": _date(validity.get("start") or r.get("authoredOn")),
                "end_date": _date(validity.get("end")) if r.get("status") != "active" else None,
                "prescribed_by": (r.get("requester") or {}).get("display"),
                "reason": _text((r.get("reasonCode") or [None])[0])})
        elif rt == "Observation":
            pid = _ref_id(r["subject"]["reference"])
            codes = {c.get("code") for c in r.get("code", {}).get("coding", [])}
            when = _date(r.get("effectiveDateTime"))
            if BODY_WEIGHT_LOINC in codes and "valueQuantity" in r:
                out["vitals"].append({"patient_id": pid, "measured_at": when, "kind": "weight_kg",
                                      "value": r["valueQuantity"]["value"]})
            else:
                symptom = (_text(r.get("code")) or "").lower().replace(" ", "_")
                if symptom in SYMPTOMS:                 # a structured symptom score from another system
                    out["events"].append({
                        "event_id": f"FHIR-{r['id']}", "report_id": None, "patient_id": pid,
                        "symptom": symptom, "severity": r.get("valueInteger"), "frequency": None,
                        "onset": when, "related_med": None, "confidence": 1.0,
                        "extracted_by": "import", "needs_review": False, "flags": "fhir_import"})
                # anything else is skipped rather than guessed

    return {name: pd.DataFrame(rows, columns=COLUMNS[name]) for name, rows in out.items() if rows}


IMPORTERS = {"fhir_bundle": import_fhir_bundle}


def import_from_source(source: str | Path | dict, kind: str = "fhir_bundle") -> dict[str, pd.DataFrame]:
    """Single entry point for every external source. `source` is a file path or an already
    parsed payload; `kind` picks the mapper. Returns new rows per table (nothing is saved)."""
    if kind not in IMPORTERS:
        raise ValueError(f"Unknown source kind {kind!r}; known: {sorted(IMPORTERS)}")
    payload = source if isinstance(source, dict) else json.loads(Path(source).read_text())
    return IMPORTERS[kind](payload)


def merge_into_store(store: dict, rows: dict[str, pd.DataFrame]) -> dict:
    """Append imported rows; on a natural-key clash the imported row replaces the old one."""
    merged = dict(store)
    for name, new in rows.items():
        old = merged.get(name, empty_store()[name])
        combined = pd.concat([old, new], ignore_index=True) if len(old) else new
        merged[name] = combined.drop_duplicates(subset=KEYS.get(name), keep="last").reset_index(drop=True)
    return merged


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python importer.py <bundle.json> [--save]")
    rows = import_from_source(sys.argv[1])
    for name, df in rows.items():
        print(f"{name}: {len(df)} rows")
    if "--save" in sys.argv:
        save(merge_into_store(load(), rows))
        print("merged into data/ (note: pipeline_runner.py regenerates data/ and will drop it)")
