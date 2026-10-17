"""
Runs stored twice, because something outside this app put the old name back.

The operator's analysis folder is fed by a copier that matches on **filename**
and only ever adds — it never deletes and it does not follow renames. Rename a
run here and its source file looks absent on the copier's next pass, so the
original ``DDHHMMSS.csv`` name is copied back in and the folder holds the same
run twice: once under the recorder's name, once under `renamer.target_filename`'s.

Measured cadence on HA1P01 is about three minutes, and the copier runs on the
tool PC, not on the machine showing this page. That single fact decides the
shape of this module. Deleting a stale copy is a race this side cannot win —
the file is back before the operator has looked away — so removal is the
*secondary* answer here and hiding is the primary one:

- `without_duplicates` is what the run list renders. A run that exists twice on
  disk is shown once, under its renamed form, however often the old name comes
  back. Nothing is touched, so there is no race to lose.
- `find_duplicates` and `remove_duplicates` reclaim the space when the operator
  asks, by way of the Recycle Bin.

Both read the same `stale_copies` rule, which is the property worth keeping:
what the list hides is exactly what cleanup would remove, so the preview the
operator confirms cannot disagree with the table they were just looking at.

**Nothing in this app creates these.** `renamer.apply_rename` renames and rolls
back; it never copies. Debugging starts at the copier, not here.

Two guards stop a removal ever costing data:

- A file is only ever a candidate when some *other* file in the same directory
  already carries the canonical name for that run. No canonically-named file in
  the group means the group is left alone — that is not a rename artifact, and
  there is no basis for picking a survivor.
- Candidacy needs identical **bytes**, not merely the same run. A truncated or
  half-copied near-duplicate is reported as nothing at all: it stays on disk and
  stays *visible* in the list, which is where a human will notice it.
"""

import ctypes
import hashlib
import sys
from collections import defaultdict
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import streamlit as st

from .renamer import target_filename
from .scanner import RunMetadata
from .tag_store import SidecarUnreadable, clear_runcard_tag, get_runcard_tag, load_runcard_tags

_HASH_CHUNK_BYTES = 1 << 20
"""Read size for hashing. A run CSV runs to a couple of megabytes, so this is
one or two reads per file rather than a per-line loop."""


@dataclass(frozen=True)
class DuplicatePlan:
    """One stale copy and the run it duplicates, decided before anything is touched.

    ``path`` is the file that would be removed; ``keeper`` is the
    canonically-named file whose bytes it repeats. Both are carried because the
    preview names them side by side — "remove X, it is a copy of Y" is the only
    form in which an operator can sanity-check a deletion they did not plan.
    """

    path: str
    keeper: str


@st.cache_data(show_spinner=False)
def _digest(path: str, mtime: float, size: int) -> str:
    """
    SHA-256 of one file. Internal — callers want `digest_of`.

    `mtime` and `size` are cache keys and are never read. **Neither has a
    leading underscore**, for the reason `scanner.load_run_dataframe` spells
    out: Streamlit excludes underscore-prefixed parameters from the cache key,
    so an ``_mtime`` here would key the digest on the path alone and happily
    serve the hash of a file's previous contents. On a folder the controller is
    actively writing, that is a stale digest deciding whether a run gets hidden.

    Caching at all is what makes hiding affordable: the run list re-derives this
    on every 60-second tick, and an uncached hash would re-read the same
    megabytes once a minute for as long as the page is open.
    """
    return _sha256_of_file(path)


