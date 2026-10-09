"""Integration test for the sidebar's "Run All Files" batch path.

Two defects motivated it, both in modules/spectra/ui/sidebar.py:

  - the progress bar advanced BEFORE each file's work, so it read 100% for the
    whole of the final (usually slowest) fit and was then torn down;
  - `st.rerun()` immediately after the loop discarded the summary and the list
    of failed files, leaving a multi-file batch with no record of what happened.

The second is why this runs through AppTest rather than a unit test: whether
the report survives to the user is a Streamlit lifecycle question.
"""

import numpy as np
from streamlit.testing.v1 import AppTest

from core.paths import PROJECT_ROOT


def _write_silicon_raman_file(path, seed, peak_center=520.0):
    """A single peak matching data/materials.json's Silicon (Raman) preset."""
    x = np.linspace(0, 1000, 2000)
    sigma = 8.0 / 2.355
    rng = np.random.default_rng(seed)
    y = 500.0 + 1000.0 * np.exp(-0.5 * ((x - peak_center) / sigma) ** 2) + rng.normal(0, 5, size=x.size)
    with open(path, "w") as f:
        for xi, yi in zip(x, y):
            f.write(f"{xi:.4f}\t{yi:.4f}\n")


def _app_with_files(tmp_path, count=3):
    """The Spectra page with `count` files loaded through the app's own loader.

    `pending_files_to_load` is the seam the Browse button writes and the sidebar
    pops, so the fixture exercises the real parse-and-register path.
    """
    paths = []
    for i in range(1, count + 1):
        path = tmp_path / f"RM_sample{i}.txt"
        _write_silicon_raman_file(path, seed=i)
        paths.append(str(path))

    at = AppTest.from_file(str(PROJECT_ROOT / "pages/1_Spectra.py"), default_timeout=180)
    at.session_state["pending_files_to_load"] = paths
    at.run()
    return at


def _select_silicon(at):
    """Pick the Silicon (Raman) preset.

    The selectbox's options are ("Material", "Mode") tuples rendered through a
    format_func, so set_value needs the tuple; passing the formatted label makes
    format_func index into the string ('S (i)').
    """
    material = next(s for s in at.sidebar.selectbox if "aterial" in (s.label or ""))
    material.set_value("Silicon").run()
    return at


class TestRunAllFilesBatch:
    def test_the_batch_report_survives_the_run(self, tmp_path):
        """The whole point of dropping st.rerun(): after a batch the user must
        still be able to read how many files succeeded."""
        at = _select_silicon(_app_with_files(tmp_path, count=3))

        next(b for b in at.sidebar.button if "Run All Files" in b.label).click().run()

        assert not at.exception
        messages = [m.value for m in at.sidebar.success] + [m.value for m in at.sidebar.warning]
        assert any("3" in m for m in messages), f"no batch summary survived: {messages}"

    def test_the_status_resolves_instead_of_spinning(self, tmp_path):
        at = _select_silicon(_app_with_files(tmp_path, count=3))

        next(b for b in at.sidebar.button if "Run All Files" in b.label).click().run()

        statuses = at.sidebar.status
        assert statuses, "no status was rendered for the batch"
        assert statuses[0].state == "complete"
        assert "3" in statuses[0].label

    def test_every_file_actually_got_fitted(self, tmp_path):
        """Guards against the progress change quietly skipping an item -- an
        off-by-one in the loop would show here."""
        at = _select_silicon(_app_with_files(tmp_path, count=3))

        next(b for b in at.sidebar.button if "Run All Files" in b.label).click().run()

        files = at.session_state["files"]
        assert len(files) == 3
        assert all(f.fit_done for f in files.values()), \
            {name: f.fit_done for name, f in files.items()}

    def test_the_page_settles_without_a_second_run(self, tmp_path):
        """Dropping the rerun means the run that starts the batch is also the
        run that renders the results, including the export block below it."""
        at = _select_silicon(_app_with_files(tmp_path, count=3))

        next(b for b in at.sidebar.button if "Run All Files" in b.label).click().run()

        assert not at.exception
        # show_fit / show_components are written during the batch and read by
        # checkboxes further down the same function; without a rerun they must
        # still take effect in this very run.
        assert at.session_state["show_fit"] is True
        assert at.session_state["show_components"] is True


class TestRunAutoWorkflowSingle:
    """The single-file Run had the same trailing st.rerun(), which discarded its
    success summary and, worse, a failure's error -- after the workflow had
    already reset the spectrum to raw."""

    @staticmethod
    def _run(at):
        next(b for b in at.sidebar.button if "Run Auto-Workflow" in b.label).click().run()
        return at

    def test_the_success_summary_survives_the_run(self, tmp_path):
        at = self._run(_select_silicon(_app_with_files(tmp_path, count=1)))

        assert not at.exception
        assert at.sidebar.success, "the single-run success summary was discarded"

    def test_a_failed_run_says_why(self, tmp_path):
        # Silicon has no PL block, so a PL file cannot be fitted with it.
        path = tmp_path / "PL_sample1.txt"
        _write_silicon_raman_file(path, seed=1)
        at = AppTest.from_file(str(PROJECT_ROOT / "pages/1_Spectra.py"), default_timeout=180)
        at.session_state["pending_files_to_load"] = [str(path)]
        at.run()

        at = self._run(_select_silicon(at))

        assert not at.exception
        assert at.sidebar.error, "a failed run left no error on the page"


class TestSameNameFromAnotherFolder:
    """Wafer folders reuse point names; files used to be keyed by bare name, so
    the second wafer's RM_center.txt was dropped as "already loaded"."""

    @staticmethod
    def _load(paths):
        at = AppTest.from_file(str(PROJECT_ROOT / "pages/1_Spectra.py"), default_timeout=180)
        at.session_state["pending_files_to_load"] = [str(p) for p in paths]
        at.run()
        return at

    @staticmethod
    def _two_wafers(tmp_path):
        paths = []
        for i, wafer in enumerate(("W01", "W02"), start=1):
            (tmp_path / wafer).mkdir()
            path = tmp_path / wafer / "RM_center.txt"
            _write_silicon_raman_file(path, seed=i)
            paths.append(path)
        return paths

    def test_both_files_load_under_distinct_names(self, tmp_path):
        at = self._load(self._two_wafers(tmp_path))

        assert not at.exception
        files = at.session_state["files"]
        assert sorted(files) == ["RM_center (W02).txt", "RM_center.txt"]
        assert files["RM_center (W02).txt"].source_dir.endswith("W02")

    def test_the_rename_is_reported(self, tmp_path):
        at = self._load(self._two_wafers(tmp_path))

        assert any("RM_center (W02).txt" in m.value for m in at.sidebar.info)

    def test_the_same_file_twice_is_still_skipped(self, tmp_path):
        path = self._two_wafers(tmp_path)[0]
        at = self._load([path])
        at.session_state["pending_files_to_load"] = [str(path)]
        at.run()

        assert list(at.session_state["files"]) == ["RM_center.txt"]
        assert any("already loaded" in m.value for m in at.sidebar.info)
