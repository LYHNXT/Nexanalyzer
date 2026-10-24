"""
Baseline correction algorithms for spectroscopy data.

This module implements:
- Polynomial baseline fitting and subtraction
- Asymmetric Least Squares (ALS) baseline correction
- Rolling Ball (morphological opening) baseline
- Spline (piecewise cubic) baseline
- airPLS (Adaptive Iteratively Reweighted Penalized Least Squares)
- Peak masking/exclusion for Polynomial and ALS
- Baseline quality metrics

References
----------
- Eilers, P. H. C. & Boelens, H. F. M. (2005).
  "Baseline Correction with Asymmetric Least Squares Smoothing".
  Leiden University Medical Centre Report.
- Zhang, Z. M. et al. (2010). "Baseline correction using adaptive
  iteratively reweighted penalized least squares". Analyst 135:1138-1146.
"""

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve
from numpy.polynomial import Polynomial
from typing import Tuple


def baseline_polynomial_with_mask(
    x: np.ndarray,
    y: np.ndarray,
    degree: int = 3,
    exclusions: list = None
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Polynomial baseline with excluded regions (peak masking).

    Parameters
    ----------
    x, y, degree : Same as baseline_polynomial()
    exclusions : list of tuple, optional
        List of (x_min, x_max) ranges to exclude from fitting.
        Baseline will interpolate through these regions.

    Returns
    -------
    y_corrected : np.ndarray
        Baseline-corrected intensity.
    baseline : np.ndarray
        Fitted baseline curve (interpolates through excluded regions).
    """
    if exclusions is None or len(exclusions) == 0:
        # No masking, use standard polynomial
        return baseline_polynomial(x, y, degree)

    # Create mask (True = include in fit, False = exclude)
    mask = np.ones(len(x), dtype=bool)
    for x_min, x_max in exclusions:
        mask &= ~((x >= x_min) & (x <= x_max))

    # Fit polynomial only to masked (non-excluded) data
    x_fit = x[mask]
    y_fit = y[mask]

    if len(x_fit) < degree + 1:
        raise ValueError(
            f"Not enough points for degree {degree} polynomial after exclusions. "
            f"Need at least {degree + 1}, got {len(x_fit)}."
        )

    p = Polynomial.fit(x_fit, y_fit, degree)

    # Evaluate baseline over full range (interpolates through excluded regions)
    baseline = p(x)

    # Subtract baseline
    y_corrected = y - baseline

    return y_corrected, baseline


def baseline_als_with_mask(
    x: np.ndarray,
    y: np.ndarray,
    lambda_: float = 10000.0,
    p: float = 0.001,
    max_iter: int = 10,
    exclusions: list = None
) -> Tuple[np.ndarray, np.ndarray]:
    """
    ALS baseline with excluded regions (peak masking).

    Parameters
    ----------
    x, y, lambda_, p, max_iter : Same as baseline_als()
    exclusions : list of tuple, optional
        List of (x_min, x_max) ranges to exclude from fitting.

    Returns
    -------
    y_corrected : np.ndarray
        Baseline-corrected intensity.
    baseline : np.ndarray
        Fitted baseline curve.
    """
    if exclusions is None or len(exclusions) == 0:
        # No masking, use standard ALS
        return baseline_als(x, y, lambda_, p, max_iter)

    # Run standard ALS but force zero weight in excluded regions
    n = len(y)

    # Build 2nd derivative operator
    D = sparse.diags([1, -2, 1], [0, 1, 2], shape=(n - 2, n))
    D_T_D = D.T @ D

    # Excluded points carry zero weight on every iteration. Built once.
    excluded = np.zeros(n, dtype=bool)
    for x_min, x_max in exclusions:
        excluded |= (x >= x_min) & (x <= x_max)
    # With every point excluded the solve returns a baseline of exactly 0 and
    # "corrects" nothing; with a handful left it draws a wild line through
    # them. Either way the fit would run on the raw offset without a word --
    # the polynomial path already refuses the same case.
    if np.count_nonzero(~excluded) < 10:
        raise ValueError(
            f"Not enough points outside the exclusion ranges for an ALS baseline "
            f"({np.count_nonzero(~excluded)} of {n}); narrow the exclusions or widen the window."
        )

    # Initialize weights
    w = np.ones(n)
    w[excluded] = 0.0

    # Iterative fitting
    for _ in range(max_iter):
        # Build weighted matrix
        W = sparse.diags(w, 0, shape=(n, n))
        A = W + lambda_ * D_T_D

        # Convert to CSC format for spsolve
        A = A.tocsc()

        # Solve
        baseline = spsolve(A, w * y)

        # Update weights (but keep excluded regions at 0)
        w_new = p * (y > baseline) + (1 - p) * (y <= baseline)

        # Restore zero weights in excluded regions
        w_new[excluded] = 0.0

        # Check convergence
        if np.allclose(w, w_new, rtol=1e-4):
            break

        w = w_new

    # Subtract baseline
    y_corrected = y - baseline

    return y_corrected, baseline


def baseline_polynomial(
    x: np.ndarray,
    y: np.ndarray,
    degree: int = 3
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Fit and subtract polynomial baseline from spectrum.

    Parameters
    ----------
    x : np.ndarray
        Wavenumber or wavelength array (1D, float64).
    y : np.ndarray
        Intensity array (1D, float64, same length as x).
    degree : int, default=3
        Polynomial degree (1-10).
        Higher = more flexible (may overfit).
        Recommended: 2-3 for simple backgrounds, 4-6 for complex.

    Returns
    -------
    y_corrected : np.ndarray
        Baseline-corrected intensity (y - baseline).
    baseline : np.ndarray
        Fitted baseline curve (same length as x).

    Raises
    ------
    ValueError
        If degree is outside [1, 10] or arrays have different lengths.

    Notes
    -----
    Algorithm (FR-017, FR-019, FR-024):
    - Uses numpy.polynomial.Polynomial.fit() (orthogonal polynomials)
    - Robust to numerical errors for high degrees
    - Suitable for simple fluorescence backgrounds

    For complex baselines with sharp features, consider ALS instead.

    Examples
    --------
    >>> x = np.linspace(0, 1000, 1000)
    >>> y_raw = x * 0.1 + np.sin(x / 100) * 10  # Signal on sloped baseline
    >>> y_corr, baseline = baseline_polynomial(x, y_raw, degree=2)
    >>> print(baseline.shape)
    (1000,)
    """
    # Validation
    if not (1 <= degree <= 10):
        raise ValueError(f"degree must be in [1, 10] (got {degree})")

    if len(x) != len(y):
        raise ValueError(f"x and y must have same length (got {len(x)} vs {len(y)})")

    if len(x) < degree + 1:
        raise ValueError(
            f"Need at least {degree + 1} points for degree {degree} polynomial "
            f"(got {len(x)} points)"
        )

    # Fit polynomial
    p = Polynomial.fit(x, y, degree)

    # Evaluate baseline
    baseline = p(x)

    # Subtract baseline
    y_corrected = y - baseline

    return y_corrected, baseline


def baseline_als(
    x: np.ndarray,
    y: np.ndarray,
    lambda_: float = 10000.0,
    p: float = 0.001,
    max_iter: int = 10
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Asymmetric Least Squares (ALS) baseline correction.

    Parameters
    ----------
    x : np.ndarray
        Wavenumber or wavelength array (1D, float64).
        Used only for validation; ALS operates on indices.
    y : np.ndarray
        Intensity array (1D, float64).
    lambda_ : float, default=10000.0
        Smoothness parameter (1e3 to 1e6).
        Higher = smoother baseline.
        Recommended: 1e4 for typical Raman/PL.
    p : float, default=0.001
        Asymmetry parameter (0.001 to 0.1).
        Lower = more asymmetric (weights points below baseline).
        Recommended: 0.001-0.01 for fluorescence removal.
    max_iter : int, default=10
        Maximum iterations for convergence.
        Typically converges in 5-10 iterations.

    Returns
    -------
    y_corrected : np.ndarray
        Baseline-corrected intensity (y - baseline).
    baseline : np.ndarray
        Fitted baseline curve (same length as x).

    Raises
    ------
    ValueError
        If parameters are outside valid ranges.

    Notes
    -----
    Algorithm (FR-018, FR-019, FR-024):
    - Iteratively fits smooth curve that preferentially weights points
      below the current estimate (asymmetric weighting)
    - Uses sparse matrix solver for efficiency
    - Excellent for removing broad fluorescence backgrounds

    The algorithm minimizes:
        ||W(y - z)||² + λ||Dz||²
    where W is asymmetric weight matrix, D is 2nd derivative operator,
    and z is the baseline.

    Examples
    --------
    >>> y_corr, baseline = baseline_als(x, y, lambda_=1e4, p=0.001)
    >>> print(f"Baseline range: {baseline.min():.1f} - {baseline.max():.1f}")

    References
    ----------
    Eilers & Boelens (2005), "Baseline Correction with Asymmetric Least Squares"
    """
    # Validation
    if len(x) != len(y):
        raise ValueError(f"x and y must have same length (got {len(x)} vs {len(y)})")

    if not (1000.0 <= lambda_ <= 1000000.0):
        raise ValueError(f"lambda_ must be in [1e3, 1e6] (got {lambda_})")

    if not (0.001 <= p <= 0.1):
        raise ValueError(f"p must be in [0.001, 0.1] (got {p})")

    if max_iter < 1:
        raise ValueError(f"max_iter must be >= 1 (got {max_iter})")

    n = len(y)

    if n < 10:
        raise ValueError(f"Need at least 10 points for ALS (got {n})")

    # Build 2nd derivative operator (sparse matrix)
    # D is (n-2) x n matrix
    D = sparse.diags([1, -2, 1], [0, 1, 2], shape=(n - 2, n))

    # Precompute D^T * D
    D_T_D = D.T @ D

    # Initialize weights (all equal initially)
    w = np.ones(n)

    # Iterative fitting
    for _ in range(max_iter):
        # Build weighted matrix: W + λD^T*D
        W = sparse.diags(w, 0, shape=(n, n))
        A = W + lambda_ * D_T_D

        # Convert to CSC format for spsolve
        A = A.tocsc()

        # Solve: (W + λD^T*D) z = W y
        baseline = spsolve(A, w * y)

        # Update weights (asymmetric)
        # Points above baseline get low weight (p)
        # Points below baseline get high weight (1-p)
        w_new = p * (y > baseline) + (1 - p) * (y <= baseline)

        # Check convergence
        if np.allclose(w, w_new, rtol=1e-4):
            break

        w = w_new

    # Subtract baseline
    y_corrected = y - baseline

    return y_corrected, baseline
