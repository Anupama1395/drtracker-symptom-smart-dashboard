# ui/review.py — the review queue (Accept / Edit / Dismiss / Undo) on a patient's Reports tab.
import pandas as pd
import streamlit as st

from review import apply_action, export_gold, undo_last
from schema import SYMPTOMS, load, load_corrections
from ui.components import esc, flag_pills, fmt_d, nice, pill, plural, source_pill

PAGE_SIZE = 15   # report cards per page; long queues are paginated


def review_action(kind, event_id, symptom):
    """Accept / Dismiss button callback: saves only this event's row + one review-log entry."""
    try:
        done = apply_action(kind, "data", event_id, st.session_state.get("reviewer", ""))
        st.session_state.review_msg = (f"Dismissed {nice(symptom)} (event {event_id})." if done == "dismiss"
                                       else f"{done.capitalize()}ed {nice(symptom)} (event {event_id}).")
    except ValueError as err:
        st.session_state.review_err = str(err)


def undo_action(event_id):
    try:
        st.session_state.review_msg = undo_last("data", event_id, st.session_state.get("reviewer", ""))
    except ValueError as err:
        st.session_state.review_err = str(err)


def review_queue(pid, recent, ev_all, reps, lv):
    with st.container(border=True, key="card_review"):
        st.markdown(f"**Review queue** · since {fmt_d(lv)}")
        q = recent[recent["review_status"].eq("pending")].merge(
            reps[["report_id", "reported_at", "source", "raw_text"]], on="report_id", how="left")
        if q.empty:
            st.caption("Nothing is waiting for review since the last visit.")
        else:
            st.caption(f"{plural(q['report_id'].nunique(), 'report')} · {plural(len(q), 'item')} pending. "
                       "The quote is the patient's own words and is never changed; act on what the parser extracted.")
            groups = list(q.sort_values("onset", ascending=False).groupby("report_id", sort=False))
            pages = max(1, -(-len(groups) // PAGE_SIZE))
            page = 1
            if pages > 1:
                page = st.number_input(f"Page (of {pages}, {PAGE_SIZE} reports each)", 1, pages, 1,
                                       key=f"rq_page_{pid}")
            with st.container(height=560, border=False):
                for rid, g in groups[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]:
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

