"""
The recipe clock: a list of runcard commands replayed into a timeline, a
reconstructed temperature trace, and the growth window cut out of it.

Pure arithmetic — no Streamlit, no file access. It takes the `RuncardCommand`
rows `io.parser` produced and gives back what every surface on the Runcard page
reads.

**Exactly three commands advance the clock: `Wait`, `Check Status` and the
`Pulse` family.** Everything else is instantaneous. A `Heater Ramp` records its
declared duration on its tuple and moves the running temperature, but the clock
does not move with it — the recipe author is expected to follow every ramp with
a matching `Wait`, and in every example file they do.

`Pulse H2Se,72,5` (and `Pulse`, `Pulse O2`) is the growth step of every recipe
written since mid-2025: N pulses of a gas, each on for the half-period and off
for the same. The vendored reference predates the command and ignores it, which
collapsed the growth window of 44 real recipes to a single instant — a
"Growth (0 min @ 890 °C)" caption on a 13-minute growth. Its cost is in the
recipe (`N * 2 * half_period`), plus a measured per-pulse switching overhead;
see `pulse_duration_seconds`. This is the one place `modules/runcard` extends
the reference's vocabulary rather than restating it.

`Check PC-1`/`Check PC-2` and `Stage Pos` are, like `Pumping`, holds of a
length the recipe does not state (measured median 9 s and 30 s, max 189 s and
80 s). They cost the clock nothing, for the reason given for pumping below.

**`Pumping` and `Pumping Forward` are recorded but never advance the clock, and
that is forced, not an oversight.** Their single parameter is a *target
pressure* — `Pumping,5.00E-01,--`, `Pumping Forward,0.0011,--` — and `params[1]`
is always `--`. There is no number in the row that could be added to the clock,
because the real hold is however long the pump takes to reach that pressure,
which depends on tube state and is not in the recipe. The cost is measured: in
the VBBE00 datalog, `Pumping Forward` occupies **844 of 6903 logged samples,
about 14 minutes** of wall-clock while contributing 0 s to the reconstructed
7960 s. So `Timeline.total_time` and every timestamp derived from it are an
**idealised recipe clock, not wall-clock**, and the two diverge by the
cumulative pump-down time before the instant in question. Aligning a
reconstructed growth window against a datalog by absolute seconds will be wrong
by exactly that much. Do not "fix" this by inventing a duration.

They are kept in `Timeline.pump_events` regardless, because *where* the recipe
pumps down is worth drawing even when *how long* is unknowable — and because
knowing only the "Pumping Forward" spelling silently dropped every HAD* recipe's
pump-downs, which is 25 of the 39 example files.
"""

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..io.parser import RuncardCommand

#: `RuncardCommand` lives with the parser that produces it — this module tree
#: has no `models/` package — so this import runs at module scope and
#: `parser.list_runcards` defers its import of this module instead. See the
#: comment inside `list_runcards`.

CHECK_STATUS_SECONDS = 10
"""Seconds one `Check Status` costs the clock, regardless of its params.

Carried from the CLI this was ported from, where it was the inline literal
`t += 10`. Zero occurrences in the 40 example runcards it was validated on;
1,170 of the 2,349 real HA1P01/VB1P01 cards use it, one to three times each,
and the datalogs show the action itself lasting a median 1 s (max 32 s). The
10 s is kept because the reference says 10 s."""

PULSE_SWITCH_OVERHEAD_SEC = 1.5
"""Seconds one pulse costs beyond its commanded on-plus-off time.

Measured, not nominal: on HA1P01 datalogs a `Pulse H2Se,72,5` block spans
834 s for 72 MFC-8 on-edges (11.6 s per pulse against the commanded 10),
`Pulse H2Se,12,5` 139 s for 12 (11.6 s), and `Pulse,6,30` 367 s for 6
(61.2 s against 60). The controller spends about a second and a half per
pulse switching, consistently across pulse counts and periods, so it is
added per pulse rather than left out: without it a 72-pulse growth
reconstructs 14 % short."""

ROOM_TEMP_C = 25.0
"""Where every reconstructed trace starts and where the cooldown asymptotes.

Not measured per run: the runcard never states an ambient, and the first
`Heater Ramp` states only its target. 25 °C is the CLI's own literal."""

_PEAK_TOL_C = 1e-6
"""How close to the run-wide peak counts as *at* the peak.

A float-equality tolerance, not a physical band. The growth window's edges are
equality tests against `peak_temp_c` (see `find_growth_window`); this exists
only because both sides are floats that came through arithmetic. Widening it
into a real temperature band is the defect that rule was written to remove."""

