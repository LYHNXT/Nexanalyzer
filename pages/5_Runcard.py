"""
Runcard: a folder of recipe CSVs in, a comparable table and a profile chart out.

Point it at an archive folder and it reads every `.csv` beneath it, keeps the
ones that read as real recipes, and gives:

  1  a table  one row per recipe — when the file was last written, total
               time, peak temperature, growth window, and the setpoint each
               gas, controller, valve and the stage was holding at the growth
               midpoint. Listed newest file first.
  2  a chart  the ticked recipe replayed: the heater's reconstructed
               temperature with its cooldown tail, the preheaters, the growth
               window shaded, gas valves marked where they moved, and a gantt
               of every channel underneath

**The table is the point, and the chart is the follow-up.** Growers compare
recipes far more often than they read one, and the question is nearly always
"what was different about that run" — which is a row-to-row comparison, not a
chart. So every number the chart's summary block carries is also a column, and
the chart is what you open once a row looks wrong.

**A recipe folder holds more than recipes.** The tool writes its datalogs into
the same folders, and a datalog is a `.csv` that parses perfectly well: 6904
rows, the first named `Time`, the rest named after their own timestamps. They
are rejected by the one test that separates them — a real recipe's clock
advances and a datalog's does not — and the count of what was rejected is
shown, because a folder where *everything* was rejected is a folder that was
pointed at the wrong place.

**These are recipe clocks, not wall-clocks.** `Pumping` and `Pumping Forward`
hold for real time and state only a target pressure, so nothing in the file
says how long they took; every timestamp on this page is short by the
pump-down time before it. On the VBBE00 run that is about 14 minutes out of
133. Do not align these against a datalog by absolute seconds.

Long work, once: the folder walk parses every file in the tree and is done on
a folder pick and on an explicit reload, never on a widget interaction. Ticking
a different row redraws from state.
"""

import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core.io.export import export_figure_html, export_figure_png, prompt_save_path
from core.io.folder_picker import prompt_folder_path
from core.io.tool_layout import RUNCARD
from core.ui.tool_select import render_tool_select
from core.viz.render import render_plot
from modules.runcard.io.config_store import load_runcard_folder, save_runcard_folder
from modules.runcard.io.parser import derive_run_id, file_mtime, list_runcards, load_runcard
from modules.runcard.processing.growth_window import (
    RuncardProfile,
    build_profile,
    fixed_channel_value_at_growth_mid,
    get_species,
    species_value_at_growth_mid,
)
from modules.runcard.processing.stats import profile_stats, tube_series_for
from modules.runcard.ui.runcard_state import (
    chartable_selection,
    get_runcard_state,
    reset_figures,
    reset_results,
    runcard_table_key,
)
from modules.runcard.viz.profile import build_profile_figure

#: Gas columns in the order a grower reads them: reactive species first,
#: because those are what distinguishes two runs, and the carriers last,
#: because Ar is open on most lines for most of every run. A species this tuple
#: does not name still gets a column — it is appended, alphabetically, so a new
#: gas appears rather than silently missing.
_SPECIES_ORDER = ("H2Se", "H2S", "H2", "O2", "Ar", "N2")

#: The non-gas channels, as `(column heading, channel id)`. The ids are
#: `fixed_channel_value_at_growth_mid`'s, which raises on anything it does not
#: know — so a typo here fails on the first folder rather than showing an empty
#: column forever.
_FIXED_COLUMNS = (
    ("PC-1", "PC-1"),
    ("PC-2", "PC-2"),
    ("RTV", "RTV"),
    ("Spin (rpm)", "Spin"),
    ("P1 (°C)", "P1"),
    ("P2 (°C)", "P2"),
)

#: How many charts one selection will draw. A chart is ~500 px plus a row per
#: channel, so five of them is a page nobody scrolls to the bottom of, and the
#: comparison they were ticked for is better read off the table anyway.
_MAX_CHARTS = 4

st.title("📋 Runcard")
st.markdown(
    "Every recipe in a folder, replayed onto its own clock: what it ran, how "
    "hot, for how long, and with what flowing."
)

