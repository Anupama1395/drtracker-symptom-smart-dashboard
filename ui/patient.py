# ui/patient.py — one patient's pre-visit page: header, 30-second summary, KPIs, alerts, tabs.
import pandas as pd
import streamlit as st

from pdf_summary import summary_pdf
from services.data import Data
from services.triage import reported, summarize
from ui.charts import timeline_figure
from ui.components import (AMBER, GREEN, PLANNED_COURSE_DAYS, RED, STATUS, TEAL, demo_banner, esc,
                           fmt_d, fmt_dy, nice, pill, plural)
from ui.review import review_queue
from ui.upload import upload_panel


def render_patient(d: Data, pid: str, last_visit):
    ev_all, meds, pts, reps, visits, procs, as_of = (
        d.ev_all, d.meds, d.pts, d.reps, d.visits, d.procs, d.as_of)
    lv = pd.Timestamp(last_visit)
    S = summarize(d, pid, lv)
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
        fig, any_sev = timeline_figure(pt, p_ev, reps, p_med, p_proc, p_visits, p_vit, lv, as_of, full_history)
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
        review_queue(pid, recent, ev_all, reps, lv)

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
            upload_panel(pid, set(d.pts["patient_id"]), as_of)
