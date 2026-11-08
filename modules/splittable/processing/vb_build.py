"""
VB1P01's split-table rows: one per logged run, plus every wafer that has a
card, a Milestone row or QC but no logged run.

Ported from ``VB1P01/SPLITTABLE/build_vb_splittable.py`` (Ang-Yu Lu), whose
column definitions and row rules it keeps; that script still stands beside the
workbook because HA's ``build_ha_w_source.py`` imports it. What differs, and
why (user, 2026-11-08):

* **QC comes from the QC summary**, one row per wafer picked by
  `qc_select` (a "QC" fit mode first, then the newest report, then the
  frozen legacy values), and the row says which: ``QC_Source``,
  ``QC_Fit_Mode``, ``QC_Layer``, ``QC_Version``, ``QC_Report_Date``. A wafer
  with no QC still gets its runcard, datalog and Milestone columns.
* **W_source**: Milestone's W column, then the run-ID rule
  (`core.io.precursor_rules`) where that is NA or blank, then the series
  majority; ``W_source_rule`` says which, and ``Precursor_check`` names a
  Milestone value the run-ID rule disagrees with. The hard-coded VA1P0125 /
  VA1P0126 serial rule is now two of those run-ID rules.
* **Rows are in run order**: Process_Start, else the card's save time, so a
  wafer without a log sits where it was made instead of at the end.
* The Aug-2026 ``VBCAxx-2608`` rename is gone: those logs and Milestone rows
  are VBACxx now (fixed at source, 2026-11-08).
"""

import datetime
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional

import pandas as pd

from core.io.precursor_rules import precursor_for
from modules.runcard.io.parser import parse_runcard
from modules.runcard.processing import growth_window as gw

from .qc_select import QCChoice, block as qc_block

SHEET = "VB_SplitTable_gen"
FALSE_START_WAIT_MIN = 10.0

SERIAL_RE = re.compile(r"^N?S?V[AB]1P01(\d{7})", re.I)
CODE_RE = re.compile(r"^(V[AB][A-Z]{2}\d{2}(?:-\d+)?(?:-DUMMY)?)", re.I)

