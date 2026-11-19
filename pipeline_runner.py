# pipeline_runner.py — generate synthetic data, parse every report, store structured events
#   python pipeline_runner.py        # all reports
#   python pipeline_runner.py 3      # first 3 reports only (quick LLM check)
import sys
from schema import load, save
from parser import parse_report
from ingest import event_rows, flag_conflicts, typed_events
import Synthetic_datagenerator as generate_data


def main():
    generate_data.main()                # writes data/*.csv with empty events
    store = load()
    reports = store["reports"]
    if len(sys.argv) > 1:
        reports = reports.head(int(sys.argv[1]))

    rows = []
    for _, r in reports.iterrows():
        extraction, by = parse_report(r.raw_text)
        rows += event_rows(r.report_id, r.patient_id, r.reported_at, extraction, by,
                           first_event_id=len(rows) + 1)
        print(f"report {r.report_id}: {len(extraction.events)} events via {by}")

    store["events"] = flag_conflicts(typed_events(rows, store["events"].columns))
    save(store)

    ev = store["events"]
    print(f"\n{len(ev)} events saved from {len(reports)} reports "
          f"({(ev['extracted_by'] == 'llm').sum()} via LLM, {(ev['extracted_by'] == 'rule').sum()} via regex fallback). "
          f"needs_review: {int(ev['needs_review'].sum())}, "
          f"same-day conflicts: {int(ev['flags'].str.contains('same_day_conflict').sum())}")


if __name__ == "__main__":
    main()
