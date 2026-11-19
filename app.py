# app.py — SymptomLens pre-visit view: "what changed since the last visit?"
# Entry point only: page setup, cached data, sidebar, routing. Pages live in ui/, logic in services/.
import streamlit as st

from services.data import db_version, load_data
from services.triage import default_last_visit, triage_all
from ui.components import STATUS, inject_css
from ui.home import render_home
from ui.patient import render_patient

st.set_page_config(page_title="SymptomLens — Pre-visit view", page_icon=":material/monitor_heart:", layout="wide")
inject_css()

if "upload_n" not in st.session_state:
    st.session_state.upload_n = 0          # bumping this clears the file uploader after an ingest


# Cached on the database version: any save (full, or a single review click) changes it, so the next
# rerun reloads; otherwise reruns skip re-reading the tables and re-triaging every patient.
@st.cache_data(show_spinner=False, max_entries=4)
def cached_data(version):
    return load_data()


@st.cache_data(show_spinner=False, max_entries=4)
def cached_triage(version):
    data = cached_data(version)
    return triage_all(data) if data else {}


version = db_version()
d = cached_data(version)
if d is None:
    st.warning("No parsed events yet. Run `python pipeline_runner.py` first, then reload.")
    st.stop()
triage = cached_triage(version)
order = sorted(triage, key=lambda k: (triage[k]["rank"], k))
labels = {k: f"{k} — {triage[k]['name']}" for k in order}

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
        last_visit = st.date_input("Last visit", value=default_last_visit(d, pid).date(), key=f"last_visit_{pid}",
                                   help="Defaults to the most recent visit on record; change it to compare another window.")

if not pid:
    render_home(d, triage, order, labels)
else:
    render_patient(d, pid, last_visit)
