# ui/upload.py — add one or many patient notes (txt/pdf). Each file is filed under the selected
# patient, or the patient in its header; duplicates are skipped; a bad file never stops the batch.
from datetime import datetime, time

import pandas as pd
import streamlit as st

from ingest import extract_text, import_files, read_header
from ui.components import plural


def upload_panel(pid, known_ids, as_of):
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
            elif target in known_ids:
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
