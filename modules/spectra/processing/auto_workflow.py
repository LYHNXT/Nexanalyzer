"""
Automated workflow execution engine.

This module orchestrates the complete processing pipeline:
X-range → De-spike → Baseline → Peak Fitting
"""

import dataclasses

import streamlit as st
import numpy as np
from typing import Dict, Any
from ..models.spectrum import SpectrumFile, SpectrumData
from ..models.preset import MaterialPreset, parse_exclusion_ranges
from .despiking import remove_spikes, remove_spikes_local
from .baseline import (
    baseline_polynomial,
    baseline_als,
    baseline_polynomial_with_mask,
    baseline_als_with_mask
)
from .fitting import fit_voigt_peaks
from .zero_line import laser_line_offset

#: No PL axis in nanometres reaches this. A PL file whose axis median is above
#: it was exported as Raman shift (cm-1): HADH08/20/22/23 and VBBE68 (2026-08/09)
#: run 3876-9558, and were fitted as nm until v5.37.0 -- the QC Report printed a
#: Raw PL centre of ~7,300 "nm" and Exciton/Trion pinned to the axis start.
PL_MAX_NM = 2000.0
from ..models.peak import FitResult
from ..utils.fit_staleness import mark_fit_stale_if_needed, compute_preprocessing_hash
from ..ui.session_state import set_view_options


class WorkflowExecutionError(Exception):
    """Custom exception for workflow execution failures."""
    pass


