"""
Metrics derived from a fit result: the per-peak numbers every reporting
surface displays, and their aggregation across a set of fits.

Two quantities, two names, used consistently everywhere:

- **intensity** — the fitted curve's maximum, via `peak_intensity_and_stderr`.
  This is what every reporting surface shows, because it's the number
  spectroscopists read off a plot: the on-screen Fit Results table, the
  exported CSVs, the QC Report's summary tables and its LA/E2g+A1g ratio
  all agree on it.
- **area** — the area under the peak, `FittedPeak.area`, which is intensity x
  FWHM x 1.064 for a Voigt. It is what lmfit solves for and what lmfit itself
  calls "amplitude", which is exactly why it is named `area` here.

The two diverge whenever peaks have unequal widths: WSe2's LA mode is ~6x
broader than E2g+A1g, so their area ratio is ~4.3x their intensity ratio. The
report used to show area under an "Amplitude" heading while the CSV used that
same heading for intensity, and the two surfaces disagreed.

Don't reintroduce that. "Amplitude" names neither quantity anywhere in the
codebase; a caller that wants the integrated quantity takes `FittedPeak.area`
and labels it "area".
"""

from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

import numpy as np

from ..models.peak import FitResult
from core.report.models import RAW_STAT_LABEL, PeakStat


def peak_intensity_and_stderr(peak) -> Tuple[float, float]:
    """
    A fitted peak's intensity and the standard error on that intensity.

    Intensity is the maximum of the fitted component curve, not
    `FittedPeak.area` (the area lmfit solves for). Since v5.55.0 the fitter
    records that maximum analytically (`FittedPeak.intensity`, from
    `fitting.voigt_height`); the tallest *sample* of the curve, read until
    then, under-read a peak whose centre fell between grid points by up to
    2.4 % at FWHM 5 on a 0.9 cm-1 grid, differently from spectrum to spectrum.
    A peak without that field (built by hand, or loaded from an older
    project) still reads its curve's tallest sample. The reported stderr is
    `area_stderr` rescaled by the same intensity/area ratio — a linear
    approximation, but the only sensible one without re-propagating the
    covariance matrix.

    Without either (a reloaded project whose curve list is empty) there is no
    maximum to read, so both are NaN -- never the area, which is a different
    quantity (until v5.34.0 it was returned here, under "Intensity"). A peak
    fitted to zero area has no ratio to rescale by, so its stderr is NaN as
    well: an unestimable stderr is NaN, never 0.0 (it printed "0 +- 0" on the
    MoS2 A0 points that fit to nothing).
    """
    recorded = getattr(peak, "intensity", None)
    if recorded is not None and np.isfinite(recorded):
        intensity = float(recorded)
    else:
        curve = getattr(peak, "component_curve", None)
        if curve is None or len(curve) == 0:
            return float("nan"), float("nan")
        intensity = float(np.max(curve))

    if peak.area > 0:
        return intensity, float(peak.area_stderr) * (intensity / peak.area)
    return intensity, float("nan")


def peak_intensity(peak) -> float:
    """A fitted peak's intensity — see peak_intensity_and_stderr()."""
    return peak_intensity_and_stderr(peak)[0]


class RawPeakStats(NamedTuple):
    """Fit-free stats read straight off a spectrum: the tallest point and the
    width of the spectrum at half that intensity. `fwhm` is None when the
    half-maximum crossing can't be measured (flat or non-positive signal)."""

    intensity: float
    center: float
    fwhm: Optional[float]


def raw_peak_stats(x: np.ndarray, y: np.ndarray) -> Optional[RawPeakStats]:
    """
    Measure the raw spectrum's dominant peak without reference to any fit.

    Used for PL, where the emission peak's raw intensity/position/width is
    reported alongside the fitted peaks — a sanity check on the fit and the
    number some instrument software quotes. Returns None for an empty
    spectrum.
    """
    if len(y) == 0:
        return None

    imax = int(np.argmax(y))
    intensity = float(y[imax])
    center = float(x[imax])

    fwhm: Optional[float] = None
    if intensity > 0:
        above = y >= intensity / 2.0
        if above.any():
            idxs = np.where(above)[0]
            # abs(): nothing sorts X, and some exports run high-to-low.
            fwhm = float(abs(x[idxs[-1]] - x[idxs[0]]))

    return RawPeakStats(intensity=intensity, center=center, fwhm=fwhm)


