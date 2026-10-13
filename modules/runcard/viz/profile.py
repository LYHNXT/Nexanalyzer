"""
The runcard profile chart: a reconstructed recipe drawn as one Plotly figure.

Two stacked panels over one shared time axis. The top panel is the heater's
reconstructed temperature with its synthesized cooldown tail, the preheaters as
dashed lines, the growth window as a shaded band, and a marker wherever a
reactive gas opened or closed. The bottom panel is a gantt: one row per gas
line, then the throttle valve, the pressure controllers, the stage and the
inner/outer tube sums, each segment filled at one of its row's discrete levels
(dark at the row's maximum, light below it) as the reference draws them. Under
both, a summary block naming what was holding at the growth midpoint.

Pure — no Streamlit, no file access. `core.viz.render.render_plot()` is what
puts the figure on a page, and `core.io.export` is what turns it into a PNG.

This replaces a hand-written SVG renderer, and three of its decisions are
deliberately not carried over:

- **Hover adds to what is printed; it does not replace it.** This figure
  briefly moved the marker temperatures, the bar units and the preheater hold
  temperatures into hover alone, on the reasoning that Plotly can measure text
  where SVG cannot and so need not print what it might have to clip. That was
  wrong, and the way it was wrong is worth recording: a figure exported as a
  PNG for a report, or held up beside one of the reference's, has no hover at
  all, and every one of those numbers had simply vanished. They are printed
  again. Hover still carries a segment's true span, which is the one thing no
  label can show once a sliver has been widened to stay visible.
- **The fallback colour for an unrecognised gas is deterministic.** It was
  `FALLBACK_COLORS[hash(name) % 2]`, and Python salts string hashes per
  process, so the same recipe came out violet one morning and teal the next.
  `zlib.crc32` is stable across runs and machines.
- **The stage row is shaded like every other row.** The ancestor formatted spin
  values into strings (`"10 rpm"`) before drawing, which made its own
  `isinstance(val, float)` shading test fail, so the row always came out flat.
  Units live on the row here, so the values stay numeric.

**The RTV row is "RTV P", in Torr.** This app briefly drew it as "RTV" with no
unit, reasoning that `params[1]` of `RTV Pressure Ctrl` ranges over
{10, 60, 70, 90} while VBBE00's measured tube pressure during the controlled
segment is ~7.9 Torr — so the number looked like neither the gauge range nor
the achieved pressure, and labelling it a pressure looked like a guess. The
reference renderer answers it directly: the second column is the gauge range
and the third is *the chamber-pressure setpoint the recipe commands*. The
recipe asks for 60 Torr; the chamber settles somewhere else. A setpoint and a
measurement are allowed to differ, and this chart draws recipes.
"""

import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import plotly.graph_objects as go

from ..processing.growth_window import RuncardProfile, TracePoint, get_species
from ..processing.stats import (
    BAR_ROW_PX,
    BarRow,
    build_bar_rows,
    canvas_height,
    gantt_row_slots,
    gas_events,
    growth_mid_values,
    profile_stats,
)

#: Gas species -> (bar fill, outline / marker colour). The fill is the pale end
#: a bar is shaded toward; the outline is what a marker and a bar edge use.
#:
#: These are the *reference renderer's* hues, not the older ancestor's this app
#: first shipped. Each species keeps its family — H2Se is still pink, O2 still
#: green — so a grower reading these beside years of old plots still finds the
#: gas where they expect it; the values themselves are the current ones.
#:
#: Both spellings of every subscripted species are listed. A runcard is a
#: hand-typed CSV and "H₂Se" does occur; matching only the ASCII form would
#: silently drop one file's gas into the fallback palette.
GAS_COLORS: Dict[str, Tuple[str, str]] = {
    "Ar": ("#d3d1c7", "#888780"),
    "O2": ("#c0dd97", "#97c459"),
    "O₂": ("#c0dd97", "#97c459"),
    "H2": ("#f5c4b3", "#f0997b"),
    "H₂": ("#f5c4b3", "#f0997b"),
    "H2Se": ("#ed93b1", "#d4537e"),
    "H₂Se": ("#ed93b1", "#d4537e"),
    "H2S": ("#fac775", "#ef9f27"),
    "H₂S": ("#fac775", "#ef9f27"),
    "N2": ("#85b7eb", "#378add"),
    "N₂": ("#85b7eb", "#378add"),
}

#: For a species this app has never seen. Four, matching the reference's
#: fallback cycle, so up to four unknown gases in one recipe are told apart;
#: picked by a deterministic hash of the channel name, so the same recipe draws
#: the same way every time.
FALLBACK_GAS_COLORS: Tuple[Tuple[str, str], ...] = (
    ("#afa9ec", "#7f77dd"),
    ("#5dcaa5", "#1d9e75"),
    ("#fac775", "#ef9f27"),
    ("#85b7eb", "#378add"),
)

TEMP_COLOR = "#d85a30"
P1_COLOR = "#378add"
P2_COLOR = "#7f77dd"

#: The colour a combined P1/P2 trace takes when the two preheaters track
#: identically — teal, so a single line is not mistaken for P1 alone.
P1P2_COMBINED_COLOR = "#1d9e75"