def execute_auto_workflow(
    spectrum: SpectrumFile,
    preset: MaterialPreset,
) -> Dict[str, Any]:
    """
    Execute full automated workflow using preset.

    Pipeline: X-range → Despike → Baseline → Fitting

    Parameters
    ----------
    spectrum : SpectrumFile
        Current spectrum file to process
    preset : MaterialPreset
        Preset configuration. The block for `spectrum.mode` supplies every
        processing setting, the fit iteration budget included — since v5.2.0
        there is no caller-side override, so the preset alone determines what
        the fit does.

    Returns
    -------
    result : dict
        Execution results with keys:
        - success: bool
        - stage_completed: str (last completed stage)
        - error_message: str (if success=False)
        - fit_result: FitResult (if success=True)

    Notes
    -----
    Modifies spectrum object in-place. Stages before failure remain completed.
    """
    result = {
        "success": False,
        "stage_completed": None,
        "error_message": "",
        "fit_result": None
    }

    try:
        # The spectrum's own mode picks the block. A preset no longer states a
        # technique -- one material holds a Raman block, a PL block or both --
        # so the direction of this sync is reversed from v3.x, where the preset
        # overwrote the spectrum's mode. Resolving the block here rather than in
        # each caller keeps every caller's signature unchanged.
        block = preset.block_for(spectrum.mode)
        if block is None:
            raise WorkflowExecutionError(
                f"'{preset.material_name}' has no {spectrum.mode} settings. "
                f"Add a {spectrum.mode} block to it on the Material Presets "
                f"page, or pick a material that has one."
            )

        # Start from the measurement, every time. Stage 2 reads
        # `processed_data`, so without this the pipeline ran on its own previous
        # output: a re-run despiked an already-despiked array and ALS-baselined
        # an already-baselined one. Editing a preset and pressing Run again then
        # applied the new setting to the *residual of the old one* rather than
        # to the spectrum, which is how a changed preset could look like it had
        # no effect. Three symptoms, one cause:
        #   - x-range could only ever narrow, because stage 1 overwrote
        #     `raw_data` with the crop and then cropped that again next time;
        #   - switching baseline to "None (Skip)" left the previous ALS
        #     baseline subtracted, since skipping only declines to subtract a
        #     *new* one;
        #   - even re-running an unedited preset drifted the fitted areas by a
        #     few percent per run.
        # `reset_to_raw` restores from `original_data`, which no stage writes,
        # and clears the flags and fit that belong to the superseded run. It is
        # the same call the sidebar's "Reset to Raw" button makes -- operators
        # previously had to know to press it first.
        #
        # After the block check, not before: a material with no block for this
        # file's technique cannot run, and discarding a good fit on the way to
        # saying so would make picking the wrong material in the dropdown
        # destructive.
        spectrum.reset_to_raw()

        # A PL spectrum is fitted in nm. One exported as Raman shift is refused
        # rather than fitted: converting needs the excitation wavelength, which
        # the file does not carry, and a guessed one moves every centre.
        if spectrum.mode == "PL" and len(spectrum.raw_data.X) and                 float(np.median(spectrum.raw_data.X)) > PL_MAX_NM:
            raise WorkflowExecutionError(
                f"This PL file's axis runs {float(np.min(spectrum.raw_data.X)):.0f}-"
                f"{float(np.max(spectrum.raw_data.X)):.0f}, which is Raman shift (cm-1), "
                f"not wavelength (nm). Re-export the PL spectrum in nm."
            )

        # ========== STAGE 0: ZERO-LINE CALIBRATION (Raman, opt-in) ==========
        # Before the crop, so an x-range and fit windows apply to the corrected
        # axis. original_data is never touched, so a re-run starts from the
        # recorded axis and cannot shift twice.
        result["zero_offset"] = None
        if block.zero_line_calibration and spectrum.mode == "Raman":
            offset = laser_line_offset(spectrum.raw_data.X, spectrum.raw_data.Y)
            if offset is not None:
                shifted = SpectrumData(X=spectrum.raw_data.X - offset, Y=spectrum.raw_data.Y.copy())
                spectrum.raw_data = shifted
                spectrum.processed_data = SpectrumData(X=shifted.X.copy(), Y=shifted.Y.copy())
                spectrum.zero_offset = offset
                result["zero_offset"] = offset

        # ========== STAGE 1: X-RANGE CROPPING ==========
        # Match manual workflow: replicate what happens when user clicks "Apply X-Range"
        if block.x_range_enabled:
            # Use raw_data as source (NOT original_data - matches manual workflow)
            X_raw = spectrum.raw_data.X
            Y_raw = spectrum.raw_data.Y

            # Apply mask
            mask = (X_raw >= block.x_min) & (X_raw <= block.x_max)
            if not np.any(mask):
                raise WorkflowExecutionError(
                    f"No data in X-range [{block.x_min}, {block.x_max}]. "
                    f"File range: [{X_raw.min():.1f}, {X_raw.max():.1f}]. "
                    f"Suggestion: Adjust x_min/x_max in preset to match your data."
                )

            X_cropped = X_raw[mask]
            Y_cropped = Y_raw[mask]

            # Update BOTH raw_data and processed_data (matches manual workflow)
            spectrum.raw_data = SpectrumData(X=X_cropped, Y=Y_cropped)
            spectrum.processed_data = SpectrumData(X=X_cropped, Y=Y_cropped)
            spectrum.background = None

            # Reset X-range flags (matches manual workflow)
            spectrum.x_range_enabled = False
            spectrum.x_min = None
            spectrum.x_max = None

            # Reset downstream flags (matches manual workflow)
            spectrum.despike_done = False
            spectrum.baseline_done = False
            spectrum.fit_done = False
            spectrum.processing_settings.despike_applied = False
            spectrum.processing_settings.baseline_applied = False

            # Clear preview states (matches manual workflow)
            if 'despike_preview' in st.session_state:
                st.session_state['despike_preview'] = None
            if 'baseline_preview' in st.session_state:
                st.session_state['baseline_preview'] = None

            # Update view options (matches manual workflow). Via
            # set_view_options so a checkbox the user set by hand is not
            # reset out from under them by a re-run.
            set_view_options({
                'show_raw': True,
                'show_despiked': False,
                'show_corrected': False,
                'show_fit': False,
                'show_components': False,
            })

            # Auto-expand next section (matches manual workflow)
            st.session_state['expanded_section'] = 'despiking'

        result["stage_completed"] = "x_range"

        # ========== STAGE 2: DE-SPIKING ==========
        # Match manual workflow: replicate what happens when user clicks "Run Despike"
        X = spectrum.processed_data.X
        Y = spectrum.processed_data.Y

        # Save threshold to settings (matches manual workflow)
        spectrum.processing_settings.despike_threshold = block.despike_threshold

        # PL by its steps (v5.41.0): the whole-spectrum score flagged the PL
        # band itself. Raman keeps `remove_spikes`, which on the golden Raman
        # changes nothing inside the fit window. See remove_spikes_local.
        despike = remove_spikes_local if spectrum.mode == "PL" else remove_spikes
        Y_despiked, spike_mask = despike(Y, threshold=block.despike_threshold)

        # Update processed_data (X unchanged, Y updated - matches manual workflow)
        spectrum.processed_data = SpectrumData(X=X, Y=Y_despiked)
        spectrum.background = None

        # Set flags (matches manual workflow)
        spectrum.processing_settings.despike_applied = True
        spectrum.despike_done = True

        # Mark fit as stale if it exists (matches manual workflow)
        mark_fit_stale_if_needed(spectrum)

        # Clear preview state (matches manual workflow)
        if 'despike_preview' in st.session_state:
            st.session_state['despike_preview'] = None

        # Update view options (matches manual workflow)
        set_view_options({
            'show_raw': True,
            'show_despiked': True,  # Show despiked for comparison
            'show_corrected': False,
            'show_fit': False,
            'show_components': False,
        })

        # Auto-expand next section (matches manual workflow)
        st.session_state['expanded_section'] = 'baseline'

        result["stage_completed"] = "despike"

        # ========== STAGE 3: BASELINE CORRECTION ==========
        # Match manual workflow: replicate what happens when user clicks "Run Baseline Correction"
        result["stage_completed"] = "baseline"

        if block.fit_windows:
            # Each fit window is baseline-corrected on its own in stage 4, on
            # its own stretch of the despiked spectrum; nothing to do here.
            pass
        elif block.baseline_algorithm == "None (Skip)":
            # Skip baseline, mark as done
            spectrum.processing_settings.baseline_applied = False
            spectrum.baseline_done = True
            spectrum.background = None
        else:
            X = spectrum.processed_data.X
            Y_despiked = spectrum.processed_data.Y

            Y_corrected, baseline_curve, y_shift = _subtract_baseline(
                X, Y_despiked, block.baseline_algorithm, block.baseline_degree,
                block.baseline_lambda, block.baseline_p, block.exclusion_ranges,
            )

            # Update processing_settings (matches manual workflow)
            spectrum.processing_settings.baseline_algorithm = block.baseline_algorithm
            if block.baseline_algorithm == "Polynomial":
                spectrum.processing_settings.baseline_degree = block.baseline_degree if block.baseline_degree else 3
            else:  # ALS
                spectrum.processing_settings.baseline_lambda = block.baseline_lambda if block.baseline_lambda else 10000.0
                spectrum.processing_settings.baseline_p = block.baseline_p if block.baseline_p else 0.001

            # Save Y-shift amount (matches manual workflow)
            spectrum.processing_settings.y_shift = y_shift

            # Update processed_data (matches manual workflow)
            spectrum.processed_data = SpectrumData(X=X, Y=Y_corrected)
            # Kept, not discarded, so the fit can be drawn on top of it. In
            # original scale: every baseline function returns it un-shifted,
            # so Y_despiked == Y_corrected + baseline_curve.
            spectrum.background = np.asarray(baseline_curve, dtype=np.float64)

            # Set flags (matches manual workflow)
            spectrum.processing_settings.baseline_applied = True
            spectrum.baseline_done = True

            # Mark fit as stale if it exists (matches manual workflow)
            mark_fit_stale_if_needed(spectrum)

            # Clear preview state (matches manual workflow)
            if 'baseline_preview' in st.session_state:
                st.session_state['baseline_preview'] = None

        # Update view options (matches manual workflow)
        set_view_options({
            'show_raw': False,
            'show_despiked': False,
            'show_corrected': True,  # Show baseline-corrected
            'show_fit': False,
            'show_components': False,
        })

        # Auto-expand next section (matches manual workflow)
        st.session_state['expanded_section'] = 'peak_fitting'

        # ========== STAGE 4: PEAK FITTING ==========
        if block.fit_windows:
            fit_result = _fit_in_windows(spectrum, block)
            spectrum.processing_settings.baseline_algorithm = block.baseline_algorithm
            spectrum.processing_settings.baseline_applied = any(
                w.resolved(block)["baseline_algorithm"] != "None (Skip)" for w in block.fit_windows
            )
            spectrum.processing_settings.y_shift = 0.0
            spectrum.baseline_done = True
        else:
            fit_result = _fit_whole(spectrum, block)

        if not fit_result.success:
            raise WorkflowExecutionError(
                f"Peak fitting did not converge: {fit_result.error_message}. "
                f"Suggestion: Adjust peak centers or tolerances in preset."
            )

        # Save result (matches manual workflow)
        spectrum.fit_result = fit_result

        # Set flags (matches manual workflow)
        spectrum.fit_done = True
        spectrum.fit_stale = False

        # Compute and save preprocessing hash (matches manual workflow)
        spectrum.last_preprocessing_hash = compute_preprocessing_hash(spectrum)

        # Clear preview states (matches manual workflow)
        if 'despike_preview' in st.session_state:
            st.session_state['despike_preview'] = None
        if 'baseline_preview' in st.session_state:
            st.session_state['baseline_preview'] = None

        # Update view options (matches manual workflow)
        set_view_options({
            'show_raw': False,
            'show_despiked': False,
            'show_corrected': True,       # Show baseline-corrected data
            'show_fit': True,             # Show fit total curve
            'show_components': True,      # Show peak components
            'show_residuals': True,       # Show residuals
            'show_on_background': True,   # Fit drawn on the subtracted baseline
        })

        # Auto-expand export section (matches manual workflow)
        st.session_state['expanded_section'] = 'export'

        result["stage_completed"] = "fitting"
        result["fit_result"] = fit_result
        result["success"] = True

        return result

    except WorkflowExecutionError as e:
        result["error_message"] = str(e)
        return result
    except Exception as e:
        result["error_message"] = f"Unexpected error at stage '{result['stage_completed']}': {str(e)}"
        return result