state = get_runcard_state()

# The remembered folder is seeded but deliberately **not** scanned. Opening a
# page must never start a recursive walk of somebody's archive share on its
# own — including when the page is opened by the smoke test that renders every
# page in the app. The walk happens on a pick and on an explicit Scan, and on
# nothing else.
if state["folder"] is None:
    _remembered = load_runcard_folder()
    if _remembered:
        state["folder"] = _remembered


def _summary_row(path: str, profile: RuncardProfile, species: list[str]) -> dict:
    """One table row: the recipe's identity, its clock, and its growth midpoint.

    `None` throughout for anything the recipe did not run — a gas below its
    idle purge flow, a controller at zero, a preheater never addressed — rather
    than a zero, so an empty cell reads as "not used" and a `0` would read as a
    setpoint somebody chose.
    """
    stats = profile_stats(profile)
    has_window = stats["gw_start_min"] is not None
    # The only wall-clock on the page. Every other time column here is minutes
    # into the run, because a recipe's clock starts at zero and the file says
    # nothing about when it was written — so "which of these is the latest" can
    # only be answered by the filesystem. Local time, which is the clock the
    # person asking is reading. `-1.0` is `file_mtime`'s "could not stat", and
    # must stay empty rather than render as 1970.
    mtime = file_mtime(path)
    row = {
        "path": path,
        "Run": derive_run_id(path),
        "Modified": datetime.fromtimestamp(mtime) if mtime >= 0 else None,
        "Total (min)": stats["total_min"],
        "Peak T (°C)": stats["peak_T"],
        "Growth (min)": stats["gw_dur"] if has_window else None,
        "Growth start (min)": stats["gw_start_min"],
    }
    for name in species:
        row[f"{name} (sccm)"] = species_value_at_growth_mid(profile, name)
    for heading, channel_id in _FIXED_COLUMNS:
        row[heading] = fixed_channel_value_at_growth_mid(profile, channel_id)
    row["File"] = os.path.basename(path)
    return row


def _scan_folder(state: dict) -> None:
    """Walk the folder, replay every recipe in it, and fill the derived state.

    Two error tiers, as everywhere: one recipe that will not replay — a
    malformed number in a command this app recognises — is collected and
    reported by name, and the other thirty-nine still list. Only a failure of
    the walk itself loses the folder.

    Equal weight per file, which is the honest weighting here and not the
    flattening `core.report.progress` warns against: every file is one parse
    and one replay of a few dozen commands, so a file really is a file's worth
    of the work.
    """
    folder = state["folder"]
    try:
        with st.status(f"Reading {folder}...", expanded=True) as status:
            progress_bar = st.progress(0.0, text="Finding recipes...")

            paths = list_runcards(folder)
            state["runcards"] = paths
            # Everything under the folder that looked like a candidate and was
            # not one. Counted from the same recursive glob the listing used,
            # so the two cannot disagree about what was there.
            try:
                total_csv = sum(1 for _ in Path(folder).rglob("*.csv"))
            except OSError:
                total_csv = len(paths)
            state["rejected"] = max(total_csv - len(paths), 0)

            profiles = {}
            errors = []
            for done, path in enumerate(paths, start=1):
                progress_bar.progress(
                    done / len(paths),
                    text=f"Replaying {done}/{len(paths)}: {os.path.basename(path)}",
                )
                status.update(label=f"Replaying {done}/{len(paths)} recipes...")
                try:
                    profiles[path] = build_profile(load_runcard(path))
                except Exception as e:
                    errors.append((os.path.basename(path), str(e)))

            # The gas columns this folder needs, decided once across every
            # recipe in it: a per-file column set would give two runs different
            # tables and make them uncomparable, which is the one thing this
            # page is for.
            found = {
                get_species(name)
                for profile in profiles.values()
                for _t, name, _v in profile.timeline.mfc_events
            }
            species = [name for name in _SPECIES_ORDER if name in found]
            species += sorted(found - set(_SPECIES_ORDER))
            state["species"] = species

            state["profiles"] = profiles
            rows = []
            for path in paths:
                if path not in profiles:
                    continue
                try:
                    rows.append(_summary_row(path, profiles[path], species))
                except Exception as e:
                    errors.append((os.path.basename(path), str(e)))
            # Newest first, so the latest recipes are the ones on screen
            # without a click: a folder of fifty is nearly always opened with a
            # question about the last few runs. Clicking any header — `Modified`
            # included — still re-sorts, and the table's row selection is by
            # position in *this* order, so a UI sort does not disturb it.
            # Files with no readable mtime sort last rather than to 1970.
            rows.sort(
                key=lambda r: (r["Modified"] is not None, r["Modified"] or datetime.min),
                reverse=True,
            )
            state["rows"] = rows
            state["errors"] = errors

            progress_bar.progress(1.0, text="Done.")
            status.update(
                label=f"{len(state['rows'])} recipe(s) in {os.path.basename(folder) or folder}.",
                state="complete",
                expanded=False,
            )
    except Exception as e:
        st.error(f"Could not read this folder: {e}")
        state["runcards"] = []
        state["rows"] = []
        state["profiles"] = {}
        state["species"] = []
        state["errors"] = []
        state["rejected"] = 0


