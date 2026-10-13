#!/usr/bin/env python3
"""
render_runcard.py — parse a Nexstrom-style MOCVD run card CSV and produce
a recipe-profile diagram (temperature trace + gas/pressure bars).

Usage:
    python render_runcard.py <input.csv> [--out-svg PATH] [--out-png PATH]

Output: SVG + PNG to current dir (or paths given) using the input filename
as the base. e.g. VABD14.csv → VABD14_runcard.svg + VABD14_runcard.png

The script makes no assumptions about gas naming. Any MFC line is plotted
on its own row. The "growth window" is identified automatically as the
high-temperature plateau where the most gas-change events cluster.
"""

import sys
import os
import math
import argparse
from collections import defaultdict


# ---------- Recipe parsing ----------

def parse_runcard(csv_path):
    """Parse a run card CSV.

    Recipe semantics (Nexstrom MOCVD convention):
      - Heater Ramp, target_T, dur_sec: ramp from current T to target_T over
        dur_sec seconds. Runs in the BACKGROUND — does not advance program
        time. Once the ramp completes the heater holds at target_T.
      - Wait, Sec, dur: advances program time by dur seconds.
      - MFC/PC events, RTV Pressure Ctrl, Accumulation PC1, Stage Rot,
        Pumping Forward: instantaneous, happen at the current program time.
      - RTV Pressure Ctrl, "<range> Torr", setpoint: gauge range, then the
        actual chamber-pressure setpoint. Use the third column as the
        chamber-P setpoint.

    Returns a dict with keys:
      total_sec, heater_off_t (or None), temp_keypoints, gas_intervals,
      rtv_intervals, pc1_intervals, mfc_change_events, gas_event_temps
    """
    rows = []
    with open(csv_path) as f:
        for line in f:
            parts = [p.strip() for p in line.strip().split(',')]
            if not parts or parts[0] in ('--', 'End', ''):
                continue
            # Pad to 3 cols
            while len(parts) < 3:
                parts.append('--')
            rows.append(parts)

    # Pass 1: heater profile (background ramps)
    t = 0
    ramp_from = 25.0  # ambient
    ramp_to = 25.0
    ramp_start = 0
    ramp_dur = 0
    heater_off_t = None
    temp_keypoints = [(0, 25.0)]  # (t_sec, T_°C)

    # Secondary heater zones (e.g. precursor-line preheaters). These ramp
    # in the background like the main heater but are independent channels —
    # they don't turn off with 'Heater Soak' unless explicitly commanded.
    p1_ramp_to = 25.0
    p2_ramp_to = 25.0
    p1_temp_keypoints = [(0, 25.0)]
    p2_temp_keypoints = [(0, 25.0)]

    # Re-parse to also collect MFC + pressure events
    mfc_events = []     # (t_sec, mfc_name, flow_value)
    pc_events = []      # (t_sec, channel_name, setpoint) — channel_name is
                         # 'RTV', 'PC-1', 'PC-2', or another PC-* channel
    pump_events = []    # (t_sec, value)
    stage_rot_events = []  # (t_sec, rpm)

    # Initial RTV: parsed from RTV Pressure Ctrl line at t=0 (or wherever it appears)
    for r in rows:
        action, p, v = r[0], r[1], r[2]

        if action == 'Heater Ramp':
            target = float(p)
            dur = float(v)
            # Capture: previous endpoint, new endpoint
            temp_keypoints.append((t, ramp_to))      # start of new ramp = end of previous
            temp_keypoints.append((t + dur, target))  # end of new ramp
            ramp_start = t
            ramp_from = ramp_to
            ramp_to = target
            ramp_dur = dur
            # program time does not advance

        elif action == 'P1_Heater Ramp':
            target = float(p)
            dur = float(v)
            p1_temp_keypoints.append((t, p1_ramp_to))
            p1_temp_keypoints.append((t + dur, target))
            p1_ramp_to = target

        elif action == 'P2_Heater Ramp':
            target = float(p)
            dur = float(v)
            p2_temp_keypoints.append((t, p2_ramp_to))
            p2_temp_keypoints.append((t + dur, target))
            p2_ramp_to = target

        elif action == 'Heater Soak':
            # Heater off → passive cooldown begins
            # Only record the first Heater Soak; subsequent ones are
            # continued cooldown, not a new heater-off event.
            if heater_off_t is None:
                heater_off_t = t
                temp_keypoints.append((t, ramp_to))

        elif action == 'Wait':
            # p is "Min" or "Sec", v is the duration
            mult = 60 if p.strip().lower().startswith('min') else 1
            t += float(v) * mult

        elif action == 'Check Status':
            t += 10  # treat as a 10-second wait

        elif action == 'MFC/PC':
            # p is e.g. "MFC-3 O2", "PC-1", or "PC-2" — each PC-N is its
            # own channel and must not be merged with the others.
            if p.startswith('PC'):
                pc_events.append((t, p, float(v)))
            else:
                mfc_events.append((t, p, float(v)))

        elif action == 'RTV Pressure Ctrl':
            # third column is the actual chamber-pressure setpoint
            try:
                setpoint = float(v)
            except ValueError:
                setpoint = None
            if setpoint is not None:
                pc_events.append((t, 'RTV', setpoint))

        elif action == 'Accumulation PC1':
            # p is the setpoint, v is valve position
            try:
                pc_events.append((t, 'PC-1', float(p)))
            except ValueError:
                pass

        elif action == 'Accumulation PC2':
            try:
                pc_events.append((t, 'PC-2', float(p)))
            except ValueError:
                pass

        elif action == 'Pumping Forward':
            try:
                pump_events.append((t, float(p)))
            except ValueError:
                pass

        elif action == 'Stage Rot':
            try:
                stage_rot_events.append((t, float(p)))
            except ValueError:
                pass

    total_sec = t

    # Build temperature evaluator over the full run
    # Pre-sort keypoints; for any t_query, find the segment
    temp_keypoints = sorted(set(temp_keypoints), key=lambda x: (x[0], x[1]))

    def temp_at(t_q):
        """Temperature at time t_q. Uses linear interp during ramps,
        flat between ramps, and exponential decay after heater_off."""
        if heater_off_t is not None and t_q >= heater_off_t:
            # Find T at heater_off
            T_off = temp_at_pre_off(heater_off_t)
            # Exponential decay toward ambient (25°C) with tau=1500s (~25 min)
            tau = 1500.0
            return 25.0 + (T_off - 25.0) * math.exp(-(t_q - heater_off_t) / tau)
        return temp_at_pre_off(t_q)

    def temp_at_pre_off(t_q):
        # Among pre-off keypoints, walk segments
        kps = [kp for kp in temp_keypoints
               if heater_off_t is None or kp[0] <= heater_off_t]
        if not kps:
            return 25.0
        if t_q <= kps[0][0]:
            return kps[0][1]
        for i in range(len(kps) - 1):
            t0, T0 = kps[i]
            t1, T1 = kps[i + 1]
            if t0 <= t_q <= t1:
                if t1 == t0:
                    return T1
                return T0 + (T1 - T0) * (t_q - t0) / (t1 - t0)
        return kps[-1][1]

    # Build intervals for each MFC
    mfc_intervals = defaultdict(list)  # name -> [(start, end, flow), ...]
    mfc_state = {}
    open_starts = {}  # name -> start_time when flow > 0

    # Sort all mfc events by time
    for t_e, name, flow in sorted(mfc_events, key=lambda e: e[0]):
        prev = mfc_state.get(name, 0.0)
        if prev > 0 and flow != prev:
            # Close previous interval
            mfc_intervals[name].append((open_starts[name], t_e, prev))
            open_starts.pop(name, None)
        if flow > 0 and prev != flow:
            open_starts[name] = t_e
        mfc_state[name] = flow

    # Close any still-open intervals at total_sec
    for name, start in list(open_starts.items()):
        mfc_intervals[name].append((start, total_sec, mfc_state[name]))



    # Build pressure intervals
    rtv_intervals = []

    rtv_events = sorted([e for e in pc_events if e[1] == 'RTV'], key=lambda e: e[0])

    # NOTE: earlier versions tried to infer an end-of-run "switch to vacuum"
    # from the first low 'Pumping Forward' value. That's unreliable — low
    # pump values also appear during ordinary pre-growth chamber evacuation
    # (e.g. seconds into the run), which falsely truncated PC-1/PC-2's final
    # post-growth segment. Pressure channels now simply hold at their last
    # commanded setpoint through to the end of the run, matching what was
    # actually written to the recipe.
    if rtv_events:
        end_t = total_sec
        for i, (t_e, _, setp) in enumerate(rtv_events):
            next_t = rtv_events[i + 1][0] if i + 1 < len(rtv_events) else end_t
            if next_t > t_e:
                rtv_intervals.append((t_e, next_t, setp))

    # Build intervals per PC-N channel (PC-1, PC-2, ... each kept separate —
    # do NOT merge distinct channels together).
    pc_channel_intervals = {}
    pc_channel_names = sorted(set(nm for _, nm, _ in pc_events if nm != 'RTV'))
    for name in pc_channel_names:
        events = sorted([e for e in pc_events if e[1] == name], key=lambda e: e[0])
        end_t = total_sec
        intervals = []
        for i, (t_e, _, setp) in enumerate(events):
            next_t = events[i + 1][0] if i + 1 < len(events) else end_t
            if next_t > t_e:
                intervals.append((t_e, next_t, setp))
        if intervals:
            pc_channel_intervals[name] = intervals

    # Backward-compat alias (used by any external caller expecting pc1_intervals)
    pc1_intervals = pc_channel_intervals.get('PC-1', [])

    # ---------- Stage Rot ("Spin") intervals ----------
    stage_rot_intervals = []
    stage_rot_sorted = sorted(stage_rot_events, key=lambda e: e[0])
    if stage_rot_sorted:
        end_t = total_sec
        for i, (t_e, rpm) in enumerate(stage_rot_sorted):
            next_t = stage_rot_sorted[i + 1][0] if i + 1 < len(stage_rot_sorted) else end_t
            if next_t > t_e:
                stage_rot_intervals.append((t_e, next_t, rpm))

    # ---------- Secondary heater-zone evaluators (P1/P2) ----------
    p1_temp_keypoints_sorted = sorted(set(p1_temp_keypoints), key=lambda x: (x[0], x[1]))
    p2_temp_keypoints_sorted = sorted(set(p2_temp_keypoints), key=lambda x: (x[0], x[1]))
    has_p1_heater = len(p1_temp_keypoints_sorted) > 1
    has_p2_heater = len(p2_temp_keypoints_sorted) > 1

    def _static_eval(kps):
        def ev(t_q):
            if not kps:
                return 25.0
            if t_q <= kps[0][0]:
                return kps[0][1]
            for i in range(len(kps) - 1):
                t0, T0 = kps[i]
                t1, T1 = kps[i + 1]
                if t0 <= t_q <= t1:
                    if t1 == t0:
                        return T1
                    return T0 + (T1 - T0) * (t_q - t0) / (t1 - t0)
            return kps[-1][1]
        return ev

    temp_at_p1 = _static_eval(p1_temp_keypoints_sorted)
    temp_at_p2 = _static_eval(p2_temp_keypoints_sorted)

    # Compute temperature at each MFC change event
    mfc_change_events = []  # (t_sec, mfc_name, new_flow, label)
    for t_e, name, flow in sorted(mfc_events, key=lambda e: e[0]):
        if flow > 0:
            mfc_change_events.append((t_e, name, flow, f"{name.split()[-1]} ON"))
        else:
            mfc_change_events.append((t_e, name, flow, f"{name.split()[-1]} OFF"))

    gas_event_temps = [(t_e, name, flow, label, temp_at(t_e))
                       for t_e, name, flow, label in mfc_change_events]

    # ---------- Identify growth window ----------
    # Definition: exact keypoint-based. Growth begins the instant the
    # heater ramp reaches peak_T (not a "within 5°C" threshold estimate —
    # that was a previously-fixed bug and must not be reintroduced), and
    # ends the instant temperature next leaves peak_T. That end can be
    # either the passive cooldown that starts at heater-off, OR an
    # explicit ramp-down command (e.g. a controlled step down to a lower
    # temperature before the heater actually goes passive) — whichever
    # happens first. Growth does not continue through a deliberate
    # ramp-down just because the heater hasn't been commanded off yet.
    peak_T = max(T for _, T in temp_keypoints)
    growth_start = None
    growth_end = None
    if peak_T > 100:  # don't pick up ambient
        for t_kp, T_kp in temp_keypoints:
            if abs(T_kp - peak_T) < 1e-6:
                growth_start = t_kp
                break
        if growth_start is not None:
            # Look for the first consecutive keypoint PAIR after growth_start
            # where the first point is still at peak_T and the second is
            # not — that transition point (the start of the pair, i.e. the
            # moment the ramp-down begins) is where growth actually ends.
            # (A ramp-down's own keypoints are recorded as
            # (ramp_start_t, old_target) -> (ramp_end_t, new_target), so the
            # "start" keypoint still shows the old peak value — we want that
            # start time, not the end of the ramp-down.)
            kps_from_growth = [(t_kp, T_kp) for t_kp, T_kp in temp_keypoints if t_kp >= growth_start]
            ramp_down_t = None
            for i in range(len(kps_from_growth) - 1):
                t0, T0 = kps_from_growth[i]
                t1, T1 = kps_from_growth[i + 1]
                if abs(T0 - peak_T) < 1e-6 and abs(T1 - peak_T) > 1e-6:
                    ramp_down_t = t0
                    break
            if heater_off_t is not None and ramp_down_t is not None:
                growth_end = min(heater_off_t, ramp_down_t)
            elif heater_off_t is not None:
                growth_end = heater_off_t
            elif ramp_down_t is not None:
                growth_end = ramp_down_t
            else:
                growth_end = total_sec

    return {
        'csv_path': csv_path,
        'total_sec': total_sec,
        'heater_off_t': heater_off_t,
        'peak_T': peak_T,
        'temp_keypoints': temp_keypoints,
        'temp_at': temp_at,
        'mfc_intervals': dict(mfc_intervals),
        'rtv_intervals': rtv_intervals,
        'pc1_intervals': pc1_intervals,
        'pc_channel_intervals': pc_channel_intervals,
        'stage_rot_intervals': stage_rot_intervals,
        'gas_event_temps': gas_event_temps,
        'growth_start': growth_start,
        'growth_end': growth_end,
        'temp_at_p1': temp_at_p1,
        'temp_at_p2': temp_at_p2,
        'has_p1_heater': has_p1_heater,
        'has_p2_heater': has_p2_heater,
    }


