"""The Spectra page's fit workspace (v5.43.0): the controls moved out of the
sidebar into the page, and three of them are new.

Through AppTest, because what is pinned here is page layout and Streamlit's
widget lifecycle: that nothing is left behind in the sidebar, that the file
switcher's new controls switch files through the same deferred path the
arrows use, and that the baseline view -- a segmented control standing in for
the old "Peaks on Background" checkbox -- writes and pins `show_on_background`
the way the checkbox did.
"""

import numpy as np
from streamlit.testing.v1 import AppTest

from core.paths import PROJECT_ROOT
from modules.spectra.ui.session_state import VIEW_OPTIONS_PINNED_KEY


def _write_silicon_raman_file(path, seed, peak_center=520.0):
    """A single peak matching data/materials.json's Silicon (Raman) preset."""
    x = np.linspace(0, 1000, 2000)
    sigma = 8.0 / 2.355
    rng = np.random.default_rng(seed)
    y = 500.0 + 1000.0 * np.exp(-0.5 * ((x - peak_center) / sigma) ** 2) + rng.normal(0, 5, size=x.size)
    with open(path, "w") as f:
        for xi, yi in zip(x, y):
            f.write(f"{xi:.4f}\t{yi:.4f}\n")


def _app(tmp_path, count=3, fit=False):
    """The Spectra page with `count` Silicon files loaded, optionally fitted."""
    paths = []
    for i in range(1, count + 1):
        path = tmp_path / f"RM_sample{i}.txt"
        _write_silicon_raman_file(path, seed=i)
        paths.append(str(path))

    at = AppTest.from_file(str(PROJECT_ROOT / "pages/1_Spectra.py"), default_timeout=180)
    at.session_state["pending_files_to_load"] = paths
    at.run()
    if fit:
        next(s for s in at.selectbox if s.label == "Material preset").set_value("Silicon").run()
        next(b for b in at.button if "Run All Files" in b.label).click().run()
        assert all(f.fit_done for f in at.session_state["files"].values())
    return at


def _button(at, label):
    return next(b for b in at.button if b.label == label)


class TestNothingIsLeftInTheSidebar:
    def test_the_sidebar_holds_no_spectra_controls(self, tmp_path):
        at = _app(tmp_path, fit=True)

        assert not at.exception
        assert not at.sidebar.button
        assert not at.sidebar.selectbox
        assert not at.sidebar.checkbox

    def test_every_former_sidebar_action_is_on_the_page(self, tmp_path):
        at = _app(tmp_path, fit=True)

        labels = {b.label for b in at.button}
        for wanted in ("Add files…", "Remove File", "Delete All", "Reset to Raw",
                       "Run Auto-Workflow", "Run All Files (3)",
                       "Save Master CSV…", "Save PNGs…"):
            assert wanted in labels, f"{wanted!r} missing from {sorted(labels)}"
        downloads = {d.label for d in at.get("download_button")}
        assert {"PNG", "HTML", "CSV"} <= downloads


class TestFileSwitcher:
    def test_next_moves_to_the_next_file(self, tmp_path):
        at = _app(tmp_path)
        files = list(at.session_state["files"])
        at.session_state["current_file"] = files[0]
        at.run()

        _button(at, "▶").click().run()

        assert at.session_state["current_file"] == files[1]

    def test_a_file_pill_switches_to_that_file(self, tmp_path):
        at = _app(tmp_path)
        files = list(at.session_state["files"])
        at.session_state["current_file"] = files[0]
        at.run()

        at.pills[0].set_value(files[2]).run()

        assert at.session_state["current_file"] == files[2]
        assert not at.exception

    def test_one_file_shows_no_pills(self, tmp_path):
        """A row with one pill says nothing the dropdown does not."""
        at = _app(tmp_path, count=1)

        assert not at.pills


class TestBaselineView:
    def test_baseline_removed_turns_off_peaks_on_background(self, tmp_path):
        at = _app(tmp_path, fit=True)
        assert at.session_state["show_on_background"] is True

        at.segmented_control[0].set_value("Baseline removed").run()

        assert at.session_state["show_on_background"] is False
        assert not at.exception

    def test_the_choice_is_pinned_like_the_checkbox_was(self, tmp_path):
        """Set by hand, it survives a file switch instead of following the
        new file's processing stage."""
        at = _app(tmp_path, fit=True)
        at.segmented_control[0].set_value("Baseline removed").run()

        assert "show_on_background" in at.session_state[VIEW_OPTIONS_PINNED_KEY]
        _button(at, "▶").click().run()
        assert at.session_state["show_on_background"] is False


class TestFitResultsTable:
    def test_intensity_and_area_are_separate_named_columns(self, tmp_path):
        at = _app(tmp_path, fit=True)

        columns = list(at.dataframe[0].value.columns)
        assert "Intensity" in columns and "Area" in columns
        assert "Status" in columns
        assert not any("mplitude" in c for c in columns)
