"""
Which runcard a datalog run was programmed from: replay each recipe against the log.

Most of a tool's datalogs arrive untagged -- the controller names them by a
timestamp and nothing else -- while the runcard that drove each run sits in a
folder beside them. This module reads both and says which card produced which
log, so the Datalog page can offer the name instead of the operator working it
out from memory. The method and the numbers behind every constant are in
[docs/Runcard_Matching.md](../../../docs/Runcard_Matching.md); what follows is
what the code has to get right.

**Both sides are reduced to the same thing: blocks.** A block is the set of
setpoint changes applied together, the wait that follows, and the heater SV at
the end of that wait. A runcard yields them by replaying its commands; a
datalog yields them from the SV columns it logs at 1 Hz.

**The clock is a *wait clock*, and it is not the recipe clock.** Only rows the
controller spent in `Wait` advance it. Pumping, Check Status, Check PC and
Stage Pos last as long as the chamber takes, which the runcard does not say --
`modules.runcard`'s timeline charges Check Status a nominal 10 s, and that is
right for drawing a recipe and wrong here, where the log shows the same step
taking one second. Counting only waits on both sides is what lets a card line
up against a log without any time warping. Do not "fix" this to use
`growth_window.build_timeline`.

**Blocks are aligned, not compared by position.** A small DP substitutes,
skips or merges blocks, so an operator who changed one wait on the tool costs
one mismatch instead of shifting everything after it. The first version of this
integrated the difference between the two timelines and could not tell a
card whose only difference was the first purge wait (60 s vs 300 s) from one
that differed everywhere.

**Content narrows it to a family; order and exclusivity pick the member.**
Consecutive growth cards are routinely identical (HADH75..HADH79 differ in no
step), so content alone cannot choose between them. `suggest_runcards` adds
three things, all validated against 200 already-tagged HA1P01 runs:

- a *save-time prior*: about a third of cards are saved seconds before their
  run starts, and a card saved long after its run was edited later;
- *exclusivity*: a growth card belongs to one run (the common recipes --
  CLEANING-1..5 and the other files at the runcard folder's top level -- to
  any number), solved as a min-cost bipartite matching;
- *order*: within a wafer series (HADH, HADG, ...) card numbers never went
  backwards in time across those 200 runs, so interchangeable cards are put
  back in run order, and a card outside the numbers of the tagged runs around
  it is penalised.

Pure: no Streamlit, no filesystem. The page reads files through
`io.runcard_index` and caches there.
"""

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from modules.runcard.processing.growth_window import soak_value

TIME_FORMAT = "%Y/%m/%d %H:%M:%S"

MFC_CHANNELS = tuple(f"MFC-{i}" for i in range(1, 9))
PC_CHANNELS = ("P1", "P2", "P3")
ROTATION = "ROT"
GAUGE = "GAUGE"
PULSE = "PULSE"
STEP_CHANNELS = MFC_CHANNELS + PC_CHANNELS + (ROTATION,)

#: The datalog columns each channel is read from. ``651C Gauge`` is the range
#: label (``"100 Torr"``), which is the only trace a `RTV Pressure Ctrl` command
#: leaves: the controller logs no pressure setpoint, and ``651C Pre`` reads 50
#: whenever the gauge is on its 1000 Torr range, so the reading cannot stand in
#: for it.
DATALOG_COLUMNS = {
    **{ch: f"{ch} SV" for ch in MFC_CHANNELS},
    **{ch: f"{ch} SV" for ch in PC_CHANNELS},
    ROTATION: "Stage Rot",
}
NEEDED_COLUMNS = ("Time", "Program", "651C Gauge", "Heater SV", "Heater PV") + tuple(DATALOG_COLUMNS.values())

#: Rows the controller writes between runs, or before a program is loaded.
IDLE_PROGRAMS = ("--", "")
#: A logging gap longer than this splits a file into two runs.
RUN_GAP_S = 300.0
#: Events logged this close together on the wait clock are one block. The SVs
#: of one block of MFC/PC commands land over one to three logged seconds.
GROUP_S = 3.0

#: Relative-difference floors: an MFC set to 0.01 against one set to 0 is a
#: full mismatch, a PC at 150 vs 151 Torr is not.
_FLOOR = {**{ch: 0.01 for ch in MFC_CHANNELS}, **{ch: 1.0 for ch in PC_CHANNELS}, ROTATION: 1.0}
#: Every run ends with these two at 500 sccm (vent and refill), and the next run
#: often starts by switching them off by hand before its first command. That is
#: not a recipe step; see `run_steps`.
_VENT_CHANNELS = ("MFC-4", "MFC-6")
#: MFCs whose 0.01 sccm setpoint the controller logs as 0 -- below resolution,
#: so a card's 0.01 is rounded to 0 on replay (`card_blocks`). This tuple is
#: only the fallback for a caller with no log in hand: `run_steps` derives the
#: set from each log (`RunSteps.coarse`), because the set is not fixed. Through
#: 2026-09 MFC-3 (O2) and MFC-8 (H2Se) logged their 0.01; from 2026-10 the
#: controller logs MFC-8's as 0 too (0 of 103 October logs show it, 101 of 137
#: September ones did). With MFC-8 hard-coded as fine, every October card's two
#: MFC-8 0.01 steps cost 1.0 each -- a flat 2.0 on every growth run, the
#: "medium" ceiling, with the real differences buried under it (v5.21.0).
_COARSE_CHANNELS = ("MFC-1", "MFC-2", "MFC-4", "MFC-5", "MFC-6", "MFC-7")

