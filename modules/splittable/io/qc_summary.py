"""
A tool's QC summary: every QC Report's numbers, one row per wafer and preset,
beside the frozen numbers the split table used before reports were collected.

``OPTICAL/<YYYYMM>/<wafer>/<sample>_QC_Report.xlsx`` is what the QC Report
page saves; this module reads it back into the column names the old summary
scripts wrote (``Raman_E2g+A1g_FWHM``, ``OM_Reference_pct`` ...), so a report
row and a legacy row line up cell for cell. The summary workbook it keeps
(``VB_QC_summary.xlsx``) has three sheets of record:

- ``QC_Reports`` -- one row per report file and preset. A report regenerated
  with the **same** preset replaces its row; regenerated with **another**
  preset, the old row stays (``Current`` False) and a new one is added, so
  every preset a wafer was ever reported with is still there.
- ``Legacy`` -- the values the split table carried before this collector,
  copied once from the old summaries and never rewritten. ``Fit_Mode`` says
  which fit made them.
- ``Missing`` -- wafer folders that have no QC Report, i.e. wafers whose QC
  can only come from Legacy, if at all.

Which of a wafer's rows the split table shows is
`modules.splittable.processing.qc_select`'s decision, not this module's.

**The wafer is the folder, not the sample name.** ``OPTICAL/202610/VBCA00``
holds both ``VBCA00_QC_Report.xlsx`` and ``VBBG00_QC_Report.xlsx`` (the same
wafer before a rename); both are VBCA00's.

**OM is re-derived from ``OM_Points``** exactly as
``OPTICAL/_tools/build_om_wse2_qc.py`` does for a frame it cannot read (it
reproduced VBCA00-03 to four decimals that way): coverages are the frames'
means, Std their sample std, ``OM_Sigma_Pct_Mode`` the mean of
``Sigma_Noise / Green_Mode``, and ``OM_Discriminable`` the smaller cut over
that noise clearing 2.
"""

import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

import numpy as np
import pandas as pd

from core.io.shared_read import read_bytes_shared

#: Where reports live under the tool's optical folder.
REPORT_GLOB = "[0-9][0-9][0-9][0-9][0-9][0-9]/*/*_QC_Report.xlsx"
REPORT_SUFFIX = "_QC_Report.xlsx"

SHEET_REPORTS = "QC_Reports"
SHEET_LEGACY = "Legacy"
SHEET_MISSING = "Missing"
SHEET_NOTES = "Notes"

#: Cut / frame noise a contrast pair must clear for its percentages to mean
#: anything (CLAUDE.md, "Thresholding"; build_om_raman_pl_summary.GATE_RATIO).
GATE_RATIO = 2.0
#: A spectrum fits well enough to count as "used" above this R²
#: (the legacy ``Raman_n_used_R2>0.5`` column).
R2_USED = 0.5

#: The identity columns every row of either sheet starts with.
IDENTITY = [
    "Wafer_ID", "Folder", "Month", "Fit_Mode", "Layer", "NexAnalyzer_version",
    "QC_Report_Date",
]
#: Report-only bookkeeping: how a changed file is recognised.
REPORT_BOOKKEEPING = ["Report_File", "Report_Sample", "Report_Modified", "Report_Size", "Current"]

_POSITIONS = ("Below", "Reference", "Above")


# ------------------------------------------------------------------ one report
def _summary_blocks(rows: list) -> tuple[dict, Dict[str, pd.DataFrame]]:
    """(identity, {heading: table}) from the Summary sheet's stacked blocks.

    The identity block is label/value pairs above the first heading; each
    table is a heading row, a header row, then body rows to the next blank.
    """
    identity: dict = {}
    blocks: Dict[str, pd.DataFrame] = {}
    i = 1
    while i < len(rows):
        row = rows[i]
        first = row[0] if row else None
        if first is None:
            i += 1
            continue
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if nxt and nxt[0] in ("Peak", "Ratio", "Technique"):
            header = [h for h in nxt if h is not None]
            body = []
            j = i + 2
            while j < len(rows) and rows[j] and rows[j][0] is not None:
                body.append(list(rows[j])[:len(header)])
                j += 1
            blocks[str(first)] = pd.DataFrame(body, columns=header)
            i = j
            continue
        if not blocks:
            identity[str(first).strip()] = row[1] if len(row) > 1 else None
        i += 1
    return identity, blocks