_AMBIENT_FLOOR_C = 100.0
"""A run whose peak never clears this is treated as having no growth window.

Without it, a recipe that never turns the heater on reports its own ambient as
a "peak" and the whole run as growth. The reference renderer uses the same
figure."""

COOLDOWN_TAU_SEC = 1500
"""Time constant of the synthesized exponential cooldown, in seconds (25 min).

The runcard says nothing about cooling — it only says the heater went off — so
the tail after `Heater Soak,0` is a model, not data. 25 min is the reference
renderer's own figure; this port briefly used 40 min, carried from an older
ancestor, which stretched every cooldown tail by two thirds."""

COOLDOWN_STEP_SEC = 60
"""Sampling interval of the cooldown tail, in seconds.

The only uniformly-sampled part of the trace; the ramp phase is a sparse vertex
list."""

#: The two spellings of the pump-down command. The VBBE family writes
#: "Pumping Forward"; the HAD* family writes bare "Pumping". Knowing only the
#: first dropped every HAD* pump-down without a word — and 25 of the 39 example
#: recipes are HAD*.
_PUMP_COMMANDS = frozenset({"Pumping", "Pumping Forward"})

#: An MFC must be **strictly above** this to count as flowing. Real runcards
#: idle a channel at exactly `0.01` sccm — a purge flow, not a process flow —
#: so a non-strict test would report every idle channel as on.
_MFC_ON_FLOOR = 0.01

#: Pressure controllers, the throttle valve and the stage must be strictly
#: above zero to count as on. They have no idle offset to clear.
_ZERO_FLOOR = 0.0

#: `(start_s, from_C, to_C, duration_s)`. `from_C` is what the running
#: temperature was when the ramp was issued; `duration_s` is what the recipe
#: *declared*, not what the clock spent.
Ramp = Tuple[float, float, float, float]

#: `(t_s, channel_name, value)` — the channel name verbatim, species suffix
#: included ("MFC-8 H2Se", "PC-1").
NamedEvent = Tuple[float, str, float]

#: `(t_s, value)` for the two single-channel rows, RTV and stage rotation.
ValueEvent = Tuple[float, float]

#: `(t_s, temperature_C)`, a trace vertex.
TracePoint = Tuple[float, float]


@dataclass(frozen=True)
class Timeline:
    """Every command replayed onto the recipe clock, in command order.

    The lists are non-decreasing in `t` because they are appended as the clock
    advances, and simultaneous events share a timestamp: a run of `MFC/PC` rows
    between two `Wait`s all land together. Every consumer here reads them back
    with a last-write-wins scan, which is only correct while that ordering
    holds.

    `frozen=True` blocks attribute rebinding and nothing more — the lists
    themselves stay mutable and are the builder's own. Do not sort them: see
    `build_temp_trace` on why a trace may legitimately run backwards.
    """

    total_time: float
    heater_ramps: List[Ramp]
    p1_ramps: List[Ramp]
    p2_ramps: List[Ramp]
    mfc_events: List[NamedEvent]
    pc_events: List[NamedEvent]
    rtv_events: List[ValueEvent]
    spin_events: List[ValueEvent]
    pump_events: List[ValueEvent]
    heater_off_t: Optional[float]


@dataclass(frozen=True)
class GrowthWindow:
    """When the film was actually growing, and how hot it was.

    `peak_temp_c` being a real number is **not** evidence that a window exists.
    It is a `max()` over a trace that always has at least one vertex, so it is
    populated even for a recipe that never heated — 25 °C, below the ambient
    floor, and no window at all. Test `start_s`, never `peak_temp_c`.

    A heater cut part-way up a ramp peaks at the temperature it had reached
    when it was cut, at the instant it was cut, so its window is zero-length
    and sits on heater-off — the same edges the reference draws.
    """

    start_s: Optional[float]
    end_s: Optional[float]
    peak_temp_c: float

    @property
    def mid_s(self) -> Optional[float]:
        """Midpoint of the window in seconds, or None if there is no window.

        Every value-at-growth lookup keys off this, so `None` here is what
        makes them all return `None` together instead of quoting a setpoint
        from a run that never reached temperature.
        """
        if self.start_s is None or self.end_s is None:
            return None
        return (self.start_s + self.end_s) / 2

    @property
    def duration_min(self) -> float:
        """Window length in minutes; `0.0` when there is no window.

        Not `None`, which means a caller cannot tell "no window" from a genuine
        zero-length one through this property — a zero-length window is what a
        recipe with no `Heater Soak` produces, since its trace stops at the
        final ramp vertex and both edges land there. Test `start_s` or `mid_s`
        for existence and read this only for display.
        """
        if self.start_s is None or self.end_s is None:
            return 0.0
        return (self.end_s - self.start_s) / 60