#: A logged setpoint strictly inside (0, this) is a sub-resolution value the
#: controller *can* log for that channel; a channel never seen there is coarse.
_SUB_RESOLUTION_SCCM = 0.05

_MFC_PARAM_RE = re.compile(r"^(MFC-\d)")
_GAUGE_RE = re.compile(r"\s*(\d+)")


# ------------------------------------------------------------------ data types
@dataclass(frozen=True)
class Block:
    """Setpoint changes applied together, and what follows until the next ones.

    ``changes`` maps a channel to its new value. After a merge a channel set in
    both halves holds a ``(first, second)`` tuple, so a set-then-reset (H2Se
    on, then off) does not collapse into "no change".
    """

    t: float
    changes: Mapping[str, object]
    wait: float
    heater: Optional[float]


@dataclass(frozen=True)
class RunSteps:
    """One logged run, reduced to what a runcard can be compared against.

    ``coarse`` names the MFCs this log never shows at a sub-resolution
    setpoint, so a card's 0.01 on them is replayed as 0 (see
    `_COARSE_CHANNELS`). Read off the log rather than fixed, because which
    channels log their 0.01 has changed under the matcher once already.
    """

    blocks: Tuple[Block, ...]
    initial: Mapping[str, Optional[float]]
    ended: bool
    total: float
    start: pd.Timestamp
    end: pd.Timestamp
    coarse: Tuple[str, ...] = _COARSE_CHANNELS


@dataclass(frozen=True)
class Runcard:
    """One candidate recipe: its commands up to `End`, and what is known about the file."""

    name: str
    path: str
    mtime: float
    commands: Tuple[Tuple[str, str, str], ...]
    common: bool


@dataclass(frozen=True)
class Suggestion:
    """The runcard proposed for one run, and why."""

    path: str
    tag: str
    card_name: str
    card_path: str
    cost: float
    confidence: str
    differences: Tuple[str, ...]
    alternatives: Tuple[str, ...]
    notes: Tuple[str, ...]


# ------------------------------------------------------------------ small helpers
def _num(value) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _gauge(label) -> Optional[float]:
    m = _GAUGE_RE.match(str(label)) if label is not None else None
    return float(m.group(1)) if m else None


def _clock(t: float) -> str:
    """Wait-clock seconds as ``m:ss`` -- the unit the differences are read in."""
    t = int(round(t))
    return f"{t // 60}:{t % 60:02d}"


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, tuple):
        return " then ".join(_fmt(x) for x in v)
    return f"{v:g}"


# ------------------------------------------------------------------ datalog side
def _longest_stretch(programs: Sequence[str], times: Sequence[pd.Timestamp]) -> Tuple[int, int]:
    """Index range of the longest non-idle stretch: the run, in a file that may hold more.

    Per-run files hold one stretch plus a trailing idle row; the continuous daily
    logs the tool wrote before April 2025 hold several.
    """
    best, i0 = (0, 0), None
    for i, p in enumerate(programs):
        idle = p in IDLE_PROGRAMS
        gap = i > 0 and (times[i] - times[i - 1]).total_seconds() > RUN_GAP_S
        if i0 is not None and (idle or gap):
            if (times[i - 1] - times[i0]) > (times[best[1] - 1] - times[best[0]] if best[1] else pd.Timedelta(-1)):
                best = (i0, i)
            i0 = None
        if i0 is None and not idle:
            i0 = i
    if i0 is not None:
        n = len(programs)
        if not best[1] or (times[n - 1] - times[i0]) > (times[best[1] - 1] - times[best[0]]):
            best = (i0, n)
    return best


