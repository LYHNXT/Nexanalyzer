"""
Everything the runcard figure needs worked out before anything is drawn: the
metrics row, the gantt rows, the gas on/off events, and the setpoints holding
at the growth midpoint.

All of it used to be computed **inside** the renderer of the app this was
ported from — a 330-line function that measured, formatted and emitted SVG in
one pass, so the five numbers the page put above the chart could only be got at
by rendering the chart. Pulled out here they are pure, testable, and readable
by a table that draws no chart at all: the Runcard page's folder listing is one
`profile_stats` call per recipe and no figures.

Nothing in this module knows a colour, a pixel or a font. `viz/profile.py`
owns those, and it is the only caller that needs all of this at once.

**Two display guards are deliberately fixed here** where `growth_window.py`
reproduces its own verbatim. The ancestor tested `if gw_start` rather than `if
gw_start is not None` when converting the window edges to minutes, and again
when suppressing gas markers inside the window — while the band itself was
drawn on `is not None`. A window starting at exactly t=0 therefore got a drawn
band, a printed duration, and an empty "Growth window" metric beside them. The
metrics row and the band it sits above have to agree about whether there is a
window, so both guards read `is not None` here.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .growth_window import (
    GrowthWindow,
    NamedEvent,
    RuncardProfile,
    Timeline,
    TracePoint,
    ValueEvent,
    channel_value_at_growth_mid,
    fixed_channel_value_at_growth_mid,
    get_species,
)

#: An MFC segment is drawn when its setpoint is **strictly above** this.
#:
#: **Zero**, matching the reference renderer: if the recipe commanded a flow,
#: the plot shows it. This was 0.05 for a while, on the reasoning that a
#: 0.03 sccm trickle is a bar nobody can read at the width one minute gets.
#: What it actually did was delete the idle purge flow — H2Se held at exactly
#: 0.01 sccm — from every figure, so a line the recipe was addressing looked
#: untouched. An unreadably thin bar still says "something happened here" and
#: carries its value in hover; an absent one says nothing.
#:
#: Still distinct from `growth_window._MFC_ON_FLOOR` (0.01), which answers the
#: different question of whether a gas was *feeding the growth* — a purge flow
#: is drawn but is not chemistry.
MFC_BAR_FLOOR = 0.0

#: A gas marker is emitted when a channel crosses this floor in either
#: direction. **Zero**, matching the reference renderer: any commanded flow at
#: all is a transition worth marking.
#:
#: Deliberately *not* `MFC_BAR_FLOOR`. It shared that value for a while, on the
#: reasoning that a marker appearing where no bar starts reads as a rendering
#: fault. The cost was worse: a recipe idling H2Se at 0.01 sccm — its purge
#: flow — had that line's open and close dropped from the plot entirely, so two
#: markers the historical figures show were simply missing. A marker without a
#: visible bar says "the recipe addressed this line here", which is true and
#: worth saying; the bar floor answers the different question of whether a
#: sliver is wide enough to read.
MARKER_FLOOR = 0.0

#: Never marked on/off. Both are carriers: Ar is open for most of a run on
#: several lines at once, and N2 likewise, so marking their transitions buries
#: the reactive-gas markers — the ones a grower is reading the plot for — under
#: labels for a valve that was always going to be open.
SKIP_SPECIES = frozenset({"Ar", "N2", "N₂"})
#: The subscript spelling is in the set because a recipe is a hand-typed CSV
#: and "N₂" does occur; matching only the ASCII form would let one file's
#: carrier transitions through while every other file suppressed them.

GROWTH_EDGE_TOL_S = 30.0
"""How far outside the growth window still counts as "at the edge".

The reactive gases are commanded on within a second or two of the window
opening and off within a second or two of it closing, but not at exactly the
same instant — so an equality test would suppress the marker at one edge and
not the other. Thirty seconds is the reference renderer's own tolerance."""

#: Pixels one gantt row occupies: a 24 px bar plus a 6 px gap. Kept as one
#: number because the figure's height and its bar region's height must both be
#: derived from it or the rows crowd as a recipe adds channels.
BAR_ROW_PX = 30

#: Everything in the figure that is not a gantt row: title, temperature panel,
#: time axis, and the summary block under it. The height is this plus one
#: `BAR_ROW_PX` per row *slot* — see `gantt_row_slots`, which is why this is
#: never the whole height of a real figure, not even a rowless one.
CANVAS_BASE_PX = 500


