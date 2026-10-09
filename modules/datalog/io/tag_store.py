"""
Which runcard each datalog run belongs to: the `runcard_tags.json` sidecar.

**The on-disk format is frozen.** This file does not live in the repo — it sits
*inside the operator's data folder*, beside the runs it describes, and
datalog_monitor (a separate application, still installed on the tool PC) reads
and writes the same file. Change the JSON shape, the relative-POSIX keying or
the empty-tag-means-delete rule and the two applications stop agreeing about
which run is which. Keys are forward-slash paths relative to the data folder,
so the folder can be copied to another machine, or to a share with a different
drive letter, without orphaning a single tag:

    {
      "2026-08-06_173312~VBBE00.csv": "VBBE00",
      "subfolder/2026-08-07_084535~VBBE02.csv": "VBBE02"
    }

The tag has a second home: the filename itself, as ``<timestamp>~<tag>.csv``.
`get_runcard_tag` prefers the stored value and falls back to parsing the name,
which is what lets a folder of already-named runs read as tagged without a
tagging step and without this file being written at all.

This module also holds the JSON primitives `threshold_store` uses. They are
imported there, not copied: the atomic temp-file-and-replace write is the
property that stops another application ever reading half a sidecar, and two
copies of it are two chances for that property to drift.
"""

import json
import time
from pathlib import Path
from typing import Optional

RUNCARD_TAGS_FILENAME = "runcard_tags.json"

_FILENAME_TAG_DELIMITER = "~"
"""A deliberate literal duplicate of `renamer.TAG_DELIMITER`.

`renamer` imports this module to keep tags in step with the filenames it
writes, so importing the constant back would cycle. The two must stay equal;
`renamer` also sanitizes this character out of user-typed tags, which is what
makes the ``rsplit`` below unambiguous.
"""


def _read_json(path: Path, default):
    """Read a sidecar, degrading to `default` rather than raising.

    ``utf-8-sig``, because operators open these files in Notepad and Notepad
    writes a BOM. A missing file, malformed JSON or an OS error all return the
    default — losing a remembered tag must never be the reason a page refuses
    to open — and nothing here repairs the file, so a human can still look at
    what went wrong.

    One gap, stated rather than papered over: `UnicodeDecodeError` is a
    `ValueError`, so a sidecar that is genuinely not UTF-8 propagates instead
    of degrading. That has never been seen in this data, and a silent default
    would hide a real corruption.
    """
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return default
    # A sidecar holding a list or a string is as unusable as a malformed one,
    # and would otherwise surface as an AttributeError far from here.
    return data if isinstance(data, type(default)) else default


class SidecarUnreadable(OSError):
    """A sidecar exists but could not be read, so it must not be overwritten.

    An `OSError` so the pages' existing ``except OSError`` around every save
    reports it the way they report a read-only share, and `renamer` rolls a
    rename back on it like any other failed re-key.
    """


_UPDATE_READ_ATTEMPTS = 3
_UPDATE_READ_RETRY_S = 0.2


def _read_json_for_update(path: Path, default):
    """Read a sidecar that is about to be rewritten -- strictly.

    Every save here is read, change one entry, write the whole file. If the
    read degraded to `default` the way `_read_json` does, a file that was
    merely locked (robocopy or datalog_monitor holding it) or caught half-copied
    would be "read" as empty, and the write would then replace every stored
    tag with the one being saved. Losing a tag must not stop a page opening;
    it is equally true that opening a page must not lose every tag.

    So: a missing file is still `default` (nothing to lose); a locked or
    unparseable one is retried briefly, since both are usually a copy in
    flight; and if it still cannot be read, or holds something other than a
    JSON object, this raises `SidecarUnreadable` and nothing is written.
    """
    if not path.exists():
        return default
    for attempt in range(_UPDATE_READ_ATTEMPTS):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            break
        except (json.JSONDecodeError, OSError) as exc:
            if attempt == _UPDATE_READ_ATTEMPTS - 1:
                raise SidecarUnreadable(
                    f"{path.name} could not be read ({exc}), so nothing was saved -- "
                    "saving would have replaced every entry in it. If another "
                    "program has it open, try again; if it is damaged, repair or "
                    "remove it by hand."
                ) from exc
            time.sleep(_UPDATE_READ_RETRY_S)
    if not isinstance(data, type(default)):
        raise SidecarUnreadable(
            f"{path.name} holds a JSON {type(data).__name__}, not an object, so "
            "nothing was saved. Repair or remove it by hand."
        )
    return data


