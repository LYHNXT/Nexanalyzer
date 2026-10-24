"""
The Spectra page's fit workspace: everything an operator touches to load, fit,
inspect and export a spectrum, laid out in the page itself.

Until v5.43.0 all of this lived in the sidebar (`sidebar.py`, since v2.10.0),
with the plot and its results table alone in the main column. The sidebar is
narrow and scrolls on its own, so the controls an operator uses together --
the file, the preset, Run -- were stacked one under another, the exports sat
above the file list, and View Options was the last thing in it. v5.43.0 moves
them into the page in the order they are used, top to bottom:

- **Header** (`render_exports`): Quick Export of the current file (PNG, HTML,
  CSV) and Batch Export of every fitted file (Master CSV, PNGs).
- **Run setup** (`render_run_setup`): the file switcher (previous / dropdown /
  next, Browse, Remove File, Delete All, and a row of loaded files with their
  fit status), then material preset, technique, Run Auto-Workflow, Run All
  Files and Reset to Raw.
- **Fitted spectrum** (`render_spectrum`): the baseline view, the layer
  toggles (View Options), and the Plotly figure from `create_unified_figure`.
- **Fit results** (`render_fit_results`) and the fit summary
  (`render_fit_summary`).

What did not move is the order Streamlit requires. The layer toggles are
widgets keyed `show_*`, and a run, a batch, a file switch or "Follow the data
again" all write those keys. Streamlit refuses a write to a widget's key after
the widget has rendered in the same run, so the writers must run first:
`sync_pending_file_switch()` / `sync_pending_view_options()` at the top of the
page, and the Run buttons in the run-setup block, which renders above the
toggles. The exports read the toggles to match the picture on screen, so they
render last, into a header slot reserved at the top of the page.
"""

import os

import pandas as pd
import streamlit as st

from ..models.spectrum import SpectrumFile, ProcessingSettings
from ..processing.parser import parse_spectrum_multi, detect_mode_from_filename
from core.io.native_dialog import run_dialog
from core.io.export import (
    export_figure_png,
    export_figure_html,
    create_filename,
    prompt_save_path,
    prompt_save_folder,
)
from ..io.results_csv import export_fit_params_csv, export_master_csv
from ..io.preset_store import load_presets
from ..utils.preset_staleness import technique_fingerprint
from ..viz.fit_plot import plot_composite
from ..viz.live_plot import background_offset, create_empty_figure, create_unified_figure
from .session_state import (
    add_spectrum_file,
    remove_spectrum_file,
    clear_all_files,
    set_mode,
    get_current_spectrum,
    set_view_options,
    clear_pinned_view_options,
    pin_view_option,
    pinned_view_options,
)


@st.cache_data(max_entries=16, show_spinner=False)
def _export_png_cached(fig, width: int, height: int, scale: float) -> bytes:
    """`export_figure_png`, memoized on the figure itself.

    st.download_button needs its bytes at render time, so this rasterization
    used to run on EVERY rerun of the Spectra page — measured at 1316 ms, paid
    for every checkbox toggle and file switch, whether or not anyone ever
    clicked Download.

    `fig` is deliberately NOT underscore-prefixed. Streamlit hashes a plotly
    Figure by content (measured 4.3 ms for a 2000-point composite), so the
    figure *is* the cache key, and it cannot go stale: change the data, the fit,
    a peak label, a peak color or even a palette constant and the hash moves.
    A hand-rolled fingerprint was tried first and had five holes, one of them
    reachable — it hashed no module constants, so restyling `palette.py` served
    the old PNG under an identical key until the process restarted.

    max_entries bounds it: the default is unbounded, and each PNG is ~165-300 KB.
    show_spinner=False because the default would render "Running
    _export_png_cached(...)" inside the narrow Quick Export column.

    Note that a raise is not cached, so a broken kaleido install still re-pays
    the failing render every rerun — an error path, not a slow path.
    """
    return export_figure_png(fig, width=width, height=height, scale=scale)


def _describe_detected_modes(files) -> None:
    """Show what the loaded filenames said their technique was, and warn if some
    didn't say.

    Technique used to be a property of the selected preset, so it was always
    stated. It now comes from the filename, and `detect_mode_from_filename`
    returns None for a name it doesn't recognise -- whereupon the loader falls
    back to "Raman". That fallback is a guess, and a guess that nothing on
    screen distinguishes from a reading is worse than no reading at all.

    `SpectrumFile.auto_detected` already records which files were guessed at.
    """
    if not files:
        return

    counts = {"Raman": 0, "PL": 0}
    undetected = []
    for name, spectrum in files.items():
        if getattr(spectrum, "auto_detected", False):
            counts[spectrum.mode] = counts.get(spectrum.mode, 0) + 1
        else:
            undetected.append(name)

    parts = [f"{n} {mode}" for mode, n in counts.items() if n]
    if undetected:
        parts.append(f"{len(undetected)} undetected → Raman")
    if not parts:
        return

    line = ", ".join(parts)
    if undetected:
        st.warning(
            f"**Detected from filenames**: {line}. "
            f"Rename to RM_*/Raman_*/PL_* so the technique is read rather than "
            f"assumed.",
            icon="⚠️",
        )
    else:
        st.caption(f"**Detected from filenames**: {line}")


