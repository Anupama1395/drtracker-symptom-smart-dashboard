# app.py — SymptomLens pre-visit view: "what changed since the last visit?"
# Presentation only: all data comes from schema.load(); uploads go through ingest.py.
import html
from datetime import datetime, time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from ingest import extract_text, import_files, read_header
from pdf_summary import summary_pdf
from review import apply_action, ensure_review_columns, export_gold, undo_last
from schema import SYMPTOMS, load, load_corrections

st.set_page_config(page_title="SymptomLens — Pre-visit view", page_icon=":material/monitor_heart:", layout="wide")

# ---------------- design tokens ----------------
TEAL, TEXT, MUTED, BORDER = "#0F766E", "#1F2937", "#6B7280", "#E5E7EB"
GREEN, AMBER, RED = "#15803D", "#B45309", "#B91C1C"
# Okabe-Ito, colourblind-safe; no red (red = alerts only)
SYMPTOM_COLORS = {"nausea": "#0072B2", "pain": "#E69F00", "mouth_sores": "#CC79A7",
                  "swallowing_difficulty": "#009E73", "fatigue": "#56B4E9"}
STATUS = {"alert": ("Alert", "red", "🔴"), "watch": ("Watch", "amber", "🟠"), "stable": ("Stable", "green", "🟢")}
PLANNED_COURSE_DAYS = 49   # standard 7-week (35-fraction) chemoradiation; the schema has no planned-end date
FLAG_LABELS = {"same_day_conflict": ("Same-day conflict", "red"),
               "severity_unsupported": ("Severity not in text", "amber"),
               "negation_conflict": ("Negation conflict", "amber"),
               "evidence_not_in_text": ("Quote not in report", "amber"),
               "frequency_implausible": ("Implausible count", "amber"),
               "upload": ("Uploaded document", "teal"),
               "fhir_import": ("FHIR import", "teal"),
               "denial_unsupported": ("Denial not in text", "red"),
               "related_med_inferred": ("Medication link inferred", "amber"),
               "partial_llm_output": ("Partly invalid LLM output", "amber")}
# Where an event came from: shown on every review card and in the LLM-health panel
SOURCE = {"llm": ("LLM", "teal"), "rule": ("Regex fallback", "amber"), "manual": ("Manual edit", "green"),
          "import": ("FHIR import", "gray")}

st.html(f"""<style>
[class*="st-key-card"] {{ background:#FFFFFF; box-shadow:0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.04); }}
.pill {{ display:inline-flex; align-items:center; gap:6px; padding:3px 10px; border-radius:999px;
        font-size:.78rem; font-weight:600; line-height:1.4; margin:2px 6px 2px 0; white-space:nowrap; }}
.pill .dot {{ width:7px; height:7px; border-radius:50%; background:currentColor; }}
.pill-green {{ background:#DCFCE7; color:#166534; }}  .pill-amber {{ background:#FEF3C7; color:#92400E; }}
.pill-red {{ background:#FEE2E2; color:#991B1B; }}    .pill-gray {{ background:#F3F4F6; color:#374151; }}
.pill-teal {{ background:#CCFBF1; color:#115E59; }}
.hdr {{ display:flex; align-items:center; gap:16px; flex-wrap:wrap; }}
.avatar {{ width:56px; height:56px; border-radius:50%; background:{TEAL}; color:#fff; display:flex;
          align-items:center; justify-content:center; font-weight:700; font-size:1.25rem; flex:none; }}
.hdr-name {{ font-size:1.5rem; font-weight:700; color:{TEXT}; line-height:1.2; }}
.hdr-meta {{ color:{MUTED}; font-size:.9rem; margin-top:2px; }}
.hdr-right {{ margin-left:auto; text-align:right; }}
.muted {{ color:{MUTED}; font-size:.85rem; }}
.summary li {{ margin:4px 0; }}
.summary b.worse {{ color:{RED}; }} .summary b.better {{ color:{GREEN}; }} .summary b.action {{ color:{AMBER}; }}
.tl {{ border-left:2px solid {BORDER}; margin:4px 0 0 7px; padding-left:18px; }}
.tl-item {{ position:relative; padding-bottom:14px; }}
.tl-item::before {{ content:""; position:absolute; left:-25px; top:5px; width:12px; height:12px;
                   border-radius:50%; background:var(--c); box-shadow:0 0 0 3px #fff; }}
.tl-date {{ color:{MUTED}; font-size:.78rem; font-weight:600; text-transform:uppercase; letter-spacing:.02em; }}
.tl-title {{ font-weight:600; color:{TEXT}; }} .tl-detail {{ color:{MUTED}; font-size:.85rem; }}
.dt {{ width:100%; border-collapse:collapse; font-size:.85rem; }}
.dt th {{ text-align:left; color:{MUTED}; font-weight:600; border-bottom:1px solid {BORDER}; padding:6px 8px; }}
.dt td {{ padding:8px; border-bottom:1px solid #F1F5F9; vertical-align:top; color:{TEXT}; }}
.rq {{ border:1px solid {BORDER}; border-radius:12px; padding:12px 14px; background:#fff; margin-bottom:10px; }}
.rq-head {{ display:flex; justify-content:space-between; color:{MUTED}; font-size:.8rem; margin-bottom:6px; }}
.rq-text {{ color:{TEXT}; font-style:italic; margin-bottom:8px; }}
.chip {{ display:inline-block; border:1px solid {BORDER}; border-radius:8px; padding:1px 8px; margin:2px 6px 2px 0;
        font-size:.8rem; background:#F9FAFB; }}
</style>""")

# ---------------- helpers ----------------
esc = html.escape
fmt_d = lambda d: "—" if pd.isna(d) else f"{d:%b} {d.day}"
fmt_dy = lambda d: "—" if pd.isna(d) else f"{d:%b} {d.day}, {d.year}"
plural = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"
nice = lambda sym: sym.replace("_", " ")


def pill(text, color="gray", dot=False):
    return f'<span class="pill pill-{color}">{"<span class=dot></span>" if dot else ""}{esc(str(text))}</span>'


def flag_pills(flags: str) -> str:
    out = []
    for f in [x for x in str(flags).split(";") if x]:
        label, color = FLAG_LABELS.get(f, ("LLM unavailable — regex fallback", "gray") if f.startswith("llm_failed")
                                       else (f.replace("_", " "), "gray"))
        out.append(pill(label, color))
    return "".join(dict.fromkeys(out))                        # de-duplicate, keep order