@dataclass(frozen=True)
class RuncardProfile:
    """One recipe, fully reconstructed: the single thing every surface reads.

    `temp_trace` is never empty — the worst case is the lone origin vertex
    `[(0.0, 25.0)]` — while `p1_trace` and `p2_trace` are `[]` whenever the
    recipe has no preheater ramps, which is the normal case for HAD* recipes.
    That asymmetry is deliberate and lives in the two trace builders.
    """

    timeline: Timeline
    temp_trace: List[TracePoint]
    p1_trace: List[TracePoint]
    p2_trace: List[TracePoint]
    growth: GrowthWindow


def wait_duration_seconds(cmd: RuncardCommand) -> float:
    """Seconds one `Wait` command holds for.

    **The runcard puts the unit first**: `params[0]` is `Sec` or `Min`,
    `params[1]` is the magnitude. Reading them the other way round reconstructs
    `Wait,Min,20` as 20 seconds instead of 20 minutes, which on a HAD* recipe
    moves the growth window by most of an hour.

    Minutes iff the lowercased unit starts with `min`, so `Min`, `min`,
    `Minutes` and `minute` all scale. **Everything else is seconds**, including
    a typo and including `--` — a silent default, but the alternative is a
    recipe that refuses to load over a unit cell nobody looks at.

    Raises `IndexError` on fewer than two params and `ValueError` on a
    non-numeric magnitude; `list_runcards` is the caller that decides such a
    file is simply not a runcard.
    """
    unit, value = cmd.params[0], cmd.params[1]
    duration = float(value)
    return duration * 60 if unit.lower().startswith("min") else duration


def pulse_duration_seconds(cmd: RuncardCommand) -> float:
    """Seconds one `Pulse*` command holds the clock.

    `params[0]` is the pulse count and `params[1]` the half-period in seconds:
    the gas is on for `params[1]`, off for `params[1]`, `params[0]` times.
    `Pulse H2Se,72,5` is 72 x (5 + 5) s plus 72 x `PULSE_SWITCH_OVERHEAD_SEC`.
    Verified against the datalogs' MFC-8 toggling (see that constant).

    Raises `IndexError` on fewer than two params and `ValueError` on a
    non-numeric one, like `wait_duration_seconds`; `build_timeline` treats such
    a row as contributing nothing rather than refusing the recipe.
    """
    count, half_period = float(cmd.params[0]), float(cmd.params[1])
    return count * (2.0 * half_period + PULSE_SWITCH_OVERHEAD_SEC)


def soak_setpoint(cmd: RuncardCommand) -> float:
    """The temperature a `Heater Soak` steps the heater to; `0.0` means heater-off.

    `params[0]` is a setpoint in °C. `Heater Soak,0,--` (and `Heater Soak,0,600`)
    turns the heater off. `Heater Soak,600,--` does not: the HA1P01 datalogs
    show the heater SV stepping to 600 and holding there, and on the
    `NSHA1N012506029`-`036` cards the recipe then ramps back up to 900 for a
    second growth before a final `Heater Soak,0`. Reading that first soak as
    heater-off -- what the vendored reference does -- decayed the trace from
    900 °C while the datalog held 600, then drew the second 900 °C plateau on
    top of a cooled-down heater.

    A missing or non-numeric `params[0]` reads as `0.0`, i.e. off: that is what
    every `Heater Soak` meant before this distinction was drawn, so an odd row
    keeps its old meaning rather than refusing the recipe.
    """
    try:
        return float(cmd.params[0])
    except (IndexError, TypeError, ValueError):
        return 0.0