def _technique_of(heading: str) -> Optional[str]:
    for technique in ("Raman", "PL"):
        if heading.startswith(technique + " "):
            return technique
    return None


def _peak_cells(technique: str, table: pd.DataFrame) -> dict:
    cells = {}
    for _, r in table.iterrows():
        peak = f"{technique}_{r['Peak']}"
        cells.update({
            f"{peak}_Center": r.get("Center_Mean"), f"{peak}_Center_std": r.get("Center_Std"),
            f"{peak}_FWHM": r.get("FWHM_Mean"), f"{peak}_FWHM_std": r.get("FWHM_Std"),
            f"{peak}_Intensity": r.get("Intensity_Mean"),
            f"{peak}_Intensity_std": r.get("Intensity_Std"),
            f"{peak}_n": r.get("N"),
        })
    return cells


def _ratio_cells(technique: str, table: pd.DataFrame) -> dict:
    cells = {}
    for _, r in table.iterrows():
        name = f"{technique}_{str(r['Ratio']).replace(' ', '')}"
        cells.update({f"{name}_median": r.get("Median"), f"{name}_MAD": r.get("MAD"),
                      f"{name}_n": r.get("N")})
    return cells


def _spectra_cells(technique: str, fits: Optional[pd.DataFrame]) -> dict:
    """Spectrum counts and mean R² from a per-peak sheet (one row per peak)."""
    if fits is None or fits.empty or "R_Squared" not in fits:
        return {}
    key = "Spectrum" if "Spectrum" in fits else "Point"
    r2 = fits.groupby(key)["R_Squared"].first().astype(float)
    return {f"{technique}_n_spectra": int(len(r2)),
            f"{technique}_n_used_R2>0.5": int((r2 > R2_USED).sum()),
            f"{technique}_R2_mean": float(r2.mean())}


def _om_cells(points: Optional[pd.DataFrame], stats: Optional[pd.DataFrame]) -> dict:
    """OM columns from the per-frame sheet -- see the module docstring."""
    if points is None or points.empty:
        return {}
    cells: dict = {"OM_Frames": int(len(points))}
    for name in _POSITIONS:
        column = f"{name}_Coverage_pct"
        if column in points:
            cells[f"OM_{name}_pct"] = float(points[column].mean())
            cells[f"OM_{name}_Std"] = (float(points[column].std(ddof=1))
                                      if len(points) > 1 else None)
    if "Frame_Type" in points and points["Frame_Type"].notna().any():
        cells["OM_Frame_Type"] = str(points["Frame_Type"].dropna().iloc[0])
    pair = None
    if stats is not None and {"Threshold_Below_pct", "Threshold_Above_pct"} <= set(stats):
        below, above = stats["Threshold_Below_pct"].iloc[0], stats["Threshold_Above_pct"].iloc[0]
        if pd.notna(below) and pd.notna(above):
            pair = (abs(float(below)), abs(float(above)))
            cells["OM_Cut_Below_pct"], cells["OM_Cut_Above_pct"] = pair
    if {"Sigma_Noise", "Green_Mode"} <= set(points):
        sigma_pct = float(np.mean(points["Sigma_Noise"] / points["Green_Mode"] * 100.0))
        cells["OM_Sigma_Pct_Mode"] = sigma_pct
        if pair and sigma_pct:
            ratio = min(pair) / sigma_pct
            cells["OM_Threshold_Sigma_Ratio"] = ratio
            cells["OM_Discriminable"] = bool(ratio >= GATE_RATIO)
    if "Shoulder" in points:
        cells["OM_Shoulder_Frames"] = int(points["Shoulder"].fillna(False).astype(bool).sum())
    return cells