def run_steps(df: pd.DataFrame) -> Optional[RunSteps]:
    """Reduce a parsed datalog to blocks on the wait clock. None if it holds no run.

    `df` needs `NEEDED_COLUMNS`, with ``Time`` already datetime and the SVs
    numeric -- what `scanner.load_run` returns, or `io.runcard_index.read_run_steps`'s
    slimmer read. The recipe portion ends at the first ``End`` row; everything
    after it is the operator venting by hand.
    """
    if df is None or df.empty or "Program" not in df.columns:
        return None
    df = df.dropna(subset=["Time"])
    programs = df["Program"].fillna("").astype(str).tolist()
    times = list(df["Time"])
    if not programs:
        return None
    i0, i1 = _longest_stretch(programs, times)
    if i1 - i0 < 2:
        return None
    sub = df.iloc[i0:i1]
    prog = programs[i0:i1]
    tt = times[i0:i1]
    n = len(prog)
    end_i = next((i for i, p in enumerate(prog) if p == "End"), None)
    ended = end_i is not None
    m = max(end_i if ended else n, 1)

    clock = np.zeros(n)
    c = 0.0
    for i in range(n):
        clock[i] = c
        if i + 1 < n and prog[i] == "Wait":
            dt = (tt[i + 1] - tt[i]).total_seconds()
            if 0 < dt < 600:
                c += dt
    total = float(clock[m - 1])

    cols = {ch: sub[col].tolist() if col in sub.columns else [None] * n for ch, col in DATALOG_COLUMNS.items()}
    gauge = sub["651C Gauge"].tolist() if "651C Gauge" in sub.columns else [None] * n
    hsv = [_num(v) for v in (sub["Heater SV"].tolist() if "Heater SV" in sub.columns else [None] * n)]
    hpv = sub["Heater PV"].iloc[0] if "Heater PV" in sub.columns else None

    initial = {ch: _num(vals[0]) for ch, vals in cols.items()}
    initial[GAUGE] = _gauge(gauge[0])
    # An MFC that never logs a value in (0, 0.05) sccm in this run cannot show a
    # card's 0.01; the replay rounds such setpoints to 0 for it. Judged per
    # log because the controller's behaviour changed between September and
    # October 2026 (MFC-8), and a fixed list was wrong for a month.
    coarse = tuple(
        ch for ch, vals in cols.items()
        if ch in MFC_CHANNELS and not any(
            v is not None and 0 < v < _SUB_RESOLUTION_SCCM for v in (_num(x) for x in vals[:m])
        )
    )
    prev = dict(initial)
    events = []   # (clock, row, channel, value)
    in_pulse = False
    for i in range(1, m):
        if prog[i].startswith("Pulse"):
            if not in_pulse:
                events.append((clock[i], i, PULSE, 1.0))
            in_pulse = True
            continue
        if in_pulse:
            # A pulse toggles its gas many times; resync after it rather than
            # emit every toggle, since the card says only "Pulse".
            in_pulse = False
            for ch, vals in cols.items():
                v = _num(vals[i])
                if v is not None:
                    prev[ch] = v
            continue
        for ch, vals in cols.items():
            v = _num(vals[i])
            if v is None:
                continue
            p = prev.get(ch)
            if p is not None and abs(v - p) > 1e-9:
                if ch in _VENT_CHANNELS and v == 0 and p >= 300 and clock[i] < 120:
                    initial[ch] = 0.0      # the last run's vent flow shut off by hand
                else:
                    events.append((clock[i], i, ch, v))
            prev[ch] = v
        g = _gauge(gauge[i])
        if g is not None and g != prev.get(GAUGE):
            events.append((clock[i], i, GAUGE, g))
            prev[GAUGE] = g
    events.sort(key=lambda e: (e[0], e[1]))

    grouped = []   # [t, t_last, row, changes]
    for t, i, ch, v in events:
        if grouped and t - grouped[-1][1] <= GROUP_S:
            grouped[-1][1] = t
            grouped[-1][3][ch] = v
        else:
            grouped.append([t, t, i, {ch: v}])
    blocks = []
    for k, (t, _t_last, _i, changes) in enumerate(grouped):
        if k + 1 < len(grouped):
            wait = grouped[k + 1][0] - t
            heater = hsv[max(grouped[k + 1][2] - 1, 0)]
        else:
            wait = total - t
            heater = hsv[m - 1]
        blocks.append(Block(t=float(t), changes=dict(changes), wait=float(wait), heater=heater))
    initial["HEAT_SV"] = hsv[0]
    initial["HEAT_PV"] = _num(hpv)
    return RunSteps(blocks=tuple(blocks), initial=initial, ended=ended, total=total,
                    start=pd.Timestamp(tt[0]), end=pd.Timestamp(tt[-1]), coarse=coarse)


# ------------------------------------------------------------------ runcard side
def _heater_at(ramps, stale: Optional[float], pv0: Optional[float]):
    """Heater SV as a function of wait-clock time, from the card's ramps.

    A ramp runs in the background (it does not advance the clock) from wherever
    the heater is to its target. The first one starts from the chamber's actual
    temperature, which is why the log's SV jumps from the previous run's stale
    target down to the PV before climbing.
    """
    segments = []
    for t0, target, dur in ramps:
        if segments:
            start = _eval_segments(segments, t0, stale)
        else:
            start = pv0 if pv0 is not None else stale
        segments.append((t0, start, target, dur))
    return lambda tq: _eval_segments(segments, tq, stale)