# --------------------------------------------------------------- columns
# (column, group, unit, source, definition)
DESIGN = [
    ("WaferID", "identity", "", "runcard / datalog tag",
     "Run name. A wafer for growth rows; the recipe file name for cleaning / anneal / test rows."),
    ("Machine", "identity", "", "WaferID", "Recipe series owner: VA (VA* codes, 2025 serials) or VB (VB* codes). One tool, VB1P01."),
    ("Series", "identity", "", "WaferID", "First four letters (VABD, VBBE ...); SER for 2025 serials."),
    ("Month", "identity", "", "Process_Start, else card save time", "YYYYMM the run happened. Same month folder as VB1P01/OPTICAL."),
    ("Runcard", "identity", "", "RUNCARD folder", "Card file the row was parsed from (relative to VB1P01/RUNCARD)."),
    ("Runcard_Saved", "identity", "", "file mtime", "When the card was last saved."),
    ("Run_Type", "run", "", "datalog + card", "growth / conditioning (no H2Se: a wafer-named card that never flows H2Se, e.g. VATC) / cleaning / anneal / test; '(stopped)' before End, '(no datalog)' when no log was found."),
    ("Process_Start", "run", "", "datalog", "First logged row of the run."),
    ("Process_End", "run", "", "datalog", "Last logged row."),
    ("Process_Time_min", "run", "min", "datalog", "Start to end: whole tool time including pump-down."),
    ("Wait_Clock_min", "run", "min", "datalog", "Minutes the controller spent in Wait (the matcher's clock)."),
    ("Datalog", "run", "", "DATALOG folder", "Log file (month/filename)."),
    ("Datalog_Match", "run", "", "runcard_match", "tag (named on the tool) or matched (NexAnalyzer's matcher) with its confidence."),
    ("Recipe_Total_min", "process", "min", "runcard", "Recipe clock length (Check Status at its nominal 10 s)."),
    ("T_pre_C", "process", "degC", "Heater Ramp", "Ramp target immediately before the growth target."),
    ("T_growth_C", "process", "degC", "Heater Ramp", "Peak heater target -- the growth temperature."),
    ("P1_heater_C", "process", "degC", "P1 heater", "Preheater 1 setpoint at the end of the recipe (WO3 / source zone)."),
    ("P2_heater_C", "process", "degC", "P2 heater", "Preheater 2 setpoint at the end of the recipe."),
    ("P_torr", "process", "torr", "RTV Pressure Ctrl", "Chamber pressure setpoint at growth mid."),
    ("Spin_rpm", "process", "rpm", "Stage Rot", "Susceptor rotation at growth mid."),
    ("growth_start_min", "growth window", "min", "derived", "Recipe time the heater reaches its peak target."),
    ("growth_end_min", "growth window", "min", "derived", "Heater-off or the first commanded ramp-down, whichever is first."),
    ("growth_window_min", "growth window", "min", "derived", "growth_end_min - growth_start_min."),
    ("Pulse_gas", "pulse", "", "Pulse <gas>,a,b", "Gas the recipe pulses (H2Se, O2 ...); blank for a continuous recipe."),
    ("Pulse_cycles", "pulse", "count", "Pulse a", "a: number of cycles."),
    ("Pulse_step_s", "pulse", "s", "Pulse b", "b: seconds on (and off) per cycle."),
    ("Pulse_total_min", "pulse", "min", "derived", "2*a*b/60, the logged length of the Pulse step (checked against the datalogs)."),
    ("Ar_in_sccm", "process", "sccm", "MFC-4 Ar", "Main Ar inlet at growth mid."),
    ("Ar_in_pre_sccm", "process", "sccm", "MFC-4 Ar", "MFC-4 just before the growth window."),
    ("Ar_out_sccm", "process", "sccm", "MFC-5 Ar", "Ar outlet at growth mid."),
    ("Ar_MFC6_sccm", "process", "sccm", "MFC-6 Ar", "MFC-6 at growth mid."),
    ("H2O_carrier_sccm", "process", "sccm", "MFC-1 Ar", "H2O bubbler carrier at growth mid."),
    ("H2O_PC1_torr", "process", "torr", "PC-1 / Accumulation PC1", "H2O bubbler pressure at growth mid."),
    ("W_carrier_sccm", "process", "sccm", "MFC-2 Ar", "W(CO)6 bubbler carrier at growth mid (W(CO)6 recipes)."),
    ("W_PC2_torr", "process", "torr", "PC-2", "W(CO)6 bubbler pressure at growth mid."),
    ("O2_sccm", "process", "sccm", "MFC-3 O2", "O2 at growth mid."),
    ("H2_sccm", "process", "sccm", "MFC-7 H2", "H2 at growth mid."),
    ("H2Se_before_sccm", "H2Se", "sccm", "MFC-8 H2Se", "Level in force just before the growth window."),
    ("H2Se_on_sccm", "H2Se", "sccm", "MFC-8 H2Se", "Level at growth mid."),
    ("H2Se_on_lo_sccm", "H2Se", "sccm", "MFC-8 H2Se", "Lowest level inside the growth window."),
    ("H2Se_on_hi_sccm", "H2Se", "sccm", "MFC-8 H2Se", "Highest level inside the growth window."),
    ("H2Se_cool3_sccm", "H2Se", "sccm", "MFC-8 H2Se", "Level 3 min after the window ends."),
    ("H2Se_ramp_lo_sccm", "H2Se", "sccm", "MFC-8 H2Se", "Lowest non-zero level from start to growth end (< 0.05 leak checks excluded)."),
    ("H2Se_ramp_hi_sccm", "H2Se", "sccm", "MFC-8 H2Se", "Highest level from start to growth end."),
    ("H2Se_dose_sccm_min", "H2Se", "sccm*min", "derived", "Integral of MFC-8 over the growth window."),
    ("H2O_on_lo_sccm", "H2O", "sccm", "MFC-1 Ar", "Lowest MFC-1 inside the window."),
    ("H2O_on_hi_sccm", "H2O", "sccm", "MFC-1 Ar", "Highest MFC-1 inside the window."),
    ("H2O_dose_sccm_min", "H2O", "sccm*min", "derived", "Integral of MFC-1 over the growth window."),
    ("H2O_n_steps", "H2O", "count", "derived", "Distinct MFC-1 settings inside the window."),
    ("O2_dose_sccm_min", "O2", "sccm*min", "derived", "Integral of MFC-3 over the growth window."),
    ("W_source", "source", "", "Milestone W column, run-ID rule, series", "W foil / WO3 / W(CO)6: the Milestone W column; where it is NA or blank, the run-ID rule (Material Presets > Precursor by run ID); else the series majority."),
    ("W_source_rule", "source", "", "derived", "Which of those gave W_source: Milestone / run ID <prefix> / series majority."),
    ("Precursor_check", "source", "", "derived", "Set when the Milestone W column disagrees with the run-ID rule -- a typo on one side."),
    ("W_source_recipe", "source", "", "runcard + series", "The W source by the recipe rule: W foil for the VAAA series; else W(CO)6 if any of the wafer's cards flows the W bubbler (W_carrier_sccm, MFC-2); else WO3. A wafer with no runcard keeps W_source. This is the W source the OM_Raman_PL summary and the TSMC insight data use."),
    ("W_amount", "source", "", "Milestone (2nd W column)", "Charge / pressure as written in the sheet (e.g. 2.5g, 140)."),
    ("Se_source", "source", "", "runcard", "H2Se whenever MFC-8 H2Se is used."),
    ("Sheet_row", "cross-check", "", "derived", "present / absent: whether the Milestone sheet has this run."),
    ("Disagrees_with_sheet", "cross-check", "", "derived", "P, T, H2Se, O2, Ar in where card and sheet differ (> 2 % or > 0.05)."),
    ("Flag", "cross-check", "", "derived", "Anything odd found while building the row."),
    ("Hand_Edits", "cross-check", "", "refresh", "Cells edited by hand in the workbook and kept by the refresh; '(now x)' is what the sources say today."),
    ("Baseline", "joined", "", "Milestone", "Run this one was based on."),
    ("User", "joined", "", "Milestone", "Operator."),
    ("Date", "joined", "", "Milestone", "Run date as entered."),
    ("Purpose", "joined", "", "Milestone", "Stated intent."),
    ("Result", "joined", "", "Milestone", "Outcome as entered (layer count, comment)."),
    ("Remark", "joined", "", "Milestone + QC", "Free text from Milestone; a QC block taken from another QC row than QC_Source is noted here (\"QC filled: ...\")."),
    ("Bilayer", "joined", "", "Milestone (VA)", "Bilayer mark (VA sheet)."),
    ("Dist_cm", "joined", "cm", "Milestone", "Source-to-wafer distance."),
    ("SH", "joined", "", "Milestone", "Showerhead."),
    ("Pretreatment", "joined", "", "Milestone", "Pretr."),
    ("Posttreatment", "joined", "", "Milestone", "Posttr."),
    ("Coat", "joined", "", "Milestone", "Coat."),
    ("Pulse", "joined", "", "Milestone", "Pulse mode / cycles."),
    ("Issue", "joined", "", "Milestone", "Issue noted."),
    ("QC_Source", "QC measured", "", "QC summary", "QC Report or legacy: where the QC columns of this row come from."),
    ("QC_Fit_Mode", "QC measured", "", "QC summary", "The preset (QC Report) or fit mode (legacy) that made them."),
    ("QC_Layer", "QC measured", "", "QC summary", "Reference layer of the OM columns: OM_Reference_pct is the area at this layer."),
    ("QC_Version", "QC measured", "", "QC summary", "NexAnalyzer version that made them, where recorded."),
    ("QC_Report_Date", "QC measured", "", "QC summary", "The QC Report's date; blank for legacy values."),
    ("Optical_Folder", "QC measured", "", "QC summary", "VB1P01/OPTICAL folder of this wafer."),
    ("OM_Frames", "QC measured", "count", "QC summary", "OM frames segmented."),
    ("OM_Below_pct", "QC measured", "%", "QC summary", "Area below the reference contrast."),
    ("OM_Reference_pct", "QC measured", "%", "QC summary", "Area at the reference layer (the coverage figure)."),
    ("OM_Above_pct", "QC measured", "%", "QC summary", "Area above reference."),
    ("OM_Reference_Std", "QC measured", "%", "QC summary", "Frame-to-frame spread of OM_Reference_pct."),
    ("OM_Discriminable", "QC measured", "bool", "QC summary", "Cut / noise >= 2 sigma. False: the percentages are noise."),
    ("Raman_n_spectra", "QC measured", "count", "QC summary", "Raman spectra fitted."),
    ("Raman_n_used", "QC measured", "count", "QC summary", "Passing the R2 > 0.5 gate."),
    ("Raman_R2_mean", "QC measured", "", "QC summary", "Mean R2 over all spectra."),
    ("E2g_A1g_Center", "QC measured", "1/cm", "QC summary", "E2g+A1g centre."),
    ("E2g_A1g_FWHM", "QC measured", "1/cm", "QC summary", "E2g+A1g Voigt FWHM."),
    ("E2g_A1g_FWHM_std", "QC measured", "1/cm", "QC summary", "Spread of that width."),
    ("FWHM_In_Spec", "QC measured", "bool", "peak_quality spec", "E2g+A1g FWHM <= spec."),
    ("TwoLA_Center", "QC measured", "1/cm", "QC summary", "2LA centre."),
    ("B2g_Center", "QC measured", "1/cm", "QC summary", "B2g centre (bilayer-sensitive)."),
    ("B2g_In_Spec", "QC measured", "bool", "peak_quality spec", "B2g centre >= spec."),
    ("LA_Center", "QC measured", "1/cm", "QC summary", "LA centre."),
    ("Defect_Ratio_Median", "QC measured", "", "QC summary", "Median LA/(E2g+A1g) intensity ratio."),
    ("Defect_Ratio_MAD", "QC measured", "", "QC summary", "MAD of that ratio."),
    ("Defect_In_Spec", "QC measured", "bool", "peak_quality spec", "Defect ratio <= spec."),
    ("Stacking_Ratio_Median", "QC measured", "", "QC summary", "Median C/LB intensity ratio."),
    ("PL_n_spectra", "QC measured", "count", "QC summary", "PL spectra fitted."),
    ("PL_Raw_Center", "QC measured", "nm", "QC summary", "Raw spectrum's peak position (QC Reports only; legacy has none)."),
    ("PL_Raw_FWHM", "QC measured", "nm", "QC summary", "Raw spectrum's FWHM, the one the PL spec is judged on (QC Reports only)."),
    ("PL_Raw_Intensity", "QC measured", "counts", "QC summary", "Raw spectrum's maximum (QC Reports only)."),
    ("PL_Exciton_Center", "QC measured", "nm", "QC summary", "Exciton centre."),
    ("PL_Exciton_FWHM", "QC measured", "nm", "QC summary", "Exciton FWHM."),
    ("PL_Exciton_Intensity", "QC measured", "counts", "QC summary", "Exciton fitted maximum."),
    ("PL_Trion_Center", "QC measured", "nm", "QC summary", "Trion centre."),
    ("PL_Trion_FWHM", "QC measured", "nm", "QC summary", "Trion FWHM."),
]
COLUMNS = [d[0] for d in DESIGN]
#: Columns the refresh writes but never drops when empty.
ALWAYS = ("Flag", "Hand_Edits")
QC_MAP = {
    "Optical_Folder": "Folder", "OM_Frames": "OM_Frames", "OM_Below_pct": "OM_Below_pct",
    "OM_Reference_pct": "OM_Reference_pct", "OM_Above_pct": "OM_Above_pct",
    "OM_Reference_Std": "OM_Reference_Std", "OM_Discriminable": "OM_Discriminable",
    "Raman_n_spectra": "Raman_n_spectra", "Raman_n_used": "Raman_n_used_R2>0.5",
    "Raman_R2_mean": "Raman_R2_mean", "E2g_A1g_Center": "Raman_E2g+A1g_Center",
    "E2g_A1g_FWHM": "Raman_E2g+A1g_FWHM", "E2g_A1g_FWHM_std": "Raman_E2g+A1g_FWHM_std",
    "TwoLA_Center": "Raman_2LA_Center", "B2g_Center": "Raman_B2g_Center",
    "LA_Center": "Raman_LA_Center", "Defect_Ratio_Median": "Raman_LA/E2g+A1g_median",
    "Defect_Ratio_MAD": "Raman_LA/E2g+A1g_MAD", "Stacking_Ratio_Median": "Raman_C/LB_median",
    "PL_n_spectra": "PL_n_spectra", "PL_Raw_Center": "PL_Raw_Center",
    "PL_Raw_FWHM": "PL_Raw_FWHM", "PL_Raw_Intensity": "PL_Raw_Intensity",
    "PL_Exciton_Center": "PL_Exciton_Center",
    "PL_Exciton_FWHM": "PL_Exciton_FWHM", "PL_Exciton_Intensity": "PL_Exciton_Intensity",
    "PL_Trion_Center": "PL_Trion_Center", "PL_Trion_FWHM": "PL_Trion_FWHM",
    "QC_Fit_Mode": "Fit_Mode", "QC_Layer": "Layer", "QC_Version": "NexAnalyzer_version",
    "QC_Report_Date": "QC_Report_Date",
}


