"""Page-level tests for pages/8_Split_Table.py and the hand-offs it makes.

The hand-offs need the receiving page's script: `st.dataframe` cannot be given
a selection, so "open on this run" is the receiving page reading a focus out
of its own state, and only running that page shows whether it does.

`AppTest` cannot tick a dataframe row, so the hand-off is staged here the way
the Split Table's button leaves it: through `focus_run` / `focus_runcard`'s
state, written into the session before the receiving page runs.
"""

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import core.ui.tool_select as tool_select
from core.io.tool_layout import ToolLayout
from core.paths import PROJECT_ROOT
from tests.datalog_fixtures import DATALOG_SHORT, RUNCARD_FULL, write_datalog

_PAGES = PROJECT_ROOT / "pages"


def _captions(at):
    return [c.value for c in at.caption]


@pytest.fixture
def tool(tmp_path, monkeypatch):
    """A tool with a two-row split table, offered by the Tool dropdown."""
    root = tmp_path / "HA1P01"
    workbook = root / "SPLITTABLE" / "HA_SplitTable_from_runcards.xlsx"
    workbook.parent.mkdir(parents=True)
    data = pd.DataFrame({
        "WaferID": ["HADI51", "CLEANING-4"], "Month": ["202610", "202610"],
        "Run_Type": ["growth", "cleaning"], "OM_Reference_pct": [95.5, None],
        "T_growth_C": [900, 950],
    })
    with pd.ExcelWriter(workbook) as xl:
        data.to_excel(xl, sheet_name="HA_SplitTable_gen", index=False)
    layout = ToolLayout(name="HA1P01", root=root, spec={
        "splittable": {"file": "SPLITTABLE/HA_SplitTable_from_runcards.xlsx"}})
    monkeypatch.setattr(tool_select, "configured_tools", lambda: [layout])
    return layout


class TestTheSplitTablePage:
    def test_it_lists_the_workbook_and_draws_both_charts(self, tool):
        at = AppTest.from_file(str(_PAGES / "8_Split_Table.py"), default_timeout=60)
        at.session_state["splittable"] = {"tool_folder": str(tool.root)}
        at.run()

        assert not at.exception, [e.value for e in at.exception]
        assert any("2 rows" in c for c in _captions(at))
        assert any("2 of 2 row(s)" in c for c in _captions(at))

    def test_a_tool_without_a_split_table_says_where_to_declare_one(self, tool, monkeypatch):
        bare = ToolLayout(name="VB1P01", root=tool.root.parent / "VB1P01", spec={})
        monkeypatch.setattr(tool_select, "configured_tools", lambda: [bare])
        at = AppTest.from_file(str(_PAGES / "8_Split_Table.py"), default_timeout=60)
        at.session_state["splittable"] = {"tool_folder": str(bare.root)}
        at.run()

        assert not at.exception
        assert "declares no split table" in at.warning[0].value


class TestHandOffs:
    def test_the_datalog_page_opens_on_the_handed_over_run(self, tmp_path):
        folder = tmp_path / "DATALOG"
        folder.mkdir()
        path = write_datalog(folder, DATALOG_SHORT, name="2026-08-06_173312~VBBE00.csv")
        query = "2026-08-06 17:33:12"

        at = AppTest.from_file(str(_PAGES / "4_Datalog.py"), default_timeout=60)
        at.session_state["datalog"] = {
            "folder": str(folder), "focus_run": str(path), "focus_query": query,
        }
        at.session_state["datalog_search"] = query
        at.run()

        assert not at.exception, [e.value for e in at.exception]
        assert any("Opened from the Split Table" in c for c in _captions(at))
        assert not any("Select a run above" in i.value for i in at.info)

    def test_changing_the_search_moves_on(self, tmp_path):
        folder = tmp_path / "DATALOG"
        folder.mkdir()
        path = write_datalog(folder, DATALOG_SHORT, name="2026-08-06_173312~VBBE00.csv")

        at = AppTest.from_file(str(_PAGES / "4_Datalog.py"), default_timeout=60)
        at.session_state["datalog"] = {
            "folder": str(folder), "focus_run": str(path), "focus_query": "2026-08-06 17:33:12",
        }
        at.session_state["datalog_search"] = "2026-08-06"
        at.run()

        assert at.session_state["datalog"]["focus_run"] is None

    def test_the_runcard_page_scans_and_charts_the_handed_over_recipe(self, tmp_path):
        folder = tmp_path / "RUNCARD"
        folder.mkdir()
        card = folder / "VBBE00.csv"
        card.write_text(RUNCARD_FULL, encoding="utf-8", newline="")

        at = AppTest.from_file(str(_PAGES / "5_Runcard.py"), default_timeout=60)
        at.session_state["runcard"] = {
            "folder": str(folder), "selected": (), "runcards": None, "rejected": None,
            "profiles": None, "rows": None, "species": None, "errors": None,
            "figures": None, "focus": str(card),
        }
        at.run()

        assert not at.exception, [e.value for e in at.exception]
        assert any("Opened from the Split Table" in c for c in _captions(at))
        assert at.session_state["runcard"]["selected"] == (str(card),)