#: Fingerprint of the preset block each file was last fitted with, keyed by
#: filename. Session state rather than a `SpectrumFile` field: which preset a
#: fit came from is a UI affordance, not part of the measurement, and keeping
#: it here leaves the model's serialization alone.
_FIT_PRESET_KEY = "fit_preset_fingerprints"


def _remember_fit_preset(spectrum, preset) -> None:
    """Record which preset block `spectrum` was just fitted with."""
    fingerprints = st.session_state.setdefault(_FIT_PRESET_KEY, {})
    fingerprints[spectrum.filename] = (
        preset.material_name, technique_fingerprint(preset, spectrum.mode)
    )


def _warn_if_preset_moved(spectrum, preset) -> None:
    """Say so when the selected preset no longer matches the displayed fit.

    The QC Report has compared preset fingerprints since v5.0.0 and tells the
    operator to re-run; the Spectra page compared nothing, so editing a preset
    and coming back left a fit on screen that no longer matched the settings
    beside it, with nothing to say so. `fit_stale` covers despike and baseline
    only -- and nothing rendered it either.

    A warning, not an automatic refit: fitting is the operator's action, and
    the previous fit stays exportable until they take it.
    """
    if spectrum is None or not spectrum.fit_done:
        return
    remembered = st.session_state.get(_FIT_PRESET_KEY, {}).get(spectrum.filename)
    if remembered is None:
        return  # fitted before this ran, or by hand; nothing to compare against

    material, fingerprint = remembered
    if material == preset.material_name and \
            fingerprint == technique_fingerprint(preset, spectrum.mode):
        return

    made_with = (
        f"**{material}**'s earlier settings"
        if material == preset.material_name
        else f"**{material}**, not **{preset.material_name}**"
    )
    st.warning(
        f"The fit shown was made with {made_with}. Run the auto-workflow "
        f"again to apply the current preset.",
        icon="⚠️",
    )


_LOAD_NOTICE_KEY = "load_notice"


def _same_folder(a, b) -> bool:
    """Two source folders are the same folder (Windows: case- and slash-blind)."""
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _entry_name(name: str, source_dir: str, files: dict):
    """The name to load a file under, or None if it is already loaded.

    Files are keyed by name, and wafer folders reuse the same point names
    (`W01/RM_center.txt`, `W02/RM_center.txt`), so the bare name alone used to
    drop the second wafer's file as "already loaded". Only the same name *from
    the same folder* is a re-load; a clash from another folder takes its
    folder into the name -- `RM_center (W02).txt` -- and a counter if the folder
    names clash too. No `/`: the name also becomes export filenames as-is.
    """
    import itertools
    stem, ext = os.path.splitext(name)
    folder = os.path.basename(os.path.normpath(source_dir or "")) or "folder"
    candidates = itertools.chain(
        [name, f"{stem} ({folder}){ext}"],
        (f"{stem} ({folder} {k}){ext}" for k in itertools.count(2)),
    )
    for candidate in candidates:
        existing = files.get(candidate)
        if existing is None:
            return candidate
        if _same_folder(getattr(existing, "source_dir", None), source_dir):
            return None


def _fit_ok(spectrum) -> bool:
    """A successful fit is on record for `spectrum`."""
    return bool(
        spectrum is not None and spectrum.fit_done
        and getattr(spectrum, "fit_result", None) is not None and spectrum.fit_result.success
    )


def _x_unit(spectrum) -> str:
    return "cm⁻¹" if spectrum is None or spectrum.mode == "Raman" else "nm"


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------