# --------------------------------------------------------------- names
def canonical(name) -> str:
    """Wafer key shared by cards, datalog tags and optical folders."""
    n = str(name).strip()
    m = SERIAL_RE.match(n)
    if m:
        return "VA1P01" + m.group(1)
    m = CODE_RE.match(n)
    if m:
        return m.group(1).upper()
    return n


def is_wafer(key) -> bool:
    return bool(SERIAL_RE.match(key) or CODE_RE.fullmatch(key))


def series(key) -> str:
    return "SER" if SERIAL_RE.match(key) else key[:4]


def natural(text):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(text))]


def _has_se(row):
    return bool(row.get("H2Se_dose_sccm_min") or row.get("H2Se_ramp_hi_sccm")
                or "H2Se" in str(row.get("Pulse_gas") or "")) or not row.get("Runcard")


def run_kind(name):
    n = name.lower()
    if "clean" in n or "purge" in n or "moisture" in n:
        return "cleaning"
    if "anneal" in n:
        return "anneal"
    return "test"


# --------------------------------------------------------------- runcards
def card_index(runcard_dir, superseded=frozenset()) -> Dict[str, List[str]]:
    """{canonical key: [card paths]}, best first (LYH, then other owners, then backup)."""
    idx: Dict[str, List[str]] = {}
    root = str(runcard_dir)
    for path in sorted(str(p) for p in Path(runcard_dir).rglob("*.csv")):
        rel = os.path.relpath(path, root)
        if rel.startswith("Visualization") or " - " in os.path.basename(path):
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        key = canonical(stem) if (SERIAL_RE.match(stem) or CODE_RE.match(stem)) else stem
        if is_wafer(key) and stem.upper() != key and not SERIAL_RE.match(stem):
            # "VBBF44_JK", "VABD21-1" kept under their own suffixed key too
            idx.setdefault(stem.upper(), []).append(path)
        idx.setdefault(key, []).append(path)

    def rank(p):
        return (os.sep + "backup" + os.sep in p, os.sep + "LYH" + os.sep not in p, p)
    return {k: sorted(v, key=rank) for k, v in idx.items() if k not in superseded}


