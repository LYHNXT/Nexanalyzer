"""
Reading what `processing.runcard_match` compares: the recipe folder and the runs.

**The one place `datalog` reads `runcard`'s files**, and it does so through
`modules.runcard.io.parser.parse_runcard` rather than a second parser: the two
packages were independent until runcard matching needed both, and a recipe
parsed two ways is a recipe that can mean two things. The dependency runs one
way only -- nothing in `modules.runcard` imports `datalog`.

No Streamlit here; `scanner` stays the only module in this package that
imports it. The page caches around these functions, keyed on
`runcard_signature` and on each run's mtime.
"""

import csv
from pathlib import Path
from typing import List, Optional, Tuple, Union

import pandas as pd

from modules.runcard.io.parser import parse_runcard

from ..processing.runcard_match import (
    NEEDED_COLUMNS,
    TIME_FORMAT,
    Runcard,
    RunSteps,
    is_common_name,
    is_copy_name,
    run_steps,
)

#: Where a tool keeps its recipes relative to its DATALOG folder, most specific
#: first. HA1P01's are under ``RUNCARD/lcy``; the operator's own sub-folders
#: there (``ymc/``) and the loose test cards beside ``lcy`` are not candidates.
_RUNCARD_FOLDER_GUESSES = (
    ("RUNCARD", "lcy"), ("RUNCARD",), ("Runcard",), ("Run Card",),
)

#: Commands that make a file a recipe. A datalog export dropped into the recipe
#: folder parses too -- as thousands of "commands" named after timestamps --
#: and none of them is one of these.
_RECIPE_COMMANDS = ("Wait", "MFC/PC", "Heater Ramp")


def default_runcard_folder(datalog_root: Union[str, Path]) -> Optional[str]:
    """The tool's recipe folder, guessed from its datalog folder; None if none exists."""
    tool = Path(datalog_root).parent
    for parts in _RUNCARD_FOLDER_GUESSES:
        candidate = tool.joinpath(*parts)
        if candidate.is_dir():
            return str(candidate)
    return None


def runcard_signature(folder: Union[str, Path]) -> Tuple[Tuple[str, float, int], ...]:
    """(relative path, mtime, size) of every CSV under `folder`: the cache key for `read_runcards`.

    A stat per file, which on a thousand-card folder is milliseconds; a recipe
    edited in place moves its mtime and so the key, which is the point.
    """
    root = Path(folder)
    if not root.is_dir():
        return ()
    out = []
    for path in sorted(root.rglob("*.csv")):
        try:
            st = path.stat()
        except OSError:
            continue
        out.append((path.relative_to(root).as_posix(), st.st_mtime, st.st_size))
    return tuple(out)


def read_runcards(folder: Union[str, Path]) -> List[Runcard]:
    """Every recipe under `folder`, as matching candidates.

    **Common** (reusable, any number of runs) means a file directly in
    `folder` -- CLEANING-1..5 and the other shared recipes live there -- or a
    cleaning-type name inside a month folder, which is how the older versions
    of a cleaning recipe survive. Everything else is a growth card, used once.

    Skipped: unreadable files, non-recipes (no Wait / MFC/PC / Heater Ramp), and
    Explorer copies (``HADH84 - 複製.csv``), which duplicate a real card under a
    name nobody ran.
    """
    root = Path(folder)
    if not root.is_dir():
        return []
    cards = []
    for path in sorted(root.rglob("*.csv")):
        name = path.stem
        if is_copy_name(name):
            continue
        try:
            commands = parse_runcard(path)
            mtime = path.stat().st_mtime
        except (OSError, UnicodeDecodeError, ValueError, csv.Error):
            continue
        rows = []
        for cmd in commands:
            if cmd.name == "End":
                break
            params = tuple(cmd.params) + ("--", "--")
            rows.append((cmd.name, params[0], params[1]))
        if not any(r[0] in _RECIPE_COMMANDS for r in rows):
            continue
        common = path.parent == root or is_common_name(name)
        cards.append(Runcard(name=name, path=str(path), mtime=mtime, commands=tuple(rows), common=common))
    return cards


def read_run_steps(path: Union[str, Path]) -> Optional[RunSteps]:
    """One datalog file reduced for matching; None if it holds no run.

    Reads only the 17 columns matching uses, with the C engine. The page's
    `scanner.load_run` parses all 39 with the tolerant python engine and keeps
    the frame in a cache; doing that for a hundred untagged runs just to throw
    most of each frame away is minutes and hundreds of megabytes.
    """
    try:
        df = pd.read_csv(path, usecols=lambda c: c in NEEDED_COLUMNS, engine="c",
                         on_bad_lines="skip", encoding="utf-8", encoding_errors="replace",
                         low_memory=False)
    except (OSError, ValueError, pd.errors.ParserError):
        return None
    if "Time" not in df.columns or "Program" not in df.columns:
        return None
    df["Time"] = pd.to_datetime(df["Time"], format=TIME_FORMAT, errors="coerce")
    for col in df.columns:
        if col not in ("Time", "Program", "651C Gauge"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return run_steps(df)
