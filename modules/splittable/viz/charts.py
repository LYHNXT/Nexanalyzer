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
from plotly.subplots import make_subplots

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

    fig = go.Figure()
    _add_trend_traces(fig, data, growth, columns, x)

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


def _add_trend_traces(fig: go.Figure, data: pd.DataFrame, growth: pd.DataFrame,
                      columns: Sequence[str], x: pd.Series, row: int = None) -> None:
    """The growth points themselves, shared by both trend charts.

    `row` places them in a subplot, for the chart that carries a knob strip
    above them; None is the plain single-axes figure.
    """
    wafer = growth.get("WaferID", pd.Series("", index=growth.index)).astype("string").fillna("")
    run_type = growth.get("Run_Type", pd.Series("", index=growth.index)).astype("string").fillna("")
    where = dict(row=row, col=1) if row else {}
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
        ), **where)


def _add_non_growth_lines(fig: go.Figure, rows: pd.DataFrame, x: pd.Series,
                          yref: str = "paper") -> None:
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
            type="line", xref="x", yref=yref, x0=x.loc[i], x1=x.loc[i], y0=0, y1=1,
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


# ------------------------------------------------- the scripts' chart format
#: The knob strip's bands, light to dark by value rank -- the ramp the
#: SPLITTABLE PNGs use, so a run of the same setting reads as one block.
KNOB_RAMP = ["#eaf1fb", "#d6e4f7", "#c0d6f2", "#a8c8ee", "#8fb9e9", "#74a9e4", "#5798de"]

#: Offered first, where the table has them: what `plot_om_coverage_trend_range.py`
#: (HA) and `plot_vb_om_coverage_trend.py` (VB) draw above their plots.
PREFERRED_KNOBS = ("H2O_PC1_torr", "P_torr", "H2Se_on_sccm",
                   "Ar_in_sccm", "Ar_MFC6_sccm")

#: Plotly cannot measure text, and the chart is responsive, so a date or a
#: band value is placed on an estimate: this plotting width, which is what the
#: app's layout gives at a usual window size, and this label width, "10-09" at
#: the chart's font size rounded up. A label that would not fit its own span
#: is rotated (dates) or left off (band values) -- the same rule the PNGs
#: apply, which can measure their text and do not have to guess.
ASSUMED_PLOT_PX = 900
DAY_LABEL_PX = 46

#: One knob band's height, and the room under the axis for the wafer IDs plus
#: the date row beneath them.
KNOB_ROW_PX = 28
BOTTOM_MARGIN_DATED = 150
#: Where the date row and the rules between days sit, in paper units below the
#: plotting area. Tied to BOTTOM_MARGIN_DATED: the dates clear the rotated
#: wafer IDs, and the rules run from the axis to just under the dates.
DATE_ROW_PAPER = -0.17
RULE_TOP_PAPER = -0.012
RULE_BOTTOM_PAPER = -0.235


def _constant_runs(values: Sequence) -> list:
    """`values` as (first index, last index, value) for each run of equal ones."""
    runs, i = [], 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[j + 1] == values[i]:
            j += 1
        runs.append((i, j, values[i]))
        i = j + 1
    return runs


