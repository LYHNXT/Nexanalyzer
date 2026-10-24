"""
The split table's two figures: columns against run order, and one column
against another.

The trend chart is the interactive counterpart of the SPLITTABLE scripts'
``plot_*_om_coverage_trend.py`` PNGs, keeping the cues that change how a point
is read and leaving out the knob strip, which is tied to the OM columns:

- growth runs are the points; every other run -- a cleaning, an anneal -- is a
  vertical line at the place it ran, because what it changed is the chamber,
  not a number on the wafer's axis;
- a hollow marker on an ``OM_`` column is a run whose cut sat inside the frame
  noise (``OM_Discriminable`` false);
- an error bar is the frame-to-frame std the table carries for that column,
  and a column with none has none.

No spec or target line is drawn. The scripts draw 95 % on the coverage chart,
but that is not one of the customer spec lines this app draws (CLAUDE.md,
"Spec lines are drawn and never evaluated, and there are only three").

Pure Plotly: figures out, nothing rendered.
"""

from typing import Sequence

import pandas as pd
import plotly.graph_objects as go

from ..processing.table import (
    is_growth,
    non_growth_kind,
    not_discriminable,
    series_of,
    std_column,
)

#: One colour per series (one column) or per column (several), as on the
#: Datalog page's comparison mode.
TRACE_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd",
    "#8c564b", "#e377c2", "#17becf", "#bcbd22",
]

#: Non-growth runs by kind; CLEANING is the scripts' own ink.
NON_GROWTH_COLORS = {"cleaning": "#7c3aed", "anneal": "#e8a33d", "other": "#9ca3af"}

#: Run order on the x axis: the workbook's own row order, 1-based.
X_RUN_ORDER = "Run order"
#: Wall-clock on the x axis, for rows that recorded when they ran.
X_PROCESS_START = "Process start"

#: Past this many points the WaferID tick labels overlap into a smear, and the
#: axis falls back to run numbers; hover still names every point.
MAX_WAFER_TICKS = 60

FIGURE_HEIGHT = 520


def x_values(data: pd.DataFrame, x_mode: str) -> pd.Series:
    """Each row's x: its 1-based workbook position, or its Process_Start."""
    if x_mode == X_PROCESS_START and "Process_Start" in data.columns:
        return pd.to_datetime(data["Process_Start"], errors="coerce")
    return pd.Series(data.index + 1, index=data.index)


