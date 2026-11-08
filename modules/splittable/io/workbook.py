"""
Read a tool's split-table workbook: the run sheet, and the column design that
groups it.

The workbook is written by the scripts in each tool's ``SPLITTABLE`` folder
(``add_splittable_rows.py`` and friends on HA1P01) or, on VB1P01, by the
page's Refresh now (`refresh`); this module only reads it. The two tools' workbooks share a shape but not
a schema -- HA has 90 columns and VB 98, and the QC columns are spelled
differently (``E2g_A1g_FWHM_Mean`` against ``E2g_A1g_FWHM``) -- so nothing here
names a column beyond the few both carry: ``WaferID``, ``Month``,
``Run_Type`` and ``Process_Start``.

**Read as bytes first.** openpyxl opens the path as a seekable zip, and on a
OneDrive file that seek fails with ``OSError: [Errno 22] Invalid argument``
(seen on VB_SplitTable_from_runcards.xlsx while HA's beside it opened fine).
One ``read_bytes()`` is a plain sequential read, which OneDrive serves. It
shares the file for deletion too (`core.io.shared_read`), so a workbook open
in Excel still reads -- as of its last save.
"""

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import pandas as pd

from core.io.shared_read import read_bytes_shared

#: The sheet the column design lives on, and the group a column it does not
#: list falls into.
DESIGN_SHEET = "Column_Design"
OTHER_GROUP = "other"


@dataclass(frozen=True)
class SplitTable:
    """One workbook's run sheet and the group each of its columns belongs to.

    `groups` is ordered as the design sheet lists them, which is the order the
    scripts' author chose to read the table in.
    """

    data: pd.DataFrame
    groups: dict[str, list[str]]
    sheet: str


def read_split_table(path: Union[str, Path]) -> SplitTable:
    """The workbook's first sheet as a frame, with its columns grouped.

    Raises what reading raises -- a missing file, one no process will share --
    because the page has to say which of those it was. A workbook merely open
    in Excel is not one of them.
    """
    raw = read_bytes_shared(path)
    book = pd.ExcelFile(io.BytesIO(raw))
    sheet = book.sheet_names[0]
    data = _tidy(book.parse(sheet))
    design = book.parse(DESIGN_SHEET) if DESIGN_SHEET in book.sheet_names else None
    return SplitTable(data=data, groups=column_groups(data.columns, design), sheet=sheet)


def column_groups(columns, design) -> dict[str, list[str]]:
    """Every column under its design-sheet group; unlisted ones under "other".

    The design sheet can lag the table -- a column added by a script before
    anyone documented it -- and such a column must still be reachable.
    """
    by_column: dict[str, str] = {}
    if design is not None and {"Column", "Group"} <= set(design.columns):
        for name, group in zip(design["Column"], design["Group"]):
            if isinstance(name, str) and isinstance(group, str):
                by_column.setdefault(name.strip(), group.strip())

    groups: dict[str, list[str]] = {}
    for group in dict.fromkeys(by_column.values()):
        groups[group] = []
    for column in columns:
        groups.setdefault(by_column.get(column, OTHER_GROUP), []).append(column)
    return {group: cols for group, cols in groups.items() if cols}


def _tidy(data: pd.DataFrame) -> pd.DataFrame:
    """Make the sheet safe to display and filter, without changing a value.

    - ``Month`` reads as 202610 on HA and 202610.0 on VB; both become "202610".
    - A column mixing types (VB's ``Dist_cm`` holds 1, 1.5 and "1-2") cannot go
      to Arrow, so ``st.dataframe`` refuses it; such a column becomes text,
      blanks kept blank.
    """
    data = data.dropna(how="all").reset_index(drop=True)
    if "Month" in data.columns:
        data["Month"] = data["Month"].map(_month_text)
    for column in data.columns:
        if data[column].dtype == object:
            kinds = {type(v) for v in data[column].dropna()}
            if len(kinds) > 1:
                data[column] = data[column].map(lambda v: v if pd.isna(v) else str(v))
    return data


def _month_text(value) -> object:
    if pd.isna(value):
        return value
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