#: Where along the run each preheater's inline label is anchored, as a fraction
#: of its trace. Two different fractions rather than one, because P1 and P2 are
#: often within a few degrees of each other and two labels at the same x would
#: overlap however they were positioned vertically. The first sits in the gap
#: after the growth annotation; the second is further right, past it.
_P1P2_LABEL_FRACTIONS = (0.66, 0.80)

#: `meta` tags identifying the two `markers+text` traces apart. They are
#: otherwise indistinguishable by shape, and a caller that wanted "the gas
#: markers" had to reach for `fig.data[n]` by position — which silently became
#: the wrong trace the moment a preheater label was added in front of it.
_GAS_MARKER_META = "gas-markers"
_PREHEAT_LABEL_META = "preheat-label"

GROWTH_FILL = "rgba(251,234,240,0.55)"
GROWTH_STROKE = "#d4537e"
RTV_COLOR = "#534ab7"
PC1_COLOR = "#1d9e75"
PC2_COLOR = "#7f77dd"
SPIN_COLOR = "#8b96a5"

#: Text drawn on a gas bar, per species: `(on the light fill, on the dark
#: fill)` — the 800/900 and 50 stops of the same ramp as `GAS_COLORS`, so a
#: number is always dark-on-pale or pale-on-dark and never grey-on-colour.
GAS_TEXT_COLORS: Dict[str, Tuple[str, str]] = {
    "Ar": ("#2c2c2a", "#f1efe8"),
    "O2": ("#173404", "#eaf3de"),
    "O₂": ("#173404", "#eaf3de"),
    "H2": ("#4a1b0c", "#faece7"),
    "H₂": ("#4a1b0c", "#faece7"),
    "H2Se": ("#4b1528", "#fbeaf0"),
    "H₂Se": ("#4b1528", "#fbeaf0"),
    "H2S": ("#412402", "#faeeda"),
    "H₂S": ("#412402", "#faeeda"),
    "N2": ("#042c53", "#e6f1fb"),
    "N₂": ("#042c53", "#e6f1fb"),
}

#: The same, for `FALLBACK_GAS_COLORS`, index for index.
FALLBACK_GAS_TEXT_COLORS: Tuple[Tuple[str, str], ...] = (
    ("#26215c", "#eeedfe"),
    ("#04342c", "#e1f5ee"),
    ("#412402", "#faeeda"),
    ("#042c53", "#e6f1fb"),
)


@dataclass(frozen=True)
class _Ramp:
    """The fills and text colours one gantt row draws its segments with.

    `mid` is `None` for a two-level row (gas, tube) and set for the pressure
    controllers, which the reference draws in three levels.
    """

    light: str
    dark: str
    dark_text: str
    light_text: str
    mid: Optional[str] = None


#: The pressure controllers' three-level ramps — light, mid, dark — from the
#: reference's `PC_CHANNEL_RAMPS`. PC-1 teal, PC-2 purple; `PC1_COLOR` and
#: `PC2_COLOR` are the mid stops.
PC1_RAMP = _Ramp("#5dcaa5", "#0f6e56", "#04342c", "#e1f5ee", mid=PC1_COLOR)
PC2_RAMP = _Ramp("#afa9ec", "#4a4396", "#26215c", "#eeedfe", mid=PC2_COLOR)

#: The throttle valve: one solid fill, and a darker one for "vac".
RTV_RAMP = _Ramp(RTV_COLOR, "#26215c", "#eeedfe", "#eeedfe")
RTV_LABEL_COLOR = "#3c3489"

#: The stage: one flat slate fill with dark text, as the reference draws it.
SPIN_RAMP = _Ramp(SPIN_COLOR, SPIN_COLOR, "#1f2937", "#1f2937")

#: The summed inner- and outer-tube rows: the reference's purple and teal.
#: Told apart by their label's first word, since both rows are kind `"tube"`.
INNER_TUBE_RAMP = _Ramp("#afa9ec", "#7f77dd", "#26215c", "#eeedfe")
OUTER_TUBE_RAMP = _Ramp("#5dcaa5", "#1d9e75", "#04342c", "#e1f5ee")

#: A segment at or above this fraction of its row's maximum takes the dark
#: fill (when the row holds more than one value); a pressure segment at or
#: above `_MID_FRACTION` takes the mid fill. The reference's own thresholds.
_DARK_FRACTION = 0.95
_MID_FRACTION = 0.5

#: A gas with no entry in `GAS_COLORS` still gets a marker, in grey rather than
#: in its row's fallback colour — a marker that borrowed the fallback hue would
#: claim a species identity the table does not actually have.
MARKER_FALLBACK_COLOR = "#666666"

_SUMMARY_BG = "#fff8f0"
_SUMMARY_HEADER_COLOR = "#a0526a"
_SUMMARY_BODY_COLOR = "#666666"

FONT_FAMILY = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"

#: The vertical budget, in pixels, top to bottom. It is fixed rather than
#: proportional so that a bar row is 24 px on a two-row recipe and on a
#: fourteen-row one; a proportional layout squeezes the gantt flat exactly when
#: the recipe has enough channels to be worth reading.
_MARGIN_TOP_PX = 90
_TEMP_PANEL_PX = 190
_PANEL_GAP_PX = 40
_TIME_AXIS_PX = 50
_MARGIN_BOTTOM_PX = 130
_MARGIN_LEFT_PX = 90
_MARGIN_RIGHT_PX = 110