def _render_metrics(stats: dict) -> None:
    """The five numbers above one chart.

    Growth window and duration are shown together or not at all: they answer
    the same question, and a duration with no window to place it in is the
    `0.0` sentinel `GrowthWindow.duration_min` returns, which would read as a
    measured zero.
    """
    columns = st.columns(5)
    columns[0].metric("Total time", f"{stats['total_min']:.0f} min")
    columns[1].metric(
        "Peak T",
        f"{stats['peak_T']:.0f}°C" if stats["peak_T"] is not None else "—",
    )
    if stats["gw_start_min"] is not None:
        columns[2].metric(
            "Growth window",
            f"{stats['gw_start_min']:.0f}–{stats['gw_end_min']:.0f} min",
        )
        columns[3].metric("Growth duration", f"{stats['gw_dur']:.0f} min")
    else:
        columns[2].metric("Growth window", "—")
        columns[3].metric("Growth duration", "—")
    columns[4].metric(
        "P1 final",
        f"{stats['p1_T']:.0f}°C" if stats["p1_T"] is not None else "—",
    )


# --------------------------------------------------------------- Folder pick
st.subheader("1. Runcard Folder")
tool, tool_changed = render_tool_select(RUNCARD, state["folder"], key="runcard_tool")
tool_folder = tool.subfolder(RUNCARD) if tool_changed else None
if tool_changed and tool_folder is None:
    st.error(f"{tool.name} declares no runcard folder in tool_layouts.json.")

col_pick, col_scan = st.columns([1, 1])
with col_pick:
    pick_clicked = st.button("Select Runcard Folder", width="stretch")
with col_scan:
    # One button with one label, rather than "Scan" before the first read and
    # "Reload" after: the buttons are drawn before the handlers below run, so a
    # state-dependent label would describe the previous run for one rerun.
    scan_clicked = st.button(
        "Scan / Reload",
        width="stretch",
        disabled=not state["folder"],
        help="Read every CSV under this folder. Parsing is keyed on each file's "
             "modification time, so a recipe edited in place is picked up here.",
    )

if pick_clicked:
    try:
        picked = prompt_folder_path(default_dir=state["folder"] or "", title="Select Runcard Folder")
    except Exception as e:
        st.error(f"Failed to open folder browser: {e}")
        picked = None
else:
    # Choosing a tool is a pick like any other: remembered, and read at once.
    picked = str(tool_folder) if tool_folder is not None else None

