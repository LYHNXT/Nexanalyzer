"""The Tools page, and the Tool dropdown it feeds on the Datalog page."""

from streamlit.testing.v1 import AppTest

import core.io.folder_picker as folder_picker
from core.io import tool_roots
from core.paths import PROJECT_ROOT


def test_set_folder_records_the_root(tmp_path, monkeypatch):
    root = tmp_path / "HA1P01"
    (root / "DATALOG").mkdir(parents=True)
    monkeypatch.setattr(folder_picker, "prompt_folder_path", lambda **kwargs: str(root))

    at = AppTest.from_file(str(PROJECT_ROOT / "pages" / "7_Tools.py"), default_timeout=60).run()
    next(b for b in at.button if b.key == "tools_set_HA1P01").click().run()

    assert not at.exception
    assert tool_roots.load_roots() == {"HA1P01": str(root)}


def test_datalog_page_switches_folder_on_tool_choice(tmp_path):
    root = tmp_path / "HA1P01"
    (root / "DATALOG").mkdir(parents=True)
    tool_roots.set_root("HA1P01", str(root))

    at = AppTest.from_file(str(PROJECT_ROOT / "pages" / "4_Datalog.py"), default_timeout=60).run()
    tool_box = next(s for s in at.selectbox if s.label == "Tool")
    assert tool_box.value == "—"
    tool_box.select("HA1P01").run()

    assert not at.exception
    assert at.session_state["datalog"]["folder"] == str(root / "DATALOG")
    assert next(s for s in at.selectbox if s.label == "Tool").value == "HA1P01"
