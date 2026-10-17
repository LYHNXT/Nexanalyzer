"""
Finding and reading the tool controller's datalog CSVs.

One CSV per run, written live by the controller at 1 Hz: 39 columns of
timestamp, recipe step, and fourteen PV/SV pairs. Two entry points, and the
split between them is the whole design:

- `scan_folder` lists a folder. It reads each file as **text**, keeping the
  first line, the last line and a count, and parses exactly two timestamps per
  file. A folder of several hundred runs lists instantly.
- `load_run` parses one run properly, which for the ground-truth file means the
  python CSV engine building 39 typed columns over 6903 rows, a datetime parse
  and 36 numeric coercions. It happens lazily, only for the run the operator
  actually opened.

Listing a folder by fully parsing every CSV is the version of this that does
not survive contact with a real ``HA_DataRecord`` tree — tens to hundreds of
milliseconds and several megabytes of frame per file, for four scalars per row
of the list.

**This is the one module under `modules/datalog/io/` that imports Streamlit**,
and the reason is the caching, not convenience. The page's run table lives in
a fragment that re-runs every 60 seconds so a run written while the operator
is watching appears on its own; without the per-file cache below, each of
those ticks would re-read every CSV in the tree. The layering rule this bends
is real, so it is bent exactly here: nothing else in this package imports
Streamlit, and `processing/analysis.py` and `viz/charts.py` stay importable
headless.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
import streamlit as st

from ..processing.analysis import TIME_COLUMN

PROGRAM_COLUMN = "Program"
"""The recipe step the controller was executing. Read by the cheap scan only —
the list view shows the first row's value, which says what a run was doing when
it started logging (``Stage Rot``, ``Pumping Forward``, ``Wait``)."""

TIME_FORMAT = "%Y/%m/%d %H:%M:%S"
"""How the controller writes timestamps *inside* the CSV — forward slashes.

Not the format of the filename stamp, which is ``2026-08-06_173312``. The two
conventions coexist in this data and are converted by different code:
`renamer.TIMESTAMP_FORMAT` owns the filename one.
"""

#: Columns that are text, and would come back as NaN from `pd.to_numeric`.
#: ``651C Gauge`` is the subtle member: it holds ``"1000 Torr"`` / ``"100 Torr"``,
#: which is the gauge's *range label*, not a pressure reading, and it changes
#: mid-run when the controller auto-switches range (2556 rows at 1000 Torr and
#: 4347 at 100 Torr in the ground-truth run). Coercing it would throw away the
#: only record of which range the pressure traces beside it were read on.
CATEGORICAL_COLUMNS = {"Auto Action", "Program", "651C Gauge"}

#: The pressure-control channels, in the order the operator wants to read them.
#: A list, not a set: the page builds the top of its channel list from this, so
#: the order is what lands on screen.
PRESSURE_GROUP_NUMERIC = ["Tube Pressure", "651C Pre", "651C Ang"]

PRESSURE_GROUP_LABEL_COLUMN = "651C Gauge"
"""The range label that gives the channels above their units.

