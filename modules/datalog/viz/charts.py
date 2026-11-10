"""
The two datalog figures: one run's channels stacked, and several runs overlaid
on a common process t=0.

Both are stacked subplots with a shared x-axis, one panel per selected channel,
because that is how a process engineer reads a run — pressure against heater
against flow, at the same instant, with a zoom on one panel driving all of
them. A single panel with eight y-axes is the alternative and it is unreadable
at the fourth trace.

Pure Plotly: figures out, nothing rendered. The page hands them to
`core.viz.render.render_plot`, which is the only place in this app that calls
`st.plotly_chart`.
"""

import colorsys
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..processing.analysis import TIME_COLUMN

PV_COLOR = "#1f77b4"
"""The process value: what the tool actually did. Solid, and the same color in
both figures, because it is the only trace comparison mode draws."""

SV_COLOR = "#ff7f0e"
"""The setpoint: what the tool was told to do. Dashed, so PV and SV are
distinguishable in a stacked figure that carries no legend at all."""

VIOLATION_COLOR = "#d62728"
"""Out-of-tolerance samples, marked on the PV trace itself."""

#: Per-run colors for comparison mode, curated rather than taken whole from a
#: qualitative palette: `VIOLATION_COLOR`'s red and the gray of the t=0 line are
#: both left out, so no color on this page means two things.
RUN_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd",
    "#8c564b", "#e377c2", "#17becf", "#bcbd22",
]

LEGEND_ITEM_HEIGHT = 20
"""Pixels one run occupies in a per-row legend."""

LEGEND_PADDING = 40
"""Pixels of a row that the legend's own chrome takes, on top of its items."""


def plot_item_label(item: dict) -> str:
    """The one name a plot item answers to.

    It is the subplot title, the y-axis title **and** the string the page's
    channel multiselect matches on, which is why there is one function rather
    than a literal in each place: a label built two ways is a channel that can
    be selected and never appears.
    """
    return item["pair_name"] if item["kind"] == "pvsv" else item["channel"]


def _run_color(index: int) -> str:
    """A stable color for run `index`, distinct past the end of the palette.

    Beyond the eight curated colors it generates hues by the golden angle, so
    run 9 and run 10 are as far apart as the palette's own entries rather than
    starting the list again — two runs sharing a color in a comparison is the
    one failure this figure cannot survive.

    The ``index - len(RUN_COLORS)`` offset is load-bearing and was dropped once:
    without it the golden-angle walk is sampled eight steps in, so run 9 comes
    out pink where datalog_monitor draws it red. Both sequences are equally well
    spread — the offset is not about distinctness, it is about the same run list
    getting the same colors in both applications, which is what lets a chart
    from one be checked against a chart from the other. Run 9's hue of 0 is a
    red near `VIOLATION_COLOR`, and that is harmless here: only
    `build_comparison_figure` calls this, and that figure draws no violation
    markers at all.
    """
    if index < len(RUN_COLORS):
        return RUN_COLORS[index]
    hue = ((index - len(RUN_COLORS)) * 0.618033988749895) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 0.85)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def _vertical_spacing(n_rows: int) -> float:
    """Gap between stacked panels, as a fraction of figure height.

    Capped at 0.08 and shrunk for small stacks. Plotly's `vertical_spacing` is
    a fraction of the *whole* figure, so a constant that looks right for three
    panels leaves no room for the panels themselves at twelve.
    """
    return min(0.08, 1 / max(n_rows - 1, 1) * 0.5)


