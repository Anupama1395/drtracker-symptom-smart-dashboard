# bulk_import.py — import a folder of .txt/.pdf patient notes into SymptomLens.
#   python bulk_import.py docs/samples/generated            # every note in the folder
#   python bulk_import.py docs/samples/generated --limit 10 # first 10 (alphabetical)
#   python bulk_import.py notes/ --patient P002             # all notes belong to P002
# Safe to re-run: notes already imported (same patient, date and text) are skipped.
import argparse
import sys
import time
from pathlib import Path

import pandas as pd

from ingest import import_files


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="folder of .txt/.pdf notes (or a single file)")
    ap.add_argument("--patient", help="file every note under this patient ID")
    ap.add_argument("--folder", default="data", help="SymptomLens data folder")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)

    src = Path(args.path)
    paths = sorted(p for p in (src.iterdir() if src.is_dir() else [src]) if p.suffix.lower() in (".txt", ".pdf"))
    if args.limit:
        paths = paths[:args.limit]
    if not paths:
        print(f"No .txt or .pdf files in {src}")
        return 1
    t0 = time.perf_counter()

    def progress(n, total, row):
        print(f"[{n}/{total}] {row['file']}: {row['status']}"
              + (f" ({row['reports']} report(s), {row['events']} event(s))" if row["status"] == "added" else "")
              + (f" — {row['detail']}" if row["detail"] else ""))

    summary = pd.DataFrame(import_files([(p.name, p.read_bytes()) for p in paths], folder=args.folder,
                                        selected_patient=args.patient, progress=progress))
    secs = time.perf_counter() - t0
    counts = summary["status"].value_counts().to_dict()
    print(f"\nDone in {secs:.1f}s ({secs / len(paths):.2f}s per file): "
          + ", ".join(f"{v} {k}" for k, v in counts.items())
          + f". {int(summary['reports'].sum())} report(s), {int(summary['events'].sum())} event(s) added.")
    return 0 if "failed" not in counts else 2


if __name__ == "__main__":
    sys.exit(main())
