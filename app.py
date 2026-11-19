# app.py — Dr.Tracker pre-visit view: "what changed since the last visit?"
from datetime import datetime, time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from ingest import extract_text, ingest_text, other_patient_ids, read_header, register_patient
from schema import load

st.set_page_config(page_title="Dr.Tracker — Pre-visit view", layout="wide")

# Distinct colours, none of them red: red is reserved for alerts (weight line, error box).
SEVERITY_SYMPTOMS = {"nausea": "#1f77b4", "mouth_sores": "#9467bd", "pain": "#ff7f0e",
                     "swallowing_difficulty": "#17becf", "fatigue": "#8c564b"}

if "upload_n" not in st.session_state:
    st.session_state.upload_n = 0          # bumping this clears the file uploader after an ingest

# ---------------- load ----------------
store = load()
ev, meds, vit, pts, reps, visits, procs = (
    store[k] for k in ["events", "medications", "vitals", "patients", "reports", "visits", "procedures"])
if ev.empty or pts.empty:
    st.warning("No parsed events yet. Run `python pipeline_runner.py` first, then reload.")
    st.stop()

ev["onset"] = pd.to_datetime(ev["onset"])
ev["flags"] = ev["flags"].fillna("").astype(str)
ev["needs_review"] = ev["needs_review"].astype(str).str.lower().eq("true")
reps["reported_at"] = pd.to_datetime(reps["reported_at"])
vit["measured_at"] = pd.to_datetime(vit["measured_at"])
for col in ["start_date", "end_date"]:
    meds[col] = pd.to_datetime(meds[col])
visits["visit_date"] = pd.to_datetime(visits["visit_date"])
procs["date"] = pd.to_datetime(procs["date"])
for col in ["diagnosis_date", "first_visit_date", "treatment_start"]:
    pts[col] = pd.to_datetime(pts[col])

# "Today" for synthetic data = the latest thing anyone recorded.
as_of = max(reps["reported_at"].max(), vit["measured_at"].max()).normalize()

# ---------------- sidebar ----------------
labels = (pts["patient_id"] + " — " + pts["name"]).tolist()
if pending := st.session_state.pop("select_patient", None):   # set by an upload, applied before the widget exists
    st.session_state.patient = next((l for l in labels if l.startswith(pending)), None)

with st.sidebar:
    if msg := st.session_state.pop("upload_msg", None):
        st.success(msg, icon=":material/check_circle:")
    choice = st.selectbox("Patient", labels, index=None, placeholder="Choose a patient…", key="patient")
    pid = choice.split(" — ")[0] if choice else None

    if pid:
        p_visits = visits[(visits["patient_id"] == pid) & (visits["visit_date"] <= as_of)].sort_values("visit_date")
        default_lv = p_visits["visit_date"].iloc[-1] if len(p_visits) else as_of - pd.Timedelta(days=14)
        last_visit = st.date_input("Last visit", value=default_lv.date(), key=f"last_visit_{pid}",
                                   help="Defaults to the most recent visit on record; change it to compare another window.")
        full_history = st.toggle("Show full history on chart", help="Zoom out to diagnosis, staging and first consult.")

    st.divider()
    st.subheader("Add a patient document")
    st.warning("Synthetic data only. Never upload real patient information.", icon=":material/warning:")
    upload = st.file_uploader("Patient note (.txt or .pdf)", type=["txt", "pdf"],
                              key=f"upload_{st.session_state.upload_n}")
    text = header = None
    if upload is not None:
        try:
            text = extract_text(upload.name, upload.getvalue())   # only the text is kept, never the file
            header = read_header(text)
        except ValueError as err:
            st.error(str(err))
    target = pid or (header or {}).get("patient_id")
    if header:
        if not target:
            st.caption("No patient selected and no 'Patient: P###' line in the document.")
        elif target in set(pts["patient_id"]):
            st.caption(f"Will be filed under **{target}**.")
        else:
            st.caption(f"Will be filed under **{target}**, a new patient "
                       f"(**{header['name'] or 'name missing'}**) created from the document header.")
    doc_default = pd.Timestamp(header["date"]).date() if header and header["date"] else as_of.date()
    doc_date = st.date_input("Document date", value=doc_default,
                             key=f"doc_date_{st.session_state.upload_n}_{upload.name if upload else ''}",
                             help="Taken from the document's 'Date:' line when it has one.")
    if st.button("Parse and add as report", disabled=text is None, width="stretch",
                 icon=":material/upload_file:"):
        try:
            if pid and (others := other_patient_ids(text, pid)):
                raise ValueError(f"This document mentions {', '.join(others)}, but {pid} is selected. "
                                 f"Nothing was saved. Select the right patient (or none) and upload again.")
            if not target:
                raise ValueError("Choose a patient, or add a line like 'Patient: P007 (Name)' to the document.")
            fresh = load()             # from disk: the tables above were converted to datetimes for display
            is_new = target not in set(fresh["patients"]["patient_id"])
            if is_new:
                fresh = register_patient(fresh, header)
            with st.spinner("Parsing…"):
                rid, new = ingest_text(fresh, target, text, source="upload",
                                       reported_at=datetime.combine(doc_date, time(12, 0)))
            st.session_state.upload_msg = (
                (f"New patient {target} ({header['name']}) registered from the document. " if is_new else "")
                + f"Report {rid} added for {target}: {len(new)} symptom event(s), all sent to the review queue.")
            st.session_state.upload_n += 1
            st.session_state.select_patient = target
            st.rerun()
        except ValueError as err:
            st.error(str(err))