def build_timeline(commands: List[RuncardCommand]) -> Timeline:
    """Replay `commands` onto the recipe clock.

    A single first-match-wins chain with **no `else`**: an unrecognised command
    contributes nothing, silently. That is how `End` and every row of a mis-fed
    datalog are dropped, and it is what makes `total_time <= 0` a usable "this
    file is not a recipe" test.

    `Pumping` and `Pumping Forward` are **recorded but do not advance the
    clock** — see `_PUMP_COMMANDS`. Their parameter is a target pressure, so
    the recipe never says how long the pump-down takes; placing them on the
    timeline would mean inventing a duration.

    Four commands read their value out of different columns, and the asymmetry
    is real rather than a mistake to tidy up:

    - `MFC/PC` takes `params[1]` (the channel name is in `params[0]`);
    - `Accumulation PC*` takes `params[0]`, discarding its second param;
    - `RTV Pressure Ctrl` takes `params[1]`, **not** the pressure in
      `params[0]` — see the note on `rtv_events` below;
    - `Stage Rot` takes `params[0]`.

    `RTV Pressure Ctrl,100 Torr,60` stores `60.0`. `params[0]` is only ever
    `100 Torr` or `1000 Torr` across the dataset — it is the **gauge range** —
    while `params[1]` is the chamber-pressure setpoint the recipe commands.
    The reference renderer states this outright ("third column is the actual
    chamber-pressure setpoint"), which settles a question an earlier note here
    left open: the number is a pressure in Torr, and the row is labelled
    accordingly.

    `Accumulation PC1`/`PC2` are merged into `pc_events` alongside `MFC/PC
    PC-n` rows, so nothing downstream can tell the two sources apart. Both
    spellings are accepted (`Accumulation PC1` and `Accumulation_PC1`) and the
    channel number is every digit left after deleting the literal word
    `Accumulation` — a name with no digits in it yields the channel `"PC-"`.

    Only the **first** zero `Heater Soak` sets `heater_off_t`; later ones are
    ignored entirely. `HADH00` and `HADG37` each carry two (`Heater Soak,0,--`
    then `Heater Soak,0,600`), and the `600` in the second is not a duration
    the clock consumes — the `Wait,Sec,600` after it does that.

    A **nonzero** `Heater Soak` is not heater-off at all: it is an
    instantaneous setpoint step, recorded in `heater_ramps` with a zero
    duration (see `soak_setpoint`). This is a deliberate departure from the
    vendored reference, which treats every first soak as off.
    """
    t = 0.0
    heater_ramps: List[Ramp] = []
    p1_ramps: List[Ramp] = []
    p2_ramps: List[Ramp] = []
    mfc_events: List[NamedEvent] = []
    pc_events: List[NamedEvent] = []
    rtv_events: List[ValueEvent] = []
    spin_events: List[ValueEvent] = []
    pump_events: List[ValueEvent] = []
    heater_off_t: Optional[float] = None
    cur_temp = cur_p1 = cur_p2 = ROOM_TEMP_C

    for cmd in commands:
        name = cmd.name
        if name == "Wait":
            t += wait_duration_seconds(cmd)
        elif name == "Check Status":
            t += CHECK_STATUS_SECONDS
        elif name.startswith("Pulse"):
            # `Pulse`, `Pulse H2Se`, `Pulse O2`: the pulsed growth step. The
            # gas's own MFC is toggled by the controller, not by MFC/PC rows,
            # so nothing lands in mfc_events for it; only the clock moves.
            try:
                t += pulse_duration_seconds(cmd)
            except (IndexError, TypeError, ValueError):
                pass
        elif name == "Heater Ramp":
            tgt, dur = float(cmd.params[0]), float(cmd.params[1])
            heater_ramps.append((t, cur_temp, tgt, dur))
            cur_temp = tgt
        elif name == "P1_Heater Ramp":
            tgt, dur = float(cmd.params[0]), float(cmd.params[1])
            p1_ramps.append((t, cur_p1, tgt, dur))
            cur_p1 = tgt
        elif name == "P2_Heater Ramp":
            tgt, dur = float(cmd.params[0]), float(cmd.params[1])
            p2_ramps.append((t, cur_p2, tgt, dur))
            cur_p2 = tgt
        elif name == "Heater Soak":
            # Two commands under one name, told apart by params[0]. A zero
            # (`Heater Soak,0,--`) is heater **off**: where the synthesized
            # cooldown starts and the hard right edge of the growth window. A
            # nonzero one (`Heater Soak,600,--`) steps the setpoint to that
            # temperature and the heater keeps controlling -- see
            # `soak_setpoint`. It is laid down as a zero-duration ramp, so the
            # trace steps there and a later ramp starts from it.
            setpoint = soak_setpoint(cmd)
            if setpoint:
                heater_ramps.append((t, cur_temp, setpoint, 0.0))
                cur_temp = setpoint
            elif heater_off_t is None:
                heater_off_t = t
        elif name == "MFC/PC":
            channel, val = cmd.params[0], float(cmd.params[1])
            # Routed on the literal "PC-" prefix rather than a regex, so an
            # unrecognised channel name lands in mfc_events instead of
            # vanishing.
            (pc_events if channel.startswith("PC-") else mfc_events).append((t, channel, val))
        elif name == "RTV Pressure Ctrl":
            # params[1], not params[0]: the second column is the *gauge range*
            # ("100 Torr") and the third is the chamber-pressure setpoint the
            # recipe actually commands. Reading the range would report the
            # instrument rather than the process.
            rtv_events.append((t, float(cmd.params[1])))
        elif name.startswith("Accumulation PC") or name.startswith("Accumulation_PC"):
            pc_num = "".join(filter(str.isdigit, name.replace("Accumulation", "")))
            pc_events.append((t, f"PC-{pc_num}", float(cmd.params[0])))
        elif name == "Stage Rot":
            spin_events.append((t, float(cmd.params[0])))
        elif name in _PUMP_COMMANDS:
            # Recorded but not clock-advancing. The parameter is a *target
            # pressure*, not a duration, so the recipe cannot say how long
            # pumping down takes and the timeline cannot place it. Both
            # spellings are kept because the VBBE family writes "Pumping
            # Forward" and the HAD* family writes bare "Pumping"; an earlier
            # port knew only the first and dropped the HAD* ones silently.
            try:
                pump_events.append((t, float(cmd.params[0])))
            except (TypeError, ValueError):
                pass

    return Timeline(
        total_time=t,
        heater_ramps=heater_ramps,
        p1_ramps=p1_ramps,
        p2_ramps=p2_ramps,
        mfc_events=mfc_events,
        pc_events=pc_events,
        rtv_events=rtv_events,
        spin_events=spin_events,
        pump_events=pump_events,
        heater_off_t=heater_off_t,
    )


