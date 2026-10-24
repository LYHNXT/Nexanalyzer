"""Session state for the Runcard page.

Its own namespace, separate from the QC Report's folder-and-sample model: that
page reads one folder as one sample and produces one set of figures, while this
one reads a folder as a *list* of recipes and draws whichever of them the
operator ticks. A single-sample shape has nowhere to put the other thirty-nine.

A plain dict rather than a dataclass, matching the other state modules —
callers mutate it in place and Streamlit persists the object. It has no
migration story, so read any key added later with `.get()`.

**One spelling per function.** The stemmed names — `initialize_runcard_state`,
`get_runcard_state`, after `modules/optical/ui/qc_report_state.py` — are the
whole public API. Bare `initialize_state`/`get_state` aliases sat beside them
for a while so a caller could pick either; that is the shape this repo already
refuses for the diagnostic ratios (one constant, not the pair written out
again). Two names for one function have nothing keeping them in step, so the
day one of them grows a guard the other lacks, the same call reads as correct
at both sites and only one of them is.
"""

import streamlit as st

_KEY = "runcard"


def initialize_runcard_state() -> None:
    """Create st.session_state["runcard"] if absent. Idempotent."""
    if _KEY not in st.session_state:
        st.session_state[_KEY] = {
            "folder": None,
            "selected": (),
            # Everything below is derived and must appear in one of the
            # artifact tuples; see _SELECTIONS.
            "runcards": None,        # list[str], the paths that read as recipes
            "rejected": None,        # int, CSVs in the folder that did not
            "profiles": None,        # dict[str, RuncardProfile]
            "rows": None,            # list[dict], one summary row per recipe
            "species": None,         # list[str], the gas columns this folder needs
            "errors": None,          # list[(path, message)] from the scan
            "figures": None,         # dict[str, go.Figure] for the ticked rows
            "focus": None,           # a recipe another page asked to chart; see focus_runcard
        }


def get_runcard_state() -> dict:
    """Initialize (if needed) and return the Runcard state dict."""
    initialize_runcard_state()
    return st.session_state[_KEY]


def runcard_table_key(folder: str | None) -> str:
    """The recipe table's widget key, carrying the folder it is listing.

    `st.dataframe` identifies its row selection by `key` and `selection_mode`
    alone — replacing the data deliberately does *not* clear a selection — so
    under one fixed key a tick on row 3 of folder A survived the switch to
    folder B and charted whatever recipe B happened to list third, which the
    operator never ticked.

    Clearing `state["selected"]` in a `reset_*` cannot fix that on its own:
    the widget, not this dict, is what the page re-reads on every rerun, so it
    would hand back the stale row index and the page would write it straight
    back in. Only a different key is a different widget, and a new widget
    starts unticked. Re-scanning the *same* folder keeps the key, and so keeps
    the tick — which is right, because those row indices still mean what they
    meant.
    """
    return f"runcard_table_{folder}"


#: The operator's own choices. Everything else in the state dict is derived
#: from them and gets dropped by `reset_results`; a derived key missing from
#: the tuples below outlives the run that produced it, which is how one
#: folder's recipe list comes to sit under another folder's name.
_SELECTIONS = ("folder", "selected")

#: Built from *every* input — the folder found the file and the selection
#: chose it — so any change at all invalidates them. They are also the cheap
#: ones: a figure is pure arithmetic over a few dozen events, where the scan
#: below walks and parses every CSV in the tree. Nothing here is worth
#: protecting, and keeping a stale one would put one recipe's gantt under
#: another recipe's heading.
_COMPOSITE_ARTIFACTS = ("figures",)

#: Everything the folder walk produced, grouped because they are produced
#: together and invalidated together. This is the expensive group — 53 files
#: parsed and replayed on the example folder — which is exactly why ticking a
#: different row must not touch it.
_SCAN_ARTIFACTS = ("runcards", "rejected", "profiles", "rows", "species", "errors")

#: A recipe handed over by another page (the Split Table's "Open in Runcard").
#: A path into one folder, so a folder change drops it with the scan.
_FOCUS_ARTIFACTS = ("focus",)


def reset_results(state: dict) -> None:
    """Drop every derived artifact, keeping the operator's selections.

    Called whenever the folder changes, which invalidates *everything*: the
    recipe list, every profile behind it, and the figures drawn from those. The
    point of dropping them is that leaving stale derived keys behind means the
    previous folder's forty recipes stay in the table under the new folder's
    path, and ticking one charts a file that is not there.

    For a change to the ticked rows alone, use `reset_figures`: re-ticking must
    not re-walk a folder.
    """
    reset_scan_results(state)
    reset_figures(state)
    for key in _FOCUS_ARTIFACTS:
        state[key] = None


def reset_scan_results(state: dict) -> None:
    """Drop the recipe list and everything derived from parsing it.

    These reset together because one folder walk produces all of them in one
    pass — the list, the profile behind each entry, the table row built from
    that profile, the gas columns the rows needed, and the files that failed.
    Keeping any one of them across a re-scan would describe a folder by a
    mixture of two readings of it.
    """
    for key in _SCAN_ARTIFACTS:
        state[key] = None


def reset_figures(state: dict) -> None:
    """Drop the built figures, keeping the scan.

    Called when the ticked rows change. The figures are the only artifact the
    selection invalidates, and re-deriving one is arithmetic over a few dozen
    events — so this is about a chart never outliving the row that asked for
    it, not about cost.
    """
    for key in _COMPOSITE_ARTIFACTS:
        state[key] = None


def chartable_selection(state: dict) -> list[str]:
    """The ticked recipes that still have a profile behind them, in tick order.

    A selection can outlive the scan that produced it. `runcard_table_key`
    unticks the table on a folder change, but only when there *is* a table:
    point the page at a folder whose CSVs all read as datalogs and no table is
    drawn, so `selected` keeps naming the previous folder's paths while
    `profiles` is empty. A recipe that failed to replay leaves the same gap
    inside one folder.

    The page skips its whole Profile section when this is empty rather than
    drawing the heading over zero charts, because a heading with nothing under
    it reads as a chart that broke instead of as a folder with nothing to
    chart.
    """
    profiles = state.get("profiles") or {}
    return [path for path in (state.get("selected") or ()) if profiles.get(path) is not None]


def focus_runcard(state: dict, folder: str, path: str) -> None:
    """Point the page at `folder` with the recipe at `path` charted, for another page.

    The table cannot be given a tick, so the page charts `focus` while nothing
    is ticked, and scans the folder on arrival rather than waiting for
    Scan / Reload. Ticking any row replaces it.
    """
    if state.get("folder") != folder:
        state["folder"] = folder
        reset_results(state)
    state["focus"] = path
