"""
QC Report: one sample folder in, eight figures and a workbook out.

Pick a folder holding a 9-point Raman/PL/OM measurement grid, fit every
spectrum against one material's presets, segment every frame, and produce:

  1 Summary            overview page — OM grid, its class table, Raman and PL
                       fit-summary tables, and the pooled domain-size table
  2 OM                 layer segmentation across the grid positions
  3 OM diagnostic      the same, plus the green-channel histograms showing
                       where each threshold landed
  4 Domain size        flake domains split out of one OM class -- below,
                       reference, or above the reference layer, operator's
                       choice -- and measured, in microns where the
                       magnification has a calibration set (Material Presets
                       page), in pixels otherwise. Opt-in — off by default,
                       roughly doubling optical analysis time when on
  5 Raman              the nine fitted Raman spectra
  6 Raman stats        fitted FWHM, peak centres and diagnostic ratios across
                       the positions, with the inherited spec lines drawn
  7 PL                 the nine fitted PL spectra
  8 PL stats           the same panels for PL

plus one .xlsx carrying every number behind them.

This is the merge of the Sample Report and QC Panel pages (v5.0.0). They read
the same folder, ran the same scan and fit, and each produced half of what an
operator wanted — so running both meant picking the same folder twice and
fitting the same spectra twice. The .pptx the Sample Report used to build is
gone with them, along with the PowerPoint COM rendering that rasterized it:
the eight PNGs and the workbook are the deliverable now, and nothing here
needs PowerPoint installed.

**One run.** One folder pick, one `run_sample_batch`, and every artifact
derived from that single result — so no two figures can describe different
fits of the same sample.

**Auto-detect.** A technique with no data is skipped rather than drawn empty,
and the inventory above the Run button says what was found before anything
runs. Two different absences get two different messages: a folder with no PL
files is a naming problem, a material with no PL block is a preset problem,
and they are fixed in different places.

**The threshold pair comes from the preset, and only from the preset.** Every
wafer is segmented on the same `abs_threshold` pair, so two wafers' coverage
numbers are measured with the same ruler and can be compared. The pair is
printed on screen, written under the summary page's OM table, and carried on
every `OM_Stats` row — a page that does not name its pair cannot be reproduced
from itself.

The segmentation's other tunables — `nsigma`, `minpx`, the mask margin and the
flat-field divisor — come from the material preset's optical block for the
chosen layer, so a figure's numbers are reproducible from the sample folder
plus the committed preset. Tune them on the Material Presets page, not per
run; docs/OM_Contrast_Algo.md says when each is worth moving. A layer with no
block of its own runs the algorithm's own defaults, and the page says so.
"""

import json
import os
from pathlib import Path

import streamlit as st

from core.io.export import prompt_save_path
from core.io.folder_picker import prompt_folder_path
from core.io.tool_layout import OPTICAL, ToolLayout, find_wafer, list_wafers
from core.io.tool_roots import load_last_tool, save_last_tool
from core.io.om_calibration import get_um_per_px, load_om_calibration
from core.io.report_settings import (
    load_default_material,
    load_measure_domain_size,
    load_split_size_populations,
    save_default_material,
    save_measure_domain_size,
    save_split_size_populations,
)
from core.report.progress import build as build_progress
from core.ui.theme import card, page_header
from core.ui.tool_select import render_tool_select
from modules.optical.processing.contrast import (
    REFERENCE_CHOICES,
    class_labels,
    class_summary,
    layer_word,
)
from modules.optical.processing.domain_size import TARGETS
from modules.optical.processing.layer_suggest import (
    CONFIDENCE_FLOOR,
    NOISE_GATE_PCT as LAYER_NOISE_GATE,
    calibration_for,
    suggest_layer,
)
from modules.optical.ui.qc_report_state import (
    get_qc_report_state,
    reset_optical_results,
    reset_results,
    reset_spectra_results,
    select_folder,
)
from modules.qc_report.processing.build import (
    GRID_POINTS,
    REPORT_FIGURES,
    QcReportInputs,
    build_qc_report,
    grid_points_only,
    save_report,
)
from modules.spectra.io.preset_store import load_presets
from modules.spectra.processing.parser import count_spectra
from modules.spectra.processing.peak_metrics import (
    R_SQUARED_MIN,
)
from modules.spectra.processing.spectrum_selection import SPECTRA_PER_POINT
from modules.spectra.processing.sample_scanner import default_magnification, scan_sample_folder
from modules.spectra.utils.preset_staleness import optical_fingerprint, technique_fingerprint


page_header(
    "Analysis · QC Report",
    "QC Report",
    "Eight figures and one workbook from a sample folder's 9-point "
    "OM + Raman + PL measurement grid. Anything without data is skipped.",
)

state = get_qc_report_state()


