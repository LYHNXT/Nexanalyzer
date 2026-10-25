"""
Voigt profile fitting using lmfit.

This module implements:
- Multi-peak Voigt fitting with constrained nonlinear optimization
- Auto-bounds calculation based on mode
- Peak auto-finding using scipy.signal
- Fit quality metrics (chi-squared, R²)

References
----------
- lmfit documentation: https://lmfit.github.io/lmfit-py/
- Voigt profile: convolution of Gaussian and Lorentzian line shapes
"""

import numpy as np
from lmfit import Model, Parameters
from lmfit.lineshapes import voigt as _voigt_lineshape
from lmfit.models import VoigtModel
import time
from typing import List, Optional
from ..models.peak import MAX_PEAKS, PeakDefinition, FittedPeak, FitResult


# FWHM of a unit-sigma Gaussian: 2*sqrt(2*ln 2). The bounds and initial
# guesses elsewhere in this module use the rounded 2.355; the difference is
# under 0.01% and they are left as they are so fits don't shift.
_GAUSSIAN_FWHM_PER_SIGMA = 2.3548200450309493


def voigt_fwhm(sigma: float, gamma: float) -> float:
    """
    Full width at half maximum of a Voigt profile.

    A Voigt is a Gaussian convolved with a Lorentzian, and its width depends on
    both: 2.355*sigma is the Gaussian contribution alone and understates the
    real width by 60-110% at the shapes this fitter converges to. There is no
    closed form, so this uses the Olivero & Longbothum approximation, accurate
    to ~0.02%:

        f_V ~= 0.5346*f_L + sqrt(0.2166*f_L^2 + f_G^2)

    with f_G = 2*sqrt(2 ln 2)*sigma and f_L = 2*gamma. Verified against the
    measured half-maximum width of the fitted component curves.
    """
    f_g = _GAUSSIAN_FWHM_PER_SIGMA * float(sigma)
    f_l = 2.0 * float(gamma)
    return 0.5346 * f_l + np.sqrt(0.2166 * f_l * f_l + f_g * f_g)


START_MARGIN = 0.01

START_PERTURBATION = 0.25
"""How far an extra start (fit_voigt_peaks `starts` > 1) moves each starting value,
as a fraction of its bounded range: the perturbation used to measure how often
the preset's start misses the best fit on 91 real WSe2 spectra."""
"""How far inside its bounds a starting value is placed, as a fraction of the
bounded range.

lmfit enforces bounds by mapping each parameter through a sine, whose slope is
zero exactly at a bound. A parameter that *starts* there has no gradient, and
Levenberg-Marquardt can stop on its first step with every parameter unchanged
and still report success: a WSe2-like peak with shape 0.3 came back from 11
evaluations at R² 0.94 and 78% of its area. It hits any peak whose shape split
puts gamma (or sigma) under its floor, and a centre clamped onto the edge.
"""


def _inside(value: float, lo: float, hi: float) -> float:
    """`value` moved at least START_MARGIN of the range away from either bound."""
    margin = START_MARGIN * (hi - lo)
    return min(max(value, lo + margin), hi - margin)


# The fit varies `vfwhm` (the Voigt FWHM, f_V) and `gmix`, which places the
# Gaussian share gfrac = f_G / f_V between its floor and 1:
#     gfrac = g0 + (1 - g0) * gmix,   g0 = f_G,min / f_V,   f_G,min = width_min / 2.
# That floor is the one sigma always had (width_min / (2 * 2.355)): an
# instrument-resolution floor on the Gaussian part. Without it a capped peak
# goes pure Lorentzian to grow wings -- 2ZA(M) did, and took E2g+A1g's wing.
# sigma and gamma are derived. sigma = gfrac * f_V / 2.3548. gamma = f_L / 2, where
# f_L inverts voigt_fwhm's Olivero-Longbothum formula exactly: with
# L = f_L / f_V and r = gfrac, (1 - 0.5346 L)^2 = 0.2166 L^2 + r^2 is
# a L^2 - 2(0.5346) L + (1 - r^2) = 0 with a = 0.5346^2 - 0.2166, whose smaller
# root is written in the cancellation-free form 2c / (b + sqrt(b^2 - 4ac)).
# So voigt_fwhm(sigma, gamma) gives back vfwhm, and bounding vfwhm bounds the
# reported width.
_GFRAC_EXPR = "{fg_min!r} / {p}vfwhm + (1 - {fg_min!r} / {p}vfwhm) * {p}gmix"
_SIGMA_EXPR = "{p}gfrac * {p}vfwhm / " + repr(_GAUSSIAN_FWHM_PER_SIGMA)
_GAMMA_EXPR = (
    "{p}vfwhm * (1 - {p}gfrac**2)"
    " / (1.0692 + sqrt(1.0692**2 - 4 * 0.06919716 * (1 - {p}gfrac**2)))"
)

