"""Resolving a tool folder, and finding one wafer's three artifacts in it.

Built on a synthetic tree rather than the share: these assert the *rules*, and
a test that reads the real folder would change its verdict every time someone
runs a wafer.
"""

import json

import pytest

from core.io import tool_layout as tl


@pytest.fixture
def tool_root(tmp_path):
    """A miniature HA1P01: both optical eras, a runcard tree, datalogs."""
    root = tmp_path / "HA1P01"

    # 202609 era — a folder per wafer.
    for wafer in ("HADH51", "HADH52"):
        (root / "OPTICALS" / "202609" / wafer).mkdir(parents=True)

    # Earlier months — flat .wip files, and a month that does not exist
    # between them, because the month tier is not contiguous.
    (root / "OPTICALS" / "202608").mkdir(parents=True)
    (root / "OPTICALS" / "202608" / "HADG30.wip").write_text("x", encoding="utf-8")
    (root / "OPTICALS" / "202606").mkdir(parents=True)
    (root / "OPTICALS" / "202606" / "HACB18.wip").write_text("x", encoding="utf-8")

    # BACKUP holds a parallel tree — the same wafer, a second time.
    (root / "BACKUP" / "OPTICALS" / "202609" / "HADH51").mkdir(parents=True)

    runcards = root / "RUNCARD" / "lcy" / "NSHA1P01" / "202609"
    runcards.mkdir(parents=True)
    (runcards / "HADH51.csv").write_text("x", encoding="utf-8")

    # A different operator and a different tool under the same RUNCARD root —
    # the cross-tool contamination the declared path exists to exclude.
    other = root / "RUNCARD" / "ymc" / "NSHA1N01" / "202609"
    other.mkdir(parents=True)
    (other / "HADH52.csv").write_text("x", encoding="utf-8")

    datalogs = root / "DATALOG" / "2026_09"
    datalogs.mkdir(parents=True)
    (datalogs / "2026-09-01_172312~HADH51.csv").write_text("x", encoding="utf-8")
    # Untagged: the wafer ID was never written into the name.
    (datalogs / "09152912.csv").write_text("x", encoding="utf-8")

    (root / "DATALOG" / "runcard_tags.json").write_text(
        json.dumps({"2026_09/09152912.csv": "HADH52"}), encoding="utf-8"
    )

    return root


@pytest.fixture
def layouts():
    """The committed table, so the tests exercise what actually ships."""
    return tl.load_layouts()


# --- the table -----------------------------------------------------------

def test_the_committed_table_holds_ha1p01():
    layouts = tl.load_layouts()

    assert "HA1P01" in layouts


def test_an_unknown_tool_raises_rather_than_resolving_to_nothing(tmp_path, layouts):
    """A tool nobody has declared must say so.

    Eight of the twelve tool folders on the share key their files on a run
    serial rather than a wafer ID, so a wafer lookup in them finds nothing.
    Returning an empty result would be indistinguishable from a tool with no
    data in it.
    """
    with pytest.raises(tl.UnknownToolError) as excinfo:
        tl.resolve_tool(tmp_path / "DD1P01", layouts=layouts)

    message = str(excinfo.value)
    assert "DD1P01" in message
    assert "tool_layouts.json" in message  # names the fix


def test_a_malformed_table_reads_as_empty(tmp_path):
    broken = tmp_path / "tool_layouts.json"
    broken.write_text('{"layouts": {"HA1P01": {},}}', encoding="utf-8")

    assert tl.load_layouts(broken) == {}


# --- subfolders ----------------------------------------------------------

def test_each_role_resolves_to_the_folder_its_loader_expects(tool_root, layouts):
    """The runcard and datalog loaders need a start folder, not a rewrite.

    Both already recurse and both already gate on content, so pointing them
    one level deeper is the whole change.
    """
    layout = tl.resolve_tool(tool_root, layouts=layouts)

    assert layout.subfolder(tl.OPTICAL) == tool_root / "OPTICALS"
    assert layout.subfolder(tl.RUNCARD) == tool_root / "RUNCARD" / "lcy" / "NSHA1P01"
    assert layout.subfolder(tl.DATALOG) == tool_root / "DATALOG"


def test_the_runcard_subfolder_excludes_the_other_tool(tool_root, layouts):
    """`RUNCARD/ymc/NSHA1N01` is a different tool's cards under this root."""
    layout = tl.resolve_tool(tool_root, layouts=layouts)

    assert "ymc" not in str(layout.subfolder(tl.RUNCARD))
    assert "NSHA1N01" not in str(layout.subfolder(tl.RUNCARD))


# --- finding one wafer ---------------------------------------------------

def test_a_recent_wafer_links_all_three(tool_root, layouts):
    layout = tl.resolve_tool(tool_root, layouts=layouts)

    paths = tl.find_wafer(layout, "HADH51")

    assert paths.optical == tool_root / "OPTICALS" / "202609" / "HADH51"
    assert paths.runcard.name == "HADH51.csv"
    assert paths.datalog.name == "2026-09-01_172312~HADH51.csv"
    assert paths.unlinked_roles == ()