def build_single_run_figure(df: pd.DataFrame, plot_items: List[dict],
                            violation_masks: Dict[str, Tuple[pd.Series, pd.Series]],
                            heater_off: Optional[pd.Timestamp] = None) -> go.Figure:
    """
    One run: a panel per selected channel, against wall-clock time.

    A PV/SV panel draws three traces — the process value solid, its setpoint
    dashed, and a marker trace sitting on the PV line wherever that sample was
    out of tolerance. The markers are ``df[pv_col].where(mask)``: full-length x,
    NaN everywhere in tolerance, so the dots land **on** the offending part of
    the trace rather than in a separate strip that has to be read back against
    it.

    **No legend at all.** Every trace sets ``showlegend=False``, and the panels
    are identified by their subplot title and y-axis title instead. A legend
    would repeat the panel titles verbatim and, in a twelve-panel stack, would
    do it in a box floating beside whichever panel happened to be in view.
    Solid-versus-dashed carries PV versus SV, which is one convention to learn
    and the same one every controller front-end uses.

    Parameters
    ----------
    violation_masks : Dict[str, Tuple[pd.Series, pd.Series]]
        ``pair_name -> (mask, deviation_pct)`` from
        `analysis.compute_all_violations`. A pair absent from it, or one whose
        mask is all-False, simply draws no markers.
    heater_off : Optional[pd.Timestamp]
        When the run's runcard switched the main heater off
        (`analysis.runcard_heater_off`). The ``Heater`` panel's dashed setpoint
        **stops there**, because from that moment the logged ``Heater SV`` is
        not a setpoint: the controller holds the growth value to the last row
        while nothing drives the chamber. Drawing it on was the complaint this
        answers -- a dashed line at 910 °C beside a PV at 94 °C reads as a
        half-hour failure. The PV is still drawn to the end, so the cool-down
        itself stays visible; only the claim that something was commanding it
        goes away. Without a card the line is drawn whole, as before: no card
        means nothing better to say than what the controller logged.
    """
    n = len(plot_items)
    fig = make_subplots(
        rows=n, cols=1, shared_xaxes=True,
        subplot_titles=[plot_item_label(item) for item in plot_items],
        vertical_spacing=_vertical_spacing(n),
    )
    time = df[TIME_COLUMN]

    # Every line trace pins mode="lines". Plotly's default for an unset mode is
    # "lines+markers" below 20 points, so a short or aborted run -- the one a
    # process engineer opens this page to look at -- came out dotted while a
    # normal run did not, and the violation markers then read as just more dots.
    for row, item in enumerate(plot_items, start=1):
        if item["kind"] == "pvsv":
            pair_name = item["pair_name"]
            pv_col = item["pv_col"]
            sv_col = item["sv_col"]
            fig.add_trace(
                go.Scatter(x=time, y=df[pv_col], name=pv_col, legendgroup=pair_name,
                           mode="lines", line=dict(color=PV_COLOR), showlegend=False),
                row=row, col=1,
            )
            sv = df[sv_col]
            if heater_off is not None and pair_name == "Heater":
                # .where keeps the index and x alignment: the line simply ends.
                sv = sv.where(time < heater_off)
            fig.add_trace(
                go.Scatter(x=time, y=sv, name=sv_col, legendgroup=pair_name,
                           mode="lines", line=dict(color=SV_COLOR, dash="dash"),
                           showlegend=False),
                row=row, col=1,
            )
            if pair_name in violation_masks:
                mask, _deviation = violation_masks[pair_name]
                if mask.any():
                    fig.add_trace(
                        go.Scatter(x=time, y=df[pv_col].where(mask),
                                   name=f"{pair_name} out-of-tolerance", mode="markers",
                                   marker=dict(color=VIOLATION_COLOR, size=5),
                                   showlegend=False),
                        row=row, col=1,
                    )
        else:
            channel = item["channel"]
            fig.add_trace(
                go.Scatter(x=time, y=df[channel], name=channel,
                           mode="lines", line=dict(color=PV_COLOR), showlegend=False),
                row=row, col=1,
            )
        fig.update_yaxes(title_text=plot_item_label(item), row=row, col=1)

    fig.update_layout(
        height=max(220, 220 * n),
        hovermode="x unified",
        margin=dict(l=60, r=20, t=40, b=40),
    )
    # Only the bottom panel is labelled: the axis is shared, and repeating
    # "Time" under every panel spends a row of height on something already known.
    fig.update_xaxes(title_text="Time", row=n, col=1)
    return fig


