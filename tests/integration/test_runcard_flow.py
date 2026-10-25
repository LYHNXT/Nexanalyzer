"""Page-level tests for pages/5_Runcard.py, for the three failures that only exist in the wiring.

These need the page script because each one is about an interaction between a
Streamlit widget and the state dict, which neither side shows on its own: a
write that escapes before the scan it precedes, a widget whose identity
outlives its data, and a heading drawn before its content is known. The pure
logic behind them is unit-tested in test_runcard_state.py.

`AppTest` runs the script in this process, so patching the *source* module
(`core.io.folder_picker`, `modules.runcard.io.config_store`) is enough -- the
page's `from x import y` lines re-execute on every run and pick the patch up.
"""

import os
from datetime import datetime

import pytest
from streamlit.testing.v1 import AppTest

import core.io.folder_picker as folder_picker
import modules.runcard.io.config_store as config_store
from core.paths import PROJECT_ROOT
from tests.datalog_fixtures import RUNCARD_FULL

_PAGE = str(PROJECT_ROOT / "pages" / "5_Runcard.py")


def _recipe_folder(tmp_path, name="A"):
    """A folder holding one real recipe, so a scan has something to list."""
    folder = tmp_path / name
    folder.mkdir()
    # newline="" because the real cards are CRLF and `csv` is what has to see
    # the line endings, not Python's text layer.
    (folder / "VBBE00.csv").write_text(RUNCARD_FULL, encoding="utf-8", newline="")
    return str(folder)


def _pick(at, monkeypatch, folder):
    """Point the folder dialog at `folder` and click Select Runcard Folder."""
    monkeypatch.setattr(folder_picker, "prompt_folder_path", lambda **kwargs: folder)
    for button in at.button:
        if button.label == "Select Runcard Folder":
            return button.click().run()
    raise AssertionError("the page drew no Select Runcard Folder button")


class TestAPreferenceThatCannotBeWrittenDoesNotCostTheScan:
    """`data/` is not always writable, and the pick is not the preference.

    An install under Program Files, a read-only checkout, a full disk: the
    PermissionError out of `save_runcard_folder` used to escape and abort the
    script *before* `_scan_folder`, so choosing a folder in the dialog appeared
    to do nothing whatsoever. The Datalog page's pick has always guarded this.
    """

    @pytest.fixture(autouse=True)
    def _read_only_data_dir(self, monkeypatch):
        def _denied(*args, **kwargs):
            raise PermissionError(13, "Access is denied", "data/runcard.json")

        monkeypatch.setattr(config_store, "save_runcard_folder", _denied)

    def test_the_scan_still_runs(self, tmp_path, monkeypatch):
        folder = _recipe_folder(tmp_path)

        at = AppTest.from_file(_PAGE, default_timeout=60).run()
        at = _pick(at, monkeypatch, folder)

        assert not at.exception, [e.value for e in at.exception]
        # `rows` is None until a scan fills it, so this is the scan itself
        # having run and not merely the pick having been recorded.
        assert len(at.session_state["runcard"]["rows"]) == 1

    def test_the_folder_is_still_selected(self, tmp_path, monkeypatch):
        folder = _recipe_folder(tmp_path)

        at = AppTest.from_file(_PAGE, default_timeout=60).run()
        at = _pick(at, monkeypatch, folder)

        assert at.session_state["runcard"]["folder"] == folder

    def test_it_says_the_folder_was_not_remembered(self, tmp_path, monkeypatch):
        # Swallowing it silently would leave the operator to rediscover every
        # session that the page has stopped reopening where they left it.
        folder = _recipe_folder(tmp_path)

        at = AppTest.from_file(_PAGE, default_timeout=60).run()
        at = _pick(at, monkeypatch, folder)

        assert any("Could not remember this folder" in w.value for w in at.warning)


