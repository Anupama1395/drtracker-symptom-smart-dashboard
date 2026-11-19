# ui/home.py — no patient selected: patients by urgency, review inbox, parser health, bulk upload.
import pandas as pd
import streamlit as st

from services.data import Data
from ui.components import STATUS, cached_llm_status, demo_banner, flag_text, fmt_dy, plural
from ui.upload import upload_panel


def render_home(d: Data, triage: dict, order: list, labels: dict):
    ev_all, reps, pts, as_of = d.ev_all, d.reps, d.pts, d.as_of
    st.title("Patients")
    st.caption(f"Sorted by urgency · as of {fmt_dy(as_of)} · synthetic data, not for clinical use")
    demo_banner()
    roster = []
    for k in order:
        t = triage[k]
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
            inbox["why"] = inbox["flags"].map(flag_text)
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
        top = flags_all.map(flag_text).value_counts().head(5)
        models = ev_all.dropna(subset=["model"]).groupby(["model", "prompt_version"]).size()
        st.caption("Most common flags: " + (", ".join(f"{k} ({v})" for k, v in top.items()) or "none")
                   + (" · Models: " + ", ".join(f"{m} {v} ({c})" for (m, v), c in models.items()) if len(models) else "")
                   + f" · {status}")

    with st.container(border=True, key="card_upload_home"):
        upload_panel(None, set(triage), as_of)