#: The right margin holds the gantt's row labels, so it widens to fit the
#: longest one: ~6.5 px per character at the 11 px tick font, plus a gap.
#: "Outer tube (MFC5+6+7+8)" needs ~160 px, and at a fixed 110 it was clipped
#: mid-word in every figure.
_LABEL_CHAR_PX = 6.5
_LABEL_PAD_PX = 12

#: Gap between the time-axis title and the summary block below it. The block
#: runs to at most six lines, so it and this gap have to fit inside
#: `_MARGIN_BOTTOM_PX` — which is why that reserve is 130 px and not less.
_SUMMARY_TOP_GAP_PX = 12

#: Time-axis tick spacing in minutes, by how long the recipe runs: the
#: reference's own ladder. A fixed half-hour tick was used here for a while,
#: which gives a 25-minute recipe exactly one interior gridline and a
#: five-hour one a wall of them.
#:
#: The axis is in minutes even though every stored quantity is in seconds:
#: seconds on the axis meant a tick list and a label list built by hand, and
#: every number an operator quotes about a run is in minutes anyway.
_TIME_TICK_LADDER = ((30, 5), (60, 10), (150, 20), (300, 30))
_TIME_TICK_FALLBACK_MIN = 60


def time_tick_step(total_min: float) -> int:
    """Minutes between time-axis ticks for a recipe of `total_min`."""
    for limit, step in _TIME_TICK_LADDER:
        if total_min <= limit:
            return step
    return _TIME_TICK_FALLBACK_MIN

#: Gridlines at zero, at 300 °C, and at this run's own peak — and nothing else,
#: not even at the top of the scale. Three lines, one of which moves per
#: recipe, is what makes the peak readable off the axis without a ruler. They
#: are deliberately not de-duplicated: a recipe peaking near 300 °C draws two
#: labels on top of each other, which is honest about where its peak is.
_FIXED_GRIDLINES_C = (0, 300)

#: Headroom above the peak, and the floor the temperature scale never drops
#: below. The floor is what makes two recipes comparable at a glance — an
#: 850 °C run and a 900 °C run are drawn on the same scale — and the headroom
#: is what keeps the growth caption off the trace.
_TEMP_HEADROOM_C = 50
_TEMP_SCALE_FLOOR_C = 900

#: The narrowest a gantt segment is drawn, as a fraction of the whole recipe.
#:
#: Two commands on one channel with no `Wait` between them share a timestamp,
#: so the earlier one's segment starts and ends at the same second — an
#: instantaneous setpoint poke, which is common. A Plotly bar of zero width
#: draws nothing at all, so the operator reads a gap at exactly the moment the
#: recipe did something; the ancestor clamped every bar to one pixel and drew a
#: tick there.
#:
#: The clamp is in **data units** rather than pixels because Plotly has no
#: pixel-width bars and because a fraction of the recipe's own length is the
#: only minimum that survives a resize: a figure is drawn at whatever width the
#: browser gives it, and a fixed number of seconds is invisible on a
#: 130-minute card and a fat block on a five-minute one. 0.4 % is ~4 px of a
#: 1000 px plot area — a tick, not a bar — and it grows under zoom, so zooming
#: into one turns it into a sliver that can be read rather than into nothing.
#:
#: It is applied to every segment, not only to the exactly-zero ones: a
#: threshold that fired at 0 s and not at 0.1 s would leave the next-shortest
#: pokes just as invisible, with no rule an operator could learn. Only segments
#: already too narrow to read are widened, and `customdata` carries the true
#: start and end, so the hover box reports the instant and not the tick.
_MIN_BAR_SPAN_FRACTION = 0.004

#: A gas-chemistry line longer than this is split in half onto a hanging
#: continuation line. Characters, not pixels: the summary block is a
#: fixed-position annotation and a line that overruns it is clipped by the
#: figure edge rather than wrapped.
_SUMMARY_WRAP_CHARS = 90