R_SQUARED_MIN = 0.5
"""Fits at or below this R-squared are dropped before anything is aggregated.

Inherited from the WSe2 analysis scripts, which gate every Raman and PL fit
this way before plotting or summarizing. A spectrum whose fit explains less
than half the variance is not a measurement of anything; averaging it in moves
a reported mean without moving `n`, so the number looks better-supported than
it is.
"""

IQR_FENCE = 1.5
"""Tukey fence multiplier for the per-point outlier cut. Also inherited."""

NEAR_ZERO_FRACTION = 0.02
"""A peak under this fraction of the strongest peak in its own fit was fitted to
zero: it still reports a centre and a width, but with no height those
parameters are free and describe nothing.

PL only. The QC Report's PL summary table (`aggregate_fit_results`) and its PL
quality figure (`peak_quality.PL_CLEANING`) both read this one value. They
still differ in `n`: the figure also drops widths under 5 nm and the widest 5 %
of each peak (inherited cleaning the table does not apply), and its title says
so. MoS2's A0 is the case that
needs it: on red-shifted wafers the trion carries the whole peak and A0 fits to
nothing on a third of the points.
"""


def iqr_mask(values: np.ndarray) -> np.ndarray:
    """
    A boolean mask keeping values inside 1.5x Tukey fences.

    Returns all-True when the interquartile range is zero, which is what makes
    this safe on small samples: a single measurement, or several identical
    ones, has no spread to judge an outlier against, so nothing is dropped.
    That is the property that leaves one-spectrum-per-point samples — every
    sample the app could fit before multi-spectrum parsing — numerically
    untouched by this filter.
    """
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return np.zeros(0, dtype=bool)
    q1, q3 = np.percentile(values, [25, 75])
    iqr = q3 - q1
    if iqr <= 0:
        return np.ones(values.size, dtype=bool)
    return (values >= q1 - IQR_FENCE * iqr) & (values <= q3 + IQR_FENCE * iqr)


def filter_fits_by_quality(
    fits_by_point: Sequence[Tuple[int, FitResult]],
    r_squared_min: float = R_SQUARED_MIN,
) -> List[Tuple[int, FitResult]]:
    """
    Drop fits whose R-squared is at or below `r_squared_min`, preserving order.

    Kept a separate, named step rather than folded into the aggregation so the
    gate is visible at the call site and so every downstream summary — the
    stats tables, the intensity ratios, the quality figure — is demonstrably
    working from the same surviving set.
    """
    return [(point, fit) for point, fit in fits_by_point if fit.r_squared > r_squared_min]


def _clean_pool(by_point: dict) -> np.ndarray:
    """Concatenate every point's IQR-surviving values into one pooled array."""
    kept = [np.asarray(v, float)[iqr_mask(v)] for v in by_point.values() if len(v)]
    return np.concatenate(kept) if kept else np.zeros(0)


def _mean_std(pooled: np.ndarray) -> Tuple[float, float]:
    if pooled.size == 0:
        return 0.0, 0.0
    if pooled.size == 1:
        return float(pooled[0]), 0.0
    return float(np.mean(pooled)), float(np.std(pooled, ddof=1))


