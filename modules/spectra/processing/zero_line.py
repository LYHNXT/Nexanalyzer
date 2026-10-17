"""
Zero-line calibration: put a Raman spectrum's laser (Rayleigh) line at 0 cm-1.

A Raman shift is measured from the laser line, so wherever the spectrometer
places that line, every peak moves with it. On 1,527 real WSe2 spectra
(HA1P01, VB1P01, TSMC DD folders, 2026-09/10) the line sat at a median
+0.1 cm-1 with a spread of 0.46 cm-1 between sessions, and that offset
correlated with the E2g+A1g position (r = 0.45). Subtracting it narrowed the
E2g+A1g scatter from 1.10 to 0.98 cm-1 across spectra and from 0.82 to 0.64
cm-1 across wafer medians.

**The search window is narrow on purpose.** These spectrometers have a notch
filter whose transmission edge sits a few cm-1 above the line; searching
+-20 cm-1 picked that edge instead on 18 % of spectra (offsets of +4 to +7 that
jumped by 5 cm-1 between spectra of one wafer), and correcting by it doubled
the scatter. Within +-3 cm-1 the line is found on 98.6 % of spectra and the
offset varies by a median 0.1 cm-1 within a wafer, as an instrument offset
should.

**A spectrum without a clear line is left alone** -- the axis is never shifted
by a guess -- and the workflow records that no correction was applied.
"""

from typing import Optional

import numpy as np

#: Half-width of the search window around 0 cm-1. See the module docstring.
SEARCH_HALF_WIDTH = 3.0
#: The line must stand this many times the off-line noise above the off-line
#: level to be trusted.
MIN_SNR = 10.0
#: Where the off-line level and noise are read (cm-1), clear of the line and
#: the notch edge but inside every Raman export that reaches 0.
_SIDE_BANDS = ((-45.0, -25.0), (25.0, 45.0))


def laser_line_offset(x: np.ndarray, y: np.ndarray,
                      half_width: float = SEARCH_HALF_WIDTH,
                      min_snr: float = MIN_SNR) -> Optional[float]:
    """Where the laser line sits, in cm-1, or None when it cannot be located.

    The maximum inside +-`half_width` is refined to sub-point precision by the
    vertex of a parabola through it and its two neighbours (points are ~0.9 cm-1
    apart). None when the spectrum does not reach 0, the maximum sits on the
    window's edge (a slope, not a line), or the line is under `min_snr` times
    the side-band noise.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    window = (x > -half_width) & (x < half_width)
    if window.sum() < 3:
        return None
    side = np.zeros_like(window)
    for lo, hi in _SIDE_BANDS:
        side |= (x > lo) & (x < hi)
    if side.sum() < 6:
        return None
    base = float(np.median(y[side]))
    noise = float(1.4826 * np.median(np.abs(np.diff(y[side]))) / np.sqrt(2)) or 1e-9

    order = np.argsort(x[window])
    xs, ys = x[window][order], y[window][order]
    i = int(np.argmax(ys))
    if i == 0 or i == len(xs) - 1:
        return None
    if (ys[i] - base) / noise < min_snr:
        return None
    y0, y1, y2 = ys[i - 1], ys[i], ys[i + 1]
    curvature = y0 - 2.0 * y1 + y2
    step = 0.5 * (xs[i + 1] - xs[i - 1])
    delta = 0.5 * (y0 - y2) / curvature if curvature != 0 else 0.0
    return float(xs[i] + np.clip(delta, -1.0, 1.0) * step)