def _write_json(path: Path, data) -> None:
    """Write a sidecar atomically.

    Temp file in the same directory, then `Path.replace`, which is an atomic
    rename on both Windows and POSIX: a crash mid-write leaves the previous
    good file, never a truncated one. That matters more here than for a normal
    preference file, because another application reads these while this one
    writes them.

    ``sort_keys=True`` and ``indent=2`` so the file is deterministic and
    diff-friendly, and written as UTF-8 **without** a BOM.

    Writes are deliberately not wrapped in try/except, unlike reads: a data
    folder that is read-only is something the operator needs told about, not
    something to swallow.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp_path.replace(path)


def _relative_key(root_folder: str, file_path: str) -> str:
    """
    The key `file_path` is stored under: its path relative to the data folder,
    forward-slashed.

    Purely lexical — no `resolve()`, no filesystem access, no symlink
    handling — which is fast and is also the sharp edge:

    Raises
    ------
    ValueError
        If `file_path` is not under `root_folder`. Lexically not under: a
        *relative* file path against an absolute root raises even when it
        obviously belongs, and on POSIX a case difference raises where
        `PureWindowsPath` semantics tolerate it.

    Nothing catches that, and the contract it enforces is worth keeping: paths
    handed to this module come from `scanner.discover_csv_files(root_folder)`,
    which by construction yields absolute paths under the root in the same
    spelling. The callers most likely to break it are the ones that hold a path
    across a folder change — which is why `ui.state.reset_rename_results` drops
    a pending rename plan the moment the folder moves. Anything that starts
    accepting typed paths, ``..`` segments, UNC aliases of the same share or
    8.3 short names has to add a `resolve()` first.
    """
    return Path(file_path).relative_to(Path(root_folder)).as_posix()


def _tag_from_filename(file_path: str) -> str:
    """The tag encoded in a ``<timestamp>~tag.csv`` name, or ``""``.

    ``rsplit`` on the **last** delimiter, so ``a~b~VBBE00.csv`` reads as
    ``VBBE00``; `renamer` never writes more than one, but a hand-named file can.
    """
    stem = Path(file_path).stem
    if _FILENAME_TAG_DELIMITER not in stem:
        return ""
    return stem.rsplit(_FILENAME_TAG_DELIMITER, 1)[1]


def load_runcard_tags(root_folder: str) -> dict:
    """The whole ``{relative-posix-path: tag}`` map for one data folder."""
    return _read_json(Path(root_folder) / RUNCARD_TAGS_FILENAME, {})


def get_runcard_tag(root_folder: str, file_path: str, tags: Optional[dict] = None) -> str:
    """
    The tag for one run: an explicitly-set tag wins, else the filename's.

    Never writes. A file named ``2026-08-06_173312~VBBE00.csv`` reads as tagged
    ``VBBE00`` without a tagging step, and nothing lands in `runcard_tags.json`
    until the tag is actually edited.

    Parameters
    ----------
    tags : Optional[dict]
        An already-loaded map. Tested with ``is not None`` rather than for
        truthiness, so a loaded-but-empty ``{}`` is respected instead of
        triggering a re-read — the run list passes one map for several hundred
        rows, which is the difference between one file read and several hundred.
    """
    tags = tags if tags is not None else load_runcard_tags(root_folder)
    stored = tags.get(_relative_key(root_folder, file_path))
    return stored if stored else _tag_from_filename(file_path)


def set_runcard_tag(root_folder: str, file_path: str, tag: str) -> None:
    """
    Store `tag` for one run, or clear it.

    An empty tag **deletes the entry** rather than storing ``""`` — that is how
    "clear this tag" is expressed on disk, and it leaves the filename fallback
    free to answer again. `pop` with a default, so clearing an absent tag is a
    no-op rather than an error.
    """
    tags = _read_json_for_update(Path(root_folder) / RUNCARD_TAGS_FILENAME, {})
    key = _relative_key(root_folder, file_path)
    if tag:
        tags[key] = tag
    else:
        tags.pop(key, None)
    _write_json(Path(root_folder) / RUNCARD_TAGS_FILENAME, tags)


def clear_runcard_tag(root_folder: str, file_path: str) -> None:
    """
    Delete a run's tag entry, writing only if there was one.

    `set_runcard_tag` with an empty tag does the same thing but rewrites the
    sidecar either way, which in a folder that has never been tagged *creates*
    `runcard_tags.json` as a side effect of removing a file. `move_runcard_tag`
    already declines to write when the old key is absent, for the same reason:
    nothing in this module should leave a sidecar behind to record that it had
    nothing to record.
    """
    tags = _read_json_for_update(Path(root_folder) / RUNCARD_TAGS_FILENAME, {})
    key = _relative_key(root_folder, file_path)
    if key not in tags:
        return
    del tags[key]
    _write_json(Path(root_folder) / RUNCARD_TAGS_FILENAME, tags)


def set_runcard_tags(root_folder: str, tags_by_path: dict) -> None:
    """
    Store several tags in one read and one write: `set_runcard_tag` for a batch.

    The runcard detector saves a tag per untagged run -- a hundred at once on a
    folder that has never been tagged -- and one write per run would rewrite
    the shared sidecar a hundred times while datalog_monitor may be reading it.
    Same rules as the single call: keys relative to the root, an empty tag
    deletes the entry.
    """
    tags = _read_json_for_update(Path(root_folder) / RUNCARD_TAGS_FILENAME, {})
    for file_path, tag in tags_by_path.items():
        key = _relative_key(root_folder, file_path)
        if tag:
            tags[key] = tag
        else:
            tags.pop(key, None)
    _write_json(Path(root_folder) / RUNCARD_TAGS_FILENAME, tags)


def move_runcard_tag(root_folder: str, old_path: str, new_path: str) -> None:
    """
    Re-key a run's tag after its file has been renamed on disk.

    Returns without writing when the old key is absent, and that is correct
    rather than a gap: a tag that was only ever *derived from the filename* has
    no entry to move, and the new filename still encodes it, so
    `get_runcard_tag` keeps returning the same answer. Writing an entry here
    would turn an implicit tag into an explicit one as a side effect of a
    rename.
    """
    tags = _read_json_for_update(Path(root_folder) / RUNCARD_TAGS_FILENAME, {})
    old_key = _relative_key(root_folder, old_path)
    if old_key not in tags:
        return
    new_key = _relative_key(root_folder, new_path)
    tags[new_key] = tags.pop(old_key)
    _write_json(Path(root_folder) / RUNCARD_TAGS_FILENAME, tags)