def read_qc_report(path: Union[str, Path], optical_root: Union[str, Path]) -> dict:
    """One report as one flat record in the summary's column names.

    Raises what reading raises; the caller records the file as unreadable.
    """
    path = Path(path)
    book = pd.ExcelFile(io.BytesIO(read_bytes_shared(path)))
    sheets = set(book.sheet_names)
    summary_rows = [tuple(r) for r in book.parse("Summary", header=None).itertuples(index=False)]
    summary_rows = [tuple(None if (isinstance(v, float) and np.isnan(v)) else v for v in r)
                    for r in summary_rows]
    identity, blocks = _summary_blocks(summary_rows)

    folder = path.parent
    month = folder.parent.name
    stat = path.stat()
    record = {
        "Wafer_ID": folder.name,
        "Folder": folder.relative_to(optical_root).as_posix(),
        "Month": int(month) if month.isdigit() else None,
        "Fit_Mode": identity.get("Material"),
        "Layer": identity.get("Layer"),
        "NexAnalyzer_version": identity.get("NexAnalyzer"),
        "QC_Report_Date": identity.get("Date"),
        "Report_File": path.name,
        "Report_Sample": identity.get("Sample"),
        "Report_Modified": datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0),
        "Report_Size": int(stat.st_size),
        "Current": True,
    }
    for heading, table in blocks.items():
        technique = _technique_of(heading)
        if technique is None:
            continue
        if "Peak" in table:
            record.update(_peak_cells(technique, table))
        elif "Ratio" in table:
            record.update(_ratio_cells(technique, table))
    for technique in ("Raman", "PL"):
        if technique in sheets:
            record.update(_spectra_cells(technique, book.parse(technique)))
    points = book.parse("OM_Points") if "OM_Points" in sheets else None
    stats = book.parse("OM_Stats") if "OM_Stats" in sheets else None
    record.update(_om_cells(points, stats))
    if not record["Layer"] and points is not None and "Reference_Layer" in points:
        layers = points["Reference_Layer"].dropna()
        record["Layer"] = str(layers.iloc[0]) if len(layers) else None
    return record


# ------------------------------------------------------------- the summary file
@dataclass
class QCSummary:
    """The summary's three sheets of record, as frames."""

    reports: pd.DataFrame
    legacy: pd.DataFrame
    missing: pd.DataFrame = field(default_factory=pd.DataFrame)
    notes: List[str] = field(default_factory=list)


def read_qc_summary(path: Union[str, Path]) -> Optional[QCSummary]:
    """The saved summary, or None when there is none yet."""
    path = Path(path)
    if not path.is_file():
        return None
    book = pd.ExcelFile(io.BytesIO(read_bytes_shared(path)))
    def sheet(name):
        return book.parse(name) if name in book.sheet_names else pd.DataFrame()
    return QCSummary(reports=sheet(SHEET_REPORTS), legacy=sheet(SHEET_LEGACY),
                     missing=sheet(SHEET_MISSING))


def _ordered(frame: pd.DataFrame, lead: List[str]) -> pd.DataFrame:
    lead = [c for c in lead if c in frame.columns]
    return frame[lead + [c for c in frame.columns if c not in lead]]


def write_qc_summary(summary: QCSummary, path: Union[str, Path], when: datetime) -> None:
    """Write all sheets to a temp file and swap it in, so a crash leaves the old one."""
    path = Path(path)
    tmp = path.with_name(path.stem + ".tmp.xlsx")
    notes = pd.DataFrame({"Item": ["Generated"] + [""] * len(summary.notes),
                          "Value": [when.strftime("%Y-%m-%d %H:%M")] + summary.notes})
    with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
        _ordered(summary.reports, IDENTITY + REPORT_BOOKKEEPING).to_excel(
            writer, sheet_name=SHEET_REPORTS, index=False)
        _ordered(summary.legacy, ["Legacy_Source"] + IDENTITY).to_excel(
            writer, sheet_name=SHEET_LEGACY, index=False)
        summary.missing.to_excel(writer, sheet_name=SHEET_MISSING, index=False)
        notes.to_excel(writer, sheet_name=SHEET_NOTES, index=False)
        for name in (SHEET_REPORTS, SHEET_LEGACY):
            writer.sheets[name].freeze_panes = "B2"
    tmp.replace(path)


# ------------------------------------------------------------------- refresh
@dataclass
class QCRefresh:
    """What one collection pass did, for the page to report."""

    summary: QCSummary
    added: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    kept_preset: List[str] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.kept_preset)


def _same_file(row, record) -> bool:
    return (pd.Timestamp(row["Report_Modified"]) == pd.Timestamp(record["Report_Modified"])
            and int(row["Report_Size"]) == int(record["Report_Size"]))