def _subtract_baseline(X, Y, algorithm, degree, lam, p, exclusion_str, window=False):
    """`(Y - baseline, baseline, y_shift)` for one stretch of spectrum; `y_shift`
    is always 0.0 since the no-op auto-shift was removed (v5.34.0).

    The one routing of a preset's baseline settings to an algorithm, shared by a
    whole-spectrum block and by each fit window. Every algorithm returns the
    baseline in the original scale, so `Y == corrected + baseline`.
    """
    exclusions = None
    if exclusion_str:
        try:
            exclusions = parse_exclusion_ranges(exclusion_str)
        except ValueError as e:
            raise WorkflowExecutionError(
                f"Invalid exclusion_ranges format: {e}. "
                f"Expected format: '1200-1400; 2600-2800'"
            )
        if window:
            # A range that misses this window entirely is not its concern: a
            # block's exclusions are inherited by every window. Whole-spectrum
            # fits keep every range, as they always have -- dropping them would
            # switch an all-outside list from the masked to the unmasked routine.
            exclusions = [(lo, hi) for lo, hi in exclusions if hi >= X.min() and lo <= X.max()] or None

    if algorithm == "None (Skip)":
        return Y.copy(), np.zeros_like(Y, dtype=np.float64), 0.0
    if algorithm == "Polynomial":
        if exclusions:
            corrected, baseline = baseline_polynomial_with_mask(X, Y, degree=degree, exclusions=exclusions)
            return corrected, baseline, 0.0
        corrected, baseline = baseline_polynomial(X, Y, degree=degree)
        return corrected, baseline, 0.0
    if algorithm == "ALS":
        if exclusions:
            corrected, baseline = baseline_als_with_mask(X, Y, lambda_=lam, p=p, exclusions=exclusions)
            return corrected, baseline, 0.0
        corrected, baseline = baseline_als(X, Y, lambda_=lam, p=p)
        return corrected, baseline, 0.0
    raise WorkflowExecutionError(
        f"Baseline algorithm '{algorithm}' not supported. "
        f"Supported: 'Polynomial', 'ALS', 'None (Skip)'. "
        f"Suggestion: Check baseline_algorithm spelling in preset."
    )