def build_profile_figure(run_id: str, profile: RuncardProfile) -> go.Figure:
    """The whole chart for one reconstructed recipe.

    `run_id` is untrusted — it comes from a filename — and Plotly renders a
    subset of HTML in titles, annotations and hover text, so it is escaped
    before it reaches any of them. So is every channel name, for the same
    reason.

    The figure sizes itself from the number of gantt rows
    (`stats.profile_stats`'s `canvas_h`), so a recipe that addresses fourteen
    channels is taller than one that addresses four rather than drawing
    fourteen rows at half height. A recipe with no gantt rows at all still gets
    one row's worth of space, so the panel has somewhere to say it is empty.
    """
    stats = profile_stats(profile)
    # The run id picks which MFCs each tube row sums; it never changes the
    # row count, so `stats` above sized the figure correctly without it.
    rows = build_bar_rows(profile.timeline, run_id)
    timeline = profile.timeline
    growth = profile.growth

    # A zero-length recipe never reaches this function from the page —
    # `list_runcards` rejects it — but this is a public builder, and a zero-wide
    # x-axis is a Plotly error rather than an empty chart.
    total_min = timeline.total_time / 60
    x_max = total_min if total_min > 0 else 1.0

    peak_temp = growth.peak_temp_c
    temp_max = max(peak_temp + _TEMP_HEADROOM_C, _TEMP_SCALE_FLOOR_C)

    # Both from `stats`, not from a local `max(len(rows), 1)`: the page reads
    # the same height out of `profile_stats` and sizes the exported PNG with
    # it, and the two used to disagree by one row on a recipe with no gantt.
    row_count = gantt_row_slots(rows)
    height = canvas_height(rows)
    inner = height - _MARGIN_TOP_PX - _MARGIN_BOTTOM_PX
    bars_px = BAR_ROW_PX * row_count

    # Paper coordinates run bottom-up; the budget above is written top-down.
    temp_domain = (1.0 - _TEMP_PANEL_PX / inner, 1.0)
    bars_top_px = _TEMP_PANEL_PX + _PANEL_GAP_PX
    bars_domain = (1.0 - (bars_top_px + bars_px) / inner, 1.0 - bars_top_px / inner)

    fig = go.Figure()

    _add_growth_band(fig, growth, temp_max)
    _add_temperature_traces(fig, profile, stats)
    _add_gas_markers(fig, profile)
    _add_bar_rows(fig, rows, x_max)
    _add_row_labels(fig, rows)

    fig.update_layout(
        height=height,
        font=dict(family=FONT_FAMILY, size=12),
        margin=dict(
            l=_MARGIN_LEFT_PX, r=_right_margin(rows),
            t=_MARGIN_TOP_PX, b=_MARGIN_BOTTOM_PX,
        ),
        plot_bgcolor="white",
        paper_bgcolor="white",
        title=dict(
            text=(
                f"<b>{_escape(run_id)} — Recipe profile</b>"
                f"<br><span style='font-size:13px;color:#888888'>{total_min:.0f} min total</span>"
            ),
            # Anchored to the **plot** area's top edge and grown upward into
            # the margin. Anchored to the container instead, the two-line block
            # hangs off the top of the figure and the first line's ascenders are
            # clipped in every exported PNG.
            x=0.0, xanchor="left", yref="paper", y=1.0, yanchor="bottom",
            font=dict(size=20, color="#333333"),
        ),
        legend=dict(
            # In the gap between the two panels, which nothing else uses. Above
            # the temperature panel it would fight the growth caption, which is
            # centred over its own band and cannot be moved out of the way;
            # inside the panel it crosses the rising trace on any recipe that
            # reaches temperature early.
            orientation="h", x=0.0, xanchor="left", y=bars_domain[1], yanchor="bottom",
            font=dict(size=11), bgcolor="rgba(0,0,0,0)",
        ),
        hovermode="closest",
        # One bar trace per row. Plotly's default "group" mode gives every
        # trace its own slot inside each category, so with one trace per row
        # the bars drifted further off their row label the further down they
        # were — a row in eleven was drawn most of a row away from its name.
        barmode="overlay",
        xaxis=dict(
            # Anchored to the gantt axis so the time axis is drawn once, under
            # the bars, exactly where the ancestor put it.
            anchor="y2",
            domain=[0.0, 1.0],
            range=[0.0, x_max],
            tick0=0, dtick=time_tick_step(x_max),
            ticks="outside", ticklen=5,
            title=dict(text="Time (min)", font=dict(size=11, color="#888888")),
            tickfont=dict(size=10, color="#999999"),
            showgrid=False, zeroline=False,
            linecolor="#cccccc", linewidth=1,
        ),
        yaxis=dict(
            anchor="x",
            domain=list(temp_domain),
            range=[0.0, temp_max],
            tickvals=_gridline_values(peak_temp),
            title=dict(text="Temp (°C)", font=dict(size=11, color="#666666")),
            tickfont=dict(size=10, color="#999999"),
            gridcolor="#eeeeee", gridwidth=1,
            zeroline=False, showline=False,
        ),
        yaxis2=dict(
            anchor="x",
            domain=list(bars_domain),
            # Row labels in the right margin, where the ancestor put them, so
            # the left margin belongs to the temperature scale alone.
            side="right",
            categoryorder="array",
            categoryarray=[_escape(row.label) for row in reversed(rows)],
            # The names are drawn by `_add_row_labels` instead, each in its
            # row's own colour; an axis has one tick colour for every label.
            showticklabels=False,
            tickfont=dict(size=11, color="#555555"),
            showgrid=False, zeroline=False, showline=False,
        ),
    )

    if not rows:
        fig.add_annotation(
            xref="paper", yref="y2 domain", x=0.5, y=0.5,
            text="No gas, pressure or stage commands in this recipe",
            showarrow=False, font=dict(size=11, color="#999999"),
        )

    _add_summary_block(fig, profile, inner)
    return fig


