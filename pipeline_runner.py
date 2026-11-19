# pipeline_runner.py — generate synthetic reports, parse them, store structured events
#   python pipeline_runner.py        # all reports
#   python pipeline_runner.py 3      # first 3 reports only (quick LLM check)
import sys
import pandas as pd
from datetime import timedelta
from schema import load, save
from parser import parse_report
import Synthetic_datagenerator as generate_data


def flag_conflicts(events: pd.DataFrame) -> pd.DataFrame:
    """Same patient, same symptom, same day, reports that disagree -> both go to review.

    We never pick a winner: raw reports stay untouched and the clinician decides.
    Conflict = one report denies the symptom (severity 0) while another rates it > 0,
    or two severities on the same day are 4+ points apart.
    """
    ev = events.copy()
    scored = ev[ev["severity"].notna()]
    for _, grp in scored.groupby(["patient_id", "symptom", "onset"]):
        if grp["report_id"].nunique() < 2:
            continue
        sev = grp["severity"]
        if ((sev == 0).any() and (sev > 0).any()) or (sev.max() - sev.min() >= 4):
            idx = grp.index
            ev.loc[idx, "needs_review"] = True
            ev.loc[idx, "flags"] = ev.loc[idx, "flags"].apply(
                lambda f: ";".join([x for x in [f, "same_day_conflict"] if x]))
    return ev


def main():
    generate_data.main()                # writes data/*.csv with empty events
    store = load()
    reports = store["reports"]
    if len(sys.argv) > 1:
        reports = reports.head(int(sys.argv[1]))

    rows, event_id = [], 0
    for _, r in reports.iterrows():
        extraction, by = parse_report(r.raw_text)
        reported = pd.to_datetime(r.reported_at)
        for e in extraction.events:
            event_id += 1
            rows.append({
                "event_id": event_id,
                "report_id": r.report_id,
                "patient_id": r.patient_id,
                "symptom": e.symptom,
                "severity": e.severity,
                "frequency": e.frequency,
                "onset": (reported + timedelta(days=e.onset_offset_days)).date().isoformat(),
                "related_med": e.related_med,
                "confidence": e.confidence,
                "extracted_by": by,
                "needs_review": e.needs_review,
                "flags": ";".join(e.flags),
            })
        print(f"report {r.report_id}: {len(extraction.events)} events via {by}")

    events = pd.DataFrame(rows, columns=store["events"].columns)
    events["severity"] = events["severity"].astype("Float64")
    events["frequency"] = events["frequency"].astype("Int64")
    store["events"] = flag_conflicts(events)
    save(store)

    ev = store["events"]
    print(f"\n{len(ev)} events saved from {len(reports)} reports "
          f"({(ev['extracted_by'] == 'llm').sum()} via LLM, {(ev['extracted_by'] == 'rule').sum()} via regex fallback). "
          f"needs_review: {int(ev['needs_review'].sum())}, "
          f"same-day conflicts: {int(ev['flags'].str.contains('same_day_conflict').sum())}")


if __name__ == "__main__":
    main()
