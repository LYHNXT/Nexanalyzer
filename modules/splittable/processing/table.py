"""
Filtering a split table, and the few readings of it the charts share.

Pure pandas: no Streamlit, no files. Every function keeps the frame's index,
which is the row's position in the workbook -- the run order the scripts wrote
-- so a filtered frame can still be plotted against where each run sat.
"""

import re
from typing import Iterable, Optional

import pandas as pd

#: Run_Type values that are a growth run start with this word: "growth",
#: "growth (stopped)", VB's "growth (no datalog)". Everything else -- cleaning,
#: O2-annealing, conditioning, test, unidentified -- is a non-growth run.
GROWTH = "growth"

#: Shown for a row the series rule cannot place.
NO_SERIES = "—"

_SERIES_PREFIX = re.compile(r"^([A-Z]{2,})\d")


def is_growth(data: pd.DataFrame) -> pd.Series:
    """True for growth runs; a row with no Run_Type counts as one.

    The HA table predates Run_Type for its oldest rows, and every one of those
    is a growth -- the cleanings were inserted with the column.
    """
    if "Run_Type" not in data.columns:
        return pd.Series(True, index=data.index)
    kind = data["Run_Type"].astype("string").str.strip().str.lower()
    return kind.isna() | kind.str.startswith(GROWTH).fillna(False)


def non_growth_kind(run_type) -> str:
    """The marker family a non-growth run is drawn in: cleaning, anneal or other."""
    text = str(run_type or "").lower()
    if "clean" in text:
        return "cleaning"
    if "anneal" in text:
        return "anneal"
    return "other"


def series_of(data: pd.DataFrame) -> pd.Series:
    """Each row's series: the Series column where the table has one, else the
    WaferID's letter prefix (HADI51 -> HADI).

    VB carries Series because its names collide (VBCAxx-2608 against VBCAxx);
    HA does not, and its prefix is its series. A row neither rule places --
    CLEANING-4 has no digits after its letters -- gets `NO_SERIES`.
    """
    derived = data.get("WaferID", pd.Series(index=data.index, dtype=object)).map(_prefix)
    if "Series" in data.columns:
        declared = data["Series"].map(lambda v: None if pd.isna(v) else str(v).strip() or None)
        derived = declared.fillna(derived)
    return derived.fillna(NO_SERIES)


def _prefix(wafer) -> Optional[str]:
    if not isinstance(wafer, str):
        return None
    match = _SERIES_PREFIX.match(wafer.strip().upper())
    return match.group(1) if match else None


def distinct(values: pd.Series) -> list[str]:
    """The non-blank values of a column as text, sorted."""
    return sorted({str(v) for v in values.dropna() if str(v).strip()})


def filter_rows(
    data: pd.DataFrame,
    months: Optional[Iterable[str]] = None,
    run_types: Optional[Iterable[str]] = None,
    series: Optional[Iterable[str]] = None,
    query: str = "",
) -> pd.DataFrame:
    """The rows matching every filter given; None or empty means "any".

    `query` is a case-insensitive substring of WaferID, or several separated
    by commas or spaces, any of which may match.
    """
    keep = pd.Series(True, index=data.index)
    if months and "Month" in data.columns:
        keep &= data["Month"].astype("string").isin(list(months)).fillna(False)
    if run_types and "Run_Type" in data.columns:
        keep &= data["Run_Type"].astype("string").isin(list(run_types)).fillna(False)
    if series:
        keep &= series_of(data).isin(list(series))
    terms = [t for t in re.split(r"[,\s]+", (query or "").lower()) if t]
    if terms and "WaferID" in data.columns:
        wafer = data["WaferID"].astype("string").str.lower().fillna("")
        hit = pd.Series(False, index=data.index)
        for term in terms:
            hit |= wafer.str.contains(term, regex=False)
        keep &= hit
    return data[keep]


def numeric_columns(data: pd.DataFrame) -> list[str]:
    """Columns a chart can put on an axis: numeric, with at least one value."""
    return [
        c for c in data.columns
        if pd.api.types.is_numeric_dtype(data[c])
        and not pd.api.types.is_bool_dtype(data[c])
        and data[c].notna().any()
    ]


def std_column(columns: Iterable[str], column: str) -> Optional[str]:
    """The frame-to-frame std the table carries for `column`, if any.

    The scripts spell it three ways: ``OM_Reference_pct`` -> ``OM_Reference_Std``,
    ``E2g_A1g_Center_Mean`` -> ``E2g_A1g_Center_Std`` (HA), and
    ``E2g_A1g_FWHM`` -> ``E2g_A1g_FWHM_std`` (VB). A MAD is not a std and is
    not offered.
    """
    columns = set(columns)
    stem = re.sub(r"_(Mean|pct)$", "", column)
    for candidate in (f"{stem}_Std", f"{column}_Std", f"{column}_std", f"{stem}_std"):
        if candidate != column and candidate in columns:
            return candidate
    return None


def not_discriminable(data: pd.DataFrame, column: str) -> pd.Series:
    """True where an OM column's cut sat inside the frame noise.

    Only OM coverage columns have this caveat; ``OM_Discriminable`` is 0 or
    False on those rows. Anything else -- or a table without the column --
    is never flagged.
    """
    if not column.startswith("OM_") or "OM_Discriminable" not in data.columns:
        return pd.Series(False, index=data.index)
    flag = pd.to_numeric(data["OM_Discriminable"], errors="coerce")
    return flag.eq(0).fillna(False)