# ------------------------------------------------------------------ layers
def _add_growth_band(fig: go.Figure, growth, temp_max: float) -> None:
    """The shaded window, its two dashed edges, and its caption.

    Spans the **whole** plot height rather than the temperature panel alone, so
    the band lands over the gantt too and it is visible at a glance which
    segments were holding during growth. That is the one thing the ancestor's
    fixed-height band could not show, and it is the question the gantt is there
    to answer.

    Drawn below every trace (`layer="below"`), so a 0.18-alpha wash never
    lightens a bar or the temperature line on top of it.

    Guarded on `start_s is not None`, not on truthiness: see `stats`.
    """
    if growth.start_s is None or growth.end_s is None:
        return

    start_min, end_min = growth.start_s / 60, growth.end_s / 60
    fig.add_vrect(
        x0=start_min, x1=end_min,
        fillcolor=GROWTH_FILL, line_width=0, layer="below",
    )
    for edge in (start_min, end_min):
        fig.add_vline(x=edge, line=dict(color=GROWTH_STROKE, width=1, dash="4px,3px"))

    # Above the panel rather than inside it. The scale leaves 50 °C of headroom
    # over the peak, which is about ten pixels — enough to keep a line off the
    # trace and not enough to keep a caption off it.
    fig.add_annotation(
        x=(start_min + end_min) / 2, y=temp_max,
        xref="x", yref="y", yanchor="bottom",
        text=f"<i>Growth ({growth.duration_min:.0f} min @ {growth.peak_temp_c:.0f}°C)</i>",
        showarrow=False, font=dict(size=12, color=GROWTH_STROKE),
    )


def _add_temperature_traces(fig: go.Figure, profile: RuncardProfile, stats: Dict[str, Optional[float]]) -> None:
    """Main heater, then the two preheaters, into the top panel.

    The cooldown tail is **not** a separate trace: it is the tail of
    `temp_trace`, drawn in the same colour and weight, because it is the same
    heater and splitting it would invite the reading that the app measured one
    half and modelled the other. It modelled all of it.

    The preheaters' final temperatures go in their **legend entries** rather
    than in end-of-line labels. The ancestor drew those labels in the right
    margin and had to offset P2's by 10 px whenever P1 was also present, to
    stop them overlapping; a legend has no such collision and says the same
    thing.
    """
    fig.add_trace(go.Scatter(
        x=[t / 60 for t, _temp in profile.temp_trace],
        y=[temp for _t, temp in profile.temp_trace],
        mode="lines", name="Heater",
        line=dict(color=TEMP_COLOR, width=2.5, shape="linear"),
        hovertemplate="%{x:.1f} min · %{y:.0f} °C<extra>Heater</extra>",
    ))

    if _tracks_identically(profile.p1_trace, profile.p2_trace):
        # One line, not two drawn on top of each other. Both preheaters held at
        # one temperature is the ordinary case for these recipes, and two
        # dashed traces at identical y render as a single line of uncertain
        # identity with two legend entries claiming it.
        entries = (
            (profile.p1_trace, P1P2_COMBINED_COLOR, "6px,4px", "P1/P2", stats["p1_T"]),
        )
    else:
        entries = (
            (profile.p1_trace, P1_COLOR, "6px,4px", "P1", stats["p1_T"]),
            (profile.p2_trace, P2_COLOR, "4px,6px", "P2", stats["p2_T"]),
        )

    for index, (trace, color, dash, label, final) in enumerate(entries):
        if not trace:
            continue
        name = f"{label} ({final:.0f} °C)" if final is not None else label

        # The hold temperature is also printed **on the line**, not only in the
        # legend. These traces are flat and close together, so a legend entry
        # makes the reader carry a colour across the figure to find out which
        # preheater is which and how hot it was; the reference labelled them
        # inline for that reason. Two entries are anchored at different
        # fractions of the run so their labels cannot collide.
        anchor_frac = _P1P2_LABEL_FRACTIONS[index % len(_P1P2_LABEL_FRACTIONS)]
        anchor_t = trace[-1][0] * anchor_frac
        fig.add_trace(go.Scatter(
            x=[anchor_t / 60], y=[_interp(trace, anchor_t)],
            mode="markers+text",
            text=[_escape(f"{label} · {final:.0f} °C" if final is not None else label)],
            textposition="top center" if index == 0 else "bottom center",
            textfont=dict(size=10, color=color),
            marker=dict(size=6, color=color, line=dict(color="white", width=1)),
            hoverinfo="skip", showlegend=False, cliponaxis=False,
            meta=_PREHEAT_LABEL_META,
        ))

        fig.add_trace(go.Scatter(
            x=[t / 60 for t, _temp in trace],
            y=[temp for _t, temp in trace],
            mode="lines", name=name,
            line=dict(color=color, width=1.5, dash=dash),
            hovertemplate="%{x:.1f} min · %{y:.0f} °C<extra>" + _escape(label) + "</extra>",
        ))


def _tracks_identically(p1: Sequence[TracePoint], p2: Sequence[TracePoint]) -> bool:
    """Whether the two preheater traces are the same line to the nearest 0.1 °C.

    Both non-empty and equal-valued at twenty evenly spaced samples, which is
    the reference's own test. Sampling rather than comparing vertex lists,
    because two recipes can reach the same hold by different numbers of ramps
    and still draw one line.

    `False` whenever either is empty, so a recipe with only P1 keeps P1's own
    colour and label rather than being reported as a combined pair.
    """
    if not p1 or not p2:
        return False
    span = max(p1[-1][0], p2[-1][0])
    if span <= 0:
        return False
    return all(
        round(_interp(p1, (i / 20) * span), 1) == round(_interp(p2, (i / 20) * span), 1)
        for i in range(21)
    )