def _peak_definitions(templates, mode, x, y_corrected):
    """The block's templates as PeakDefinitions bounded against `x`."""
    x_range = (x.min(), x.max())
    y_max = y_corrected.max()
    spectral_resolution = np.median(np.abs(np.diff(x)))
    definitions = []
    for template in templates:
        try:
            definitions.append(template.to_peak_definition(
                mode=mode, x_range=x_range, y_max=y_max, spectral_resolution=spectral_resolution,
            ))
        except Exception as e:
            raise WorkflowExecutionError(
                f"Failed to convert peak template '{template.peak_label}': {e}"
            )
    return definitions


def _fit_whole(spectrum: SpectrumFile, block) -> FitResult:
    """One fit of every template over the whole processed spectrum (no fit windows)."""
    # Match manual workflow: replicate what happens when user clicks "Run Voigt Fit"
    X = spectrum.processed_data.X
    Y_corrected = spectrum.processed_data.Y
    peak_definitions = _peak_definitions(block.peak_templates, spectrum.mode, X, Y_corrected)

    # Update spectrum peak table (matches manual workflow)
    spectrum.peak_table = peak_definitions

    try:
        return fit_voigt_peaks(
            X, Y_corrected, peak_definitions,
            mode=spectrum.mode, max_iterations=block.max_iterations,
            starts=block.fit_starts, dominant=block.dominant_peak,
        )
    except Exception as e:
        raise WorkflowExecutionError(
            f"Peak fitting failed: {e}. "
            f"Suggestion: Check peak initial guesses (center, intensity, width) in preset."
        )