def build_temp_trace(ramps: List[Ramp], off_t: Optional[float], total: float) -> List[TracePoint]:
    """The main heater's reconstructed temperature, as a sparse vertex list.

    **Not a uniform sample.** A ramp contributes at most two vertices — a
    plateau at its start carrying the previous temperature, and its end at
    `start + duration` carrying the target — and the straight line between them
    *is* the ramp. Only the cooldown is sampled uniformly. Anything wanting a
    temperature part-way up a ramp has to interpolate; the lookups in this
    module deliberately do not (see `_trace_value_at_or_before`).

    The plateau vertex is emitted only when `pts[-1][0] < ts`, i.e. only when
    there is a gap since the last vertex. The ramp's recorded `from_C` is
    unused: the plateau carries the previous vertex's temperature instead,
    which is the same number in a well-formed recipe and the honest one in a
    malformed one.

    **Nothing the heater was told after it went off reaches the trace.** A ramp
    issued at or after `off_t` is dropped, and a ramp still running at `off_t`
    is cut there, with a vertex at the temperature it had reached by then —
    linearly interpolated, as the figure draws the ramp. The cooldown decays
    from that vertex. This is what the reference's evaluator does for a ramp
    issued after heater-off (it reads only keypoints `<= heater_off_t`); for a
    ramp still running it instead pins the ramp's *target* at `off_t`, as if
    the ramp had finished the instant the heater was cut, and this module
    deliberately does not (see docs/reference/README.md). Before v5.23.0 every
    ramp was laid down whole and the decay started from the last one's target:
    the 34 HA1N01 cards with a ramp after their (then heater-off) nonzero soak
    drew a trace that ran backwards through time and read 900 °C where the
    cooldown said 746 °C, and `NSVA1P012512040`, cut 56 % of the way up a
    ramp to 850 °C, reported an 850 °C peak it never reached (about 564 °C).
    Those 34 now read their soak as a setpoint step (`soak_setpoint`), so no
    real card currently issues a ramp after heater-off; the clip is kept so
    one that does cannot run the trace backwards again.

    First cooldown vertex at `off_t + 60`, last exactly at `total`, so the
    final step is short whenever `total - off_t` is not a multiple of 60.

    **With no `Heater Soak` there is no cooldown and no extension to `total`**:
    the trace simply ends at the final ramp vertex, which may be far short of
    the end of the run. That asymmetry with `build_aux_trace`, which always
    extends, is deliberate — a heater with no off command has no modelled decay
    to draw, and drawing a flat hold out to `total` would assert one.

    Vertices are never sorted. Before heater-off, a ramp whose declared
    duration runs past the next ramp's start still emits x-coordinates that go
    backwards, and every consumer here iterates in **list order**, not time
    order. Real runcards pair each ramp with a matching `Wait` so it does not
    arise; adding a sort would change results for files that have never
    produced one.
    """
    pts: List[TracePoint] = [(0.0, ROOM_TEMP_C)]
    for ts, _t0, t1, dur in ramps:
        if off_t is not None and ts >= off_t:
            # Issued once the heater was already off: it never acted.
            continue
        if pts[-1][0] < ts:
            pts.append((ts, pts[-1][1]))
        end = ts + dur
        if off_t is not None and end > off_t:
            # Still climbing (or falling) when the heater was cut. `end > off_t
            # > ts` means `dur > 0`, so the fraction is well defined.
            start_temp = pts[-1][1]
            pts.append((off_t, start_temp + (t1 - start_temp) * (off_t - ts) / dur))
        else:
            pts.append((end, t1))
    if off_t is not None:
        last_t = pts[-1][1]
        if pts[-1][0] < off_t:
            pts.append((off_t, last_t))
        tn = off_t
        while tn < total:
            tn += COOLDOWN_STEP_SEC
            tn = min(tn, total)
            pts.append((tn, ROOM_TEMP_C + (last_t - ROOM_TEMP_C) * math.exp(-(tn - off_t) / COOLDOWN_TAU_SEC)))
    return pts