# A Voigt's maximum per unit area, sigma*sqrt(2*pi) / Re[w(i*gamma / (sigma*sqrt(2)))]
# being its inverse -- the expression lmfit's own VoigtModel uses for `height`.
# With `dominant` set, the dominant peak's height is derived from its area, and
# every other peak's area from `hratio` (its height over the dominant's, in
# [0, 1]), so no other peak's fitted maximum can exceed the dominant's.
_UNIT_HEIGHT_EXPR = "real(wofz((1j*{p}gamma)/(max(1e-12, {p}sigma)*sqrt(2)))) / (max(1e-12, {p}sigma)*sqrt(2*pi))"
_HEIGHT_EXPR = "{p}amplitude * " + _UNIT_HEIGHT_EXPR
_DOMINATED_AREA_EXPR = "{p}hratio * {d}hdom / (" + _UNIT_HEIGHT_EXPR + ")"


def _sigma_gamma(vfwhm, gmix, fgmin):
    """sigma and gamma from the fitted Voigt FWHM and Gaussian share --
    _GFRAC_EXPR / _SIGMA_EXPR / _GAMMA_EXPR written as Python, operation for
    operation, so the values are the ones lmfit's expression evaluator gave."""
    gfrac = fgmin / vfwhm + (1 - fgmin / vfwhm) * gmix
    sigma = gfrac * vfwhm / 2.3548200450309493
    gamma = vfwhm * (1 - gfrac**2) / (1.0692 + np.sqrt(1.0692**2 - 4 * 0.06919716 * (1 - gfrac**2)))
    return sigma, gamma


def _voigt_by_width(x, amplitude=1.0, center=0.0, vfwhm=1.0, gmix=0.5, fgmin=0.0):
    """A Voigt parameterised by its FWHM and Gaussian share, sigma and gamma
    derived inline rather than by three lmfit expressions per peak.

    The fit's model for every peak when no dominant peak is set (v5.40.0).
    lmfit re-evaluated the three expressions per peak through its expression
    interpreter on every residual call, ~40 % of a fit; computing them here
    with the same operations in the same order leaves every number
    bit-identical (481 golden and production spectra, Raman and PL) and makes
    WSe2 Raman fits ~2.3x faster. A dominant peak's constraint still needs
    lmfit expressions (its area depends on another peak's height and needs a
    propagated stderr), so those fits keep the expression model.
    """
    sigma, gamma = _sigma_gamma(vfwhm, gmix, fgmin)
    return _voigt_lineshape(x, amplitude, center, sigma, gamma)