def _single_optical_layer(material: str):
    """The one layer a material's optical block names, or None (none, or both)."""
    try:
        preset = load_presets().get(material)
    except Exception:
        return None
    layers = sorted((preset.optical or {}).keys()) if preset else []
    return layers[0] if len(layers) == 1 else None


@st.cache_data(show_spinner=False, max_entries=64)
def _suggest_layer(paths: tuple[str, ...], stamps: tuple[float, ...], material: str):
    """layer_suggest.suggest_layer, cached per frame set. `stamps` (mtimes) is
    only part of the key, so a frame rewritten in place is read again."""
    return suggest_layer(paths, material)


def _layer_suggestion_caption(suggestion, chosen) -> str:
    """One line saying what the colour suggests, why, and whether the
    current choice agrees with it."""
    f = suggestion.features
    evidence = (
        f"B/G {f.blue_green:.3f}, R/G {f.red_green:.3f}, {f.band_pct:.1f} % of "
        f"the frame 7–14 % darker than the film; {suggestion.n_frames} frame(s)"
    )
    if suggestion.too_noisy:
        return (
            f"Suggestion: **none** — the frames are too noisy to read the film's colour "
            f"(noise {f.noise_pct:.1f} % of the film level, limit {LAYER_NOISE_GATE:.0f} %; "
            f"it leans {suggestion.likely}). Choose the layer."
        )
    if suggestion.layer is None:
        return (
            f"Suggestion: **not sure** — leans {suggestion.likely} at confidence "
            f"{suggestion.confidence:.2f}, below the {CONFIDENCE_FLOOR:.1f} floor "
            f"({evidence})."
        )
    line = (
        f"Suggested **{suggestion.layer}** from the film's colour, confidence "
        f"{suggestion.confidence:.2f} ({evidence})."
    )
    if chosen is not None and chosen != suggestion.layer:
        line += f" You chose **{chosen}** instead."
    return line


@st.cache_data(ttl=300, show_spinner="Listing wafers...")
def _tool_wafers(name: str, root: str, spec_json: str) -> list[str]:
    """A tool's wafers, newest first. Cached: the optical tree is on OneDrive."""
    return list_wafers(ToolLayout(name=name, root=Path(root), spec=json.loads(spec_json)))


# ---------------------------------------------------------------- Folder pick
# One card, entered twice: the pick widgets here, and the scan's summary once
# the folder has been read below.
sample_card = card("sample")
with sample_card:
    st.markdown("#### 1 · Sample")
    picked = None
    tool, tool_changed = render_tool_select(
        OPTICAL, state["folder"], key="qc_tool", fallback=load_last_tool()
    )
    if tool_changed:
        try:
            save_last_tool(tool.name)
        except OSError:
            pass  # a lost preference is not a reason to stop the page
        # The shown tool is read back from the loaded folder, so a sample from the
        # previous tool would pull the dropdown straight back to it. Switching tool
        # is starting over: drop the sample, and the dropdown falls back to the
        # tool just saved.
        state["folder"] = None
        state["scan"] = None
        reset_results(state)
        st.rerun()

    if tool is not None:
        wafers = _tool_wafers(tool.name, str(tool.root), json.dumps(tool.spec, sort_keys=True))
        current_wafer = Path(state["folder"]).name if state["folder"] else None
        options = wafers if current_wafer in wafers else [""] + wafers
        col_wafer, col_refresh = st.columns([5, 1], vertical_alignment="bottom")
        with col_wafer:
            wafer = st.selectbox(
                "Wafer",
                options,
                index=options.index(current_wafer) if current_wafer in options else 0,
                key=f"qc_wafer__{tool.name}__{current_wafer}",
                help="Every wafer with optical data under this tool, newest first.",
            )
        with col_refresh:
            refresh_clicked = st.button(
                "Refresh",
                icon=":material/refresh:",
                key="qc_wafer_refresh",
                width="stretch",
                help="Re-read the tool's optical tree and the selected folder. The "
                     "wafer list is cached for five minutes, so a folder created or "
                     "filled while the page was open needs this.",
            )
        if refresh_clicked:
            _tool_wafers.clear()
            if state["folder"]:
                # A folder whose contents moved invalidates the results drawn from
                # them, so re-selecting it is the honest response -- but only when
                # the scan actually changed, or every refresh would throw away a
                # run that is still current.
                try:
                    fresh = scan_sample_folder(state["folder"])
                except OSError as e:
                    st.warning(f"Could not re-read {state['folder']}: {e}")
                else:
                    if fresh != state["scan"]:
                        select_folder(state, state["folder"], fresh,
                                      default_magnification(fresh))
            st.rerun()
        if wafer and wafer != current_wafer:
            optical = find_wafer(tool, wafer).optical
            if optical is not None and optical.is_dir():
                picked = str(optical)
            elif optical is not None:
                st.warning(f"{wafer} has only `{optical.name}`, not a sample folder.")
            else:
                st.warning(f"No optical folder found for {wafer}.")

    col_pick, col_path = st.columns([1, 3])
    with col_pick:
        pick_clicked = st.button("Select Sample Folder", icon=":material/folder_open:",
                                 width="stretch")
    with col_path:
        st.caption(state["folder"] or "No folder selected yet.")

    if pick_clicked:
        browse_from = state["folder"] or (str(tool.subfolder(OPTICAL) or "") if tool else "")
        try:
            picked = prompt_folder_path(default_dir=browse_from)
        except Exception as e:
            st.error(f"Failed to open folder browser: {e}")
            picked = None

    if picked:
        _scan = scan_sample_folder(picked)
        select_folder(state, picked, _scan, default_magnification(_scan))
        st.rerun()

