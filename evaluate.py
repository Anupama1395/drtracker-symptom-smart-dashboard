# evaluate.py — how good is the parser? Two checks:
#   1. Gold set (gold.json, 25 hand-labelled reports incl. phrasings the generator never uses):
#      symptom precision/recall, severity MAE, frequency accuracy — LLM vs regex baseline.
#   2. Silver labels (data/silver_labels.csv, symptom labels carried over from the generator
#      templates): symptom precision/recall over every stored report, from the saved events.
import json
import os

import pandas as pd
from parser import parse_report, regex_parse


def score_gold(gold, fn):
    tp = fp = fn_ = 0
    sev_err = sev_n = freq_ok = freq_n = 0
    for item in gold:
        pred = fn(item["text"])
        pred_syms = {e.symptom for e in pred.events}
        gold_syms = {e["symptom"] for e in item["events"]}
        tp += len(pred_syms & gold_syms)
        fp += len(pred_syms - gold_syms)
        fn_ += len(gold_syms - pred_syms)
        for g in item["events"]:
            p = next((e for e in pred.events if e.symptom == g["symptom"]), None)
            if p is None:
                continue
            if g["severity"] is not None and p.severity is not None:
                sev_err += abs(p.severity - g["severity"]); sev_n += 1
            if g["frequency"] is not None:
                freq_n += 1; freq_ok += int(p.frequency == g["frequency"])
    prec = tp / (tp + fp) if tp + fp else 0
    rec = tp / (tp + fn_) if tp + fn_ else 0
    mae = sev_err / sev_n if sev_n else float("nan")
    return (f"precision={prec:.2f} recall={rec:.2f} severity MAE={mae:.2f} (n={sev_n}) "
            f"frequency exact={freq_ok}/{freq_n}")


def score_silver(events_path="data/events.csv", labels_path="data/silver_labels.csv",
                 reports_path="data/reports.csv"):
    ev = pd.read_csv(events_path)
    labels = pd.read_csv(labels_path)
    report_ids = pd.read_csv(reports_path)["report_id"]
    parsed = set(ev["report_id"])
    labels = labels[labels["report_id"].isin(parsed)]          # pipeline may have run on a subset
    pred = set(zip(ev["report_id"], ev["symptom"]))
    truth = set(zip(labels["report_id"], labels["symptom"]))
    tp = len(pred & truth)
    prec = tp / len(pred) if pred else 0
    rec = tp / len(truth) if truth else 0
    by = ev.drop_duplicates("report_id")["extracted_by"].value_counts().to_dict()
    return (f"precision={prec:.2f} recall={rec:.2f} over {len(parsed)}/{len(report_ids)} reports "
            f"(parsed by: {by})")


if __name__ == "__main__":
    with open("gold.json") as f:
        gold = json.load(f)

    print(f"Gold set ({len(gold)} hand-labelled reports)")
    by = [parse_report(item["text"])[1] for item in gold[:1]]
    if by[0] == "llm":
        print("  llm   ", score_gold(gold, lambda t: parse_report(t)[0]))
    else:
        print("  llm    LLM unavailable (Ollama not running?) — skipped, not scored as regex")
    print("  regex ", score_gold(gold, regex_parse))

    # Labels produced by clinicians in the dashboard (Reports tab -> Export corrections as gold labels)
    if os.path.exists("gold_corrections.json"):
        with open("gold_corrections.json") as f:
            corrected = json.load(f)
        if corrected:
            print(f"\nClinician-reviewed reports ({len(corrected)}, from gold_corrections.json)")
            if by[0] == "llm":
                print("  llm   ", score_gold(corrected, lambda t: parse_report(t)[0]))
            print("  regex ", score_gold(corrected, regex_parse))
            print(f"\nCombined ({len(gold) + len(corrected)} reports)")
            if by[0] == "llm":
                print("  llm   ", score_gold(gold + corrected, lambda t: parse_report(t)[0]))
            print("  regex ", score_gold(gold + corrected, regex_parse))

    print("\nSilver labels (stored pipeline output vs generator template labels)")
    print("  ", score_silver())