class TestTheRecipeTableIsANewWidgetPerFolder:
    """st.dataframe keys its selection on key + selection_mode, never on data.

    Under one fixed key, a tick on row 3 of folder A survived the switch to
    folder B, and the page charted whatever recipe B happened to list third --
    a recipe the operator never ticked.
    """

    def test_the_key_names_the_folder_being_listed(self, tmp_path, monkeypatch):
        folder = _recipe_folder(tmp_path)

        at = AppTest.from_file(_PAGE, default_timeout=60).run()
        at = _pick(at, monkeypatch, folder)

        assert at.dataframe[0].key == f"runcard_table_{folder}"

    def test_switching_folders_replaces_the_widget(self, tmp_path, monkeypatch):
        folder_a = _recipe_folder(tmp_path, "A")
        folder_b = _recipe_folder(tmp_path, "B")

        at = AppTest.from_file(_PAGE, default_timeout=60).run()
        at = _pick(at, monkeypatch, folder_a)
        key_a = at.dataframe[0].key
        at = _pick(at, monkeypatch, folder_b)

        assert at.dataframe[0].key != key_a


class TestTheProfileSectionIsSkippedWhenNothingCanBeCharted:
    """A selection outlives the scan that produced it.

    Keying the table by folder unticks it on a switch -- but only when there
    is a table. A folder whose CSVs all read as datalogs draws none, so
    `selected` goes on naming the previous folder's paths with no profile
    behind them. The heading was written before that was known, leaving
    the Profile heading standing over an empty section, which reads as a chart that
    broke rather than as a folder with nothing to chart.
    """

    def _page_with_a_stale_selection(self, tmp_path):
        at = AppTest.from_file(_PAGE, default_timeout=60)
        at.session_state["runcard"] = {
            "folder": str(tmp_path),
            "selected": (r"C:\previous\VBBE00.csv",),
            "runcards": [],
            "rejected": 3,
            "profiles": {},
            "rows": [],
            "species": [],
            "errors": [],
            "figures": None,
        }
        return at.run()

    def test_no_profile_heading_is_drawn(self, tmp_path):
        at = self._page_with_a_stale_selection(tmp_path)

        assert not at.exception, [e.value for e in at.exception]
        assert not any(m.value == "#### Profile" for m in at.markdown)

    def test_the_folder_is_still_reported_as_empty(self, tmp_path):
        # The section that *should* speak for this state is section 2's
        # "everything here was rejected" warning, and it still does.
        at = self._page_with_a_stale_selection(tmp_path)

        assert any("read as a recipe" in w.value for w in at.warning)


class TestTheTableIsOrderedNewestFirst:
    """A folder of fifty recipes is nearly always opened about the last few.

    A recipe's clock starts at zero and the file records no date inside itself,
    so the filesystem's mtime is the only thing that can answer "which did I
    write last". Sorting on it means the answer is on screen without a click.
    """

    def _folder_with(self, tmp_path, names_and_times):
        folder = tmp_path / "cards"
        folder.mkdir()
        for name, when in names_and_times:
            path = folder / f"{name}.csv"
            path.write_text(RUNCARD_FULL, encoding="utf-8", newline="")
            os.utime(path, (when, when))
        return str(folder)

    def test_the_newest_recipe_is_the_first_row(self, tmp_path, monkeypatch):
        folder = self._folder_with(tmp_path, [
            ("OLDEST", 1_000_000_000),
            ("NEWEST", 1_400_000_000),
            ("MIDDLE", 1_200_000_000),
        ])

        at = AppTest.from_file(_PAGE, default_timeout=60).run()
        at = _pick(at, monkeypatch, folder)

        assert not at.exception, [e.value for e in at.exception]
        assert [row["Run"] for row in at.session_state["runcard"]["rows"]] == [
            "NEWEST", "MIDDLE", "OLDEST",
        ]

    def test_every_row_carries_its_modified_date(self, tmp_path, monkeypatch):
        folder = self._folder_with(tmp_path, [("A", 1_000_000_000)])

        at = AppTest.from_file(_PAGE, default_timeout=60).run()
        at = _pick(at, monkeypatch, folder)

        row = at.session_state["runcard"]["rows"][0]
        assert row["Modified"] is not None
        assert row["Modified"].year == datetime.fromtimestamp(1_000_000_000).year