def source_pill(by) -> str:
    label, color = SOURCE.get(str(by), (str(by), "gray"))
    return pill(label, color, dot=True)


@st.cache_data(ttl=60, show_spinner=False)
def cached_llm_status():
    from parser import llm_status
    return llm_status()


def demo_banner():
    ok, status = cached_llm_status()
    if not ok:
        st.html(f'<div style="margin:0 0 8px">{pill("Demo mode", "amber", dot=True)}'
                f'<span class="muted">{esc(status)}. New reports are parsed by the regex fallback and '
                f'go to review. On a hosted demo, changes reset when the app restarts.</span></div>')


def review_action(kind, event_id, symptom):
    """Accept / Dismiss button callback: saves only this event's row + one review-log entry."""
    try:
        done = apply_action(kind, "data", event_id, st.session_state.get("reviewer", ""))
        st.session_state.review_msg = f"{done.capitalize()}ed {nice(symptom)} (event {event_id})." \
            if done != "dismiss" else f"Dismissed {nice(symptom)} (event {event_id})."
    except ValueError as err:
        st.session_state.review_err = str(err)


def undo_action(event_id):
    try:
        st.session_state.review_msg = undo_last("data", event_id, st.session_state.get("reviewer", ""))
    except ValueError as err:
        st.session_state.review_err = str(err)


# ---------------- load ----------------
if "upload_n" not in st.session_state:
    st.session_state.upload_n = 0          # bumping this clears the file uploader after an ingest

store = load()
ev, meds, vit, pts, reps, visits, procs = (
    store[k] for k in ["events", "medications", "vitals", "patients", "reports", "visits", "procedures"])
if ev.empty or pts.empty:
    st.warning("No parsed events yet. Run `python pipeline_runner.py` first, then reload.")
    st.stop()

ev["onset"] = pd.to_datetime(ev["onset"])
ev["flags"] = ev["flags"].fillna("").astype(str)
ev["needs_review"] = ev["needs_review"].astype(str).str.lower().eq("true")
ev = ensure_review_columns(ev)
ev_all = ev                                   # incl. dismissed: for the review history only
ev = ev[ev["review_status"] != "dismissed"]   # dismissed events never reach tiles, charts or the banner
reps["reported_at"] = pd.to_datetime(reps["reported_at"])
vit["measured_at"] = pd.to_datetime(vit["measured_at"])
for col in ["start_date", "end_date"]:
    meds[col] = pd.to_datetime(meds[col])
visits["visit_date"] = pd.to_datetime(visits["visit_date"])
procs["date"] = pd.to_datetime(procs["date"])
for col in ["diagnosis_date", "first_visit_date", "treatment_start", "dob"]:
    pts[col] = pd.to_datetime(pts[col])

# "Today" for synthetic data = the latest thing anyone recorded.
as_of = max(reps["reported_at"].max(), vit["measured_at"].max()).normalize()
reported = lambda df: df[df["severity"].isna() | (df["severity"] > 0)]   # drop explicit denials


def default_last_visit(pid):
    v = visits[(visits["patient_id"] == pid) & (visits["visit_date"] <= as_of)]
    return v["visit_date"].max() if len(v) else as_of - pd.Timedelta(days=14)


def summarize(pid, lv):
    """Everything the page shows for one patient and one 'since last visit' window."""
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
    for d in pd.date_range(lv.normalize(), as_of, freq="D"):
        run = 0 if d in have else run + 1
        s["gap"] = max(s["gap"], run)

    rising = {k: d for k, (_, d) in sev.items() if d is not None and d >= 1.0}
    s["rising"], s["falling"] = rising, {k: d for k, (_, d) in sev.items() if d is not None and d <= -0.5}
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


# Triage every patient with their own default last visit (sidebar list + home page)
triage = {}
for _, p in pts.iterrows():
    t = summarize(p["patient_id"], default_last_visit(p["patient_id"]))
    triage[p["patient_id"]] = {"name": p["name"], "status": t["status"], "reason": t["reason"],
                               "rank": ["alert", "watch", "stable"].index(t["status"]), "t": t}
order = sorted(triage, key=lambda k: (triage[k]["rank"], k))
labels = {k: f"{k} — {triage[k]['name']}" for k in order}


# ---------------- upload panel (Reports tab, or home page for new patients) ----------------
def upload_panel(pid):
    """One or many notes. Each is filed under the selected patient, or the patient in its header
    (new IDs become new patients). Duplicates are skipped; a bad file never stops the batch."""
    st.markdown("**Add patient documents**")
    st.caption("Synthetic data only — never upload real patient information. Only the text is kept, never the file.")
    if summary := st.session_state.pop("import_summary", None):
        df = pd.DataFrame(summary)
        counts = df["status"].value_counts().to_dict()
        (st.warning if counts.get("failed") else st.success)(
            ", ".join(f"{v} {k}" for k, v in counts.items()) + f" · {int(df['reports'].sum())} report(s), "
            f"{int(df['events'].sum())} event(s) added, all sent to review.", icon=":material/fact_check:")
        st.dataframe(df, hide_index=True, alt="Import summary",
                     column_config={"file": "File", "patient": "Patient", "status": "Result", "reports": "Reports",
                                    "events": "Events", "duplicates": "Skipped (duplicate)", "detail": "Detail"})
    uploads = st.file_uploader("Patient notes (.txt or .pdf; select several to import in bulk)", type=["txt", "pdf"],
                               accept_multiple_files=True, key=f"upload_{st.session_state.upload_n}") or []
    header = None
    if len(uploads) == 1:
        try:
            header = read_header(extract_text(uploads[0].name, uploads[0].getvalue()))
            target = pid or header["patient_id"]
            if not target:
                st.caption("No patient selected and no 'Patient: P###' line in the document.")
            elif target in triage:
                st.caption(f"Will be filed under **{target}**.")
            else:
                st.caption(f"Will be filed under **{target}**, a new patient "
                           f"(**{header['name'] or 'name missing'}**) created from the document header.")
        except ValueError as err:
            st.error(str(err))
    elif uploads:
        st.caption(f"{len(uploads)} files. Each is filed under "
                   + (f"**{pid}** (files naming another patient are refused)." if pid else
                      "the patient named in its header; unknown IDs become new patients."))
    doc_default = pd.Timestamp(header["date"]).date() if header and header["date"] else as_of.date()
    c1, c2 = st.columns([1, 1], vertical_alignment="bottom")
    doc_date = c1.date_input("Date for notes without a 'Date:' line", value=doc_default,
                             key=f"doc_date_{st.session_state.upload_n}_{len(uploads)}",
                             help="A 'Date:' line inside a note always wins. Notes with several dates become one report per date.")
    if c2.button(f"Import {plural(len(uploads), 'file')}" if uploads else "Import", disabled=not uploads,
                 width="stretch", type="primary", icon=":material/upload_file:"):
        bar = st.progress(0.0, text="Importing…")
        summary = import_files([(u.name, u.getvalue()) for u in uploads], folder="data", selected_patient=pid,
                               default_date=datetime.combine(doc_date, time(12, 0)),
                               progress=lambda n, total, row: bar.progress(n / total, text=f"{n}/{total} · {row['file']}"))
        st.session_state.import_summary = summary
        st.session_state.upload_n += 1
        added = [r for r in summary if r["status"] == "added"]
        if len(uploads) == 1 and added:
            st.session_state.select_patient = added[0]["patient"]
        st.rerun()


