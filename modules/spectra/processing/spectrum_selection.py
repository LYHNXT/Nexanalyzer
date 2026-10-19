"""
Per-point spectrum selection for the QC Report (v5.28.0): outliers out, then a
fixed number of spectra per point.

A point is measured as a map of 36 spectra, but the report compares points and
wafers on 25 a point. `select_spectra_per_point` takes every fitted spectrum
and, at each point holding more than `SPECTRA_PER_POINT`:

1. drops spectra whose fit fails the R² gate (`peak_metrics.R_SQUARED_MIN`);
2. drops spectra whose *main peaks* lie outside the point's 1.5x IQR fence on
   centre, FWHM or intensity (`peak_metrics.iqr_mask`, the same fence the stats
   tables use) -- whole spectra, so the 25 kept are 25 complete spectra;
3. keeps `SPECTRA_PER_POINT` of the rest at random, seeded by the point index
   so a rerun of the same data keeps the same spectra.

A point with `SPECTRA_PER_POINT` spectra or fewer is passed through untouched,
which keeps every 25-spectrum and single-spectrum report exactly as it was. If
outlier removal leaves fewer than `SPECTRA_PER_POINT`, all survivors are kept.

The main peaks are found from the data, not the preset: the peak with the
highest median intensity across the technique's spectra, plus every peak whose
median intensity is at least `MAIN_PEAK_FRACTION` of it, never counting the
laser line (`LASER_LINE_HALF_WIDTH`). Weak peaks are left
out on purpose -- their widths and centres are noisy enough that fencing on
them would discard good spectra.

Every downstream surface (stats tables, ratios, quality figures, fit grids and
the workbook) is fed the selection, so they all describe the same spectra.
"""

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import numpy as np

from .peak_metrics import R_SQUARED_MIN, iqr_mask, peak_intensity

SPECTRA_PER_POINT = 25
MAIN_PEAK_FRACTION = 0.5
#: A peak whose median centre is this close to 0 is the laser line (the
#: zero-line calibration's `center` peak in WSe2_QC), not the film: it is the
#: tallest thing in a low-frequency window, and fencing on it removed a third
#: of HADI09's spectra. Only a Raman shift can sit here; PL centres are in nm.
LASER_LINE_HALF_WIDTH = 10.0


@dataclass(frozen=True)
class SelectionSummary:
    """What `select_spectra_per_point` did, for the page to report."""
    fitted: int                 # spectra in
    outliers: int               # removed by the R² gate or the main-peak fence
    unused: int                 # good spectra left out by the random draw
    selected: int               # spectra out
    main_peaks: Tuple[str, ...]
    points_reduced: int         # points that held more than SPECTRA_PER_POINT


def main_peak_labels(fits) -> Tuple[str, ...]:
    """The strongest peak by median intensity, and every peak within
    MAIN_PEAK_FRACTION of it, strongest first."""
    by_label: Dict[str, List[float]] = {}
    centers: Dict[str, List[float]] = {}
    for fit in fits:
        if fit is None:
            continue
        for peak in fit.fitted_peaks:
            by_label.setdefault(peak.label, []).append(peak_intensity(peak))
            centers.setdefault(peak.label, []).append(peak.center)
    by_label = {lab: v for lab, v in by_label.items()
                if abs(float(np.median(centers[lab]))) > LASER_LINE_HALF_WIDTH}
    if not by_label:
        return ()
    medians = {label: float(np.median(v)) for label, v in by_label.items()}
    top = max(medians.values())
    if top <= 0:
        return ()
    return tuple(sorted((lab for lab, m in medians.items() if m >= MAIN_PEAK_FRACTION * top),
                        key=lambda lab: -medians[lab]))


def _outlier_flags(fits, main_peaks: Sequence[str]) -> np.ndarray:
    """True for each fit that fails the R² gate or puts a main peak outside the fence."""
    flags = np.array([fit is None or fit.r_squared <= R_SQUARED_MIN for fit in fits], dtype=bool)
    good = np.flatnonzero(~flags)
    for label in main_peaks:
        for quantity in ("center", "width_fwhm", "intensity"):
            values = np.full(good.size, np.nan)
            for j, i in enumerate(good):
                peak = next((p for p in fits[i].fitted_peaks if p.label == label), None)
                if peak is not None:
                    values[j] = peak_intensity(peak) if quantity == "intensity" else getattr(peak, quantity)
            measured = ~np.isnan(values)
            keep = np.ones(good.size, dtype=bool)
            keep[measured] = iqr_mask(values[measured])
            flags[good[~keep]] = True
    return flags


def select_spectra_per_point(point_spectra, target: int = SPECTRA_PER_POINT):
    """`(point, SpectrumFile)` pairs -> (the selected pairs in their original order, SelectionSummary)."""
    point_spectra = list(point_spectra)
    main_peaks = main_peak_labels([s.fit_result for _, s in point_spectra])
    by_point: Dict[int, List[int]] = {}
    for i, (point, _) in enumerate(point_spectra):
        by_point.setdefault(point, []).append(i)

    keep = set()
    outliers = unused = reduced = 0
    for point, idx in by_point.items():
        if len(idx) <= target:
            keep.update(idx)
            continue
        reduced += 1
        flags = _outlier_flags([point_spectra[i][1].fit_result for i in idx], main_peaks)
        survivors = [i for i, bad in zip(idx, flags) if not bad]
        outliers += len(idx) - len(survivors)
        if len(survivors) > target:
            unused += len(survivors) - target
            rng = np.random.default_rng(point)
            survivors = sorted(rng.choice(survivors, size=target, replace=False).tolist())
        keep.update(survivors)

    selected = [pair for i, pair in enumerate(point_spectra) if i in keep]
    return selected, SelectionSummary(
        fitted=len(point_spectra), outliers=outliers, unused=unused, selected=len(selected),
        main_peaks=main_peaks, points_reduced=reduced,
    )