Never plotted -- it is text. The page surfaces its distinct values as a caption
whenever a pressure channel is on screen, because a pressure trace read on the
wrong gauge range is off by 10x and nothing in the trace itself says so.
"""

ALIGNMENT_SV_COLUMN = "Heater SV"
"""The commanded growth temperature: comparison mode aligns runs on when this
setpoint settles, not on when the run started. See
`processing.analysis.find_final_plateau_start`, which does the finding and
records the data quirk that shapes it."""


@dataclass(frozen=True)
class RunMetadata:
    """The four scalars the run list shows, plus the path they came from.

    Frozen, so it is hashable and picklable — `st.cache_data` has to be able to
    store it. ``path`` is a plain `str` rather than a `Path` for the same
    reason: it is fed straight back into cache-keyed functions, which need a
    stable, hashable key.
    """

    path: str
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    row_count: int
    first_program: str


def _file_signature(path: str) -> float:
    """The file's mtime, which is the whole cache key beyond the path.

    Datalogs are *live*: the controller appends to the current run's CSV while
    the app is open, so a cache keyed on path alone serves a parse that is
    missing the last however-many minutes of the run being watched.

    No size, no hash — a bare float. It raises `OSError` if the file vanishes
    between the directory walk and this call, which on a folder the tool is
    actively writing is a real race; `scan_folder` catches it there.
    """
    return Path(path).stat().st_mtime


@st.cache_data(show_spinner=False)
def load_run_dataframe(path: str, mtime: float) -> pd.DataFrame:
    """
    Parse one run CSV. Internal — callers want `load_run`.

    `mtime` is a cache key and is never read. **It deliberately has no leading
    underscore.** Streamlit excludes underscore-prefixed parameters from the
    cache key — that is the documented escape hatch for unhashable arguments
    like database connections — so the source app's ``_mtime`` spelling meant
    this function was keyed on `path` alone and kept serving the first parse of
    a file the controller was still writing to. Renaming it is the fix; putting
    the underscore back re-breaks it silently.

    Three decisions in four lines:

    1. ``on_bad_lines="skip"`` with ``engine="python"``, together. A datalog can
       be truncated mid-line by a crash or by being copied mid-write, and can
       carry rows with the wrong field count. Skipping costs one sample;
       raising costs the whole run. The python engine is the tolerant parser,
       and it is slower — which is exactly why the list view does not come
       through here.
    2. The timestamp format is pinned and ``errors="coerce"``. No inference, so
       no locale or dayfirst ambiguity and no per-row fallback parsing; an
       unparseable stamp becomes NaT instead of raising.
    3. Those NaT rows are dropped immediately, so **every row that leaves here
       has a valid Time**. The index is *not* reset afterwards: it keeps gaps
       but stays monotonically increasing, which is the property the
       label-based slicing in `analysis._build_segment` depends on.

    Numeric coercion is a pure exclusion rule — everything that is not
    categorical and not the timestamp — so a column added to a future datalog
    is treated as numeric without this file being edited.

    Decoded as ``utf-8-sig`` with ``encoding_errors="replace"``, so no byte can
    stop a run opening. Before v5.23.0 this was the only strict-UTF-8 reader of
    a datalog: `_scan_metadata_from_file` and `runcard_index.read_run_steps`
    already replaced bad bytes, so a file with one stray byte was *listed* and
    then raised `UnicodeDecodeError` when opened, and a BOM turned the first
    header into ``"﻿Time"`` and ``df["Time"]`` into a KeyError.
    """
    df = pd.read_csv(path, on_bad_lines="skip", engine="python",
                     encoding="utf-8-sig", encoding_errors="replace")
    df[TIME_COLUMN] = pd.to_datetime(df[TIME_COLUMN], format=TIME_FORMAT, errors="coerce")
    df = df.dropna(subset=[TIME_COLUMN])
    numeric_cols = [c for c in df.columns if c not in CATEGORICAL_COLUMNS and c != TIME_COLUMN]
    for col in numeric_cols:
        # Engineering notation ("19.45E-3", "50.30E+0") is already a valid float
        # literal, so this reads it natively. Do not "fix" it with a regex:
        # float("19.45E-3") == 0.01945.
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_run(path: str) -> pd.DataFrame:
    """Parse the run at `path`, reusing the cached parse while the file is unchanged."""
    return load_run_dataframe(path, _file_signature(path))


def _parse_time_field(fields: List[str], time_idx: int) -> Optional[pd.Timestamp]:
    """One timestamp out of an already-split row, or None if it isn't one.

    Returns None for a short row as well as an unparseable one, because the
    last line of a live datalog is regularly half-written -- and for an
    **empty** field, which `pd.to_datetime` turns into NaT without raising.
    NaT is truthy, so before v5.23.0 it slipped past both the caller's
    ``is None`` test and its ``or start_time`` fallback, and the page then
    crashed on ``NaT.total_seconds()``.
    """
    if time_idx >= len(fields):
        return None
    try:
        parsed = pd.to_datetime(fields[time_idx], format=TIME_FORMAT)
    except (ValueError, TypeError):
        return None
    return None if pd.isna(parsed) else parsed


def _scan_metadata_from_file(path: str) -> Optional[RunMetadata]:
    """
    Read one run's list-view metadata without parsing it.

    One sequential pass, one line of peak memory, two timestamps parsed instead
    of one per row. Column positions are resolved **by name**, so header order
    is not assumed; a file missing either name is rejected rather than guessed
    at.

    ``encoding="utf-8"`` with ``errors="replace"``, not ``utf-8-sig``: no decode
    can raise, and bad bytes become U+FFFD. The BOM asymmetry against
    `tag_store._read_json` is deliberate — those sidecars get opened in Notepad
    by operators, datalogs are written by the controller and never hand-edited.
    A BOM here would corrupt the first column name, ``columns.index("Time")``
    would raise, and the run would silently vanish from the list.

    Splitting on ``","`` rather than using `csv.reader` is safe only because
    this format has no quoted fields and no embedded commas — ``"1000 Torr"``
    contains a space, which is the character that makes that claim checkable.

    Returns
    -------
    Optional[RunMetadata]
        None for an empty file, a header without ``Time``/``Program``, a file
        with no data rows, or a first row whose timestamp will not parse.
        `scan_folder` drops those silently: a folder can hold a CSV that is not
        a datalog, and a run list is not the place to argue about it.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        header_line = f.readline()
        if not header_line:
            return None
        columns = header_line.rstrip("\r\n").split(",")
        try:
            time_idx = columns.index(TIME_COLUMN)
            program_idx = columns.index(PROGRAM_COLUMN)
        except ValueError:
            return None

        first_line = None
        last_line = None
        row_count = 0
        for raw_line in f:
            line = raw_line.rstrip("\r\n")
            if not line:
                continue  # blank lines are not rows and are not counted
            row_count += 1
            if first_line is None:
                first_line = line
            last_line = line

    if first_line is None:
        return None

    first_fields = first_line.split(",")
    start_time = _parse_time_field(first_fields, time_idx)
    if start_time is None:
        return None
    # Falls back to start_time when the final line is half-written, which is the
    # normal state of the run currently being logged.
    # `is None`, not `or`: a Timestamp is always truthy, and the helper never
    # returns NaT, so the test says what it means.
    end_time = _parse_time_field(last_line.split(","), time_idx)
    if end_time is None:
        end_time = start_time
    first_program = first_fields[program_idx] if program_idx < len(first_fields) else ""

    return RunMetadata(
        path=path,
        start_time=start_time,
        end_time=end_time,
        row_count=row_count,
        first_program=first_program,
    )


