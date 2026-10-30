"""
Data models for workflow preset system.

This module provides dataclasses for material-specific processing presets
that enable automated workflow execution.
"""

from dataclasses import dataclass, field, fields
from typing import Dict, List, Tuple, Optional
from .peak import MAX_PEAKS, PeakDefinition
from .spectrum import DESPIKE_THRESHOLD_MAX, DESPIKE_THRESHOLD_MIN

#: Layers an optical block may be keyed by. The stored form, never the words
#: `contrast.layer_word` prints for them.
OPTICAL_LAYERS = ("1L", "2L")


def parse_exclusion_ranges(exclusion_str: Optional[str]) -> List[Tuple[float, float]]:
    """
    Parse exclusion ranges from string format.

    Parameters
    ----------
    exclusion_str : str or None
        Format: "min1-max1; min2-max2; ..."
        Example: "1200-1400; 2600-2800"

    Returns
    -------
    list of tuple
        List of (x_min, x_max) tuples

    Raises
    ------
    ValueError
        If format is invalid
    """
    if not exclusion_str:
        return []

    ranges = []
    for pair in exclusion_str.split(';'):
        pair = pair.strip()
        if not pair:
            continue

        parts = pair.split('-')
        if len(parts) != 2:
            raise ValueError(f"Invalid exclusion range format: '{pair}'. Expected 'min-max'")

        try:
            x_min = float(parts[0].strip())
            x_max = float(parts[1].strip())
        except ValueError:
            raise ValueError(f"Invalid numbers in exclusion range: '{pair}'")

        if x_min >= x_max:
            raise ValueError(f"Invalid exclusion range: x_min ({x_min}) must be < x_max ({x_max})")

        ranges.append((x_min, x_max))

    return ranges