def process_pending_loads() -> None:
    """Load the files the Browse button queued on the previous run.

    `pending_files_to_load` is written by Browse (and by the integration tests,
    which use it as the seam into the real parse-and-register path). It is
    drained before anything reads the file list, so the switcher and the
    material picker see the new files in the same run.
    """
    if 'last_picked_dir' not in st.session_state:
        st.session_state['last_picked_dir'] = ""

    files_to_load = st.session_state.pop('pending_files_to_load', None)
    if files_to_load:
        loaded_count = 0
        skipped_count = 0
        renamed = []

        for file_path in files_to_load:
            filename = os.path.basename(file_path)
            source_dir = os.path.dirname(file_path)  # v2.7: remember source folder for exports

            try:
                # Parse spectrum (returns list; len > 1 for multi-Y files)
                spectra = parse_spectrum_multi(file_path)

                # v2.1+ (FR-12): Auto-detect mode from filename
                detected_mode = detect_mode_from_filename(filename)

                if detected_mode is not None:
                    file_mode = detected_mode
                    auto_detected = True
                    set_mode(detected_mode)
                else:
                    file_mode = "Raman"
                    auto_detected = False

                # Build per-spectrum entries. For multi-Y files, suffix the
                # filename with __1, __2, … so each Y column appears as a
                # separate file entry in the file list.
                base, ext = os.path.splitext(filename)
                n = len(spectra)
                for i, spectrum_data in enumerate(spectra, start=1):
                    wanted = filename if n == 1 else f"{base}__{i}{ext}"
                    entry_name = _entry_name(wanted, source_dir, st.session_state.get("files", {}))
                    if entry_name is None:
                        skipped_count += 1
                        continue
                    if entry_name != wanted:
                        renamed.append(entry_name)

                    spectrum_file = SpectrumFile(
                        filename=entry_name,
                        mode=file_mode,
                        original_data=spectrum_data,
                        raw_data=spectrum_data,
                        processed_data=spectrum_data,
                        source_dir=source_dir,  # v2.7: folder the .txt came from
                        processing_settings=ProcessingSettings(),
                        auto_detected=auto_detected
                    )
                    add_spectrum_file(spectrum_file)
                    loaded_count += 1

            except Exception as e:
                st.error(f"❌ Failed to load {filename}: {e}")

        notice = []
        if loaded_count > 0:
            notice.append(("success", f"✅ Loaded {loaded_count} file(s)"))
            if renamed:
                notice.append(("info", "ℹ️ Same name as a file from another folder, so loaded as: "
                                       + ", ".join(renamed)))
            if skipped_count > 0:
                notice.append(("info", f"ℹ️ Skipped {skipped_count} already-loaded file(s)"))
        elif skipped_count > 0:
            notice.append(("info", f"ℹ️ All {skipped_count} file(s) already loaded"))
        # Shown by the run-setup block, beside the file list it is about.
        st.session_state[_LOAD_NOTICE_KEY] = notice

    files = st.session_state.get("files", {})
    if files and st.session_state.get("current_file") not in files:
        st.session_state["current_file"] = next(iter(files))


def _browse_for_files() -> None:
    """Open the native multi-file dialog and queue the picks for the next run."""
    try:
        picked = run_dialog(
            "askopenfilenames",
            timeout=120,
            title="Select Spectrum Files",
            initialdir=st.session_state.get('last_picked_dir', ''),
            filetypes=[["Text files", "*.txt"], ["All files", "*.*"]],
        )
        if picked:
            st.session_state['pending_files_to_load'] = picked
            st.session_state['last_picked_dir'] = os.path.dirname(picked[0])
            st.rerun()
        # cancel / empty selection -> no-op, no rerun
    except Exception as e:
        st.error(f"❌ Failed to open file browser: {e}")


# ----------------------------------------------------------------------
# Run setup: the file switcher, then preset and Run
# ----------------------------------------------------------------------

def _file_status_dot(spectrum) -> str:
    """The coloured dot the loaded-files row puts before each name."""
    if _fit_ok(spectrum):
        return ":green[●]" if spectrum.fit_result.converged else ":orange[●]"
    return ":gray[●]"


#: Above this many files the row of file pills wraps into a wall; the
#: dropdown and the arrows still reach every file.
_MAX_FILE_PILLS = 30


def _render_file_switcher(files: dict, current_file) -> None:
    """Previous / dropdown / next, Add files, Remove File and Delete All.

    A switch is never applied here: the layer toggles below follow the new
    file's processing stage, and they are written by
    `sync_pending_file_switch()` at the top of the next run (see the module
    docstring). Every control here only records the target and reruns.
    """
    file_list = list(files.keys())
    current_idx = file_list.index(current_file) if current_file in file_list else 0
    n = len(file_list)

    cols = st.columns([0.55, 6, 0.55, 0.8, 1.5, 1.5, 1.4], vertical_alignment="bottom")
    with cols[0]:
        if st.button("◀", key="file_prev", help="Previous file", disabled=n < 2, width="stretch"):
            st.session_state['_pending_file_switch'] = file_list[(current_idx - 1) % n]
            st.rerun()
    with cols[1]:
        if n:
            selected = st.selectbox(
                "Spectrum file",
                options=file_list,
                index=current_idx,
            )
            if selected != current_file:
                st.session_state['_pending_file_switch'] = selected
                st.rerun()
        else:
            st.selectbox("Spectrum file", options=["No files loaded"], disabled=True)
    with cols[2]:
        if st.button("▶", key="file_next", help="Next file", disabled=n < 2, width="stretch"):
            st.session_state['_pending_file_switch'] = file_list[(current_idx + 1) % n]
            st.rerun()
    with cols[3]:
        st.markdown(
            f"<div style='text-align: center; padding-bottom: 8px; color: #5A616B; "
            f"font-family: monospace;'>{current_idx + 1 if n else 0} / {n}</div>",
            unsafe_allow_html=True,
        )
    with cols[4]:
        if st.button("Add files…", key="browse_files", width="stretch",
                     help="Pick spectrum files (.txt); the dialog opens at the last folder"):
            _browse_for_files()
    with cols[5]:
        if st.button("Remove File", key="remove_file", disabled=not n, width="stretch",
                     help="Remove the current file from the session"):
            remove_spectrum_file(current_file)
            st.rerun()
    with cols[6]:
        if st.button("Delete All", key="delete_all_files", icon=":material/delete:",
                     disabled=not n, width="stretch", help="Remove all loaded files"):
            clear_all_files()
            st.rerun()

    for kind, text in st.session_state.pop(_LOAD_NOTICE_KEY, None) or []:
        getattr(st, kind)(text)


