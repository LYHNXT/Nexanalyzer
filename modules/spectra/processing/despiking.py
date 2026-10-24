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

    # Replace spikes with local median
    y_clean = y.copy()
    spike_indices = np.where(spike_mask)[0]

    for idx in spike_indices:
        # Define window around spike
        half_window = window_size // 2
        start = max(0, idx - half_window)
        end = min(len(y), idx + half_window + 1)

        # Get neighbors (excluding the spike itself if possible)
        neighbors = []
        for i in range(start, end):
            if i != idx:
                neighbors.append(y[i])

        # Replace with median of neighbors
        if len(neighbors) > 0:
            y_clean[idx] = np.median(neighbors)
        else:
            # Fallback: use global median (edge case for very small arrays)
            y_clean[idx] = median_y

    return y_clean, spike_mask