#: A window with fewer points than this cannot be baseline-corrected (ALS
#: needs 10) or fitted meaningfully.
_MIN_WINDOW_POINTS = 10


def _fit_in_windows(spectrum: SpectrumFile, block) -> FitResult:
    """Baseline and fit each of `block.fit_windows` on its own, then join them.

    Reads the despiked spectrum from `processed_data` and replaces it with the
    windows' baseline-corrected stretches side by side -- points outside every
    window are dropped -- with `background` holding each window's own baseline
    over the same points. The result is one `FitResult` over those points:

    - every peak's `component_curve` is its window's curve, zero in the other
      windows, so `peak_intensity` (the curve's maximum) is the window fit's;
    - `total_fit_curve` and `residuals` are each window's, side by side;
    - chi-squared is their sum, R-squared is over the joined data, and the fit
      is `converged` only if every window's fit converged.

    A window the spectrum does not reach (fewer than `_MIN_WINDOW_POINTS`
    points) is skipped and its peaks are absent from the result, as a peak a
    single-fit preset never had would be: a digitized reference curve covering
    200-330 cm-1 still gets its main band fitted. Only a spectrum that reaches
    no window at all fails.

    Peaks come back in the block's template order, so every table lists them as
    a single-fit block would.
    """
    X = spectrum.processed_data.X
    Y = spectrum.processed_data.Y
    windows = block.window_peaks()
    last = len(windows) - 1

    keep = np.zeros(len(X), dtype=bool)
    corrected = np.zeros(len(X), dtype=np.float64)
    background = np.zeros(len(X), dtype=np.float64)
    total = np.zeros(len(X), dtype=np.float64)
    peaks_by_label = {}
    definitions_by_label = {}
    convergence_time = 0.0
    converged = True
    nfev = 0
    max_nfev = 0
    skipped = []

    for i, (window, templates) in enumerate(windows):
        name = f"fit window {i + 1} ({window.x_min:g} to {window.x_max:g})"
        mask = window.contains(X, i == last)
        if mask.sum() < _MIN_WINDOW_POINTS:
            skipped.append(name)
            continue
        settings = window.resolved(block)
        xw = X[mask]
        yw_corrected, yw_background, _shift = _subtract_baseline(
            xw, Y[mask], settings["baseline_algorithm"], settings["baseline_degree"],
            settings["baseline_lambda"], settings["baseline_p"], settings["exclusion_ranges"],
            window=True,
        )
        definitions = _peak_definitions(templates, spectrum.mode, xw, yw_corrected)
        try:
            window_fit = fit_voigt_peaks(
                xw, yw_corrected, definitions,
                mode=spectrum.mode, max_iterations=block.max_iterations,
                starts=block.fit_starts, dominant=block.dominant_peak,
            )
        except Exception as e:
            raise WorkflowExecutionError(f"Peak fitting failed in {name}: {e}")
        if not window_fit.success:
            raise WorkflowExecutionError(f"In {name}: {window_fit.error_message}")

        keep |= mask
        corrected[mask] = yw_corrected
        background[mask] = yw_background
        total[mask] = window_fit.total_fit_curve
        for definition, peak in zip(definitions, window_fit.fitted_peaks):
            full = np.zeros(len(X), dtype=np.float64)
            full[mask] = peak.component_curve
            peaks_by_label[peak.label] = (peak, full)
            definitions_by_label[definition.label] = definition
        convergence_time += window_fit.convergence_time
        converged &= window_fit.converged
        nfev += window_fit.nfev or 0
        max_nfev += window_fit.max_nfev or 0

    if len(skipped) == len(windows):
        raise WorkflowExecutionError(
            f"The spectrum reaches none of the fit windows (X range [{X.min():.1f}, {X.max():.1f}]). "
            f"Suggestion: check the fit windows against this file."
        )
    if keep.sum() < 100:
        # SpectrumData's own floor; a display of fewer points is not a spectrum.
        raise WorkflowExecutionError(
            f"The fit windows hold only {int(keep.sum())} of this file's points (100 needed)."
        )

    order = [t.peak_label for t in block.peak_templates if t.peak_label in peaks_by_label]
    fitted_peaks = [
        dataclasses.replace(peaks_by_label[label][0], component_curve=peaks_by_label[label][1][keep])
        for label in order
    ]
    y = corrected[keep]
    fit_curve = total[keep]
    residuals = y - fit_curve
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))

    spectrum.processed_data = SpectrumData(X=X[keep], Y=y)
    spectrum.background = background[keep]
    spectrum.peak_table = [definitions_by_label[label] for label in order]

    return FitResult(
        success=True,
        fitted_peaks=fitted_peaks,
        total_fit_curve=fit_curve,
        residuals=residuals,
        chi_squared=ss_res,
        r_squared=1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0,
        convergence_time=convergence_time,
        converged=converged,
        nfev=nfev,
        max_nfev=max_nfev,
    )