def aggregate_fit_results(
    fits_by_point: Sequence[Tuple[int, FitResult]],
    relative_intensity_floor: Optional[float] = None,
) -> List[PeakStat]:
    """
    Group fitted peaks by label across `(point, fit_result)` pairs and compute
    mean/std/n of center, intensity, and width_fwhm for each label.

    Intensity, not `FittedPeak.area` — see the module docstring.

    Takes pairs rather than bare fits because outlier removal is **per point**:
    a multi-spectrum file contributes many fits at one grid position, and the
    spread that matters is within a position, not across the wafer. Cleaning
    across the whole sample would mistake real position-to-position variation
    for noise and delete it — which is exactly the variation the report exists
    to show.

    Values outside 1.5x Tukey fences within their own point are excluded from
    each metric independently, since a fit can be sound in center and wild in
    width. `n` counts the fits contributing that peak, before that per-metric
    cut, so it reports measurements taken rather than a different number for
    every column.

    Label order follows first-seen order. Standard deviation uses ddof=1 when
    more than one value survives, else 0.0. Callers should pass only successful
    fits, already gated by `filter_fits_by_quality`; neither `FitResult.success`
    nor R-squared is re-checked here.

    `relative_intensity_floor` (the QC Report passes `NEAR_ZERO_FRACTION` for
    PL) skips a peak whose intensity is under that fraction of the strongest
    peak in the same fit. It is skipped whole -- centre, width, intensity and
    `n` -- so `n` counts the points where the peak was found, and the intensity
    mean is over those points.
    """
    metrics: dict = {}
    counts: dict = {}
    order: List[str] = []

    for point, fit_result in fits_by_point:
        floor = None
        if relative_intensity_floor is not None and fit_result.fitted_peaks:
            floor = relative_intensity_floor * max(
                peak_intensity(p) for p in fit_result.fitted_peaks
            )
        for peak in fit_result.fitted_peaks:
            if floor is not None and peak_intensity(peak) < floor:
                continue
            label = peak.label
            if label not in metrics:
                metrics[label] = {"center": {}, "intensity": {}, "fwhm": {}}
                counts[label] = 0
                order.append(label)
            counts[label] += 1
            for key, value in (
                ("center", peak.center),
                ("intensity", peak_intensity(peak)),
                ("fwhm", peak.width_fwhm),
            ):
                metrics[label][key].setdefault(point, []).append(value)

    stats = []
    for label in order:
        center_mean, center_std = _mean_std(_clean_pool(metrics[label]["center"]))
        intensity_mean, intensity_std = _mean_std(_clean_pool(metrics[label]["intensity"]))
        fwhm_mean, fwhm_std = _mean_std(_clean_pool(metrics[label]["fwhm"]))
        stats.append(PeakStat(
            label=label,
            n=counts[label],
            center_mean=center_mean,
            center_std=center_std,
            intensity_mean=intensity_mean,
            intensity_std=intensity_std,
            fwhm_mean=fwhm_mean,
            fwhm_std=fwhm_std,
        ))

    return stats


def aggregate_raw_peak_stats(spectra) -> Optional[PeakStat]:
    """
    The empirical PL measurement, aggregated across a sample's points: the
    tallest point of each processed spectrum, with no fit involved.

    Returned as a `PeakStat` labelled "Raw" so it drops straight into the
    report's summary table alongside the fitted peaks. This is the same
    fit-free quantity the on-screen Fit Results table and the master CSV
    already show as a "Raw" row; the three now agree.

    Read off `processed_data` — after de-spiking and baseline correction, the
    same layer the fit sees — so its intensity is comparable to the fitted
    intensities in the rows below it, not inflated by a baseline offset.

    `fwhm_mean`/`fwhm_std` are None when no point yielded a half-maximum
    crossing (`raw_peak_stats` returns None there). Averaging over the points
    that did measure would report a width from a subset while `n` claimed the
    full count, and defaulting to 0.0 would print a fake measurement, so the
    cell is dashed out instead.

    Returns None when no spectrum yields a raw peak at all.
    """
    intensities: List[float] = []
    centers: List[float] = []
    fwhms: List[float] = []

    for spectrum in spectra:
        data = getattr(spectrum, "processed_data", None)
        if data is None:
            continue
        raw = raw_peak_stats(data.X, data.Y)
        if raw is None:
            continue
        intensities.append(raw.intensity)
        centers.append(raw.center)
        if raw.fwhm is not None:
            fwhms.append(raw.fwhm)

    n = len(intensities)
    if n == 0:
        return None

    ddof = 1 if n > 1 else 0
    fwhm_mean = float(np.mean(fwhms)) if len(fwhms) == n else None
    if fwhm_mean is None:
        fwhm_std = None
    else:
        fwhm_std = float(np.std(fwhms, ddof=ddof)) if n > 1 else 0.0

    return PeakStat(
        label=RAW_STAT_LABEL,
        n=n,
        center_mean=float(np.mean(centers)),
        center_std=float(np.std(centers, ddof=ddof)) if n > 1 else 0.0,
        intensity_mean=float(np.mean(intensities)),
        intensity_std=float(np.std(intensities, ddof=ddof)) if n > 1 else 0.0,
        fwhm_mean=fwhm_mean,
        fwhm_std=fwhm_std,
    )


MEASURED_SNR = 3.0
"""A peak counts as measured when its intensity is at least this many times its
fit's own residual noise (and it is not on a fit limit). Below it the fit has
placed a component in the noise, and a ratio formed with it is noise too."""