# ---------- SVG rendering ----------

# Color palette for gas rows — picked from Anthropic Sans diagram palette
# Each gas is auto-assigned a color ramp based on first-seen order, but we
# also have heuristics for common gases.
GAS_COLOR_HINTS = {
    'Ar':   ('#D3D1C7', '#888780', '#2C2C2A', '#F1EFE8'),    # gray
    'O2':   ('#C0DD97', '#97C459', '#173404', '#EAF3DE'),    # green
    'O₂':   ('#C0DD97', '#97C459', '#173404', '#EAF3DE'),
    'H2':   ('#F5C4B3', '#F0997B', '#4A1B0C', '#FAECE7'),    # coral
    'H₂':   ('#F5C4B3', '#F0997B', '#4A1B0C', '#FAECE7'),
    'H2Se': ('#ED93B1', '#D4537E', '#4B1528', '#FBEAF0'),    # pink
    'H₂Se': ('#ED93B1', '#D4537E', '#4B1528', '#FBEAF0'),
    'H2S':  ('#FAC775', '#EF9F27', '#412402', '#FAEEDA'),    # amber
    'H₂S':  ('#FAC775', '#EF9F27', '#412402', '#FAEEDA'),
    'N2':   ('#85B7EB', '#378ADD', '#042C53', '#E6F1FB'),    # blue
    'N₂':   ('#85B7EB', '#378ADD', '#042C53', '#E6F1FB'),
}
# Fallback ramp cycle
FALLBACK_RAMPS = [
    ('#AFA9EC', '#7F77DD', '#26215C', '#EEEDFE'),    # purple
    ('#5DCAA5', '#1D9E75', '#04342C', '#E1F5EE'),    # teal
    ('#FAC775', '#EF9F27', '#412402', '#FAEEDA'),    # amber
    ('#85B7EB', '#378ADD', '#042C53', '#E6F1FB'),    # blue
]