@dataclass
class PeakTemplate:
    """
    Peak template with center tolerance for flexible fitting.

    This is used in presets to define initial peak guesses with
    flexible center position bounds (center ± tolerance).
    """
    peak_label: str
    center: float
    center_tolerance: float  # ± tolerance for center bounds
    width_fwhm: float
    shape: float  # Voigt mixing: 0=Gaussian, 1=Lorentzian
    color: str  # Hex color for plotting

    def to_peak_definition(
        self,
        mode: str,
        x_range: Tuple[float, float],
        y_max: float,
        spectral_resolution: float
    ) -> PeakDefinition:
        """
        Convert template to PeakDefinition with auto-calculated bounds.

        The template's `center_tolerance` is carried onto the PeakDefinition
        rather than baked into its bounds, because `fit_voigt_peaks` re-runs
        `calculate_auto_bounds` and would otherwise overwrite them with the mode
        default.

        Parameters
        ----------
        mode : str
            "Raman" or "PL" (affects default bounds calculation)
        x_range : tuple
            (x_min, x_max) of spectrum
        y_max : float
            Maximum Y value of spectrum
        spectral_resolution : float
            Median spacing between X points

        Returns
        -------
        PeakDefinition
            Configured peak definition ready for fitting
        """
        peak = PeakDefinition(
            center=self.center,
            # Placeholder: PeakDefinition.intensity is never consulted by the
            # fitter (auto-estimated from data at fit time; see PeakDefinition's
            # own docstring), so PeakTemplate has no intensity field of its own.
            intensity=1.0,
            width_fwhm=self.width_fwhm,
            label=self.peak_label,
            shape=self.shape,
            color=self.color,
            center_tolerance=self.center_tolerance,
        )

        # Calculate auto-bounds using existing logic. This reads
        # center_tolerance, so the bounds it writes are the template's.
        peak.calculate_auto_bounds(mode, x_range, y_max, spectral_resolution)

        return peak

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export."""
        return {
            "peak_label": self.peak_label,
            "center": self.center,
            "center_tolerance": self.center_tolerance,
            "width_fwhm": self.width_fwhm,
            "shape": self.shape,
            "color": self.color,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PeakTemplate":
        """Deserialize from dictionary."""
        return cls(**data)


#: h*c in eV*nm, as fitting.HC_EV_NM (models must not import processing).
_HC_EV_NM = 1239.84198
#: Slack (meV) on the templates-respect-the-gap check. A fixed gap
#: (tolerance_meV = 0) asks for an exact equality that no float centre can
#: meet: 755 nm and the 30 meV centre computed from it sit 30.00000000000003
#: meV apart. One micro-meV is far below anything a spectrum resolves.
_GAP_CHECK_SLACK_MEV = 1e-6


@dataclass
class EnergyGap:
    """PL only (v5.46.0): peak `lower` sits `gap_meV` +- `tolerance_meV` below
    peak `upper` in photon energy -- a trion below its exciton. See
    `fitting.fit_voigt_peaks(gaps=)`. Tolerance 0 fixes the gap."""

    upper: str
    lower: str
    gap_meV: float
    tolerance_meV: float = 0.0

    def as_fit_tuple(self) -> Tuple[str, str, float, float]:
        return (self.upper, self.lower, float(self.gap_meV), float(self.tolerance_meV))

    def to_dict(self) -> dict:
        return {"upper": self.upper, "lower": self.lower,
                "gap_meV": self.gap_meV, "tolerance_meV": self.tolerance_meV}

    @classmethod
    def from_dict(cls, data: dict) -> "EnergyGap":
        return _construct(cls, dict(data), what="energy gap")


BASELINE_ALGORITHMS = ("Polynomial", "ALS", "None (Skip)")


def _baseline_errors(algorithm: str, degree, lam, p) -> List[str]:
    """What is wrong with one set of baseline settings; shared by blocks and windows."""
    errors = []
    if algorithm not in BASELINE_ALGORITHMS:
        errors.append(
            f"Invalid baseline_algorithm: {algorithm} (must be one of {list(BASELINE_ALGORITHMS)})"
        )
    if algorithm == "Polynomial":
        if degree is None or not (1 <= degree <= 10):
            errors.append(f"Polynomial algorithm requires baseline_degree in [1, 10], got {degree}")
    if algorithm == "ALS":
        if lam is None or not (1000 <= lam <= 1000000):
            errors.append(f"ALS algorithm requires baseline_lambda in [1e3, 1e6], got {lam}")
        if p is None or not (0.001 <= p <= 0.1):
            errors.append(f"ALS algorithm requires baseline_p in [0.001, 0.1], got {p}")
    return errors


@dataclass
class FitWindow:
    """One stretch of the spectrum with its own baseline and its own fit (v5.22.0).

    A block with fit windows does not fit its peaks in one go. Each window is
    cut out of the despiked spectrum, baseline-corrected on its own, and fitted
    with the peaks whose `center` falls inside it; the windows' results are then
    put side by side into one corrected spectrum, one fit and one result, so the
    plots, tables, ratios and the QC Report read them exactly as they read a
    single fit.

    Why: WSe2's Raman band holds peaks 300 cm-1 apart that share no line shape,
    and one 32-variable fit across all of them took a median 29 s per spectrum
    against 4 s for three small ones, on 91 real HA1P01 spectra.

    A window owns `x_min <= x < x_max`; the last window of a block also owns its
    own `x_max`. That rule assigns both the data points and the peaks, so a
    point or a peak on a shared edge belongs to exactly one window.

    The baseline fields default to `None`, meaning "the block's setting", so a
    window states only what it does differently -- typically a stiffer or
    different baseline under a broad feature. `exclusion_ranges` likewise:
    `None` inherits the block's, `""` means none.
    """

    x_min: float
    x_max: float
    baseline_algorithm: Optional[str] = None
    baseline_degree: Optional[int] = None
    baseline_lambda: Optional[float] = None
    baseline_p: Optional[float] = None
    exclusion_ranges: Optional[str] = None

    def contains(self, x, last: bool):
        """Whether `x` (scalar or array) lies in this window under the edge rule above."""
        return (x >= self.x_min) & ((x <= self.x_max) if last else (x < self.x_max))

    def resolved(self, block: "TechniquePreset") -> dict:
        """The baseline settings this window runs with, the block's filling every None."""
        return {
            "baseline_algorithm": self.baseline_algorithm or block.baseline_algorithm,
            "baseline_degree": self.baseline_degree if self.baseline_degree is not None else block.baseline_degree,
            "baseline_lambda": self.baseline_lambda if self.baseline_lambda is not None else block.baseline_lambda,
            "baseline_p": self.baseline_p if self.baseline_p is not None else block.baseline_p,
            "exclusion_ranges": (self.exclusion_ranges if self.exclusion_ranges is not None
                                 else block.exclusion_ranges),
        }

    def to_dict(self) -> dict:
        return {
            "x_min": self.x_min,
            "x_max": self.x_max,
            "baseline_algorithm": self.baseline_algorithm,
            "baseline_degree": self.baseline_degree,
            "baseline_lambda": self.baseline_lambda,
            "baseline_p": self.baseline_p,
            "exclusion_ranges": self.exclusion_ranges,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FitWindow":
        return _construct(cls, dict(data), what="fit window")


@dataclass
class TechniquePreset:
    """Processing settings and peak templates for one technique of a material.

    This is everything the old flat `MaterialPreset` held except the fields
    that identify the material rather than the measurement (`material_name`,
    `enabled`, `description`) and `mode`, which is no longer stored at all: a
    block's technique is the name of the slot it sits in.

    Every field is defaulted, so a block can be created empty and filled in by
    the editor.
    """

    # X-range settings
    x_range_enabled: bool = False
    x_min: Optional[float] = None
    x_max: Optional[float] = None

    # De-spiking settings
    despike_threshold: float = 8.0

    # Baseline settings
    baseline_algorithm: str = "None (Skip)"  # "Polynomial", "ALS", "None (Skip)"
    baseline_degree: Optional[int] = None    # For Polynomial
    baseline_lambda: Optional[float] = None  # For ALS
    baseline_p: Optional[float] = None       # For ALS

    # Peak templates
    peak_templates: List[PeakTemplate] = field(default_factory=list)

    #: Fit iteration budget: Levenberg-Marquardt iterations, passed to lmfit
    #: as `max_iterations * (variables + 1)` evaluations since v5.21.0, and
    #: spent per fit window when the block has windows. Per technique, not per
    #: material: a PL doublet on a noisy background and a two-peak Raman
    #: spectrum converge at very different speeds, and the block is where every
    #: other fit setting already lives. Until v5.2.0 this was a sidebar slider
    #: living in session state, which meant the number that produced a fit was
    #: never written down anywhere -- not in the preset, not in the QC report,
    #: not in the CSV -- so a run could not be reproduced from its preset alone.
    max_iterations: int = 2000

    #: Starting points per fit (v5.23.0): the preset's own, plus perturbed ones;
    #: the best fit is kept. See `fitting.fit_voigt_peaks(starts=)`. 1 = one
    #: fit from the preset, as before. Per fit window when the block has windows.
    fit_starts: int = 1

    #: Label of the peak that must fit tallest (v5.26.0), or None. Every other
    #: peak's fitted maximum is bounded to at most this one's; see
    #: `fitting.fit_voigt_peaks(dominant=)`. MoS2 PL: A0 over A- and B.
    dominant_peak: Optional[str] = None

    #: PL only (v5.46.0): photon-energy distances held between pairs of peaks
    #: (WSe2 Exciton-Trion, MoS2 A0-A-); see `EnergyGap`. Empty: every centre
    #: fits freely within its own window, as before.
    energy_gaps: List[EnergyGap] = field(default_factory=list)

    #: Raman only (v5.24.0): shift the axis so the laser line sits at 0 cm-1
    #: before anything else runs. See `processing/zero_line.py`. A spectrum with
    #: no clear line is left as recorded.
    zero_line_calibration: bool = False

    # Semicolon-separated ranges (e.g., "1200-1400; 2600-2800")
    exclusion_ranges: Optional[str] = None

    #: Empty: one baseline and one fit over the (x-range-cropped) spectrum, as
    #: before v5.22.0. Otherwise each `FitWindow` gets its own baseline and fit
    #: and the results are joined for display; see `FitWindow`.
    fit_windows: List["FitWindow"] = field(default_factory=list)

    def window_peaks(self) -> List[Tuple["FitWindow", List[PeakTemplate]]]:
        """Each fit window with the templates whose centre it owns, in template order."""
        out = []
        last = len(self.fit_windows) - 1
        for i, window in enumerate(self.fit_windows):
            out.append((window, [t for t in self.peak_templates if window.contains(t.center, i == last)]))
        return out

    def validate(self) -> List[str]:
        """
        Error messages for this block alone (empty list if valid).

        The caller prefixes each with the technique name, since a material
        reports all of its blocks' errors together.
        """
        errors = []

        # Despike threshold
        if not (DESPIKE_THRESHOLD_MIN <= self.despike_threshold <= DESPIKE_THRESHOLD_MAX):
            errors.append(
                f"despike_threshold {self.despike_threshold} out of range "
                f"[{DESPIKE_THRESHOLD_MIN}, {DESPIKE_THRESHOLD_MAX}]"
            )

        # Fit iteration budget
        if not (500 <= self.max_iterations <= 20000):
            errors.append(
                f"max_iterations {self.max_iterations} out of range [500, 20000]"
            )

        if not (1 <= self.fit_starts <= 10):
            errors.append(f"fit_starts {self.fit_starts} out of range [1, 10]")

        if self.dominant_peak is not None and self.dominant_peak not in {
                t.peak_label for t in self.peak_templates}:
            errors.append(f"dominant_peak {self.dominant_peak!r} is not one of this block's peak labels")

        errors += self._energy_gap_errors()

        # Baseline
        errors += _baseline_errors(self.baseline_algorithm, self.baseline_degree,
                                   self.baseline_lambda, self.baseline_p)
        if self.exclusion_ranges:
            try:
                parse_exclusion_ranges(self.exclusion_ranges)
            except ValueError as e:
                errors.append(str(e))

        # Fit windows: ordered, non-overlapping, every peak owned by one, none empty
        for i, w in enumerate(self.fit_windows):
            name = f"Fit window {i + 1} ({w.x_min:g} to {w.x_max:g})"
            if w.x_min >= w.x_max:
                errors.append(f"{name}: x_min must be < x_max")
            if i and w.x_min < self.fit_windows[i - 1].x_max:
                errors.append(f"{name}: overlaps the window before it (windows must be in order "
                              f"and may only touch)")
            r = w.resolved(self)
            errors += [f"{name}: {e}" for e in _baseline_errors(
                r["baseline_algorithm"], r["baseline_degree"], r["baseline_lambda"], r["baseline_p"])]
            if r["exclusion_ranges"]:
                try:
                    parse_exclusion_ranges(r["exclusion_ranges"])
                except ValueError as e:
                    errors.append(f"{name}: {e}")
        if self.fit_windows:
            owned = self.window_peaks()
            for i, (w, peaks) in enumerate(owned):
                if not peaks:
                    errors.append(f"Fit window {i + 1} ({w.x_min:g} to {w.x_max:g}) holds no peak centre")
            claimed = {id(t) for _, peaks in owned for t in peaks}
            for t in self.peak_templates:
                if id(t) not in claimed:
                    errors.append(f"Peak {t.peak_label} (centre {t.center:g}) lies in no fit window")

        # X-range validation
        if self.x_range_enabled:
            if self.x_min is None or self.x_max is None:
                errors.append("x_range_enabled=True requires both x_min and x_max")
            elif self.x_min >= self.x_max:
                errors.append(f"x_min ({self.x_min}) must be < x_max ({self.x_max})")

        # Peak templates validation
        if len(self.peak_templates) == 0:
            errors.append("At least one peak template required")
        if len(self.peak_templates) > MAX_PEAKS:
            errors.append(
                f"Maximum {MAX_PEAKS} peaks allowed (got {len(self.peak_templates)})"
            )

        # Validate individual peak templates
        for i, template in enumerate(self.peak_templates):
            if template.center_tolerance <= 0:
                errors.append(
                    f"Peak {i+1} ({template.peak_label}): center_tolerance must be > 0"
                )
            if template.width_fwhm <= 0:
                errors.append(
                    f"Peak {i+1} ({template.peak_label}): width_fwhm must be > 0"
                )
            if not (0.0 <= template.shape <= 1.0):
                errors.append(
                    f"Peak {i+1} ({template.peak_label}): shape must be in [0.0, 1.0]"
                )
            # Validate hex color format
            if not (template.color.startswith('#') and len(template.color) == 7):
                errors.append(
                    f"Peak {i+1} ({template.peak_label}): color must be hex format #RRGGBB"
                )

        return errors

    def _energy_gap_errors(self) -> List[str]:
        """What is wrong with `energy_gaps` (the PL-only rule is MaterialPreset's: a
        block does not know its technique)."""
        errors = []
        by_label = {t.peak_label: t for t in self.peak_templates}
        window_of = {id(t): i for i, (_, peaks) in enumerate(self.window_peaks()) for t in peaks}
        lowers = set()
        for g in self.energy_gaps:
            name = f"energy gap {g.upper} - {g.lower}"
            before = len(errors)
            missing = [lab for lab in (g.upper, g.lower) if lab not in by_label]
            if missing:
                errors.append(f"{name}: {', '.join(map(repr, missing))} is not one of this block's peak labels")
                continue
            if g.upper == g.lower:
                errors.append(f"{name}: upper and lower must be two different peaks")
                continue
            if not g.gap_meV > 0:
                errors.append(f"{name}: gap_meV must be > 0, got {g.gap_meV}")
            if not 0 <= g.tolerance_meV < g.gap_meV:
                errors.append(f"{name}: tolerance_meV must be in [0, gap_meV), got {g.tolerance_meV}")
            if g.lower in lowers:
                errors.append(f"{name}: {g.lower!r} is already the lower peak of another gap")
            lowers.add(g.lower)
            up, lo = by_label[g.upper], by_label[g.lower]
            if self.fit_windows and window_of.get(id(up)) != window_of.get(id(lo)):
                errors.append(f"{name}: the two peaks are in different fit windows")
            # The templates are the fit's starting point, so they must already respect the gap.
            if up.center > 0 and lo.center > 0 and len(errors) == before:
                implied = 1000 * (_HC_EV_NM / up.center - _HC_EV_NM / lo.center)
                if not abs(implied - g.gap_meV) <= g.tolerance_meV + _GAP_CHECK_SLACK_MEV:
                    errors.append(
                        f"{name}: the templates' centres ({up.center:g}, {lo.center:g} nm) are "
                        f"{implied:.1f} meV apart, outside {g.gap_meV:g} +- {g.tolerance_meV:g} meV")
        return errors

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export."""
        return {
            "x_range_enabled": self.x_range_enabled,
            "x_min": self.x_min,
            "x_max": self.x_max,
            "despike_threshold": self.despike_threshold,
            "baseline_algorithm": self.baseline_algorithm,
            "baseline_degree": self.baseline_degree,
            "baseline_lambda": self.baseline_lambda,
            "baseline_p": self.baseline_p,
            "peak_templates": [t.to_dict() for t in self.peak_templates],
            "exclusion_ranges": self.exclusion_ranges,
            "max_iterations": self.max_iterations,
            # Omitted at 1 (and windows when empty), so a block that uses
            # neither serialises exactly as before and older versions read it.
            **({"fit_starts": self.fit_starts} if self.fit_starts != 1 else {}),
            **({"dominant_peak": self.dominant_peak} if self.dominant_peak else {}),
            **({"energy_gaps": [g.to_dict() for g in self.energy_gaps]} if self.energy_gaps else {}),
            **({"zero_line_calibration": True} if self.zero_line_calibration else {}),
            # Omitted when empty, so a block without windows serialises exactly
            # as it did before v5.22.0 and older app versions can still read it.
            **({"fit_windows": [w.to_dict() for w in self.fit_windows]} if self.fit_windows else {}),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TechniquePreset":
        """Deserialize from dictionary."""
        data = dict(data)
        data["peak_templates"] = [
            PeakTemplate.from_dict(t) for t in data.get("peak_templates", [])
        ]
        data["fit_windows"] = [FitWindow.from_dict(w) for w in data.get("fit_windows") or []]
        data["energy_gaps"] = [EnergyGap.from_dict(g) for g in data.get("energy_gaps") or []]
        return _construct(cls, data, what="technique block")


@dataclass
class OpticalParams:
    """
    Optical-contrast tuning for one layer of one material.

    Every field is `None` by default, meaning "use contrast.py's own default".
    That is deliberate: the numbers live in exactly one place, and a material
    with no optical block produces `analyse_frame(**{})`, which is the vendored
    algorithm untouched.

    `mask_margin` and `ff_divisor` are **not** circular-only, despite
    docs/OM_Contrast_Algo.md quoting circular figures for both. Whether a frame
    is circular is decided per image at runtime by `contrast.is_circular`, so a
    preset has no way to address one frame type; an override applies to both,
    and naming it otherwise would promise a precision that cannot exist.

    `ff_divisor` is a **divisor**, not a sigma: the flat-field blur runs at
    max(H, W) / ff_divisor, so a larger number means a *smaller* sigma.

    `marker_margin_sigma`, `marker_min_distance`, and `mask_closing_radius`
    are not part of `_KWARGS` and never reach `analyse_frame`/`classify()` --
    they tune `domain_size.measure_domains()`'s watershed pass instead,
    domain-size-only settings layered on top of the same segmentation. Kept
    on this dataclass rather than a new one because they are still per
    material and layer (a thin, faint 1L flake and a bolder 2L flake can need
    different marker sensitivity, spacing, or edge closing), which is exactly
    what this dataclass already exists to carry.
    """

    nsigma: Optional[float] = None
    minpx: Optional[int] = None
    mask_margin: Optional[float] = None
    ff_divisor: Optional[float] = None
    abs_threshold_below: Optional[float] = None
    abs_threshold_above: Optional[float] = None
    #: How many sigma_noise past a class's own boundary a watershed marker
    #: must clear -- lower catches weaker, flatter domains at the risk of
    #: more noise-driven markers. `None` means
    #: `domain_size.MARKER_MARGIN_SIGMA` (0.5). Not passed to `analyse_frame`
    #: (as_kwargs skips it): it never reaches `classify()`, only
    #: `measure_domains()`.
    marker_margin_sigma: Optional[float] = None
    #: Minimum pixel separation between two accepted watershed markers. `None`
    #: means `domain_size.DEFAULT_MARKER_MIN_DISTANCE` (8), the value the
    #: "above" path was validated against for VBBF46 -- raise it for a
    #: material whose flakes show internal reflection highlights bright
    #: enough to be watershed-split into several markers (and so several
    #: domains) instead of staying one. Not passed to `analyse_frame` either:
    #: like `marker_margin_sigma`, it only tunes `measure_domains()`.
    marker_min_distance: Optional[int] = None
    #: Morphological-closing radius (pixels) applied to the domain-size target
    #: mask before connected-component/marker/watershed. `None` means
    #: `domain_size.DEFAULT_MASK_CLOSING_RADIUS` (0, off) -- the mask is used
    #: exactly as `classify()` produced it. Raise it for a material whose
    #: flakes' ragged, noisy real edges are leaving small fringe fragments
    #: disconnected from the flake's main body, each counted as its own
    #: domain sitting right at or inside the real flake's footprint. Not
    #: passed to `analyse_frame` either: like the two fields above, it only
    #: tunes `measure_domains()`. Too large a radius starts merging
    #: genuinely separate close-together small flakes into one.
    mask_closing_radius: Optional[int] = None
    #: Smallest domain kept, as an equivalent-circle diameter in microns, at 50x
    #: and at 100x (v5.22.0). `None` keeps `domain_size.DEFAULT_MIN_DOMAIN_PX`
    #: (40 working pixels), which is 1.07 um at 50x but 0.53 um at 100x -- a
    #: floor in pixels means a different size at each magnification. Separate
    #: values because the two lenses resolve different sizes: a 0.5 um floor at
    #: 100x changes VBBF26's count by +10 %, while at 50x, where 0.5 um is the
    #: lens's resolution limit, it multiplied HADH51's count 12x. Applied only
    #: when the frame's magnification is calibrated in om_calibration.json.
    min_domain_um_50x: Optional[float] = None
    min_domain_um_100x: Optional[float] = None
    #: False hides the QC Report's domain-size measurement for this material and
    #: layer -- for a film that is continuous by process (MoS2), where there are
    #: no domains to count. `None` means available, as before v5.22.0.
    domain_size_enabled: Optional[bool] = None
    #: Field name -> the `contrast.analyse_frame` keyword it sets.
    _KWARGS = {
        "nsigma": "nsigma",
        "minpx": "minpx",
        "mask_margin": "margin",
        "ff_divisor": "ff_divisor",
    }

    #: Set together or not at all; they become one `abs_threshold` pair.
    _ABS_FIELDS = ("abs_threshold_below", "abs_threshold_above")

    def as_kwargs(self) -> dict:
        """
        Only the fields that are set, keyed as `analyse_frame` wants them.

        An unset field is omitted rather than passed as None, so the defaults
        stay written down once, in contrast.py.

        The two `abs_threshold_*` fields collapse into `analyse_frame`'s single
        `abs_threshold=(below, above)` argument. They are stored apart because a
        preset editor wants two number boxes, and passed together because the
        algorithm wants one pair. Setting them switches thresholding from
        `nsigma` to a fixed contrast; `nsigma` is then unused but still stored,
        so a preset can be switched back without losing its old value.
        """
        out = {
            keyword: getattr(self, name)
            for name, keyword in self._KWARGS.items()
            if getattr(self, name) is not None
        }
        below, above = (getattr(self, f) for f in self._ABS_FIELDS)
        if below is not None and above is not None:
            out["abs_threshold"] = (below, above)
        return out

    def validate(self) -> List[str]:
        """Error messages for this block alone (empty list if valid)."""
        errors = []
        if self.nsigma is not None and not (1.0 <= self.nsigma <= 10.0):
            errors.append(f"nsigma {self.nsigma} out of range [1.0, 10.0]")
        if self.minpx is not None and self.minpx < 1:
            errors.append(f"minpx must be >= 1 (got {self.minpx})")
        if self.mask_margin is not None and not (0.0 <= self.mask_margin < 0.5):
            errors.append(f"mask_margin {self.mask_margin} out of range [0.0, 0.5)")
        if self.ff_divisor is not None and not (1.0 <= self.ff_divisor <= 64.0):
            errors.append(f"ff_divisor {self.ff_divisor} out of range [1.0, 64.0]")
        below, above = (getattr(self, f) for f in self._ABS_FIELDS)
        if (below is None) != (above is None):
            errors.append(
                "abs_threshold_below and abs_threshold_above must be set together "
                "or left unset; one alone does not describe a segmentation"
            )
        for name in self._ABS_FIELDS:
            value = getattr(self, name)
            if value is not None and not (0.5 <= value <= 50.0):
                errors.append(f"{name} {value} out of range [0.5, 50.0] %")
        if self.marker_margin_sigma is not None and not (0.0 <= self.marker_margin_sigma <= 5.0):
            errors.append(
                f"marker_margin_sigma {self.marker_margin_sigma} out of range [0.0, 5.0]"
            )
        if self.marker_min_distance is not None and not (1 <= self.marker_min_distance <= 200):
            errors.append(
                f"marker_min_distance {self.marker_min_distance} out of range [1, 200]"
            )
        if self.mask_closing_radius is not None and not (0 <= self.mask_closing_radius <= 10):
            errors.append(
                f"mask_closing_radius {self.mask_closing_radius} out of range [0, 10]"
            )
        for name in ("min_domain_um_50x", "min_domain_um_100x"):
            value = getattr(self, name)
            if value is not None and not (0.1 <= value <= 20.0):
                errors.append(f"{name} {value} out of range [0.1, 20.0] um")
        return errors

    def min_domain_um_for(self, magnification: Optional[str]) -> Optional[float]:
        """The minimum domain diameter set for `magnification` ("50x", "100X", ...), or None."""
        mag = (magnification or "").strip().lower()
        return {"50x": self.min_domain_um_50x, "100x": self.min_domain_um_100x}.get(mag)

    def to_dict(self) -> dict:
        """Only the fields that are set; absent means "algorithm default"."""
        return {
            name: getattr(self, name)
            for name in tuple(self._KWARGS) + self._ABS_FIELDS
            + ("marker_margin_sigma", "marker_min_distance", "mask_closing_radius",
               "min_domain_um_50x", "min_domain_um_100x", "domain_size_enabled")
            if getattr(self, name) is not None
        }

    #: Keys that older presets carry and this version no longer honours.
    #: Dropped rather than rejected: a stored preset written by v4.6-v5.4 is
    #: still a valid preset, it just names a method that no longer exists.
    _RETIRED = ("adaptive_threshold",)

    @classmethod
    def from_dict(cls, data: dict) -> "OpticalParams":
        """Deserialize from dictionary, ignoring retired keys."""
        data = {k: v for k, v in data.items() if k not in cls._RETIRED}
        return _construct(cls, data, what="optical block")


def _construct(cls, data: dict, what: str):
    """
    `cls(**data)`, but naming the offending key when one is unknown.

    A bare ``TypeError: __init__() got an unexpected keyword argument 'mode'``
    says nothing about which entry in materials.json to go and fix, which is
    the only thing the reader needs to know.
    """
    allowed = {f.name for f in fields(cls)}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise TypeError(
            f"Unknown {what} field(s) {unknown}; expected any of {sorted(allowed)}"
        )
    return cls(**data)


@dataclass
class MaterialPreset:
    """
    Everything the app knows about one material.

    Keyed by material name alone. Technique is no longer part of a preset's
    identity -- it comes from the filename, via
    `parser.detect_mode_from_filename` -- so one material holds a `raman`
    block, a `pl` block, or both, and either may be absent.

    `optical` splits a level further, by layer ("1L", "2L"), because optical
    contrast against SiO2/Si genuinely differs between a monolayer and a
    bilayer, while a Raman or PL spectrum does not: the signal says which peaks
    it has. Layers are stored as "1L"/"2L", never as the words
    `contrast.layer_word` prints for them -- that helper is one-way
    presentation and passes unknown values through, so keying persisted JSON by
    its output would orphan every stored block the day the wording changed, and
    would let the file express a "Trilayer" the UI can never select.
    """

    material_name: str
    enabled: bool = True
    description: str = ""

    raman: Optional[TechniquePreset] = None
    pl: Optional[TechniquePreset] = None
    optical: Dict[str, OpticalParams] = field(default_factory=dict)

    #: Technique name -> the attribute holding its block.
    _BLOCKS = {"Raman": "raman", "PL": "pl"}

    def block_for(self, mode: str) -> Optional[TechniquePreset]:
        """The block for `mode` ("Raman"/"PL"), or None if there isn't one."""
        attribute = self._BLOCKS.get(mode)
        return getattr(self, attribute) if attribute else None

    def optical_for(self, layer: str) -> "OpticalParams":
        """
        Tuning for `layer`, or an all-defaults block when that layer is untuned.

        Never None: an untuned layer is a legitimate state that runs the
        algorithm's own defaults, and the page says which it is using rather
        than refusing to run.
        """
        return self.optical.get(layer) or OpticalParams()

    def validate(self) -> List[str]:
        """Error messages across every block (empty list if valid)."""
        errors = []

        if self.raman is None and self.pl is None and not self.optical:
            errors.append(
                f"'{self.material_name}' has no Raman, PL or optical settings; "
                f"a preset with no blocks does nothing"
            )

        for mode, attribute in self._BLOCKS.items():
            block = getattr(self, attribute)
            if block is not None:
                errors.extend(f"{mode}: {e}" for e in block.validate())
                if mode != "PL" and block.energy_gaps:
                    errors.append(f"{mode}: energy gaps apply to PL only (the axis must be a wavelength)")

        for layer, params in self.optical.items():
            if layer not in OPTICAL_LAYERS:
                # Reported, not dropped. The loader keeps unknown layers so that
                # "I loaded the file" never quietly means "I deleted part of it";
                # flagging it here is what makes the choice visible.
                errors.append(
                    f"Optical: unknown layer '{layer}' (expected one of "
                    f"{list(OPTICAL_LAYERS)}); it is preserved but unused"
                )
            errors.extend(f"Optical {layer}: {e}" for e in params.validate())

        return errors

    def to_dict(self) -> dict:
        """
        Serialize to dictionary for JSON export.

        Absent blocks are **omitted**, not written as null: "this material has
        no PL" and "this material has an empty PL block" are different states,
        and only omission round-trips the first one.
        """
        out: dict = {
            "material_name": self.material_name,
            "enabled": self.enabled,
            "description": self.description,
        }
        if self.raman is not None:
            out["raman"] = self.raman.to_dict()
        if self.pl is not None:
            out["pl"] = self.pl.to_dict()
        if self.optical:
            out["optical"] = {
                layer: params.to_dict() for layer, params in self.optical.items()
            }
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "MaterialPreset":
        """
        Deserialize from dictionary.

        Nested blocks are reconstructed explicitly: `cls(**data)` would hand
        `raman` a raw dict, and every later attribute access would fail
        somewhere far away from the entry that caused it.
        """
        data = dict(data)
        for attribute in cls._BLOCKS.values():
            if data.get(attribute) is not None:
                data[attribute] = TechniquePreset.from_dict(data[attribute])
            else:
                data.pop(attribute, None)
        if data.get("optical") is not None:
            data["optical"] = {
                layer: OpticalParams.from_dict(params)
                for layer, params in data["optical"].items()
            }
        else:
            data.pop("optical", None)
        return _construct(cls, data, what=f"material '{data.get('material_name')}'")