def _interp(trace: Sequence[TracePoint], t: float) -> float:
    """Temperature at `t` along `trace`, clamped at both ends.

    A local copy of `stats.interp_temp`'s arithmetic rather than an import:
    this one answers "are these the same line", where that one places a marker
    on a line, and tying the two together would mean a change made for one
    silently moving the other.
    """
    if not trace:
        return 0.0
    if t <= trace[0][0]:
        return trace[0][1]
    if t >= trace[-1][0]:
        return trace[-1][1]
    for (t0, temp0), (t1, temp1) in zip(trace, trace[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return temp0
            return temp0 + (temp1 - temp0) * (t - t0) / (t1 - t0)
    return trace[-1][1]


def _add_gas_markers(fig: go.Figure, profile: RuncardProfile) -> None:
    """A dot on the temperature line wherever a reactive gas opened or closed.

    Which events exist at all is `stats.gas_events`'s decision, not this
    function's: it already drops the carriers, the t=0 setup block, and
    anything inside the growth window. This used to re-filter the window here
    as well, which meant one rule in two places — and the two disagreed about
    the edges, because `gas_events` allows a tolerance either side and this did
    not. Everything handed back is drawn.

    Labels alternate above and below the line. The temperature itself is left
    to the hover box; printing it beside every marker is what made these plots
    unreadable wherever two valves moved in the same minute.
    """
    xs: List[float] = []
    ys: List[float] = []
    texts: List[str] = []
    positions: List[str] = []
    colors: List[str] = []
    customdata: List[List[float]] = []
    above = True

    for event in gas_events(profile):
        xs.append(event.t_s / 60)
        ys.append(event.temp_c)
        # The temperature is printed, not left to hover: "H2Se on" says when a
        # valve moved, and "H2Se on · 800 °C" says what the recipe was doing
        # when it did — which is the thing a grower reads the marker for, and
        # is gone the moment the figure becomes a PNG. Crowding is handled by
        # having fewer markers (carriers and in-window events are dropped in
        # `stats.gas_events`) rather than by printing less on each one.
        texts.append(_escape(f"{event.species} {event.direction} · {event.temp_c:.0f} °C"))
        positions.append("top center" if above else "bottom center")
        colors.append(GAS_COLORS.get(event.species, (None, MARKER_FALLBACK_COLOR))[1])
        customdata.append([event.temp_c])
        above = not above

    if not xs:
        return

    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="markers+text",
        text=texts, textposition=positions,
        textfont=dict(size=10, color=colors),
        marker=dict(size=7, color=colors, opacity=0.7),
        customdata=customdata,
        hovertemplate="%{text}<br>%{x:.1f} min · %{customdata[0]:.0f} °C<extra></extra>",
        showlegend=False, cliponaxis=False,
        meta=_GAS_MARKER_META,
    ))


#: An RTV setpoint at or below this is the chamber being pumped down rather
#: than held at a pressure, and the reference draws it as "vac". The gauges
#: these recipes use do not control below about a Torr, so the number itself
#: says nothing.
_RTV_VACUUM_TORR = 1.0

#: Row kinds whose bar labels carry their unit. The gas and PC rows are left
#: bare: their row label already names the channel, every segment in a row
#: shares one unit, and the width a repeated "sccm" costs is the width that
#: makes a one-minute segment's number legible at all.
_UNIT_LABELLED_KINDS = frozenset({"rtv", "spin"})


def _bar_label(row: BarRow, value: float) -> str:
    """The text printed inside one gantt segment."""
    if row.kind == "rtv" and value <= _RTV_VACUUM_TORR:
        return "vac"
    if row.kind in _UNIT_LABELLED_KINDS and row.unit:
        return f"{_format_value(value)} {row.unit}"
    return _format_value(value)