if picked:
    state["folder"] = picked
    try:
        save_runcard_folder(picked)
    except OSError as e:
        # A preference that cannot be written is not a reason to lose the
        # pick, and — unguarded — it was not just the preference that was
        # lost: a read-only `data/` (an install under Program Files, a
        # checkout without write access, a full disk) raised PermissionError
        # here and aborted the script *before* `_scan_folder`, so naming a
        # folder appeared to do nothing at all. Same guard as the Datalog
        # page's pick.
        st.warning(f"Could not remember this folder: {e}")
    reset_results(state)
    # Scanned here rather than after an `st.rerun()`: naming a folder in the
    # dialog *is* the request to read it, and a rerun would either lose the
    # status box or need a flag in state to say a scan was owed. Nothing
    # stale is left on screen because everything below this point — the
    # path caption included — is drawn after this handler.
    _scan_folder(state)

if scan_clicked:
    reset_results(state)
    _scan_folder(state)

if state["folder"] and state["runcards"] is None and state.get("focus"):
    # Handed over by the Split Table: the operator asked for one recipe, and
    # making them press Scan before it appears would hide it behind a button.
    _scan_folder(state)

st.caption(state["folder"] or "No folder selected yet.")
if state["folder"] and state["runcards"] is None:
    st.info("Click **Scan / Reload** to read the recipes in this folder.")

# ------------------------------------------------------------------- Recipes
if state["folder"] and state["rows"] is not None:
    st.subheader("2. Recipes")

    rows = state["rows"]
    rejected = state.get("rejected") or 0
    if not rows:
        # Two different absences, said differently: a folder full of rejected
        # CSVs was pointed at the wrong place (a datalog archive, most likely),
        # while a folder with no CSVs at all is a path problem. They are fixed
        # in different places.
        if rejected:
            st.warning(
                f"None of the {rejected} CSV file(s) here read as a recipe. Every "
                "one of them parsed but never advanced its clock, which is what a "
                "datalog export does — check that this is the runcard folder and "
                "not the datalog folder."
            )
        else:
            st.warning("No CSV files under this folder.")
    else:
        st.markdown(
            f"**Found:** {len(rows)} recipe(s)"
            + (f" · {rejected} other CSV file(s) skipped" if rejected else "")
        )
        if rejected:
            st.caption(
                "↳ Skipped files parsed but their clock never advanced — the test "
                "that separates a recipe from a datalog export sharing the folder."
            )
        for name, message in state.get("errors") or []:
            st.warning(f"Could not replay {name}: {message}")

        st.caption(
            "Every setpoint column is the value holding at the **growth "
            "midpoint** — the quietest point in the window, since both its edges "
            "are moments when something changed. A blank cell means the channel "
            "was not running, not that it was zero. **RTV** is the throttle "
            "valve's second parameter, carried through as the reactor writes it; "
            "it is not the achieved tube pressure and is deliberately unitless."
        )

        display = pd.DataFrame([{k: v for k, v in row.items() if k != "path"} for row in rows])
        event = st.dataframe(
            display,
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            # Keyed by folder, so switching folders is a new widget with
            # nothing ticked; see `runcard_table_key` for why clearing
            # `state["selected"]` instead does not work.
            key=runcard_table_key(state["folder"]),
            column_config={
                "Modified": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
                "Total (min)": st.column_config.NumberColumn(format="%.0f"),
                "Peak T (°C)": st.column_config.NumberColumn(format="%.0f"),
                "Growth (min)": st.column_config.NumberColumn(format="%.1f"),
                "Growth start (min)": st.column_config.NumberColumn(format="%.0f"),
            },
        )

        picked_rows = list(event.selection.rows) if event is not None else []
        selected = tuple(rows[i]["path"] for i in picked_rows if i < len(rows))
        focus = state.get("focus")
        if selected:
            state["focus"] = None
        elif focus:
            target = os.path.normcase(os.path.normpath(focus))
            selected = tuple(
                row["path"] for row in rows
                if os.path.normcase(os.path.normpath(row["path"])) == target
            )
            if selected:
                st.caption(f"Opened from the Split Table: `{os.path.basename(focus)}`. "
                           "Tick a row to chart something else.")
        if selected != tuple(state["selected"] or ()):
            # Only the figures are invalidated. Re-ticking must not re-walk the
            # folder: the walk parsed every file in the tree, the figures are
            # arithmetic over a few dozen events.
            state["selected"] = selected
            reset_figures(state)

        st.caption(
            f"Tick a row to chart it. Up to {_MAX_CHARTS} are drawn at once; "
            "past that, the comparison is what the table above is for."
        )