scan, _dropped_points = grid_points_only(state["scan"])

if scan is not None:
    with sample_card:
        if _dropped_points:
            st.caption(f"Point(s) {', '.join(map(str, _dropped_points))} left out: the QC grid is 3 × 3, "
                       f"points 1–{GRID_POINTS}.")
        n_images = sum(len(v) for v in scan.image_files.values())
        st.success(
            f"**{scan.sample_name}** — {len(scan.raman_files)} Raman, "
            f"{len(scan.pl_files)} PL, {n_images} image file(s)"
        )

        if scan.ignored_files:
            with st.expander(f"{len(scan.ignored_files)} file(s) ignored (didn't match naming pattern)"):
                st.write(", ".join(scan.ignored_files))

    # -------------------------------------------------------- Magnification
    with card("magnification"):
        st.markdown("#### 2 · OM magnification")
        magnifications = scan.magnifications()
        if magnifications:
            current_mag = state["magnification"] if state["magnification"] in magnifications else magnifications[0]
            chosen_mag = st.selectbox(
                "Magnification group", options=magnifications, index=magnifications.index(current_mag)
            )
            if chosen_mag != state["magnification"]:
                # Every optical artifact — and the summary page, whose grid caption
                # names the magnification — was built from the other group's nine
                # frames. Without this the page keeps showing and saving 50x images
                # under a heading that says 100x.
                state["magnification"] = chosen_mag
                reset_optical_results(state)
        else:
            st.caption("No OM images found in this folder.")

    # ---------------------------------------------------------------- Layer
    with card("layer"):
        st.markdown("#### 3 · Film layer")
        # Suggest a layer from the frames' colour whenever the frames or the
        # material change (layer_suggest). The material is resolved the way
        # section 4 resolves it, since that selector renders below this one.
        # An operator's choice is never overwritten; a confident suggestion
        # pre-fills the selector; an unsure one leaves it empty and the page
        # stops below until a layer is chosen. A material with no calibration
        # keeps the plain 2L default.
        material_now = state["material"] or load_default_material()
        layer_paths = tuple(
            str(path) for point, path in sorted(
                scan.image_files.get(state["magnification"] or "", {}).items()
            ) if point >= 1
        )
        layer_context = (state["folder"], state["magnification"], material_now)
        if state.get("layer_context") != layer_context:
            state["layer_context"] = layer_context
            suggestion = None
            if layer_paths and calibration_for(material_now) is not None:
                with st.spinner("Reading the film's colour to suggest a layer..."):
                    try:
                        suggestion = _suggest_layer(
                            layer_paths,
                            tuple(os.path.getmtime(p) for p in layer_paths),
                            material_now,
                        )
                    except Exception as e:  # a suggestion is never worth stopping the page
                        st.caption(f"Layer suggestion unavailable: {e}")
            state["layer_suggestion"] = suggestion
            if state.get("layer_source") != "operator":
                # A material whose optical block names a single layer (MoS2_QC: 1L
                # only, monolayer by process) has nothing to choose: use it.
                only_layer = _single_optical_layer(material_now)
                if only_layer is not None and calibration_for(material_now) is None:
                    new_layer, state["layer_source"] = only_layer, "preset"
                elif suggestion is not None:
                    new_layer, state["layer_source"] = suggestion.layer, "suggestion"
                elif state.get("layer_source") in ("suggestion", "preset"):
                    new_layer, state["layer_source"] = "2L", None
                else:
                    new_layer = state["reference_layer"]
                if new_layer != state["reference_layer"]:
                    state["reference_layer"] = new_layer
                    # Everything, not just optical: the layer also picks the
                    # Raman/PL stats figures' spec lines and hides B2g on 1L.
                    reset_results(state)

        current_layer = state["reference_layer"]
        reference = st.selectbox(
            "The film being measured",
            options=REFERENCE_CHOICES,
            index=REFERENCE_CHOICES.index(current_layer) if current_layer in REFERENCE_CHOICES else None,
            placeholder="Choose 1L or 2L",
            # layer_word() is presentation only; "1L"/"2L" is what gets stored and
            # what keys the preset's optical block.
            format_func=lambda r: f"{r} — {layer_word(r).lower()} film",
        )
        if reference is not None and reference != current_layer:
            state["reference_layer"] = reference
            state["layer_source"] = "operator"
            reset_results(state)

        if state.get("layer_suggestion") is not None:
            st.caption(_layer_suggestion_caption(state["layer_suggestion"], reference))
        if reference is None:
            st.warning(
                "The colour doesn't say clearly which layer this film is. Choose 1L "
                "or 2L above to continue — a wrong choice relabels every class by "
                "one layer and selects a different tuning block."
            )
            st.stop()

        # classify() always treats the frame's most common pixel value as "the
        # reference" -- reference (above) never reaches the threshold math, it
        # only names the result. For a sparse-nucleation sample, bare substrate
        # is that majority, not the film, so the classes come out mislabelled
        # unless this says so.
        low_coverage = st.checkbox(
            f"Low coverage — most of the frame is substrate, not "
            f"{layer_word(reference).lower()}",
            value=state["low_coverage"],
            help="Relabels the reference class \"Substrate\" and the brighter "
                 "class as the named film itself, instead of \"Above "
                 f"{reference}\" over what would really be substrate. Still two "
                 "populations underneath -- doesn't separate a thicker layer "
                 "from the named one if both sit above substrate in the same "
                 "frame.",
        )
        if low_coverage != state["low_coverage"]:
            state["low_coverage"] = low_coverage
            reset_optical_results(state)

        # Worth stating plainly: with identical tuning the segmentation is the same
        # either way, so a wrong choice here relabels every class by one layer with
        # no visible symptom in the figure.
        if low_coverage:
            st.caption(
                f"Classes will be labelled **Below Substrate**, **Substrate**, "
                f"**{layer_word(reference)}** — the algorithm's reference is "
                f"still the frame's majority population, now interpreted as "
                f"substrate rather than {layer_word(reference).lower()}."
            )
        else:
            st.caption(
                f"Classes will be labelled **Below {reference}**, "
                f"**{layer_word(reference)}**, **Above {reference}**. "
                "The algorithm cannot infer this — a wrong choice mislabels every class "
                "by one layer, and selects a different tuning block."
            )

    # ------------------------------------------------------------- Material
    with card("material"):
        st.markdown("#### 4 · Material")
        presets = load_presets()
        materials = sorted(name for name, p in presets.items() if p.enabled)

        if not materials:
            st.warning("No materials configured yet. Add one on the **Material Presets** page.")
        else:
            default_material = load_default_material()
            current_material = state["material"] if state["material"] in materials else (
                default_material if default_material in materials else materials[0]
            )
            material_selection = st.selectbox(
                "Material", options=materials, index=materials.index(current_material)
            )
            if material_selection != state["material"]:
                state["material"] = material_selection
                save_default_material(material_selection)
                reset_results(state)
                # Section 3 rendered above with the old material; rerun so its
                # layer suggestion is made for this one.
                st.rerun()

            selected = presets.get(state["material"])
            raman_preset = selected if (selected and selected.block_for("Raman")) else None
            pl_preset = selected if (selected and selected.block_for("PL")) else None

            # Optical tuning for the chosen layer. Never None: an untuned layer
            # runs contrast.py's own defaults, which is a legitimate state and one
            # the operator should be told about rather than blocked by.
            optical = selected.optical_for(reference) if selected else None
            if selected is not None:
                settings = optical.as_kwargs()
                if reference in selected.optical and settings:
                    st.caption(
                        f"Optical tuning from **{state['material']} / {reference}**: "
                        + ", ".join(f"`{k}`={v}" for k, v in sorted(settings.items()))
                    )
                else:
                    st.caption(
                        f"**{state['material']}** has no optical tuning for "
                        f"{layer_word(reference).lower()} films — running the "
                        f"algorithm's own defaults. Add a block on the "
                        f"**Material Presets** page to tune it."
                    )

            # Domain-size measurement re-runs a segmentation-equivalent pass per
            # frame plus watershed splitting on top -- progress.py's own weighting
            # rates it as costing as much as segmentation itself, so it is opt-in
            # rather than always-on, and remembered like the material choice above.
            default_measure_domain_size = load_measure_domain_size()
            domain_size_allowed = not (optical is not None and optical.domain_size_enabled is False)
            if domain_size_allowed:
                measure_domain_size = st.checkbox(
                    "Measure domain size (adds a watershed segmentation pass, "
                    "roughly doubling optical analysis time)",
                    value=(
                        state["measure_domain_size"]
                        if state["measure_domain_size"] is not None
                        else default_measure_domain_size
                    ),
                )
            else:
                # The preset says this film has no domains (continuous by
                # process); don't offer a measurement of nothing.
                measure_domain_size = False
                st.caption(f"Domain size is off for **{state['material']}** (its optical preset).")
            if measure_domain_size != state["measure_domain_size"]:
                state["measure_domain_size"] = measure_domain_size
                if domain_size_allowed:
                    save_measure_domain_size(measure_domain_size)
                reset_optical_results(state)

            # Which of classify()'s three classes counts as "a domain" -- the
            # same three the OM class table already names, so the wording here
            # always matches it and updates automatically if the reference layer
            # changes. Shares that reference layer rather than an independent
            # one: a second choice of layer would mean a second, full
            # segmentation-equivalent pass just for domain size.
            domain_target_label = dict(
                zip(TARGETS, class_labels(reference, state["low_coverage"]))
            )
            if measure_domain_size:
                domain_target = st.radio(
                    "Which class is the domain?",
                    options=TARGETS,
                    index=TARGETS.index(state["domain_target"]),
                    format_func=lambda t: domain_target_label[t],
                    horizontal=True,
                )
                if domain_target != state["domain_target"]:
                    state["domain_target"] = domain_target
                    reset_optical_results(state)

                # Off by default and remembered per installation, like domain-size
                # measurement itself -- the split has fired on frames with no real
                # second population at all (pure background texture), so an
                # operator who hasn't asked for it should see the plain pooled
                # measurement, not a split that may not be real.
                default_split_populations = load_split_size_populations()
                split_size_populations = st.checkbox(
                    "Split into small/large size populations",
                    value=(
                        state["split_size_populations"]
                        if state["split_size_populations"] is not None
                        else default_split_populations
                    ),
                    help="Tests whether the domains split into a small and a "
                         "large size population (at least a 2x gap in a KDE of "
                         "the diameters), rather than reporting one pooled mean.",
                )
                if split_size_populations != state["split_size_populations"]:
                    state["split_size_populations"] = split_size_populations
                    save_split_size_populations(split_size_populations)
                    reset_optical_results(state)

            # Domain-size calibration is keyed by magnification alone, not by
            # material/layer like the optical tuning above — a 50x objective has
            # the same field of view regardless of what film is under it.
            um_per_px = get_um_per_px(load_om_calibration(), state["magnification"])
            if measure_domain_size and state["magnification"]:
                if um_per_px is not None:
                    st.caption(
                        f"Domain size calibration for **{state['magnification']}**: "
                        f"`{um_per_px:g}` µm/px."
                    )
                else:
                    st.caption(
                        f"No µm/px calibration set for **{state['magnification']}** — "
                        f"domain sizes will be reported in pixels. Set one on the "
                        f"**Material Presets** page."
                    )

            # Drop only what a preset edit invalidates. Editing a Raman peak must
            # not discard a 30-second OM segmentation, and vice versa.
            _optical_now = optical_fingerprint(selected, reference)
            _raman_now = technique_fingerprint(selected, "Raman")
            _pl_now = technique_fingerprint(selected, "PL")
            if state.get("om_png") and state.get("optical_fingerprint") != _optical_now:
                reset_optical_results(state)
                st.info("The optical settings changed — re-run to rebuild the OM figures.")
            # Named separately so the message points at the block that moved, even
            # though both techniques re-fit together: one batch call produces them.
            #
            # Gated on the fingerprint, not on a figure. A run against the wrong
            # material fits everything and loses it all at the quality gate, so it
            # has no stats figure — but it does have a fitted-spectra grid, a
            # summary page and a workbook. Keying the check on the figure meant
            # exactly that run, the one whose whole point is that the operator goes
            # and fixes the preset, never noticed the fix.
            _stale = []
            if state.get("raman_fingerprint") is not None and state["raman_fingerprint"] != _raman_now:
                _stale.append("Raman")
            if state.get("pl_fingerprint") is not None and state["pl_fingerprint"] != _pl_now:
                _stale.append("PL")
            if _stale:
                reset_spectra_results(state)
                st.info(
                    f"The {' and '.join(_stale)} settings changed — re-run to rebuild "
                    "the fitted figures. Both techniques re-fit together: one batch "
                    "pass produces them."
                )

    # ------------------------------------------------------------- Inventory
    # Its own card, outside the material one; `if materials` is the `else` of
    # the material card's `if not materials`, so it runs exactly as it did.
    if materials:
        with card("run"):
            st.markdown("#### 5 · Run")
            om_paths = {
                point: path
                for point, path in scan.image_files.get(state["magnification"] or "", {}).items()
                # `scan` is already limited to 1..GRID_POINTS (`grid_points_only`).
                if point >= 1
            }

            # Said before the run, not after: a folder whose PL files are named
            # `PL1.txt` instead of `PL_1.txt` scans as a sample with no PL, and is
            # indistinguishable from one that genuinely has none once the report is
            # built. The two causes below get two different sentences because they
            # are fixed in two different places.
            st.markdown(
                f"**Found:** {len(om_paths)} OM "
                f"({state['magnification'] or 'no magnification'}) · "
                f"{len(scan.raman_files)} Raman · {len(scan.pl_files)} PL"
            )
            for label, files, preset in (
                ("Raman", scan.raman_files, raman_preset),
                ("PL", scan.pl_files, pl_preset),
            ):
                if not files:
                    st.caption(
                        f"↳ No {label} files in this folder — the {label} figures and "
                        f"sheet will be skipped. Check the file naming if you expected some."
                    )
                elif preset is None:
                    st.caption(
                        f"↳ **{state['material']}** defines no {label} peaks, so its "
                        f"{len(files)} {label} file(s) will not be fitted. Add a "
                        f"{label} block on the **Material Presets** page."
                    )
            if not om_paths:
                st.caption(
                    "↳ No OM images for this magnification — the OM figures and sheets "
                    "will be skipped, and the summary page's image grid will be empty."
                )

            will_fit_raman = bool(scan.raman_files and raman_preset)
            will_fit_pl = bool(scan.pl_files and pl_preset)
            has_segmentation = bool(om_paths) and selected is not None
            has_content = bool(om_paths) or will_fit_raman or will_fit_pl

            if not has_content:
                st.warning(
                    "Nothing to analyse: this folder yielded no OM images for the "
                    "selected magnification, and no spectra this material can fit."
                )

            if st.button("Generate QC Report", type="primary", icon=":material/play_arrow:",
                         width="stretch", disabled=not has_content):
                reset_results(state)
                try:
                    # One status for the whole build. The `with` form is
                    # load-bearing: on the way out it resolves the status to
                    # complete, or to error if the body raised. A bare handle would
                    # leave it spinning forever on a failure, which is exactly the
                    # "did it hang?" the progress is here to answer.
                    with st.status("Generating QC report...", expanded=True) as status:
                        progress_bar = st.progress(0.0, text="Starting...")

                        def _report(fraction, message):
                            progress_bar.progress(fraction, text=message)
                            status.update(label=message)

                        # Counting columns costs ~2 ms per file and is what keeps the
                        # bar proportional: a folder whose files hold 25 spectra each
                        # spends most of the run fitting, against the share the stage
                        # weights reserve for a one-spectrum-per-point sample.
                        fit_files = (
                            (list(scan.raman_files.values()) if will_fit_raman else [])
                            + (list(scan.pl_files.values()) if will_fit_pl else [])
                        )
                        progress = build_progress(
                            _report,
                            has_raman=will_fit_raman,
                            has_pl=will_fit_pl,
                            has_optical=bool(om_paths),
                            has_segmentation=has_segmentation,
                            has_domain_size=state["measure_domain_size"],
                            fit_spectra=sum(count_spectra(path) for path in fit_files),
                        )

                        # The pipeline itself is modules.qc_report (v5.35.0); this
                        # page resolves the inputs, shows the warnings, and stores
                        # what comes back under the same state keys.
                        result = build_qc_report(
                            QcReportInputs(
                                scan=scan,
                                material=state["material"],
                                magnification=state["magnification"],
                                reference_layer=state["reference_layer"],
                                om_paths=om_paths,
                                optical=optical if has_segmentation else None,
                                raman_preset=raman_preset if will_fit_raman else None,
                                pl_preset=pl_preset if will_fit_pl else None,
                                low_coverage=state["low_coverage"],
                                measure_domain_size=bool(state["measure_domain_size"]),
                                domain_target=state["domain_target"],
                                split_size_populations=bool(state["split_size_populations"]),
                                um_per_px=um_per_px,
                            ),
                            progress,
                            warn=st.warning,
                        )
                        state.update(result)

                        # Stamped on *attempt*, not on success. A run that fitted
                        # everything and lost it all at the quality gate still
                        # produced artifacts from these preset blocks, and is the
                        # run most likely to be followed by a preset edit — so it
                        # is the one that most needs the staleness check to fire.
                        if state.get("om_png"):
                            state["optical_fingerprint"] = _optical_now
                        if will_fit_raman:
                            state["raman_fingerprint"] = _raman_now
                        if will_fit_pl:
                            state["pl_fingerprint"] = _pl_now
                        progress.finish(f"QC report generated for {scan.sample_name}.")

                        status.update(
                            label=f"QC report generated for {scan.sample_name}.",
                            state="complete",
                            expanded=False,
                        )
                except Exception as e:
                    st.error(f"Report generation failed: {e}")