# ---------------- sidebar ----------------
if pending := st.session_state.pop("select_patient", None):   # set by an upload, applied before the widget exists
    st.session_state.patient = next((l for l in labels.values() if l.startswith(pending)), None)


def pick(label):
    st.session_state.patient = label


with st.sidebar:
    st.markdown("### :material/monitor_heart: SymptomLens")
    st.caption("Pre-visit view · oncology")
    if msg := st.session_state.pop("review_msg", None):
        st.toast(msg, icon=":material/task_alt:")
    if msg := st.session_state.pop("review_err", None):
        st.error(msg, icon=":material/error:")
    st.text_input("Reviewer name", key="reviewer", placeholder="e.g. Dr. Chen", icon=":material/badge:",
                  help="Saved with every Accept / Edit / Dismiss / Undo, so each decision is attributed.")
    if msg := st.session_state.pop("upload_msg", None):
        st.success(msg, icon=":material/check_circle:")

    query = st.text_input("Search patients", placeholder="Name or ID", icon=":material/search:",
                          label_visibility="collapsed").strip().lower()
    shown = [k for k in order if not query or query in labels[k].lower()]
    current = st.session_state.get("patient")
    options = [labels[k] for k in shown] + ([current] if current and current not in [labels[k] for k in shown] else [])
    choice = st.selectbox("Patient", options, index=None, placeholder="Choose a patient…", key="patient",
                          format_func=lambda l: f"{STATUS[triage[l.split(' — ')[0]]['status']][2]} {l}")
    pid = choice.split(" — ")[0] if choice else None

    st.caption("Patients by urgency")
    if not shown:
        st.caption("No patient matches that search.")
    show_all = len(shown) <= 12 or query or st.toggle(f"Show all {len(shown)} patients", key="show_all_pts")
    for k in (shown if show_all else shown[:12]):
        st.button(f"{STATUS[triage[k]['status']][2]} **{triage[k]['name']}** · {triage[k]['reason']}",
                  key=f"pick_{k}", on_click=pick, args=(labels[k],), type="tertiary", width="stretch",
                  help=f"{k} — {STATUS[triage[k]['status']][0]}")

    if pid:
        st.divider()
        last_visit = st.date_input("Last visit", value=default_last_visit(pid).date(), key=f"last_visit_{pid}",
                                   help="Defaults to the most recent visit on record; change it to compare another window.")