def _eval_segments(segments, tq, stale):
    current = None
    for seg in segments:
        if tq >= seg[0]:
            current = seg
        else:
            break
    if current is None:
        return stale
    t0, start, target, dur = current
    if start is None or dur <= 0:
        return target
    return start + (target - start) * min(max((tq - t0) / dur, 0.0), 1.0)


def card_blocks(commands: Sequence[Tuple[str, str, str]],
                initial: Mapping[str, Optional[float]],
                coarse: Optional[Sequence[str]] = None) -> Tuple[Tuple[Block, ...], float]:
    """Replay a card's commands from the log's starting state. Returns (blocks, total).

    Starting from the log's own state is what makes a command that sets a
    channel to the value it already has a no-op on both sides; replaying from
    zero would charge every such command as a step the log never shows.

    `coarse` is the log's `RunSteps.coarse`: the MFCs on which a sub-resolution
    setpoint is replayed as 0. Defaults to the fixed `_COARSE_CHANNELS` for a
    caller without a log.
    """
    state = {k: v for k, v in initial.items() if not k.startswith("HEAT")}
    coarse_channels = _COARSE_CHANNELS if coarse is None else tuple(coarse)
    t = 0.0
    events = []
    ramps = []

    def set_value(ch, v):
        if ch in coarse_channels and abs(v) < _SUB_RESOLUTION_SCCM:
            v = 0.0
        cur = state.get(ch)
        if cur is None or abs(cur - v) > 1e-9:
            events.append((t, ch, v))
        state[ch] = v

    for name, a, b in commands:
        if name == "Wait":
            d = _num(b)
            if d is not None:
                t += d * (60 if a.lower().startswith("min") else 1)
        elif name.startswith("MFC") and "/PC" in name:
            v = _num(b)
            if v is None:
                continue
            m = _MFC_PARAM_RE.match(a)
            if m:
                set_value(m.group(1), v)
            elif a.startswith("PC-") and a[3:4].isdigit():
                set_value("P" + a[3:4], v)
        elif name in ("Accumulation PC1", "Accumulation PC2"):
            v = _num(a)
            if v is not None:
                set_value("P" + name[-1], v)
        elif name == "Stage Rot":
            v = _num(a)
            if v is not None:
                set_value(ROTATION, v)
        elif name == "Heater Ramp":
            target, dur = _num(a), _num(b)
            if target is not None:
                ramps.append((t, target, dur or 0.0))
        elif name == "Heater Soak":
            # A nonzero soak steps the SV there at once, as the datalog shows
            # and growth_window reads it (v5.21.0); dropping it charged every
            # CLEANING-5 run 1.4 against its own card ("log 800, card 900").
            # Zero is heater-off, which the wait clock does not model.
            target = soak_value(a)
            if target:
                ramps.append((t, target, 0.0))
        elif name == "RTV Pressure Ctrl":
            g = _gauge(a)
            if g is not None:
                set_value(GAUGE, g)
        elif name.startswith("Pulse"):
            events.append((t, PULSE, 1.0))
    total = t

    grouped = []
    for et, ch, v in events:
        if grouped and et == grouped[-1][0]:
            grouped[-1][1][ch] = v
        else:
            grouped.append([et, {ch: v}])
    heater = _heater_at(ramps, initial.get("HEAT_SV"), initial.get("HEAT_PV"))
    blocks = []
    for k, (et, changes) in enumerate(grouped):
        nxt = grouped[k + 1][0] if k + 1 < len(grouped) else None
        wait = (nxt if nxt is not None else total) - et
        h = heater(nxt - 1e-6) if nxt is not None else heater(total)
        blocks.append(Block(t=float(et), changes=dict(changes), wait=float(wait), heater=h))
    return tuple(blocks), total


# ------------------------------------------------------------------ costs
# Units are "about one differing step". A step present on one side only is
# structural (1.0); a changed value on a step both sides have is an edit of the
# same recipe (at most 0.7) -- the asymmetry is what keeps an edited cleaning
# recipe closer to its own older version than to a different recipe.
MERGE_COST = 0.5


def _cost_channel(ch, x, y) -> float:
    if x is None or y is None:
        return 1.0
    if isinstance(x, tuple) or isinstance(y, tuple):
        return 0.0 if x == y else 1.0
    if ch in (GAUGE, PULSE):
        return 0.0 if x == y else 0.7
    d = abs(x - y) / max(abs(x), abs(y), _FLOOR.get(ch, 1.0))
    return 0.0 if d < 1e-3 else 0.2 + 0.5 * min(1.0, 3 * d)


def _cost_wait(w1, w2, last=False, aborted=False) -> float:
    if last and aborted:
        return 0.0 if w1 <= w2 + 10 else _cost_wait(w1, w2)
    d = abs(w1 - w2)
    if d <= max(6.0, 0.02 * max(w1, w2)):
        return 0.0
    c = 0.2 + 0.5 * min(1.0, d / max(w1, w2, 30.0))
    return c * (0.5 if last else 1.0)


