"""
Spectra page: load Raman/PL files, run the material-preset auto-workflow,
inspect the fit, and export -- all in the page, top to bottom in the order
an operator works (v5.43.0; until then the controls lived in the sidebar).
The layout and its ordering constraints are in modules/spectra/ui/workspace.py.
"""

import streamlit as st

from core.version import APP_NAME, REPO_URL, __version__
from modules.spectra.ui.session_state import initialize_session_state
from modules.spectra.ui.workspace import (
    process_pending_loads,
    render_exports,
    render_fit_results,
    render_fit_summary,
    render_run_setup,
    render_spectrum,
)
from modules.spectra.viz.live_plot import sync_pending_file_switch, sync_pending_view_options

initialize_session_state()

# Must run before anything renders the layer toggles: applies a pending
# prev/next/select file switch and updates the show_* state, which has to
# land before those widgets are instantiated (see sync_pending_file_switch()).
sync_pending_file_switch()

# Same ordering rule: "Follow the data again" sits beside the toggles it
# resets, so it defers the write to here.
sync_pending_view_options()

# Files queued by "Add files…" on the previous run, loaded before anything
# reads the file list.
process_pending_loads()

title_col, export_col = st.columns([1, 1.6], vertical_alignment="bottom")
with title_col:
    st.caption("ANALYSIS · SPECTRA")
    st.title("Raman & PL Spectra")
    st.markdown("Load a file, pick a material, run.")
# Filled last (see render_exports()): the exports follow this run's toggles
# and fit, which the sections below set.
export_slot = export_col.container()

render_run_setup()
render_spectrum()
render_fit_results()
render_fit_summary()

with export_slot:
    render_exports()

st.markdown("---")
st.markdown(
    f"{APP_NAME} v{__version__} | "
    f"[User Guide]({REPO_URL}/blob/main/USER_GUIDE.md) | "
    f"[Report Issues]({REPO_URL}/issues)"
)