def build_trend_figure(
    data: pd.DataFrame,
    columns: Sequence[str],
    x_mode: str = X_RUN_ORDER,
    mark_non_growth: bool = True,
) -> go.Figure:
    """`columns` against run order for the growth rows of `data`.

    One column is coloured by series, so a series change reads as a colour
    change; several are coloured by column, since a series colour would then
    mean two things.
    """
    x = x_values(data, x_mode)
    growth = data[is_growth(data) & x.notna()]
    wafer = growth.get("WaferID", pd.Series("", index=growth.index)).astype("string").fillna("")
    run_type = growth.get("Run_Type", pd.Series("", index=growth.index)).astype("string").fillna("")

    fig = go.Figure()
    if len(columns) == 1:
        by_series = growth.groupby(series_of(growth), sort=True).groups
        traces = [(str(name), columns[0], idx) for name, idx in by_series.items()]
    else:
        traces = [(col, col, growth.index) for col in columns]
    for n, (name, col, idx) in enumerate(traces):
        rows = growth.loc[idx]
        values = pd.to_numeric(rows[col], errors="coerce")
        keep = values.notna()
        if not keep.any():
            continue
        rows, values = rows[keep], values[keep]
        std = std_column(data.columns, col)
        hollow = not_discriminable(rows, col)
        color = TRACE_COLORS[n % len(TRACE_COLORS)]
        fig.add_trace(go.Scatter(
            x=x.loc[rows.index], y=values, name=name, mode="lines+markers",
            line=dict(color=color, width=1.5),
            marker=dict(
                color=color, size=8, line=dict(color=color, width=1.5),
                symbol=["circle-open" if h else "circle" for h in hollow],
            ),
            error_y=(
                dict(type="data", array=pd.to_numeric(rows[std], errors="coerce"),
                     visible=True, color=color, thickness=1)
                if std else None
            ),
            customdata=list(zip(wafer.loc[rows.index], run_type.loc[rows.index])),
            hovertemplate=(
                "<b>%{customdata[0]}</b> (%{customdata[1]})<br>"
                f"{col} = %{{y}}<extra>{name}</extra>"
            ),
        ))

    if mark_non_growth:
        _add_non_growth_lines(fig, data[~is_growth(data) & x.notna()], x)

    xaxis = dict(title=X_PROCESS_START if x_mode == X_PROCESS_START else "Run # (workbook order)")
    if x_mode != X_PROCESS_START and 0 < len(growth) <= MAX_WAFER_TICKS:
        xaxis.update(tickmode="array", tickvals=list(x.loc[growth.index]),
                     ticktext=list(wafer), tickangle=-60)
    fig.update_layout(
        height=FIGURE_HEIGHT, xaxis=xaxis,
        yaxis=dict(title=columns[0] if len(columns) == 1 else "value"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(t=60, b=40, l=60, r=20), hovermode="closest",
    )
    return fig


def _add_non_growth_lines(fig: go.Figure, rows: pd.DataFrame, x: pd.Series) -> None:
    """One vertical line per non-growth run, one legend entry per kind.

    Dotted where the run stopped early. The line is a layout shape, which has
    no hover; the legend entry says what the colour is, and the table names
    the run.

    The shapes go in with one layout update: `add_shape` revalidates every
    shape already on the figure, so VB's ~340 non-growth runs took 8 s one by one.
    """
    seen = set()
    shapes = []
    run_types = rows["Run_Type"] if "Run_Type" in rows.columns else pd.Series(None, index=rows.index)
    for i, run_type in run_types.items():
        kind = non_growth_kind(run_type)
        color = NON_GROWTH_COLORS[kind]
        stopped = "stopped" in str(run_type or "").lower()
        shapes.append(dict(
            type="line", xref="x", yref="paper", x0=x.loc[i], x1=x.loc[i], y0=0, y1=1,
            line=dict(color=color, width=1, dash="dot" if stopped else "solid"),
            opacity=0.6, layer="below",
        ))
        if kind not in seen:
            seen.add(kind)
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode="lines", name=kind,
                line=dict(color=color, width=1), hoverinfo="skip",
            ))
    fig.update_layout(shapes=shapes)


def build_scatter_figure(data: pd.DataFrame, x_col: str, y_col: str) -> go.Figure:
    """`y_col` against `x_col` for every row that has both, coloured by series."""
    xs = pd.to_numeric(data[x_col], errors="coerce")
    ys = pd.to_numeric(data[y_col], errors="coerce")
    rows = data[xs.notna() & ys.notna()]
    series = series_of(rows)
    wafer = rows.get("WaferID", pd.Series("", index=rows.index)).astype("string").fillna("")

    fig = go.Figure()
    for n, (name, idx) in enumerate(sorted(rows.groupby(series).groups.items())):
        color = TRACE_COLORS[n % len(TRACE_COLORS)]
        fig.add_trace(go.Scatter(
            x=xs.loc[idx], y=ys.loc[idx], name=str(name), mode="markers",
            marker=dict(color=color, size=9, line=dict(color="white", width=0.5)),
            customdata=wafer.loc[idx],
            hovertemplate=(
                f"<b>%{{customdata}}</b><br>{x_col} = %{{x}}<br>{y_col} = %{{y}}"
                f"<extra>{name}</extra>"
            ),
        ))
    fig.update_layout(
        height=FIGURE_HEIGHT,
        xaxis=dict(title=x_col), yaxis=dict(title=y_col),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(t=60, b=40, l=60, r=20), hovermode="closest",
    )
    return fig