# ---------------- home page: no patient chosen ----------------
if not pid:
    st.title("Patients")
    st.caption(f"Sorted by urgency · as of {fmt_dy(as_of)} · synthetic data, not for clinical use")
    demo_banner()
    roster = []
    for k in order:
        p, t = pts.set_index("patient_id").loc[k], triage[k]["t"]
        roster.append({
            "Status": f"{STATUS[triage[k]['status']][2]} {STATUS[triage[k]['status']][0]}",
            "Patient": labels[k], "Why": triage[k]["reason"],
            "Weight": "—" if t["wt_pct"] is None else f"{t['wt_pct']:+.1%}", "To review": t["review"]})
    st.dataframe(pd.DataFrame(roster), hide_index=True, alt="All patients sorted by urgency",
                 column_config={"Status": st.column_config.TextColumn(width="small"),
                                "Patient": st.column_config.TextColumn(width="medium"),
                                "Why": st.column_config.TextColumn(width="medium"),
                                "Weight": st.column_config.TextColumn("Weight Δ", width="small",
                                                                      help="Weight change since treatment start"),
                                "To review": st.column_config.NumberColumn(width="small", help="Items needing review since last visit")})
    st.info("Choose a patient in the sidebar to open their pre-visit summary.", icon=":material/person_search:")

    with st.container(border=True, key="card_inbox"):
        inbox = ev_all[ev_all["review_status"] == "pending"].merge(
            reps[["report_id", "raw_text"]], on="report_id", how="left").merge(
            pts[["patient_id", "name"]], on="patient_id", how="left")
        st.markdown(f"**Review inbox** · {plural(len(inbox), 'pending item')} across all patients")
        if inbox.empty:
            st.caption("Nothing is waiting for review.")
        else:
            inbox["why"] = inbox["flags"].map(lambda f: ", ".join(dict.fromkeys(
                (FLAG_LABELS.get(x, ("LLM unavailable", "")) if x.startswith("llm_failed") else
                 FLAG_LABELS.get(x, (x.replace("_", " "), "")))[0] for x in str(f).split(";") if x)) or "Low confidence")
            f1, f2, f3 = st.columns([2, 2, 2], vertical_alignment="bottom")
            reasons = sorted({r for w in inbox["why"] for r in w.split(", ")})
            pick_reasons = f1.multiselect("Flag", reasons, placeholder="Any flag")
            pick_pts = f2.multiselect("Patient", sorted(inbox["patient_id"].unique()), placeholder="Any patient")
            d0, d1 = inbox["onset"].min().date(), inbox["onset"].max().date()
            span_in = f3.date_input("Dates", value=(d0, d1), min_value=d0, max_value=d1)
            view = inbox
            if pick_reasons:
                view = view[view["why"].map(lambda w: any(r in w for r in pick_reasons))]
            if pick_pts:
                view = view[view["patient_id"].isin(pick_pts)]
            if isinstance(span_in, (list, tuple)) and len(span_in) == 2:
                view = view[(view["onset"].dt.date >= span_in[0]) & (view["onset"].dt.date <= span_in[1])]
            view = view.sort_values("onset", ascending=False)
            sel = st.dataframe(view[["onset", "patient_id", "name", "symptom", "severity", "why", "raw_text"]],
                               hide_index=True, on_select="rerun", selection_mode="single-row",
                               key=f"inbox_table_{st.session_state.get('inbox_n', 0)}",   # fresh key = selection cleared
                               alt="Pending review items across all patients",
                               column_config={"onset": st.column_config.DateColumn("Date", format="MMM D"),
                                              "patient_id": "ID", "name": "Patient", "symptom": "Symptom",
                                              "severity": "Severity", "why": "Why flagged",
                                              "raw_text": st.column_config.TextColumn("Patient's words", width="large")})
            st.caption(f"Showing {len(view)} of {len(inbox)}. Select a row to open that patient's Reports tab.")
            rows = sel.selection.rows if sel is not None and hasattr(sel, "selection") else []
            if rows:
                st.session_state.select_patient = view.iloc[rows[0]]["patient_id"]
                st.session_state.inbox_n = st.session_state.get("inbox_n", 0) + 1   # so going back doesn't re-open it
                st.rerun()

    with st.container(border=True, key="card_llm_health"):
        ok, status = cached_llm_status()
        st.markdown("**Parser health** · " + ("LLM online" if ok else "LLM offline"))
        n = max(len(ev_all), 1)
        by = ev_all["extracted_by"].value_counts()
        flags_all = ev_all["flags"].fillna("").str.split(";").explode()
        flags_all = flags_all[flags_all != ""]
        failed = ev_all["flags"].fillna("").str.contains("llm_failed|partial_llm_output").mean()
        h = st.columns(4)
        h[0].metric("Parsed by LLM", f"{by.get('llm', 0) / n:.0%}", border=True)
        h[1].metric("Regex fallback", f"{by.get('rule', 0) / n:.0%}", border=True)
        h[2].metric("Manual / import", f"{(by.get('manual', 0) + by.get('import', 0)) / n:.0%}", border=True)
        h[3].metric("LLM failed or partial", f"{failed:.0%}", border=True,
                    help="Events whose report the LLM could not fully parse (validation or connection failure).")
        top = flags_all.map(lambda f: "LLM unavailable" if f.startswith("llm_failed")
                            else FLAG_LABELS.get(f, (f.replace("_", " "),))[0]).value_counts().head(5)
        models = ev_all.dropna(subset=["model"]).groupby(["model", "prompt_version"]).size()
        st.caption("Most common flags: " + (", ".join(f"{k} ({v})" for k, v in top.items()) or "none")
                   + (" · Models: " + ", ".join(f"{m} {v} ({c})" for (m, v), c in models.items()) if len(models) else "")
                   + f" · {status}")

    with st.container(border=True, key="card_upload_home"):
        upload_panel(None)
    st.stop()

# ---------------- patient page ----------------
lv = pd.Timestamp(last_visit)
S = summarize(pid, lv)
pt = pts.set_index("patient_id").loc[pid]
p_ev, recent, p_vit = S["p_ev"], S["recent"], S["p_vit"]
p_med = meds[meds["patient_id"] == pid].sort_values("start_date")
p_proc = procs[procs["patient_id"] == pid].sort_values("date")
p_visits = visits[visits["patient_id"] == pid].sort_values("visit_date")
p_reps = reps[reps["patient_id"] == pid]
status_label, status_color, _ = STATUS[S["status"]]

demo_banner()

# 1) header card
initials = "".join(w[0] for w in str(pt["name"]).split()[:2]).upper()
age = "—" if pd.isna(pt["dob"]) else (as_of.year - pt["dob"].year
                                       - ((as_of.month, as_of.day) < (pt["dob"].month, pt["dob"].day)))
if pd.isna(pt["treatment_start"]):
    tx = "Treatment not started"
else:
    day = (as_of - pt["treatment_start"]).days + 1
    tx = f"Treatment day {day} of {PLANNED_COURSE_DAYS}" if day > 0 else "Treatment starts " + fmt_d(pt["treatment_start"])
with st.container(border=True, key="card_header"):
    st.html(f"""<div class="hdr">
      <div class="avatar">{esc(initials)}</div>
      <div><div class="hdr-name">{esc(str(pt['name']))}</div>
        <div class="hdr-meta">{esc(pid)} · {age} y · {esc(str(pt['diagnosis']))} · {esc(tx)}</div></div>
      <div class="hdr-right">{pill(status_label + ' · ' + S['reason'], status_color, dot=True)}<br>
        {pill('Synthetic data — not for clinical use', 'gray')}</div></div>""")

# 2) 30-second summary
worse = [f"{nice(k)} up {d:.1f} (now {S['sev'][k][0]:.1f}/10)" for k, d in
         sorted(S["rising"].items(), key=lambda x: -x[1])]
if S["vomits"] > S["vomits_before"]:
    worse.append(f"vomiting {S['vomits']} episodes (was {S['vomits_before']})")
better = [f"{nice(k)} down {abs(d):.1f} (now {S['sev'][k][0]:.1f}/10)" for k, d in S["falling"].items()]
if S["vomits"] < S["vomits_before"]:
    better.append(f"vomiting {plural(S['vomits'], 'episode')} (was {S['vomits_before']})")
action = []
if S["wt_pct"] is not None and S["wt_pct"] < -0.05:
    action.append(f"weight down {abs(S['wt_pct']):.1%} since treatment start — check nutrition")
if S["conflicts"]:
    action.append(f"{plural(S['conflicts'], 'conflicting report')} to reconcile")
if S["gap"] >= 3:
    action.append(f"no check-in for {S['gap']} days")
if S["review"]:
    action.append(f"{plural(S['review'], 'item')} to review")
sentence = lambda items, empty: (items[0][0].upper() + items[0][1:] + ("; " + "; ".join(items[1:]) if items[1:] else "")
                                  + ".") if items else empty