def build_aux_trace(ramps: List[Ramp], total: float) -> List[TracePoint]:
    """A preheater's reconstructed temperature. Three differences from the main one.

    1. **No ramps gives `[]`**, not `[(0.0, 25.0)]`. A recipe with no
       `P1_Heater Ramp` has no P1 to draw, and an origin-only trace would put a
       25 °C line on the plot for a heater that was never addressed.
    2. **No cooldown.** The preheaters get no off command, so they are modelled
       as holding their last setpoint. Synthesizing a decay here would invent
       the one thing the recipe does not say.
    3. **Always extended to `total`**, so the line reaches the right edge of
       the plot instead of stopping at the last ramp.
    """
    if not ramps:
        return []
    pts: List[TracePoint] = [(0.0, ROOM_TEMP_C)]
    for ts, _t0, t1, dur in ramps:
        if pts[-1][0] < ts:
            pts.append((ts, pts[-1][1]))
        pts.append((ts + dur, t1))
    if pts[-1][0] < total:
        pts.append((total, pts[-1][1]))
    return pts


def _at_peak(temp: float, peak: float) -> bool:
    """Whether `temp` *is* the peak, to float tolerance. Not a band — see `_PEAK_TOL_C`."""
    return abs(temp - peak) < _PEAK_TOL_C


def find_growth_window(temp_trace: List[TracePoint], heater_off_t: Optional[float]) -> GrowthWindow:
    """Exactly the stretch at the peak: the plateau, not an approximation of it.

    Growth **begins** the instant the trace first reaches `peak_temp_c`, and
    **ends** the instant it next leaves it. Both edges are equality tests
    against the peak (within float tolerance), never a band.

    A band is what this used to be — every vertex within 5 °C of the peak — and
    it is a defect, not a simplification. The reference renderer records it as
    "a previously-fixed bug [that] must not be reintroduced", and this port
    reintroduced it by carrying an older ancestor forward. A 5 °C band starts
    the window early on any ramp that passes through `peak - 5` on its way up,
    which on a slow final ramp is minutes of heating counted as growth.

    The end is **the earlier of two things**, which is the other half of what
    the band got wrong:

    - `heater_off_t`, where the heater goes passive, and
    - the start of a commanded **ramp-down** — a deliberate step to a lower
      temperature while the heater is still on.

    Growth does not continue through a ramp-down merely because nothing has
    said "off" yet. A ramp-down's vertices are `(ramp_start, old_target)` then
    `(ramp_end, new_target)`, so the moment growth ends is the *first* of that
    pair: the last instant still at peak.

    `peak > _AMBIENT_FLOOR_C` gates the whole thing, so a recipe that never
    heats reports no window rather than a "growth window" at room temperature.

    Only ever called with a `build_temp_trace` result, which is never empty. An
    empty list would raise `ValueError` out of `max()`; `build_aux_trace`
    output, which *can* be empty, must not be passed here.
    """
    peak = max(temp for _t, temp in temp_trace)
    if peak <= _AMBIENT_FLOOR_C:
        return GrowthWindow(start_s=None, end_s=None, peak_temp_c=peak)

    start_s = next((t for t, temp in temp_trace if _at_peak(temp, peak)), None)
    if start_s is None:
        return GrowthWindow(start_s=None, end_s=None, peak_temp_c=peak)

    # The first vertex pair after the start where the trace steps off the peak.
    # Taking `t0` (still at peak) rather than `t1` is what keeps a commanded
    # ramp-down out of the window instead of counting its whole descent.
    at_or_after = [(t, temp) for t, temp in temp_trace if t >= start_s]
    ramp_down_t = next(
        (
            t0
            for (t0, temp0), (_t1, temp1) in zip(at_or_after, at_or_after[1:])
            if _at_peak(temp0, peak) and not _at_peak(temp1, peak)
        ),
        None,
    )

    candidates = [t for t in (heater_off_t, ramp_down_t) if t is not None]
    end_s = min(candidates) if candidates else temp_trace[-1][0]

    # An end before the start is not a short window, it is no window. A
    # `build_temp_trace` result no longer produces one -- it stops every ramp
    # at heater-off, so the peak is never first reached after it -- but this
    # function takes any trace, and an inverted window must never reach a
    # caller as a negative duration.
    if end_s < start_s:
        return GrowthWindow(start_s=None, end_s=None, peak_temp_c=peak)

    return GrowthWindow(start_s=start_s, end_s=end_s, peak_temp_c=peak)