# ---------------- no patient chosen: patient list ----------------
if not pid:
    st.title("Dr.Tracker — pre-visit view")
    st.info("Choose a patient in the sidebar to open their pre-visit summary.", icon=":material/person_search:")
    roster = []
    for _, p in pts.iterrows():
        w = vit[(vit["patient_id"] == p["patient_id"]) & (vit["kind"] == "weight_kg")].sort_values("measured_at")
        e = ev[ev["patient_id"] == p["patient_id"]]
        roster.append({
            "Patient": f"{p['patient_id']} — {p['name']}", "Diagnosis": p["diagnosis"],
            "Treatment start": p["treatment_start"],
            "Last report": reps[reps["patient_id"] == p["patient_id"]]["reported_at"].max(),
            "Weight since start": (w["value"].iloc[-1] / w["value"].iloc[0] - 1) if len(w) > 1 else None,
            "Items needing review": int(e["needs_review"].sum())})
    st.dataframe(pd.DataFrame(roster), hide_index=True, alt="All patients",
                 column_config={"Treatment start": st.column_config.DateColumn(),
                                "Last report": st.column_config.DatetimeColumn(format="MMM D, YYYY"),
                                "Weight since start": st.column_config.NumberColumn(format="percent")})
    st.stop()

lv = pd.Timestamp(last_visit)
p_ev = ev[ev["patient_id"] == pid]
p_vit = vit[(vit["patient_id"] == pid) & (vit["kind"] == "weight_kg")].sort_values("measured_at")
p_med = meds[meds["patient_id"] == pid]
p_proc = procs[procs["patient_id"] == pid].sort_values("date")
p_all_visits = visits[visits["patient_id"] == pid].sort_values("visit_date")
pt = pts.set_index("patient_id").loc[pid]

st.title(f"{pt['name']} — {pt['diagnosis']}")

# ---------------- patient history panel ----------------
with st.container(border=True):
    st.subheader("Patient history", anchor=False)
    fmt = lambda d: "—" if pd.isna(d) else d.strftime("%b %d, %Y")
    c = st.columns(6)
    c[0].metric("Diagnosis", pt["diagnosis"] or "—")
    c[1].metric("Diagnosis date", fmt(pt["diagnosis_date"]))
    c[2].metric("First visit", fmt(pt["first_visit_date"]))
    c[3].metric("Treatment start", fmt(pt["treatment_start"]))
    c[4].metric("Days since diagnosis", "—" if pd.isna(pt["diagnosis_date"]) else (as_of - pt["diagnosis_date"]).days)
    c[5].metric("Visits", len(p_all_visits[p_all_visits["visit_date"] <= as_of]))
    st.caption(f"As of {fmt(as_of)} (latest data in the synthetic dataset).")

# ---------------- since last visit ----------------
recent = p_ev[p_ev["onset"] >= lv]
earlier = p_ev[p_ev["onset"] < lv]
span = max(as_of - lv, pd.Timedelta(days=1))           # equally long window before the last visit
prev = p_ev[(p_ev["onset"] < lv) & (p_ev["onset"] >= lv - span)]
reported = lambda df: df[df["severity"].isna() | (df["severity"] > 0)]   # drop explicit denials

def days_reported(df, sym):
    return reported(df[df["symptom"] == sym])["onset"].dt.normalize().nunique()

vom = reported(p_ev[p_ev["symptom"] == "vomiting"])
vomits = int(vom[vom["onset"] >= lv]["frequency"].sum())
vomits_before = int(vom[(vom["onset"] < lv) & (vom["onset"] >= lv - span)]["frequency"].sum())

if len(p_vit):
    since = p_vit[p_vit["measured_at"] >= lv]
    wt_change = (p_vit["value"].iloc[-1] - since["value"].iloc[0]) if not since.empty else 0.0
    baseline = p_vit["value"].iloc[0]                                  # weight at treatment start
    wt_pct_total = (p_vit["value"].iloc[-1] - baseline) / baseline     # same baseline as the -5% line