# Which MFCs are summed into the combined "inner tube" / "outer tube" rows,
# chosen per reactor series from the first two letters of the run name
# (VBCA08 -> VB, VABD14 -> VA, HADI44 -> HA). Edit the numbers to match the
# reactor plumbing. VA is set equal to VB. VA/VB (8 MFCs): MFC-1..4 are the
# inner tube, MFC-5..8 the outer tube (confirmed by user).
_VB_GROUPS = {'inner': (1, 2, 3, 4), 'outer': (5, 6, 7, 8)}
SERIES_TUBE_GROUPS = {
    'VB': _VB_GROUPS,
    'VA': _VB_GROUPS,
    'HA': {'inner': (1, 3, 4), 'outer': (6, 7, 8)},
}
DEFAULT_SERIES = 'HA'


def tube_groups_for(run_name):
    key = (run_name or '')[:2].upper()
    if key not in SERIES_TUBE_GROUPS:
        print(f'Note: no inner/outer mapping for series "{key}"; using {DEFAULT_SERIES}.')
        key = DEFAULT_SERIES
    return SERIES_TUBE_GROUPS[key]


def tube_label(kind, nums):
    return f"{kind} tube (MFC{'+'.join(str(n) for n in nums)})"


def compute_combined_flow(parsed, mfc_prefixes):
    """Sum the flows of several MFCs (matched by their 'MFC-N' prefix,
    e.g. {'MFC-1','MFC-3','MFC-4'}) into one merged piecewise-constant
    series covering the full run. Returns [(start, end, total_flow), ...],
    omitting zero-flow stretches."""
    total_sec = parsed['total_sec']
    group_intervals = []
    for full_name, intervals in parsed['mfc_intervals'].items():
        prefix = full_name.split()[0] if full_name.split() else full_name
        if prefix in mfc_prefixes:
            group_intervals.extend(intervals)
    if not group_intervals:
        return []
    breakpoints = {0.0, total_sec}
    for s, e, _ in group_intervals:
        breakpoints.add(s)
        breakpoints.add(e)
    breakpoints = sorted(breakpoints)
    merged = []
    for i in range(len(breakpoints) - 1):
        t0, t1 = breakpoints[i], breakpoints[i + 1]
        if t1 <= t0:
            continue
        mid = (t0 + t1) / 2.0
        total = sum(flow for s, e, flow in group_intervals if s <= mid < e)
        if total > 0:
            merged.append((t0, t1, total))
    collapsed = []
    for s, e, v in merged:
        if collapsed and abs(collapsed[-1][2] - v) < 1e-9 and abs(collapsed[-1][1] - s) < 1e-6:
            collapsed[-1] = (collapsed[-1][0], e, v)
        else:
            collapsed.append((s, e, v))
    return collapsed