def _render_file_pills(files: dict, current_file) -> None:
    """The loaded files as pills, each with its fit status.

    Rendered into a slot under the switcher but filled after Run and Run All
    Files have had their turn, so a batch's new statuses show in the same run.
    """
    file_list = list(files.keys())
    if not 1 < len(file_list) <= _MAX_FILE_PILLS:
        return
    picked = st.pills(
        "Loaded files",
        options=file_list,
        default=current_file if current_file in files else file_list[0],
        format_func=lambda name: f"{_file_status_dot(files[name])} {name}",
        label_visibility="collapsed",
    )
    if picked is not None and picked != current_file:
        st.session_state['_pending_file_switch'] = picked
        st.rerun()


def _run_single(spectrum, preset) -> None:
    """Run Auto-Workflow on the current file and report the outcome."""
    from ..processing.auto_workflow import (
        execute_auto_workflow, format_workflow_summary, get_workflow_suggestions,
    )

    with st.spinner("Running the auto-workflow…"):
        result = execute_auto_workflow(spectrum, preset)

    if result["success"]:
        st.success(format_workflow_summary(result, preset, spectrum.mode))
        _remember_fit_preset(spectrum, preset)
        # Show the fit, unless the operator pinned these toggles by hand. Safe
        # to write here: the toggles render further down the page.
        set_view_options({'show_fit': True, 'show_components': True,
                          'show_on_background': True})
    else:
        st.error(format_workflow_summary(result, preset, spectrum.mode))
        if result['stage_completed']:
            st.info(get_workflow_suggestions(result['stage_completed'], result['error_message']))

    # No st.rerun(): it discarded the success summary, and on a failure the
    # error and its suggestions, one frame after drawing them -- while the
    # workflow had already reset the spectrum to raw, so a failed run looked
    # like the previous fit silently vanishing.


def _run_batch(files: dict, preset) -> None:
    """Run All Files: the auto-workflow on every file whose technique is known."""
    from ..processing.auto_workflow import execute_auto_workflow

    # Skip files whose technique was guessed rather than read. Fitting a PL
    # spectrum against Raman settings produces a confident-looking wrong
    # answer, and in a batch of 25 no one would notice which one it was.
    file_items = [item for item in files.items() if getattr(item[1], "auto_detected", False)]
    skipped = [name for name, spec in files.items() if not getattr(spec, "auto_detected", False)]
    if skipped:
        st.warning(
            f"Skipping {len(skipped)} file(s) whose technique the "
            f"filename doesn't state: {', '.join(skipped[:5])}"
            + (" ..." if len(skipped) > 5 else "")
            + ". Run them individually, or rename them RM_*/PL_*."
        )
    total = len(file_items)
    success_count = 0
    failed_files = []
    unconverged_files = []

    if total == 0:
        st.error(
            "No loaded file states its technique in its "
            "filename, so there is nothing safe to batch-fit."
        )
    # One widget carrying one number: the old pair reported the same index as
    # "now starting" (label) and "this many finished" (bar), so they could
    # never be reconciled.
    with st.status(f"Fitting {total} files...", expanded=True) as status:
        progress_bar = st.progress(0.0, text="Starting...")
        for idx, (filename, spectrum) in enumerate(file_items):
            # idx, not idx + 1: the fraction is work *finished*, while the
            # label names the file now running.
            progress_bar.progress(idx / total, text=f"Fitting {idx + 1}/{total}: {filename}")
            result = execute_auto_workflow(spectrum, preset)
            if result["success"]:
                success_count += 1
                _remember_fit_preset(spectrum, preset)
                if not result["fit_result"].converged:
                    unconverged_files.append(filename)
            else:
                failed_files.append((filename, result.get("error_message") or "Unknown error"))

        progress_bar.progress(1.0, text=f"{success_count}/{total} fitted")
        status.update(label=f"{success_count}/{total} files fitted", state="complete",
                      expanded=False)

    if total == 0:
        pass  # already reported above
    elif success_count == total:
        st.success(f"All {total} files processed successfully!")
    elif success_count > 0:
        st.warning(f"{success_count}/{total} files succeeded. {len(failed_files)} failed.")
    else:
        st.error("All files failed processing.")

    # Outside the status — st.status is expander-like, and Streamlit forbids
    # nesting expanders. Expanded, because a collapsed one could never be
    # opened: clicking it reruns the script with this button False, so the
    # whole block (and the expander) ceases to exist.
    if failed_files:
        with st.expander(f"{len(failed_files)} file(s) failed", expanded=True):
            for fname, err in failed_files:
                st.error(f"**{fname}**: {err}")
    if unconverged_files:
        # Fitted, kept, but not finished: the preset's max_iterations ran out
        # first. Named so the operator can raise the budget rather than trust
        # a number that is wherever the search happened to stop.
        st.warning(
            f"{len(unconverged_files)} file(s) stopped at the preset's "
            f"max_iterations before converging: "
            + ", ".join(unconverged_files)
            + ". Their numbers are the best found, not an optimum, "
            "with no uncertainties. Raise max_iterations in the preset."
        )

    set_view_options({'show_fit': True, 'show_components': True,
                      'show_on_background': True})
    st.session_state['expanded_section'] = 'export'
    st.session_state['despike_preview'] = None
    st.session_state['baseline_preview'] = None
    st.session_state['despike_preview_toggle'] = False
    st.session_state['baseline_preview_toggle'] = False
    # No st.rerun(): it used to discard the summary and the whole list of
    # files that failed one frame after drawing it.