def _cost_heater(x, y, last=False, aborted=False) -> float:
    if x is None or y is None or (last and aborted):
        return 0.0
    d = abs(x - y)
    return 0.0 if d <= 6 else 0.2 + 0.5 * min(1.0, d / 50.0)


def _cost_sub(a: Block, b: Block, last=False, aborted=False) -> float:
    keys = set(a.changes) | set(b.changes)
    c = sum(_cost_channel(k, a.changes.get(k), b.changes.get(k)) for k in keys)
    return c + _cost_wait(a.wait, b.wait, last, aborted) + _cost_heater(a.heater, b.heater, last, aborted)


def _cost_gap(blk: Block) -> float:
    return 0.5 + 1.0 * len(blk.changes)


def _merge(b1: Block, b2: Block) -> Block:
    changes = dict(b1.changes)
    for k, v in b2.changes.items():
        changes[k] = (changes[k], v) if k in changes else v
    return Block(t=b1.t, changes=changes, wait=b1.wait + b2.wait, heater=b2.heater)


def _is_last(i: int, j: int, n: int, m: int, aborted: bool) -> bool:
    """Whether a move ending at (i, j) pairs the log's final block with its card counterpart.

    A run that ended must also be at the card's end; an aborted one need not --
    its final block is wherever the run was stopped.
    """
    return i == n and (aborted or j == m)