# ---------------------------------------------------------------- Results
def _render_gate_verdict(technique: str, dropped_key: str, fitted_key: str,
                         figure_key: str) -> None:
    """Say what the quality gate did to one technique, on every rerun.

    Spectra dropped here are not fit failures, so they never reach the "failed
    to fit" list; they just leave, and `n` comes out smaller. When *every*
    spectrum is dropped there is no figure either, and a count on its own reads
    as a malfunction rather than as the verdict it is. The usual cause of a
    wholesale drop is a preset aimed at another material — MoS2's 383/408 cm-1
    peaks on WSe2 data converge onto empty spectrum and score about -0.11
    apiece — so name the selected material and let the operator check it.

    One verdict per technique and no combined grade: PL carries a single spec
    line and no centre or ratio spec, so an overall pass/fail would assert a
    judgment the data cannot support.
    """
    dropped = state.get(dropped_key) or 0
    if not dropped:
        return
    fitted = state.get(fitted_key) or 0
    if state.get(figure_key):
        st.caption(
            f"**{technique}:** {dropped} of {fitted} spectra fitted but scored "
            f"R² ≤ {R_SQUARED_MIN} and were excluded from the {technique} "
            f"figure and statistics."
        )
    else:
        st.warning(
            f"**No {technique} stats figure:** all {fitted} spectra fitted, and "
            f"every one scored R² ≤ {R_SQUARED_MIN}, so none reached the figure. "
            f"The usual cause is the wrong material — check that "
            f"**{state.get('material')}** is what this sample is."
        )