def _technique_badge(spectrum) -> None:
    """The current file's technique, as read from its filename (or guessed)."""
    if spectrum is None:
        text, bg, fg = "—", "#F1F3F5", "#5A616B"
    elif getattr(spectrum, "auto_detected", False):
        text, bg, fg = f"{spectrum.mode}", "#E8F0FB", "#134383"
    else:
        text, bg, fg = f"{spectrum.mode} (assumed)", "#FBEEDF", "#8A3F0A"
    st.markdown(
        f"<div style='font-size: 14px; color: #31333F; margin-bottom: 8px' "
        f"title='Read from the filename (RM_*, Raman_*, PL_*)'>Technique</div>"
        f"<div style='display: flex; align-items: center; min-height: 40px; padding: 0 12px; "
        f"border-radius: 8px; background: {bg}; color: {fg}; font-weight: 600'>{text}</div>",
        unsafe_allow_html=True,
    )


def render_run_setup() -> None:
    """The run-setup card: which file, which material, and Run.

    Renders above the layer toggles on purpose: Run, Run All Files and Reset
    write `show_*` keys, which must happen before those widgets exist.
    """
    presets = load_presets()
    files = st.session_state.get("files", {})
    current_file = st.session_state.get("current_file")
    current_spectrum = files.get(current_file) if files else None

    with st.container(border=True):
        _render_file_switcher(files, current_file)
        pills_slot = st.container()

        preset = None
        cols = st.columns([2.2, 1.6, 2.1, 2.1, 1.5, 2.5], vertical_alignment="bottom")
        with cols[0]:
            # As of v4.0.0 a preset is keyed by material alone and each file's
            # own mode picks the technique block, so nothing here rewrites a
            # loaded file's mode.
            preset_keys = sorted(key for key, p in presets.items() if p.enabled) if presets else []
            selected_key = st.selectbox(
                "Material preset",
                options=[None] + preset_keys,
                index=0,
                format_func=lambda k: "(None)" if k is None else k,
                help="Materials configured on the Material Presets page (Raman + PL)",
                disabled=not presets,
            )
            preset = presets[selected_key] if selected_key is not None else None
            st.session_state['selected_preset'] = preset
        with cols[1]:
            _technique_badge(current_spectrum)
        with cols[2]:
            run_clicked = st.button(
                "Run Auto-Workflow", type="primary", icon=":material/play_arrow:",
                width="stretch", disabled=preset is None or current_spectrum is None,
            )
        with cols[3]:
            batch_clicked = st.button(
                f"Run All Files ({len(files)})", icon=":material/fast_forward:",
                width="stretch", disabled=preset is None or len(files) < 2,
                help="Run auto-workflow on all loaded files",
            )
        with cols[4]:
            if st.button("Reset to Raw", type="tertiary", icon=":material/undo:",
                         disabled=current_spectrum is None,
                         help="Clear all processing and start over"):
                current_spectrum.reset_to_raw()
                st.rerun()

        if not presets:
            st.caption("No materials configured yet. Add one on the **Material Presets** page.")
        elif preset is not None:
            _describe_detected_modes(files)
            notes = []
            for _mode in ("Raman", "PL"):
                _block = preset.block_for(_mode)
                if _block is not None:
                    notes.append(f"**{_mode}**: {_block.baseline_algorithm} baseline, "
                                 f"{len(_block.peak_templates)} peaks")
            if not notes:
                notes.append("No Raman or PL settings on this material.")
            if preset.description:
                notes.append(f"**Notes**: {preset.description}")
            st.caption(" · ".join(notes))
            # Next to the Run button, which is the thing to press about it.
            _warn_if_preset_moved(current_spectrum, preset)
        elif files:
            st.caption("Pick a material preset to enable Run.")
        else:
            st.caption("Add spectrum files to begin.")

        if run_clicked and current_spectrum is not None and preset is not None:
            _run_single(current_spectrum, preset)
        if batch_clicked and preset is not None:
            _run_batch(files, preset)

        with pills_slot:
            _render_file_pills(files, current_file)