def _is_number(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _knob_colors(values: Sequence) -> dict:
    """A colour per distinct value, light to dark in the value's own order."""
    distinct = sorted({v for v in values if v not in (None, "")},
                      key=lambda v: (0, float(v)) if _is_number(v) else (1, str(v)))
    if not distinct:
        return {}
    last = len(KNOB_RAMP) - 1
    if len(distinct) == 1:
        return {distinct[0]: KNOB_RAMP[last // 2]}
    return {v: KNOB_RAMP[round(i * last / (len(distinct) - 1))]
            for i, v in enumerate(distinct)}


def _knob_text(value) -> str:
    """A band's value as the PNGs print it: no trailing .0 on a whole number."""
    if _is_number(value):
        return "{:g}".format(float(value))
    return str(value)


def build_trend_bar_figure(
    data: pd.DataFrame,
    columns: Sequence[str],
    knob_columns: Sequence[str] = (),
    mark_non_growth: bool = True,
) -> go.Figure:
    """`build_trend_figure` in the SPLITTABLE scripts' PNG format.

    Two things the plain chart leaves out, both about reading a point against
    what the chamber was doing:

    * **the knob strip** -- one band per chosen column above the plot, a block
      per run of wafers that shared a value, labelled once;
    * **the dated axis** -- under the wafer IDs, one date per day, horizontal
      and centred on the wafers that ran that day, with a rule between days.
      The x axis is run order, not time, so the gap between two neighbours can
      be an hour or three weeks, and only the date says which.

    Run order only: a strip of bands and a date row both describe consecutive
    runs, and on a wall-clock axis neither is a block. The plain chart keeps
    both x modes, and is unchanged.
    """
    x = x_values(data, X_RUN_ORDER)
    growth = data[is_growth(data) & x.notna()]
    wafer = growth.get("WaferID", pd.Series("", index=growth.index)).astype("string").fillna("")
    knobs = [c for c in knob_columns if c in data.columns]

    strip_px = KNOB_ROW_PX * len(knobs) + 12
    height = FIGURE_HEIGHT + (strip_px if knobs else 0)
    if knobs:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                            row_heights=[strip_px / height, 1 - strip_px / height])
        trend_row = 2
    else:
        fig = make_subplots(rows=1, cols=1)
        trend_row = 1

    _add_trend_traces(fig, data, growth, columns, x, row=trend_row)
    if not fig.data:
        return fig
    xref = fig.data[0].xaxis or "x"
    yref = "{} domain".format(fig.data[0].yaxis or "y")

    if knobs:
        _add_knob_strip(fig, growth, x, knobs)
    if mark_non_growth:
        _add_non_growth_lines(fig, data[~is_growth(data) & x.notna()], x, yref=yref)

    dated = 0 < len(growth) <= MAX_WAFER_TICKS
    if dated:
        fig.update_xaxes(tickmode="array", tickvals=list(x.loc[growth.index]),
                         ticktext=list(wafer), tickangle=-60, row=trend_row, col=1)
        _add_day_row(fig, growth, x, xref)
    # The date row is annotations, which the axis title does not know about,
    # so it needs to be pushed past them by hand.
    fig.update_xaxes(title="Run # (workbook order)",
                     title_standoff=34 if dated else 8, row=trend_row, col=1)
    fig.update_yaxes(title=columns[0] if len(columns) == 1 else "value",
                     row=trend_row, col=1)
    fig.update_layout(
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(t=60, b=BOTTOM_MARGIN_DATED if dated else 40, l=60, r=20),
        hovermode="closest",
    )
    return fig


def _add_knob_strip(fig: go.Figure, growth: pd.DataFrame, x: pd.Series,
                    knobs: Sequence[str]) -> None:
    """One band per knob column, a block per run of wafers sharing a value.

    The bands are shapes rather than traces: they carry nothing to hover for
    beyond the number printed on them, and a trace per block would put a few
    hundred legend entries on a VB table.
    """
    xs = list(x.loc[growth.index])
    per_tick = ASSUMED_PLOT_PX / max(1, len(xs))
    for row_index, column in enumerate(knobs):
        values = ["" if pd.isna(v) else v for v in growth[column]]
        colors = _knob_colors(values)
        top = len(knobs) - row_index
        for first, last, value in _constant_runs(values):
            if value == "":
                continue
            x0, x1 = xs[first] - 0.5, xs[last] + 0.5
            fig.add_shape(
                type="rect", x0=x0, x1=x1, y0=top - 0.86, y1=top - 0.14,
                fillcolor=colors.get(value, KNOB_RAMP[0]), line=dict(width=0),
                layer="below", row=1, col=1,
            )
            text = _knob_text(value)
            if len(text) * 7 <= (last - first + 1) * per_tick:
                fig.add_annotation(
                    x=0.5 * (x0 + x1), y=top - 0.5, text=text, showarrow=False,
                    font=dict(size=11, color="#1f2937"), row=1, col=1,
                )
    fig.update_yaxes(range=[0, len(knobs)], tickmode="array",
                     tickvals=[len(knobs) - i - 0.5 for i in range(len(knobs))],
                     ticktext=list(knobs), showgrid=False, zeroline=False,
                     row=1, col=1)
    fig.update_xaxes(showticklabels=False, showgrid=False, zeroline=False, row=1, col=1)


def _add_day_row(fig: go.Figure, growth: pd.DataFrame, x: pd.Series, xref: str) -> None:
    """One date per day under the wafer IDs, and a rule between days.

    The rules go under the axis only. The plot already carries the non-growth
    lines, and a second set of verticals through it would compete with them.
    """
    if "Process_Start" not in growth.columns:
        return
    stamps = pd.to_datetime(growth["Process_Start"], errors="coerce")
    days = ["" if pd.isna(s) else "{:%m-%d}".format(s) for s in stamps]
    if not any(days):
        return
    xs = list(x.loc[growth.index])
    per_tick = ASSUMED_PLOT_PX / max(1, len(xs))
    for first, last, day in _constant_runs(days):
        if not day:
            continue
        fig.add_annotation(
            x=0.5 * (xs[first] + xs[last]), y=DATE_ROW_PAPER, xref=xref, yref="paper",
            text=day, showarrow=False, font=dict(size=11, color="#4b5563"),
            # Rotated where the day's own wafers are too few to fit it, which
            # is the single-wafer day on a crowded range.
            textangle=0 if DAY_LABEL_PX <= (last - first + 1) * per_tick else -90,
        )
        if first:
            fig.add_shape(
                type="line", xref=xref, yref="paper",
                x0=xs[first] - 0.5, x1=xs[first] - 0.5,
                y0=RULE_TOP_PAPER, y1=RULE_BOTTOM_PAPER,
                line=dict(color="#d1d5db", width=1), layer="below",
            )