def build_profile(commands: List[RuncardCommand]) -> RuncardProfile:
    """Commands in, everything the page reads out. The single public entry point.

    The order is fixed: the timeline first because both traces need its clock,
    the main trace before the window because the window is cut out of it, and
    the preheater traces with no `off_t` argument at all — they have no
    cooldown to place.
    """
    timeline = build_timeline(commands)
    temp_trace = build_temp_trace(timeline.heater_ramps, timeline.heater_off_t, timeline.total_time)
    p1_trace = build_aux_trace(timeline.p1_ramps, timeline.total_time)
    p2_trace = build_aux_trace(timeline.p2_ramps, timeline.total_time)
    growth = find_growth_window(temp_trace, timeline.heater_off_t)
    return RuncardProfile(
        timeline=timeline,
        temp_trace=temp_trace,
        p1_trace=p1_trace,
        p2_trace=p2_trace,
        growth=growth,
    )


def get_species(channel_name: str) -> str:
    """The gas a channel name names: its last whitespace-separated token.

    `"MFC-8 H2Se"` gives `"H2Se"`. A single-token name passes through unchanged,
    which is what makes `"PC-1"` answer `"PC-1"` rather than something that
    looks like a species — the pressure controllers carry no gas and must not
    be reachable through a species lookup.

    The *last* token, not the second: `"MFC-3 O2 high"` gives `"high"`. No
    validation, so a malformed channel name yields garbage rather than an
    error; the four species in this dataset are Ar, O2, H2 and H2Se.
    """
    parts = channel_name.split()
    return parts[-1] if len(parts) >= 2 else channel_name


def _first_channel_for_species(events: List[NamedEvent], species: str) -> Optional[str]:
    """The earliest-touched channel carrying `species`, or None.

    First-appearance order, not channel number. It matters when a recipe runs
    one gas on several lines: VBBE00 has Ar on MFC-1, 2, 4 and 5, so a species
    lookup for Ar reports MFC-1 (first event at t=10) alone and says nothing
    about the 170 and 70 sccm carrier flows on MFC-4 and MFC-5. A per-species
    total would be the wrong fix — these are different lines into different
    parts of the tube, and summing them would invent a flow nobody set.
    """
    seen: List[str] = []
    for _t, name, _v in events:
        if name not in seen:
            seen.append(name)
    for name in seen:
        if get_species(name) == species:
            return name
    return None


def _named_event_value_at_or_before(events: List[NamedEvent], name: str, t: float) -> Optional[float]:
    """The last value channel `name` was set to at or before `t`.

    Zero-order hold, last-write-wins, `<=` inclusive: a setpoint stands until
    the next command for that channel. A linear scan of the whole list rather
    than a bisect — these lists are tens of entries, and the scan is correct
    for the duplicate timestamps that a run of simultaneous commands produces
    where a bisect would need a tiebreak rule.

    It cannot tell an `Accumulation PC1`-sourced entry from an `MFC/PC PC-1`
    one; `build_timeline` merges them deliberately.
    """
    value = None
    for et, en, ev in events:
        if en == name and et <= t:
            value = ev
    return value


def _event_value_at_or_before(events: List[ValueEvent], t: float) -> Optional[float]:
    """`_named_event_value_at_or_before` for the two single-channel rows."""
    value = None
    for et, ev in events:
        if et <= t:
            value = ev
    return value


def _trace_value_at_or_before(points: List[TracePoint], t: float) -> Optional[float]:
    """The last trace vertex at or before `t`. A **step** lookup, not interpolation.

    So a `t` part-way up a ramp reads the pre-ramp plateau while the figure
    draws that same stretch as a rising line, and the two disagree. In every
    example file the growth midpoint is well past every preheater ramp, so they
    agree there — but the step semantics are what the numbers on the page have
    always been, and interpolating would quietly change every historical value.
    """
    value = None
    for pt, pv in points:
        if pt <= t:
            value = pv
    return value