with st.container(border=True, key="card_summary"):
    c1, c2 = st.columns([3, 1], vertical_alignment="top")
    c1.markdown(f"**30-second summary** · since last visit, {fmt_d(lv)} · {plural(S['n_new'], 'new report')}")
    st.html(f"""<ul class="summary">
      <li><b class="worse">Worse:</b> {esc(sentence(worse, 'Nothing got clearly worse since the last visit.'))}</li>
      <li><b class="better">Better:</b> {esc(sentence(better, 'Nothing clearly improved.'))}</li>
      <li><b class="action">Action:</b> {esc(sentence(action, 'No action items.'))}</li></ul>""")

    pdf_lines = [
        "# SymptomLens pre-visit summary",
        f"{pt['name']} ({pid}), age {age} - {pt['diagnosis']}",
        f"{tx} - Status: {status_label} ({S['reason']})",
        f"Since last visit {fmt_dy(lv)}, as of {fmt_dy(as_of)}",
        "SYNTHETIC DATA - NOT FOR CLINICAL USE",
        "# 30-second summary",
        "Worse: " + sentence(worse, "Nothing got clearly worse since the last visit."),
        "Better: " + sentence(better, "Nothing clearly improved."),
        "Action: " + sentence(action, "No action items."),
        "# Symptoms since last visit",
        *[f"{nice(k).capitalize()}: " + ("not reported" if pd.isna(n) else f"avg {n:.1f}/10"
          + ("" if d is None else f" ({d:+.1f} vs before)")) for k, (n, d) in S["sev"].items()],
        f"Vomiting: {plural(S['vomits'], 'episode')} (previous {S['span'].days} days: {S['vomits_before']})",
        *[f"{nice(k).capitalize()}: reported on {plural(n, 'day')}" for k, (n, _) in S["days"].items()],
        "Weight: " + ("no data" if S["wt_pct"] is None else f"{S['wt_pct']:+.1%} since treatment start"),
        "# Active medications",
        *([f"{m['drug']} {m['dose'] or ''} (since {fmt_dy(m['start_date'])})"
           for _, m in p_med.iterrows() if pd.isna(m["end_date"]) or m["end_date"] > as_of] or ["None recorded"]),
        "# Vague reports since last visit (patient's own words)",
        *([f"{fmt_d(r['onset'])}: {r['raw_text']}" for _, r in
           S["other_now"].merge(reps[["report_id", "raw_text"]], on="report_id").iterrows()] or ["None"]),
    ]
    c2.download_button("PDF", data=summary_pdf(pdf_lines), file_name=f"{pid}_previsit_{as_of:%Y%m%d}.pdf",
                       mime="application/pdf", icon=":material/download:", width="stretch",
                       help="One-page text summary of this view (synthetic data).")


# 3) KPI row with sparklines (last 21 days)
def spark(sym, how):
    s = p_ev[(p_ev["symptom"] == sym) & (p_ev["onset"] > as_of - pd.Timedelta(days=21))]
    if how == "severity":
        vals = s.dropna(subset=["severity"]).groupby(s["onset"].dt.normalize())["severity"].max()
        return vals.tolist() if len(vals) > 1 else None
    days = pd.date_range(as_of - pd.Timedelta(days=20), as_of, freq="D")
    s = reported(s)
    vals = (s.groupby(s["onset"].dt.normalize())["frequency"].sum() if how == "count"
            else s.groupby(s["onset"].dt.normalize()).size()).reindex(days, fill_value=0)
    return vals.astype(float).tolist()


kpi_cols = st.columns(3) + st.columns(3)     # 2 rows of 3: labels and deltas stay readable at 1280px
kpi = [c.container(border=True, key=f"card_kpi_{i}") for i, c in enumerate(kpi_cols)]   # white cards
for col, sym, label in zip(kpi[:3], ["nausea", "pain", "mouth_sores"], ["Nausea", "Pain", "Mouth sores"]):
    now, delta = S["sev"][sym]
    col.metric(label, "—" if pd.isna(now) else f"{now:.1f}", delta, delta_color="inverse",
               delta_description="vs before", chart_data=spark(sym, "severity"),
               help=f"Average severity (0–10) reported since {fmt_d(lv)}, compared with before it.")
sw_now, sw_prev = S["days"]["swallowing_difficulty"]
kpi[3].metric("Swallowing", f"{sw_now} d", sw_now - sw_prev, delta_color="inverse", delta_description="vs before",
              chart_data=spark("swallowing_difficulty", "days"), chart_type="bar",
              help=f"Days with swallowing difficulty reported since {fmt_d(lv)} vs the previous {S['span'].days} days.")
kpi[4].metric("Vomiting", S["vomits"], S["vomits"] - S["vomits_before"], delta_color="inverse",
              delta_description="vs before", chart_data=spark("vomiting", "count"), chart_type="bar",
              help=f"Vomiting episodes since {fmt_d(lv)} vs the previous {S['span'].days} days.")
fa_now, fa_prev = S["days"]["fatigue"]
kpi[5].metric("Fatigue", f"{fa_now} d", fa_now - fa_prev, delta_color="inverse", delta_description="vs before",
              chart_data=spark("fatigue", "days"), chart_type="bar",
              help=f"Days with fatigue reported since {fmt_d(lv)} vs the previous {S['span'].days} days.")

# 4) alert strip
alerts = []
if S["wt_pct"] is None:
    alerts.append(pill("No weight data", "gray", dot=True))
else:
    wcolor = "red" if S["wt_pct"] < -0.05 else "amber" if S["wt_pct"] < -0.03 else "green"
    alerts.append(pill(f"Weight {S['wt_pct']:+.1%} since start ({S['wt_change']:+.1f} kg since last visit)", wcolor, True))
alerts.append(pill(f"No check-in for {S['gap']} days", "amber", True) if S["gap"] >= 3
              else pill("Check-ins on track", "green", True))
alerts.append(pill(f"{plural(S['review'], 'item')} to review", "amber" if S["review"] else "green", True))
if S["conflicts"]:
    alerts.append(pill(plural(S["conflicts"], "same-day conflict"), "red", True))
if len(S["other_now"]):
    alerts.append(pill(plural(len(S["other_now"]), "vague report"), "gray", True))
st.html(f'<div style="margin:4px 0 8px">{"".join(alerts)}</div>')