# ----------------------------------------------------------------------
# The fitted spectrum: baseline view, layer toggles, the Plotly figure
# ----------------------------------------------------------------------

_BASELINE_VIEWS = ("On background", "Baseline removed")


def _render_baseline_view() -> None:
    """On background / Baseline removed: the `show_on_background` layer.

    Not a widget keyed `show_on_background`: that key is written by the
    stage-following paths (file switch, Run), and a segmented control's value
    is a label, not the bool they write. The control is drawn from the key on
    every run and writes it back -- pinned, like any toggle the operator sets --
    before the figure below reads it.
    """
    current = _BASELINE_VIEWS[0] if st.session_state.get("show_on_background", True) else _BASELINE_VIEWS[1]
    choice = st.segmented_control(
        "Baseline", options=_BASELINE_VIEWS, default=current,
        label_visibility="collapsed",
        help="Draw the data, fit and components on top of the subtracted "
             "baseline (gray), or on zero. Residuals stay about zero. "
             "Quick Export follows.",
    )
    if choice is not None and choice != current:
        st.session_state["show_on_background"] = choice == _BASELINE_VIEWS[0]
        pin_view_option("show_on_background")


def _render_layer_toggles() -> None:
    """The View Options, as one row of toggles beside the plot they change.

    No value= on these: defaults come from initialize_session_state(), and
    sync_pending_file_switch() (run first) may have just written the keys --
    passing value= as well makes Streamlit warn that the state was set
    through both paths. on_change pins the key: from then on the toggle is
    the operator's answer, and the stage-derived defaults (file switch,
    auto-workflow, batch) stop overwriting it (v5.7.1).
    """
    toggles = [
        ("show_corrected", ":violet[●] Data", "The baseline-corrected data — the series that is fitted"),
        ("show_fit", "**━** Fit total", "The total fitted curve"),
        ("show_components", ":rainbow[▮] Components", "Each fitted peak, in its preset colour"),
        ("show_residuals", ":green[●] Residuals", "Baseline-corrected minus Fit Total, in its own panel"),
        ("show_raw", ":blue[●] Raw", "The data as loaded, before any processing"),
        ("show_despiked", ":orange[●] De-spiked", "The data after spike removal"),
    ]
    pinned = bool(pinned_view_options())
    cols = st.columns([1, 1.1, 1.4, 1.2, 0.9, 1.2, 1.8], vertical_alignment="center")
    for col, (key, label, help_text) in zip(cols, toggles):
        with col:
            st.checkbox(label, key=key, on_change=pin_view_option, args=(key,), help=help_text)
    with cols[-1]:
        if pinned and st.button(
            "Follow the data again", type="tertiary", width="stretch",
            help="Drop your manual choices and let each file's processing stage "
                 "pick the layers again",
        ):
            # Un-pin here, but re-derive on the NEXT run: the toggles are
            # already instantiated, and Streamlit raises if show_* is written
            # after its widget rendered. The flag is consumed by
            # sync_pending_view_options() at the top of the page.
            clear_pinned_view_options()
            st.session_state['_pending_view_options_reset'] = True
            st.rerun()


def render_spectrum() -> None:
    """The fitted-spectrum card: baseline view, layer toggles and the plot."""
    files = st.session_state.get("files", {})
    spectrum = files.get(st.session_state.get("current_file"))

    with st.container(border=True):
        head, view = st.columns([3, 2], vertical_alignment="center")
        with head:
            name = f" · `{spectrum.filename}`" if spectrum is not None else ""
            st.markdown(f"#### Fitted spectrum{name}")
        with view:
            _render_baseline_view()
        _render_layer_toggles()

        if spectrum is None:
            fig = create_empty_figure()
            layer_config = {}
        else:
            layer_config = {
                "raw": st.session_state.get("show_raw", True),
                "despiked": st.session_state.get("show_despiked", False),
                "baseline_corrected": st.session_state.get("show_corrected", False),
                "fit_total": st.session_state.get("show_fit", False),
                "components": st.session_state.get("show_components", False),
                "residuals": st.session_state.get("show_residuals", False),
                "on_background": st.session_state.get("show_on_background", True),
            }
            fig = create_unified_figure(spectrum, layer_config)

        # Anchor for the mobile "Jump to Plot" link.
        st.markdown('<div id="plot-anchor"></div>', unsafe_allow_html=True)
        from core.viz.render import render_plot
        render_plot(fig, key="unified_plot")

        if (spectrum is not None and layer_config.get("on_background") and _fit_ok(spectrum)
                and spectrum.processing_settings.baseline_applied
                and background_offset(spectrum, layer_config) is None):
            st.caption("Peaks on Background needs the baseline from this session's fit "
                       "— re-run Auto-Workflow on this file to show it.")


# ----------------------------------------------------------------------
# Fit results and summary
# ----------------------------------------------------------------------