def _value_at(events, name, t):
    v = None
    for et, en, ev in events:
        if en == name and et <= t:
            v = ev
    return v


def _segments(events, name, t0, t1):
    """[(value, seconds)] of channel `name` held inside [t0, t1]."""
    pts = [(et, ev) for et, en, ev in events if en == name]
    out, v = [], _value_at(events, name, t0)
    cursor = t0
    for et, ev in pts:
        if et <= t0:
            continue
        if et >= t1:
            break
        if v is not None:
            out.append((v, et - cursor))
        cursor, v = et, ev
    if v is not None:
        out.append((v, t1 - cursor))
    return out


def _channel(events, prefix):
    for _, en, _ in events:
        if en.startswith(prefix):
            return en
    return None


def _r(v, nd=3):
    return None if v is None else round(float(v), nd)


def _num(v):
    try:
        return float(str(v).replace("sccm", "").strip())
    except (TypeError, ValueError):
        return None


#: A `Pulse <gas>,a,b` step runs a cycles of b s on + b s off. The runcard
#: model gives it no duration; the logs do (60,7 -> 796-844 s of Pulse rows,
#: 2*60*7 = 840), so it is replayed here as a Wait of 2*a*b seconds and its gas
#: is reported in the Pulse_* columns rather than as an "on" setpoint.
PULSE_FACTOR = 2.0


