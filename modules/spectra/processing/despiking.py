"""
Cosmic-ray spike removal using modified Z-score (MAD-based).

This module implements the modified Z-score algorithm for detecting
and removing cosmic-ray spikes in spectroscopy data.

References
----------
Iglewicz, B. & Hoaglin, D. C. (1993).
"How to Detect and Handle Outliers". ASQC Quality Press.
"""

import numpy as np
from scipy.stats import median_abs_deviation
from typing import Tuple

from ..models.spectrum import DESPIKE_THRESHOLD_MAX, DESPIKE_THRESHOLD_MIN


def remove_spikes(
    y: np.ndarray,
    threshold: float = 6.0,
    window_size: int = 5
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Remove cosmic-ray spikes using modified Z-score (MAD-based).

    Parameters
    ----------
    y : np.ndarray
        Intensity array (1D, float64).
    threshold : float, default=6.0
        Modified Z-score threshold (DESPIKE_THRESHOLD_MIN to
        DESPIKE_THRESHOLD_MAX). Higher = less sensitive (fewer spikes
        detected). The score is against the whole spectrum's median and MAD,
        so a strong real peak can exceed a low threshold and be flattened.
    window_size : int, default=5
        Window size for local median replacement (must be odd).
        Used to replace detected spikes with local median.

    Returns
    -------
    y_clean : np.ndarray
        Cleaned intensity (spikes replaced with local median).
    spike_mask : np.ndarray (bool)
        True at spike indices, False elsewhere.

    Raises
    ------
    ValueError
        If threshold is outside [DESPIKE_THRESHOLD_MIN, DESPIKE_THRESHOLD_MAX]
        or window_size is even.

    Notes
    -----
    Algorithm (FR-011 to FR-016):
    1. Calculate median(Y) and MAD (Median Absolute Deviation)
    2. Compute modified Z-scores: z_i = 0.6745 * (Y_i - median) / MAD
    3. Flag as spike if |z_i| > threshold
    4. Replace spikes with local median (window_size neighbors)

    The factor 0.6745 converts MAD to standard deviation units for
    normally distributed data (consistency factor).

    Examples
    --------
    >>> y = np.array([100., 102., 101., 99., 500., 98., 101., 100., 102., 99.])  # index 4 is a spike
    >>> y_clean, mask = remove_spikes(y, threshold=6.0)
    >>> print(mask)
    [False False False False  True False False False False False]
    >>> print(y_clean[4])  # Replaced with local median
    100.0

    Arrays shorter than 10 points are returned unchanged (too short for
    meaningful spike detection):

    >>> y_short = np.array([100., 102., 500., 98., 101.])
    >>> y_clean, mask = remove_spikes(y_short, threshold=6.0)
    >>> mask.any()
    False

    References
    ----------
    - Iglewicz & Hoaglin (1993), "How to Detect and Handle Outliers"
    - Modified Z-score: robust alternative to standard Z-score
    """
    # Validation
    if not (DESPIKE_THRESHOLD_MIN <= threshold <= DESPIKE_THRESHOLD_MAX):
        raise ValueError(
            f"threshold must be in [{DESPIKE_THRESHOLD_MIN}, {DESPIKE_THRESHOLD_MAX}] (got {threshold})"
        )

    if window_size % 2 == 0:
        raise ValueError(f"window_size must be odd (got {window_size})")

    if len(y) < 10:
        # Too short for meaningful spike detection
        return y.copy(), np.zeros(len(y), dtype=bool)

    # Calculate global median and MAD
    median_y = np.median(y)
    mad = median_abs_deviation(y, nan_policy='omit')

    # Handle edge case: constant signal (MAD = 0)
    if mad < 1e-10:
        # No spikes if signal is constant
        return y.copy(), np.zeros(len(y), dtype=bool)

    # Calculate modified Z-scores
    # Factor 0.6745 converts MAD to std deviation units
    z_scores = 0.6745 * np.abs(y - median_y) / mad

    # Detect spikes
    spike_mask = z_scores > threshold

    # Replace each flagged pixel by the median of its neighbours.
    #
    # A *ray* -- a flagged run no wider than the window -- is replaced by the
    # median of its *clean* neighbours, the window widening up to 4x until two
    # are in it, as `remove_spikes_local` does (v5.54.0). Until then the
    # neighbours included the other flagged pixels, so a 3-pixel ray at +2000
    # kept +1000 on all three pixels and went into the fit unflagged: one or
    # two flagged neighbours are outvoted by the median of four, three are not.
    # A one-pixel ray sees exactly the neighbours it always did.
    #
    # A flagged run *wider* than the window is not a cosmic ray but a real
    # peak scored against a low threshold (Silicon's 6, MoS2's 8: the whole
    # spectrum's MAD is the yardstick, see the docstring). Those pixels keep
    # the pre-v5.54.0 replacement, the median of the fixed window with flagged
    # pixels included -- a 5-point median filter over the peak top -- so every
    # number a low-threshold preset ever produced is unchanged; the
    # clean-neighbour rule would flatten the peak to the baseline instead.
    y_clean = y.copy()
    half_window = window_size // 2
    flagged = np.flatnonzero(spike_mask)
    runs = np.split(flagged, np.flatnonzero(np.diff(flagged) > 1) + 1) if flagged.size else []

    for run in runs:
        is_ray = len(run) <= window_size
        for idx in run:
            if is_ray:
                for half in range(half_window, 4 * half_window + 1):
                    start, end = max(0, idx - half), min(len(y), idx + half + 1)
                    neighbors = [y[i] for i in range(start, end) if i != idx and not spike_mask[i]]
                    if len(neighbors) >= 2:
                        y_clean[idx] = np.median(neighbors)
                        break
                else:
                    y_clean[idx] = median_y
            else:
                start, end = max(0, idx - half_window), min(len(y), idx + half_window + 1)
                neighbors = [y[i] for i in range(start, end) if i != idx]
                y_clean[idx] = np.median(neighbors) if neighbors else median_y

    return y_clean, spike_mask




def remove_spikes_local(
    y: np.ndarray,
    threshold: float = 10.0,
    window: int = 3,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Remove cosmic-ray spikes by their jumps (Whitaker-Hayes); the PL despiker
    since v5.41.0.

    `remove_spikes` scores each point against the *whole* spectrum's median
    and MAD, so on a PL spectrum -- one broad band over a flat background --
    the band itself scores as a spike: on golden HKU PL_1 it flagged 303 of
    1650 points and smoothed the band (raw maximum -0.6 %, Raw FWHM +0.3 nm,
    up to +5 nm on HADH22). And until v5.54.0 it replaced each flagged point by
    the median of neighbours that were themselves flagged, so a 3-pixel ray
    kept half its height.

    Here a point is scored by the *step* to its neighbour, against the MAD of
    all steps: a band rises over tens of points, a ray in one. Both ends of a
    large step are flagged, and each flagged point is replaced by the mean of
    the unflagged points within `window` (widened up to 4 x `window` until two
    are found). On 53 HA/VA production PL spectra at threshold 10: a clean
    spectrum has ~10 noise points touched (median), its maximum and Raw FWHM
    unchanged; a ray of 20 x the noise is removed in 98 % of spectra, 40 x in
    all; a 3-pixel ray leaves under 1 %.

    Parameters and return value as `remove_spikes`; `threshold` is in the same
    [DESPIKE_THRESHOLD_MIN, DESPIKE_THRESHOLD_MAX] range but scores steps, not
    levels, so the same number is not the same sensitivity.
    """
    if not (DESPIKE_THRESHOLD_MIN <= threshold <= DESPIKE_THRESHOLD_MAX):
        raise ValueError(
            f"threshold must be in [{DESPIKE_THRESHOLD_MIN}, {DESPIKE_THRESHOLD_MAX}] (got {threshold})"
        )
    y = np.asarray(y, dtype=float)
    if len(y) < 10:
        return y.copy(), np.zeros(len(y), dtype=bool)

    steps = np.diff(y)
    mad = median_abs_deviation(steps, nan_policy="omit")
    if mad < 1e-10:
        return y.copy(), np.zeros(len(y), dtype=bool)
    jump = 0.6745 * np.abs(steps - np.median(steps)) / mad > threshold
    spike_mask = np.zeros(len(y), dtype=bool)
    spike_mask[1:] |= jump
    spike_mask[:-1] |= jump

    y_clean = y.copy()
    for idx in np.flatnonzero(spike_mask):
        for half in range(window, 4 * window + 1):
            lo, hi = max(0, idx - half), min(len(y), idx + half + 1)
            keep = ~spike_mask[lo:hi]
            if keep.sum() >= 2:
                y_clean[idx] = y[lo:hi][keep].mean()
                break
    return y_clean, spike_mask
