"""
NexAnalyzer: Nexstrom's measurement data analyzer.

Streamlit entry point and composition root. This file does global setup
(page config, module session state) and page routing only — analysis code
lives in modules/<technique>/, platform code in core/, and page content in
pages/.

Adding a technique module: drop it under modules/, initialize its session
state here, and register its pages below.

The navigation's first two sections, "Analysis" and "Process", are both named
for a **data source** rather than a technique. Spectra, QC Report and Material
Presets read measurements taken after a run; Datalog and Runcard read the
deposition tool's own logs and recipes. That split is a fact about where the
files come from, so no future technique can falsify it.

The first group used to be called "Raman & PL", which stopped being true at
v4.0.0 when the QC Panel grew an OM image and Material Presets grew an optical
block, and was unlabelled from then until v5.2.0. "Analysis" is the heading
that argument allows: it says what the three pages do with a measurement, and
stays true however many techniques they learn.

"Publish" (v5.8.0) is the one section named for something else, and it has to
be. OneNote reads no data source of its own — it takes the other pages'
finished results and sends them out. Filing it under either heading would have
claimed it reads measurements or reads tool logs, and it does neither, so the
data-source rule above holds for exactly the two sections that state it. A
third page that sends results somewhere belongs here; a page that reads a new
kind of file does not.

"Setup" (v5.15.0) holds the Tools page, which reads no data either: it records
where each tool's folders are, for every page above to use.
"""

import streamlit as st

from core.version import APP_NAME, APP_TAGLINE, __version__
from modules.spectra.ui.session_state import initialize_session_state

# Configure page (must run once, before any other Streamlit command)
st.set_page_config(
    page_title=APP_NAME,
    page_icon="📊",
    layout="wide",
    # Expanded by default — the sidebar (file upload, material preset,
    # Run Auto-Workflow) is the only way to process a spectrum.
    initial_sidebar_state="expanded",
)

# Initialize each module's session state (shared across all its pages)
initialize_session_state()

spectra_page = st.Page("pages/1_Spectra.py", title="Spectra", icon="📊", default=True)
qc_report_page = st.Page("pages/2_QC_Report.py", title="QC Report", icon="🔬")
material_presets_page = st.Page("pages/3_Material_Presets.py", title="Material Presets", icon="🧪")
datalog_page = st.Page("pages/4_Datalog.py", title="Datalog", icon="📈")
runcard_page = st.Page("pages/5_Runcard.py", title="Runcard", icon="📋")
onenote_page = st.Page("pages/6_OneNote.py", title="OneNote", icon="📓")
tools_page = st.Page("pages/7_Tools.py", title="Tools", icon="🧰")

nav = st.navigation(
    {
        "Analysis": [
            spectra_page,
            qc_report_page,
            material_presets_page,
        ],
        "Process": [
            datalog_page,
            runcard_page,
        ],
        "Publish": [
            onenote_page,
        ],
        "Setup": [
            tools_page,
        ],
    },
    position="sidebar",
)

st.sidebar.caption(f"{APP_NAME} v{__version__} — {APP_TAGLINE}")

nav.run()