def format_workflow_summary(result: Dict[str, Any], preset: MaterialPreset,
                            mode: str = "Raman") -> str:
    """
    Format workflow execution result as user-friendly summary.

    Parameters
    ----------
    result : dict
        Result from execute_auto_workflow()
    preset : MaterialPreset
        Preset that was used

    Returns
    -------
    str
        Formatted summary message
    """
    block = preset.block_for(mode)
    if block is None:
        # Only reachable if a caller summarises a preset it never ran.
        return f"No {mode} settings for '{preset.material_name}'."

    if result["success"]:
        fit_result = result["fit_result"]
        summary = (
            f"**Auto-workflow completed successfully!**\n\n"
            f"**Material:** {preset.material_name} ({mode})\n"
            f"**Stages:**\n"
            f"- X-range: {'Applied' if block.x_range_enabled else 'Skipped'}"
        )
        if block.x_range_enabled:
            summary += f" ({block.x_min} - {block.x_max})"
        if block.zero_line_calibration and mode == "Raman":
            offset = result.get("zero_offset")
            summary += (f"\n- Zero line: laser line at {offset:+.2f} cm⁻¹, axis shifted to put it at 0"
                        if offset is not None else
                        "\n- Zero line: no clear laser line, axis left as recorded")
        summary += (
            f"\n- De-spiking: Threshold {block.despike_threshold}\n"
            f"- Baseline: {block.baseline_algorithm}"
            + (f" (own baseline in each of {len(block.fit_windows)} fit windows)" if block.fit_windows else "")
            + f"\n- Fitting: {len(block.peak_templates)} peaks fitted"
            + (f" in {len(block.fit_windows)} fit windows" if block.fit_windows else "")
            + "\n\n"
            f"**Fit Quality:**\n"
            f"- R²: {fit_result.r_squared:.4f}\n"
            f"- χ²: {fit_result.chi_squared:.2e}\n"
            f"- Convergence time: {fit_result.convergence_time:.2f}s"
        )
        if not fit_result.converged:
            summary += "\n\n" + convergence_warning(fit_result)
        return summary
    else:
        return (
            f"**Auto-workflow failed at stage: {result['stage_completed']}**\n\n"
            f"{result['error_message']}"
        )