def fit_noise(fit: FitResult) -> float:
    """The fit's residual RMS: what is left in the data that the model does not explain."""
    residuals = getattr(fit, "residuals", None)
    if residuals is None or len(residuals) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(np.asarray(residuals, float)))))


def peak_is_measured(peak, fit: FitResult) -> bool:
    """Whether `peak` is a measurement rather than a component the fit parked (v5.23.0).

    Not measured when it ended on a fit limit (`FittedPeak.on_limit`: centre on
    its range edge, or width on its ceiling) or when its intensity is under
    `MEASURED_SNR` times the fit's residual noise. On HA1P01's WSe2 spectra C sits
    on its range edge and width limit ~70 % of the time, fitting the laser line's
    tail, and LB is under 3x the noise on many: C/LB formed from those is not a
    stacking measurement.
    """
    if getattr(peak, "on_limit", False):
        return False
    return peak_intensity(peak) >= MEASURED_SNR * fit_noise(fit)


#: Ratios formed from every spectrum, measured or not (v5.32.0): the report
#: shows 25 per region. LA and 2LA are present on every WSe2 film, but on a
#: weak region LA sits just under 3x the noise and the gate kept only its
#: strongest few -- KRX261001 point 7 read 9.7 % against ~8 % elsewhere from 5
#: of 25 spectra. With every spectrum the region medians barely move where the
#: signal is good. B2g/E2g+A1g too: its width ends on the cap in nearly every
#: spectrum, so the gate kept 3-19 of 25 on a 2L film. On a monolayer B2g is
#: absent; a 1L report hides its panels (peak_quality.BILAYER_ONLY_PEAKS)
#: rather than show a bilayer signal formed from noise fits.
RATIO_FROM_EVERY_SPECTRUM = frozenset({("LA", "E2g+A1g"), ("2LA", "E2g+A1g"), ("B2g", "E2g+A1g")})

RAMAN_RATIO_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("LA", "E2g+A1g"), ("2LA", "E2g+A1g"), ("C", "LB"), ("B2g", "E2g+A1g"),
    ("2LA(M)", "A1g"),
)
"""The WSe2 bilayer quality indicators, as (numerator, denominator) labels.

- ``LA / E2g+A1g`` — the defect ratio: disorder relative to the in-plane mode.
  LA(M) is a zone-edge mode that only becomes Raman-active where defects
  break momentum conservation, as graphene's D band does.
- ``2LA / E2g+A1g`` — the layer / resonance ratio (v5.29.0): 2LA(M) is a
  two-phonon double-resonance band, so it needs no defects; its strength
  follows the laser's resonance with the exciton and the layer number (1L
  0.24-0.37 against 2L 0.10-0.13 on IMEC-Nov and HA/VB, 2026-10-21). Not a
  defect metric, and laser-dependent: compare within one tool. It replaced
  the 2LA FWHM panel, whose width sits on its cap on most VA films.
- ``C / LB`` — the stacking ratio: the shear mode against the layer-breathing
  mode. Both are interlayer vibrations, so their ratio speaks directly to how
  the two layers sit on each other, which is what a bilayer wafer is judged on.
- ``B2g / E2g+A1g`` — the bilayer ratio (v5.25.0): B2g is Raman-silent in a
  monolayer and appears with a second layer, so a 1L film shows it under the
  noise (the ratio reads as absent) and a 2L film shows a measurable ratio.
  No spec line: the threshold is the reader's judgement.

One constant, because two surfaces read it: the QC Report's Raman summary table
and the Raman quality figure's ratio panels. They were separate literals until
v5.0.0 — one in the page, one in the figure module — with a comment in each
saying they had to agree, which is not a mechanism. A figure and a table
disagreeing about which ratios matter is the exact failure that comment feared.

B2g / E2g+A1g was reported until v3.10.0, dropped in favour of the stacking
ratio, and restored in v5.25.0 beside it: on HA1P01 C/LB is often unmeasurable
(C on its fit limit, LB under the noise), and B2g is the layer check people
read by eye. Any pair whose labels aren't both present in a fit is dropped
automatically (see `compute_peak_intensity_ratio`), so this list is safe for
materials that don't define these peaks.

- ``2LA(M) / A1g`` — MoS2's defect ratio (v5.34.0; until then only
  `scripts/qc_report/run_qc_mos2.py` reported it, by replacing this constant).
  Gated by `peak_is_measured`, like C/LB. The summary table and workbook carry
  it; the MoS2 quality figure has no ratio column (its layout,
  `peak_quality.MOS2_RAMAN_COLUMNS`, follows the runner's 2026-10-19 one).
"""


