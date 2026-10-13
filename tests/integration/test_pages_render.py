"""
Smoke test: every page renders without raising.

Cheap insurance against the failure mode a refactor actually causes — a stale
import or a renamed module that no unit test touches, which would otherwise
only surface as a red Streamlit traceback in front of a user. Rendering is all
this checks; page behavior is covered by the per-page integration tests.
"""

import pytest
from streamlit.testing.v1 import AppTest

from core.paths import PROJECT_ROOT

PAGES = [
    "app.py",
    "pages/1_Spectra.py",
    "pages/2_QC_Report.py",
    "pages/3_Material_Presets.py",
    "pages/4_Datalog.py",
    "pages/5_Runcard.py",
    "pages/6_OneNote.py",
    "pages/7_Tools.py",
]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(page):
    # Absolute, via PROJECT_ROOT: Streamlit 1.63 resolves a relative AppTest
    # path against the file that calls from_file(), not the working directory,
    # so a bare "app.py" is looked for in tests/integration/ and every
    # integration test in the suite dies with FileNotFoundError before the page
    # is ever executed.
    at = AppTest.from_file(str(PROJECT_ROOT / page), default_timeout=60).run()
    assert not at.exception, f"{page} raised: {[e.value for e in at.exception]}"