def collect_reports(
    optical_root: Union[str, Path],
    previous: Optional[pd.DataFrame],
    reader: Callable = read_qc_report,
) -> QCRefresh:
    """Bring ``QC_Reports`` up to date with the reports on disk.

    A file whose modified time and size match its current row is not reread.
    """
    optical_root = Path(optical_root)
    rows = [] if previous is None or previous.empty else previous.to_dict("records")
    current = {(r["Folder"], r["Report_File"]): r for r in rows if bool(r.get("Current"))}
    result = QCRefresh(summary=QCSummary(reports=pd.DataFrame(), legacy=pd.DataFrame()))

    for path in sorted(optical_root.glob(REPORT_GLOB)):
        folder = path.parent.relative_to(optical_root).as_posix()
        old = current.get((folder, path.name))
        try:
            stat = path.stat()
            probe = {"Report_Modified": datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0),
                     "Report_Size": stat.st_size}
            if old is not None and _same_file(old, probe):
                continue
            record = reader(path, optical_root)
        except Exception as e:  # OneDrive placeholder, a half-written file
            result.unreadable.append(f"{folder}/{path.name}: {e}")
            continue
        label = f"{folder}/{path.name} ({record['Fit_Mode']})"
        if old is None:
            rows.append(record)
            result.added.append(label)
        elif old.get("Fit_Mode") == record["Fit_Mode"]:
            rows[rows.index(old)] = record
            result.updated.append(label)
        else:
            old["Current"] = False
            rows.append(record)
            result.kept_preset.append(f"{label}; {old.get('Fit_Mode')} row kept")

    reports = pd.DataFrame(rows)
    if not reports.empty:
        reports = reports.sort_values(["Folder", "Report_File", "Report_Modified"],
                                      kind="stable").reset_index(drop=True)
    result.summary.reports = reports
    return result


def missing_reports(optical_root: Union[str, Path], reports: pd.DataFrame) -> pd.DataFrame:
    """Wafer folders under the month folders with no QC Report in them."""
    optical_root = Path(optical_root)
    have = set(reports["Folder"]) if not reports.empty else set()
    rows = []
    for month in sorted(optical_root.glob("[0-9][0-9][0-9][0-9][0-9][0-9]")):
        for folder in sorted(p for p in month.iterdir() if p.is_dir()):
            rel = folder.relative_to(optical_root).as_posix()
            if rel not in have and not folder.name.startswith(("_", ".")):
                rows.append({"Wafer_ID": folder.name, "Folder": rel})
    return pd.DataFrame(rows, columns=["Wafer_ID", "Folder"])


# ------------------------------------------------------------------- legacy
def build_legacy(sources: List[dict]) -> pd.DataFrame:
    """The frozen Legacy sheet from the old summaries, built once.

    Each source is ``{"path", "sheet", "series" (optional tuple of 4-letter
    prefixes), "om_csv" (optional)}``. One row per wafer and fit mode, so a
    wafer both old summaries cover keeps both, each labelled; ``series``
    limits a source to the wafers the old split-table builder took from it.
    """
    by_wafer: Dict[tuple, dict] = {}
    for source in sources:
        path = Path(source["path"])
        if not path.is_file():
            continue
        frame = pd.read_excel(io.BytesIO(read_bytes_shared(path)), sheet_name=source["sheet"])
        om = {}
        if source.get("om_csv") and Path(source["om_csv"]).is_file():
            om_frame = pd.read_csv(source["om_csv"], encoding="utf-8-sig")
            om = {r["Wafer_ID"]: r for r in om_frame.to_dict("records")}
        series = tuple(source.get("series") or ())
        for record in frame.to_dict("records"):
            wafer = str(record.get("Wafer_ID"))
            if series and not wafer.startswith(series):
                continue
            record = {k: v for k, v in record.items() if not (isinstance(v, float) and np.isnan(v))}
            record.update({k: v for k, v in om.get(wafer, {}).items()
                           if k not in ("Wafer_ID", "Folder")
                           and not (isinstance(v, float) and np.isnan(v))})
            if "OM_Layer" in record and not record.get("Layer"):
                record["Layer"] = record["OM_Layer"]
            elif "OM_Ref_Layer" in record and not record.get("Layer"):
                record["Layer"] = record["OM_Ref_Layer"]
            if not record.get("Fit_Mode"):
                record["Fit_Mode"] = source["sheet"]
            record["Legacy_Source"] = f"{path.name} [{source['sheet']}]" + (
                f" + {Path(source['om_csv']).name}" if om else "")
            by_wafer[(wafer, record["Fit_Mode"])] = record
    return pd.DataFrame(list(by_wafer.values()))