def wafer_fence(by_point: Dict[int, np.ndarray]) -> Dict[int, np.ndarray]:
    """Drop ratios outside the 1.5x IQR fence of the whole wafer (v5.32.0).

    A ratio formed from every spectrum can come from a weak spectrum whose
    region is itself spread wide, so the per-region fence keeps it (KRX261003
    point 5: 20 % from a spectrum at half the usual E2g+A1g intensity, against
    8-16 % in its region and a 19.7 % wafer fence). The per-region fence
    (`region_values`) still applies after this one.
    """
    pooled = np.concatenate([v for v in by_point.values() if v.size]) if by_point else np.array([])
    if pooled.size < 4:
        return by_point
    q1, q3 = np.percentile(pooled, [25, 75])
    lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    return {p: v[(v >= lo) & (v <= hi)] for p, v in by_point.items()}


def region_values(by_point: Dict[int, np.ndarray], per_position_iqr: bool = True) -> Dict[int, np.ndarray]:
    """Each position's values with the 1.5x IQR cut taken within the position
    (where `per_position_iqr`), empty positions dropped."""
    out = {}
    for point, values in by_point.items():
        clean = values[iqr_mask(values)] if per_position_iqr and values.size else values
        if clean.size:
            out[point] = clean
    return out


def ratio_values_by_point(
    spectra: Iterable[Tuple[int, Dict[str, Tuple[float, bool]]]],
    numerator_label: str,
    denominator_label: str,
) -> Dict[int, np.ndarray]:
    """Per-spectrum intensity ratios, grouped by grid position.

    `spectra` yields `(point, {label: (intensity, measured)})`, one item per
    spectrum. The ratio is formed within a spectrum, never as
    mean(numerator) / mean(denominator). A pair needs both peaks measured
    (`peak_is_measured`) unless it is in `RATIO_FROM_EVERY_SPECTRUM`, whose
    values are formed from every spectrum and then cut by `wafer_fence`.

    The one place ratios are selected: the summary table and workbook
    (`compute_peak_intensity_ratio`) and the quality figure's panels and title
    all read it, so they cannot disagree again (until v5.34.0 the table took a
    plain median over unfenced values while the figure fenced them).
    """
    every = (numerator_label, denominator_label) in RATIO_FROM_EVERY_SPECTRUM
    out: Dict[int, list] = {}
    for point, peaks in spectra:
        numerator = peaks.get(numerator_label)
        denominator = peaks.get(denominator_label)
        if numerator is None or denominator is None or not denominator[0]:
            continue
        if not every and not (numerator[1] and denominator[1]):
            continue
        ratio = numerator[0] / denominator[0]
        if np.isfinite(ratio):
            out.setdefault(point, []).append(ratio)
    values = {p: np.asarray(v, float) for p, v in out.items()}
    return wafer_fence(values) if every else values


def compute_peak_intensity_ratio(
    fits_by_point: Sequence, numerator_label: str, denominator_label: str
) -> Optional[Tuple[float, float, int]]:
    """
    The peak-intensity ratio `numerator_label` / `denominator_label` (e.g.
    "LA" / "E2g+A1g" for WSe2 Raman), summarized across a sample's spectra.

    `fits_by_point` is `(point, FitResult)` pairs; a bare FitResult counts as
    its own position. Ratios are selected by `ratio_values_by_point` -- the same
    values the quality figure draws -- then cut by the 1.5x IQR within each
    position, as the figure's panels are, and pooled.

    Summarized by **median and median absolute deviation**, not mean and
    standard deviation: a ratio of two fitted quantities is exactly where one
    badly fitted spectrum moves a mean. `n` counts the ratios kept.
    Returns (median, mad, n), or None if no spectrum yields the ratio.
    """
    def spectra():
        for index, item in enumerate(fits_by_point):
            point, fit = item if isinstance(item, tuple) else (index, item)
            yield point, {peak.label: (peak_intensity(peak), peak_is_measured(peak, fit))
                          for peak in fit.fitted_peaks}

    by_point = region_values(ratio_values_by_point(spectra(), numerator_label, denominator_label))
    if not by_point:
        return None
    values = np.concatenate(list(by_point.values()))
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    return (median, mad, int(values.size))