@dataclass(frozen=True)
class BarSegment:
    """One channel holding one value, from `start_s` until its next command.

    The last segment of a channel runs to the end of the run: a setpoint is
    never cancelled by the recipe, it is only replaced.
    """

    start_s: float
    end_s: float
    value: float


@dataclass(frozen=True)
class BarRow:
    """One gantt row: a channel, its segments, and what kind of thing it is.

    `kind` is `"gas"`, `"rtv"`, `"pc"`, `"spin"` or `"tube"` (a summed-flow
    row, see `build_tube_rows`). It carries no colour — the
    figure picks those — but it is what the figure switches on, because gas
    rows and pressure rows are drawn differently in four places and a boolean
    called `is_pressure` could not say which of the three pressure-ish rows it
    was looking at.

    A row may legitimately have **no segments**: a channel commanded only to
    values at or below the floor still gets its row, so the operator can see
    that the recipe addressed it and left it shut. Dropping the row would make
    "commanded closed" and "never mentioned" look identical.
    """

    label: str
    kind: str
    unit: str
    segments: Tuple[BarSegment, ...]


@dataclass(frozen=True)
class GasEvent:
    """A reactive gas crossing `MARKER_FLOOR`, with the temperature it crossed at.

    `temp_c` is **interpolated** along the reconstructed trace, unlike every
    setpoint lookup in `growth_window`, which holds. A marker sitting on the
    temperature line has to sit *on* it, and a step lookup would place a marker
    for a valve opened part-way up a ramp at the plateau temperature below,
    visibly off the line it is annotating.
    """

    t_s: float
    species: str
    direction: str
    temp_c: float


@dataclass(frozen=True)
class GrowthMidValues:
    """Every setpoint holding at the growth midpoint — the recipe's own summary.

    Midpoint rather than start or end: the edges are trace vertices, so the
    start is the instant a ramp completed and the end the instant the heater
    went off, and both are moments when something was changing. The midpoint is
    the quietest point in the window.

    `mfc` and `pc` are per **channel**, in first-appearance order, not per
    species: a recipe running Ar on four lines has four numbers here and one in
    a species column, and the summary is the place that shows all four.

    Empty tuples and `None`s throughout when there is no growth window.
    """

    mid_s: Optional[float]
    peak_temp_c: float
    mfc: Tuple[Tuple[str, float], ...]
    pc: Tuple[Tuple[str, float], ...]
    rtv: Optional[float]
    spin: Optional[float]
    p1: Optional[float]
    p2: Optional[float]