def gas_color(name, fallback_idx):
    """Return (light_fill, dark_fill, dark_text, light_text) for a gas name."""
    # Extract species from "MFC-3 O2" → "O2"
    parts = name.split()
    species = parts[-1] if parts else name
    if species in GAS_COLOR_HINTS:
        return GAS_COLOR_HINTS[species]
    return FALLBACK_RAMPS[fallback_idx % len(FALLBACK_RAMPS)]


def render_svg(parsed, title_label):
    """Build the SVG string from a parsed run card."""
    total = parsed['total_sec']
    total_min = total / 60.0

    # Layout constants
    plot_x0 = 60
    plot_x1 = 640
    plot_w = plot_x1 - plot_x0

    def x(t_sec):
        return plot_x0 + (t_sec / total) * plot_w

    # Temperature plot y range: T=950 → y=72, T=0 → y=165.7
    T_max = 950.0
    y_T_top = 72.0
    y_T_bot = 165.7

    def y_T(T):
        return y_T_bot - (T / T_max) * (y_T_bot - y_T_top)

    # Determine MFC row layout
    mfc_names = sorted(parsed['mfc_intervals'].keys())
    n_mfc = len(mfc_names)

    # Vertical layout
    gas_bar_y0 = 220
    gas_bar_h = 18
    gas_bar_gap = 6
    pressure_y0 = gas_bar_y0 + n_mfc * (gas_bar_h + gas_bar_gap) + 14  # divider gap
    pressure_h = 18
    pc_channel_names = sorted(parsed['pc_channel_intervals'].keys())
    has_spin = bool(parsed.get('stage_rot_intervals'))
    n_pressure_rows = (1 if parsed['rtv_intervals'] else 0) + len(pc_channel_names) + (1 if has_spin else 0)
    combined_y0 = pressure_y0 + n_pressure_rows * (pressure_h + gas_bar_gap) + 14  # divider gap
    combined_h = 18
    n_combined_rows = 2  # inner tube, outer tube
    time_axis_y = combined_y0 + n_combined_rows * (combined_h + gas_bar_gap) + 16
    summary_box_y = time_axis_y + 50
    n_extra_summary_lines = (1 if parsed.get('has_p1_heater') else 0) + (1 if parsed.get('has_p2_heater') else 0)
    svg_height = summary_box_y + 70 + n_extra_summary_lines * 15

    # Pick nice tick spacing
    tick_step = pick_tick_step(total_min)
    ticks = []
    tm = 0
    while tm <= total_min:
        ticks.append(tm)
        tm += tick_step
    if ticks[-1] < total_min - 1:
        ticks.append(round(total_min))

    parts = []
    parts.append(f'<svg width="100%" viewBox="0 0 680 {svg_height}" role="img" xmlns="http://www.w3.org/2000/svg">')
    parts.append(f'<title>{escape(title_label)} recipe profile</title>')
    parts.append(f'<desc>Run-card recipe profile showing temperature versus time and gas/pressure bars across {total_min:.0f} minutes.</desc>')

    # ---------- Title ----------
    parts.append(f'<text x="40" y="26" font-family="Anthropic Sans, sans-serif" font-size="14" font-weight="500" fill="#2C2C2A">{escape(title_label)} — Recipe profile</text>')
    parts.append(f'<text x="640" y="26" text-anchor="end" font-family="Anthropic Sans, sans-serif" font-size="12" fill="#888780">{total_min:.0f} min total</text>')

    # ---------- Growth window band (drawn first so it's behind everything) ----------
    if parsed['growth_start'] is not None and parsed['growth_end'] is not None:
        gx0 = x(parsed['growth_start'])
        gx1 = x(parsed['growth_end'])
        band_top = 42
        band_bot = time_axis_y - 4
        parts.append(f'<rect x="{gx0:.1f}" y="{band_top}" width="{gx1-gx0:.1f}" height="{band_bot-band_top}" fill="#FBEAF0" opacity="0.55"/>')
        parts.append(f'<rect x="{gx0:.1f}" y="{band_top}" width="{gx1-gx0:.1f}" height="{band_bot-band_top}" fill="none" stroke="#D4537E" stroke-width="0.6" stroke-dasharray="3 3"/>')
        gdur = (parsed['growth_end'] - parsed['growth_start']) / 60.0
        gT = parsed['peak_T']
        gx_center = (gx0 + gx1) / 2
        parts.append(f'<text x="{gx_center:.1f}" y="38" text-anchor="middle" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="#993556">Growth ({gdur:.0f} min @ {gT:.0f}°C)</text>')

    # ---------- Temperature plot ----------
    parts.append(f'<text x="50" y="64" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="#5F5E5A">Temp (°C)</text>')
    # Gridlines at peak T and any intermediate plateau
    for T_ref in sorted({parsed['peak_T'], 300, 0}):
        y_ref = y_T(T_ref)
        if T_ref == 0:
            parts.append(f'<line x1="{plot_x0}" y1="{y_ref:.1f}" x2="{plot_x1}" y2="{y_ref:.1f}" stroke="#888780" stroke-width="0.5"/>')
        else:
            parts.append(f'<line x1="{plot_x0}" y1="{y_ref:.1f}" x2="{plot_x1}" y2="{y_ref:.1f}" stroke="#D3D1C7" stroke-width="0.5" stroke-dasharray="2 3"/>')
        parts.append(f'<text x="55" y="{y_ref+4:.1f}" text-anchor="end" font-family="Anthropic Sans, sans-serif" font-size="10" fill="#888780">{T_ref:.0f}</text>')

    # Temperature trace: sample the temp function across time
    n_samples = 80
    pts = []
    for i in range(n_samples + 1):
        t_q = (i / n_samples) * total
        pts.append((x(t_q), y_T(parsed['temp_at'](t_q))))
    path = "M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    parts.append(f'<path d="{path}" fill="none" stroke="#D85A30" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')

    # ---------- Secondary heater-zone traces (P1/P2 preheaters), if present ----------
    has_p1 = parsed.get('has_p1_heater', False)
    has_p2 = parsed.get('has_p2_heater', False)
    if has_p1 or has_p2:
        # If P1 and P2 track identically (common — both are line preheaters
        # held at the same low temp), draw them as a single combined line.
        p1_hold = parsed['temp_at_p1'](total) if has_p1 else None
        p2_hold = parsed['temp_at_p2'](total) if has_p2 else None
        combined = False
        if has_p1 and has_p2:
            p1_samples = [round(parsed['temp_at_p1']((i / 20) * total), 1) for i in range(21)]
            p2_samples = [round(parsed['temp_at_p2']((i / 20) * total), 1) for i in range(21)]
            combined = (p1_samples == p2_samples)

        # Inline label anchor point: placed in the open gap after the growth
        # window / event labels and before the cooldown-end label, directly
        # on the trace(s) rather than tucked in a corner — so the actual
        # hold temperature is legible right where the line is.
        label_t = total * 0.66
        label_x = x(label_t)

        if combined:
            pts = [(x((i / n_samples) * total), y_T(parsed['temp_at_p1']((i / n_samples) * total)))
                   for i in range(n_samples + 1)]
            path_p = "M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
            parts.append(f'<path d="{path_p}" fill="none" stroke="#1D9E75" stroke-width="1.4" stroke-dasharray="4 2" stroke-linejoin="round" stroke-linecap="round" opacity="0.9"/>')
            label_y = y_T(parsed['temp_at_p1'](label_t))
            parts.append(f'<circle cx="{label_x:.1f}" cy="{label_y:.1f}" r="2.5" fill="#1D9E75" stroke="white" stroke-width="0.8"/>')
            parts.append(f'<text x="{label_x:.1f}" y="{label_y-8:.1f}" text-anchor="middle" font-family="Anthropic Sans, sans-serif" font-size="10" font-weight="500" fill="#1D9E75">P1/P2 · {p1_hold:g}°C</text>')
        else:
            if has_p1:
                p1_pts = [(x((i / n_samples) * total), y_T(parsed['temp_at_p1']((i / n_samples) * total)))
                          for i in range(n_samples + 1)]
                p1_path = "M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in p1_pts)
                parts.append(f'<path d="{p1_path}" fill="none" stroke="#378ADD" stroke-width="1.4" stroke-dasharray="4 2" stroke-linejoin="round" stroke-linecap="round" opacity="0.9"/>')
                p1_label_y = y_T(parsed['temp_at_p1'](label_t))
                parts.append(f'<circle cx="{label_x:.1f}" cy="{p1_label_y:.1f}" r="2.5" fill="#378ADD" stroke="white" stroke-width="0.8"/>')
                parts.append(f'<text x="{label_x:.1f}" y="{p1_label_y-8:.1f}" text-anchor="middle" font-family="Anthropic Sans, sans-serif" font-size="10" font-weight="500" fill="#378ADD">P1 · {p1_hold:g}°C</text>')
            if has_p2:
                p2_pts = [(x((i / n_samples) * total), y_T(parsed['temp_at_p2']((i / n_samples) * total)))
                          for i in range(n_samples + 1)]
                p2_path = "M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in p2_pts)
                parts.append(f'<path d="{p2_path}" fill="none" stroke="#7F77DD" stroke-width="1.4" stroke-dasharray="4 2" stroke-linejoin="round" stroke-linecap="round" opacity="0.9"/>')
                # Offset P2's label below the line (P1's sits above) so the
                # two labels don't collide when the traces sit close together.
                p2_label_x = x(total * 0.78)
                p2_label_y = y_T(parsed['temp_at_p2'](total * 0.78))
                parts.append(f'<circle cx="{p2_label_x:.1f}" cy="{p2_label_y:.1f}" r="2.5" fill="#7F77DD" stroke="white" stroke-width="0.8"/>')
                parts.append(f'<text x="{p2_label_x:.1f}" y="{p2_label_y+16:.1f}" text-anchor="middle" font-family="Anthropic Sans, sans-serif" font-size="10" font-weight="500" fill="#7F77DD">P2 · {p2_hold:g}°C</text>')

    # ---------- Event markers on temperature trace ----------
    # Strategy to avoid the label-pileup problem:
    # 1. Skip Ar/N2 carrier-gas events (too many, not chemistry transitions)
    # 2. Skip events at t=0 (initial setup, not a transition)
    # 3. Skip events inside or at the boundary of the growth window
    #    (the growth band annotation already conveys what happens there)
    # 4. For remaining events, place labels above/below the curve alternately,
    #    shifting horizontally with a leader line when they collide
    species_to_skip = {'Ar', 'N2', 'N₂'}
    sorted_events = sorted(parsed['gas_event_temps'], key=lambda e: e[0])

    informative_events = []
    for t_e, name, flow, label, T in sorted_events:
        if T is None:
            continue
        species = name.split()[-1]
        if species in species_to_skip:
            continue
        if t_e == 0:
            continue
        # Skip events strictly inside or at the growth window boundary
        if parsed['growth_start'] is not None and parsed['growth_end'] is not None:
            # 30-sec tolerance around boundaries
            if parsed['growth_start'] - 30 < t_e < parsed['growth_end'] + 30:
                continue
        informative_events.append((t_e, name, flow, T))

    # Collision-aware label placement
    placed_labels = []  # (left, right, top, bot) bboxes of placed text
    above_next = False
    for t_e, name, flow, T in informative_events:
        px = x(t_e)
        py = y_T(T)
        species = name.split()[-1]
        action_word = 'on' if flow > 0 else 'off'
        full_label = f'{species} {action_word} · {T:.0f}°C'
        label_w = len(full_label) * 6.5  # rough px width at 10px font
        ramp = GAS_COLOR_HINTS.get(species, ('#888780', '#444441', '#2C2C2A', '#F1EFE8'))
        dot_color = ramp[1]
        text_color = ramp[2]
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" fill="{dot_color}" stroke="white" stroke-width="1"/>')

        # Try placement candidates in order: centered, shifted right, shifted left, etc.
        target_y = (py - 10) if above_next else (py + 16)
        anchor_y_dir = -1 if above_next else 1

        def bbox_for(cx, anchor, ty):
            if anchor == 'middle':
                return (cx - label_w / 2, cx + label_w / 2, ty - 11, ty + 2)
            elif anchor == 'start':
                return (cx, cx + label_w, ty - 11, ty + 2)
            else:  # 'end'
                return (cx - label_w, cx, ty - 11, ty + 2)

        def collides(b):
            for (l, r, t_, b_) in placed_labels:
                if b[0] < r and b[1] > l and b[2] < b_ and b[3] > t_:
                    return True
            return False

        candidates = [
            (px, 'middle'),
            (px + 35, 'start'),
            (px - 35, 'end'),
            (px + 65, 'start'),
            (px - 65, 'end'),
        ]
        chosen = None
        # Search vertically too — push further from curve in 14px steps
        for v_offset in (0, 14, 28, 42):
            ty = target_y + anchor_y_dir * v_offset
            for cx, anchor in candidates:
                b = bbox_for(cx, anchor, ty)
                if not collides(b):
                    chosen = (cx, anchor, ty, b)
                    break
            if chosen:
                break

        if chosen is None:
            cx, anchor, ty = px, 'middle', target_y
            b = bbox_for(cx, anchor, ty)
        else:
            cx, anchor, ty, b = chosen
        placed_labels.append(b)

        # Leader if the label is offset from the dot
        if anchor != 'middle' or abs(ty - py) > 18:
            leader_x_end = cx if anchor == 'middle' else (cx - 2 if anchor == 'start' else cx + 2)
            leader_y_end = ty - anchor_y_dir * 3
            parts.append(f'<line x1="{px:.1f}" y1="{py + anchor_y_dir*4:.1f}" x2="{leader_x_end:.1f}" y2="{leader_y_end:.1f}" stroke="{dot_color}" stroke-width="0.4" opacity="0.6"/>')

        parts.append(f'<text x="{cx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" font-family="Anthropic Sans, sans-serif" font-size="10" font-weight="500" fill="{text_color}">{escape(full_label)}</text>')
        above_next = not above_next

    # ---------- Gas bars ----------
    swatch_x = 44
    swatch_x_left = 14  # pressure-row labels sit on the LEFT (x=55), so their
                         # swatch needs to be further left to avoid colliding with the text
    swatch_size = 8
    for i, name in enumerate(mfc_names):
        row_y = gas_bar_y0 + i * (gas_bar_h + gas_bar_gap)
        intervals = parsed['mfc_intervals'][name]
        light_fill, dark_fill, dark_text, light_text = gas_color(name, i)
        # Small color swatch to the left of the plot area, echoing the row's color
        sw_y = row_y + (gas_bar_h - swatch_size) / 2
        parts.append(f'<rect x="{swatch_x}" y="{sw_y:.1f}" width="{swatch_size}" height="{swatch_size}" rx="2" fill="{dark_fill}"/>')
        # If multiple distinct flow values, lighter for lower flow and darker for higher
        flows = [iv[2] for iv in intervals]
        max_flow = max(flows) if flows else 1
        for s_t, e_t, flow in intervals:
            bx = x(s_t)
            bw = x(e_t) - bx
            # Choose fill: if this is the highest flow level use dark, else light
            if flow >= 0.95 * max_flow and len(set(flows)) > 1:
                fill = dark_fill
                text_color = light_text
            else:
                fill = light_fill
                text_color = dark_text
            parts.append(f'<rect x="{bx:.1f}" y="{row_y}" width="{bw:.1f}" height="{gas_bar_h}" rx="3" fill="{fill}"/>')
            # Label inside bar
            cx = bx + bw / 2
            flow_str = format_flow(flow)
            parts.append(f'<text x="{cx:.1f}" y="{row_y + gas_bar_h - 5}" text-anchor="middle" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="{text_color}">{flow_str}</text>')
        # Row label
        parts.append(f'<text x="640" y="{row_y + gas_bar_h - 5}" text-anchor="end" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="{dark_text}">{escape(name)}</text>')

    # ---------- Divider between gas and pressure ----------
    divider_y = pressure_y0 - 8
    parts.append(f'<line x1="{plot_x0}" y1="{divider_y}" x2="{plot_x1}" y2="{divider_y}" stroke="#D3D1C7" stroke-width="0.5"/>')

    # ---------- Pressure bars ----------
    row_y = pressure_y0
    if parsed['rtv_intervals']:
        sw_y = row_y + (pressure_h - swatch_size) / 2
        parts.append(f'<rect x="{swatch_x_left}" y="{sw_y:.1f}" width="{swatch_size}" height="{swatch_size}" rx="2" fill="#534AB7"/>')
        for s_t, e_t, setp in parsed['rtv_intervals']:
            bx = x(s_t)
            bw = x(e_t) - bx
            # Vacuum segment gets a darker fill
            if setp < 1:
                fill = '#26215C'
                text_color = '#EEEDFE'
                txt = 'vac'
            else:
                fill = '#534AB7'
                text_color = '#EEEDFE'
                txt = f'{setp:g} Torr'
            parts.append(f'<rect x="{bx:.1f}" y="{row_y}" width="{bw:.1f}" height="{pressure_h}" rx="3" fill="{fill}"/>')
            cx = bx + bw / 2
            parts.append(f'<text x="{cx:.1f}" y="{row_y + pressure_h - 5}" text-anchor="middle" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="{text_color}">{escape(txt)}</text>')
        # Row label on the LEFT to avoid clashing with vac pill at the right
        parts.append(f'<text x="55" y="{row_y + pressure_h - 5}" text-anchor="end" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="#3C3489">RTV P</text>')
        row_y += pressure_h + gas_bar_gap

    # PC-N channels: each channel gets its own row and its own color ramp,
    # so PC-1 and PC-2 (etc.) are never visually conflated.
    PC_CHANNEL_RAMPS = {
        'PC-1': ('#5DCAA5', '#1D9E75', '#0F6E56', '#04342C', '#E1F5EE'),  # teal
        'PC-2': ('#AFA9EC', '#7F77DD', '#4A4396', '#26215C', '#EEEDFE'),  # purple
    }
    FALLBACK_PC_RAMPS = [
        ('#FAC775', '#EF9F27', '#C97D0F', '#412402', '#FAEEDA'),  # amber
        ('#85B7EB', '#378ADD', '#1F63A8', '#042C53', '#E6F1FB'),  # blue
    ]
    fallback_i = 0
    for pc_name in pc_channel_names:
        intervals = parsed['pc_channel_intervals'][pc_name]
        if pc_name in PC_CHANNEL_RAMPS:
            light, mid, dark, dark_text, light_text = PC_CHANNEL_RAMPS[pc_name]
        else:
            light, mid, dark, dark_text, light_text = FALLBACK_PC_RAMPS[fallback_i % len(FALLBACK_PC_RAMPS)]
            fallback_i += 1
        sw_y = row_y + (pressure_h - swatch_size) / 2
        parts.append(f'<rect x="{swatch_x_left}" y="{sw_y:.1f}" width="{swatch_size}" height="{swatch_size}" rx="2" fill="{dark}"/>')
        setps = [iv[2] for iv in intervals]
        max_p = max(setps) if setps else 1
        for s_t, e_t, setp in intervals:
            bx = x(s_t)
            bw = x(e_t) - bx
            if setp >= 0.95 * max_p and len(set(setps)) > 1:
                fill = dark
                text_color = light_text
            elif setp >= 0.5 * max_p:
                fill = mid
                text_color = light_text
            else:
                fill = light
                text_color = dark_text
            parts.append(f'<rect x="{bx:.1f}" y="{row_y}" width="{bw:.1f}" height="{pressure_h}" rx="3" fill="{fill}"/>')
            cx = bx + bw / 2
            parts.append(f'<text x="{cx:.1f}" y="{row_y + pressure_h - 5}" text-anchor="middle" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="{text_color}">{setp:g}</text>')
        parts.append(f'<text x="55" y="{row_y + pressure_h - 5}" text-anchor="end" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="{dark_text}">{escape(pc_name)}</text>')
        row_y += pressure_h + gas_bar_gap

    # ---------- Spin (Stage Rot) ----------
    if has_spin:
        spin_color = '#8B96A5'  # slate gray-blue
        spin_text = '#1F2937'
        sw_y = row_y + (pressure_h - swatch_size) / 2
        parts.append(f'<rect x="{swatch_x_left}" y="{sw_y:.1f}" width="{swatch_size}" height="{swatch_size}" rx="2" fill="{spin_color}"/>')
        for s_t, e_t, rpm in parsed['stage_rot_intervals']:
            bx = x(s_t)
            bw = x(e_t) - bx
            parts.append(f'<rect x="{bx:.1f}" y="{row_y}" width="{bw:.1f}" height="{pressure_h}" rx="3" fill="{spin_color}"/>')
            cx = bx + bw / 2
            parts.append(f'<text x="{cx:.1f}" y="{row_y + pressure_h - 5}" text-anchor="middle" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="white">{format_flow(rpm)} rpm</text>')
        parts.append(f'<text x="55" y="{row_y + pressure_h - 5}" text-anchor="end" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="{spin_text}">Spin</text>')
        row_y += pressure_h + gas_bar_gap

    # ---------- Combined inner-tube / outer-tube flow rows ----------
    combined_divider_y = combined_y0 - 8
    parts.append(f'<line x1="{plot_x0}" y1="{combined_divider_y}" x2="{plot_x1}" y2="{combined_divider_y}" stroke="#D3D1C7" stroke-width="0.5"/>')
    TUBE_GROUPS = tube_groups_for(title_label)
    combined_rows = [
        (tube_label('Inner', TUBE_GROUPS['inner']),
         compute_combined_flow(parsed, {f'MFC-{n}' for n in TUBE_GROUPS['inner']}),
         '#AFA9EC', '#7F77DD', '#26215C', '#EEEDFE'),
        (tube_label('Outer', TUBE_GROUPS['outer']),
         compute_combined_flow(parsed, {f'MFC-{n}' for n in TUBE_GROUPS['outer']}),
         '#5DCAA5', '#1D9E75', '#04342C', '#E1F5EE'),
    ]
    for i, (label, intervals, light_fill, dark_fill, dark_text, light_text) in enumerate(combined_rows):
        row_y = combined_y0 + i * (combined_h + gas_bar_gap)
        sw_y = row_y + (combined_h - swatch_size) / 2
        parts.append(f'<rect x="{swatch_x_left}" y="{sw_y:.1f}" width="{swatch_size}" height="{swatch_size}" rx="2" fill="{dark_fill}"/>')
        for s_t, e_t, flow_total in intervals:
            bx = x(s_t)
            bw = x(e_t) - bx
            parts.append(f'<rect x="{bx:.1f}" y="{row_y}" width="{bw:.1f}" height="{combined_h}" rx="3" fill="{light_fill}"/>')
            cx = bx + bw / 2
            parts.append(f'<text x="{cx:.1f}" y="{row_y + combined_h - 5}" text-anchor="middle" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="{dark_text}">{format_flow(flow_total)}</text>')
        parts.append(f'<text x="640" y="{row_y + combined_h - 5}" text-anchor="end" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="{dark_text}">{escape(label)}</text>')

    # ---------- Time axis ----------
    parts.append(f'<line x1="{plot_x0}" y1="{time_axis_y}" x2="{plot_x1}" y2="{time_axis_y}" stroke="#5F5E5A" stroke-width="0.5"/>')
    for tm in ticks:
        tx = x(tm * 60)
        parts.append(f'<line x1="{tx:.1f}" y1="{time_axis_y}" x2="{tx:.1f}" y2="{time_axis_y + 6}" stroke="#5F5E5A" stroke-width="0.5"/>')
        parts.append(f'<text x="{tx:.1f}" y="{time_axis_y + 19}" text-anchor="middle" font-family="Anthropic Sans, sans-serif" font-size="10" fill="#5F5E5A">{tm:g}</text>')
    parts.append(f'<text x="350" y="{time_axis_y + 36}" text-anchor="middle" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="#5F5E5A">Time (min)</text>')

    # ---------- Growth window summary box ----------
    if parsed['growth_start'] is not None and parsed['growth_end'] is not None:
        gs = parsed['growth_start']
        ge = parsed['growth_end']
        gmid = (gs + ge) / 2  # sample at the middle of the window
        gdur = (ge - gs) / 60.0
        # Active gases at the middle of growth window
        active = []
        for name, intervals in parsed['mfc_intervals'].items():
            for s_t, e_t, flow in intervals:
                if s_t <= gmid <= e_t:
                    active.append((name, flow))
                    break
        # Pressure values at the middle of growth window (each PC channel reported separately)
        pc_during = {}
        for pc_name, intervals in parsed['pc_channel_intervals'].items():
            setp = next((setp for s, e, setp in intervals if s <= gmid <= e), None)
            if setp is not None:
                pc_during[pc_name] = setp
        rtv_during = next((setp for s, e, setp in parsed['rtv_intervals'] if s <= gmid <= e), None)
        spin_during = next((rpm for s, e, rpm in parsed.get('stage_rot_intervals', []) if s <= gmid <= e), None)

        # Extra lines for secondary heater zones, if present
        extra_lines = []
        if parsed.get('has_p1_heater'):
            extra_lines.append(f'Heater 2 (P1): {parsed["temp_at_p1"](gmid):g}°C')
        if parsed.get('has_p2_heater'):
            extra_lines.append(f'Heater 3 (P2): {parsed["temp_at_p2"](gmid):g}°C')

        box_y = summary_box_y
        box_h = 58 + len(extra_lines) * 15
        parts.append(f'<rect x="40" y="{box_y}" width="600" height="{box_h}" rx="6" fill="#FBEAF0" opacity="0.6"/>')
        parts.append(f'<rect x="40" y="{box_y}" width="600" height="{box_h}" rx="6" fill="none" stroke="#D4537E" stroke-width="0.6"/>')
        parts.append(f'<text x="52" y="{box_y + 18}" font-family="Anthropic Sans, sans-serif" font-size="11" font-weight="500" fill="#993556">Growth window · t={gs/60:.0f}–{ge/60:.0f} min · {parsed["peak_T"]:.0f}°C plateau</text>')
        active_str = ' · '.join(f'{name} {format_flow(flow)} sccm' for name, flow in active)
        parts.append(f'<text x="52" y="{box_y + 34}" font-family="Anthropic Sans, sans-serif" font-size="10" fill="#4B1528">Gas chemistry: {escape(active_str)}</text>')
        p_str_parts = []
        for pc_name in sorted(pc_during.keys()):
            p_str_parts.append(f'{pc_name} = {pc_during[pc_name]:g} Torr')
        if rtv_during is not None:
            p_str_parts.append(f'RTV {rtv_during:g} Torr')
        if spin_during is not None:
            p_str_parts.append(f'Spin {format_flow(spin_during)} rpm')
        p_str = ' · '.join(p_str_parts)
        parts.append(f'<text x="52" y="{box_y + 50}" font-family="Anthropic Sans, sans-serif" font-size="10" fill="#4B1528">Pressure: {escape(p_str)}</text>')
        for i, line in enumerate(extra_lines):
            parts.append(f'<text x="52" y="{box_y + 50 + (i+1)*15}" font-family="Anthropic Sans, sans-serif" font-size="10" fill="#4B1528">{escape(line)}</text>')

    parts.append('</svg>')
    return '\n'.join(parts)


