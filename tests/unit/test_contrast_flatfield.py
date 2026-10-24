"""contrast.flatfield runs its three channels on threads; the numbers must not notice.

`test_om_contrast.py` locks the end-to-end percentages on real frames; this
file pins the narrower promise the threading rests on -- each channel's
arithmetic is exactly the sequential one -- on synthetic frames of both
types, so a later change that makes the channels interact fails here with a
pixel-level diff rather than as a 1e-6 coverage drift there.
"""

import numpy as np
import pytest
from scipy import ndimage as ndi

from modules.optical.processing import contrast


def _sequential_flatfield(a, aperture, frame_type, ff_divisor=None):
    """The pre-threading implementation, verbatim."""
    H, W = a.shape[:2]
    if ff_divisor is None:
        ff_divisor = contrast.FF_SIGMA_CIRC if frame_type == "circular" else contrast.FF_SIGMA_RECT
    s = max(H, W) / ff_divisor
    out = a.copy()
    for ch in range(3):
        plane = a[..., ch].copy()
        if frame_type == "circular":
            plane[~aperture] = np.median(plane[aperture])
        bg = ndi.gaussian_filter(plane, sigma=s, mode="nearest")
        out[..., ch] = a[..., ch] / np.maximum(bg, 1) * bg[aperture].mean()
    return out


def _frame(seed, h=120, w=160):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    gradient = 40 * (xx / w) + 20 * (yy / h)
    base = np.stack([150 + gradient, 170 + gradient, 130 + 0.5 * gradient], axis=-1)
    return base + rng.normal(0, 3, size=(h, w, 3))


@pytest.mark.parametrize("frame_type", ["rectangular", "circular"])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_threaded_output_is_bit_identical_to_sequential(frame_type, seed):
    a = _frame(seed)
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    if frame_type == "circular":
        aperture = (yy - h / 2) ** 2 + (xx - w / 2) ** 2 < (min(h, w) / 2.2) ** 2
    else:
        aperture = np.ones((h, w), dtype=bool)

    got = contrast.flatfield(a, aperture, frame_type)
    want = _sequential_flatfield(a, aperture, frame_type)

    assert got.dtype == want.dtype
    assert np.array_equal(got, want)


def test_the_input_frame_is_not_modified():
    a = _frame(3)
    before = a.copy()
    contrast.flatfield(a, np.ones(a.shape[:2], dtype=bool), "rectangular")
    assert np.array_equal(a, before)


def test_a_margin_that_leaves_no_pixels_says_so(monkeypatch):
    """Was numpy's "zero-size array to reduction", from deep in histogram_stats."""
    a = _frame(4)
    empty = np.zeros(a.shape[:2], dtype=bool)
    monkeypatch.setattr(contrast, "make_mask", lambda a, margin=None: (empty, empty, "rectangular"))

    with pytest.raises(ValueError, match="no pixels"):
        contrast.analyse_array(a, "2L")
