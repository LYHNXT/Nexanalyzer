"""
Suggest which layer a film is -- 1L or 2L -- from its frames' colour.

`contrast.py` segments a frame *relative to its own majority population* and
cannot tell which layer that population is: the operator declares it, and a
wrong declaration relabels every class by one layer with nothing on screen to
show it. This module makes that declaration a suggestion instead of a guess.
It never segments anything and never overrides an operator's choice.

What it reads, per frame, after the same load / flat-field / smoothing that
`contrast.analyse_array` does (with contrast.py's default tuning, which is what
the calibration was measured on -- a preset's optical block is not applied):

- **B/G and R/G at the film mode.** Thin-film interference on SiO2/Si tints a
  bilayer bluer and less red than a monolayer. Ratios, not levels: the camera
  auto-exposes, so the film's absolute green level moved ~15 % between months
  on the same tool, which is larger than the 1L/2L difference.
- **The share of the frame 7-14 % darker than the film**, on a log scale. That
  band is where monolayer regions sit inside a bilayer film (1L->2L is a ~10 %
  green step); in a monolayer film it holds only bare substrate, which is
  rarer.

The three combine into one linear score, positive meaning 2L. A wafer's score
is the median over its frames, and `confidence = |2 * sigmoid(score) - 1|`.
Below `CONFIDENCE_FLOOR` the suggestion names no layer and the operator must
choose.

Calibration (v5.20.0): WSe2 only. Labels came from PL and tool -- strong PL =
1L, VA/VB1P01 = 1L, HA1P01 from 2026-09 = 2L, PL overriding the tool -- on 296
wafers. The weights were fitted (class-balanced logistic regression) on the
1,174 frames of known magnification, 50x and 100x pooled: one model was as
accurate as one per magnification, and it does not need the magnification. The
1,249 frames whose magnification was never recorded were held out of the fit,
because their colour (a different white balance) swamps the 1L class if
included; scored afterwards, 97 % of those wafers agree with their label.
Leave-one-wafer-out over all 296 wafers: 94 % agree, 84 % clear the floor and
96 % of those agree, and 6 of the 7 HA wafers whose strong PL marks them 1L
are called 1L. It is weakest at 100x (79 %), where most disagreements are HA
wafers labelled 2L by date that it calls 1L -- their Raman LB/E2g ratio is
monolayer-like too, so the label may be the error. A material with no entry
in `CALIBRATIONS` gets no suggestion.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Sequence

import numpy as np
from scipy import ndimage as ndi

from modules.optical.processing import contrast

#: Below this, the suggestion names no layer. 0.6 is p(2L) outside 0.2-0.8;
#: in calibration 84 % of wafers cleared it and 96 % of those agreed with
#: their label.
CONFIDENCE_FLOOR = 0.6

#: Contrast band, in percent of the film mode, where 1L sits inside a 2L film.
MONOLAYER_BAND = (-14.0, -7.0)
#: The band's share is taken of the pixels within this contrast range, as the
#: calibration's histogram was; bright dust and dark holes beyond it don't count.
_CONTRAST_SPAN = 40.0
#: Added to the band share (percent) before the log, so an empty band is finite.
_BAND_FLOOR = 0.01


@dataclass(frozen=True)
class Calibration:
    """Weights of the linear 2L-vs-1L score; positive means 2L."""

    intercept: float
    blue_green: float
    red_green: float
    log_band: float


CALIBRATIONS: Dict[str, Calibration] = {
    "WSe2": Calibration(intercept=-80.3083, blue_green=82.9188, red_green=-3.3207,
                        log_band=0.5163),
}


@dataclass(frozen=True)
class FrameFeatures:
    blue_green: float
    red_green: float
    band_pct: float          # share of in-span pixels inside MONOLAYER_BAND, %


@dataclass(frozen=True)
class LayerSuggestion:
    """`layer` is "1L", "2L", or None when confidence is below the floor."""

    layer: Optional[str]
    confidence: float
    likely: str              # the side the score falls on, even below the floor
    n_frames: int
    features: FrameFeatures  # per-feature medians over the frames


def frame_features(a: np.ndarray) -> FrameFeatures:
    """The three colour features of one loaded frame (see the module docstring)."""
    aperture, valid, ftype = contrast.make_mask(a)
    flat = contrast.flatfield(a, aperture, ftype)
    s = ndi.gaussian_filter(
        flat, sigma=(contrast.SMOOTH_SIGMA, contrast.SMOOTH_SIGMA, 0), mode="nearest"
    )
    g = s[..., 1]
    st = contrast.histogram_stats(g[valid])
    mode = st["mode"]
    noise = min(st["sigma_l"], st["sigma_r"])
    core = valid & (np.abs(g - mode) < noise)
    r, gm, b = (float(np.median(s[..., c][core])) for c in range(3))

    pct = (g[valid] / mode - 1.0) * 100.0
    in_span = np.count_nonzero(np.abs(pct) <= _CONTRAST_SPAN)
    lo, hi = MONOLAYER_BAND
    in_band = np.count_nonzero((pct >= lo) & (pct < hi))
    return FrameFeatures(
        blue_green=b / gm, red_green=r / gm,
        band_pct=100.0 * in_band / max(in_span, 1),
    )


def frame_score(f: FrameFeatures, cal: Calibration) -> float:
    """Linear 2L-vs-1L score of one frame; positive means 2L."""
    return (cal.intercept + cal.blue_green * f.blue_green + cal.red_green * f.red_green
            + cal.log_band * np.log10(f.band_pct + _BAND_FLOOR))


def suggest_from_features(
    features: Sequence[FrameFeatures], material: Optional[str]
) -> Optional[LayerSuggestion]:
    """Combine frames into one suggestion. None if the material is uncalibrated
    or there are no frames -- "no opinion", distinct from a low-confidence one."""
    cal = CALIBRATIONS.get(material or "")
    if cal is None or not features:
        return None
    score = float(np.median([frame_score(f, cal) for f in features]))
    confidence = float(abs(2.0 / (1.0 + np.exp(-score)) - 1.0))
    likely = "2L" if score > 0 else "1L"
    return LayerSuggestion(
        layer=likely if confidence >= CONFIDENCE_FLOOR else None,
        confidence=confidence,
        likely=likely,
        n_frames=len(features),
        features=FrameFeatures(
            blue_green=float(np.median([f.blue_green for f in features])),
            red_green=float(np.median([f.red_green for f in features])),
            band_pct=float(np.median([f.band_pct for f in features])),
        ),
    )


def suggest_layer(paths: Sequence, material: Optional[str]) -> Optional[LayerSuggestion]:
    """Load each frame and suggest a layer for the set. See `suggest_from_features`."""
    if (material or "") not in CALIBRATIONS:
        return None
    return suggest_from_features([frame_features(contrast.load(p)) for p in paths], material)
