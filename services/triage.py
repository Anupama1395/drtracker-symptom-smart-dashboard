# services/triage.py — what changed since the last visit, and how urgent each patient is.
# Pure pandas (no Streamlit), so it can be tested and cached.
import pandas as pd

from services.data import Data

STATUS_ORDER = ["alert", "watch", "stable"]


def nice(sym: str) -> str:
    return sym.replace("_", " ")


def reported(df):
    """Drop explicit denials (severity 0)."""
    return df[df["severity"].isna() | (df["severity"] > 0)]


def default_last_visit(d: Data, pid) -> pd.Timestamp:
    v = d.visits[(d.visits["patient_id"] == pid) & (d.visits["visit_date"] <= d.as_of)]
    if len(v):
        return v["visit_date"].max()
    # No visit on record (e.g. a patient created from an uploaded note): nothing has been seen at a
    # visit yet, so start the window no later than their first report, or older notes would be hidden.
    fallback = d.as_of - pd.Timedelta(days=14)
    first = d.reps.loc[d.reps["patient_id"] == pid, "reported_at"].min()
    return min(fallback, first.normalize()) if pd.notna(first) else fallback


def summarize(d: Data, pid, lv: pd.Timestamp) -> dict:
    """Everything the page shows for one patient and one 'since last visit' window."""
    ev, vit, reps, as_of = d.ev, d.vit, d.reps, d.as_of
    p_ev = ev[ev["patient_id"] == pid]
    recent, earlier = p_ev[p_ev["onset"] >= lv], p_ev[p_ev["onset"] < lv]
    span = max(as_of - lv, pd.Timedelta(days=1))           # equally long window before the last visit
    prev = p_ev[(p_ev["onset"] < lv) & (p_ev["onset"] >= lv - span)]
    days_rep = lambda df, sym: reported(df[df["symptom"] == sym])["onset"].dt.normalize().nunique()

    vom = reported(p_ev[p_ev["symptom"] == "vomiting"])
    s = {"p_ev": p_ev, "recent": recent, "span": span,
         "vomits": int(vom[vom["onset"] >= lv]["frequency"].sum()),
         "vomits_before": int(vom[(vom["onset"] < lv) & (vom["onset"] >= lv - span)]["frequency"].sum())}

    sev = {}
    for sym in ["nausea", "mouth_sores", "pain"]:
        now, before = (recent[recent["symptom"] == sym]["severity"].mean(),
                       earlier[earlier["symptom"] == sym]["severity"].mean())
        sev[sym] = (now, None if pd.isna(now) or pd.isna(before) else round(now - before, 1))
    s["sev"] = sev
    s["days"] = {sym: (days_rep(recent, sym), days_rep(prev, sym)) for sym in ["swallowing_difficulty", "fatigue"]}

    p_vit = vit[(vit["patient_id"] == pid) & (vit["kind"] == "weight_kg")].sort_values("measured_at")
    s["p_vit"] = p_vit
    if len(p_vit):
        since = p_vit[p_vit["measured_at"] >= lv]
        s["wt_change"] = (p_vit["value"].iloc[-1] - since["value"].iloc[0]) if not since.empty else 0.0
        s["wt_pct"] = (p_vit["value"].iloc[-1] - p_vit["value"].iloc[0]) / p_vit["value"].iloc[0]
    else:
        s["wt_change"] = s["wt_pct"] = None

    s["other_now"] = recent[recent["symptom"] == "other"]
    pending = recent["review_status"].eq("pending")             # counts show only what is still open
    s["review"] = int(pending.sum())
    s["conflicts"] = int((recent["flags"].str.contains("same_day_conflict") & pending).sum())
    p_reps = reps[reps["patient_id"] == pid]
    s["n_new"] = int((p_reps["reported_at"] >= lv).sum())

    have = set(p_reps["reported_at"].dt.normalize())          # longest run of days with no check-in
    run = s["gap"] = 0
    for day in pd.date_range(lv.normalize(), as_of, freq="D"):
        run = 0 if day in have else run + 1
        s["gap"] = max(s["gap"], run)

    rising = {k: dl for k, (_, dl) in sev.items() if dl is not None and dl >= 1.0}
    s["rising"], s["falling"] = rising, {k: dl for k, (_, dl) in sev.items() if dl is not None and dl <= -0.5}
    if s["wt_pct"] is not None and s["wt_pct"] < -0.05:
        s["status"], s["reason"] = "alert", f"Weight {s['wt_pct']:+.1%}"
    elif rising:
        top = max(rising, key=rising.get)
        s["status"], s["reason"] = "watch", f"{nice(top).capitalize()} +{rising[top]:.1f}"
    elif s["vomits"] > s["vomits_before"]:
        s["status"], s["reason"] = "watch", f"Vomiting ×{s['vomits']}, up"
    elif s["gap"] >= 3:
        s["status"], s["reason"] = "watch", f"No check-in {s['gap']} d"
    else:
        s["status"], s["reason"] = "stable", "No major changes"
    return s


def triage_all(d: Data) -> dict:
    """Every patient, with their own default last visit. Small values only (cache-friendly)."""
    out = {}
    for _, p in d.pts.iterrows():
        t = summarize(d, p["patient_id"], default_last_visit(d, p["patient_id"]))
        out[p["patient_id"]] = {"name": p["name"], "status": t["status"], "reason": t["reason"],
                                "rank": STATUS_ORDER.index(t["status"]), "wt_pct": t["wt_pct"],
                                "review": t["review"]}
    return out