def _expand_pulses(commands):
    out, pulses = [], []
    for c in commands:
        if c.name.startswith("Pulse"):
            a, b = (_num(x) for x in (tuple(c.params) + ("", ""))[:2])
            gas = c.name[len("Pulse"):].strip() or "(unnamed)"
            secs = PULSE_FACTOR * a * b if a and b else 0.0
            pulses.append((gas, a, b, secs))
            out.append(c)
            if secs:
                out.append(type(c)(index=c.index, name="Wait", params=("Sec", "{:g}".format(secs))))
        else:
            out.append(c)
    return out, pulses


def parse_card(path):
    """(values, flags) of one card: the process, growth-window and gas columns."""
    commands, pulses = _expand_pulses(parse_runcard(path))
    prof = gw.build_profile(commands)
    tl, g = prof.timeline, prof.growth
    out = {"Recipe_Total_min": _r(tl.total_time / 60, 2)}
    if pulses:
        out.update(Pulse_gas=", ".join(sorted({p[0] for p in pulses})),
                   Pulse_cycles=pulses[0][1], Pulse_step_s=pulses[0][2],
                   Pulse_total_min=_r(sum(p[3] for p in pulses) / 60, 2))
    flags = []
    if g.start_s is None:
        flags.append("no growth window (heater never held a peak)")
        out["T_growth_C"] = _r(g.peak_temp_c, 1) if g.peak_temp_c > 100 else None
        return out, flags
    s, e, mid = g.start_s, g.end_s, g.mid_s
    out.update(T_growth_C=_r(g.peak_temp_c, 1), growth_start_min=_r(s / 60, 2),
               growth_end_min=_r(e / 60, 2), growth_window_min=_r((e - s) / 60, 2))
    targets = [to for (_t, _f, to, _d) in tl.heater_ramps]
    peak_i = next((i for i, to in enumerate(targets) if abs(to - g.peak_temp_c) < 1e-6), None)
    if peak_i:
        out["T_pre_C"] = _r(targets[peak_i - 1], 1)
    out["P1_heater_C"] = _r(prof.p1_trace[-1][1], 1) if prof.p1_trace else None
    out["P2_heater_C"] = _r(prof.p2_trace[-1][1], 1) if prof.p2_trace else None
    out["P_torr"] = _r(gw.fixed_channel_value_at_growth_mid(prof, "RTV"), 2)
    out["Spin_rpm"] = _r(gw.fixed_channel_value_at_growth_mid(prof, "Spin"), 1)
    mfc, pc = tl.mfc_events, tl.pc_events

    def at(prefix, t=mid, events=mfc):
        ch = _channel(events, prefix)
        return None if ch is None else _value_at(events, ch, t)

    out.update(Ar_in_sccm=at("MFC-4"), Ar_in_pre_sccm=at("MFC-4", s - 1),
               Ar_out_sccm=at("MFC-5"), Ar_MFC6_sccm=at("MFC-6"),
               H2O_carrier_sccm=at("MFC-1"), W_carrier_sccm=at("MFC-2"),
               O2_sccm=at("MFC-3"), H2_sccm=at("MFC-7"), H2Se_on_sccm=at("MFC-8"),
               H2O_PC1_torr=at("PC-1", events=pc), W_PC2_torr=at("PC-2", events=pc),
               H2Se_before_sccm=at("MFC-8", s - 1),
               H2Se_cool3_sccm=at("MFC-8", e + 180))
    h2se = _channel(mfc, "MFC-8")
    if h2se:
        seg = _segments(mfc, h2se, s, e)
        vals = [v for v, d in seg if d > 0]
        if vals:
            out.update(H2Se_on_lo_sccm=min(vals), H2Se_on_hi_sccm=max(vals))
        out["H2Se_dose_sccm_min"] = _r(sum(v * d for v, d in seg) / 60, 2)
        ramp = [v for v, d in _segments(mfc, h2se, 0.0, e) if d > 0 and v >= 0.05]
        if ramp:
            out.update(H2Se_ramp_lo_sccm=min(ramp), H2Se_ramp_hi_sccm=max(ramp))
        out["Se_source"] = "H2Se"
    h2o = _channel(mfc, "MFC-1")
    if h2o:
        seg = [(v, d) for v, d in _segments(mfc, h2o, s, e) if d > 0]
        if seg:
            vals = [v for v, _ in seg]
            out.update(H2O_on_lo_sccm=min(vals), H2O_on_hi_sccm=max(vals),
                       H2O_dose_sccm_min=_r(sum(v * d for v, d in seg) / 60, 2),
                       H2O_n_steps=len({v for v in vals if v > 0}))
    o2 = _channel(mfc, "MFC-3")
    if o2:
        out["O2_dose_sccm_min"] = _r(sum(v * d for v, d in _segments(mfc, o2, s, e)) / 60, 2)
    for k, v in list(out.items()):
        if isinstance(v, float):
            out[k] = round(v, 3)
    return out, flags


