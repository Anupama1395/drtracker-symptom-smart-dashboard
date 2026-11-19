# ui/charts.py — the patient timeline: symptom severity, medications/visits/procedures lane, weight.
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ui.components import RED, SYMPTOM_COLORS, TEAL, TEXT, esc, fmt_d, nice


def timeline_figure(pt, p_ev, reps, p_med, p_proc, p_visits, p_vit, lv, as_of, full_history=False):
    """Returns (figure, any_severity). Missing days are gaps, never joined lines."""
    start = pt["treatment_start"] if not pd.isna(pt["treatment_start"]) else p_ev["onset"].min()
    if pd.isna(start):
        start = as_of - pd.Timedelta(days=28)
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
    return fig, any_sev
