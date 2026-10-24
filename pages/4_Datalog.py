"""
Datalog: a folder of tool-controller run logs in, one run's channels or several
runs side by side out.

Point it at the folder the controller writes into and it lists every run under
it, newest first, with the runcard each one belongs to. Pick a run and it
draws:

  - one stacked panel per selected channel, PV solid and its setpoint dashed,
    with every out-of-tolerance sample marked on the PV trace itself
  - a summary table of min / max / mean per channel
  - a violations table: which channel, when, for how long, how far out

Pick several runs and it overlays them instead, aligned on the moment each one
settled at its final commanded heater setpoint rather than on wall-clock start.

**The run list is cheap and the parse is not.** Listing a run reads its first
line, its last line and a line count — four scalars. Parsing one properly means
39 typed columns over ~6900 rows through the tolerant CSV engine, and that
happens only for the runs actually opened. A folder of several hundred runs
lists instantly; see `modules/datalog/io/scanner.py`.

**The run table lives inside a fragment that re-runs every 60 seconds**, which
is what makes a run written while the operator is watching appear on its own.
That boundary is load-bearing in both directions — `_render_workspace`'s own
docstring says what breaks if it is tightened or widened.

**Renaming is real.** The "Bulk rename" section renames files on disk to
``<timestamp>~<tag>.csv`` and re-keys their tags in the same step, so a run
copied out of the folder still says which runcard it belongs to.

**Runcard detection names files.** "Detect runcards" replays every recipe in
the tool's runcard folder against each untagged run and proposes the card that
produced it (`processing.runcard_match`); applying renames the files to
``<timestamp>~<tag>.csv``. The filename, not the sidecar, is the durable
carrier on HA1P01: its DATALOG is filled by SulfurSync (a one-way robocopy from
the tool PC), which also copies the tool PC's own `runcard_tags.json` over this
one whenever that changes. SulfurSync's ``keepRenamed`` (on for HA-DataRecord
since 2026-09-28) keeps renamed files renamed instead of copying the original
name back beside them. "Save as tags only" remains for folders nothing copies into.

**Two sidecars here are not ours to change.** `runcard_tags.json` and
`thresholds.json` live in the operator's data folder and are read by
datalog_monitor, the application this page replaces, which is still installed
on the tool PC. Their format is frozen. The only per-installation preference
this page owns is the remembered folder, in `data/datalog.json`.
"""

import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from core.io.export import create_filename, export_figure_png, prompt_save_path
from core.io.folder_picker import prompt_folder_path
from core.io.tool_layout import DATALOG
from core.ui.tool_select import render_tool_select
from core.viz.render import render_plot
from modules.datalog.io import duplicates, renamer
from modules.datalog.io.config_store import load_last_folder, save_last_folder
from modules.datalog.io.scanner import (
    ALIGNMENT_SV_COLUMN,
    get_run_metadata,
    PRESSURE_GROUP_LABEL_COLUMN,
    PRESSURE_GROUP_NUMERIC,
    detect_pv_sv_pairs,
    get_plain_numeric_channels,
    load_run,
    scan_folder,
)
from modules.datalog.io.runcard_index import (
    default_runcard_folder,
    read_run_steps,
    read_runcards,
    runcard_signature,
)
from modules.datalog.io.tag_store import (
    get_runcard_tag,
    load_runcard_tags,
    set_runcard_tag,
    set_runcard_tags,
)
from modules.datalog.io.threshold_store import load_thresholds, save_tolerances
from modules.datalog.processing import runcard_match
from modules.datalog.processing.analysis import (
    TIME_COLUMN,
    compute_all_violations,
    compute_summary_stats,
    find_final_plateau_start,
)
from modules.datalog.ui.datalog_state import get_datalog_state, reset_results
from modules.datalog.viz.charts import (
    build_comparison_figure,
    build_single_run_figure,
    plot_item_label,
)

#: How many of the newest runs the list shows when nothing is being searched
#: for. A folder that has been logging for a year holds thousands of runs and
#: the operator wants the last few; None means all of them.
RECENT_WINDOW_OPTIONS = {
    "Last 50 runs": 50,
    "Last 100 runs": 100,
    "Last 200 runs": 200,
    "All runs": None,
}

#: What a run opens with: tube pressure, the pressure controller's own reading,
#: and the heater pair. Two plain channels and one *pair name* -- "Heater"
#: brings both Heater PV and Heater SV. Channels this file doesn't carry are
#: skipped silently, so a datalog from a differently-configured tool just
#: starts with fewer panels rather than erroring.
DEFAULT_CHANNEL_LABELS = {"Tube Pressure", "651C Pre", "Heater"}

#: The saved file set, in the order it is meant to be read. The numeric prefix
#: is load-bearing: alphabetical order in a file browser puts the chart after
#: the tables it illustrates.
_CHART_ARTIFACT = (1, "chart")
_TABLE_ARTIFACTS = ((2, "summary"), (3, "violations"))

#: Ceiling on the exported chart's pixel height. A twelve-panel comparison of
#: eight runs asks for a figure several thousand pixels tall, and kaleido will
#: happily produce a 30 MB PNG nobody can open.
_MAX_EXPORT_HEIGHT_PX = 4000


