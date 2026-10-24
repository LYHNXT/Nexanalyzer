"""Session state for the Datalog page.

Its own namespace, separate from the QC Report's folder-and-scan model: that
page reads one sample folder and derives everything from a single batch run,
while this one points at a tree of several hundred live CSVs and derives
nothing until the operator picks a run out of it. The expensive results here
are cached against each file's mtime in `io.scanner`, not held in session
state, so what is left to keep is small.

Named for its page, and its functions with it, matching
`modules.optical.ui.qc_report_state`. It was `ui/state.py` with bare
`get_state`/`initialize_state` briefly: two modules called `ui.state` read
identically at an import site and at the top of a traceback, and the page that
imports both ends up aliasing one of them, which is the point at which "the
module path already says datalog" stops being true.

A plain dict rather than a dataclass, matching the other state modules —
callers mutate it in place and Streamlit persists the object. It has no
migration story, so read any key added later with `.get()`.
"""

import streamlit as st

_KEY = "datalog"


def initialize_datalog_state() -> None:
    """Create st.session_state["datalog"] if absent. Idempotent."""
    if _KEY not in st.session_state:
        st.session_state[_KEY] = {
            "folder": None,
            # Everything below is derived and must appear in one of the
            # artifact tuples; see _SELECTIONS.
            "rename_plans": None,      # pending sweep, already filtered to will_change
            "rename_result": None,     # the last sweep's renamed/skipped lists
            "duplicate_plans": None,   # pending cleanup, one plan per stale copy
            "duplicate_result": None,  # the last cleanup's removed/skipped lists
            "runcard_folder": None,    # recipes to match against; None = guess from folder
            "runcard_suggestions": None,   # the detector's rows, waiting to be applied
            "runcard_detect_result": None, # the last rename's or save's outcome
            "focus_run": None,         # a run another page asked to open; see focus_run
            "focus_query": None,       # the search string that shows it
        }


def get_datalog_state() -> dict:
    """Initialize (if needed) and return the Datalog state dict."""
    initialize_datalog_state()
    return st.session_state[_KEY]


#: The operator's own choice. Everything else in the state dict is derived from
#: it and gets dropped by `reset_results`; a derived key missing from the tuple
#: below outlives the folder it was computed against.
_SELECTIONS = ("folder",)

#: The bulk-rename workflow's two steps: a plan waiting to be confirmed, and the
#: outcome of the last one that was. They reset together because they are the
#: same conversation -- previewing a new sweep must clear the banner from the
#: previous one, or a preview appears to report results it has not produced yet.
_RENAME_ARTIFACTS = ("rename_plans", "rename_result")

#: The duplicate-cleanup workflow's same two steps. Separate from
#: `_RENAME_ARTIFACTS` so a new rename preview does not silently discard a
#: cleanup banner the operator has not read, but reset by the same folder
#: change: a `DuplicatePlan` holds absolute paths into the previous folder, and
#: confirming one after a switch would Recycle-Bin files nobody is looking at.
_DUPLICATE_ARTIFACTS = ("duplicate_plans", "duplicate_result")

#: The runcard detector's state. ``runcard_folder`` is in here rather than in
#: `_SELECTIONS` although the operator can set it: it is chosen *for* a datalog
#: folder -- normally guessed from it -- and one tool's recipe folder carried
#: onto another tool's logs would match every run against the wrong recipes.
#: The suggestions hold absolute paths into the folder, like a rename plan.
_DETECT_ARTIFACTS = ("runcard_folder", "runcard_suggestions", "runcard_detect_result")

#: A run handed over by another page (the Split Table's "Open in Datalog"),
#: and the search string that narrows the list to it. A path into one folder,
#: so a folder change drops it like a rename plan.
_FOCUS_ARTIFACTS = ("focus_run", "focus_query")

#: The search box's and the view radio's widget keys, which `focus_run` seeds.
SEARCH_KEY = "datalog_search"
VIEW_MODE_KEY = "datalog_view_mode"


def reset_results(state: dict) -> None:
    """Drop every derived artifact, keeping the operator's selection.

    Called when the folder changes, and the thing it prevents is not cosmetic:
    a `RenamePlan` holds absolute paths into the *previous* folder, so
    confirming it after a folder switch touches files the operator is no longer
    looking at, and then raises `ValueError` out of `tag_store._relative_key`
    because those paths are not under the new root. `renamer.apply_rename` now
    rolls each such rename back, which makes that a failed sweep rather than a
    silently re-shuffled folder — but a rollback that itself fails leaves a run
    orphaned, so dropping the plan here is still the actual fix.
    """
    reset_rename_results(state)
    reset_duplicate_results(state)
    for key in _DETECT_ARTIFACTS + _FOCUS_ARTIFACTS:
        state[key] = None


def reset_rename_results(state: dict) -> None:
    """Drop the pending rename plan and the last sweep's banner."""
    for key in _RENAME_ARTIFACTS:
        state[key] = None


def reset_duplicate_results(state: dict) -> None:
    """Drop the pending cleanup plan and the last cleanup's banner."""
    for key in _DUPLICATE_ARTIFACTS:
        state[key] = None


def focus_run(state: dict, folder: str, path: str, query: str) -> None:
    """Point the page at `folder` with the run at `path` open, for another page.

    `st.dataframe` cannot be given a selection, so the run is shown the way an
    operator would find it: the search box is seeded with `query` (its start
    timestamp), which lists that run alone, and the page treats it as selected
    until the operator picks a row or changes the search.
    """
    if state.get("folder") != folder:
        state["folder"] = folder
        reset_results(state)
    state["focus_run"] = path
    state["focus_query"] = query
    st.session_state[SEARCH_KEY] = query
    st.session_state[VIEW_MODE_KEY] = "Single run"