def _sha256_of_file(path: str) -> str:
    """SHA-256 of one file, read from disk now -- never from a cache."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest_of(path: str) -> str:
    """SHA-256 of one file, cached until its mtime or size moves.

    Raises `OSError` if the file has vanished — the same race
    `scanner.scan_folder` catches, and callers here catch it the same way.
    """
    stat = Path(path).stat()
    return _digest(path, stat.st_mtime, stat.st_size)


def _canonical_name(root_folder: str, meta: RunMetadata, tags: dict) -> str:
    """The name this run *should* have, by the same rule `renamer` renames to."""
    tag = get_runcard_tag(root_folder, meta.path, tags)
    return target_filename(meta.start_time, tag, Path(meta.path).suffix)


def _run_key(meta: RunMetadata) -> Tuple:
    """
    A cheap "these are probably the same run", used to decide what to hash.

    Grouping on metadata `scan_folder` has already read is what keeps the whole
    feature affordable: hashing every CSV in a folder of fourteen hundred runs
    to find five duplicates would read gigabytes to answer a question three
    `stat`-backed fields already narrow to a handful of files. Bytes are only
    ever read for files that agree on start, end *and* row count.

    The parent directory is part of the key deliberately. The same run appearing
    under two month folders is an archiving decision, and this module must not
    quietly resolve one by deleting somebody's second copy.
    """
    return (str(Path(meta.path).parent), meta.start_time, meta.end_time, meta.row_count)


def stale_copies(root_folder: str, runs: List[RunMetadata]) -> Dict[str, str]:
    """
    Map each stale copy's path to the path of the run it duplicates.

    Takes an already-scanned run list rather than re-walking the folder, so the
    run list and the cleanup sweep judge one set of files by one rule in one
    pass — a file hidden from the table is exactly a file cleanup would remove.

    A file that vanishes between the scan and its hash is skipped, not raised:
    the copier and the controller are both writing into this tree while the page
    reads it, and a run archived mid-tick must cost one row, not the page.
    """
    tags = load_runcard_tags(root_folder)

    groups: Dict[Tuple, List[RunMetadata]] = defaultdict(list)
    for meta in runs:
        groups[_run_key(meta)].append(meta)

    stale: Dict[str, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        keepers = [
            m for m in members
            if Path(m.path).name == _canonical_name(root_folder, m, tags)
        ]
        # Exactly one canonical name, or there is no basis for choosing a
        # survivor. Zero means the group is not a rename artifact at all; more
        # than one cannot happen inside a single directory, where names are
        # unique, and if it ever does it is not this module's call to break.
        if len(keepers) != 1:
            continue
        keeper = keepers[0]
        try:
            keeper_digest = digest_of(keeper.path)
            for meta in members:
                if meta.path != keeper.path and digest_of(meta.path) == keeper_digest:
                    stale[meta.path] = keeper.path
        except OSError:
            continue
    return stale


def without_duplicates(root_folder: str, runs: List[RunMetadata]) -> List[RunMetadata]:
    """
    `runs` minus every stale copy — what the run list should actually show.

    The copier re-creates these every few minutes, so removing the files is a
    race this app cannot win; not *showing* a run twice is a promise it can
    always keep. Cleanup reclaims the disk space when asked, but the list is
    correct whether or not anyone ever asks.
    """
    stale = stale_copies(root_folder, runs)
    return [m for m in runs if m.path not in stale]


def find_duplicates(root_folder: str, runs: List[RunMetadata]) -> List[DuplicatePlan]:
    """Every stale copy, as a plan, sorted by path so the preview is stable."""
    stale = stale_copies(root_folder, runs)
    return sorted(
        (DuplicatePlan(path=path, keeper=keeper) for path, keeper in stale.items()),
        key=lambda plan: plan.path,
    )


# ---- Recycle Bin removal ----------------------------------------------------

#: `SHFileOperationW` flags. `_FOF_ALLOWUNDO` is the one that matters: it is the
#: difference between the Recycle Bin and an unrecoverable delete.
_FO_DELETE = 0x0003
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040
_FOF_NOERRORUI = 0x0400


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def send_to_recycle_bin(path: str) -> None:
    """
    Remove one file, recoverably.

    The shell API rather than `Path.unlink`, and rather than a ``send2trash``
    dependency, for two reasons. These CSVs are the only copy of a measurement
    until the moment they are removed, and this module decides on its own which
    ones are redundant — so the operator has to be able to disagree afterwards.
    And a new third-party dependency would have to reach the tool PC's install,
    where `ctypes` reaches the same API with nothing to ship.

    Off Windows this falls back to `Path.unlink`, which is *not* recoverable;
    the app runs on Windows, and a silently-different guarantee is worth stating
    rather than pretending the fallback is equivalent.

    Raises
    ------
    OSError
        The path is not a file, the shell refused the operation, or the
        operation was aborted partway.
    """
    target = Path(path)
    if not target.is_file():
        raise OSError(f"Not a file: {path}")

    if sys.platform != "win32":
        target.unlink()
        return

    op = _SHFILEOPSTRUCTW(
        hwnd=None,
        wFunc=_FO_DELETE,
        # `pFrom` is a double-NUL-terminated *list*, not a plain string. ctypes
        # contributes only the final NUL, so the list separator is written here;
        # without it the shell reads past the end of the buffer.
        pFrom=str(target.resolve()) + "\0",
        pTo=None,
        fFlags=_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI,
        fAnyOperationsAborted=False,
        hNameMappings=None,
        lpszProgressTitle=None,
    )
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        raise OSError(
            f'"{target.name}" could not be moved to the Recycle Bin '
            f"(SHFileOperation returned {result})."
        )
    if op.fAnyOperationsAborted:
        raise OSError(f'Moving "{target.name}" to the Recycle Bin was aborted.')


class CopyNoLongerIdentical(OSError):
    """The planned copy no longer matches its keeper byte for byte; nothing was removed.

    An `OSError`, so `remove_duplicates` reports it as a skip with this text
    beside the filename, the way it reports a file locked open in Excel.
    """

    def __init__(self, plan: DuplicatePlan, why: str):
        self.plan = plan
        super().__init__(
            f'"{Path(plan.path).name}" was left in place: {why} since the preview, '
            f'so it is no longer known to be a copy of "{Path(plan.keeper).name}".'
        )


def verify_still_identical(plan: DuplicatePlan) -> None:
    """
    Re-check, from disk and uncached, that `plan.path` still repeats `plan.keeper`.

    The plan was decided when the preview was drawn, and the copier and the
    controller both write into this folder in the seconds before the operator
    confirms. Size first, because it is a `stat`; then SHA-256 of both files,
    read now rather than through `digest_of`'s cache, which is keyed on mtime
    and size and so cannot see a same-size rewrite within one mtime tick.

    A missing *copy* is left for `send_to_recycle_bin` to refuse, so the skip
    reason stays its "Not a file". A missing *keeper* is a refusal here: the
    copy may now be the only one.

    Raises
    ------
    CopyNoLongerIdentical
        The keeper is gone, or either file's size or content changed. Nothing
        is touched.
    OSError
        Either file could not be read.
    """
    if not Path(plan.path).is_file():
        return
    if not Path(plan.keeper).is_file():
        raise CopyNoLongerIdentical(plan, "its keeper has been removed")
    if Path(plan.path).stat().st_size != Path(plan.keeper).stat().st_size:
        raise CopyNoLongerIdentical(plan, "it or its keeper changed size")
    if _sha256_of_file(plan.path) != _sha256_of_file(plan.keeper):
        raise CopyNoLongerIdentical(plan, "it or its keeper changed content")


def remove_duplicate(root_folder: str, plan: DuplicatePlan) -> None:
    """
    Recycle one stale copy, then drop the tag entry it may still hold.

    **Re-verified immediately before removal** (`verify_still_identical`):
    the two files are compared again, size and SHA-256, and a pair that no
    longer matches raises `CopyNoLongerIdentical` with nothing removed. Before
    v5.23.0 the deletion trusted the preview's verdict, however old it was.

    Order matters, and it is the opposite of `renamer.apply_rename`'s. There the
    file must end up where the tag says it is, so the rename rolls back if the
    re-key fails. Here the file is *going away*: a sidecar entry cleared before
    a removal that then failed would leave a run on disk that reads as untagged,
    which is a worse state than the reverse. Clearing after means the only way
    this can half-apply is a stale key naming a file that no longer exists —
    which `get_runcard_tag` already ignores, and which the next sweep drops.
    """
    verify_still_identical(plan)
    send_to_recycle_bin(plan.path)
    # `clear_runcard_tag`, not `set_runcard_tag(..., "")`: the two agree on the
    # result, but only the first declines to write when there was no entry, and
    # a folder that has never been tagged must not gain a `runcard_tags.json`
    # as a side effect of a file being removed from it.
    try:
        clear_runcard_tag(root_folder, plan.path)
    except SidecarUnreadable:
        # The file is already in the Recycle Bin, so reporting this plan as
        # skipped would be false. What is left is the harmless stale key the
        # docstring describes; the sidecar was not touched.
        pass


@dataclass(frozen=True)
class CleanupResult:
    """What a cleanup did: ``removed`` holds the plans that applied, ``skipped``
    holds ``(path, reason)`` for the ones that did not.

    The reason is the exception's own text, which is what the page renders
    beside the filename — matching `renamer.SweepResult`, and for the same
    reason: a cleanup reporting "2 skipped" without saying why is one the
    operator has to run again to learn anything.
    """

    removed: List[DuplicatePlan]
    skipped: List[Tuple[str, str]]


def remove_duplicates(root_folder: str, plans: List[DuplicatePlan]) -> CleanupResult:
    """
    Apply every plan, skipping individual failures rather than aborting on them.

    Same bet as `renamer.execute_sweep`: one run locked open in Excel must not
    stop the other four being cleaned up. `ValueError` is left to propagate, as
    it is there — it means the plans were built against a different root folder,
    so every remaining one would fail identically.
    """
    removed = []
    skipped = []
    for plan in plans:
        try:
            remove_duplicate(root_folder, plan)
        except OSError as exc:
            skipped.append((plan.path, str(exc)))
            continue
        removed.append(plan)
    return CleanupResult(removed=removed, skipped=skipped)
