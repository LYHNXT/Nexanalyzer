"""Session state for the OneNote page.

Its own namespace, matching the other state modules. A plain dict rather than
a dataclass — callers mutate it in place and Streamlit persists the object. It
has no migration story, so read any key added later with `.get()`.

**The upload log is a selection, not an artifact.** Everywhere else in this
app a derived key is dropped the moment its input changes, because a stale
figure sitting under a new sample's name is a lie. The log is the exception:
it records what *happened* — this artifact went to this page at this time, or
failed with this error — and that stays true no matter what the operator picks
next. Dropping it on a folder change would erase the record of a failed upload
at exactly the moment the operator moved on, which is the silent loss the
non-blocking design was chosen to avoid.

So `_SELECTIONS` carries the log, and `reset_results` leaves it alone. A
Streamlit `st.error` is not enough on its own: it vanishes on the next rerun,
and a rerun is one widget click away.
"""

import streamlit as st

_KEY = "onenote"


def initialize_onenote_state() -> None:
    """Create st.session_state["onenote"] if absent. Idempotent."""
    if _KEY not in st.session_state:
        st.session_state[_KEY] = {
            "tool_folder": None,
            "wafer_id": None,
            # The upload log outlives every selection; see the module
            # docstring. list[dict]: artifact, wafer_id, status, detail.
            "log": [],
            # MSAL's pending device-code flow between "Sign in" and "I've
            # signed in"; kept across wafer changes so a sign-in survives one.
            "device_flow": None,
            # Everything below is derived and must appear in one of the
            # artifact tuples; see _SELECTIONS.
            "material_check": None,   # prefix_store.MaterialCheck
            "page_preview": None,     # existing page content, before append
        }


def get_onenote_state() -> dict:
    """Initialize (if needed) and return the OneNote state dict."""
    initialize_onenote_state()
    return st.session_state[_KEY]


#: The operator's own choices, plus the upload log — which is a record of
#: past events rather than something derived from the current selection, and
#: so must survive every reset. Everything else is derived and gets dropped
#: by `reset_results`.
_SELECTIONS = ("tool_folder", "wafer_id", "log", "device_flow")

#: Derived from the wafer ID and the selected material together, so any
#: change to either invalidates them. Both are cheap — a dict lookup and one
#: Graph read — so there is nothing to protect, and keeping a stale one would
#: show one wafer's verdict above another wafer's upload button.
_CHECK_ARTIFACTS = ("material_check", "page_preview")


def reset_results(state: dict) -> None:
    """Drop the derived verdict and page preview, keeping the log.

    Called when the wafer or the selected material changes. The log is
    deliberately kept: it is the only surviving record that an upload failed,
    and the point of a non-blocking upload is that a failure stays visible
    until someone acts on it.
    """
    for key in _CHECK_ARTIFACTS:
        state[key] = None


def record_upload(
    state: dict,
    artifact: str,
    wafer_id: str,
    status: str,
    detail: str = "",
) -> None:
    """Append one outcome to the upload log.

    `status` is "ok" or "failed". Timestamps are added by the caller rather
    than here, so this stays testable without freezing a clock.
    """
    state.setdefault("log", []).append(
        {
            "artifact": artifact,
            "wafer_id": wafer_id,
            "status": status,
            "detail": detail,
        }
    )


def failed_uploads(state: dict) -> list[dict]:
    """Every logged failure, in the order they happened.

    The page renders these as a standing list rather than a toast, so a
    failure the operator scrolled past is still there when they come back.
    """
    return [entry for entry in state.get("log", []) if entry.get("status") == "failed"]