def _add_bar_rows(fig: go.Figure, rows: Sequence[BarRow], total_min: float) -> None:
    """One horizontal bar trace per gantt row, shaded by setpoint.

    One trace per row rather than one per segment: a 40-command recipe produces
    ~45 segments, and 45 traces is 45 legend-suppression flags, 45 hover
    templates and a figure that is slow to serialize for no gain.

    `total_min` is the x-axis range, and it is here only to size the minimum
    segment width off it — see `_MIN_BAR_SPAN_FRACTION` for why an
    instantaneous setpoint change has to be drawn wider than it lasted.

    Fills come in discrete levels from the row's ramp, as the reference draws
    them (see `_segment_style`), opaque and with text in the ramp's own dark or
    pale stop. This used to be a continuous square-root shade toward white at
    85 % opacity with grey text on every bar; next to the reference's PNGs it
    read as washed out, and the levels were too close to tell apart. The
    levels are **per row**: a gas line running 0–5 sccm and a controller
    running 0–400 Torr each take their dark fill at their own maximum.
    """
    min_span = total_min * _MIN_BAR_SPAN_FRACTION

    for row in rows:
        if not row.segments:
            # A channel the recipe addressed and left shut keeps its row. The
            # category only exists on a Plotly axis if some trace references
            # it, so an empty row needs an invisible zero-width bar or it
            # disappears — and "commanded closed" would become indistinguishable
            # from "never mentioned", which is exactly what `BarRow` keeps apart.
            fig.add_trace(go.Bar(
                y=[_escape(row.label)], x=[0.0], base=[0.0],
                orientation="h", width=0.8, yaxis="y2",
                marker=dict(color="rgba(0,0,0,0)"),
                hoverinfo="skip", showlegend=False,
            ))
            continue

        styles = _segment_styles(row)
        unit = f" {row.unit}" if row.unit else ""

        fig.add_trace(go.Bar(
            y=[_escape(row.label)] * len(row.segments),
            # Drawn width, floored; `customdata` below keeps the true span.
            x=[max(s.end_s / 60 - s.start_s / 60, min_span) for s in row.segments],
            base=[s.start_s / 60 for s in row.segments],
            orientation="h", width=0.8, yaxis="y2",
            # A thin white edge, so two neighbouring segments of one fill still
            # read as two — the reference gets the same from rounded corners.
            marker=dict(
                color=[fill for fill, _text in styles],
                line=dict(color="white", width=1.0),
            ),
            # Units are printed on the rows whose label does not already imply
            # one — the throttle valve and the stage — and left off the gas and
            # pressure-controller rows, which is what the reference does and
            # what the historical figures show. Printing "0.1 sccm" on every
            # gas segment costs the width that makes "0.1" legible on a
            # one-minute sliver, and the row is named "MFC-1 Ar" already.
            #
            # An RTV setpoint below 1 Torr is the chamber being pumped rather
            # than held, and reads as "vac" rather than as a number nobody
            # controls to.
            text=[_bar_label(row, s.value) for s in row.segments],
            textposition="inside", insidetextanchor="middle",
            insidetextfont=dict(size=11, color=[text for _fill, text in styles]),
            #
            # The **true** start and end, deliberately not `base` and
            # `base + x`: a segment narrower than `min_span` is drawn wider
            # than it lasted, and the hover box is where the operator goes to
            # find out that it was an instant.
            customdata=[[s.start_s / 60, s.end_s / 60, s.value] for s in row.segments],
            hovertemplate=(
                f"<b>{_escape(row.label)}</b>: %{{customdata[2]:g}}{_escape(unit)}"
                "<br>%{customdata[0]:.1f} – %{customdata[1]:.1f} min<extra></extra>"
            ),
            showlegend=False,
        ))


def _add_row_labels(fig: go.Figure, rows: Sequence[BarRow]) -> None:
    """Each gantt row's name in the right margin, in its row's dark stop.

    As the reference prints them, so a label is tied to its bars by colour as
    well as by position. Annotations rather than the axis's tick labels,
    because an axis has a single tick colour for all of them.
    """
    for row in rows:
        fig.add_annotation(
            xref="paper", x=1.0, xanchor="left", xshift=4,
            yref="y2", y=_escape(row.label), yanchor="middle",
            text=_escape(row.label), showarrow=False,
            font=dict(size=11, color=_row_label_color(row)),
        )


def _add_summary_block(fig: go.Figure, profile: RuncardProfile, inner_px: int) -> None:
    """The recipe's own summary, in the bottom margin.

    Only drawn when there is a growth window, because every line in it is a
    value read at the growth midpoint and there is no midpoint without one. The
    strip is reserved unconditionally in the height budget, so a recipe without
    a window ends in whitespace rather than in a shorter figure that no longer
    lines up beside its neighbours.
    """
    lines = summary_lines(profile)
    if not lines:
        return

    text = "<br>".join(
        f"<span style='color:{color}'>{'<b>' + body + '</b>' if bold else body}</span>"
        for body, color, bold in lines
    )
    # Paper y=0 is already the bottom of the axis area, below the time axis and
    # its title, so this offset only has to clear that title — the whole
    # `_MARGIN_BOTTOM_PX` strip below it is reserved for this block.
    fig.add_annotation(
        xref="paper", yref="paper", x=0.0, y=-_SUMMARY_TOP_GAP_PX / inner_px,
        xanchor="left", yanchor="top", align="left",
        text=text, showarrow=False,
        font=dict(size=11, color=_SUMMARY_BODY_COLOR),
        bgcolor=_SUMMARY_BG, bordercolor=GROWTH_STROKE, borderwidth=1, borderpad=10,
    )


def summary_lines(profile: RuncardProfile) -> List[Tuple[str, str, bool]]:
    """The summary block's `(text, colour, bold)` lines, or `[]` with no window.

    Separate from the annotation that draws them so the same sentences can be
    shown as text beside the chart, and so they can be asserted on without
    parsing a figure.

    The gas line is split onto a hanging continuation when the joined string
    runs past `_SUMMARY_WRAP_CHARS`: a recipe addressing eight channels
    overruns the figure's width, and the annotation has no wrapping of its own.
    """
    values = growth_mid_values(profile)
    growth = profile.growth
    if values.mid_s is None or growth.start_s is None or growth.end_s is None:
        return []

    lines: List[Tuple[str, str, bool]] = [(
        f"Growth window · t={growth.start_s / 60:.0f}–{growth.end_s / 60:.0f} min "
        f"· {growth.peak_temp_c:.0f}°C plateau",
        _SUMMARY_HEADER_COLOR,
        True,
    )]

    gas_parts = [f"{_escape(name)} {value:g} sccm" for name, value in values.mfc]
    if len(" · ".join(gas_parts)) > _SUMMARY_WRAP_CHARS:
        half = len(gas_parts) // 2
        lines.append(("Gas chemistry: " + " · ".join(gas_parts[:half]), _SUMMARY_BODY_COLOR, False))
        lines.append((" · ".join(gas_parts[half:]), _SUMMARY_BODY_COLOR, False))
    else:
        lines.append(("Gas chemistry: " + " · ".join(gas_parts), _SUMMARY_BODY_COLOR, False))

    pressure_parts = [f"{_escape(name)} = {value:g} Torr" for name, value in values.pc]
    if values.rtv is not None:
        # In Torr, like the other pressure rows: params[1] of RTV Pressure Ctrl
        # is the commanded chamber-pressure setpoint. See the module docstring.
        pressure_parts.append(f"RTV P {values.rtv:g} Torr")
    if values.spin is not None:
        pressure_parts.append(f"Spin {values.spin:g} rpm")
    lines.append(("Pressure: " + " · ".join(pressure_parts), _SUMMARY_BODY_COLOR, False))

    if values.p1 is not None:
        lines.append((f"Heater 2 (P1): {values.p1:.0f}°C", P1_COLOR, False))
    if values.p2 is not None:
        lines.append((f"Heater 3 (P2): {values.p2:.0f}°C", P2_COLOR, False))

    return lines


