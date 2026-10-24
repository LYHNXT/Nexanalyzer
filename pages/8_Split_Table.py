"""
Split Table: a tool's run-by-run workbook in, filtered views, trends and
correlations out.

Each tool folder's ``SPLITTABLE/<TOOL>_SplitTable_from_runcards.xlsx`` holds
one row per run -- recipe parameters from the runcard, run time from the
datalog, OM / Raman / PL results from the QC report -- built by the scripts
beside it. This page reads that workbook and never writes it: the scripts own
it, keep a ``.bak`` chain of it, and carry the naming overrides that decide
what a row is called. A page that edited it would be a second author nobody
reruns. Views are saved elsewhere, as CSV or XLSX.

Where the workbook is comes from ``data/tool_layouts.json`` (each tool's
``splittable.file``), so HA1P01 and VB1P01 are picked from the same Tool
dropdown the other pages use.

**A row opens its run.** Pick one in the table and the Datalog, Runcard and
QC Report pages open on that run's files, found by
`modules.splittable.processing.links`; a file the row cannot be traced to is
*not linked*, as on the OneNote page, not missing.
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core.io import tool_layout
from core.io.export import prompt_save_path
from core.io.tool_roots import load_last_tool, save_last_tool
from core.ui.tool_select import render_tool_select
from core.viz.render import render_plot
from modules.datalog.io.scanner import get_run_metadata
from modules.datalog.ui.datalog_state import focus_run, get_datalog_state
from modules.optical.ui.qc_report_state import get_qc_report_state
from modules.optical.ui.qc_report_state import select_folder as select_qc_folder
from modules.runcard.ui.runcard_state import focus_runcard, get_runcard_state
from modules.spectra.processing.sample_scanner import (
    default_magnification,
    scan_sample_folder,
)
from modules.splittable.io.workbook import SplitTable, read_split_table
from modules.splittable.processing import links
from modules.splittable.processing.table import (
    distinct,
    filter_rows,
    numeric_columns,
    series_of,
)
from modules.splittable.viz.charts import (
    X_PROCESS_START,
    X_RUN_ORDER,
    build_scatter_figure,
    build_trend_figure,
)

#: The page's own state: which tool it is on. Everything else is widgets.
_STATE_KEY = "splittable"

#: The 1-based workbook position, shown first so a row can be found in Excel.
RUN_COLUMN = "Run #"

#: What the charts open on, where the table has it.
_DEFAULT_TREND = ("OM_Reference_pct",)
_DEFAULT_SCATTER = ("T_growth_C", "OM_Reference_pct")


@st.cache_data(show_spinner="Reading split table...")
def _load(path: str, mtime: float) -> SplitTable:
    """`mtime` is a cache key and is never read: a rebuilt workbook rereads."""
    return read_split_table(path)


def _same_path(a, b) -> bool:
    return Path(a).resolve() == Path(b).resolve()


def _is_under(path: Path, folder) -> bool:
    return folder is not None and Path(folder) in path.parents


# --------------------------------------------------------------------- Links
def _render_links(tool: tool_layout.ToolLayout, row: pd.Series) -> None:
    """Three buttons, one per page, each opening on this row's file."""
    found = links.resolve_row(tool, row)
    wafer = row.get("WaferID")
    st.markdown(f"**Open `{wafer}`**")
    col_dl, col_rc, col_qc = st.columns(3)

    with col_dl:
        datalog = found.datalog
        folder = tool.subfolder(tool_layout.DATALOG)
        if datalog is None:
            st.caption("Datalog: not linked.")
        elif not _is_under(datalog, folder):
            st.caption(f"Datalog `{datalog.name}` is outside {tool.name}'s DATALOG folder.")
        else:
            st.caption(f"Datalog: `{datalog.parent.name}/{datalog.name}`")
            if st.button("Open in Datalog →", key="splittable_goto_datalog", width="stretch"):
                meta = get_run_metadata(str(datalog))
                if meta is None:
                    st.error(f"`{datalog.name}` does not read as a datalog.")
                else:
                    query = meta.start_time.strftime("%Y-%m-%d %H:%M:%S")
                    focus_run(get_datalog_state(), str(folder), str(datalog), query)
                    st.switch_page("pages/4_Datalog.py")

    with col_rc:
        runcard = found.runcard
        if runcard is None:
            st.caption("Runcard: not linked.")
        else:
            st.caption(f"Runcard: `{runcard.name}`")
            if st.button("Open in Runcard →", key="splittable_goto_runcard", width="stretch"):
                # The declared subfolder when the card is in it, so the table
                # lists the card's neighbours; VB's RUNCARD/JK cards are not.
                declared = tool.subfolder(tool_layout.RUNCARD)
                folder = declared if _is_under(runcard, declared) else runcard.parent
                focus_runcard(get_runcard_state(), str(folder), str(runcard))
                st.switch_page("pages/5_Runcard.py")

    with col_qc:
        optical = found.optical
        if optical is None:
            st.caption("QC Report: no optical folder linked.")
        elif not optical.is_dir():
            # The pre-202609 months are flat `.wip` project files.
            st.caption(f"`{optical.name}` is an archived project file.")
        else:
            st.caption(f"Optical: `{optical.parent.name}/{optical.name}`")
            if st.button("Open in QC Report →", key="splittable_goto_qc", width="stretch"):
                qc_state = get_qc_report_state()
                if qc_state.get("folder") != str(optical):
                    scan = scan_sample_folder(str(optical))
                    select_qc_folder(qc_state, str(optical), scan, default_magnification(scan))
                st.switch_page("pages/2_QC_Report.py")