def _render_selection_note(technique: str, selection_key: str) -> None:
    """Say how a technique's maps were cut down to SPECTRA_PER_POINT a point."""
    sel = state.get(selection_key)
    if not sel or not sel.points_reduced:
        return
    st.caption(
        f"**{technique}:** {sel.fitted} spectra fitted; at the {sel.points_reduced} point(s) "
        f"with more than {SPECTRA_PER_POINT}, {sel.outliers} outlier(s) were removed "
        f"(R² ≤ {R_SQUARED_MIN}, or {', '.join(sel.main_peaks) or 'the main peak'} outside "
        f"the point's 1.5×IQR fence on centre, FWHM or intensity) and {sel.unused} more "
        f"left out at random, leaving {sel.selected} — up to {SPECTRA_PER_POINT} per point — "
        f"in every figure, table and the workbook."
    )


def _render_convergence_note(technique: str, unconverged_key: str, fitted_key: str) -> None:
    """Say how many of one technique's fits stopped on the preset's iteration budget.

    Those fits are in every table and figure -- their peaks are the best the
    optimiser found -- but they are not an optimum and carry no uncertainties,
    and until v5.21.0 nothing distinguished them from a finished fit. The
    workbook's `Converged` column carries the same flag per row.
    """
    unconverged = state.get(unconverged_key) or 0
    if not unconverged:
        return
    fitted = state.get(fitted_key) or 0
    st.warning(
        f"**{technique}:** {unconverged} of {fitted} fits stopped at the preset's "
        f"max_iterations before converging. Their numbers are included but are "
        f"the best found so far, not an optimum, with no uncertainties. The "
        f"workbook's `Converged` column marks them; raise max_iterations in the "
        f"**{state.get('material')}** preset to finish them."
    )