# -------------------------------------------------------------------- Charts
# Asked before the heading is written, not inside the loop: a ticked path with
# no profile behind it draws nothing, and every one of them having no profile
# used to leave "3. Profile" standing over an empty section.
chartable = chartable_selection(state)

if chartable:
    st.markdown("---")
    st.subheader("3. Profile")

    figures = dict(state.get("figures") or {})
    profiles = state["profiles"]
    drawn = []

    for index, path in enumerate(chartable[:_MAX_CHARTS]):
        profile = profiles[path]
        run_id = derive_run_id(path)
        st.markdown(f"#### {run_id}")
        _render_metrics(profile_stats(profile))
        if tube_series_for(run_id) is None:
            # The reference prints the same note. Without it a run id that
            # names no series is drawn with HA's inner/outer plumbing and
            # nothing on screen says the tube rows are a guess.
            st.caption(
                f"Tube rows assume HA plumbing (MFC 1+3+4 inner / 6+7+8 outer): "
                f"'{run_id}' names no known reactor series."
            )
        try:
            if path not in figures:
                figures[path] = build_profile_figure(run_id, profile)
            # Widget keys are namespaced by the row's own identity, not by
            # position alone: two folders can hold the same run id, and two
            # charts sharing a key is not an error Streamlit reports.
            render_plot(figures[path], key=f"runcard_profile_{index}_{run_id}")
            drawn.append((run_id, path))
        except Exception as e:
            st.warning(f"Could not draw {run_id}: {e}")

    state["figures"] = figures

    if len(chartable) > _MAX_CHARTS:
        st.caption(
            f"{len(chartable) - _MAX_CHARTS} more recipe(s) are ticked "
            f"than are drawn. The table carries their numbers."
        )

    # ---------------------------------------------------------------- Save
    if drawn:
        st.subheader("Save")
        st.caption(
            f"One dialog, one name, two files per chart — {len(drawn)} PNG for a "
            f"report and {len(drawn)} HTML that keeps the hover this chart was "
            "built around, where every bar carries its setpoint, its unit and "
            "its span whether or not it is wide enough to print them."
        )
        if st.button("💾 Save Chart(s) As...", width="stretch"):
            try:
                save_path = prompt_save_path(
                    default_dir=state["folder"],
                    default_filename=f"{drawn[0][0]}_profile.png",
                    title="Save Runcard Profile",
                    filetypes=(("PNG images", "*.png"), ("All files", "*.*")),
                    default_extension=".png",
                )
                if save_path:
                    # The chosen name supplies the stem and each run appends its
                    # own id, so a multi-selection cannot overwrite itself. The
                    # extension the dialog collected is dropped: it named only
                    # the first image, and half the files are not PNGs.
                    base, _ext = os.path.splitext(save_path)
                    saved = []
                    seen = {}
                    for run_id, path in drawn:
                        # derive_run_id maps `0810_NX123_runcard.csv` and
                        # `NX123.csv` to one id on purpose; the second chart
                        # must not overwrite the first's files.
                        seen[run_id] = seen.get(run_id, 0) + 1
                        stem = run_id if seen[run_id] == 1 else f"{run_id}_{seen[run_id]}"
                        figure = figures[path]
                        height = int(profile_stats(profiles[path])["canvas_h"])
                        png_target = f"{base}_{stem}.png"
                        with open(png_target, "wb") as f:
                            f.write(export_figure_png(figure, width=1200, height=height, scale=2.0))
                        saved.append(png_target)

                        html_target = f"{base}_{stem}.html"
                        with open(html_target, "w", encoding="utf-8") as f:
                            f.write(export_figure_html(figure))
                        saved.append(html_target)

                    st.success("Saved:\n" + "\n".join(f"- {p}" for p in saved))
                # save_path is None => user cancelled => no-op
            except Exception as e:
                st.error(f"Failed to save: {e}")
