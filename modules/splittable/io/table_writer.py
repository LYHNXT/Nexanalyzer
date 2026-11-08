"""
Writing a refreshed split table over the old one without losing what people
did to it.

**Hand edits are kept and flagged.** Every write stores what the refresh
computed in a hidden ``_Computed`` sheet. Next time, a visible cell that no
longer equals its ``_Computed`` twin was edited by hand: it is kept, and the
row's ``Hand_Edits`` cell names the column (with today's computed value when
that has moved). A column nobody's refresh writes, and a row the refresh never
made, are someone's own additions and are carried over as they are. The very
first refresh has no ``_Computed`` to compare with, so it cannot tell an edit
from a stale value: it takes the computed values and lists every cell it
changed in the ``Refresh_Changes`` sheet, for review against the backup.

**Rows are matched by run, not by position**: a logged run by its
Process_Start (a log can be renamed; the time its run started cannot), any
other row by WaferID.

**Nothing is written while it cannot be done whole.** Excel holding the file
open, or another refresh running (a lock file beside the table), stops the
write before anything changes. Otherwise the old file is copied to
``_backups/<name>.bak_<stamp>`` -- the convention of the SPLITTABLE scripts --
and the new one is saved to a temp file and swapped in.

The header row keeps each column's existing style (the group colours set by
the builder, or whatever someone changed them to); a new column gets its
group's colour.
"""

import io
import math
import os
import shutil
import time
from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from core.io.shared_read import read_bytes_shared

COMPUTED_SHEET = "_Computed"
CHANGES_SHEET = "Refresh_Changes"
HAND_EDITS = "Hand_Edits"
#: A lock older than this is from a refresh that died; it is taken over.
STALE_LOCK_S = 30 * 60

#: Header colours by column group (HA_SplitTable_from_runcards.xlsx's scheme).
GROUP_FILL = {"identity": "374151", "run": "374151", "process": "1F6F8B", "pulse": "1F6F8B",
              "source": "1F6F8B", "growth window": "0F766E", "H2O": "0F766E", "O2": "0F766E",
              "H2Se": "7C2D12", "cross-check": "92400E", "joined": "4B5563", "QC measured": "5B21B6"}
#: Non-growth rows are greyed so the wafers stand out.
QUIET_ROW = "E5E7EB"


class TableBusy(RuntimeError):
    """The workbook cannot be written now: open in Excel, or another refresh running."""


@dataclass
class WriteReport:
    rows: int = 0
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    hand_edits: List[str] = field(default_factory=list)
    first_refresh_changes: int = 0
    cells_changed: int = 0          # visible cells this refresh changes (any refresh)
    backup: Optional[Path] = None

    @property
    def unchanged(self) -> bool:
        """Nothing to write: no row in or out, no cell different from the sheet."""
        return not (self.added or self.removed or self.cells_changed)


# ------------------------------------------------------------------ guards
def excel_owner_file(path: Path) -> Path:
    """Excel's ``~$<name>`` marker, present while someone has the file open."""
    return path.with_name("~$" + path.name[2:] if len(path.name) > 2 else "~$" + path.name)


def check_writable(path: Path) -> None:
    """Raise `TableBusy` if Excel (or anything) holds the file against writing."""
    if not path.exists():
        return
    owner = path.with_name("~$" + path.name)
    if owner.exists() or excel_owner_file(path).exists():
        raise TableBusy(f"{path.name} is open in Excel. Close it there and press Refresh again.")
    try:
        with open(path, "r+b"):
            pass
    except PermissionError:
        raise TableBusy(f"{path.name} is open in another program. Close it and press Refresh again.")


@contextmanager
def refresh_lock(path: Path):
    """One refresh at a time per table, across PCs sharing the folder."""
    lock = path.with_name(path.name + ".refresh.lock")
    if lock.exists() and time.time() - lock.stat().st_mtime < STALE_LOCK_S:
        try:
            who = lock.read_text(encoding="utf-8").strip()
        except OSError:
            who = "another PC"
        raise TableBusy(f"A refresh of {path.name} is already running ({who}).")
    lock.write_text(f"{os.environ.get('COMPUTERNAME', '?')} since {datetime.now():%H:%M}",
                    encoding="utf-8")
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def backup_path(path: Path) -> Path:
    folder = path.parent / "_backups"
    folder.mkdir(exist_ok=True)
    return folder / f"{path.name}.bak_{time.strftime('%Y%m%d_%H%M%S')}"