# -------------------------------------------------------------------- Export
def _render_save(view: pd.DataFrame, tool: tool_layout.ToolLayout, workbook: Path) -> None:
    if not st.button("💾 Save View As...", key="splittable_save"):
        return
    try:
        target = prompt_save_path(
            default_dir=None,
            default_filename=f"{tool.name}_SplitTable_view_{datetime.now():%Y%m%d}.xlsx",
            title="Save Split Table View",
            filetypes=(("Excel workbook", "*.xlsx"), ("CSV files", "*.csv"), ("All files", "*.*")),
            default_extension=".xlsx",
        )
    except Exception as e:
        st.error(f"Failed to open save dialog: {e}")
        return
    if not target:
        return
    if Path(target).exists() and _same_path(target, workbook):
        # The one file this page must never write.
        st.error("That is the split table itself, which the SPLITTABLE scripts own. "
                 "Pick another name.")
        return
    try:
        if target.lower().endswith(".csv"):
            view.to_csv(target, index=False, encoding="utf-8-sig")
        else:
            view.to_excel(target, index=False, sheet_name="SplitTable_view")
    except OSError as e:
        st.error(f"Could not save: {e}")
        return
    st.success(f"Saved {len(view)} row(s) to `{target}`.")


# ---------------------------------------------------------------------- Page
st.title("🧾 Split Table")
st.markdown(
    "Every run on a tool, one row each: recipe, run time and QC results from the "
    "tool's split-table workbook. Read-only; the SPLITTABLE scripts build it."
)

state = st.session_state.setdefault(_STATE_KEY, {"tool_folder": None})

st.subheader("1. Tool")
tool, tool_changed = render_tool_select(
    None, state.get("tool_folder"), key="splittable_tool", fallback=load_last_tool()
)
if tool is not None and (tool_changed or not state.get("tool_folder")):
    if tool_changed:
        try:
            save_last_tool(tool.name)
        except OSError:
            pass
    state["tool_folder"] = str(tool.root)
    st.rerun()

if tool is None:
    st.info("Pick a tool to read its split table.")
    st.stop()

workbook = tool.splittable_file
if workbook is None:
    st.warning(
        f"{tool.name} declares no split table. Add "
        '`"splittable": {"file": "SPLITTABLE/<name>.xlsx"}` to its entry in '
        f"`{tool_layout.get_layouts_path().name}`."
    )
    st.stop()
if not workbook.is_file():
    st.error(f"No split table at `{workbook}`.")
    st.stop()

try:
    mtime = workbook.stat().st_mtime
    table = _load(str(workbook), mtime)