def format_flow(v):
    """Format a flow value: integer if whole, one decimal otherwise."""
    if v == int(v):
        return f'{int(v)}'
    return f'{v:g}'


def pick_tick_step(total_min):
    """Pick a sensible tick spacing in minutes."""
    if total_min <= 30:
        return 5
    if total_min <= 60:
        return 10
    if total_min <= 150:
        return 20
    if total_min <= 300:
        return 30
    return 60


def escape(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


# ---------- PNG export ----------

def export_png(svg_path, png_path, width=1600):
    """Convert SVG to PNG. Uses cairosvg if available, else falls back to
    matplotlib's svg→png via Pillow, else prints a message."""
    try:
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=png_path, output_width=width)
        return True
    except ImportError:
        pass
    # Fallback 1: try rsvg-convert via subprocess
    import subprocess
    try:
        subprocess.run(['rsvg-convert', '-w', str(width), '-o', png_path, svg_path],
                       check=True, capture_output=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    # Fallback 2: try inkscape
    try:
        subprocess.run(['inkscape', '--export-type=png', f'--export-width={width}',
                        f'--export-filename={png_path}', svg_path],
                       check=True, capture_output=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    print(f"Warning: could not convert SVG to PNG (no cairosvg / rsvg-convert / inkscape).", file=sys.stderr)
    print(f"Install one of them, or the SVG is still usable at: {svg_path}", file=sys.stderr)
    return False


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('csv', help='Path to run card CSV')
    ap.add_argument('--out-svg', help='Output SVG path (default: <csv-base>_runcard.svg)')
    ap.add_argument('--out-png', help='Output PNG path (default: <csv-base>_runcard.png)')
    ap.add_argument('--out-dir', default='.', help='Output directory (default: current)')
    args = ap.parse_args()

    base = os.path.splitext(os.path.basename(args.csv))[0]
    out_svg = args.out_svg or os.path.join(args.out_dir, f'{base}_runcard.svg')
    out_png = args.out_png or os.path.join(args.out_dir, f'{base}_runcard.png')

    parsed = parse_runcard(args.csv)
    svg = render_svg(parsed, base)

    with open(out_svg, 'w') as f:
        f.write(svg)
    print(f'Wrote {out_svg}')

    export_png(out_svg, out_png)
    if os.path.exists(out_png):
        print(f'Wrote {out_png}')

    # Print key facts for the assistant to summarize
    print('\n--- Run summary ---')
    print(f'Total time: {parsed["total_sec"]/60:.1f} min')
    print(f'Peak temperature: {parsed["peak_T"]:.0f}°C')
    if parsed['growth_start'] is not None:
        gs = parsed['growth_start'] / 60
        ge = parsed['growth_end'] / 60
        print(f'Growth window: t={gs:.0f}–{ge:.0f} min ({ge-gs:.0f} min at peak)')
    if parsed['heater_off_t']:
        print(f'Heater off at: t={parsed["heater_off_t"]/60:.0f} min')
    for pc_name in sorted(parsed['pc_channel_intervals'].keys()):
        intervals = parsed['pc_channel_intervals'][pc_name]
        vals = ' -> '.join(f'{setp:g} Torr (t={s/60:.0f}-{e/60:.0f} min)' for s, e, setp in intervals)
        print(f'{pc_name}: {vals}')
    if parsed.get('has_p1_heater'):
        print('P1 heater zone: present (plotted as dashed overlay)')
    if parsed.get('has_p2_heater'):
        print('P2 heater zone: present (plotted as dashed overlay)')
    print('\nGas transitions (T at each event):')
    for t_e, name, flow, label, T in parsed['gas_event_temps']:
        if T is not None:
            print(f'  t={t_e/60:5.1f} min  {name:<15} {label:<10} T={T:.0f}°C  flow={format_flow(flow)}')


if __name__ == '__main__':
    main()