# --------------------------------------------------------------- milestone
def w_source_of(value) -> Optional[str]:
    """Milestone's W cell as a precursor; None for NA, blank or unreadable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() == "NA":
        return None
    if "foil" in text.lower():
        return "W foil"
    if "WO3" in text.upper():
        return "WO3"
    if "CO" in text.upper():
        return "W(CO)6"
    try:
        float(text)
        return "W(CO)6"
    except ValueError:
        return None


MILESTONE_SHEETS = ("VA_SplitTable", "VB_SplitTable", "VAHA_SplitTable")


def read_milestone(book) -> Dict[str, dict]:
    """{wafer key: joined columns} from an openpyxl workbook of Milestone and Schedule."""
    runs = {}
    want = {"Baseline": "Baseline", "Date": "Date", "User": "User", "Purpose": "Purpose",
            "Result": "Result", "Remark": "Remark", "Bilayer": "Bilayer", "Dist.": "Dist_cm",
            "SH": "SH", "Pretr.": "Pretreatment", "Posttr.": "Posttreatment", "Coat": "Coat",
            "Issue": "Issue", "P": "_P", "T": "_T", "H2Se": "_H2Se", "Ar in": "_Ar_in"}
    for sheet in MILESTONE_SHEETS:
        if sheet not in book.sheetnames:
            continue
        rows = list(book[sheet].iter_rows(values_only=True))
        head = [str(h).strip() if h else None for h in rows[0]]
        first = {}
        for i, h in enumerate(head):
            if h and h not in first:
                first[h] = i
        w_i = first.get("W")
        o2_i = first.get("O2")
        pulse = [i for i, h in enumerate(head) if h == "Pulse"]
        for r in rows[2:]:
            run = r[first["Run"]]
            if not run:
                continue
            key = canonical(run)
            rec = {out: r[first[h]] for h, out in want.items() if h in first}
            rec["W_raw"] = r[w_i] if w_i is not None else None
            rec["W_source"] = w_source_of(rec["W_raw"])
            rec["W_amount"] = r[w_i + 1] if w_i is not None else None
            rec["_O2"] = r[o2_i] if o2_i is not None else None
            rec["Pulse"] = " / ".join(str(r[i]) for i in pulse if r[i] is not None) or None
            rec["_sheet"] = sheet
            if key in runs:
                if runs[key].get("W_source") is None:
                    runs[key]["W_source"] = rec["W_source"]
                continue
            runs[key] = rec
    return runs


def series_majority(runs: Mapping[str, dict]) -> Dict[str, str]:
    counts: Dict[str, Counter] = {}
    for key, rec in runs.items():
        if rec.get("W_source"):
            counts.setdefault(series(key), Counter())[rec["W_source"]] += 1
    return {s: c.most_common(1)[0][0] for s, c in counts.items()}


def disagreements(row, rec):
    out = []

    def differs(a, b):
        return abs(a - b) > max(0.05, 0.02 * abs(b))

    for col, key in (("P_torr", "_P"), ("T_growth_C", "_T"), ("H2Se_on_sccm", "_H2Se"),
                     ("O2_sccm", "_O2"), ("Ar_in_sccm", "_Ar_in")):
        a, b = row.get(col), _num(rec.get(key))
        if a is None or b is None:
            continue
        # The sheet's "Ar in" is usually the pre-growth MFC-4 setting (150 where
        # the card drops to 110 at growth); only both disagreeing is a disagreement.
        alt = row.get("Ar_in_pre_sccm") if col == "Ar_in_sccm" else None
        if differs(a, b) and (alt is None or differs(alt, b)):
            out.append("{} card {:g} vs sheet {:g}".format(col, a, b))
    return "; ".join(out) or None


def w_source_for(key, rec, rules, majority) -> tuple:
    """(W_source, W_source_rule, Precursor_check) for one wafer."""
    sheet = (rec or {}).get("W_source")
    by_id = precursor_for(key, rules)
    check = None
    if sheet and by_id and sheet != by_id[0]:
        check = f"Milestone {sheet} vs run ID {by_id[1]} {by_id[0]}"
    if sheet:
        return sheet, "Milestone", check
    if by_id:
        return by_id[0], f"run ID {by_id[1]}", check
    if majority.get(series(key)):
        return majority[series(key)], "series majority", check
    return None, None, check


# --------------------------------------------------------------- QC
def spec_limits():
    """(E2g+A1g FWHM spec, B2g centre, LA/E2g+A1g spec) as peak_quality states them."""
    from modules.spectra.viz.peak_quality import (
        _RAMAN_RATIO_PRESENTATION,
        CUSTOMER_SPEC,
        PANEL_COLUMNS,
    )
    limits = {}
    for _title, panels in PANEL_COLUMNS:
        for panel in panels:
            if getattr(panel, "spec", None) is not None and hasattr(panel, "peak"):
                limits[(panel.peak, panel.metric)] = panel.spec
    _, _, defect = _RAMAN_RATIO_PRESENTATION[("LA", "E2g+A1g")]
    return (limits[("E2g+A1g", "fwhm")], CUSTOMER_SPEC["Raman"]["near"]["B2g"], defect)


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v))


# --------------------------------------------------------------- build
@dataclass
class BuildInputs:
    """Everything one build reads, already loaded."""

    runcard_dir: Path
    runs: pd.DataFrame                    # the datalog run list (datalog_runs)
    milestone: Dict[str, dict]
    qc: Dict[str, QCChoice]
    rules: Dict[str, Dict[str, str]]
    superseded_cards: frozenset = frozenset()
    card_cache: Dict[str, tuple] = field(default_factory=dict)   # path -> (mtime, values, flags)


@dataclass
class Built:
    table: pd.DataFrame
    w_source_recipe: pd.DataFrame
    notes: List[str]


def _card_values(path, cache):
    mtime = os.path.getmtime(path)
    hit = cache.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1], list(hit[2])
    try:
        values, flags = parse_card(path)
    except Exception as exc:  # unreadable or malformed card
        values, flags = {}, ["card unreadable: {}".format(exc)]
    cache[path] = (mtime, values, flags)
    return values, list(flags)


def build_rows(inputs: BuildInputs) -> Built:
    """The table, in run order, plus the per-wafer recipe W source."""
    runcard_dir = str(inputs.runcard_dir)
    cards = card_index(inputs.runcard_dir, inputs.superseded_cards)
    sheet_runs, qc = inputs.milestone, inputs.qc
    majority = series_majority(sheet_runs)
    fwhm_spec, b2g_spec, defect_spec = spec_limits()
    rows, notes = [], []

    def fill(row, key):
        rec = sheet_runs.get(key)
        if is_wafer(key):
            row["Sheet_row"] = "present" if rec else "absent"
        if rec:
            for col in ("Baseline", "User", "Date", "Purpose", "Result", "Remark", "Bilayer",
                        "Dist_cm", "SH", "Pretreatment", "Posttreatment", "Coat", "Pulse",
                        "Issue", "W_amount"):
                v = rec.get(col)
                row[col] = v.date() if isinstance(v, datetime.datetime) else v
            row["Disagrees_with_sheet"] = disagreements(row, rec)
        if is_wafer(key):
            row["W_source"], row["W_source_rule"], row["Precursor_check"] = w_source_for(
                key, rec, inputs.rules, majority)
        elif rec and rec.get("W_source"):
            # A named run that is not a wafer (VABDTC): the sheet's word only.
            row["W_source"], row["W_source_rule"] = rec["W_source"], "Milestone"
        choice = qc.get(key)
        if choice:
            q = choice.record
            row["QC_Source"] = choice.source
            for col, src in QC_MAP.items():
                v = q.get(src)
                row[col] = None if _blank(v) else v
            shown = {qc_block(QC_MAP[c]) for c in QC_MAP} - {None}
            fills = [f for f in choice.fills if f.split(" ", 1)[0] in shown]
            if fills:
                note = "QC filled: " + "; ".join(fills)
                remark = row.get("Remark")
                row["Remark"] = f"{remark} | {note}" if not _blank(remark) else note
            if row.get("E2g_A1g_FWHM") is not None:
                row["FWHM_In_Spec"] = bool(row["E2g_A1g_FWHM"] <= fwhm_spec)
            if row.get("B2g_Center") is not None:
                row["B2g_In_Spec"] = bool(row["B2g_Center"] >= b2g_spec)
            if row.get("Defect_Ratio_Median") is not None:
                row["Defect_In_Spec"] = bool(row["Defect_Ratio_Median"] <= defect_spec)

    def base_row(key, card_path):
        row = {c: None for c in COLUMNS}
        row["WaferID"] = key
        if is_wafer(key):
            row["Machine"] = "VA" if key.startswith("VA") else "VB"
            row["Series"] = series(key)
        flags = []
        if card_path:
            row["Runcard"] = os.path.relpath(card_path, runcard_dir)
            row["Runcard_Saved"] = datetime.datetime.fromtimestamp(
                os.path.getmtime(card_path)).replace(microsecond=0)
            values, flags = _card_values(card_path, inputs.card_cache)
            row.update(values)
        return row, list(flags)

    # 1. every logged run
    seen = set()
    runs_log = inputs.runs.copy()
    runs_log["Start"] = pd.to_datetime(runs_log["Start"])
    runs_log["End"] = pd.to_datetime(runs_log["End"])
    for _, lg in runs_log.sort_values("Start").iterrows():
        name = lg["Name"] if isinstance(lg["Name"], str) else ""
        if lg["Wait_s"] / 60 < FALSE_START_WAIT_MIN:
            notes.append("false start left out: {} {} ({:.1f} min, {:.0f} s waited) -> {}".format(
                lg["Start"], lg["Datalog"], lg["Minutes"], lg["Wait_s"], name or "unnamed"))
            continue
        key = canonical(name) if name else None
        card_path = None
        if isinstance(lg.get("Card"), str) and lg["Card"]:
            card_path = os.path.join(runcard_dir, lg["Card"])
        elif key:
            card_path = (cards.get(key) or cards.get(name.upper()) or [None])[0]
        row, flags = base_row(key or "(unmatched)", card_path)
        if key and is_wafer(key):
            kind = "growth" if _has_se(row) else "conditioning (no H2Se)"
            seen.add(key)
        elif key:
            kind = run_kind(key)
        else:
            kind = "unknown"
            flags.append("no runcard match")
        ended = lg["Ended"] if isinstance(lg["Ended"], bool) else str(lg["Ended"]) == "True"
        if not ended:
            kind += " (stopped)"
            flags.append("stopped before End after {:g} min".format(lg["Minutes"]))
        row.update(Run_Type=kind, Process_Start=lg["Start"].to_pydatetime(),
                   Process_End=lg["End"].to_pydatetime(), Process_Time_min=lg["Minutes"],
                   Wait_Clock_min=round(lg["Wait_s"] / 60, 1), Datalog=lg["Datalog"],
                   Month=int(lg["Start"].strftime("%Y%m")),
                   Datalog_Match="tag" if lg["How"] == "tag" else "{} ({}, cost {})".format(
                       lg["How"], lg["Confidence"], lg["Cost"]))
        per_wafer_card = bool(card_path) and os.path.dirname(card_path) != runcard_dir
        if lg["How"] == "matched" and per_wafer_card and key and is_wafer(key) \
                and os.path.exists(card_path):
            # VB cards are near-identical across a series, so the matcher's
            # content-based confidence is mostly "low"; what supports a match
            # is the card having been saved just before the run (0-6 h for
            # 70 % of them). Flag only a save time that does not fit.
            dt_h = (lg["Start"] - pd.Timestamp.fromtimestamp(
                os.path.getmtime(card_path))).total_seconds() / 3600
            row["Datalog_Match"] += ", card saved {:+.1f} h before".format(dt_h)
            if not (-lg["Minutes"] / 60 <= dt_h <= 48):
                flags.append("card saved {:+.0f} h from the run -- match unconfirmed".format(dt_h))
        elif lg["How"] == "matched" and key and not is_wafer(key) and run_kind(key) == "test":
            flags.append("matched to a generic test recipe ({}) -- real card may be missing".format(key))
        if key:
            fill(row, key)
        row["Flag"] = "; ".join(flags) or None
        rows.append(row)

    # 2. wafers with a card, a sheet row or QC but no logged run
    keys = {k for k in cards if is_wafer(k)} | {k for k in sheet_runs if is_wafer(k)} | set(qc)
    for key in sorted(keys - seen, key=natural):
        card_path = (cards.get(key) or [None])[0]
        row, flags = base_row(key, card_path)
        row["Run_Type"] = ("growth" if _has_se(row) else "conditioning (no H2Se)") + " (no datalog)"
        q = qc.get(key)
        month = q.record.get("Month") if q else None
        if not _blank(month):
            row["Month"] = int(month)
        elif card_path:
            row["Month"] = int(row["Runcard_Saved"].strftime("%Y%m"))
        elif sheet_runs.get(key, {}).get("Date"):
            d = sheet_runs[key]["Date"]
            row["Month"] = int(d.strftime("%Y%m")) if hasattr(d, "strftime") else None
        if not card_path:
            flags.append("no runcard")
        fill(row, key)
        row["Flag"] = "; ".join(flags) or None
        rows.append(row)

    recipe = _w_source_recipe(rows)
    for r in rows:
        if r["WaferID"] in recipe:
            r["W_source_recipe"] = recipe[r["WaferID"]][0]
    w_table = pd.DataFrame(
        [(k, *v) for k, v in sorted(recipe.items(), key=lambda kv: natural(kv[0]))],
        columns=["WaferID", "W_source_recipe", "Basis"])

    table = pd.DataFrame(rows, columns=COLUMNS)
    table = sort_rows(table)
    return Built(table=table, w_source_recipe=w_table, notes=notes)


def _w_source_recipe(rows) -> Dict[str, tuple]:
    """Per wafer: any card flowing the W bubbler -> W(CO)6; VAAA -> W foil; else WO3."""
    carrier, carded, fallback = {}, set(), {}
    for r in rows:
        key = r["WaferID"]
        if is_wafer(key):
            carrier[key] = carrier.get(key, False) or bool(r.get("W_carrier_sccm"))
            if r.get("Runcard"):
                carded.add(key)
            fallback.setdefault(key, r.get("W_source"))
    rule = {}
    for key, has_w in carrier.items():
        if key.startswith("VAAA"):
            rule[key] = ("W foil", "VAAA series")
        elif has_w:
            rule[key] = ("W(CO)6", "W_carrier_sccm set")
        elif key in carded:
            rule[key] = ("WO3", "card has no W bubbler flow")
        else:
            # No card to read the bubbler from: keep the Milestone / run-ID / series source.
            rule[key] = (fallback.get(key), "no runcard; W_source kept")
    return rule


def sort_rows(table: pd.DataFrame) -> pd.DataFrame:
    """Run order: Process_Start, else Runcard_Saved; rows with neither last, by name."""
    when = pd.to_datetime(table["Process_Start"], errors="coerce")
    if "Runcard_Saved" in table:
        when = when.fillna(pd.to_datetime(table["Runcard_Saved"], errors="coerce"))
    order = sorted(range(len(table)), key=lambda i: (
        pd.isna(when.iloc[i]), when.iloc[i] if pd.notna(when.iloc[i]) else pd.Timestamp.min,
        natural(table["WaferID"].iloc[i])))
    return table.iloc[order].reset_index(drop=True)