# ------------------------------------------------------------------ merging
def row_key(row: Mapping) -> str:
    start = row.get("Process_Start")
    if start is not None and not (isinstance(start, float) and math.isnan(start)) and pd.notna(start):
        return "run:" + pd.Timestamp(start).strftime("%Y-%m-%d %H:%M:%S")
    return "wafer:" + str(row.get("WaferID"))


def _norm(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if v is pd.NaT:
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return pd.Timestamp(v).floor("s")
    if isinstance(v, date):
        return pd.Timestamp(v)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if hasattr(v, "item"):          # numpy scalar
        return _norm(v.item())
    text = str(v).strip()
    return text or None


def same(a, b) -> bool:
    if type(a) is type(b) and isinstance(a, (str, int, float)) and a == b:
        return True     # the common case, ~350k cells a press
    a, b = _norm(a), _norm(b)
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, float) and isinstance(b, str) or isinstance(a, str) and isinstance(b, float):
        try:
            return math.isclose(float(a), float(b), rel_tol=1e-9)
        except ValueError:
            return False
    return a == b


def _records(frame: Optional[pd.DataFrame]) -> Dict[str, dict]:
    if frame is None or frame.empty:
        return {}
    out = {}
    for r in frame.to_dict("records"):
        out.setdefault(row_key(r), r)
    return out


def _show(v) -> str:
    v = _norm(v)
    if v is None:
        return "blank"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def merge(
    computed: pd.DataFrame,
    visible: Optional[pd.DataFrame],
    last_computed: Optional[pd.DataFrame],
    sort: Callable[[pd.DataFrame], pd.DataFrame],
) -> tuple:
    """(table to show, changes on a first refresh, report).

    `computed` is today's build; `visible` the sheet as it is now; and
    `last_computed` the ``_Computed`` sheet the previous refresh left (None
    before the first).
    """
    report = WriteReport()
    new, old, base = _records(computed), _records(visible), _records(last_computed)
    first = last_computed is None
    gen_cols = list(computed.columns)
    extra_cols = [c for c in (visible.columns if visible is not None else [])
                  if c not in gen_cols and c != HAND_EDITS]
    changes = []
    out = []
    # A column the sheet lacks (new in this version) is a change for every row.
    report.cells_changed = sum(1 for c in gen_cols + [HAND_EDITS]
                               if visible is None or c not in visible.columns)
    for key, row in new.items():
        row = dict(row)
        seen = old.get(key)
        if seen is None:
            report.added.append(f"{row.get('WaferID')} {row.get('Process_Start') or ''}".strip())
        elif first:
            for col in gen_cols:
                if col in seen and col != HAND_EDITS and not same(seen[col], row[col]):
                    changes.append({"Row": key, "WaferID": row.get("WaferID"), "Column": col,
                                    "Before": _show(seen[col]), "After": _show(row[col])})
        else:
            prior = base.get(key, {})
            kept = []
            for col in gen_cols:
                if col == HAND_EDITS or col not in seen:
                    continue
                if key in base and col in prior and not same(seen[col], prior[col]):
                    note = col if same(seen[col], row[col]) else f"{col} (now {_show(row[col])})"
                    kept.append(note)
                    row[col] = seen[col]
            if kept:
                row[HAND_EDITS] = "kept: " + "; ".join(kept)
                report.hand_edits.append(f"{row.get('WaferID')}: {', '.join(kept)}")
        if seen is not None:
            for col in extra_cols:
                row[col] = seen.get(col)
            report.cells_changed += sum(
                1 for col in gen_cols + [HAND_EDITS]
                if col in seen and not same(seen[col], row.get(col)))
        out.append(row)
    for key, row in old.items():
        if key in new:
            continue
        if not first and key not in base:
            kept_row = dict(row)
            kept_row[HAND_EDITS] = "row added by hand"
            out.append(kept_row)
        else:
            report.removed.append(f"{row.get('WaferID')} ({key})")
    columns = gen_cols + ([HAND_EDITS] if HAND_EDITS not in gen_cols else []) + extra_cols
    table = sort(pd.DataFrame(out).reindex(columns=columns))
    report.rows = len(table)
    report.first_refresh_changes = len(changes)
    return table, pd.DataFrame(changes, columns=["Row", "WaferID", "Column", "Before", "After"]), report


