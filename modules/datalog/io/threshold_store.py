"""
Per-folder PV/SV tolerances: the `thresholds.json` sidecar.

**The on-disk format is frozen**, for the same reason as `runcard_tags.json`:
this file lives inside the operator's data folder, not in the repo, and
datalog_monitor reads and writes the same one. It travels with the data, so a
folder carries its own tolerances to whatever machine opens it next.

    {
      "global_default_pct": 5.0,
      "overrides": {"Heater": 2.0, "MFC-1": 10.0},
      "timing_tolerance_pct": 5.0,
      "timing_floor_sec": 2.0
    }

The two ``timing_*`` keys belong to the Runcard check, not to anything here,
and that is precisely why `save_thresholds` merges rather than overwrites —
see its docstring for the bug that shape fixes.

The JSON primitives are imported from `tag_store` rather than repeated: both
files are read by another application, and the atomic write is the property
that must not drift between them.
"""

from pathlib import Path

from .tag_store import _read_json, _read_json_for_update, _write_json

THRESHOLDS_FILENAME = "thresholds.json"

DEFAULT_TOLERANCE_PCT = 5.0
"""The band a channel gets when nothing was ever set for it.

Five percent of setpoint, inherited from the datalog_monitor install this page
replaces. It is a starting point for a conversation with the process engineer,
not a measured specification: a mass-flow controller and a three-zone heater
have no business sharing a tolerance, which is what the per-channel overrides
are for.
"""

DEFAULT_TIMING_TOLERANCE_PCT = 5.0
"""Runcard timing tolerance. Stored here, read by the Runcard check."""

DEFAULT_TIMING_FLOOR_SEC = 2.0
"""Runcard timing floor, in seconds. Stored here, read by the Runcard check."""


def get_thresholds_path(root_folder: str) -> Path:
    """Absolute path to one data folder's thresholds.json."""
    return Path(root_folder) / THRESHOLDS_FILENAME


def load_thresholds(root_folder: str) -> dict:
    """
    The folder's tolerances, backfilled with defaults.

    Stored keys win wholesale — a **shallow** merge, so a stored ``overrides``
    replaces the default ``{}`` entirely rather than being merged key by key,
    which is what makes deleting an override on save actually delete it.

    The return always carries all four keys. That is what lets
    `analysis.get_tolerance_pct` index ``global_default_pct`` directly, and it
    is what quietly upgrades a two-key file written by an older build.
    """
    return _with_defaults(_read_json(get_thresholds_path(root_folder), {}))


def _with_defaults(stored: dict) -> dict:
    """`stored` backfilled with every default it lacks (a shallow merge)."""
    defaults = {
        "global_default_pct": DEFAULT_TOLERANCE_PCT,
        "overrides": {},
        "timing_tolerance_pct": DEFAULT_TIMING_TOLERANCE_PCT,
        "timing_floor_sec": DEFAULT_TIMING_FLOOR_SEC,
    }
    defaults.update(stored)
    return defaults


def save_thresholds(root_folder: str, thresholds: dict) -> None:
    """
    Persist `thresholds`, keeping the keys it doesn't mention.

    **This merges; the source app wrote the dict verbatim, and that was a
    bug.** Its Datalog view saved only ``global_default_pct`` and
    ``overrides``, which erased ``timing_tolerance_pct`` and
    ``timing_floor_sec`` from the file — so an operator who had tuned the
    Runcard check's timing tolerance lost it, silently, by adjusting a PV/SV
    band on a different page. `load_thresholds` backfills defaults, so the loss
    was invisible until someone noticed the timing check had gone back to
    5 % / 2 s.

    Merging costs the ability to *delete* a top-level key, which nothing wants,
    and keeps the ability to delete an override, which the tolerance editor
    depends on: ``overrides`` is replaced wholesale by whatever is passed.
    """
    # Read strictly: a locked or damaged file read as empty would be saved back
    # as defaults, erasing the overrides and timing keys this merge preserves.
    path = get_thresholds_path(root_folder)
    merged = _with_defaults(_read_json_for_update(path, {}))
    merged.update(thresholds)
    _write_json(path, merged)