def convergence_warning(fit_result) -> str:
    """The one sentence every surface prints beside a fit that ran out of budget.

    Kept here so the Spectra page, the batch summary and the QC Report say the
    same thing about the same condition. Empty for a converged fit.
    """
    if fit_result is None or fit_result.converged:
        return ""
    budget = (
        f" ({fit_result.nfev}/{fit_result.max_nfev} evaluations)"
        if fit_result.nfev is not None and fit_result.max_nfev is not None else ""
    )
    return (
        f"⚠️ Not converged: the fit stopped at the preset's max_iterations{budget}. "
        f"The numbers are the best found so far, not an optimum, and no "
        f"uncertainties could be estimated. Raise max_iterations in the preset."
    )


def get_workflow_suggestions(stage: str, error_msg: str) -> str:
    """
    Provide helpful suggestions based on failure stage and error.

    Parameters
    ----------
    stage : str
        Stage that failed
    error_msg : str
        Error message

    Returns
    -------
    str
        Suggestion text
    """
    suggestions = {
        "x_range": (
            "**Common fixes:**\n"
            "- Open Excel file and adjust x_min/x_max to match your data range\n"
            "- Or disable x_range_enabled in preset\n"
            "- Check that your file units match preset mode (cm⁻¹ for Raman, nm for PL)"
        ),
        "despike": (
            "**Common fixes:**\n"
            "- Increase despike_threshold (try 8.0-10.0 for noisy data)\n"
            "- Or decrease threshold (try 3.0-5.0) if removing too many points"
        ),
        "baseline": (
            "**Common fixes:**\n"
            "- Try different baseline algorithm (Polynomial vs ALS)\n"
            "- For Polynomial: Adjust baseline_degree (typically 2-5)\n"
            "- For ALS: Adjust baseline_lambda (higher = smoother, try 50000-100000)\n"
            "- For ALS: Adjust baseline_p (lower = more asymmetric, try 0.01-0.05)"
        ),
        "fitting": (
            "**Common fixes:**\n"
            "- Check peak centers match your spectrum (view data first)\n"
            "- Increase center_tolerance to allow more fitting flexibility\n"
            "- Adjust initial intensity guesses (should be ~peak maximum)\n"
            "- Reduce number of peaks if spectrum is simple\n"
            "- Try manual workflow to see if baseline correction is adequate"
        )
    }

    return suggestions.get(stage, "Try manual workflow to diagnose the issue.")
