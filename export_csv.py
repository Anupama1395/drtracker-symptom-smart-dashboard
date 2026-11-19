# export_csv.py — write every table from data/symptomlens.db to readable CSVs in data/.
# Review clicks update SQLite only; run this before committing data/ so the CSVs match.
from schema import EXTRA_TABLES, export_csv, load, _load_extra, storable
import os

if __name__ == "__main__":
    store = load()
    export_csv(store)
    for name in EXTRA_TABLES:
        storable(_load_extra(name, "data")).to_csv(os.path.join("data", f"{name}.csv"), index=False)
    print("Exported:", ", ".join(f"{k} ({len(v)})" for k, v in store.items()), "+ corrections, review_log")