def _peak_status(peak, fit_result) -> str:
    """Measured, or why not: the same test the ratios and QC figures use."""
    from ..processing.peak_metrics import MEASURED_SNR, peak_is_measured
    if peak_is_measured(peak, fit_result):
        return "Measured"
    if getattr(peak, "on_limit", False):
        return "On fit limit"
    return f"Below {MEASURED_SNR:g}× noise"


def render_fit_results() -> None:
    """The Fit Results table: one row per peak, intensity first among the values.

    Intensity is the fitted maximum and Area the integral (CLAUDE.md, "Two
    quantities, two names"); FWHM is the Voigt FWHM. The table's Intensity is
    the same number both CSVs carry.
    """
    spectrum = get_current_spectrum()
    if not _fit_ok(spectrum):
        return

    from ..processing.peak_metrics import peak_intensity, raw_peak_stats

    unit = _x_unit(spectrum)
    fit = spectrum.fit_result
    rows = []
    # PL only: the unfitted spectrum's own peak stats, first.
    if spectrum.mode == "PL":
        raw = raw_peak_stats(spectrum.processed_data.X, spectrum.processed_data.Y)
        if raw is not None:
            rows.append({
                "Peak": "Raw",
                f"Centre ({unit})": f"{raw.center:.2f}",
                f"FWHM ({unit})": f"{raw.fwhm:.2f}" if raw.fwhm is not None else "—",
                "Intensity": f"{raw.intensity:.0f}",
                "Area": "—",
                "Status": "Unfitted spectrum",
            })
    for peak in fit.fitted_peaks:
        rows.append({
            "Peak": peak.label,
            f"Centre ({unit})": f"{peak.center:.2f}",
            f"FWHM ({unit})": f"{peak.width_fwhm:.2f}",
            "Intensity": f"{peak_intensity(peak):.0f}",
            "Area": f"{peak.area:.0f}",
            "Status": _peak_status(peak, fit),
        })

    with st.container(border=True):
        head, note = st.columns([1, 2], vertical_alignment="bottom")
        with head:
            st.markdown("#### Fit results")
        with note:
            st.caption("Intensity = fitted maximum · Area = integral · FWHM = Voigt FWHM",
                       text_alignment="right")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _pipeline_text(spectrum) -> str:
    """What was done to the spectrum before the fit, in the order it was done."""
    settings = spectrum.processing_settings
    steps = []
    if settings.despike_applied:
        steps.append("Despike")
    if settings.baseline_applied:
        steps.append(f"{settings.baseline_algorithm} baseline")
    if spectrum.x_range_enabled and spectrum.x_min is not None and spectrum.x_max is not None:
        steps.append(f"fit {spectrum.x_min:g}–{spectrum.x_max:g} {_x_unit(spectrum)}")
    else:
        steps.append("fit")
    return " → ".join(steps)


def render_fit_summary() -> None:
    """R², χ², whether the fit converged, and what ran before it."""
    spectrum = get_current_spectrum()
    if not _fit_ok(spectrum):
        return
    fit = spectrum.fit_result
    c1, c2, c3, c4 = st.columns(4)
    with c1, st.container(border=True):
        st.metric("R²", f"{fit.r_squared:.4f}")
    with c2, st.container(border=True):
        chi = fit.chi_squared
        st.metric("χ²", f"{chi:,.0f}".replace(",", " ") if chi < 1e7 else f"{chi:.2e}")
    with c3, st.container(border=True):
        st.metric("Fit status", "Converged" if fit.converged else "Stopped early")
    with c4, st.container(border=True):
        st.caption("Pipeline")
        st.markdown(_pipeline_text(spectrum))
    if not fit.converged:
        from ..processing.auto_workflow import convergence_warning
        st.warning(convergence_warning(fit))


# ----------------------------------------------------------------------
# Exports, top right of the page
# ----------------------------------------------------------------------

def _composite_for(spectrum):
    """The export figure for `spectrum`, following the on-screen layers.

    Follows the toggles rather than hardcoding both on: the exported figure is
    the same picture the operator just tuned on screen.
    """
    return plot_composite(
        x=spectrum.processed_data.X,
        y_data=spectrum.processed_data.Y,
        fit_result=spectrum.fit_result,
        mode=spectrum.mode,
        title=f"{spectrum.filename} - Fit Results",
        show_components=st.session_state.get("show_components", True),
        show_residuals=st.session_state.get("show_residuals", True),
        background=(spectrum.background
                    if st.session_state.get("show_on_background", True) else None),
    )