if len(S["other_now"]):                       # vague reports: shown in the patient's own words, never coded
    with st.container(border=True, key="card_vague"):
        st.markdown(f"**Vague reports since {fmt_d(lv)}** · patient's own words, not coded to a symptom")
        st.dataframe(S["other_now"].merge(reps[["report_id", "raw_text"]], on="report_id")[["onset", "raw_text"]]
                     .sort_values("onset", ascending=False), hide_index=True, alt="Vague patient reports",
                     column_config={"onset": st.column_config.DateColumn("Date", format="MMM D"),
                                    "raw_text": st.column_config.TextColumn("Report", width="large")})

# 5) tabs
tab_over, tab_hist, tab_rep = st.tabs([":material/monitoring: Overview", ":material/history: History",
                                       f":material/description: Reports ({S['review']} to review)"])

with tab_over:
    full_history = st.toggle("Show full history", help="Zoom out to diagnosis, staging and first consult.")
    start = pt["treatment_start"] if not pd.isna(pt["treatment_start"]) else p_ev["onset"].min()
    x0 = (min(d for d in [pt["diagnosis_date"], p_proc["date"].min(), start] if not pd.isna(d)) if full_history
          else start - pd.Timedelta(days=3))
    x1 = as_of + pd.Timedelta(days=4)
    days = pd.date_range(min(x0, start), x1, freq="D")

    lanes = list(dict.fromkeys(p_med["drug"])) + ["Procedures", "Visits"]
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        row_heights=[0.5, 0.06 * len(lanes) + 0.05, 0.25])
    texts = p_ev.merge(reps[["report_id", "raw_text"]], on="report_id", how="left")
    any_sev = False
    for sym, color in SYMPTOM_COLORS.items():
        s = texts[(texts["symptom"] == sym) & texts["severity"].notna()].copy()
        if s.empty:
            continue
        any_sev = True
        s["day"] = s["onset"].dt.normalize()
        top = s.sort_values("severity").groupby("day").tail(1).set_index("day")   # the report behind each daily max
        daily = top.reindex(days)                                                # missing days -> gaps
        snippet = daily["raw_text"].fillna("").map(lambda t: (t[:110] + "…") if len(t) > 110 else t)
        fig.add_trace(go.Scatter(x=daily.index, y=daily["severity"], mode="lines+markers", connectgaps=False,
                                 name=nice(sym), line=dict(color=color, width=2), marker=dict(size=6, color=color),
                                 customdata=snippet,
                                 hovertemplate=f"<b>%{{x|%b %d}}</b> · {nice(sym)} %{{y:.0f}}/10"
                                               "<br><i>%{customdata}</i><extra></extra>"), row=1, col=1)

    for _, m in p_med.iterrows():                                                 # medication bands
        end = m["end_date"] if not pd.isna(m["end_date"]) else x1
        fig.add_trace(go.Scatter(x=[m["start_date"], end], y=[m["drug"]] * 2, mode="lines",
                                 line=dict(color="#99D5CF", width=12), showlegend=False,
                                 hovertemplate=f"<b>{esc(m['drug'])}</b> {esc(str(m['dose'] or ''))}<br>"
                                               f"{fmt_d(m['start_date'])} → "
                                               f"{'ongoing' if pd.isna(m['end_date']) else fmt_d(m['end_date'])}"
                                               f"<br>{esc(str(m['reason'] or ''))}<extra></extra>"), row=2, col=1)
    fig.add_trace(go.Scatter(x=p_visits["visit_date"], y=["Visits"] * len(p_visits), mode="markers",
                             marker=dict(symbol="circle", size=11, color="#334155", line=dict(color="#fff", width=1.5)),
                             name="visit", showlegend=False,
                             text=p_visits["visit_type"].str.capitalize() + " — " + p_visits["provider"].fillna(""),
                             customdata=p_visits["notes"].fillna(""),
                             hovertemplate="<b>%{x|%b %d}</b> · %{text}<br>%{customdata}<extra></extra>"), row=2, col=1)
    done = p_proc["status"].eq("completed")
    fig.add_trace(go.Scatter(x=p_proc["date"], y=["Procedures"] * len(p_proc), mode="markers",
                             marker=dict(symbol=["diamond" if d else "diamond-open" for d in done], size=12,
                                         color=TEAL, line=dict(color=TEAL, width=2)),
                             name="procedure", showlegend=False, text=p_proc["procedure"] + " (" + p_proc["status"] + ")",
                             hovertemplate="<b>%{x|%b %d}</b> · %{text}<extra></extra>"), row=2, col=1)

    if len(p_vit):
        fig.add_trace(go.Scatter(x=p_vit["measured_at"], y=p_vit["value"], mode="lines", name="weight",
                                 line=dict(color="#334155", width=2), showlegend=False,
                                 hovertemplate="<b>%{x|%b %d}</b> · %{y:.1f} kg<extra></extra>"), row=3, col=1)
        fig.add_hline(y=p_vit["value"].iloc[0] * 0.95, line_dash="dash", line_width=2, line_color=RED, row=3, col=1,
                      annotation_text="−5% alert", annotation_position="bottom right",
                      annotation_font_color=RED)

    fig.add_vrect(x0=lv.isoformat(), x1=x1.isoformat(), fillcolor=TEAL, opacity=0.06, line_width=0, layer="below")
    fig.add_vline(x=lv.isoformat(), line_color="#111827", line_width=1.5)
    fig.add_annotation(x=lv.isoformat(), y=1.0, xref="x", yref="paper", text="last visit", showarrow=False,
                       xanchor="left", yanchor="bottom", font=dict(color="#111827", size=11))
    fig.update_yaxes(range=[0, 10.5], title_text="Severity (0–10)", showgrid=True, gridcolor="#EEF2F6", row=1, col=1)
    fig.update_yaxes(categoryorder="array", categoryarray=lanes[::-1], row=2, col=1)
    fig.update_yaxes(title_text="Weight (kg)", showgrid=True, gridcolor="#EEF2F6", row=3, col=1)
    fig.update_xaxes(range=[x0, x1], showgrid=False)
    fig.update_layout(template="simple_white", height=420 + 22 * len(lanes), margin=dict(t=40, l=10, r=10, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.03, x=0), hovermode="closest",
                      font=dict(family="Inter, system-ui, sans-serif", color=TEXT),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    with st.container(border=True, key="card_chart"):
        if not any_sev:
            st.caption("No severity scores reported yet, so the trend lines are empty. Timeline and weight are still shown.")
        st.plotly_chart(fig, width="stretch", config={"displaylogo": False},
                        alt="Symptom severity over time with medications, visits, procedures and weight")
        st.caption("Shaded area = since last visit. Gaps = no report that day. Vomiting is a count, shown in its card. "
                   "Filled diamond = completed procedure, open = scheduled. Hover a point to read the patient's words.")

with tab_hist:
    left, right = st.columns([5, 7], gap="medium")
    with left, st.container(border=True, key="card_timeline"):
        st.markdown("**Timeline**")
        items = []
        if not pd.isna(pt["diagnosis_date"]):
            items.append((pt["diagnosis_date"], "Diagnosis", str(pt["diagnosis"]), RED))
        if not pd.isna(pt["first_visit_date"]) and not (p_visits["visit_date"] == pt["first_visit_date"]).any():
            items.append((pt["first_visit_date"], "First visit", "", "#334155"))
        for _, r in p_proc.iterrows():
            items.append((r["date"], r["procedure"], r["status"], TEAL if r["status"] == "completed" else AMBER))
        if not pd.isna(pt["treatment_start"]):
            items.append((pt["treatment_start"], "Treatment started", "Chemoradiation, day 1", GREEN))
        for _, r in p_visits.iterrows():
            items.append((r["visit_date"], f"{r['visit_type'].capitalize()} · {r['provider']}", r["notes"] or "", "#334155"))
        if items:
            body = "".join(
                f'<div class="tl-item" style="--c:{c}"><div class="tl-date">{fmt_dy(d)}'
                f'{" · upcoming" if d > as_of else ""}</div><div class="tl-title">{esc(str(t))}</div>'
                f'<div class="tl-detail">{esc(str(det))}</div></div>'
                for d, t, det, c in sorted(items, key=lambda x: x[0]))
            st.html(f'<div class="tl">{body}</div>')
        else:
            st.caption("No history recorded for this patient yet (e.g. added from an uploaded document).")
    with right:
        with st.container(border=True, key="card_meds"):
            st.markdown("**Medications**")
            if p_med.empty:
                st.caption("No medications recorded.")
            else:
                rows = "".join(
                    f"<tr><td><b>{esc(str(m['drug']))}</b><br><span class=muted>{esc(str(m['dose'] or ''))}</span></td>"
                    f"<td>{fmt_d(m['start_date'])} → {'—' if pd.isna(m['end_date']) else fmt_d(m['end_date'])}</td>"
                    f"<td>{pill('Active', 'green', True) if pd.isna(m['end_date']) or m['end_date'] > as_of else pill('Stopped', 'gray', True)}</td>"
                    f"<td>{esc(str(m['reason'] or '—'))}<br><span class=muted>{esc(str(m['prescribed_by'] or ''))}</span></td></tr>"
                    for _, m in p_med.iterrows())
                st.html(f'<table class="dt"><tr><th>Drug</th><th>Dates</th><th>Status</th><th>Reason · prescriber</th></tr>{rows}</table>')
        with st.container(border=True, key="card_procs"):
            st.markdown("**Procedures**")
            if p_proc.empty:
                st.caption("No procedures recorded.")
            else:
                scol = {"completed": "green", "scheduled": "amber", "cancelled": "gray"}
                rows = "".join(f"<tr><td>{fmt_dy(r['date'])}</td><td>{esc(str(r['procedure']))}</td>"
                               f"<td>{pill(str(r['status']).capitalize(), scol.get(r['status'], 'gray'), True)}</td></tr>"
                               for _, r in p_proc.iterrows())
                st.html(f'<table class="dt"><tr><th>Date</th><th>Procedure</th><th>Status</th></tr>{rows}</table>')

    with st.expander("Table view: sort, search, download CSV", icon=":material/table:"):
        st.markdown("**Visits**")
        st.dataframe(p_visits.sort_values("visit_date", ascending=False)[["visit_date", "visit_type", "provider", "notes"]],
                     hide_index=True, alt="Visits table",
                     column_config={"visit_date": st.column_config.DateColumn("Date", format="MMM D, YYYY"),
                                    "visit_type": "Type", "provider": "Provider",
                                    "notes": st.column_config.TextColumn("Notes", width="large")})
        st.markdown("**Medications**")
        med_tbl = p_med.assign(status=p_med["end_date"].apply(
            lambda e: "Active" if pd.isna(e) or e > as_of else "Stopped"))
        st.dataframe(med_tbl[["drug", "dose", "start_date", "end_date", "status", "prescribed_by", "reason"]],
                     hide_index=True, alt="Medications table",
                     column_config={"drug": "Drug", "dose": "Dose", "status": "Status",
                                    "start_date": st.column_config.DateColumn("Start", format="MMM D"),
                                    "end_date": st.column_config.DateColumn("End", format="MMM D"),
                                    "prescribed_by": "Prescribed by", "reason": "Reason"})
        st.markdown("**Procedures**")
        st.dataframe(p_proc[["date", "procedure", "status"]], hide_index=True, alt="Procedures table",
                     column_config={"date": st.column_config.DateColumn("Date", format="MMM D, YYYY"),
                                    "procedure": "Procedure", "status": "Status"})

with tab_rep:
    with st.container(border=True, key="card_review"):
        st.markdown(f"**Review queue** · since {fmt_d(lv)}")
        q = recent[recent["review_status"].eq("pending")].merge(
            reps[["report_id", "reported_at", "source", "raw_text"]], on="report_id", how="left")
        if q.empty:
            st.caption("Nothing is waiting for review since the last visit.")
        else:
            st.caption(f"{plural(q['report_id'].nunique(), 'report')} · {plural(len(q), 'item')} pending. "
                       "The quote is the patient's own words and is never changed; act on what the parser extracted.")
            with st.container(height=560, border=False):
                for rid, g in q.sort_values("onset", ascending=False).groupby("report_id", sort=False):
                    first = g.iloc[0]
                    when = first["reported_at"] if not pd.isna(first["reported_at"]) else first["onset"]
                    with st.container(border=True, key=f"card_rq_{rid}"):
                        st.html(f'<div class="rq-head"><span>{fmt_d(when)} · {esc(str(first["source"] or "import"))}'
                                f'</span><span>#{esc(str(rid))}</span></div><div class="rq-text">'
                                f'“{esc(str(first["raw_text"] or "(structured import, no text)"))}”</div>'
                                f'<div>{flag_pills(";".join(g["flags"])) or pill("Low confidence", "amber")}</div>')
                        for _, r in g.iterrows():
                            eid = r["event_id"]
                            c_txt, c_acc, c_edit, c_dis = st.columns([5, 1.3, 1.2, 1.4], vertical_alignment="center")
                            c_txt.html(f'<span class="chip">{esc(nice(r["symptom"]).capitalize())}'
                                       + ("" if pd.isna(r["severity"]) else f' · {r["severity"]:.0f}/10')
                                       + ("" if pd.isna(r["frequency"]) else f' · ×{int(r["frequency"])}')
                                       + f' · conf {r["confidence"]:.1f}</span>' + source_pill(r["extracted_by"]))
                            c_acc.button("Accept", key=f"acc_{eid}", icon=":material/check:", width="stretch",
                                         on_click=review_action, args=("accept", eid, r["symptom"]))
                            with c_edit.popover("Edit", icon=":material/edit:", width="stretch"):
                                with st.form(f"edit_{eid}", border=False):
                                    sym = st.selectbox("Symptom", SYMPTOMS, index=SYMPTOMS.index(r["symptom"]),
                                                       format_func=lambda s: nice(s).capitalize())
                                    sev = st.number_input("Severity 0–10 (empty = not stated)", step=1,
                                                          value=None if pd.isna(r["severity"]) else int(r["severity"]))
                                    freq = st.number_input("Episodes (empty = not stated)", step=1,
                                                           value=None if pd.isna(r["frequency"]) else int(r["frequency"]))
                                    if st.form_submit_button("Save correction", type="primary", width="stretch"):
                                        try:
                                            apply_action("edit", "data", eid, st.session_state.get("reviewer", ""),
                                                         symptom=sym, severity=sev, frequency=freq)
                                            st.session_state.review_msg = f"Saved correction to {nice(sym)} (event {eid})."
                                            st.rerun()
                                        except ValueError as err:
                                            st.error(str(err))
                            c_dis.button("Dismiss", key=f"dis_{eid}", icon=":material/close:", width="stretch",
                                         on_click=review_action, args=("dismiss", eid, r["symptom"]))

        done = ev_all[(ev_all["patient_id"] == pid) & ev_all["review_status"].isin(["accepted", "edited", "dismissed"])]
        corrections = load_corrections()
        with st.expander(f"Reviewed items ({len(done)}) and corrections log", icon=":material/fact_check:"):
            if done.empty:
                st.caption("Nothing reviewed yet for this patient.")
            else:
                st.markdown("**Recent decisions** · Undo reverts the last action on an item and logs it")
                for _, d in done.sort_values("reviewed_at", ascending=False).head(8).iterrows():
                    c_d, c_u = st.columns([5, 1], vertical_alignment="center")
                    c_d.caption(f"{esc(str(d['reviewed_at'])[:16].replace('T', ' '))} · **{d['review_status']}** · "
                                f"{nice(d['symptom'])}" + ("" if pd.isna(d['severity']) else f" {d['severity']:.0f}/10")
                                + (f" · by {d['reviewed_by']}" if isinstance(d.get('reviewed_by'), str) else "")
                                + f" · event {d['event_id']}")
                    c_u.button("Undo", key=f"undo_{d['event_id']}", icon=":material/undo:", width="stretch",
                               on_click=undo_action, args=(d["event_id"],))
                st.dataframe(done.merge(reps[["report_id", "raw_text"]], on="report_id", how="left")
                             .sort_values("reviewed_at", ascending=False)[
                                 ["reviewed_at", "reviewed_by", "review_status", "symptom", "severity", "frequency", "raw_text"]],
                             hide_index=True, alt="Reviewed items",
                             column_config={"reviewed_at": "Reviewed", "reviewed_by": "By", "review_status": "Decision",
                                            "symptom": "Symptom", "severity": "Severity", "frequency": "Count",
                                            "raw_text": st.column_config.TextColumn("Patient's words", width="large")})
                mine = corrections[corrections["event_id"].astype(str).isin(done["event_id"].astype(str))]
                if len(mine):
                    st.markdown("**Field changes**")
                    st.dataframe(mine.sort_values("corrected_at", ascending=False), hide_index=True,
                                 alt="Corrections log", column_config={
                                     "event_id": "Event", "field": "Field", "old_value": "Was",
                                     "new_value": "Now", "corrected_at": "When", "reviewed_by": "By"})
        c1, c2 = st.columns([3, 2], vertical_alignment="center")
        c1.caption(f"{plural(int(ev_all['review_status'].isin(['accepted', 'edited', 'dismissed']).sum()), 'decision')} "
                   f"across all patients · {plural(len(corrections), 'field correction')}. Fully reviewed reports "
                   "can become new test labels for evaluate.py.")
        if c2.button("Export corrections as gold labels", icon=":material/output:", width="stretch"):
            n = export_gold(load(), "gold_corrections.json")
            st.success(f"Wrote {plural(n, 'fully reviewed report')} to gold_corrections.json. "
                       "Run `python evaluate.py` to score the parser on them.")
        with st.expander("Table view of pending items: sort, search, download CSV", icon=":material/table:"):
            st.dataframe(q[["onset", "symptom", "severity", "frequency", "confidence", "extracted_by",
                            "source", "flags", "raw_text"]], hide_index=True, alt="Review queue table",
                         column_config={"onset": st.column_config.DateColumn("Date", format="MMM D"),
                                        "symptom": "Symptom", "severity": "Severity", "frequency": "Count",
                                        "confidence": st.column_config.NumberColumn("Confidence", format="%.1f"),
                                        "extracted_by": "Parser", "source": "Source", "flags": "Flags",
                                        "raw_text": st.column_config.TextColumn("Patient's words", width="large")})

    with st.container(border=True, key="card_raw"):
        st.markdown("**All reports**")
        if p_reps.empty:
            st.caption("No reports from this patient yet.")
        else:
            st.dataframe(p_reps.sort_values("reported_at", ascending=False)[["reported_at", "source", "raw_text"]],
                         hide_index=True, alt="All raw patient reports",
                         column_config={"reported_at": st.column_config.DatetimeColumn("Date", format="MMM D, h:mm a"),
                                        "source": st.column_config.TextColumn("Source"),
                                        "raw_text": st.column_config.TextColumn("Patient's words", width="large")})

    with st.container(border=True, key="card_upload"):
        upload_panel(pid)