def _format_duration(delta) -> str:
    """``HH:MM:SS``, zero-padded, hours uncapped.

    Not `str(Timedelta)`, which renders "0 days 01:59:07" and puts the only
    part anybody reads behind a constant.
    """
    total = int(delta.total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _build_plot_item_catalog(columns):
    """Everything this run can plot, in the order it should be offered.

    Three tiers, deliberately: the pressure-control group first in
    `PRESSURE_GROUP_NUMERIC`'s own order, then every PV/SV pair in the
    controller's column order, then the remaining plain channels. The order is
    both the multiselect's option order and the figure's panel stacking order,
    so the channels an operator reaches for first are also the panels at the
    top of the chart.

    The ``label`` comes from `charts.plot_item_label` rather than being written
    out again here: it is what the multiselect matches on *and* what titles the
    panel, and two spellings of it is a channel that can be selected and never
    drawn.
    """
    pv_sv_pairs = detect_pv_sv_pairs(columns)
    plain_channels = get_plain_numeric_channels(columns, pv_sv_pairs)

    catalog = []
    for channel in PRESSURE_GROUP_NUMERIC:
        if channel in plain_channels:
            catalog.append({"kind": "plain", "channel": channel})
    for name, pv, sv in pv_sv_pairs:
        catalog.append({"kind": "pvsv", "pair_name": name, "pv_col": pv, "sv_col": sv})
    for channel in plain_channels:
        if channel not in PRESSURE_GROUP_NUMERIC:
            catalog.append({"kind": "plain", "channel": channel})
    for item in catalog:
        item["label"] = plot_item_label(item)
    return catalog


def _render_bulk_rename(root_folder: str, state: dict) -> None:
    """Preview / confirm / apply a rename of every run in the tree.

    Lives in the main page body, **outside** the auto-refreshing fragment: a
    Streamlit fragment may write into only one container, and this plus the
    run table plus the charts cannot all be one scope without either giving up
    the 60-second refresh or paying a full-page rerun per row click.

    Two state keys carry the whole flow. ``rename_plans`` is a pending preview
    (already filtered to the files that would actually change) or None;
    ``rename_result`` is the last sweep's outcome, and it deliberately survives
    reruns so the "renamed 47 files" banner is still there after the list
    refreshes underneath it.
    """
    st.caption(
        "Rename every run in this folder (recursively) to `<timestamp>~<tag>.csv`. "
        "Target names are recomputed from current data, so this also fixes files "
        "renamed under a tag that has since been edited. In a folder a sync writes into, "
        "the sync must keep renamed files (SulfurSync: keepRenamed, on for HA-DataRecord) "
        "or it copies the original name back beside every renamed file."
    )
    pending = state.get("rename_plans")
    with st.expander("Bulk rename", expanded=bool(pending or state.get("rename_result"))):
        if st.button("Preview rename all", width="stretch"):
            state["rename_plans"] = [p for p in renamer.plan_renames(root_folder) if p.will_change]
            # A new preview clears the previous sweep's banner, which would
            # otherwise sit under a plan it did not produce.
            state["rename_result"] = None

        plans = state.get("rename_plans")
        if plans is not None:
            if not plans:
                st.info("Every filename already matches its tag.")
                state["rename_plans"] = None  # one-shot message
            else:
                st.write(f"{len(plans)} file(s) will change:")
                st.dataframe(
                    pd.DataFrame([
                        {
                            "Current name": Path(p.path).name,
                            "New name": Path(p.new_path).name,
                            # Forecast only. The plan is still handed to the
                            # sweep, which lets apply_rename raise and records
                            # it -- the target can be taken in the seconds
                            # between previewing and confirming.
                            "Status": "Collision - will be skipped" if p.collision else "OK",
                        }
                        for p in plans
                    ]),
                    hide_index=True, width="stretch",
                )
                col_confirm, col_cancel = st.columns(2)
                if col_confirm.button("Confirm rename all", width="stretch"):
                    try:
                        state["rename_result"] = renamer.execute_sweep(root_folder, plans)
                    except ValueError as e:
                        # tag_store._relative_key: a plan built against another
                        # folder. `reset_results` on a folder change is what
                        # stops this; the plan is kept on screen rather than
                        # cleared, because an error banner that vanishes on the
                        # next rerun is an error nobody read.
                        st.error(f"These plans do not belong to this folder: {e}")
                    else:
                        state["rename_plans"] = None
                        st.rerun()
                if col_cancel.button("Cancel", width="stretch"):
                    state["rename_plans"] = None
                    st.rerun()

        result = state.get("rename_result")
        if result is not None:
            st.success(f"Renamed {len(result.renamed)} file(s).")
            if result.skipped:
                st.warning(
                    "Skipped:\n"
                    + "\n".join(f"- {Path(p).name}: {reason}" for p, reason in result.skipped)
                )


def _render_duplicate_cleanup(root_folder: str, state: dict) -> None:
    """Preview / confirm / Recycle-Bin every run stored twice in the tree.

    The same preview-then-confirm shape as `_render_bulk_rename`, and the same
    two state keys, because it is the same conversation in a different verb.
    The difference worth knowing is what a mistake costs: a wrong rename is
    renamed back, so that flow can afford to be brisk, while this one takes
    files away and shows every name it would take before touching one.

    Collapsed by default when there is nothing pending, because the run list
    above already hides these -- an operator who never opens this expander is
    not looking at a wrong table, only at a folder using more disk than it
    needs.
    """
    st.caption(
        "Find runs stored twice — once under the recorder's original filename, "
        "once under the renamed one — and move the original to the Recycle Bin. "
        "The run list already hides these; this reclaims the disk space."
    )
    pending = state.get("duplicate_plans")
    with st.expander("Duplicate cleanup", expanded=bool(pending or state.get("duplicate_result"))):
        if st.button("Scan for duplicates", width="stretch"):
            state["duplicate_plans"] = duplicates.find_duplicates(
                root_folder, scan_folder(root_folder)
            )
            state["duplicate_result"] = None

        plans = state.get("duplicate_plans")
        if plans is not None:
            if not plans:
                st.info("No run in this folder is stored twice.")
                state["duplicate_plans"] = None  # one-shot message
            else:
                st.write(f"{len(plans)} file(s) will go to the Recycle Bin:")
                st.dataframe(
                    pd.DataFrame([
                        {
                            "Remove": Path(p.path).name,
                            "Identical to": Path(p.keeper).name,
                        }
                        for p in plans
                    ]),
                    hide_index=True, width="stretch",
                )
                col_confirm, col_cancel = st.columns(2)
                if col_confirm.button("Confirm cleanup", width="stretch"):
                    try:
                        state["duplicate_result"] = duplicates.remove_duplicates(root_folder, plans)
                    except ValueError as e:
                        # tag_store._relative_key, as in the rename sweep: plans
                        # built against another folder. Kept on screen rather
                        # than cleared, so the error survives the next rerun.
                        st.error(f"These plans do not belong to this folder: {e}")
                    else:
                        state["duplicate_plans"] = None
                        st.rerun()
                if col_cancel.button("Cancel cleanup", width="stretch"):
                    state["duplicate_plans"] = None
                    st.rerun()

        result = state.get("duplicate_result")
        if result is not None:
            st.success(f"Moved {len(result.removed)} file(s) to the Recycle Bin.")
            if result.skipped:
                st.warning(
                    "Skipped:\n"
                    + "\n".join(f"- {Path(p).name}: {reason}" for p, reason in result.skipped)
                )


# ------------------------------------------------------------------ Runcard detection
#: How many tagged cleaning runs are loaded as references at most. Each is a
#: file read on first use, and the matcher only looks within 30 days anyway.
_MAX_CLEANING_REFERENCES = 60

#: A run file modified this recently may still be being written by the
#: controller (and copied by the sync); renaming it now would leave the copy
#: that arrives next under the old name.
_LIVE_WINDOW_S = 30 * 60

#: A file longer than this is one of the continuous daily logs the tool wrote
#: before April 2025, holding several runs; one tag cannot name all of them.
_MAX_SINGLE_RUN_SPAN = pd.Timedelta(hours=12)

#: How far back "Runs from" starts by default, from the newest run: the backlog
#: an operator is catching up on, not two years of history in one table.
_DEFAULT_DETECT_SPAN = pd.Timedelta(days=60)

#: Confidence levels the detector pre-ticks for saving. "low" is shown and left
#: for the operator to tick: it means the log differs from every card in
#: several steps, which is a run worth a human look before it is named.
_PRESELECTED_CONFIDENCE = ("high", "medium")


@st.cache_data(show_spinner=False, ttl=60)
def _runcard_signature(folder: str):
    """The recipe folder's (path, mtime, size) list, re-walked at most once a minute.

    The per-run suggestion renders inside the 60-second fragment; walking a
    thousand cards on every tick would buy nothing, and a card saved a minute
    ago is picked up on the next one.
    """
    return runcard_signature(folder)


@st.cache_data(show_spinner=False, max_entries=4)
def _runcards(folder: str, signature):
    """Every candidate recipe, parsed once per change to the folder.

    `signature` is the cache key and is never read -- no leading underscore,
    for the reason `scanner.load_run_dataframe` gives.
    """
    return read_runcards(folder)


@st.cache_data(show_spinner=False, max_entries=4096)
def _run_steps(path: str, mtime: float):
    """One run reduced for matching: a few kB, so thousands of them fit."""
    return read_run_steps(path)


def _steps_for(path: str):
    try:
        return _run_steps(path, os.path.getmtime(path))
    except OSError:
        return None


def _runcard_folder(root_folder: str, state: dict) -> str:
    """The recipe folder to match against: the operator's pick, else the guess."""
    if state.get("runcard_folder") is None:
        state["runcard_folder"] = default_runcard_folder(root_folder) or ""
    return state["runcard_folder"]


def _usable(steps) -> bool:
    """Enough recipe to compare: two blocks and two minutes of waits."""
    return steps is not None and len(steps.blocks) >= 2 and steps.total >= 120


def _tag_context(root_folder: str, runs, tags: dict, window=None, exclude=None):
    """Tags already on runs, and the tagged cleaning runs that can stand as references.

    Returns ``(used, reference_paths)``: ``used`` maps each tag to its run's
    start -- those growth cards are taken, and they anchor the order check --
    and ``reference_paths`` holds ``(tag, path)`` for numbered-cleaning runs
    inside `window` (a ``(start, end)`` pair), newest first, capped. Nothing is
    read here; `_load_references` reads them, and only when a cleaning needs them.
    """
    used, refs = {}, []
    for meta in runs:
        if meta.path == exclude:
            continue
        tag = get_runcard_tag(root_folder, meta.path, tags)
        if not tag:
            continue
        used.setdefault(tag, meta.start_time)
        if runcard_match.cleaning_name(tag) and (
            window is None or window[0] <= meta.start_time <= window[1]
        ):
            refs.append((meta.start_time, tag, meta.path))
    refs.sort(reverse=True)
    return used, tuple((tag, path) for _t, tag, path in refs[:_MAX_CLEANING_REFERENCES])


def _load_references(reference_paths):
    """``(tag, steps)`` for each reference run that holds enough recipe to compare."""
    out = []
    for tag, path in reference_paths:
        steps = _steps_for(path)
        if _usable(steps):
            out.append((tag, steps))
    return out


def _detect_runcards(root_folder: str, runcard_folder: str, progress, since=None) -> dict:
    """Suggest a runcard for every untagged run started on or after `since`. Returns the page's result dict.

    Starts from the list the run table shows -- `duplicates.without_duplicates`
    has already dropped the byte-identical stale copies -- and then also skips a
    run with any *tagged twin*: another file with the same start time, such as
    a copy the sync put back that differs by a trailing row. It is the same
    run, so it is listed as such rather than named twice.
    """
    runs = duplicates.without_duplicates(root_folder, scan_folder(root_folder))
    tags = load_runcard_tags(root_folder)
    tagged_start = {}
    for meta in runs:
        if get_runcard_tag(root_folder, meta.path, tags):
            tagged_start.setdefault(meta.start_time, meta.path)
    untagged = [m for m in runs if not get_runcard_tag(root_folder, m.path, tags)]
    twins = [(m, tagged_start[m.start_time]) for m in untagged if m.start_time in tagged_start]
    todo, seen, multi = [], set(), []
    for meta in sorted(untagged, key=lambda m: m.start_time):
        if since is not None and meta.start_time < since:
            continue
        if meta.start_time in tagged_start or meta.start_time in seen:
            continue
        seen.add(meta.start_time)
        if meta.end_time - meta.start_time > _MAX_SINGLE_RUN_SPAN:
            multi.append(meta)
            continue
        todo.append(meta)
    twins = [(m, other) for m, other in twins if since is None or m.start_time >= since]

    pairs, unreadable = [], []
    for k, meta in enumerate(todo):
        progress(k / max(len(todo), 1) * 0.5, f"Reading {Path(meta.path).name}")
        steps = _steps_for(meta.path)
        if _usable(steps):
            pairs.append((meta.path, steps))
        else:
            unreadable.append(meta)

    cards = _runcards(runcard_folder, _runcard_signature(runcard_folder))
    suggestions = {}
    if pairs and cards:
        span = (min(s.start for _p, s in pairs) - pd.Timedelta(days=30),
                max(s.start for _p, s in pairs) + pd.Timedelta(days=30))
        used, reference_paths = _tag_context(root_folder, runs, tags, window=span)
        suggestions = runcard_match.suggest_runcards(
            pairs, cards, used=used, references=_load_references(reference_paths),
            progress=lambda done, total: progress(0.5 + 0.5 * done / max(total, 1), "Matching runcards"),
        )
    rows = []
    for meta in todo:
        s = suggestions.get(meta.path)
        if s is None:
            continue
        rows.append({
            "path": meta.path,
            "Save": s.confidence in _PRESELECTED_CONFIDENCE,
            "Start": meta.start_time.strftime("%Y-%m-%d %H:%M"),
            "File": Path(meta.path).name,
            "Tag": s.tag,
            "Confidence": s.confidence,
            "Differences": "; ".join(s.differences) or "none",
            "Notes": "; ".join(list(s.notes) + (["also fits: " + ", ".join(s.alternatives)] if s.alternatives else [])),
        })
    return {
        "rows": rows,
        "nonce": pd.Timestamp.now().value,
        "cards": len(cards),
        "twins": [(m.path, other) for m, other in twins],
        "unreadable": [m.path for m in unreadable],
        "multi": [m.path for m in multi],
    }


def _render_runcard_detection(root_folder: str, state: dict) -> None:
    """Find the runcard behind each untagged run and apply the names the operator accepts.

    Lives beside the bulk rename, outside the fragment, for the reason
    `_render_bulk_rename` gives. Two state keys carry it: ``runcard_suggestions``
    (the detector's rows, pending) and ``runcard_detect_result`` (the last
    rename's or save's outcome), both dropped with the folder.
    """
    runcard_folder = _runcard_folder(root_folder, state)
    pending = state.get("runcard_suggestions")
    with st.expander("Detect runcards for untagged runs",
                     expanded=bool(pending or state.get("runcard_detect_result"))):
        st.caption(
            "Replays every recipe in the runcard folder against each untagged run and "
            "proposes the one that produced it. CLEANING-1~5 and the other files at the "
            "folder's top level can match any number of runs; every other card matches one. "
            "Rename puts the tag in the filename (`<timestamp>~<tag>.csv`); a folder that a "
            "sync writes into needs that sync to keep renamed files (SulfurSync: keepRenamed)."
        )
        col_folder, col_change = st.columns([3, 1])
        with col_folder:
            st.caption(f"Runcard folder: {runcard_folder or 'none found next to this datalog folder'}")
        with col_change:
            if st.button("Change...", key="datalog_runcard_folder_pick", width="stretch"):
                try:
                    picked = prompt_folder_path(default_dir=runcard_folder or root_folder,
                                                title="Select Runcard Folder")
                except Exception as e:
                    st.error(f"Failed to open folder browser: {e}")
                    picked = None
                if picked:
                    state["runcard_folder"] = picked
                    state["runcard_suggestions"] = None
                    st.rerun()
        if not runcard_folder:
            st.info("Choose the tool's runcard folder to detect runcards.")
            return

        runs = scan_folder(root_folder)
        newest = runs[0].start_time if runs else pd.Timestamp.now()
        # Keyed by folder, for the reason the tolerance input gives: `value`
        # seeds a keyed widget once, and another folder's date would stick.
        since = st.date_input("Runs from", value=(newest - _DEFAULT_DETECT_SPAN).date(),
                              key=f"datalog_detect_since_{root_folder}")
        if st.button("Detect runcards", width="stretch"):
            bar = st.progress(0.0, text="Reading runs")
            try:
                state["runcard_suggestions"] = _detect_runcards(
                    root_folder, runcard_folder, lambda f, text: bar.progress(min(f, 1.0), text=text),
                    since=pd.Timestamp(since))
            except OSError as e:
                st.error(f"Could not read the folders: {e}")
                state["runcard_suggestions"] = None
            finally:
                bar.empty()
            state["runcard_detect_result"] = None

        found = state.get("runcard_suggestions")
        if found is not None:
            rows = found["rows"]
            if found["twins"]:
                st.warning(
                    f"{len(found['twins'])} untagged file(s) are copies of a tagged run "
                    "(same start time) -- typically the original name put back by a one-way "
                    "copy after the file was renamed. They are left alone:\n"
                    + "\n".join(f"- {Path(a).name} = {Path(b).name}" for a, b in found["twins"][:20])
                )
            if found.get("multi"):
                st.caption(
                    f"{len(found['multi'])} file(s) span more than 12 hours -- continuous logs "
                    "holding several runs -- and are not tagged: one tag cannot name them all."
                )
            if found["unreadable"]:
                st.caption(
                    f"{len(found['unreadable'])} run(s) stopped before enough of a recipe ran to "
                    "compare, and get no suggestion."
                )
            if not rows:
                st.info(f"No untagged run to suggest a runcard for ({found['cards']} runcards read).")
            else:
                st.write(f"{len(rows)} suggestion(s) from {found['cards']} runcards. "
                         "Edit a tag or untick a row before saving.")
                edited = st.data_editor(
                    pd.DataFrame(rows).drop(columns=["path"]),
                    hide_index=True, width="stretch",
                    disabled=["Start", "File", "Confidence", "Differences", "Notes"],
                    # A new detection is a new widget: its pending edits belong
                    # to the rows they were made on, not to whatever is listed next.
                    key=f"datalog_runcard_suggestions_{root_folder}_{found['nonce']}",
                )
                chosen = [
                    (row["path"], str(edited.iloc[i]["Tag"]).strip())
                    for i, row in enumerate(rows)
                    if bool(edited.iloc[i]["Save"]) and str(edited.iloc[i]["Tag"]).strip()
                ]
                col_rename, col_tags, col_clear = st.columns(3)
                if col_rename.button(f"Rename {len(chosen)} file(s)", width="stretch", type="primary"):
                    state["runcard_detect_result"] = _rename_to_tags(root_folder, chosen)
                    state["runcard_suggestions"] = None
                    st.rerun()
                if col_tags.button("Save as tags only", width="stretch"):
                    try:
                        set_runcard_tags(root_folder, dict(chosen))
                    except OSError as e:
                        # A read-only share, or runcard_tags.json held open by
                        # datalog_monitor. Nothing was written: the batch is one write.
                        st.error(f"Could not save the tags: {e}")
                    else:
                        state["runcard_detect_result"] = {"tagged": len(chosen)}
                        state["runcard_suggestions"] = None
                        st.rerun()
                if col_clear.button("Discard", width="stretch"):
                    state["runcard_suggestions"] = None
                    st.rerun()

        done = state.get("runcard_detect_result")
        if done is not None:
            if "tagged" in done:
                st.success(f"Saved {done['tagged']} tag(s) to runcard_tags.json.")
            else:
                st.success(f"Renamed {len(done['renamed'])} file(s).")
                if done["skipped"]:
                    st.warning("Not renamed:\n" + "\n".join(
                        f"- {Path(p).name}: {reason}" for p, reason in done["skipped"]))
                if done.get("tag_orphaned"):
                    st.warning("Renamed, but the tag did not move with the file:\n" + "\n".join(
                        f"- {Path(old).name} → {Path(new).name}: {why}"
                        for old, new, why in done["tag_orphaned"]))


def _rename_to_tags(root_folder: str, chosen) -> dict:
    """Rename each ``(path, tag)`` to ``<start>~<tag>.csv`` through the renamer's own sweep.

    `renamer.execute_sweep` is what makes this safe to run on hundreds of files:
    a collision or a locked file costs its own row, and a tag already stored
    for the old name follows the file. A run still being written is skipped --
    the controller (and the sync behind it) would carry on under the old name.
    """
    plans, skipped = [], []
    now = time.time()   # epoch, like getmtime; a naive pd.Timestamp would read as UTC
    for path, tag in chosen:
        try:
            if now - os.path.getmtime(path) < _LIVE_WINDOW_S:
                skipped.append((path, "still being written; try again later"))
                continue
            meta = get_run_metadata(path)
        except OSError as e:
            skipped.append((path, str(e)))
            continue
        if meta is None:
            skipped.append((path, "not a readable run"))
            continue
        src = Path(path)
        dest = src.with_name(renamer.target_filename(meta.start_time, tag, src.suffix))
        if dest == src:
            continue
        plans.append(renamer.RenamePlan(path=path, new_path=str(dest), will_change=True,
                                        collision=dest.exists()))
    result = renamer.execute_sweep(root_folder, plans)
    # Orphaned files *were* renamed -- only their tag did not follow -- so they
    # are reported apart from the files that kept their name.
    return {"renamed": result.renamed, "skipped": skipped + result.skipped,
            "tag_orphaned": result.tag_orphaned}


@st.cache_data(show_spinner=False, max_entries=256)
def _cached_suggestion(path: str, mtime: float, runcard_folder: str, signature, used_items, reference_paths):
    """One run's suggestion, recomputed only when the run, the cards or the other tags change.

    Every argument is cache key. References are read only when the plain answer
    is a cleaning recipe: they exist to tell CLEANING-N versions apart, and a
    growth run's answer does not depend on them.
    """
    steps = _run_steps(path, mtime)
    if not _usable(steps):
        return None
    cards = _runcards(runcard_folder, signature)
    if not cards:
        return None
    used = dict(used_items)
    s = runcard_match.suggest_runcards([(path, steps)], cards, used=used).get(path)
    if s is not None and "CLEAN" in s.card_name.upper() and reference_paths:
        s = runcard_match.suggest_runcards(
            [(path, steps)], cards, used=used, references=_load_references(reference_paths)
        ).get(path, s)
    return s


def _single_suggestion(root_folder: str, meta, tags: dict, runs):
    """The detector's answer for one selected run, or None when there is nothing to say.

    Excludes the run's own tag from the taken cards, so a tagged run is checked
    against its own card rather than pushed off it.
    """
    runcard_folder = _runcard_folder(root_folder, get_datalog_state())
    if not runcard_folder:
        return None
    try:
        mtime = os.path.getmtime(meta.path)
    except OSError:
        return None
    window = (meta.start_time - pd.Timedelta(days=30), meta.start_time + pd.Timedelta(days=30))
    used, reference_paths = _tag_context(root_folder, runs, tags, window=window, exclude=meta.path)
    return _cached_suggestion(meta.path, mtime, runcard_folder, _runcard_signature(runcard_folder),
                              tuple(sorted(used.items())), reference_paths)


def _use_suggested_tag(root_folder: str, file_path: str, widget_key: str, tag: str) -> None:
    """Button callback: store the suggested tag and show it in the tag box.

    Written through the same sidecar call as a typed tag, and the box's session
    value is set here -- in a callback, before the rerun paints the widget --
    because a keyed text_input ignores ``value`` after its first render.
    """
    try:
        set_runcard_tag(root_folder, file_path, tag)
    except OSError as e:
        st.session_state[_TAG_ERROR_KEY_PREFIX + file_path] = f"Could not save the runcard tag: {e}"
        return
    st.session_state[widget_key] = tag


def _render_suggestion(root_folder: str, meta, tags: dict, runs, widget_key: str) -> None:
    """Under a selected run's tag box: the detected runcard, or a check of the tag it has."""
    try:
        s = _single_suggestion(root_folder, meta, tags, runs)
    except Exception as e:     # a detector bug must not cost the tag editor
        st.caption(f"Runcard detection failed: {e}")
        return
    if s is None:
        return
    current = get_runcard_tag(root_folder, meta.path, tags)
    detail = f"{s.confidence} confidence"
    if s.notes:
        detail += " -- " + "; ".join(s.notes)
    if not current:
        st.caption(f"Detected runcard: **{s.tag}** ({detail})")
        if s.differences:
            st.caption("Differs from the card: " + "; ".join(s.differences[:4]))
        st.button(f"Use {s.tag}", key=f"datalog_use_suggestion_{meta.path}",
                  on_click=_use_suggested_tag, args=(root_folder, meta.path, widget_key, s.tag))
        return
    same = runcard_match.base_tag(current) == runcard_match.base_tag(s.tag) or (
        "CLEAN" in current.upper() and "CLEAN" in s.tag.upper()
        and (runcard_match.cleaning_name(current) is None
             or runcard_match.cleaning_name(current) == runcard_match.cleaning_name(s.tag))
    )
    if same:
        st.caption(f"The log matches this tag's runcard ({s.card_name}, {s.confidence} confidence).")
    else:
        st.warning(f"The log looks like **{s.tag}**, not {current} ({detail}).")


#: Where a failed tag write parks its message until a render pass can show it.
#: Keyed by file path, so two selected runs cannot overwrite each other's error.
_TAG_ERROR_KEY_PREFIX = "datalog_tag_error_"


def _commit_tag_edit(root_folder: str, file_path: str, widget_key: str) -> None:
    """Write the tag box's contents to `runcard_tags.json`. One write per edit.

    An ``on_change`` callback, not a "the widget disagrees with disk" test, and
    the difference is a hung page rather than a nicety. `get_runcard_tag`
    answers from the *filename* whenever no tag is stored, so for a run already
    named ``2026-08-06_173312~VBBE00.csv`` that test can never go false once
    the box is cleared: the write deletes the entry, the next pass re-derives
    "VBBE00" from the name, the empty box still disagrees, and the page reruns
    and rewrites the sidecar forever. Firing on the edit itself bounds it to
    one write, and there is no `st.rerun()` here because Streamlit already
    reruns the page after a widget callback -- the source app writes without
    one too.

    **Clearing the box drops the stored tag; it does not untag the run.** An
    empty tag is how "no stored tag" is expressed in the frozen sidecar (see
    `tag_store.set_runcard_tag`), which hands the question back to the
    filename -- so the run list goes on showing "VBBE00" while the box stays
    empty until the selection is rebuilt. The only way to take the tag off such
    a run is to change its *name*: type the tag it should carry and use the
    rename button, or rename the file by hand.
    """
    try:
        set_runcard_tag(root_folder, file_path, st.session_state[widget_key])
    except OSError as e:
        # Deferred, not raised and not drawn: a callback runs before the rerun
        # that paints the page, so there is no container to st.error into from
        # here. The render pass below pops this and shows it.
        st.session_state[_TAG_ERROR_KEY_PREFIX + file_path] = (
            f"Could not save the runcard tag: {e}"
        )


def _render_tag_editor(root_folder: str, meta, tags: dict, runs) -> None:
    """One selected run's tag box and its rename button.

    Both widgets are keyed by absolute path -- the identity of the thing being
    edited -- so two runs rendered in one pass cannot share a widget, and a
    rename (which changes the path) retires the old widget rather than carrying
    a stale value onto the new name.

    The box's ``value`` seeds it on first render only; afterwards Streamlit
    holds the widget's own value against that key. That is what makes a cleared
    box stay cleared for the rest of the selection while the table's Runcard
    column reverts to the filename's tag -- see `_commit_tag_edit` for why the
    two are allowed to disagree.
    """
    widget_key = f"datalog_tag_{meta.path}"
    st.text_input(
        f"{meta.start_time:%Y-%m-%d %H:%M:%S}",
        value=get_runcard_tag(root_folder, meta.path, tags),
        key=widget_key,
        on_change=_commit_tag_edit,
        args=(root_folder, meta.path, widget_key),
    )
    tag_error = st.session_state.pop(_TAG_ERROR_KEY_PREFIX + meta.path, None)
    if tag_error:
        st.error(tag_error)
    _render_suggestion(root_folder, meta, tags, runs, widget_key)

    plan = renamer.plan_single_rename(root_folder, meta)
    if plan.will_change:
        if st.button(f"Rename file to {Path(plan.new_path).name}",
                     key=f"datalog_rename_{meta.path}", width="stretch"):
            try:
                renamer.apply_rename(root_folder, plan)
            except (renamer.RenameCollision, OSError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.success(f"Renamed to {Path(plan.new_path).name}")
                # App-scoped on purpose: the path just changed, so every widget
                # keyed by it is stale and the page should rebuild.
                st.rerun()
    else:
        st.caption("Filename already matches this tag.")


def _render_run_table(root_folder: str, mode: str):
    """The run list, its search, and the inline tag/rename editor. Returns selected paths.

    Every widget key here is prefixed with the page, because widget keys share
    one flat namespace with the state module's own ``"datalog"`` entry and with
    whatever the other pages register. Past that prefix, each key carries the
    identity of the data its value only makes sense against: the per-run
    widgets by absolute path, so two runs rendered in one pass cannot share a
    widget, and the table itself by folder as well as mode, so a selection made
    by row position cannot outlive the row set it was pointing at.

    Two keys deliberately carry no data identity, because their values are
    preferences rather than readings of anything: ``datalog_window`` (how many
    runs to list) and ``datalog_search``. A search string surviving a folder
    change is visible and self-correcting -- the box still holds it -- where a
    surviving row index is not.
    """
    scanned = scan_folder(root_folder)
    # Hidden, not deleted. See io.duplicates: the copier that re-creates these
    # runs on the tool PC every few minutes, so the list showing a run once is
    # a promise this page can keep and removing the file is not.
    runs = duplicates.without_duplicates(root_folder, scanned)
    hidden = len(scanned) - len(runs)

    col_caption, col_refresh = st.columns([3, 1])
    with col_caption:
        st.caption(f"{len(runs)} run(s) found on disk · auto-refreshes every 60s")
        if hidden:
            st.caption(
                f"{hidden} duplicate file(s) hidden — the same run also stored "
                "under its original filename. Duplicate cleanup deletes them."
            )
    with col_refresh:
        # The return value is ignored on purpose: pressing it *is* the refresh,
        # because the resulting fragment rerun re-walks the folder and re-scans
        # any file whose mtime moved.
        st.button("Refresh now", key="datalog_refresh", width="stretch")

    if not runs:
        st.info("No CSV files found in this folder.")
        return []

    tags = load_runcard_tags(root_folder)

    query = st.text_input("Search (timestamp or runcard)", key="datalog_search").strip().lower()
    if query:
        # Search always looks across every run, not just the recent window --
        # the whole reason to search is that the run is not in the last 50.
        candidate_runs = runs
    else:
        window_label = st.selectbox("Show", list(RECENT_WINDOW_OPTIONS), key="datalog_window")
        limit = RECENT_WINDOW_OPTIONS[window_label]
        candidate_runs = runs[:limit] if limit else runs

    table_rows = []
    row_meta = []
    for meta in candidate_runs:
        tag = get_runcard_tag(root_folder, meta.path, tags)
        if query and query not in f"{meta.start_time:%Y-%m-%d %H:%M:%S} {tag}".lower():
            continue
        row_meta.append(meta)
        table_rows.append({
            "Start": meta.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "Program": meta.first_program,
            "Rows": meta.row_count,
            "Duration": _format_duration(meta.end_time - meta.start_time),
            "Runcard": tag,
        })

    if not table_rows:
        st.info("No run matches that search.")
        return []

    st.caption(f"Showing {len(table_rows)} run(s)")

    # The mode is in the key, so switching Single/Compare starts a fresh widget
    # and drops the selection. That is deliberate: the two modes disagree about
    # how many rows may be selected, and carrying three rows into a single-row
    # table is a selection Streamlit has no way to honour.
    #
    # The folder is in it for a sharper reason: this selection is row
    # *positions*, and nothing else on the page is. Held across a folder change
    # under one key, "row 3" survives and silently resolves to whichever
    # unrelated run now sits third in the new folder -- in range, so the
    # stale-index guard below cannot see it. A per-folder key retires the
    # selection with the folder it was made in.
    selection_mode = "single-row" if mode == "Single run" else "multi-row"
    event = st.dataframe(
        pd.DataFrame(table_rows), hide_index=True, width="stretch",
        on_select="rerun", selection_mode=selection_mode,
        key=f"datalog_run_table_{mode}_{root_folder}",
    )
    selected_indices = event.selection.rows if event and event.selection else []
    # A selection can carry over from before the row set shrank -- narrowing the
    # search, a smaller "Show" window, or the 60-second re-scan finding fewer
    # matching runs. st.dataframe's selection is keyed by row position, not by
    # run identity, so a stale index has to be dropped rather than crash.
    selected_metas = [row_meta[i] for i in selected_indices if i < len(row_meta)]

    if selected_metas:
        st.divider()
        st.caption("Edit runcard tag for the selected run(s):")
        for meta in selected_metas:
            _render_tag_editor(root_folder, meta, tags, runs)

    return [meta.path for meta in selected_metas]


def _render_tolerance_settings(root_folder: str, pv_sv_pairs) -> dict:
    """The global tolerance and its per-channel overrides. Returns what to judge against.

    One row per PV/SV pair **detected in the file**, not per selected channel:
    the violations table covers every pair regardless of what is plotted, so
    the tolerances have to as well.

    The returned dict is built from what is on screen, not from what is on
    disk. The source app returned the live global input mixed with the
    disk-loaded overrides, so moving a channel's tolerance changed nothing
    until Save was pressed while moving the global changed the plot
    immediately — two controls in one table behaving differently, with nothing
    saying so. Here both apply at once and Save persists exactly what is
    already being shown.

    An override equal to the global is dropped rather than stored, so a channel
    dragged back to the default follows the global again instead of being
    pinned to whatever it happened to equal that day.

    Save **merges** into the folder's overrides (`threshold_store.save_tolerances`):
    only the channels this run shows are rewritten. Writing the rows wholesale
    deleted the override of every channel the open run happens not to carry.
    """
    thresholds = load_thresholds(root_folder)
    with st.expander("PV/SV tolerance settings"):
        # Keyed by folder: `value` seeds a keyed widget once and is ignored on
        # every later pass, so one global key would carry the previous folder's
        # tolerance across a folder change and show a number that folder's
        # thresholds.json does not contain.
        global_default = st.number_input(
            "Global default tolerance (%)", min_value=0.0, step=0.5,
            value=float(thresholds["global_default_pct"]),
            key=f"datalog_global_tolerance_{root_folder}",
        )
        pair_names = [name for name, _, _ in pv_sv_pairs]
        rows = pd.DataFrame({
            "Channel": pair_names,
            "Tolerance %": [
                float(thresholds["overrides"].get(name, global_default)) for name in pair_names
            ],
        })
        # The folder *and* the channel set are in the key, because a keyed
        # fixed-row data_editor's identity is its column names/types and its row
        # COUNT -- never its cell values -- and Streamlit re-applies pending
        # edited_rows positionally. Two runs in one folder with different
        # channels but the same number of pairs would otherwise have an
        # unsaved edit land on the wrong channel; the folder is there for the
        # same reason the global input carries it, since the overrides these
        # rows show are loaded per folder.
        editor_key = f"datalog_tolerance_editor_{root_folder}_{'|'.join(pair_names)}"
        edited = st.data_editor(rows, hide_index=True, key=editor_key, width="stretch")
        overrides = {
            row["Channel"]: float(row["Tolerance %"])
            for _, row in edited.iterrows()
            if pd.notna(row["Tolerance %"]) and abs(row["Tolerance %"] - global_default) > 1e-9
        }
        if st.button("Save tolerance settings", width="stretch"):
            try:
                # Merges into the folder's file, so the Runcard check's timing
                # keys survive -- and the overrides merge too, against the file
                # as it is now, so a channel this run lacks keeps the override
                # saved for it. See threshold_store.save_tolerances.
                save_tolerances(root_folder, global_default, pair_names, overrides)
            except OSError as e:
                # A read-only share, or thresholds.json held open by
                # datalog_monitor on the tool PC. Handled the way the folder
                # preference below is: the page says so and stays usable, since
                # the tolerances on screen still drive this session's chart and
                # tables whether or not they reached disk.
                st.error(f"Could not save tolerance settings: {e}")
            else:
                st.success("Saved to this folder's thresholds.json.")
    return {"global_default_pct": global_default, "overrides": overrides}


def _render_save(fig, tables, default_dir: str, default_stem: str) -> None:
    """One dialog, one stem, numbered files: the chart as PNG and the tables as CSV.

    The chart is rasterized at its own layout height, capped, because a stacked
    figure's height carries its panel count — exporting at a fixed 600 px would
    squash twelve panels into a strip.
    """
    st.subheader("Save")
    st.caption(
        "One dialog, one name. The chart lands as `_1_chart.png` and each table "
        "beside it as CSV, numbered so a file browser lists them in reading order."
    )
    if not st.button("💾 Save Datalog Report As...", width="stretch"):
        return

    try:
        save_path = prompt_save_path(
            default_dir=default_dir,
            default_filename=f"{default_stem}_datalog.png",
            title="Save Datalog Report",
            filetypes=(("PNG images", "*.png"), ("All files", "*.*")),
            default_extension=".png",
        )
        if not save_path:
            return  # cancelled

        # The chosen name supplies the stem only; each artifact appends its own
        # number and extension, so none can overwrite another and the dialog's
        # ".png" does not end up on a workbook of numbers.
        base, _ext = os.path.splitext(save_path)
        saved_files = []

        with st.spinner("Rasterizing chart..."):
            height = int(min(float(fig.layout.height or 600), _MAX_EXPORT_HEIGHT_PX))
            png = export_figure_png(fig, width=1400, height=height, scale=2.0)
        number, suffix = _CHART_ARTIFACT
        target = create_filename(base, f"{number}_{suffix}", "png")
        with open(target, "wb") as f:
            f.write(png)
        saved_files.append(target)

        for (number, suffix), table in zip(_TABLE_ARTIFACTS, tables):
            if table is None or table.empty:
                continue
            target = create_filename(base, f"{number}_{suffix}", "csv")
            table.to_csv(target, index=False, encoding="utf-8")
            saved_files.append(target)

        st.success("Saved:\n" + "\n".join(f"- {p}" for p in saved_files))
    except Exception as e:
        st.error(f"Failed to save: {e}")


def _plotted_channels(plot_items):
    """The statistics table's channels: what is on the chart, PVs then SVs then plain.

    Scoped to `plot_items` -- the operator's own selection -- because the table
    answers a question about the panels directly above it. Built from the whole
    file instead, it puts 35 rows under a four-panel chart, leaves "Channels to
    plot" a control that changes the picture and not the numbers, and in
    comparison mode multiplies those 35 by the number of runs, into the
    exported summary CSV as well.

    Grouped by kind rather than interleaved per pair, so a reader scanning the
    Avg column compares process values against process values instead of
    reading every other row as a setpoint.

    The violations table is deliberately *not* scoped this way: an excursion on
    a channel nobody thought to select is the one most worth surfacing, so it
    keeps covering every pair in the file.
    """
    return (
        [item["pv_col"] for item in plot_items if item["kind"] == "pvsv"]
        + [item["sv_col"] for item in plot_items if item["kind"] == "pvsv"]
        + [item["channel"] for item in plot_items if item["kind"] == "plain"]
    )


def _render_single_run_view(path: str, df: pd.DataFrame, plot_items, pv_sv_pairs,
                            thresholds: dict, root_folder: str) -> None:
    """One run: chart, summary statistics, violations."""
    pressure_group_selected = any(
        item["kind"] == "plain" and item["channel"] in PRESSURE_GROUP_NUMERIC
        for item in plot_items
    )
    if pressure_group_selected and PRESSURE_GROUP_LABEL_COLUMN in df.columns:
        # The gauge's range label, surfaced as text because it is categorical
        # and cannot be plotted. A pressure trace read on the 100 Torr range and
        # one read on 1000 Torr differ by 10x and nothing in the trace says so.
        gauge_values = df[PRESSURE_GROUP_LABEL_COLUMN].dropna().unique()
        st.caption(f"{PRESSURE_GROUP_LABEL_COLUMN}: {', '.join(str(v) for v in gauge_values)}")

    # Every pair in the file, not just the plotted ones: a violation on a
    # channel nobody happened to select is the one most worth surfacing. Only
    # the red markers are limited to the panels on screen.
    violations_df, masks = compute_all_violations(df, pv_sv_pairs, thresholds)

    fig = build_single_run_figure(df, plot_items, masks)
    # Keyed by the run, not just by the page: a keyed chart keeps its frontend
    # state across reruns, so one fixed key hands the next run the previous
    # one's zoom -- a pressure axis held at 0-8 Torr while the new trace lives
    # at 1e-3, and nothing on screen saying it is not the full trace.
    render_plot(fig, key=f"datalog_single_plot_{path}")

    st.subheader("Summary statistics")
    summary_df = compute_summary_stats(df, _plotted_channels(plot_items))
    st.dataframe(summary_df, width="stretch", hide_index=True)

    st.subheader("Out-of-tolerance violations")
    if violations_df.empty:
        st.success("No PV/SV violations found for this run.")
    else:
        st.dataframe(violations_df, width="stretch", hide_index=True)

    _render_save(fig, (summary_df, violations_df), root_folder, Path(path).stem)


def _render_comparison_view(root_folder: str, loaded, plot_items, pv_sv_pairs,
                            thresholds: dict) -> None:
    """Several runs, aligned on process t=0: chart, then both tables stacked with a Run column."""
    tags = load_runcard_tags(root_folder)
    runs = []
    align_times = []
    fallback_labels = []
    for path, df in loaded:
        tag = get_runcard_tag(root_folder, path, tags)
        start = df[TIME_COLUMN].iloc[0]
        # One string in three places: the trace name, the legend entry, and the
        # Run column of both tables.
        label = (f"{tag} " if tag else "") + f"{start:%Y-%m-%d %H:%M:%S}"
        align_time = (
            find_final_plateau_start(df, ALIGNMENT_SV_COLUMN)
            if ALIGNMENT_SV_COLUMN in df.columns else None
        )
        if align_time is None:
            align_time = start
            fallback_labels.append(label)
        runs.append((label, df))
        align_times.append(align_time)

    if fallback_labels:
        # Loud, and naming the runs: a silent fallback to wall-clock start looks
        # exactly like a real misalignment between two runs.
        st.warning(
            f"{ALIGNMENT_SV_COLUMN} not available in: {', '.join(fallback_labels)} "
            "— aligned by run start time instead."
        )

    fig = build_comparison_figure(runs, plot_items, align_times)
    # Keyed by the run set, for the reason `_render_single_run_view` gives: a
    # kept zoom from a different set of runs is a chart that does not show what
    # it says it shows.
    render_plot(fig, key="datalog_comparison_plot_" + "|".join(path for path, _df in loaded))

    summaries = []
    violation_tables = []
    # One channel list for every run: it comes from the selection, which is the
    # same for all of them, and a run missing one of those columns is skipped
    # by compute_summary_stats rather than reported as a row of NaNs.
    stat_channels = _plotted_channels(plot_items)
    for label, df in runs:
        stats = compute_summary_stats(df, stat_channels)
        if not stats.empty:
            stats.insert(0, "Run", label)
            summaries.append(stats)
        violations, _masks = compute_all_violations(df, pv_sv_pairs, thresholds)
        if not violations.empty:
            violations.insert(0, "Run", label)
            violation_tables.append(violations)

    summary_df = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()
    violations_df = (
        pd.concat(violation_tables, ignore_index=True) if violation_tables else pd.DataFrame()
    )

    st.subheader("Summary statistics")
    st.dataframe(summary_df, width="stretch", hide_index=True)

    st.subheader("Out-of-tolerance violations")
    if violations_df.empty:
        st.success("No PV/SV violations found across selected runs.")
    else:
        st.dataframe(violations_df, width="stretch", hide_index=True)

    _render_save(fig, (summary_df, violations_df), root_folder, "comparison")


@st.fragment(run_every="60s")
def _render_workspace(root_folder: str, mode: str) -> None:
    """Run picker plus everything downstream of a selection, in one reactive scope.

    Selecting a row is a widget interaction *inside* this fragment, so the
    fragment must also own the chart and the tables: code outside a fragment
    does not re-run when only the fragment re-runs, so a row click would land
    in session state with nothing downstream ever executing to notice it. The
    chart would keep showing the previous run until some unrelated full-page
    rerun happened — pressing Browse, toggling the view mode, a bulk-rename
    button — and to the operator, clicking a run would do nothing. The tag
    editor and the per-file rename button inside the table fail the same way.

    It cannot be widened to cover the sidebar or the bulk-rename block either:
    a Streamlit fragment may write into only one container, so a scope holding
    both the sidebar and the main area raises at runtime. That is why the
    folder picker, the bulk rename and the mode radio sit outside it.

    ``run_every="60s"`` is the other half of its job — a periodic re-scan so a
    run written while the page is open appears without anyone pressing
    anything. The tick is cheap: the directory walk is uncached, but each
    file's metadata is cached against its mtime, so an unchanged folder costs
    one `stat` per file.
    """
    st.subheader("4. Runs")
    selected_paths = _render_run_table(root_folder, mode)
    if not selected_paths:
        st.info("Select a run above to view its data.")
        return

    loaded = []
    with st.spinner("Reading run CSVs..."):
        for path in selected_paths:
            # Per-run, so one truncated or locked file costs its own row rather
            # than the whole comparison.
            try:
                df = load_run(path)
            except Exception as e:
                st.warning(f"Could not read {Path(path).name}: {e}")
                continue
            if df.empty:
                st.warning(f"{Path(path).name} has no rows with a readable timestamp.")
                continue
            loaded.append((path, df))

    if not loaded:
        return

    # The channel catalog comes from the first selected run. In comparison mode
    # a run whose controller logged a different column set simply contributes no
    # trace to the panels it lacks (see build_comparison_figure) rather than
    # forcing the offered channels down to the intersection, which would hide
    # channels the operator can see on the run they picked first.
    columns = list(loaded[0][1].columns)
    catalog = _build_plot_item_catalog(columns)
    pv_sv_pairs = detect_pv_sv_pairs(columns)

    st.subheader("5. Tolerances")
    thresholds = _render_tolerance_settings(root_folder, pv_sv_pairs)

    st.subheader("6. Channels")
    labels = [item["label"] for item in catalog]
    default_labels = [label for label in labels if label in DEFAULT_CHANNEL_LABELS]
    # Keyed, so the selection survives switching to another run. Streamlit
    # stores a multiselect's value as its option *labels*, not as indices, so a
    # run with a different column set drops the labels it doesn't have rather
    # than silently selecting whatever is now at that position.
    selected_labels = st.multiselect(
        "Channels to plot", labels, default=default_labels, key="datalog_channels",
    )
    plot_items = [item for item in catalog if item["label"] in selected_labels]
    if not plot_items:
        st.info("Select at least one channel to plot.")
        return

    try:
        if mode == "Single run":
            path, df = loaded[0]
            _render_single_run_view(path, df, plot_items, pv_sv_pairs, thresholds, root_folder)
        else:
            _render_comparison_view(root_folder, loaded, plot_items, pv_sv_pairs, thresholds)
    except Exception as e:
        st.error(f"Could not draw this run: {e}")


st.title("📈 Datalog")
st.markdown(
    "Tool-controller run logs: one run's channels against their setpoints, or "
    "several runs overlaid on the moment each reached its growth temperature."
)

state = get_datalog_state()

# ------------------------------------------------------------------ Folder
st.subheader("1. Data Folder")
if state["folder"] is None:
    # Only before the first pick of the session. The folder is several levels
    # down a share and picking it is a dozen clicks; a page that charges that
    # on every restart is a page nobody opens twice.
    state["folder"] = load_last_folder()

tool, tool_changed = render_tool_select(DATALOG, state["folder"], key="datalog_tool")
if tool_changed:
    tool_folder = tool.subfolder(DATALOG)
    if tool_folder is None:
        st.error(f"{tool.name} declares no datalog folder in tool_layouts.json.")
    else:
        state["folder"] = str(tool_folder)
        try:
            save_last_folder(state["folder"])
        except OSError as e:
            st.warning(f"Could not remember this folder: {e}")
        # The runcard folder is left to the guess from the new datalog folder,
        # not the layout's runcard subfolder: matching needs the operator's
        # whole recipe folder (HA1P01's CLEANING cards sit at RUNCARD/lcy, one
        # level above the layout's RUNCARD/lcy/NSHA1P01).
        reset_results(state)
        st.rerun()

col_pick, col_path = st.columns([1, 3])
with col_pick:
    pick_clicked = st.button("Select Datalog Folder", width="stretch")
with col_path:
    st.caption(state["folder"] or "No folder selected yet.")

if pick_clicked:
    try:
        picked = prompt_folder_path(default_dir=state["folder"] or "", title="Select Datalog Folder")
    except Exception as e:
        st.error(f"Failed to open folder browser: {e}")
        picked = None

    if picked:
        state["folder"] = picked
        try:
            save_last_folder(picked)
        except OSError as e:
            # A preference that cannot be written is not a reason to lose the
            # pick itself, so this says so and carries on.
            st.warning(f"Could not remember this folder: {e}")
        # A pending rename plan holds absolute paths into the previous folder.
        reset_results(state)
        st.rerun()

root_folder = state["folder"]
if not root_folder:
    st.info("Choose a data folder to get started.")
    st.stop()

# ------------------------------------------------------------------ Renaming
st.subheader("2. Filenames")
_render_runcard_detection(root_folder, state)
_render_bulk_rename(root_folder, state)
_render_duplicate_cleanup(root_folder, state)

# ------------------------------------------------------------------ View mode
st.subheader("3. View Mode")
mode = st.radio(
    "View mode", ["Single run", "Compare runs"], horizontal=True, label_visibility="collapsed",
    key="datalog_view_mode",
)

_render_workspace(root_folder, mode)