def _render_quick_export(spectrum) -> None:
    st.caption("QUICK EXPORT · THIS FILE")
    ready = _fit_ok(spectrum)
    export_fig = None
    if ready:
        try:
            export_fig = _composite_for(spectrum)
        except Exception as e:
            st.error(f"Failed to prepare export: {e}")

    c1, c2, c3 = st.columns(3)
    with c1:
        png_bytes = b""
        if export_fig is not None:
            try:
                png_bytes = _export_png_cached(export_fig, width=1200, height=600, scale=2.0)
            except RuntimeError:
                st.error("PNG export requires kaleido")
                st.caption("Install: `pip install kaleido`")
        st.download_button(
            "PNG", data=png_bytes, icon=":material/download:",
            file_name=create_filename(spectrum.filename, "fit", "png") if ready else "fit.png",
            mime="image/png", width="stretch", disabled=not png_bytes,
            help="Download the fit-results figure as a PNG",
        )
    with c2:
        html_string = ""
        if export_fig is not None:
            try:
                html_string = export_figure_html(export_fig)
            except Exception as e:
                st.error(f"HTML export failed: {e}")
        st.download_button(
            "HTML", data=html_string,
            file_name=create_filename(spectrum.filename, "fit", "html") if ready else "fit.html",
            mime="text/html", width="stretch", disabled=not html_string,
            help="Download the figure as an interactive HTML page",
        )
    with c3:
        st.download_button(
            "CSV", data=export_fit_params_csv(spectrum) if ready else "",
            file_name=create_filename(spectrum.filename, "fit_params", "csv") if ready else "fit_params.csv",
            mime="text/csv", width="stretch", disabled=not ready,
            help="Download this file's fit parameters",
        )


def _save_batch_pngs(fitted_files, image_dir) -> None:
    progress = st.progress(0.0, text="Exporting images…")
    saved = 0
    failed = []
    for i, spectrum in enumerate(fitted_files, start=1):
        try:
            png_bytes = export_figure_png(_composite_for(spectrum), width=1200, height=600, scale=2.0)
            out_path = os.path.join(image_dir, create_filename(spectrum.filename, "fit", "png"))
            with open(out_path, "wb") as out_f:
                out_f.write(png_bytes)
            saved += 1
        except RuntimeError:
            # kaleido missing -- every remaining export would fail identically,
            # so stop rather than repeat the same error N times.
            st.error("❌ PNG export requires kaleido")
            st.caption("Install: `pip install kaleido`")
            failed = None
            break
        except Exception as e:
            failed.append((spectrum.filename, str(e)))
        progress.progress(i / len(fitted_files),
                          text=f"Exporting images… ({i}/{len(fitted_files)})")
    progress.empty()
    if saved:
        st.success(f"✅ Saved {saved} PNG(s) to {image_dir}")
    if failed:
        with st.expander(f"⚠️ {len(failed)} file(s) failed"):
            for name, err in failed:
                st.caption(f"{name}: {err}")


def _render_batch_export(spectrum) -> None:
    files = st.session_state.get('files', {})
    fitted_files = [f for f in files.values() if _fit_ok(f)]
    st.caption(f"BATCH EXPORT · {len(fitted_files)} FITTED FILES")
    # The Save dialogs open at the current file's source folder, then the
    # last-browsed folder, then the OS default.
    default_dir = (getattr(spectrum, 'source_dir', None) if spectrum is not None else None) \
        or st.session_state.get('last_picked_dir', '')
    enough = len(fitted_files) > 1
    c1, c2 = st.columns(2)
    with c1:
        save_csv = st.button(
            "Save Master CSV…", icon=":material/save:", width="stretch", disabled=not enough,
            help="Open a Save-As dialog (pre-pointed at your raw-data folder) and type a "
                 "filename. Includes Filename, Mode, Peak_Label, Center, Intensity, FWHM, "
                 "Shape, R², χ² and standard errors.",
        )
    with c2:
        save_pngs = st.button(
            "Save PNGs…", icon=":material/photo_library:", width="stretch", disabled=not enough,
            help="Open a folder dialog (pre-pointed at your raw-data folder) and save one "
                 "fit-results PNG per fitted file",
        )

    if save_csv:
        try:
            save_path = prompt_save_path(
                default_dir=default_dir,
                default_filename=create_filename("nexanalyzer", "master_results", "csv"),
                title="Save Master CSV",
            )
            if save_path:
                # Built on Save only: it walks every fitted file's peaks.
                with open(save_path, "w", newline="", encoding="utf-8") as f:
                    f.write(export_master_csv(files))
                st.success(f"✅ Saved to {save_path}")
            # save_path is None => user cancelled => no-op
        except Exception as e:
            st.error(f"❌ Failed to save master CSV: {e}")

    if save_pngs:
        # Its own try/except: a broken kaleido install must not also take down
        # the CSV export, and vice versa.
        try:
            image_dir = prompt_save_folder(
                default_dir=default_dir, title="Select Folder for Batch PNG Export",
            )
            if image_dir:
                _save_batch_pngs(fitted_files, image_dir)
            # image_dir is None => user cancelled => no-op
        except Exception as e:
            st.error(f"Failed to export batch images: {e}")


def render_exports() -> None:
    """Quick Export of this file and Batch Export of every fitted file.

    Called last, into the header slot the page reserves: the exports follow
    the layer toggles, so they must see this run's values, and a Run on this
    run must already have made the fit they export.
    """
    spectrum = get_current_spectrum()
    quick, batch = st.columns([1, 1.15])
    with quick:
        _render_quick_export(spectrum)
    with batch:
        _render_batch_export(spectrum)
