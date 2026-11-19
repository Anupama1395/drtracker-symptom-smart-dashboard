# ui/components.py — design tokens, CSS, small HTML helpers shared by every page.
import html

import pandas as pd
import streamlit as st

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
# Where an event came from: shown on every review card and in the parser-health panel
SOURCE = {"llm": ("LLM", "teal"), "rule": ("Regex fallback", "amber"), "manual": ("Manual edit", "green"),
          "import": ("FHIR import", "gray")}

esc = html.escape
fmt_d = lambda d: "—" if pd.isna(d) else f"{d:%b} {d.day}"
fmt_dy = lambda d: "—" if pd.isna(d) else f"{d:%b} {d.day}, {d.year}"
plural = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"
nice = lambda sym: sym.replace("_", " ")


def inject_css():
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


def pill(text, color="gray", dot=False):
    return f'<span class="pill pill-{color}">{"<span class=dot></span>" if dot else ""}{esc(str(text))}</span>'


def flag_label(f: str) -> tuple[str, str]:
    if f.startswith("llm_failed"):
        return "LLM unavailable — regex fallback", "gray"
    return FLAG_LABELS.get(f, (f.replace("_", " "), "gray"))


def flag_pills(flags: str) -> str:
    out = [pill(*flag_label(f)) for f in str(flags).split(";") if f]
    return "".join(dict.fromkeys(out))                        # de-duplicate, keep order


def flag_text(flags: str) -> str:
    return ", ".join(dict.fromkeys(flag_label(f)[0].replace(" — regex fallback", "")
                                   for f in str(flags).split(";") if f)) or "Low confidence"


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