def interp_temp(trace: Sequence[TracePoint], t: float) -> float:
    """Temperature at `t`, linearly interpolated between trace vertices.

    Clamps to the first and last vertex outside the trace's own span, which
    matters at the right edge: a recipe with no `Heater Soak` has a trace that
    stops at its final ramp while the run continues, so a marker after that
    point reads the last temperature rather than falling off the end.

    A zero-length segment (two vertices sharing a timestamp, which simultaneous
    commands produce) returns the earlier vertex's value instead of dividing by
    zero.
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


def _channel_order(events: Sequence[NamedEvent]) -> List[str]:
    """Distinct channel names in first-appearance order.

    First appearance, not sorted: it puts the row a recipe touches first at the
    top of the gantt, which is the order the recipe reads in. Sorting would put
    `MFC-1` above `MFC-8` on every card and lose that.
    """
    seen: List[str] = []
    for _t, name, _v in events:
        if name not in seen:
            seen.append(name)
    return seen


def _segments_from(points: Sequence[Tuple[float, float]], total: float, floor: float) -> Tuple[BarSegment, ...]:
    """Turn one channel's `(t, value)` commands into held segments.

    Each command above `floor` runs until that channel's **next** command, or
    to the end of the run for the last one. A command at or below the floor
    emits nothing, which is what ends the preceding segment.
    """
    segments: List[BarSegment] = []
    for i, (t, value) in enumerate(points):
        if value > floor:
            end = points[i + 1][0] if i + 1 < len(points) else total
            segments.append(BarSegment(start_s=t, end_s=end, value=value))
    return tuple(segments)


def _named_rows(events: Sequence[NamedEvent], total: float, floor: float, kind: str, unit: str) -> List[BarRow]:
    """One `BarRow` per distinct channel in `events`, in first-appearance order."""
    rows: List[BarRow] = []
    for name in _channel_order(events):
        points = [(t, v) for t, n, v in events if n == name]
        rows.append(BarRow(label=name, kind=kind, unit=unit, segments=_segments_from(points, total, floor)))
    return rows


def _simple_row(events: Sequence[ValueEvent], total: float, label: str, kind: str, unit: str) -> Optional[BarRow]:
    """The single row for RTV or Spin, or `None` when the recipe never set it.

    Unlike the named rows, these are dropped entirely when empty: there is one
    throttle valve and one stage, so an empty row would say "this reactor has a
    stage" rather than anything about this recipe.
    """
    segments = _segments_from(list(events), total, 0.0)
    if not segments:
        return None
    return BarRow(label=label, kind=kind, unit=unit, segments=segments)


def build_bar_rows(timeline: Timeline, run_id: str = "") -> List[BarRow]:
    """The gantt rows, top to bottom, in the order they are stacked.

    Gases first (first-appearance order), then the throttle valve, then the
    pressure controllers, then the stage, then the inner- and outer-tube sums.
    Gases lead because they are what a grower reads first and what the on/off
    markers above the temperature trace refer to; the stage is last of the
    commanded channels because it is usually one segment spanning the whole run
    and carries the least information per pixel. The tube sums are derived
    rather than commanded, so they sit below everything the recipe set.

    `run_id` only picks *which* MFCs each tube sums (see `tube_groups_for`); it
    never changes how many rows there are. That is what lets `profile_stats`
    size the figure without being told the run id.

    The row *count* is load-bearing beyond the drawing: `profile_stats` derives
    the figure height from it, so both come from this one function rather than
    from two counts that have to agree.
    """
    total = timeline.total_time
    rows = _named_rows(timeline.mfc_events, total, MFC_BAR_FLOOR, "gas", "sccm")
    gas_rows = list(rows)

    # "RTV P" with a Torr unit, both restored. This row briefly read "RTV" and
    # carried no unit, on the reasoning that the stored number was neither the
    # gauge range nor the achieved pressure and so might not be a pressure at
    # all. The reference renderer settles it: the third column *is* the
    # chamber-pressure setpoint the recipe commands, in Torr.
    rtv = _simple_row(timeline.rtv_events, total, "RTV P", "rtv", "Torr")
    if rtv is not None:
        rows.append(rtv)

    rows.extend(_named_rows(timeline.pc_events, total, 0.0, "pc", "Torr"))

    spin = _simple_row(timeline.spin_events, total, "Spin", "spin", "rpm")
    if spin is not None:
        rows.append(spin)

    rows.extend(build_tube_rows(gas_rows, total, run_id))
    return rows


#: Which MFCs feed the inner tube and which the outer, per reactor series —
#: `(inner, outer)` MFC numbers. The CSV never says which tube a line feeds,
#: so this is plumbing knowledge, keyed on the first two letters of the run id
#: (`VBCA08` -> VB, `VABD14` -> VA, `HADI44` -> HA). VA is plumbed like VB.
#: From the reference renderer's `SERIES_TUBE_GROUPS`; keep the two in step.
_VB_TUBES: Tuple[Tuple[int, ...], Tuple[int, ...]] = ((1, 2, 3, 4), (5, 6, 7, 8))
TUBE_GROUPS: Dict[str, Tuple[Tuple[int, ...], Tuple[int, ...]]] = {
    "VB": _VB_TUBES,
    "VA": _VB_TUBES,
    "HA": ((1, 3, 4), (6, 7, 8)),
}

#: The series an unrecognised run id falls back to, as the reference does.
DEFAULT_TUBE_SERIES = "HA"


def tube_groups_for(run_id: str) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    """`(inner, outer)` MFC numbers for the series `run_id` belongs to."""
    return TUBE_GROUPS.get((run_id or "")[:2].upper(), TUBE_GROUPS[DEFAULT_TUBE_SERIES])


def tube_label(kind: str, numbers: Sequence[int]) -> str:
    """`"Inner tube (MFC1+3+4)"` — the row names the MFCs it sums."""
    return f"{kind} tube (MFC{'+'.join(str(n) for n in numbers)})"


def _summed_segments(rows: Sequence[BarRow], numbers: Sequence[int], total: float) -> Tuple[BarSegment, ...]:
    """The total flow of the MFCs numbered `numbers`, as held segments.

    A channel is matched on its `MFC-N` prefix, the first word of its name, so
    `MFC-3 O2` is MFC 3 whatever gas it carries. Every segment edge of every
    matched channel is a breakpoint; between two breakpoints the total is
    constant. Neighbouring stretches with the same total are merged, and a
    stretch where the total is zero is left out — an empty tube row is blank,
    as the reference draws it.
    """
    prefixes = {f"MFC-{n}" for n in numbers}
    segments = [
        segment
        for row in rows
        if (row.label.split() or [""])[0] in prefixes
        for segment in row.segments
    ]
    if not segments:
        return ()

    edges = sorted({0.0, total, *(s.start_s for s in segments), *(s.end_s for s in segments)})
    merged: List[BarSegment] = []
    for t0, t1 in zip(edges, edges[1:]):
        if t1 <= t0:
            continue
        mid = (t0 + t1) / 2
        value = sum(s.value for s in segments if s.start_s <= mid < s.end_s)
        if value <= 0:
            continue
        if merged and merged[-1].end_s == t0 and abs(merged[-1].value - value) < 1e-9:
            merged[-1] = BarSegment(start_s=merged[-1].start_s, end_s=t1, value=merged[-1].value)
        else:
            merged.append(BarSegment(start_s=t0, end_s=t1, value=value))
    return tuple(merged)


def build_tube_rows(gas_rows: Sequence[BarRow], total: float, run_id: str = "") -> List[BarRow]:
    """The two summed-flow rows: inner tube, then outer tube.

    They exist to check one rule at a glance — the inner tube must run at least
    60 sccm above the outer — which no single gas row can show.

    Both rows are present whenever the recipe addressed any gas line, even when
    one sums to nothing, so every figure of a series has the same two rows in
    the same place. A recipe with no gas lines gets neither, which keeps the
    gantt's "no commands" note for a recipe that really has none.
    """
    if not gas_rows:
        return []
    inner, outer = tube_groups_for(run_id)
    return [
        BarRow(label=tube_label("Inner", inner), kind="tube", unit="sccm",
               segments=_summed_segments(gas_rows, inner, total)),
        BarRow(label=tube_label("Outer", outer), kind="tube", unit="sccm",
               segments=_summed_segments(gas_rows, outer, total)),
    ]


def gantt_row_slots(rows: Sequence[BarRow]) -> int:
    """Row-heights the gantt panel occupies — one per row, and never fewer than one.

    A recipe with no gas, pressure or stage commands still needs a row's worth
    of panel to print "no commands in this recipe" into, so such a figure is
    one `BAR_ROW_PX` taller than its row count says.

    The floor used to live only in the figure builder while `canvas_h` was
    computed from the raw count, so a Heater-Ramp-only recipe reported 500 px
    and drew 530. The page feeds the reported number to the layout and to the
    PNG exporter, so the export was sized 30 px short of the figure it was
    exporting. One expression, both callers.
    """
    return max(len(rows), 1)


def canvas_height(rows: Sequence[BarRow]) -> int:
    """Total figure height in pixels for a gantt of `rows`.

    The figure's `height`, the depth of its bar region and the `canvas_h` the
    page exports at all come from here and from `gantt_row_slots`, which is
    the only way they cannot drift apart again.
    """
    return CANVAS_BASE_PX + BAR_ROW_PX * gantt_row_slots(rows)


def gas_events(profile: RuncardProfile) -> List[GasEvent]:
    """Every reactive-gas valve opening or closing, with its temperature.

    A channel's state starts at 0 and is updated by every command; a crossing
    of `MARKER_FLOOR` in either direction emits one event. Three kinds of
    command deliberately emit nothing:

    - anything on a channel whose species is in `SKIP_SPECIES` — the carriers,
      which switch too often to be chemistry;
    - **anything at t=0**, which sets the channel's initial state instead. A
      recipe opens its carrier lines in its first block, and marking those
      would put a stack of labels on top of each other at the left edge before
      the run has started;
    - **anything inside the growth window**, within `GROWTH_EDGE_TOL_S` of
      either edge. The growth band is annotated with its own chemistry summary,
      so a marker there says a second time what the band already says, and the
      reactive gases all switch at once at the window edges — which is exactly
      where labels pile up.

    The state machine still *runs* through every skipped command, so a valve
    that opens inside the window and closes outside it still reports the close.
    Skipping means "emit no marker", never "ignore the command".

    Events come back in time order, which is the order the figure alternates
    label placement in.
    """
    growth = profile.growth
    events: List[GasEvent] = []
    state: Dict[str, float] = {}
    for t, name, value in sorted(profile.timeline.mfc_events, key=lambda e: e[0]):
        species = get_species(name)
        suppressed = (
            species in SKIP_SPECIES
            or t == 0
            or _inside_growth(t, growth)
        )
        if suppressed:
            state[name] = value
            continue
        previous = state.get(name, 0.0)
        if previous <= MARKER_FLOOR < value:
            events.append(GasEvent(t, species, "on", interp_temp(profile.temp_trace, t)))
        elif value <= MARKER_FLOOR < previous:
            events.append(GasEvent(t, species, "off", interp_temp(profile.temp_trace, t)))
        state[name] = value
    return events


def _inside_growth(t: float, growth: GrowthWindow) -> bool:
    """Whether `t` falls in the growth window, edges included within tolerance.

    `False` whenever there is no window, so a recipe that never reached
    temperature suppresses nothing and shows every transition it has.
    """
    if growth.start_s is None or growth.end_s is None:
        return False
    return growth.start_s - GROWTH_EDGE_TOL_S < t < growth.end_s + GROWTH_EDGE_TOL_S


def growth_mid_values(profile: RuncardProfile) -> GrowthMidValues:
    """What every channel was holding at the growth midpoint.

    Built from `growth_window`'s own lookups rather than from a second scan of
    the event lists, so the floors — strictly above 0.01 sccm for a gas,
    strictly above zero for a controller, none at all for a preheater — have
    one home. A channel below its floor is left out of the tuple entirely, so
    the summary lists what was running and says nothing about what was shut.
    """
    growth = profile.growth
    mid = growth.mid_s
    if mid is None:
        return GrowthMidValues(
            mid_s=None, peak_temp_c=growth.peak_temp_c,
            mfc=(), pc=(), rtv=None, spin=None, p1=None, p2=None,
        )

    mfc = tuple(
        (name, value)
        for name, value in (
            (n, channel_value_at_growth_mid(profile, n)) for n in _channel_order(profile.timeline.mfc_events)
        )
        if value is not None
    )
    pc = tuple(
        (name, value)
        for name, value in (
            (n, channel_value_at_growth_mid(profile, n)) for n in _channel_order(profile.timeline.pc_events)
        )
        if value is not None
    )
    return GrowthMidValues(
        mid_s=mid,
        peak_temp_c=growth.peak_temp_c,
        mfc=mfc,
        pc=pc,
        rtv=fixed_channel_value_at_growth_mid(profile, "RTV"),
        spin=fixed_channel_value_at_growth_mid(profile, "Spin"),
        p1=fixed_channel_value_at_growth_mid(profile, "P1"),
        p2=fixed_channel_value_at_growth_mid(profile, "P2"),
    )


def profile_stats(profile: RuncardProfile) -> Dict[str, Optional[float]]:
    """The eight numbers above the chart, and the height of the chart itself.

    Keys, with their units:

    ``total_min``
        `total_time / 60`. The **recipe** clock, not wall-clock — see the
        module docstring of `growth_window` on the pump-down time it omits.
    ``peak_T``
        °C, the run-wide maximum over the whole trace, cooldown included. Never
        `None` and never below 25, because the origin vertex is always there.
        Its presence says nothing about whether a growth window exists.
    ``gw_start_min`` / ``gw_end_min``
        Minutes, or `None` when there is no window. `is not None` guards, not
        the ancestor's truthiness ones — see the module docstring.
    ``gw_dur``
        Minutes, and **`0.0` rather than `None`** when there is no window,
        because it comes straight off `GrowthWindow.duration_min`. Read
        `gw_start_min` to decide whether to show it.
    ``p1_T`` / ``p2_T``
        °C at the **end of the run**, not at the growth midpoint: an aux trace
        holds its last setpoint out to `total`, so this is the last vertex.
        `None` when the recipe has no preheater ramps, which is every HAD*
        recipe in the example folder.
    ``canvas_h``
        Pixels, via `canvas_height`, so the figure grows a row at a time and a
        recipe with twelve gas lines does not draw them at half height. It is
        `build_profile_figure`'s own height and not a second estimate of it:
        the page sizes the layout and the exported PNG from this number, and a
        rowless recipe reported 500 against a figure that drew 530.

    The key names are the ancestor's, kept deliberately: they are what the
    metrics row and the figure both read, and renaming them to house style
    would be a rename with no reader to benefit from it and every historical
    reference to break.
    """
    timeline = profile.timeline
    growth = profile.growth

    start_s, end_s = growth.start_s, growth.end_s
    return {
        "total_min": timeline.total_time / 60,
        "peak_T": growth.peak_temp_c,
        "gw_start_min": start_s / 60 if start_s is not None else None,
        "gw_end_min": end_s / 60 if end_s is not None else None,
        "gw_dur": growth.duration_min,
        "p1_T": profile.p1_trace[-1][1] if profile.p1_trace else None,
        "p2_T": profile.p2_trace[-1][1] if profile.p2_trace else None,
        "canvas_h": canvas_height(build_bar_rows(timeline)),
    }