except Exception as e:
    # Excel holding the file open, OneDrive mid-sync: say which, don't crash.
    st.error(f"Could not read `{workbook.name}`: {e}")
    st.stop()

data = table.data
st.caption(
    f"`{workbook}` · sheet `{table.sheet}` · {len(data)} rows × {len(data.columns)} columns · "
    f"saved {datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}"
)

# ------------------------------------------------------------------- Filters
st.subheader("2. Rows")
col_month, col_type, col_series, col_search = st.columns(4)
key = f"splittable_{tool.name}"
with col_month:
    months = st.multiselect("Month", distinct(data["Month"]) if "Month" in data else [],
                            key=f"{key}_month", placeholder="All months")
with col_type:
    run_types = st.multiselect("Run type", distinct(data["Run_Type"]) if "Run_Type" in data else [],
                               key=f"{key}_run_type", placeholder="All run types")
with col_series:
    series = st.multiselect("Series", distinct(series_of(data)),
                            key=f"{key}_series", placeholder="All series")
with col_search:
    query = st.text_input("Wafer", key=f"{key}_search",
                          placeholder="e.g. HADI5, VBCA1")

rows = filter_rows(data, months, run_types, series, query)

groups = list(table.groups)
shown_groups = st.multiselect("Column groups", groups, default=groups, key=f"{key}_groups",
                              help="From the workbook's Column_Design sheet.")
columns = [c for g in shown_groups for c in table.groups[g]]
view = rows[columns].copy()
view.insert(0, RUN_COLUMN, rows.index + 1)

st.caption(f"{len(view)} of {len(data)} row(s). Select a row to open its run.")
# Keyed by the row set as well as the tool: the selection is a row *position*,
# and a position from one filter means a different run under another.
event = st.dataframe(
    view, hide_index=True, width="stretch", height=420,
    on_select="rerun", selection_mode="single-row",
    key=f"{key}_table_{hash(tuple(rows.index))}_{mtime}",
    column_config={
        c: st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")
        for c in view.columns if pd.api.types.is_datetime64_any_dtype(view[c])
    },
)
picked = event.selection.rows if event and event.selection else []
if picked and picked[0] < len(rows):
    _render_links(tool, rows.iloc[picked[0]])

_render_save(view, tool, workbook)

# -------------------------------------------------------------------- Charts
st.subheader("3. Charts")
numeric = numeric_columns(rows)
if not numeric:
    st.info("No numeric column has a value in these rows.")
    st.stop()

tab_trend, tab_scatter = st.tabs(["Trend", "X vs Y"])

with tab_trend:
    trend_cols = st.multiselect(
        "Columns", numeric,
        default=[c for c in _DEFAULT_TREND if c in numeric] or numeric[:1],
        key=f"{key}_trend_cols",
    )
    col_x, col_mark = st.columns([2, 1])
    with col_x:
        x_mode = st.radio("X axis", [X_RUN_ORDER, X_PROCESS_START], horizontal=True,
                          key=f"{key}_trend_x")
    with col_mark:
        mark = st.checkbox("Mark non-growth runs", value=True, key=f"{key}_trend_mark")
    if trend_cols:
        render_plot(build_trend_figure(rows, trend_cols, x_mode, mark), key=f"{key}_trend")
        st.caption(
            "Points are growth runs; lines are cleanings, anneals and other non-growth "
            "runs (dotted if stopped). A hollow marker on an OM column is a run whose "
            "cut sat in the frame noise. Error bars are the table's frame-to-frame std."
        )

with tab_scatter:
    x_default, y_default = (
        c if c in numeric else numeric[min(i, len(numeric) - 1)]
        for i, c in enumerate(_DEFAULT_SCATTER)
    )
    col_sx, col_sy = st.columns(2)
    with col_sx:
        x_col = st.selectbox("X", numeric, index=numeric.index(x_default), key=f"{key}_scatter_x")
    with col_sy:
        y_col = st.selectbox("Y", numeric, index=numeric.index(y_default), key=f"{key}_scatter_y")
    render_plot(build_scatter_figure(rows, x_col, y_col), key=f"{key}_scatter")
