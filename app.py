# app.py
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from schema import load

st.set_page_config(page_title="Dr.Tracker — Pre-visit view", layout="wide")
store = load()
ev, meds, vit, pts, reps = (store[k] for k in ["events", "medications", "vitals", "patients", "reports"])
if ev.empty:
    st.warning("No parsed events yet. Run `python pipeline_runner.py` first, then reload.")
    st.stop()
ev["onset"] = pd.to_datetime(ev["onset"])
ev["flags"] = ev["flags"].fillna("")
reps["reported_at"] = pd.to_datetime(reps["reported_at"])
vit["measured_at"] = pd.to_datetime(vit["measured_at"])
meds["start_date"] = pd.to_datetime(meds["start_date"])

# ---- sidebar: pick a patient ----
pid = st.sidebar.selectbox("Patient", pts["patient_id"] + " — " + pts["name"]).split(" — ")[0]
last_visit = st.sidebar.date_input("Last visit", value=ev["onset"].max() - pd.Timedelta(days=14))
p_ev  = ev[ev["patient_id"] == pid]
p_vit = vit[(vit["patient_id"] == pid) & (vit["kind"] == "weight_kg")]
p_med = meds[meds["patient_id"] == pid]

st.title(f"{pts.set_index('patient_id').loc[pid, 'name']} — {pts.set_index('patient_id').loc[pid, 'diagnosis']}")

# ---- 30-second banner: what changed since last visit ----
recent = p_ev[p_ev["onset"] >= pd.Timestamp(last_visit)]
earlier = p_ev[p_ev["onset"] <  pd.Timestamp(last_visit)]

cols = st.columns(4)
deltas = {}
for i, sym in enumerate(["nausea", "vomiting", "mouth_sores", "pain"]):
    now  = recent[recent["symptom"] == sym]["severity"].mean()
    before = earlier[earlier["symptom"] == sym]["severity"].mean()
    delta = None if pd.isna(now) or pd.isna(before) else round(now - before, 1)
    if delta is not None:
        deltas[sym] = delta
    cols[i].metric(sym.replace("_", " ").title(),
                   "—" if pd.isna(now) else f"{now:.1f}/10",
                   delta, delta_color="inverse")

vomits = recent[recent["symptom"] == "vomiting"]["frequency"].sum()
wt = p_vit.sort_values("measured_at")
since = wt[wt["measured_at"] >= pd.Timestamp(last_visit)]
wt_change = (wt["value"].iloc[-1] - since["value"].iloc[0]) if not since.empty else 0.0
flags = int(recent["needs_review"].sum())

n_new = int(((reps["patient_id"] == pid) & (reps["reported_at"] >= pd.Timestamp(last_visit))).sum())
worst = max(deltas, key=deltas.get) if deltas and max(deltas.values()) > 0 else None
st.info(f"Since last visit: **{n_new} new reports**, **{int(vomits)} vomiting episodes**, "
        f"weight **{wt_change:+.1f} kg**, **{flags} items need review**. "
        + (f"Biggest change: **{worst.replace('_', ' ')} +{deltas[worst]:.1f}**. " if worst else "")
        + ("⚠️ Weight loss over 5% — check nutrition." if wt_change / wt['value'].iloc[0] < -0.05 else ""))

# ---- chart 1: symptom severity over time, med changes marked ----
fig = go.Figure()
for sym in ["nausea", "vomiting", "mouth_sores", "pain", "swallowing_difficulty"]:
    s = p_ev[(p_ev["symptom"] == sym) & p_ev["severity"].notna()]
    if s.empty:
        continue
    daily = s.groupby("onset")["severity"].max().reset_index()
    fig.add_trace(go.Scatter(x=daily["onset"], y=daily["severity"], mode="lines+markers",
                             name=sym.replace("_", " ")))
for _, m in p_med.iterrows():
    if m["drug"] != "cisplatin":
        fig.add_vline(x=m["start_date"], line_dash="dash", line_color="gray",
                      annotation_text=f"start {m['drug']}", annotation_position="top")
fig.add_vline(x=pd.Timestamp(last_visit), line_color="red", annotation_text="last visit")
fig.update_layout(title="Symptom severity (0–10), daily max", yaxis_range=[0, 10], height=380,
                  legend_orientation="h")
st.plotly_chart(fig, width="stretch")

# ---- chart 2: weight with alert line ----
fig2 = go.Figure()
fig2.add_trace(go.Scatter(x=wt["measured_at"], y=wt["value"], mode="lines", name="weight kg"))
alert = wt["value"].iloc[0] * 0.95
fig2.add_hline(y=alert, line_dash="dot", line_color="red", annotation_text="-5% alert")
fig2.update_layout(title="Weight (kg)", height=260)
st.plotly_chart(fig2, width="stretch")

# ---- review queue ----
with st.expander(f"Reports needing review ({flags})"):
    q = recent[recent["needs_review"]].merge(reps[["report_id", "raw_text"]], on="report_id")
    st.dataframe(q[["onset", "symptom", "severity", "confidence", "extracted_by", "flags", "raw_text"]],
                 width="stretch")

# ---- raw reports, for traceability ----
with st.expander("All raw reports"):
    st.dataframe(reps[reps["patient_id"] == pid].sort_values("reported_at", ascending=False),
                 width="stretch")