def align(log: Sequence[Block], card: Sequence[Block], aborted: bool = False, want_path: bool = False):
    """Cost of explaining `log` by `card`, and optionally the alignment behind it.

    Five moves: substitute, a log-only block, a card-only block, and two
    card blocks read as one log block (or the reverse) -- the last two because
    a set that is a no-op on one side splits or joins blocks. An aborted run
    stops early, so card blocks after the log's last one cost nothing.

    "Last" (the halved / forgiven wait and heater on the final pair) is
    `_is_last`: for a run that ended it is the final log block against the
    final card block, but an aborted run's final log block lines up with a
    *middle* card block, and its wait was cut short by the abort. Keying it on
    ``j == m`` as well meant the forgiveness never applied: a run stopped 40 s
    into a 120 s hold was charged the 80 s it never got to wait (v5.23.0). The
    merge moves pass the same flag, because they can end the path too.
    """
    n, m = len(log), len(card)
    inf = float("inf")
    D = [[inf] * (m + 1) for _ in range(n + 1)]
    back = {}
    D[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best, arg = inf, None
            if i and j:
                c = D[i - 1][j - 1] + _cost_sub(log[i - 1], card[j - 1], _is_last(i, j, n, m, aborted), aborted)
                if c < best:
                    best, arg = c, ("sub", i - 1, j - 1)
            if i:
                c = D[i - 1][j] + _cost_gap(log[i - 1])
                if c < best:
                    best, arg = c, ("log-only", i - 1, j)
            if j:
                c = D[i][j - 1] + (0.0 if aborted and i == n else _cost_gap(card[j - 1]))
                if c < best:
                    best, arg = c, ("card-only", i, j - 1)
            if i and j >= 2:
                c = D[i - 1][j - 2] + MERGE_COST + _cost_sub(
                    log[i - 1], _merge(card[j - 2], card[j - 1]), _is_last(i, j, n, m, aborted), aborted)
                if c < best:
                    best, arg = c, ("merge-card", i - 1, j - 2)
            if i >= 2 and j:
                c = D[i - 2][j - 1] + MERGE_COST + _cost_sub(
                    _merge(log[i - 2], log[i - 1]), card[j - 1], _is_last(i, j, n, m, aborted), aborted)
                if c < best:
                    best, arg = c, ("merge-log", i - 2, j - 1)
            D[i][j] = best
            if want_path:
                back[(i, j)] = arg
    if not want_path:
        return D[n][m], None
    path, i, j = [], n, m
    while (i, j) != (0, 0):
        op, pi, pj = back[(i, j)]
        path.append((op, i, j))
        i, j = pi, pj
    return D[n][m], path[::-1]


def explain(log: Sequence[Block], card: Sequence[Block], path, aborted: bool = False) -> List[str]:
    """The differences along an alignment, as short sentences in recipe (wait-clock) time."""
    out = []
    for op, i, j in path:
        if op == "sub":
            a, b = log[i - 1], card[j - 1]
            last = _is_last(i, j, len(log), len(card), aborted)
            for k in sorted(set(a.changes) | set(b.changes)):
                x, y = a.changes.get(k), b.changes.get(k)
                if _cost_channel(k, x, y) > 0:
                    out.append(f"{_clock(b.t)} {k}: log {_fmt(x)}, card {_fmt(y)}")
            if _cost_wait(a.wait, b.wait, last, aborted) > 0:
                out.append(f"{_clock(b.t)} wait: log {a.wait:.0f} s, card {b.wait:.0f} s")
            if _cost_heater(a.heater, b.heater, last, aborted) > 0:
                out.append(f"{_clock(b.t)} heater SV: log {_fmt(a.heater)}, card {_fmt(round(b.heater, 1))}")
        elif op == "log-only":
            blk = log[i - 1]
            out.append(f"{_clock(blk.t)} only in log: " + ", ".join(f"{k} {_fmt(v)}" for k, v in blk.changes.items()))
        elif op == "card-only" and not (aborted and i == len(log)):
            blk = card[j - 1]
            out.append(f"{_clock(blk.t)} only in card: " + ", ".join(f"{k} {_fmt(v)}" for k, v in blk.changes.items()))
        elif op in ("merge-card", "merge-log"):
            out.append(f"{_clock(card[j - 1].t)} two steps on one side are one on the other")
    return out


def content_cost(run: RunSteps, card: Runcard) -> float:
    """How far `card` is from explaining `run`: about one per differing step, 0 for a replay."""
    blocks, _total = card_blocks(card.commands, run.initial, run.coarse)
    if not blocks:
        return float("inf")
    return align(run.blocks, blocks, aborted=not run.ended)[0]


def differences(run: RunSteps, card: Runcard) -> List[str]:
    blocks, _total = card_blocks(card.commands, run.initial, run.coarse)
    _cost, path = align(run.blocks, blocks, aborted=not run.ended, want_path=True)
    return explain(run.blocks, blocks, path, aborted=not run.ended)


# ------------------------------------------------------------------ assignment
#: Non-common cards are only considered when saved within this window of the
#: run's start. The widest gap among the 200 tagged runs was 6.6 days before
#: and 2.3 days after (edited later).
WINDOW_BEFORE = pd.Timedelta(days=14)
WINDOW_AFTER = pd.Timedelta(days=7)
DUP_COST = 0.4          # an aborted attempt may share its card with the rerun
RERUN_WINDOW = pd.Timedelta(hours=12)   # a failed attempt's card is free this long after it
ORDER_PENALTY = 1.0     # a card number outside the tagged runs around it
SWAP_TOL = 0.3          # content slack allowed when restoring run order
REFERENCE_MAX = 0.3     # a cleaning log this close to a tagged one is that recipe
_NONE_COST = 50.0

_COMMON_RE = re.compile(r"clean|refill|^test$|bak|oxid|anneal|selen|pc testing", re.I)
_COPY_RE = re.compile(r"複製|copy", re.I)
_CLEAN_TAG_RE = re.compile(r"clean\w*[-_ ]?(\d)$", re.I)
_FAIL_RE = re.compile(r"[_-]+fail$", re.I)


def is_common_name(name: str) -> bool:
    """A reusable recipe by its name alone (cleaning-type files kept in month folders)."""
    return bool(_COMMON_RE.search(name))


def is_copy_name(name: str) -> bool:
    """``HADH84 - 複製``: an unrenamed Explorer copy, never a card anyone ran by that name."""
    return bool(_COPY_RE.search(name))


def base_tag(tag: str) -> str:
    """A tag without its failure suffix, upper-cased: ``HADF22--fail`` -> ``HADF22``."""
    return _FAIL_RE.sub("", tag.strip()).upper()


def cleaning_name(tag: str) -> Optional[str]:
    """``cleanin-4`` / ``Cleaning-4`` -> ``CLEANING-4``; None for a tag that names no number."""
    m = _CLEAN_TAG_RE.search(tag.strip())
    return f"CLEANING-{m.group(1)}" if m else None


def series_of(name: str) -> Tuple[Optional[str], Optional[int]]:
    """Wafer series and number: ``HADH75`` -> ("HADH", 75); serial names by year-month."""
    n = name.upper()
    m = re.match(r"^(HA[A-Z]{2})(\d+)", n)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"^N?S?HA1[NP]01(\d{4,5}?)(\d{3})(?!\d)", n)
    if m:
        return "SER" + m.group(1), int(m.group(2))
    m = re.match(r"^HBR(\d{5})(\d{3})", n)
    if m:
        return "HBR" + m.group(1), int(m.group(2))
    return None, None


def save_prior(run: RunSteps, card: Runcard) -> float:
    """What the card file's save time says, in content-cost units. 0 for common recipes.

    Convex over the first two days so that a batch of cards prepared the night
    before pairs with that morning's runs in order; bounded after, because
    cards are legitimately prepared a week ahead.
    """
    if card.common:
        return 0.0
    saved = pd.Timestamp.fromtimestamp(card.mtime)
    d = (run.start - saved).total_seconds() / 3600.0
    dur = (run.end - run.start).total_seconds() / 3600.0
    if 0 <= d <= 0.1:
        return -1.5                                   # saved as the run was started
    if -dur <= d < 0:
        return -0.5                                   # saved while it was running
    if 0.1 < d <= 48:
        return 0.01 * d + 0.0002 * d * d
    if d > 48:
        return 0.94 + 0.004 * (d - 48)
    return 0.5 + 0.004 * (-d - dur)                   # edited after the run