# ------------------------------------------------------------------ write
def read_sheets(path: Path, main: str) -> tuple:
    """(visible main sheet, _Computed or None) of the workbook as saved."""
    if not path.exists():
        return None, None
    book = pd.ExcelFile(io.BytesIO(read_bytes_shared(path)))

    def parse(name):
        # Only an empty cell is blank: Milestone writes "NA" as text, and
        # pandas' default would read it as missing and write it back empty.
        return book.parse(name, keep_default_na=False, na_values=[""])
    visible = parse(main) if main in book.sheet_names else None
    computed = parse(COMPUTED_SHEET) if COMPUTED_SHEET in book.sheet_names else None
    return visible, computed


def _cell(v):
    v = None if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT else v
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        return v.item()
    return v


def _write_frame(ws, frame: pd.DataFrame) -> None:
    ws.append(list(frame.columns))
    for row in frame.itertuples(index=False):
        ws.append([_cell(v) for v in row])


def write_table(
    path: Path,
    main: str,
    table: pd.DataFrame,
    computed: pd.DataFrame,
    groups: Dict[str, str],
    other_sheets: Sequence[tuple],
) -> Optional[Path]:
    """Replace the main, ``_Computed`` and generated sheets; keep every other sheet.

    `other_sheets` is ``(name, frame)`` for the generated companions
    (Column_Design, Notes, Datalog_runs, Refresh_Changes); a sheet with the
    same name is replaced, any other sheet in the workbook is left as it is.
    Returns the backup of the old file, None when there was none.
    """
    if path.exists():
        wb = load_workbook(io.BytesIO(read_bytes_shared(path)))
    else:
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.title = main

    old_ws = wb[main] if main in wb.sheetnames else None
    header_style, widths = {}, {}
    if old_ws is not None:
        for cell in old_ws[1]:
            if cell.value is not None:
                header_style[cell.value] = (copy(cell.font), copy(cell.fill), copy(cell.alignment),
                                            copy(cell.border))
                letter = cell.column_letter
                if letter in old_ws.column_dimensions:
                    widths[cell.value] = old_ws.column_dimensions[letter].width
    index = wb.sheetnames.index(main) if old_ws is not None else 0
    if old_ws is not None:
        wb.remove(old_ws)
    ws = wb.create_sheet(main, index)
    _write_frame(ws, table)

    white = Font(bold=True, color="FFFFFFFF")
    for cell in ws[1]:
        style = header_style.get(cell.value)
        if style:
            cell.font, cell.fill, cell.alignment, cell.border = (copy(s) for s in style)
        else:
            fill = GROUP_FILL.get(groups.get(cell.value))
            if fill:
                cell.fill = PatternFill("solid", fgColor="FF" + fill)
                cell.font = white
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        width = widths.get(cell.value)
        ws.column_dimensions[cell.column_letter].width = width or min(
            max(len(str(cell.value)) + 2, 10), 28)
    ws.row_dimensions[1].height = 28.75
    grey = PatternFill("solid", fgColor="FF" + QUIET_ROW)
    if "Run_Type" in table:
        # One pass: ws[i] rescans the whole sheet for its width on every call.
        kinds = table["Run_Type"].fillna("").astype(str)
        for kind, cells in zip(kinds, ws.iter_rows(min_row=2, max_row=len(table) + 1)):
            if not kind.startswith("growth"):
                for cell in cells:
                    cell.fill = grey
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions

    for name, frame in list(other_sheets) + [(COMPUTED_SHEET, computed)]:
        if name in wb.sheetnames:
            wb.remove(wb[name])
        if frame is None:
            continue
        sheet = wb.create_sheet(name)
        _write_frame(sheet, frame)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        if name == COMPUTED_SHEET:
            sheet.sheet_state = "hidden"
    if "Column_Design" in wb.sheetnames:
        design = wb["Column_Design"]
        for cell in design[1]:
            cell.fill = PatternFill("solid", fgColor="FF111827")
            cell.font = white
        for row in design.iter_rows(min_row=2):
            fill = GROUP_FILL.get(row[2].value)
            if fill:
                row[2].fill = PatternFill("solid", fgColor="FF" + fill)
                row[2].font = Font(color="FFFFFFFF")
    wb.active = wb.sheetnames.index(main)

    tmp = path.with_name(path.stem + ".tmp.xlsx")
    wb.save(tmp)
    backup = None
    if path.exists():
        backup = backup_path(path)
        shutil.copy2(path, backup)
    os.replace(tmp, path)
    return backup
