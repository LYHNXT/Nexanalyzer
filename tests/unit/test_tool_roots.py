"""data/tool_roots.json, and how it combines with the layout table."""

import json
from pathlib import Path

from core.io import tool_roots as tr
from core.io.tool_layout import (
    DATALOG,
    OPTICAL,
    RUNCARD,
    configured_tools,
    load_layouts,
    save_layouts,
    tool_owning,
)

LAYOUTS = {
    "HA1P01": {
        "datalog": {"subfolder": "DATALOG"},
        "runcard": {"subfolder": "RUNCARD/lcy/NSHA1P01"},
        "optical": {"subfolder": "OPTICALS"},
    },
    "VB1P01": {"datalog": {"subfolder": "DATALOG"}},
}


def test_missing_file_reads_empty(tmp_path):
    path = tmp_path / "tool_roots.json"
    assert tr.load_roots(path) == {}
    assert tr.load_last_tool(path) is None


def test_set_and_clear_root_round_trip(tmp_path):
    path = tmp_path / "tool_roots.json"
    tr.set_root("VB1P01", "D:/x/VB1P01", path)
    tr.set_root("HA1P01", "D:/x/HA1P01", path)
    assert tr.load_roots(path) == {"HA1P01": "D:/x/HA1P01", "VB1P01": "D:/x/VB1P01"}
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1

    tr.set_root("HA1P01", None, path)
    assert tr.load_roots(path) == {"VB1P01": "D:/x/VB1P01"}


def test_last_tool_survives_root_edits(tmp_path):
    path = tmp_path / "tool_roots.json"
    tr.save_last_tool("HA1P01", path)
    tr.set_root("HA1P01", "D:/x/HA1P01", path)
    assert tr.load_last_tool(path) == "HA1P01"
    assert tr.load_roots(path) == {"HA1P01": "D:/x/HA1P01"}


def test_bad_file_and_unknown_schema_read_empty(tmp_path):
    path = tmp_path / "tool_roots.json"
    path.write_bytes(b"\xff\xfe not json")
    assert tr.load_roots(path) == {}
    path.write_text(json.dumps({"schema_version": 99, "roots": {"A": "B"}}), encoding="utf-8")
    assert tr.load_roots(path) == {}


def test_configured_tools_needs_both_layout_and_root(tmp_path):
    tools = configured_tools(LAYOUTS, {"HA1P01": str(tmp_path / "HA1P01"), "XX": "D:/x"})
    assert [t.name for t in tools] == ["HA1P01"]
    assert tools[0].subfolder(RUNCARD) == tmp_path / "HA1P01" / "RUNCARD/lcy/NSHA1P01"


def test_tool_owning_reads_the_tool_back_from_a_folder(tmp_path):
    tools = configured_tools(LAYOUTS, {"HA1P01": str(tmp_path / "HA1P01"),
                                       "VB1P01": str(tmp_path / "VB1P01")})
    ha = tmp_path / "HA1P01"
    assert tool_owning(ha / "DATALOG", DATALOG, tools).name == "HA1P01"
    # A wafer folder under the optical tree belongs to the tool too.
    assert tool_owning(ha / "OPTICALS" / "202609" / "HADH51", OPTICAL, tools).name == "HA1P01"
    # Slashes are not what decides it.
    assert tool_owning(str(ha / "DATALOG").replace("\\", "/"), DATALOG, tools).name == "HA1P01"
    # A folder browsed to by hand belongs to no tool.
    assert tool_owning(tmp_path / "elsewhere", DATALOG, tools) is None
    # Role None matches the tool folder itself, for the OneNote page.
    assert tool_owning(ha, None, tools).name == "HA1P01"
    assert tool_owning(None, DATALOG, tools) is None


def test_save_layouts_round_trips(tmp_path):
    path = tmp_path / "tool_layouts.json"
    save_layouts(LAYOUTS, path)
    assert load_layouts(path) == LAYOUTS


def test_committed_layouts_declare_every_role_for_ha_and_vb():
    layouts = load_layouts()
    for name in ("HA1P01", "VB1P01"):
        for role in (DATALOG, RUNCARD, OPTICAL):
            assert (layouts[name].get(role) or {}).get("subfolder"), (name, role)
    assert Path(layouts["VB1P01"]["runcard"]["subfolder"]).as_posix() == "RUNCARD/LYH"
