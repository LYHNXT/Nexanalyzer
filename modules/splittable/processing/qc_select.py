"""
Which of a wafer's QC rows the split table shows.

A wafer can have several: a QC Report per preset it was ever run with
(``QC_Reports``), and the frozen values the table carried before reports were
collected (``Legacy``). The order is the user's (2026-11-08):

1. a fit mode containing **"QC"** (``WSe2_QC``, legacy ``3_WSe2_QC``) -- of
   two, the newer report; a legacy row carries no report date and so loses a
   tie to any dated report;
2. else the **newest report** with any other preset;
3. else the **legacy** value, the QC-mode one first.

Newest is the report's own ``Date`` cell, then its file's modified time, so a
report regenerated today beats one written yesterday with the same preset.

**Gaps are filled, block by block** (user, 2026-11-08: "fill them, add a note
... if things like this happen again, do so"). A measurement block the chosen
row has none of -- ``PL_Raw_*`` on a legacy row, which the old summaries never
had -- is taken whole from the best-ranked other row that has it, and
`QCChoice.fills` says from where. A block is never assembled from two rows,
and the identity (source, fit mode, layer, date) stays the chosen row's.
"""

from dataclasses import dataclass
from typing import Callable, Dict, Optional

import pandas as pd

#: What ``QC_Source`` says for each kind of row.
SOURCE_REPORT = "QC Report"
SOURCE_LEGACY = "legacy"


@dataclass(frozen=True)
class QCChoice:
    """The row picked for a wafer, and how many it was picked from."""

    record: dict
    source: str
    candidates: int
    fills: tuple = ()       # "PL_Raw from VBCA00_QC_Report.xlsx (WSe2, 2026-10-21)"


def is_qc_mode(fit_mode) -> bool:
    return isinstance(fit_mode, str) and "QC" in fit_mode.upper()


def _when(record: dict) -> pd.Timestamp:
    for key in ("QC_Report_Date", "Report_Modified"):
        value = pd.to_datetime(record.get(key), errors="coerce")
        if pd.notna(value):
            return value
    return pd.Timestamp.min


#: Measurement columns start with these; everything else is identity.
_MEASURED = ("OM_", "Raman_", "PL_")


def block(column: str) -> Optional[str]:
    """The block a measurement column belongs to: OM whole, else technique_peak."""
    if not column.startswith(_MEASURED):
        return None
    if column.startswith("OM_"):
        return "OM"
    return "_".join(column.split("_")[:2])


def _blocks(record: dict) -> Dict[str, list]:
    out: Dict[str, list] = {}
    for column in record:
        name = block(column)
        if name:
            out.setdefault(name, []).append(column)
    return out


def _describe(record: dict, source: str) -> str:
    if source == SOURCE_REPORT:
        date = record.get("QC_Report_Date")
        when = f", {pd.to_datetime(date).date()}" if not _blank(date) else ""
        return f"{record.get('Report_File', 'QC Report')} ({record.get('Fit_Mode')}{when})"
    return f"legacy {record.get('Fit_Mode')}"


def _fill(chosen: dict, ranked: list) -> tuple:
    have = set(_blocks(chosen))
    record, fills = dict(chosen), []
    for other, source in ranked:
        for name, columns in _blocks(other).items():
            if name in have:
                continue
            have.add(name)
            record.update({c: other[c] for c in columns})
            fills.append(f"{name} from {_describe(other, source)}")
    return record, tuple(fills)


def _rank(record: dict, source: str) -> tuple:
    """Smaller is better."""
    report = source == SOURCE_REPORT
    qc = is_qc_mode(record.get("Fit_Mode"))
    tier = 0 if qc else (1 if report else 2)
    stamp = _when(record) if report else pd.Timestamp.min
    modified = pd.to_datetime(record.get("Report_Modified"), errors="coerce")
    modified = modified if pd.notna(modified) else pd.Timestamp.min
    # Within a tier: dated reports newest first, legacy (undated) last.
    return (tier, not report, -stamp.value, -modified.value)


def select_qc(
    reports: Optional[pd.DataFrame],
    legacy: Optional[pd.DataFrame],
    key: Callable[[str], str] = str,
) -> Dict[str, QCChoice]:
    """{wafer key: QCChoice}. `key` maps a Wafer_ID to the table's wafer key."""
    candidates: Dict[str, list] = {}
    for frame, source in ((reports, SOURCE_REPORT), (legacy, SOURCE_LEGACY)):
        if frame is None or frame.empty:
            continue
        for record in frame.to_dict("records"):
            record = {k: v for k, v in record.items() if not _blank(v)}
            if record.get("Wafer_ID"):
                candidates.setdefault(key(str(record["Wafer_ID"])), []).append((record, source))
    out = {}
    for wafer, rows in candidates.items():
        ranked = sorted(rows, key=lambda rs: _rank(*rs))
        (record, source), rest = ranked[0], ranked[1:]
        record, fills = _fill(record, rest)
        out[wafer] = QCChoice(record=record, source=source, candidates=len(rows), fills=fills)
    return out


def _blank(value) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value))