def species_value_at_growth_mid(profile: RuncardProfile, species: str) -> Optional[float]:
    """The setpoint of whichever channel first carried `species`, at the growth midpoint.

    `None` on four distinct paths, which the caller cannot tell apart and does
    not need to: no growth window; the species is not in this recipe; the
    channel's first command comes after the midpoint; or the value fails the
    strict `> 0.01` floor. That last one folds "explicitly closed" and "idling
    at purge flow" together with "absent", which is the right answer for a
    column headed with a gas name — none of the three was feeding the growth.

    Searches `mfc_events` only, so `PC-1`/`PC-2` are unreachable here by
    construction.
    """
    mid = profile.growth.mid_s
    if mid is None:
        return None
    channel = _first_channel_for_species(profile.timeline.mfc_events, species)
    if channel is None:
        return None
    value = _named_event_value_at_or_before(profile.timeline.mfc_events, channel, mid)
    return value if value is not None and value > _MFC_ON_FLOOR else None


def channel_value_at_growth_mid(profile: RuncardProfile, channel_name: str) -> Optional[float]:
    """The setpoint of one **named** channel at the growth midpoint.

    Where `species_value_at_growth_mid` answers "what was the Ar doing", this
    answers "what was MFC-4 Ar doing" — which is what a per-channel summary
    needs, since a species lookup reports only the earliest-touched line.

    Routed to `pc_events` or `mfc_events` by the same literal `"PC-"` prefix
    `build_timeline` used to split them, so the routing rule has one home; each
    side keeps its own floor (strictly above 0.01 sccm for an MFC, strictly
    above zero for a controller).
    """
    mid = profile.growth.mid_s
    if mid is None:
        return None
    if channel_name.startswith("PC-"):
        value = _named_event_value_at_or_before(profile.timeline.pc_events, channel_name, mid)
        floor = _ZERO_FLOOR
    else:
        value = _named_event_value_at_or_before(profile.timeline.mfc_events, channel_name, mid)
        floor = _MFC_ON_FLOOR
    return value if value is not None and value > floor else None


def fixed_channel_value_at_growth_mid(profile: RuncardProfile, channel_id: str) -> Optional[float]:
    """The value of one of the seven non-gas channels at the growth midpoint.

    Accepts exactly `Heater`, `P1`, `P2`, `PC-1`, `PC-2`, `RTV`, `Spin`.
    Anything else **raises** rather than returning `None`, because a typo in a
    channel id is a programming error and a silent `None` would show as an
    empty cell indistinguishable from a channel the recipe never used.

    Three asymmetries, all deliberate:

    - **`Heater` ignores the midpoint entirely** and returns the run-wide peak.
      It is the plateau temperature a grower quotes for the run, and sampling
      the trace at the midpoint would return the same number on a well-formed
      recipe and a subtly different one on a ragged trace.
    - **`P1`/`P2` have no floor**, so a genuine 0 °C reads as `0.0`, while a
      zero on PC/RTV/Spin reads as `None`. Those three are off when they are
      zero; a preheater at 0 °C is a reading.
    - The **`mid is None` gate runs before the dispatch**, so an unknown
      `channel_id` on a profile with no growth window returns `None` silently
      instead of raising. Reproduced as-is; it means the raise cannot be relied
      on to catch a typo on every file.

    Mind that `PC-1`/`PC-2` (process pressure controllers, hyphenated) and
    `P1`/`P2` (preheaters, not hyphenated) are four different physical
    channels. Unrelatedly, the datalog column for the *command* `PC-1` is
    `P1 SV`, which is the same collision one layer down.
    """
    mid = profile.growth.mid_s
    if mid is None:
        return None
    tl = profile.timeline
    if channel_id == "Heater":
        return profile.growth.peak_temp_c
    if channel_id == "P1":
        return _trace_value_at_or_before(profile.p1_trace, mid)
    if channel_id == "P2":
        return _trace_value_at_or_before(profile.p2_trace, mid)
    if channel_id in ("PC-1", "PC-2"):
        value = _named_event_value_at_or_before(tl.pc_events, channel_id, mid)
        return value if value is not None and value > _ZERO_FLOOR else None
    if channel_id == "RTV":
        value = _event_value_at_or_before(tl.rtv_events, mid)
        return value if value is not None and value > _ZERO_FLOOR else None
    if channel_id == "Spin":
        value = _event_value_at_or_before(tl.spin_events, mid)
        return value if value is not None and value > _ZERO_FLOOR else None
    raise ValueError(f"Unknown fixed channel: {channel_id!r}")