def test_an_older_wafer_is_found_by_the_wip_pattern(tool_root, layouts):
    """The naming changed mid-2026, so the optical side needs two rules."""
    layout = tl.resolve_tool(tool_root, layouts=layouts)

    paths = tl.find_wafer(layout, "HADG30")

    assert paths.optical.name == "HADG30.wip"


def test_the_month_tier_is_globbed_not_computed(tool_root, layouts):
    """202511 and 202611 do not exist — months are not contiguous.

    Deriving a month from a wafer ID would miss whenever the gap moved.
    """
    layout = tl.resolve_tool(tool_root, layouts=layouts)

    assert tl.find_wafer(layout, "HACB18").optical is not None


def test_the_tag_index_finds_an_untagged_datalog(tool_root, layouts):
    """1186 of 1404 datalogs carry no `~WAFER`; the index rescues a few.

    Most of its keys merely restate the tilde already in the filename, but
    the ones that do not are the only route to those wafers' logs.
    """
    layout = tl.resolve_tool(tool_root, layouts=layouts)

    paths = tl.find_wafer(layout, "HADH52")

    assert paths.datalog is not None
    assert paths.datalog.name == "09152912.csv"


def test_backup_is_never_offered(tool_root, layouts):
    """The parallel tree would show every wafer twice, indistinguishably."""
    layout = tl.resolve_tool(tool_root, layouts=layouts)

    paths = tl.find_wafer(layout, "HADH51")

    assert "BACKUP" not in str(paths.optical)
    assert "BACKUP" not in " ".join(tl.list_wafers(layout))


def test_a_wafer_with_no_runcard_reports_unlinked_not_missing(tool_root, layouts):
    """19 real wafers have optical data and no card under any glob.

    The distinction matters most for the datalog, where 58% of wafers are in
    this state because the logger never wrote the ID — telling an operator
    the file is *missing* sends them hunting for something that is there.
    """
    layout = tl.resolve_tool(tool_root, layouts=layouts)

    paths = tl.find_wafer(layout, "HACB18")

    assert paths.optical is not None
    assert not paths.linked(tl.RUNCARD)
    assert tl.RUNCARD in paths.unlinked_roles


def test_an_unknown_wafer_links_nothing_without_raising(tool_root, layouts):
    layout = tl.resolve_tool(tool_root, layouts=layouts)

    paths = tl.find_wafer(layout, "ZZZZ99")

    assert paths.unlinked_roles == tl.ROLES


def test_an_empty_wafer_id_is_not_a_search(tool_root, layouts):
    layout = tl.resolve_tool(tool_root, layouts=layouts)

    assert tl.find_wafer(layout, "  ").unlinked_roles == tl.ROLES


# --- listing -------------------------------------------------------------

def test_list_wafers_spans_both_optical_eras(tool_root, layouts):
    layout = tl.resolve_tool(tool_root, layouts=layouts)

    wafers = tl.list_wafers(layout)

    assert set(wafers) == {"HADH51", "HADH52", "HADG30", "HACB18"}


def test_list_wafers_does_not_choke_on_the_wildcard_wafer(tool_root, layouts):
    """`{wafer}*.wip` with a `*` wafer collapses to `**`, which pathlib rejects."""
    layout = tl.resolve_tool(tool_root, layouts=layouts)

    assert tl.list_wafers(layout)  # would raise ValueError unmitigated


# --- picking somewhere under the tool ------------------------------------

def test_a_wafer_folder_resolves_to_its_tool(tool_root, layouts):
    """The folder dialog reopens where it was last used.

    Picking `OPTICALS/202609/HADH96` is the easy slip, and refusing it left
    the operator standing inside HA1P01 while being told HA1P01 was unknown.
    """
    picked = tool_root / "OPTICALS" / "202609" / "HADH51"

    layout = tl.resolve_tool(picked, layouts=layouts)

    assert layout.name == "HA1P01"
    assert layout.root == tool_root


def test_any_depth_under_the_tool_resolves(tool_root, layouts):
    for picked in (
        tool_root / "OPTICALS",
        tool_root / "RUNCARD" / "lcy" / "NSHA1P01" / "202609",
        tool_root / "DATALOG" / "2026_09",
    ):
        assert tl.resolve_tool(picked, layouts=layouts).root == tool_root


def test_a_folder_with_no_known_ancestor_still_raises(tmp_path, layouts):
    """The walk-up must not turn a genuinely undeclared tool into a pass."""
    with pytest.raises(tl.UnknownToolError):
        tl.resolve_tool(tmp_path / "DD1P01" / "Optical Properties", layouts=layouts)


def test_a_file_vanishing_mid_scan_does_not_raise(tool_root, layouts, monkeypatch):
    """OneDrive can remove or lock a file between the glob and the stat."""
    layout = tl.resolve_tool(tool_root, layouts=layouts)
    real_stat = type(tool_root).stat

    def flaky_stat(self, *args, **kwargs):
        if self.name == "HADH51.csv":
            raise PermissionError("locked")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(type(tool_root), "stat", flaky_stat)

    assert tl.find_wafer(layout, "HADH51").runcard.name == "HADH51.csv"