@st.cache_data(show_spinner=False)
def _cached_scan_metadata(path: str, mtime: float) -> Optional[RunMetadata]:
    """`mtime` is a cache key and is never read; see `load_run_dataframe`."""
    return _scan_metadata_from_file(path)


def get_run_metadata(path: str) -> Optional[RunMetadata]:
    """List-view metadata for one run, cached until the file changes."""
    return _cached_scan_metadata(path, _file_signature(path))


def discover_csv_files(root_folder: str) -> List[str]:
    """
    Every ``*.csv`` under `root_folder`, recursively, sorted as strings.

    A missing or non-directory root returns ``[]`` rather than raising: the page
    remembers the last folder across restarts, and that folder is regularly an
    unplugged drive or a share that has not reconnected yet. The list view says
    so; it does not crash.

    Case sensitivity follows the platform, so ``.CSV`` matches on Windows. A
    file whose extension is malformed — the real ``HADH01csv``, a dot lost in a
    copy — is not matched and never appears.
    """
    root = Path(root_folder)
    if not root.is_dir():
        return []
    return sorted(str(p) for p in root.rglob("*.csv"))


def scan_folder(root_folder: str) -> List[RunMetadata]:
    """
    List every run under `root_folder`, newest first.

    Two orderings, and they are different on purpose: `discover_csv_files`
    walks in lexicographic path order, which is deterministic; this returns
    display order, sorted by ``start_time`` descending. `sorted` is stable, so
    two runs that started in the same second keep path order.

    Nothing caches the walk itself — that is what lets a run written a moment
    ago appear on the next 60-second tick — while each file's metadata is
    cached against its mtime one level down, so a tick over an unchanged folder
    costs a `stat` per file.

    A file that vanishes between the walk and its `stat` is skipped rather than
    raising. The controller writes into this tree while the page is watching
    it, and a run that was archived mid-tick must cost one row, not the page.
    """
    runs = []
    for path in discover_csv_files(root_folder):
        try:
            meta = get_run_metadata(path)
        except OSError:
            continue
        if meta is not None:
            runs.append(meta)
    return sorted(runs, key=lambda r: r.start_time, reverse=True)


def detect_pv_sv_pairs(columns: List[str]) -> List[Tuple[str, str, str]]:
    """
    Every ``<name> PV`` column that has a ``<name> SV`` twin.

    The rule is suffix-anchored on the literal three characters ``" PV"``,
    case-sensitive, and the partner is the exact string ``f"{prefix} SV"``.
    Nothing is normalised, folded or fuzzy-matched, and that is what keeps
    ``P1`` and ``P1_H`` independent pairs instead of one swallowing the other —
    the property to preserve if this is ever rewritten as a regex. A PV with no
    SV, or an SV with no PV, is not a pair and falls through to
    `get_plain_numeric_channels`.

    Returned in **header order**, not alphabetical, so the pairs stack on the
    chart in the order the controller logs them. On the ground-truth file that
    is 14 pairs: ``P1_H``, ``P2_H``, ``P3_H``, ``Heater``, ``MFC-1``..``MFC-8``,
    ``P1``, ``P2``, ``P3``.

    ``pair_name`` — the prefix — is also the key a tolerance override is saved
    under and the ``Channel`` value in the violations table, so renaming a
    column in the controller's log orphans that override.
    """
    pairs = []
    col_set = set(columns)
    for col in columns:
        if col.endswith(" PV"):
            prefix = col[: -len(" PV")]
            sv_col = f"{prefix} SV"
            if sv_col in col_set:
                pairs.append((prefix, col, sv_col))
    return pairs


def get_plain_numeric_channels(columns: List[str],
                               pv_sv_pairs: List[Tuple[str, str, str]]) -> List[str]:
    """
    The numeric channels no PV/SV pair claimed, in header order.

    Takes the pairs rather than recomputing them so the caller decides what
    counts as consumed: handing this a filtered subset of pairs pushes the rest
    back into the plain list, which is a degree of freedom, not an accident.

    On the ground-truth file this is exactly ``Tube Pressure``, ``651C Pre``,
    ``651C Ang``, ``Stage Rot``, ``Stage Pos`` — the first three being
    `PRESSURE_GROUP_NUMERIC`.
    """
    paired_cols = {pv for _, pv, _ in pv_sv_pairs} | {sv for _, _, sv in pv_sv_pairs}
    return [
        c for c in columns
        if c not in CATEGORICAL_COLUMNS and c != TIME_COLUMN and c not in paired_cols
    ]