_HAS_RESULTS = any(state.get(key) for _, _, key in REPORT_FIGURES) or state.get("raman_dropped")

if _HAS_RESULTS:
    # The OM class coverage leads the results, above the figures it summarises.
    frames = (state.get("frames") or []) if state.get("om_png") else []
    if frames:
        with card("coverage"):
            st.markdown("#### OM coverage")
            cols = st.columns(3)
            for col, label, (mean, std) in zip(cols, frames[0].labels, class_summary(frames)):
                col.metric(label, f"{mean:.1f} %", f"± {std:.1f} %", delta_color="off")

    # Inline: the overview and the two OM figures — the ones you look at every
    # run. The fitted spectra and the stats panels go behind expanders: they
    # are what you open when something looks wrong, and eight figures of this
    # size stacked would bury the summary under a page of scrolling. Tabs would
    # hide the OM grid well enough to miss a bad one by never clicking.
    with card("figures"):
        st.markdown("#### Figures")
        if state.get("summary_png"):
            st.image(state["summary_png"], caption="1 — Summary", width="stretch")

        if state.get("om_png"):
            st.image(state["om_png"], caption="2 — OM analysis", width="stretch")

        if state.get("om_diagnostic_png"):
            # Collapsed: this copy is for deciding whether to trust the one above,
            # which is a question you ask only when something looks off.
            with st.expander("3 — OM diagnostic (green-channel histograms)"):
                st.caption(
                    "Each frame's histogram with its mode and both thresholds marked. "
                    "Coverage percentages alone cannot show whether a frame segmented "
                    "sensibly; a saturated frame reads as a clean wafer in the overlay "
                    "and as a single spike here. Internal review only — the clean copy "
                    "above is the one a report carries."
                )
                st.image(state["om_diagnostic_png"], width="stretch")

        if state.get("domain_grid_png"):
            with st.expander("4 — Domain size analysis"):
                stat = state.get("domain_stat")
                class_label = dict(zip(TARGETS, class_labels(
                    state["reference_layer"], state["low_coverage"]
                )))[state["domain_target"]]
                if stat is not None:
                    if stat.populations:
                        small, large = stat.populations
                        population_line = (
                            f"small: n={small.n}, {small.diameter_mean:.2f} ± "
                            f"{small.diameter_std:.2f} {stat.unit}  \n"
                            f"large: n={large.n}, {large.diameter_mean:.2f} ± "
                            f"{large.diameter_std:.2f} {stat.unit}"
                        )
                        if small.density_per_100um2 is not None:
                            population_line += (
                                f"  \nsmall: {small.density_per_100um2:.1f}/(100µm)² "
                                f"· large: {large.density_per_100um2:.1f}/(100µm)²"
                            )
                    else:
                        population_line = (
                            f"median {stat.diameter_median:.2f} {stat.unit} "
                            f"· mean {stat.diameter_mean:.2f} ± {stat.diameter_std:.2f} {stat.unit}"
                        )
                        if stat.density_per_100um2 is not None:
                            population_line += f"  \n{stat.density_per_100um2:.1f}/(100µm)²"
                    st.caption(
                        f"**{class_label}** — {stat.n_domains} domains"
                        + (f" (+ {stat.n_edge} at the frame edge, counted ½ in density)"
                           if stat.n_edge else "")
                        + f" across {stat.n_frames} frames "
                        f"· {population_line}"
                        + ("" if stat.calibrated else
                           " — no µm/px calibration set for this magnification; "
                           "sizes shown in pixels.")
                    )
                else:
                    st.caption(f"**{class_label}** — no domains found.")
                st.image(state["domain_grid_png"], width="stretch")

        for number, caption, key in (
            (5, "Raman — fitted spectra", "raman_grid_png"),
            (6, "Raman — quality panels", "raman_stats_png"),
            (7, "PL — fitted spectra", "pl_grid_png"),
            (8, "PL — quality panels", "pl_stats_png"),
        ):
            if state.get(key):
                with st.expander(f"{number} — {caption}"):
                    st.image(state[key], width="stretch")

    # What the run did to the data, not a figure: drawn as a card only when one
    # of the notes below has something to say, so a clean run shows no empty
    # box. Each condition mirrors its helper's own early return.
    errors = (state.get("raman_errors") or []) + (state.get("pl_errors") or [])
    _has_notes = errors or any(
        (state.get(key) and state[key].points_reduced)
        for key in ("raman_selection", "pl_selection")
    ) or any(
        state.get(key)
        for key in ("raman_dropped", "pl_dropped", "raman_unconverged", "pl_unconverged")
    )
    if _has_notes:
        with card("run-notes"):
            st.markdown("#### Run notes")
            _render_selection_note("Raman", "raman_selection")
            _render_selection_note("PL", "pl_selection")
            _render_gate_verdict("Raman", "raman_dropped", "raman_fitted", "raman_stats_png")
            _render_gate_verdict("PL", "pl_dropped", "pl_fitted", "pl_stats_png")
            _render_convergence_note("Raman", "raman_unconverged", "raman_fitted")
            _render_convergence_note("PL", "pl_unconverged", "pl_fitted")

            if errors:
                with st.expander(f"{len(errors)} point(s) failed to fit and were excluded"):
                    for point, message in errors[:50]:
                        st.error(f"Point {point}: {message}")

    # ------------------------------------------------------------------ Save
    with card("save"):
        st.markdown("#### Save report")
        st.caption(
            "One dialog, one name, one file per artifact. The figures are numbered "
            "in reading order — alphabetical sort would put the summary last — and "
            "every table lands in one `.xlsx` beside them: `Summary`, `Raman`, "
            "`PL`, `OM_Stats`, `OM_Points`."
        )
        if st.button("Save QC Report As...", icon=":material/save:", width="stretch"):
            try:
                save_path = prompt_save_path(
                    default_dir=state["folder"],
                    default_filename=f"{scan.sample_name}_QC_Report.png",
                    title="Save QC Report",
                    filetypes=(("PNG images", "*.png"), ("All files", "*.*")),
                    default_extension=".png",
                )
                if save_path:
                    # One dialog, many files: the chosen name supplies the stem and
                    # each artifact appends its own number, suffix and extension, so
                    # the set stays together and none can overwrite another. The
                    # extension the dialog collected is dropped — it only ever named
                    # the first image, and the workbook is not a PNG.
                    base, _ext = os.path.splitext(save_path)
                    saved_files = save_report(state, base)

                    st.success("Saved:\n" + "\n".join(f"- {p}" for p in saved_files))
                # save_path is None => user cancelled => no-op
            except Exception as e:
                st.error(f"Failed to save: {e}")