# ------------------------------------------------------------------ colours
def _row_ramp(row: BarRow) -> _Ramp:
    """The ramp one gantt row draws with.

    The two pressure controllers are told apart by a digit in their name rather
    than by position, because `Accumulation PC*` commands can introduce a
    channel that no `MFC/PC` row ever mentioned and the order they appear in is
    the recipe's, not the hardware's.
    """
    if row.kind == "rtv":
        return RTV_RAMP
    if row.kind == "spin":
        return SPIN_RAMP
    if row.kind == "tube":
        return INNER_TUBE_RAMP if row.label.startswith("Inner") else OUTER_TUBE_RAMP
    if row.kind == "pc":
        return PC1_RAMP if "1" in row.label else PC2_RAMP
    species = get_species(row.label)
    if species in GAS_COLORS:
        (light, dark), (dark_text, light_text) = GAS_COLORS[species], GAS_TEXT_COLORS[species]
    else:
        index = zlib.crc32(row.label.encode("utf-8")) % len(FALLBACK_GAS_COLORS)
        (light, dark), (dark_text, light_text) = FALLBACK_GAS_COLORS[index], FALLBACK_GAS_TEXT_COLORS[index]
    return _Ramp(light, dark, dark_text, light_text)


def _row_label_color(row: BarRow) -> str:
    """The colour a row's name is printed in: its ramp's dark text stop."""
    if row.kind == "rtv":
        return RTV_LABEL_COLOR
    return _row_ramp(row).dark_text


def _segment_styles(row: BarRow) -> List[Tuple[str, str]]:
    """`(fill, text colour)` for each of `row`'s segments, in order.

    The reference's levels, per row:

    - **gas**: the dark fill for a segment at or above 95 % of the row's
      maximum, the light fill for the rest — and the light fill throughout
      when the row only ever holds one value, so a gas opened once is not
      drawn as if it were a peak;
    - **pressure controller**: the same, with a mid fill between 50 % and 95 %;
    - **throttle valve**: one solid fill, and the dark one for "vac";
    - **stage** and **tube sums**: one flat fill.
    """
    ramp = _row_ramp(row)
    values = [segment.value for segment in row.segments]
    vmax = max(values, default=0.0)
    varied = len(set(values)) > 1

    styles: List[Tuple[str, str]] = []
    for value in values:
        if row.kind == "rtv":
            fill = ramp.dark if value <= _RTV_VACUUM_TORR else ramp.light
            styles.append((fill, ramp.light_text))
        elif row.kind in ("spin", "tube"):
            styles.append((ramp.light, ramp.dark_text))
        elif varied and value >= _DARK_FRACTION * vmax:
            styles.append((ramp.dark, ramp.light_text))
        elif ramp.mid is not None and value >= _MID_FRACTION * vmax:
            styles.append((ramp.mid, ramp.light_text))
        else:
            styles.append((ramp.light, ramp.dark_text))
    return styles


# ------------------------------------------------------------------ helpers
def _right_margin(rows: Sequence[BarRow]) -> int:
    """Right margin wide enough for the longest gantt row label."""
    longest = max((len(row.label) for row in rows), default=0)
    return max(_MARGIN_RIGHT_PX, int(longest * _LABEL_CHAR_PX) + _LABEL_PAD_PX)


def _gridline_values(peak_temp: float) -> List[int]:
    """Tick positions for the temperature axis: zero, 300 °C, and this peak."""
    return [*_FIXED_GRIDLINES_C, int(peak_temp)]


def _format_value(value: float) -> str:
    """The number as a bar prints it: `20.0` -> `"20"`, `0.5` -> `"0.5"`.

    Setpoints are written as whole numbers in the recipe far more often than
    not, and `20.0 sccm` inside a 40-pixel bar spends a third of its width on a
    zero nobody typed.
    """
    if float(value) == int(value):
        return f"{int(value)}"
    return f"{value:g}"


def _escape(text: str) -> str:
    """XML-escape text that came from a filename or a recipe.

    Plotly renders a subset of HTML in titles, hover text and annotations, so a
    channel name or a run id containing `<` is markup unless it is escaped
    here. Only applied to strings the figure did not compose itself; numbers
    formatted above are safe by construction.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