def build_comparison_figure(runs: Sequence[Tuple[str, pd.DataFrame]],
                            plot_items: List[dict],
                            align_times: Sequence[Optional[pd.Timestamp]]) -> go.Figure:
    """
    Several runs, overlaid on a common process t=0.

    ``align_times[i]`` becomes t=0 for ``runs[i]`` — the timestamp the heater
    setpoint settled at its final value, per
    `analysis.find_final_plateau_start`. The x-axis is elapsed seconds from
    that instant, negative before it, so every run's growth phase starts at the
    same place and the dotted vertical line at 0 is the common reference.
    Overlaying on wall-clock start instead puts each run's growth at a different
    x, because runs spend different amounts of time in load, pump-down and
    purge, and then nothing lines up with anything.

    **Only the PV side is drawn**, and violation markers are not drawn at all:
    this figure answers "did these runs do the same thing?", and a second
    dashed trace per run plus red dots turns eight lines into twenty-four.
    Setpoints and tolerances belong to the single-run view.

    Color is per **run**, held constant down the whole stack, so one run reads
    as one color across every panel.

    Parameters
    ----------
    runs : Sequence[Tuple[str, pd.DataFrame]]
        ``(run_label, df)`` in the order they should be colored. The label is
        the trace name, the legend entry and the ``Run`` column of the tables
        below the figure — one string in three places, so a run cannot be
        called one thing on the chart and another in the table.
    """
    n = len(plot_items)
    spacing = _vertical_spacing(n)
    fig = make_subplots(
        rows=n, cols=1, shared_xaxes=True,
        subplot_titles=[plot_item_label(item) for item in plot_items],
        vertical_spacing=spacing,
    )

    for run_idx, ((run_label, df), align_time) in enumerate(zip(runs, align_times)):
        elapsed = (df[TIME_COLUMN] - align_time).dt.total_seconds()
        for row, item in enumerate(plot_items, start=1):
            column = item["pv_col"] if item["kind"] == "pvsv" else item["channel"]
            if column not in df.columns:
                continue  # this run's controller logged a different channel set
            # A separate legend per row rather than one shared legend at the
            # top, so the run key stays beside whichever panel is scrolled into
            # view in these tall stacks. Needs Plotly >= 5.22.
            legend_ref = "legend" if row == 1 else f"legend{row}"
            # mode="lines" for the same reason as the single-run figure: an
            # unset mode means "lines+markers" under 20 points, so one aborted
            # run in a comparison would be the only dotted line on the chart.
            fig.add_trace(
                go.Scatter(x=elapsed, y=df[column], name=run_label, mode="lines",
                           legendgroup=run_label, legend=legend_ref, showlegend=True,
                           line=dict(color=_run_color(run_idx))),
                row=row, col=1,
            )

    for row, item in enumerate(plot_items, start=1):
        fig.update_yaxes(title_text=plot_item_label(item), row=row, col=1)
        fig.add_vline(x=0, row=row, col=1, line_dash="dot", line_color="gray", opacity=0.6)
        legend_ref = "legend" if row == 1 else f"legend{row}"
        yaxis_key = "yaxis" if row == 1 else f"yaxis{row}"
        fig.update_layout(**{
            legend_ref: dict(
                y=fig.layout[yaxis_key].domain[1], yanchor="top",
                x=1.0, xanchor="left",
            )
        })

    # Rows grow taller as runs are added so a per-row legend never overflows its
    # panel. Each row's on-screen height is its domain fraction -- which shrinks
    # as vertical_spacing eats into it -- times the total figure height, so the
    # total is inflated by that same fraction for row_height to actually hold.
    row_height = max(220, LEGEND_PADDING + LEGEND_ITEM_HEIGHT * len(runs))
    usable_fraction = 1 - (n - 1) * spacing if n > 1 else 1
    fig.update_layout(
        height=(row_height * n) / usable_fraction,
        hovermode="x unified",
        margin=dict(l=60, r=150, t=40, b=40),
    )
    # Names what the x-axis is, because "seconds" on its own reads as run time.
    fig.update_xaxes(title_text="Time relative to Heater SV reaching setpoint (s)", row=n, col=1)
    return fig