def _order_penalty(anchors, start, name, aborted) -> float:
    """1.0 when `name`'s number sits outside the tagged runs just before and after `start`.

    Equal numbers are allowed where a card really is used twice: after a
    failed attempt (its rerun), and before one when this run is itself the
    aborted attempt.
    """
    s, k = series_of(name)
    if s is None or s not in anchors:
        return 0.0
    before = [(t, num, failed) for t, num, failed in anchors[s] if t < start]
    after = [(num, failed) for t, num, failed in anchors[s] if t > start]
    if before:
        t, num, failed = before[-1]
        rerun = failed and start - t <= RERUN_WINDOW
        if k < num or (k == num and not rerun):
            return ORDER_PENALTY
    if after:
        num, _failed = after[0]
        if k > num or (k == num and not aborted):
            return ORDER_PENALTY
    return 0.0


def suggest_runcards(runs: Sequence[Tuple[str, RunSteps]],
                     cards: Sequence[Runcard],
                     used: Mapping[str, pd.Timestamp] = None,
                     references: Sequence[Tuple[str, RunSteps]] = (),
                     progress=None) -> Dict[str, Suggestion]:
    """Propose a runcard for each run, jointly. Returns {path: Suggestion}.

    Parameters
    ----------
    runs
        ``(path, steps)`` for the runs to name -- normally every untagged run.
    cards
        The candidate recipes, e.g. from `io.runcard_index.read_runcards`.
    used
        Tags already on *other* runs, with each run's start: those growth cards
        are taken, and they anchor the order check. Common recipes are never taken.
    references
        ``(tag, steps)`` of tagged cleaning runs. The CLEANING-N files are
        edited in place, so a months-old cleaning matches its own old version
        best -- which only a tagged log of that era still records.
    progress
        Optional ``callable(done, total)``.
    """
    used = used or {}
    # A card tagged only on a failed attempt is still free for its rerun
    # (HADF22--fail at 14:15, HADF22 at 16:10) -- but only for a rerun: days
    # later it is a card nobody is going to run again (HADD54_FAIL).
    taken = {base_tag(t) for t in used if not _FAIL_RE.search(t.strip())}
    failed_at = {}
    for t, when in used.items():
        if _FAIL_RE.search(t.strip()) and base_tag(t) not in taken:
            failed_at[base_tag(t)] = when
    anchors: Dict[str, List[Tuple[pd.Timestamp, int, bool]]] = {}
    for tag, when in used.items():
        s, k = series_of(base_tag(tag))
        if s:
            anchors.setdefault(s, []).append((when, k, bool(_FAIL_RE.search(tag.strip()))))
    for s in anchors:
        anchors[s].sort()

    scored = []   # per run: {NAME: (total, content, card)}, best common, all candidates
    for idx, (path, steps) in enumerate(runs):
        per_name, best_common, cands = {}, None, []
        for card in cards:
            if not card.common and not (steps.start - WINDOW_BEFORE <= pd.Timestamp.fromtimestamp(card.mtime)
                                        <= steps.start + WINDOW_AFTER):
                continue
            c = content_cost(steps, card)
            if not math.isfinite(c):
                continue
            c = round(c, 3)
            cands.append((c, card))
            if card.common:
                if best_common is None or c < best_common[0]:
                    best_common = (c, card)
                continue
            key = card.name.upper()
            aborted = not steps.ended
            is_taken = key in taken or (
                key in failed_at and not (pd.Timedelta(0) <= steps.start - failed_at[key] <= RERUN_WINDOW)
            )
            if is_taken and not aborted:
                continue
            total = c + save_prior(steps, card) + _order_penalty(anchors, steps.start, key, aborted)
            if is_taken:
                total += DUP_COST
            if key not in per_name or total < per_name[key][0]:
                per_name[key] = (round(total, 3), c, card)
        scored.append((path, steps, per_name, best_common, sorted(cands, key=lambda x: x[0])))
        if progress:
            progress(idx + 1, len(runs))

    assigned = _solve(scored)
    _repair_order(scored, assigned)
    _retarget_aborted(scored, assigned)
    return _build(scored, assigned, references, taken)