else:
    wt_change = wt_pct_total = None

deltas = {}
for sym in ["nausea", "mouth_sores", "pain"]:
    now, before = (recent[recent["symptom"] == sym]["severity"].mean(),
                   earlier[earlier["symptom"] == sym]["severity"].mean())
    deltas[sym] = (now, None if pd.isna(now) or pd.isna(before) else round(now - before, 1))

flags = int(recent["needs_review"].sum())
n_new = int(((reps["patient_id"] == pid) & (reps["reported_at"] >= lv)).sum())
rising = {s: d for s, (_, d) in deltas.items() if d is not None and d > 0}
worst = max(rising, key=rising.get) if rising else None
plural = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"

weight_txt = ("" if wt_change is None else
              f"weight **{wt_change:+.1f} kg** since last visit (**{wt_pct_total:+.1%}** since treatment start), ")
st.info(f"**{plural(n_new, 'new report')}**, **{plural(vomits, 'vomiting episode')}**, {weight_txt}"
        f"**{plural(flags, 'item')} need{'s' if flags == 1 else ''} review**. "
        + (f"Biggest change: **{worst.replace('_', ' ')} +{rising[worst]:.1f}**." if worst else ""),
        title=f"Since last visit ({lv.strftime('%b %d')})")
if wt_pct_total is not None and wt_pct_total < -0.05:
    st.error(f"Weight down {abs(wt_pct_total):.1%} since treatment start (alert threshold 5%). Check nutrition.",
             icon=":material/monitor_weight:")

# ---------------- all symptoms ----------------
row1 = st.columns(4)
for col, sym in zip(row1[:3], ["nausea", "mouth_sores", "pain"]):
    now, delta = deltas[sym]
    col.metric(f"{sym.replace('_', ' ').capitalize()} (avg 0–10)", "—" if pd.isna(now) else f"{now:.1f}",
               delta, delta_color="inverse", delta_description="vs. before last visit", border=True)
row1[3].metric("Vomiting episodes", vomits, vomits - vomits_before, delta_color="inverse",
               delta_description=f"vs. previous {span.days} days", border=True)

row2 = st.columns(4)
for col, sym, label in [(row2[0], "swallowing_difficulty", "Swallowing difficulty (days)"),
                        (row2[1], "fatigue", "Fatigue (days)")]:
    n_now, n_prev = days_reported(recent, sym), days_reported(prev, sym)
    col.metric(label, n_now, n_now - n_prev, delta_color="inverse",
               delta_description=f"days reported vs. previous {span.days} days", border=True)
other_now = recent[recent["symptom"] == "other"]
n_other_prev = len(prev[prev["symptom"] == "other"])
row2[2].metric("Other / vague reports", len(other_now), len(other_now) - n_other_prev, delta_color="inverse",
               delta_description=f"vs. previous {span.days} days", border=True)
row2[3].metric("Weight since start", "—" if wt_pct_total is None else f"{wt_pct_total:+.1%}",
               None if wt_change is None else f"{wt_change:+.1f} kg",
               delta_description="since last visit", border=True)

if len(other_now):
    with st.container(border=True):
        st.markdown("**Vague reports since last visit** — patient's own words, not coded to a symptom")
        st.dataframe(other_now.merge(reps[["report_id", "raw_text"]], on="report_id")[["onset", "raw_text"]]
                     .sort_values("onset", ascending=False),
                     column_config={"onset": st.column_config.DateColumn("Date"),
                                    "raw_text": st.column_config.TextColumn("Report", width="large")},
                     hide_index=True, alt="Vague patient reports since the last visit")

# ---------------- timeline chart ----------------
start = pt["treatment_start"] if not pd.isna(pt["treatment_start"]) else p_ev["onset"].min()
x0 = min(d for d in [pt["diagnosis_date"], p_proc["date"].min(), start] if not pd.isna(d)) if full_history \
    else start - pd.Timedelta(days=3)
x1 = as_of + pd.Timedelta(days=4)
days = pd.date_range(min(x0, start), x1, freq="D")

fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22], vertical_spacing=0.05)
for sym, color in SEVERITY_SYMPTOMS.items():
    s = p_ev[(p_ev["symptom"] == sym) & p_ev["severity"].notna()]
    if s.empty:
        continue
    daily = (s.assign(day=s["onset"].dt.normalize()).groupby("day")["severity"].max()
             .reindex(days))                                  # missing days stay NaN -> gaps, not lines
    fig.add_trace(go.Scatter(x=daily.index, y=daily.values, mode="lines+markers", connectgaps=False,
                             name=sym.replace("_", " "), line_color=color, marker_color=color), row=1, col=1)

