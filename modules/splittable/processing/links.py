"""
From a split-table row to the files it was built from.

The table records where it found each run, but not uniformly: VB writes the
runcard (``LYH\\VBCA11.csv``, under RUNCARD) and the optical folder
(``202610/VBCA11``, under OPTICAL) into columns of their own, while HA records
only the datalog. Both write ``Datalog`` relative to the tool's DATALOG folder,
as ``2026_10/13184401.csv``. Whatever a row does not say is looked up by wafer
ID through the tool layout, as the OneNote page does.

**The datalog may have been renamed since the table was written.** Runcard
detection renames ``13184401.csv`` to ``13184401~HADI51.csv``, and the table
keeps the old name, so a file missing under its recorded name is looked for
as ``<stem>~*.csv`` beside it.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from core.io import tool_layout
from core.io.tool_layout import ToolLayout


@dataclass(frozen=True)
class RowLinks:
    """What one row resolves to on disk; None is *not linked*, never *missing*."""

    datalog: Optional[Path] = None
    runcard: Optional[Path] = None
    optical: Optional[Path] = None


def resolve_row(layout: ToolLayout, row: pd.Series) -> RowLinks:
    """The datalog, runcard and optical folder behind one split-table row."""
    wafer = _text(row.get("WaferID"))
    found = tool_layout.find_wafer(layout, wafer) if wafer else tool_layout.WaferPaths("")

    datalog = _recorded_datalog(layout, _text(row.get("Datalog"))) or found.datalog
    runcard = (
        _existing(layout.root / "RUNCARD", _text(row.get("Runcard")))
        or _in_month(layout, tool_layout.RUNCARD, wafer, _text(row.get("Month")))
        or found.runcard
    )
    optical = (
        _existing(layout.subfolder(tool_layout.OPTICAL), _text(row.get("Optical_Folder")))
        or _in_month(layout, tool_layout.OPTICAL, wafer, _text(row.get("Month")))
        or found.optical
    )
    return RowLinks(datalog=datalog, runcard=runcard, optical=optical)


def _text(value) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    return str(value).strip()


def _existing(base: Optional[Path], relative: str) -> Optional[Path]:
    if base is None or not relative:
        return None
    candidate = base / relative.replace("\\", "/")
    return candidate if candidate.exists() else None


def _recorded_datalog(layout: ToolLayout, relative: str) -> Optional[Path]:
    folder = layout.subfolder(tool_layout.DATALOG)
    exact = _existing(folder, relative)
    if exact is not None or folder is None or not relative:
        return exact
    recorded = folder / relative.replace("\\", "/")
    if not recorded.parent.is_dir():
        return None
    renamed = sorted(recorded.parent.glob(f"{recorded.stem}~*{recorded.suffix}"))
    return renamed[0] if renamed else None


def _in_month(layout: ToolLayout, role: str, wafer: str, month: str) -> Optional[Path]:
    """The wafer's file in the table's own month, before any other month's.

    `find_wafer` takes the newest match across months, and six HA wafers have
    a runcard in two adjacent months; the row's Month says which one it is.
    """
    if not wafer or not month:
        return None
    for pattern in layout.patterns(role):
        if "/*/" not in pattern:
            continue
        dated = pattern.replace("/*/", f"/{month}/", 1).format(wafer=wafer)
        matches = sorted(layout.root.glob(dated))
        if matches:
            return matches[0]
    return None