def _solve(scored):
    """Min-cost matching: growth card names have capacity one, everything else is private."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import min_weight_full_bipartite_matching

    names = {}
    for _p, _s, per_name, _bc, _c in scored:
        for key in per_name:
            names.setdefault(key, len(names))
    rows, cols, vals, choice = [], [], [], {}
    base = len(names)
    for i, (_p, _s, per_name, best_common, _c) in enumerate(scored):
        for key, (total, c, card) in per_name.items():
            j = names[key]
            rows.append(i); cols.append(j); vals.append(total + 10.0)
            choice[(i, j)] = (c, card)
        if best_common is not None:
            rows.append(i); cols.append(base + 2 * i); vals.append(best_common[0] + 10.0)
            choice[(i, base + 2 * i)] = best_common
        rows.append(i); cols.append(base + 2 * i + 1); vals.append(_NONE_COST + 10.0)
        choice[(i, base + 2 * i + 1)] = None
    if not scored:
        return {}
    matrix = csr_matrix((vals, (rows, cols)), shape=(len(scored), base + 2 * len(scored)))
    r_ind, c_ind = min_weight_full_bipartite_matching(matrix)
    return {int(i): choice[(int(i), int(j))] for i, j in zip(r_ind, c_ind)}


def _repair_order(scored, assigned):
    """Swap interchangeable cards so numbers rise with run time inside each series."""
    by_series = {}
    for i, pick in assigned.items():
        if pick and not pick[1].common:
            s, _k = series_of(pick[1].name)
            if s:
                by_series.setdefault(s, []).append(i)
    for members in by_series.values():
        members.sort(key=lambda i: scored[i][1].start)
        changed = True
        while changed:
            changed = False
            for p in range(len(members)):
                for q in range(p + 1, len(members)):
                    x, y = members[p], members[q]
                    kx = series_of(assigned[x][1].name)[1]
                    ky = series_of(assigned[y][1].name)[1]
                    if kx <= ky:
                        continue
                    alt_x = scored[x][2].get(assigned[y][1].name.upper())
                    alt_y = scored[y][2].get(assigned[x][1].name.upper())
                    if not alt_x or not alt_y:
                        continue
                    if alt_x[1] + alt_y[1] <= assigned[x][0] + assigned[y][0] + SWAP_TOL:
                        assigned[x] = (alt_x[1], alt_x[2])
                        assigned[y] = (alt_y[1], alt_y[2])
                        changed = True


def _retarget_aborted(scored, assigned):
    """An aborted attempt takes the card of the run that follows it, when that card fits."""
    order = sorted(assigned, key=lambda i: scored[i][1].start)
    for n, i in enumerate(order):
        steps = scored[i][1]
        pick = assigned[i]
        if steps.ended or not pick or pick[1].common:
            continue
        for j in order[n + 1:n + 3]:
            if (scored[j][1].start - steps.end).total_seconds() > 3 * 3600:
                break
            nxt = assigned[j]
            if nxt and not nxt[1].common:
                alt = scored[i][2].get(nxt[1].name.upper())
                if alt and alt[1] <= pick[0] + SWAP_TOL:
                    assigned[i] = (alt[1], alt[2])
                break


def _build(scored, assigned, references, taken):
    owner = {}
    for i, pick in assigned.items():
        if pick and not pick[1].common:
            owner.setdefault(pick[1].name.upper(), scored[i][0])
    out = {}
    for i, (path, steps, per_name, _bc, cands) in enumerate(scored):
        pick = assigned.get(i)
        if not pick:
            continue
        cost, card = pick
        notes = []
        tag = card.name
        confidence = "high" if cost <= 0.6 else ("medium" if cost <= 2.0 else "low")
        alternatives = []
        for c, other in cands:
            if other.name.upper() == card.name.upper() or c > cost + SWAP_TOL:
                continue
            label = other.name
            holder = owner.get(other.name.upper())
            if holder and holder != path:
                label += " (taken by another run)"
            elif other.name.upper() in taken:
                label += " (already tagged)"
            if label not in alternatives:
                alternatives.append(label)
        free_alts = [a for a in alternatives if "(" not in a]
        if card.common:
            ref = _best_reference(steps, references) if "CLEAN" in card.name.upper() else None
            if ref and ref[0] <= REFERENCE_MAX:
                notes.append(f"same steps as tagged run {ref[1]}")
                if ref[2] != card.name.upper():
                    notes.append(f"closest current file is {card.name} (cost {cost:g}); it was edited since")
                    tag = ref[2]
                if steps.ended:
                    confidence = "high"
        else:
            delta_h = (steps.start - pd.Timestamp.fromtimestamp(card.mtime)).total_seconds() / 3600
            if 0 <= delta_h <= 0.1:
                notes.append("card saved as the run started")
            elif confidence == "high" and free_alts:
                confidence = "medium"
            if not steps.ended:
                tag = f"{card.name}_FAIL"
                notes.append("stopped before the recipe's End")
        if not steps.ended and confidence == "high":
            confidence = "medium"
        out[path] = Suggestion(
            path=path, tag=tag, card_name=card.name, card_path=card.path, cost=cost,
            confidence=confidence, differences=tuple(differences(steps, card)[:8]),
            alternatives=tuple(alternatives[:5]), notes=tuple(notes),
        )
    return out


def _best_reference(steps: RunSteps, references):
    """Closest tagged cleaning log within 30 days: (cost, its tag, CLEANING-N)."""
    best = None
    for tag, ref in references:
        name = cleaning_name(tag)
        if not name or abs((ref.start - steps.start).days) > 30:
            continue
        c, _ = align(steps.blocks, ref.blocks, aborted=not steps.ended)
        if best is None or c < best[0]:
            best = (c, tag, name)
    return best
