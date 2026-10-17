"""
Renaming runs to ``<timestamp>[~tag].csv``, and keeping their tags attached.

**The target format is frozen**: datalog_monitor writes the same names, the
Runcard check reads the tag back out of them, and `tag_store._tag_from_filename`
recovers a tag by splitting on the delimiter. A run named
``2026-08-06_173312~VBBE00.csv`` is self-describing — copy it out of the folder,
away from `runcard_tags.json`, and it still says which runcard it belongs to.
That is the whole reason for renaming at all.

Renames happen **in place**, via `Path.with_name`: same directory, same suffix,
only the stem changes. Nothing here moves a file between folders, and nothing
here writes a tag it was not already given.

Two operations, and the difference matters when something goes wrong.
`apply_rename` raises, so a single-file button can say why. `execute_sweep`
catches per file and keeps going, because one run locked open in Excel must not
abort a rename of four hundred others.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

from .scanner import RunMetadata, scan_folder
from .tag_store import get_runcard_tag, load_runcard_tags, move_runcard_tag

TIMESTAMP_FORMAT = "%Y-%m-%d_%H%M%S"
"""The filename stamp: hyphens and an underscore, unlike the slashes the
controller writes *inside* the CSV (`scanner.TIME_FORMAT`). Sorts
lexicographically into chronological order, which is the only reason a file
browser showing this folder is usable."""

TAG_DELIMITER = "~"
"""Separates the stamp from the tag. Duplicated as
`tag_store._FILENAME_TAG_DELIMITER` to break an import cycle; the two must stay
equal. Chosen because it is legal in a Windows filename and does not occur in
any runcard identifier seen so far — and `sanitize_tag_for_filename` removes it
from user input, so a generated name carries exactly one."""

#: Characters Windows refuses in a filename. A tag carrying one of these is a
#: tag someone typed, not one this app generated, and it must not be able to
#: turn a rename into a path.
_ILLEGAL_FILENAME_CHARS = set('\\/:*?"<>|')


def sanitize_tag_for_filename(tag: str) -> str:
    """
    Make a runcard tag safe to embed in a filename.

    Only the filename derivation is sanitized — the tag stored in
    `runcard_tags.json`, which is what search and the run list display, is kept
    exactly as the operator typed it. Mangling that would mean the tag shown on
    screen was not the tag they entered.

    The delimiter itself is replaced along with the illegal characters, which
    is what keeps the round trip unambiguous: `tag_store._tag_from_filename`
    splits on the last ``~``, and a sanitized tag can never contain one.
    Leading and trailing spaces and dots are stripped, because Windows silently
    drops them and the file would then not match the name this planned.
    """
    cleaned = "".join(
        "-" if ch in _ILLEGAL_FILENAME_CHARS or ch == TAG_DELIMITER else ch
        for ch in tag
    )
    return cleaned.strip(" .")


def target_filename(start_time, tag: str, suffix: str) -> str:
    """The name a run with this start time and tag should have.

    Keeps the original suffix rather than forcing ``.csv``: the planner is fed
    whatever `scanner.discover_csv_files` found, and a file that arrived as
    ``.CSV`` should not silently change extension as a side effect of being
    tagged. An empty tag gives ``<timestamp><suffix>`` with no trailing
    delimiter left behind.
    """
    stamp = start_time.strftime(TIMESTAMP_FORMAT)
    safe_tag = sanitize_tag_for_filename(tag) if tag else ""
    if safe_tag:
        return f"{stamp}{TAG_DELIMITER}{safe_tag}{suffix}"
    return f"{stamp}{suffix}"


@dataclass(frozen=True)
class RenamePlan:
    """What one file would be renamed to, decided before anything is touched.

    ``will_change`` is a pure path comparison, which makes it the UI's "is this
    file already correctly named?" test — the answer that turns the rename
    button into the caption saying there is nothing to do.

    ``collision`` is a point-in-time ``exists()`` check and is only meaningful
    when ``will_change``. It forecasts; it does not decide. A colliding plan is
    still handed to `execute_sweep`, which lets `apply_rename` raise and records
    it as skipped — because the seconds between previewing a sweep and
    confirming it are seconds in which the controller can write a new file.
    """

    path: str
    new_path: str
    will_change: bool
    collision: bool


class RenameCollision(Exception):
    """A file already exists at the planned name."""

    def __init__(self, target_path: str):
        self.target_path = target_path
        super().__init__(f'A file named "{Path(target_path).name}" already exists.')


class TagOrphaned(Exception):
    """The file was renamed, its tag could not follow, and it could not be put back.

    The one outcome `apply_rename`'s rollback cannot repair, and it is reported
    as itself rather than folded into "skipped" because the two need opposite
    things from the operator: a skipped file is untouched and can simply be
    swept again, while this one is sitting under a new name with its tag still
    filed under the old one, and sweeping again will not find it — the tag has
    to be re-entered, or the file renamed back by hand.

    Carries both names because the operator has to look for the file under the
    new one and the tag under the old one.
    """

    def __init__(self, path: str, new_path: str, cause: BaseException):
        self.path = path
        self.new_path = new_path
        self.cause = cause
        super().__init__(
            f'"{Path(path).name}" was renamed to "{Path(new_path).name}" but its tag '
            f"could not be moved and the file could not be renamed back: {cause}"
        )


def _plan_for(meta: RunMetadata, tag: str) -> RenamePlan:
    """Plan one rename from already-loaded metadata and an already-resolved tag."""
    src = Path(meta.path)
    dest = src.with_name(target_filename(meta.start_time, tag, src.suffix))
    will_change = dest != src
    return RenamePlan(
        path=meta.path,
        new_path=str(dest),
        will_change=will_change,
        collision=will_change and dest.exists(),
    )


def plan_single_rename(root_folder: str, meta: RunMetadata) -> RenamePlan:
    """Plan the rename for one run, resolving its tag from disk. Writes nothing."""
    return _plan_for(meta, get_runcard_tag(root_folder, meta.path))


def plan_renames(root_folder: str) -> List[RenamePlan]:
    """
    Plan a rename for every run in the folder tree. Writes nothing.

    Loads the tag map **once** for the whole sweep — the alternative re-reads
    `runcard_tags.json` per file, which on a folder of several hundred runs is
    several hundred reads of the same few kilobytes.

    Every target name is recomputed from current data, so this also fixes files
    that were renamed under a tag that has since been edited.

    **Plans over the de-duplicated run list**, the one the run table shows
    (`duplicates.without_duplicates`). A byte-identical stale copy the copier
    put back (``06173312.csv`` beside ``2026-08-06_173312~VBBE00.csv``) has no
    tag of its own, so planning it renamed it to ``2026-08-06_173312.csv``.
    Both files then carried *their* canonical name, `stale_copies` no longer
    recognised either as stale, the hidden copy reappeared as a second row,
    and Duplicate cleanup could never remove it. A stale copy is cleanup's to
    handle; the rename sweep leaves it exactly where it is.
    """
    # Imported here: `duplicates` imports `renamer.target_filename` at module
    # level, so a top-level import would be a cycle.
    from .duplicates import without_duplicates

    tags = load_runcard_tags(root_folder)
    return [
        _plan_for(meta, get_runcard_tag(root_folder, meta.path, tags))
        for meta in without_duplicates(root_folder, scan_folder(root_folder))
    ]


def apply_rename(root_folder: str, plan: RenamePlan) -> None:
    """
    Rename one file on disk and move its tag entry to the new key, or neither.

    The tag map is keyed by path, so the rename invalidates the old key and the
    two steps are one operation: a rename that succeeded without the re-key
    leaves a run that had a tag reading as untagged.

    **The rename is rolled back if the re-key fails**, so every exception out of
    here except `TagOrphaned` means nothing on disk moved. Without that, a
    read-only share or another process holding `runcard_tags.json` open — which
    on Windows makes `_write_json`'s atomic replace raise `PermissionError` —
    left the file renamed, the tag stranded under the old key, and `execute_sweep`
    reporting the file as "skipped", which was simply untrue.

    The tag is deliberately **not** written ahead of the rename instead. It
    would need the sidecar to carry the new key before the file exists, and
    `runcard_tags.json` is read by datalog_monitor while this app writes it: a
    key naming a file that is not there is a row that other application renders.
    A rollback keeps every intermediate state one the other reader can make
    sense of.

    Raises
    ------
    RenameCollision
        Something already occupies the target name. Checked again here rather
        than trusting `plan.collision`, which was computed when the plan was
        built. Nothing has been touched.
    OSError
        The file is locked, gone, or the folder is read-only — either on the
        rename itself, or on the sidecar write, in which case the rename has
        been undone.
    ValueError
        `plan` was built against a different root folder — the file is outside
        `root_folder`, so `tag_store._relative_key` cannot key it. The rename is
        undone before this surfaces, but `ui.datalog_state.reset_rename_results`
        still drops a pending plan the moment the folder changes: a plan that
        reaches here is already a bug, and one whose rollback fails renames a
        file the operator is no longer looking at.
    TagOrphaned
        The re-key failed *and* the rollback failed. The only outcome that
        leaves the pair half-applied.
    """
    src = Path(plan.path)
    dest = Path(plan.new_path)
    # Asked of the disk, not of `plan.collision`, which was true whenever the
    # preview was drawn and says nothing about now. `Path.rename` silently
    # replaces an existing destination on POSIX, so trusting the stale flag
    # there means a run that appeared between preview and confirm is
    # overwritten; on Windows it raises a bare OSError whose text names two
    # absolute paths instead of the filename that is taken.
    if dest != src and dest.exists():
        raise RenameCollision(plan.new_path)
    src.rename(dest)
    try:
        move_runcard_tag(root_folder, plan.path, plan.new_path)
    except BaseException as exc:
        # Deliberately broader than the OSError/ValueError this is known to
        # raise: the invariant being defended is "the file is where the tag
        # says it is", and an exception this does not recognise is exactly the
        # case where leaving the file moved would be hardest to notice.
        try:
            dest.rename(src)
        except OSError as rollback_exc:
            raise TagOrphaned(plan.path, plan.new_path, exc) from rollback_exc
        raise


@dataclass(frozen=True)
class SweepResult:
    """What a bulk rename did, in three buckets, because there are three outcomes.

    ``renamed`` holds ``(old, new)``; ``skipped`` holds ``(path, reason)`` for
    files nothing happened to; ``tag_orphaned`` holds ``(old, new, reason)`` for
    the file that was renamed but whose tag could not follow *and* could not be
    put back — see `TagOrphaned`.

    The reason is the exception's own text, which is what the page renders
    beside the filename — a sweep that says "4 skipped" without saying why is a
    sweep the operator has to run again to learn anything. That is also why
    ``tag_orphaned`` is not folded into ``skipped``: a skipped file is untouched
    and re-sweeping fixes it, while an orphaned one is on disk under its new
    name with its tag filed under the old one, and re-sweeping will not find it.
    Reporting it as "skipped" was a lie about the state of the folder.

    ``tag_orphaned`` defaults to empty so existing readers of the first two
    buckets keep working; a reader that ignores it under-reports rather than
    crashes.
    """

    renamed: List[Tuple[str, str]]
    skipped: List[Tuple[str, str]]
    tag_orphaned: List[Tuple[str, str, str]] = field(default_factory=list)


def execute_sweep(root_folder: str, plans: List[RenamePlan]) -> SweepResult:
    """
    Apply every plan, skipping individual failures rather than aborting on them.

    A folder being renamed in bulk reliably contains one file locked open in
    Excel and one whose target name was taken between preview and confirm.
    Aborting on the first would leave the folder half-renamed with no record of
    where it stopped; skipping leaves it fully renamed except for the files that
    said why not.

    `TagOrphaned` is caught into its own bucket rather than into ``skipped``,
    because `apply_rename`'s rollback means everything in ``skipped`` really is
    untouched and an orphan really is not. `ValueError` is still left to
    propagate and abort the sweep: it means the whole plan list was built
    against another folder, so every remaining plan would fail the same way and
    the page has an error banner for exactly that case.
    """
    renamed = []
    skipped = []
    tag_orphaned = []
    for plan in plans:
        try:
            apply_rename(root_folder, plan)
        except TagOrphaned as exc:
            tag_orphaned.append((exc.path, exc.new_path, str(exc)))
            continue
        except (RenameCollision, OSError) as exc:
            skipped.append((plan.path, str(exc)))
            continue
        renamed.append((plan.path, plan.new_path))
    return SweepResult(renamed=renamed, skipped=skipped, tag_orphaned=tag_orphaned)