def fit_voigt_peaks(
    x: np.ndarray,
    y: np.ndarray,
    peak_table: List[PeakDefinition],
    mode: str = "Raman",
    max_iterations: int = 2000,
    starts: int = 1,
    dominant: Optional[str] = None,
) -> FitResult:
    """
    Fit multiple Voigt profiles to spectrum data.

    Parameters
    ----------
    x : np.ndarray
        Wavenumber (cm⁻¹) or wavelength (nm).
    y : np.ndarray
        Baseline-corrected intensity.
    peak_table : List[PeakDefinition]
        User-defined peak guesses with bounds.
    mode : str, default="Raman"
        "Raman" or "PL" (affects auto-bounds if not manually set).
    max_iterations : int, default=2000
        Levenberg-Marquardt iterations the fit may spend. Passed to lmfit as
        `max_nfev = max_iterations * (variables + 1)` per start, which is lmfit's own
        default scaling: one iteration costs a function evaluation per
        variable for the finite-difference Jacobian plus one for the step.
        Until v5.21.0 the number went straight in as `max_nfev`, so an
        8-peak WSe2 fit (32 variables) got 1/33 of the budget its preset
        named, and a fifth of real spectra stopped on it.

    starts : int, default=1
        How many starting points to fit from (v5.23.0). Start 1 is the preset's
        own guesses; each further start moves every starting value by up to
        +-`START_PERTURBATION` of its bounded range (seeded, so a refit gives
        the same answer), and the fit with the lowest chi-squared is kept,
        a converged fit beating one that ran out of budget. Levenberg-Marquardt
        walks to the nearest minimum: on 91 real WSe2 spectra the preset's
        start reached the best main-band fit only 62 % of the time even with
        non-overlapping centre ranges, and the misses included E2g+A1g pinned
        at its width limit with 2LA taller. 1 keeps the single fit every
        preset had before.

    dominant : str, optional
        Label of a peak that must be the tallest (v5.26.0). Every other peak's
        area is then derived from `hratio` -- its fitted maximum over the
        dominant peak's, bounded to [0, 1] -- so the constraint is on the
        *intensity* every surface reports, not on the area. A peak whose
        `hratio` ends at 1 is held level with the dominant one by the
        constraint and is flagged `on_limit`. None (or a label not in
        `peak_table`) fits every area freely, as before.

    Returns
    -------
    FitResult
        Fitting results with fitted_peaks, quality metrics, and error handling.
        `converged` is False when the budget ran out first -- see FitResult.
        `nfev` / `max_nfev` add up over all starts.

    Raises
    ------
    ValueError
        If peak_table is empty or has more than MAX_PEAKS peaks.

    Notes
    -----
    Algorithm (FR-031 to FR-037):
    - Uses lmfit VoigtModel (Gaussian + Lorentzian convolution)
    - Levenberg-Marquardt optimizer (method='leastsq')
    - Auto-bounds from PeakDefinition, honouring its center_tolerance when set
    - Returns actionable error messages on convergence failure

    Voigt parameters:
    - center: peak position
    - amplitude: lmfit's name for the area — peak intensity × width
    - sigma: Gaussian width (FWHM = 2.355 * sigma)
    - gamma: Lorentzian width (FWHM = 2 * gamma)

    Examples
    --------
    >>> peak_defs = [
    ...     PeakDefinition(center=1350, intensity=5000, width_fwhm=50),
    ...     PeakDefinition(center=1580, intensity=8000, width_fwhm=60)
    ... ]
    >>> result = fit_voigt_peaks(x, y, peak_defs, mode="Raman")
    >>> if result.success:
    ...     print(f"R² = {result.r_squared:.4f}")
    """
    # Validation
    if len(peak_table) == 0:
        raise ValueError("peak_table must have at least 1 peak")

    if len(peak_table) > MAX_PEAKS:
        raise ValueError(f"peak_table must have <= {MAX_PEAKS} peaks (got {len(peak_table)})")

    if len(x) != len(y):
        raise ValueError(f"x and y must have same length (got {len(x)} vs {len(y)})")

    # (Re)calculate bounds against this spectrum's own range and resolution.
    # Unconditional by design: it is what keeps the bounds consistent with the
    # current center and width. A peak that carries its own center_tolerance —
    # every peak built from a preset does — keeps that tolerance here.
    x_range = (x.min(), x.max())
    y_max = y.max()
    spectral_resolution = np.median(np.abs(np.diff(x)))

    if not y_max > 0:
        # The intensity ceiling is 5 x y_max: at 0 lmfit refuses min == max,
        # below 0 it fits a negative-area peak and calls it a success.
        raise ValueError("no positive signal to fit: the corrected spectrum is <= 0 everywhere")
    for peak in peak_table:
        peak.calculate_auto_bounds(mode, x_range, y_max, spectral_resolution)
        if peak.width_min >= peak.width_max:
            # A span narrower than the template FWHM inverts the width bounds;
            # lmfit swaps them silently and returns a meaningless "success".
            raise ValueError(
                f"peak {peak.label!r}: the fitted range ({x_range[1] - x_range[0]:.4g} wide) "
                f"is too narrow for its {peak.width_fwhm:g} FWHM template -- width bounds "
                f"{peak.width_min:.4g} >= {peak.width_max:.4g}. Widen the x-range or fit window."
            )

    dominant_index = next(
        (i for i, peak in enumerate(peak_table) if dominant and peak.label == dominant), None
    ) if len(peak_table) > 1 else None

    def _build(rng):
        """The composite model and its parameters, started from the preset (rng
        None) or from a perturbed point inside the same bounds."""
        def _start(value, lo, hi):
            if rng is not None and hi > lo:
                value = value + rng.uniform(-START_PERTURBATION, START_PERTURBATION) * (hi - lo)
            return _inside(value, lo, hi)

        # Build composite model (sum of Voigt models)
        composite_model = None
        params = Parameters()
        dominated = []  # (prefix, intensity_guess) of peaks whose area follows the dominant's
        dominant_guess = None

        for i, peak in enumerate(peak_table):
            prefix = f"p{i}_"
            voigt = (Model(_voigt_by_width, prefix=prefix) if dominant_index is None
                     else VoigtModel(prefix=prefix))

            if composite_model is None:
                composite_model = voigt
            else:
                composite_model += voigt

            # Convert FWHM to sigma/gamma using shape parameter
            # Shape: 0 = pure Gaussian, 1 = pure Lorentzian, 0.5 = equal contribution
            # The split is linear in shape across the whole range; this used to be
            # written as an if/else on shape < 0.5 whose two arms were identical,
            # which read as though the halves were treated differently.
            shape = peak.shape
            frac_gaussian = 1.0 - shape
            frac_lorentzian = shape

            # The fit varies the Voigt FWHM itself, bounded to [width_min,
            # width_max], and the Gaussian share of it; sigma and gamma follow by
            # expression (see VOIGT_* below). Bounding sigma and gamma separately
            # -- each to half of width_max -- capped a Lorentzian-shaped peak at
            # ~0.52 x width_max: WSe2's E2g+A1g piled up at 6.21 cm-1.
            # The shape split only sets the starting Gaussian share.
            f_g0 = peak.width_fwhm * frac_gaussian
            f_l0 = peak.width_fwhm * frac_lorentzian
            f_v0 = 0.5346 * f_l0 + np.sqrt(0.2166 * f_l0 * f_l0 + f_g0 * f_g0)
            fwhm_guess = _start(peak.width_fwhm, peak.width_min, peak.width_max)
            fg_min = peak.width_min / 2.0
            g0 = fg_min / fwhm_guess
            gmix_guess = _start((f_g0 / f_v0 - g0) / (1.0 - g0), 0.0, 1.0)

            # The starting center must lie inside its own bounds, which it doesn't
            # when the preset places the peak outside the spectrum's range and
            # calculate_auto_bounds has pinned it to the edge (then it is fixed,
            # and the margin collapses to the pinned value).
            center_guess = _start(peak.center, peak.center_min, peak.center_max)

            # Auto-estimate the peak intensity from the data at center_guess.
            # Position + FWHM come from the preset (material properties); intensity
            # depends on measurement conditions, so we always init from the data.
            idx = int(np.argmin(np.abs(x - center_guess)))
            intensity_guess = max(float(y[idx]), 1e-6)
            fwhm_eff = peak.width_fwhm
            # lmfit's "amplitude" is the area, so both the guess and the ceiling
            # convert from intensity: area = intensity x FWHM x 1.064 for a Voigt.
            area_max = peak.intensity_max * fwhm_eff * 1.064
            area_guess = _start(intensity_guess * fwhm_eff * 1.064, 0.0, area_max)

            # Add parameters with bounds
            if peak.center_min == peak.center_max:
                # calculate_auto_bounds pinned it: the preset places this peak
                # outside the spectrum, so the edge is the only position open to
                # it. lmfit rejects min == max, so say "fixed" rather than passing
                # a degenerate range -- and note it swaps min/max silently when
                # they arrive inverted, which is how an off-range peak used to come
                # back at a position belonging to neither bound.
                params.add(f"{prefix}center", value=center_guess, vary=False)
            else:
                params.add(f"{prefix}center", value=center_guess,
                           min=peak.center_min, max=peak.center_max)
            # Parameter order is the order Levenberg-Marquardt steps in, so a fit
            # without a dominant peak keeps exactly the order it always had.
            if dominant_index is not None and i != dominant_index:
                dominated.append((prefix, intensity_guess))
            else:
                params.add(f"{prefix}amplitude", value=area_guess,
                           min=0, max=area_max)
                if i == dominant_index:
                    dominant_guess = intensity_guess
            params.add(f"{prefix}vfwhm", value=fwhm_guess,
                       min=peak.width_min, max=peak.width_max)
            params.add(f"{prefix}gmix", value=gmix_guess, min=0.0, max=1.0)
            if dominant_index is None:
                params.add(f"{prefix}fgmin", value=float(fg_min), vary=False)
            else:
                params.add(f"{prefix}gfrac", expr=_GFRAC_EXPR.format(p=prefix, fg_min=float(fg_min)))
                params.add(f"{prefix}sigma", expr=_SIGMA_EXPR.format(p=prefix))
                params.add(f"{prefix}gamma", expr=_GAMMA_EXPR.format(p=prefix))

        # A dominated peak's area is an expression in the dominant peak's height,
        # which lmfit can only parse once every peak's widths exist.
        if dominant_index is not None:
            d = f"p{dominant_index}_"
            params.add(f"{d}hdom", expr=_HEIGHT_EXPR.format(p=d))
            for prefix, intensity_guess in dominated:
                params.add(f"{prefix}hratio", value=_start(min(intensity_guess / dominant_guess, 1.0), 0.0, 1.0),
                           min=0.0, max=1.0)
                params.add(f"{prefix}amplitude", expr=_DOMINATED_AREA_EXPR.format(p=prefix, d=d))

        return composite_model, params

    # Fit using Levenberg-Marquardt with increased tolerance, from each start
    start_time = time.time()
    best = None            # (rank, result, converged)
    total_nfev = 0
    total_max_nfev = 0
    last_error = None

    for k in range(max(1, int(starts))):
        composite_model, params = _build(None if k == 0 else np.random.default_rng(k))

        # Iterations, not evaluations: see the max_iterations note above.
        n_vary = sum(1 for p in params.values() if p.vary)
        max_nfev = int(max_iterations) * (n_vary + 1)
        total_max_nfev += max_nfev

        try:
            result = composite_model.fit(
                y, params, x=x,
                method='leastsq',
                max_nfev=max_nfev,
                fit_kws={'ftol': 1e-6, 'xtol': 1e-6}  # Increased tolerance for stability
            )
        except Exception as e:
            last_error = f"Fitting error: {str(e)}"
            continue
        total_nfev += int(result.nfev)

        # lmfit's `success` is MINPACK ier in {1,2,3,4}. Three other outcomes
        # are still a usable fit and are told apart here by `ier`, because
        # lmfit gives the *same* message -- "Tolerance seems to be too small"
        # -- to all of them, and matching on it used to wave a truncated fit
        # through as a finished one:
        #   ier 6/7/8  ftol/xtol/gtol unachievable: no further improvement is
        #              possible, i.e. converged to machine precision;
        #   ier 5      MINPACK ran out of maxfev;
        #   ier -1     lmfit's own abort on reaching max_nfev.
        # The last two are the budget running out: the peaks are the best
        # found so far, not an optimum, and no covariance exists. They are
        # returned with converged=False rather than refused, since a fit that
        # is 99% there is more useful flagged than discarded -- but flagged.
        ier = getattr(result, "ier", None)
        exhausted = ier in (-1, 5) or result.nfev >= max_nfev
        converged = not exhausted and (bool(result.success) or ier in (6, 7, 8))
        if not result.success and not exhausted and ier not in (6, 7, 8):
            last_error = (
                f"Fitting failed to converge ({result.message}). "
                f"Try adjusting initial guesses or reducing number of peaks."
            )
            continue
        # A converged fit beats one that ran out of budget; then lower chi-squared.
        rank = (0 if converged else 1, float(result.chisqr))
        if best is None or rank < best[0]:
            best = (rank, result, converged)

    convergence_time = time.time() - start_time
    if best is None:
        return FitResult(
            success=False,
            fitted_peaks=[],
            total_fit_curve=np.zeros_like(y),
            residuals=y.copy(),
            chi_squared=np.sum(y**2),
            r_squared=0.0,
            convergence_time=convergence_time,
            error_message=last_error or "Fitting failed.",
        )
    _rank, result, converged = best

    # Extract fitted parameters
    fitted_peaks = []
    total_fit = result.best_fit

    # Helper functions to safely extract parameter values and errors
    # (handles both Parameter objects and raw floats)
    def get_param_value(param):
        """Extract value from Parameter object or float."""
        return param.value if hasattr(param, 'value') else float(param)

    def get_param_stderr(param):
        """Extract stderr from Parameter object; NaN when lmfit could not estimate one.

        None means the covariance could not be computed -- a parameter on a
        bound, a peak fitted to zero, or a fit that ran out of budget. That
        used to be reported as 0.0, which every CSV and workbook printed as a
        perfectly determined value. NaN lands as an empty cell instead.
        """
        stderr = getattr(param, 'stderr', None)
        return float(stderr) if stderr is not None else float('nan')

    for i, peak in enumerate(peak_table):
        prefix = f"p{i}_"

        try:
            # Get fitted values - robust to both Parameter objects and raw floats
            center_fit = get_param_value(result.params[f"{prefix}center"])
            center_stderr = get_param_stderr(result.params[f"{prefix}center"])
            # lmfit's "amplitude" parameter is the integrated area under the peak.
            area_fit = get_param_value(result.params[f"{prefix}amplitude"])
            area_stderr = get_param_stderr(result.params[f"{prefix}amplitude"])
            if f"{prefix}sigma" in result.params:
                sigma_fit = get_param_value(result.params[f"{prefix}sigma"])
                gamma_fit = get_param_value(result.params[f"{prefix}gamma"])
            else:
                sigma_fit, gamma_fit = _sigma_gamma(
                    get_param_value(result.params[f"{prefix}vfwhm"]),
                    get_param_value(result.params[f"{prefix}gmix"]),
                    get_param_value(result.params[f"{prefix}fgmin"]))
        except AttributeError as e:
            # Debug: print parameter type if extraction fails
            param_key = f"{prefix}center"
            param_obj = result.params.get(param_key)
            raise AttributeError(
                f"Parameter extraction failed for {param_key}. "
                f"Type: {type(param_obj)}, "
                f"Has 'value': {hasattr(param_obj, 'value')}, "
                f"Original error: {e}"
            )

        # A Voigt's width comes from both of its components, so 2.355*sigma
        # is only the Gaussian half of it. The width is itself a fitted
        # parameter, so its stderr comes straight from lmfit's covariance.
        width_fwhm_fit = get_param_value(result.params[f"{prefix}vfwhm"])
        width_stderr = get_param_stderr(result.params[f"{prefix}vfwhm"])

        # Calculate shape parameter (Lorentzian fraction)
        # shape = gamma / (gamma + sigma)
        shape_fit = gamma_fit / (gamma_fit + sigma_fit)

        # Evaluate component curve
        voigt_component = VoigtModel(prefix=prefix)
        # Pass fitted parameters directly as keyword arguments
        component_curve = voigt_component.eval(
            x=x,
            **{
                f"{prefix}center": center_fit,
                f"{prefix}amplitude": area_fit,
                f"{prefix}sigma": sigma_fit,
                f"{prefix}gamma": gamma_fit
            }
        )

        # On a limit: centre on its range edge (when it was free to move) or the
        # Voigt FWHM on its ceiling -- CLAUDE.md's "vfwhm@max" test.
        def _at(prm, side):
            lim = prm.max if side == "max" else prm.min
            return prm.vary and lim is not None and np.isfinite(lim) and abs(prm.value - lim) <= 1e-6 * max(1.0, abs(lim))
        c_prm, w_prm = result.params[f"{prefix}center"], result.params[f"{prefix}vfwhm"]
        on_limit = bool(_at(c_prm, "min") or _at(c_prm, "max") or _at(w_prm, "max"))
        # Held level with the dominant peak: the constraint set this height, not the data.
        h_prm = result.params.get(f"{prefix}hratio")
        if h_prm is not None and _at(h_prm, "max"):
            on_limit = True

        fitted_peaks.append(FittedPeak(
            label=peak.label,
            center=center_fit,
            center_stderr=center_stderr,
            area=area_fit,
            area_stderr=area_stderr,
            width_fwhm=width_fwhm_fit,
            width_stderr=width_stderr,
            shape=shape_fit,
            component_curve=component_curve,
            color=peak.color,  # Copy color from PeakDefinition
            on_limit=on_limit,
        ))

    # Calculate quality metrics
    residuals = result.residual
    chi_squared = result.chisqr

    # R-squared
    ss_res = np.sum(residuals**2)
    ss_tot = np.sum((y - np.mean(y))**2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return FitResult(
        success=True,
        fitted_peaks=fitted_peaks,
        total_fit_curve=total_fit,
        residuals=residuals,
        chi_squared=chi_squared,
        r_squared=r_squared,
        convergence_time=convergence_time,
        error_message="",
        converged=converged,
        nfev=total_nfev,
        max_nfev=total_max_nfev,
    )


