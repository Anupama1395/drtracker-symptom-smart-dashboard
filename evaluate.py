# evaluate.py — how good is the parser?
#   python evaluate.py                                   # gold set + clinician corrections + silver labels
#   python evaluate.py --models llama3.1:8b qwen2.5:7b mistral   # benchmark several local models
#
# 1. Gold set (gold.json, 30 hand-labelled reports, half in phrasings the generator never uses):
#    symptom precision/recall, severity MAE, frequency accuracy — LLM vs regex baseline.
# 2. Clinician corrections (gold_corrections.json, exported from the dashboard), when present.
# 3. Silver labels (data/silver_labels.csv): symptom labels carried over from the generator templates,
#    scored against the stored events. Optimistic for regex, which was written against those templates.
import argparse
import json
import os
import time
from datetime import date

import pandas as pd

import parser
from parser import parse_report, regex_parse
from schema import load


def gold_metrics(gold, preds) -> dict:
    tp = fp = fn_ = 0
    sev_err = sev_n = freq_ok = freq_n = 0
    for item, pred in zip(gold, preds):
        pred_syms = {e.symptom for e in pred.events}
        gold_syms = {e["symptom"] for e in item["events"]}
        tp += len(pred_syms & gold_syms)
        fp += len(pred_syms - gold_syms)
        fn_ += len(gold_syms - pred_syms)
        for g in item["events"]:
            p = next((e for e in pred.events if e.symptom == g["symptom"]), None)
            if p is None:
                continue
            if g.get("severity") is not None and p.severity is not None:
                sev_err += abs(p.severity - g["severity"]); sev_n += 1
            if g.get("frequency") is not None:
                freq_n += 1; freq_ok += int(p.frequency == g["frequency"])
    return {"precision": tp / (tp + fp) if tp + fp else 0.0, "recall": tp / (tp + fn_) if tp + fn_ else 0.0,
            "severity_mae": sev_err / sev_n if sev_n else float("nan"), "severity_n": sev_n,
            "freq_ok": freq_ok, "freq_n": freq_n}


def fmt(m: dict) -> str:
    return (f"precision={m['precision']:.2f} recall={m['recall']:.2f} severity MAE={m['severity_mae']:.2f} "
            f"(n={m['severity_n']}) frequency exact={m['freq_ok']}/{m['freq_n']}")


def score_gold(gold, fn):
    return fmt(gold_metrics(gold, [fn(item["text"]) for item in gold]))


def score_silver(folder="data", labels_path=None):
    """Stored events vs generator template labels. Only reports that have a label are scored
    (uploads and imports don't)."""
    labels = pd.read_csv(labels_path or os.path.join(folder, "silver_labels.csv"))
    ev = load(folder)["events"]
    ev = ev[ev["report_id"].isin(set(labels["report_id"])) & (ev["review_status"] != "dismissed")]
    parsed = set(ev["report_id"])
    labels = labels[labels["report_id"].isin(parsed)]
    pred = set(zip(ev["report_id"], ev["symptom"]))
    truth = set(zip(labels["report_id"], labels["symptom"]))
    tp = len(pred & truth)
    prec = tp / len(pred) if pred else 0
    rec = tp / len(truth) if truth else 0
    by = ev.drop_duplicates("report_id")["extracted_by"].value_counts().to_dict()
    return f"precision={prec:.2f} recall={rec:.2f} over {len(parsed)} labelled reports (parsed by: {by})"


def load_sets():
    with open("gold.json") as f:
        gold = json.load(f)
    corrected = []
    if os.path.exists("gold_corrections.json"):
        with open("gold_corrections.json") as f:
            corrected = json.load(f)
    return gold, corrected


def run_model(model: str, items: list) -> dict:
    """Parse every item with one model (cache off, so timings are real)."""
    parser.MODEL = model
    os.environ["SYMPTOMLENS_LLM_CACHE"] = ""
    ok, status = parser.llm_status()
    if not ok:
        return {"model": model, "status": status}
    preds, by, partial, t0 = [], [], 0, time.perf_counter()
    for item in items:
        ext, b = parse_report(item["text"])
        preds.append(ext); by.append(b)
        partial += any("partial_llm_output" in e.flags for e in ext.events)
    secs = (time.perf_counter() - t0) / max(len(items), 1)
    m = gold_metrics(items, preds)
    n = len(items)
    m.update(model=model, status="ok", valid_json=(by.count("llm") - partial) / n,
             fallback=by.count("rule") / n, sec_per_report=secs)
    return m


def write_comparison(rows: list, items: list, path="docs/MODEL_COMPARISON.md"):
    n_items = len(items)
    regex = gold_metrics(items, [regex_parse(i["text"]) for i in items]) if items else None
    lines = [f"# Model comparison ({date.today().isoformat()})", "",
             f"{n_items} reports: gold.json" + (" + gold_corrections.json" if n_items > 30 else "")
             + f". Prompt version `{parser.PROMPT_VERSION}`, temperature 0, schema-constrained output.", "",
             "| Model | Precision | Recall | Severity MAE | Frequency exact | Valid JSON | Fallback | Sec/report |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["status"] != "ok":
            lines.append(f"| {r['model']} | not run: {r['status']} | | | | | | |")
            continue
        lines.append(f"| {r['model']} | {r['precision']:.2f} | {r['recall']:.2f} | {r['severity_mae']:.2f} "
                     f"| {r['freq_ok']}/{r['freq_n']} | {r['valid_json']:.0%} | {r['fallback']:.0%} "
                     f"| {r['sec_per_report']:.1f} |")
    if regex:
        lines.append(f"| regex baseline | {regex['precision']:.2f} | {regex['recall']:.2f} | "
                     f"{regex['severity_mae']:.2f} | {regex['freq_ok']}/{regex['freq_n']} | n/a | n/a | <0.01 |")
    ok = [r for r in rows if r["status"] == "ok"]
    lines += ["", ("**Pick:** " + max(ok, key=lambda r: (r["recall"] + r["precision"], -r["sec_per_report"]))["model"]
                   + " (highest precision + recall; speed breaks ties). Set it with `SYMPTOMLENS_MODEL`.")
              if ok else "**No model could be run.** Start Ollama and pull the models, then run this again."]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", help="benchmark these Ollama models and write docs/MODEL_COMPARISON.md")
    args = ap.parse_args()
    gold, corrected = load_sets()

    if args.models:
        items = gold + corrected
        rows = []
        for m in args.models:
            print(f"Running {m} on {len(items)} reports…")
            r = run_model(m, items)
            print("  ", fmt(r) if r["status"] == "ok" else r["status"])
            rows.append(r)
        print("Wrote", write_comparison(rows, items))
        raise SystemExit(0)

    ok, status = parser.llm_status()
    print(f"Gold set ({len(gold)} hand-labelled reports)   [{status}]")
    if ok:
        print("  llm   ", score_gold(gold, lambda t: parse_report(t)[0]))
    else:
        print("  llm    skipped (not scored as regex)")
    print("  regex ", score_gold(gold, regex_parse))

    if corrected:
        print(f"\nClinician-reviewed reports ({len(corrected)}, from gold_corrections.json)")
        if ok:
            print("  llm   ", score_gold(corrected, lambda t: parse_report(t)[0]))
        print("  regex ", score_gold(corrected, regex_parse))
        print(f"\nCombined ({len(gold) + len(corrected)} reports)")
        if ok:
            print("  llm   ", score_gold(gold + corrected, lambda t: parse_report(t)[0]))
        print("  regex ", score_gold(gold + corrected, regex_parse))

    print("\nSilver labels (stored pipeline output vs generator template labels)")
    print("  ", score_silver())