fig.add_trace(go.Scatter(x=p_all_visits["visit_date"], y=["Visits"] * len(p_all_visits), mode="markers",
                         marker=dict(symbol="circle", size=11, color="#444"), name="visit",
                         text=p_all_visits["visit_type"] + " — " + p_all_visits["provider"].fillna(""),
                         hovertemplate="%{x|%b %d}<br>%{text}<extra></extra>"), row=2, col=1)
done = p_proc["status"].eq("completed")
fig.add_trace(go.Scatter(x=p_proc["date"], y=["Procedures"] * len(p_proc), mode="markers",
                         marker=dict(symbol=["diamond" if d else "diamond-open" for d in done], size=12,
                                     color="#2ca02c", line=dict(color="#2ca02c", width=2)),
                         name="procedure", text=p_proc["procedure"] + " (" + p_proc["status"] + ")",
                         hovertemplate="%{x|%b %d}<br>%{text}<extra></extra>"), row=2, col=1)

# Vertical markers: mid-treatment medication starts (gray, dashed) and the last visit (black).
lines = [(m["start_date"], f"start {m['drug']}", "gray", "dash") for _, m in p_med.iterrows()
         if not pd.isna(start) and m["start_date"] > start]
lines.append((lv, "last visit", "black", "solid"))
for k, (x, label, color, dash) in enumerate(sorted(lines, key=lambda l: l[0])):
    fig.add_vline(x=x.isoformat(), line_color=color, line_dash=dash, line_width=1.5)
    fig.add_annotation(x=x.isoformat(), y=1.0 + 0.07 * (k % 3), xref="x", yref="paper", text=label,
                       showarrow=False, xanchor="left", font=dict(color=color, size=11))

fig.update_yaxes(range=[0, 10.5], title_text="severity 0–10", row=1, col=1)
fig.update_yaxes(categoryorder="array", categoryarray=["Procedures", "Visits"], row=2, col=1)
fig.update_xaxes(range=[x0, x1])
fig.update_layout(title=dict(text="Symptom severity (daily max) with medications, visits and procedures", y=0.98),
                  height=500, margin=dict(t=110), legend=dict(orientation="h", y=-0.12))
st.plotly_chart(fig, width="stretch")
st.caption("Vomiting is a count, so it's in its tile rather than on this chart. Gaps = no report that day. "
           "Filled diamond = completed procedure, open = scheduled.")

# ---------------- weight ----------------
if len(p_vit):
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=p_vit["measured_at"], y=p_vit["value"], mode="lines", name="weight kg",
                              line_color="#1f77b4"))
    fig2.add_hline(y=p_vit["value"].iloc[0] * 0.95, line_dash="dot", line_color="red", annotation_text="-5% alert")
    fig2.add_vline(x=lv.isoformat(), line_color="black", line_width=1.5)
    fig2.update_layout(title="Weight (kg)", height=260)
    st.plotly_chart(fig2, width="stretch")

# ---------------- history tables ----------------
tab_v, tab_m, tab_p = st.tabs(["Visit timeline", "Medications", "Procedures"])
with tab_v:
    st.dataframe(p_all_visits.sort_values("visit_date", ascending=False)[
                     ["visit_date", "visit_type", "provider", "notes"]],
                 column_config={"visit_date": st.column_config.DateColumn("Date"),
                                "notes": st.column_config.TextColumn("Notes", width="large")},
                 hide_index=True, alt="Visit timeline")
with tab_m:
    m = p_med.copy()
    m["status"] = m["end_date"].apply(lambda e: "Active" if pd.isna(e) or e > as_of else "Stopped")
    st.dataframe(m.sort_values("start_date")[["drug", "dose", "start_date", "end_date", "status",
                                              "prescribed_by", "reason"]],
                 column_config={"start_date": st.column_config.DateColumn("Start"),
                                "end_date": st.column_config.DateColumn("End")},
                 hide_index=True, alt="Medications with start and end dates")
with tab_p:
    st.dataframe(p_proc[["date", "procedure", "status"]],
                 column_config={"date": st.column_config.DateColumn("Date")},
                 hide_index=True, alt="Procedures with dates and status")

# ---------------- review queue + raw reports ----------------
with st.expander(f"Reports needing review ({flags})"):
    q = recent[recent["needs_review"]].merge(reps[["report_id", "source", "raw_text"]], on="report_id", how="left")
    st.dataframe(q[["onset", "symptom", "severity", "confidence", "extracted_by", "source", "flags", "raw_text"]],
                 column_config={"onset": st.column_config.DateColumn("Date")},
                 hide_index=True, alt="Events needing clinician review")

with st.expander("All raw reports"):
    st.dataframe(reps[reps["patient_id"] == pid].sort_values("reported_at", ascending=False),
                 hide_index=True, alt="All raw patient reports")
