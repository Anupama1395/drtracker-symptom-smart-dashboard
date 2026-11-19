# pipeline_runner.py — parse patient reports into structured events, without losing anyone's work.
#
#   python pipeline_runner.py                 # parse reports that have no events yet (first run: generate data too)
#   python pipeline_runner.py 3               # same, but at most 3 reports (quick LLM check)
#   python pipeline_runner.py --reparse       # re-run the parser on UNREVIEWED parser output only
#   python pipeline_runner.py --generate      # rebuild the synthetic dataset from scratch (asks first if
#                                             # uploads, imports or clinician reviews would be lost)
#
# Never touched by --parse / --reparse: raw report text, manual edits, accepted/edited/dismissed events,
# FHIR imports, and the corrections / review logs.
import argparse
import sys

import pandas as pd

import Synthetic_datagenerator as generate_data
from ingest import event_rows, flag_conflicts, parse_context, read_header, typed_events
from parser import parse_report
from schema import clear_review_history, load, load_corrections, save

PARSER_SOURCES = {"llm", "rule"}
REVIEWED = {"accepted", "edited", "dismissed"}


def protected_work(store, folder="data") -> list[str]:
    """Things a full regeneration would destroy."""
    ev, reps = store["events"], store["reports"]
    found = []
    if (n := int((reps["source"] == "upload").sum())):
        found.append(f"{n} uploaded report(s)")
    if (n := int(ev["review_status"].isin(REVIEWED).sum())):
        found.append(f"{n} clinician review decision(s)")
    if (n := int(ev["extracted_by"].isin(["manual", "import"]).sum())):
        found.append(f"{n} manual/imported event(s)")
    if (n := len(load_corrections(folder))):
        found.append(f"{n} logged correction(s)")
    generated = {p[0] for p in generate_data.PATIENTS}
    if (extra := sorted(set(store["patients"]["patient_id"]) - generated)):
        found.append(f"{len(extra)} patient(s) not from the generator ({', '.join(extra[:5])}{'…' if len(extra) > 5 else ''})")
    return found


def next_event_id(events) -> int:
    ids = pd.to_numeric(events["event_id"], errors="coerce")
    return int(ids.max()) + 1 if ids.notna().any() else 1


def parse_reports(store, reports, limit=None) -> list[dict]:
    rows, start = [], next_event_id(store["events"])
    if limit is not None:
        reports = reports.head(limit)
    for _, r in reports.iterrows():
        extraction, by = parse_report(read_header(r.raw_text)["body"],      # header lines aren't symptoms
                                      context=parse_context(store, r.patient_id, r.reported_at))
        rows += event_rows(r.report_id, r.patient_id, r.reported_at, extraction, by,
                           first_event_id=start + len(rows),
                           force_review=(r.source == "upload"),
                           extra_flags=["upload"] if r.source == "upload" else None)
        print(f"report {r.report_id}: {len(extraction.events)} events via {by}")
    return rows


def summary(store, n_reports):
    ev = store["events"]
    print(f"\n{n_reports} report(s) parsed. Database now has {len(ev)} events "
          f"({int((ev['extracted_by'] == 'llm').sum())} via LLM, {int((ev['extracted_by'] == 'rule').sum())} via "
          f"regex fallback). Pending review: {int((ev['review_status'] == 'pending').sum())}, "
          f"same-day conflicts: {int(ev['flags'].fillna('').str.contains('same_day_conflict').sum())}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("limit", nargs="?", type=int, help="parse at most this many reports")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--generate", action="store_true", help="rebuild synthetic data from scratch, then parse")
    mode.add_argument("--reparse", action="store_true", help="re-run the parser on unreviewed parser output")
    ap.add_argument("--yes", action="store_true", help="with --generate: don't ask, even if work would be lost")
    ap.add_argument("--folder", default="data")
    args = ap.parse_args(argv)
    folder = args.folder
    store = load(folder)

    if args.generate or store["reports"].empty:
        if not store["reports"].empty and (lost := protected_work(store, folder)) and not args.yes:
            print("Regenerating would permanently delete: " + "; ".join(lost) + ".")
            if not sys.stdin.isatty() or input("Type 'delete' to continue: ").strip() != "delete":
                print("Cancelled. Nothing was changed.")
                return 1
        generate_data.main(folder)
        clear_review_history(folder)
        store = load(folder)

    if args.reparse:
        ev = store["events"]
        by_report = ev.groupby("report_id")
        safe = [rid for rid, g in by_report
                if g["extracted_by"].isin(PARSER_SOURCES).all()
                and not g["review_status"].isin(REVIEWED).any()]
        todo = store["reports"][store["reports"]["report_id"].isin(safe)]
        kept = ev[~ev["report_id"].isin(safe)]
        print(f"Re-parsing {len(todo)} report(s) with unreviewed parser output "
              f"({len(store['reports']) - len(todo)} left as they are).")
        rows = parse_reports(store, todo, args.limit)
        if args.limit is not None:                       # only replace what was actually re-parsed
            done = set(todo.head(args.limit)["report_id"])
            kept = ev[~ev["report_id"].isin(done)]
        new = typed_events(rows, ev.columns)
        store["events"] = flag_conflicts(pd.concat([kept, new], ignore_index=True) if len(kept) else new)
        n = len(set(r["report_id"] for r in rows))
    else:
        parsed = set(store["events"]["report_id"].dropna())
        todo = store["reports"][~store["reports"]["report_id"].isin(parsed)]
        if todo.empty:
            print("Nothing to parse: every report already has events. "
                  "Use --reparse to re-run the parser on unreviewed output.")
            return 0
        rows = parse_reports(store, todo, args.limit)
        new = typed_events(rows, store["events"].columns)
        old = store["events"]
        store["events"] = flag_conflicts(pd.concat([old, new], ignore_index=True) if len(old) else new)
        n = len(set(r["report_id"] for r in rows))

    save(store, folder)
    summary(store, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
