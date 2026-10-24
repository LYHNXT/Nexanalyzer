"""
The QC Report's per-technique quality panels: Raman and PL.

Each figure is a grid whose columns are metric families (FWHM, peak centres,
diagnostic ratios) and whose rows are the peaks within each family. Every panel
is a strip plot across the sample's grid positions: the individual fits as a
jittered cloud, a mean-or-median +/- std marker per position, and the pooled
median as a dashed line.

One builder, two techniques, because the drawing never knew anything about
Raman: it reads `peak.label`, `peak.center`, `peak.width_fwhm` and
`peak_intensity(peak)`, all of which exist identically on a PL fit. Everything
technique-specific is a `QualityFigureSpec` value — columns, cleaning rule and
marker statistic travelling together so a caller cannot pair Raman's panels
with PL's cleaning. `results_excel.TechniqueResults` made the same choice for
the workbook; this is that pattern applied to the figure.

**The spec lines.** Red dashed horizontal references (grey solid until
v5.32.0), drawn and never evaluated — this figure reports, it does not judge.
They are literals, not preset data, and there are only three:

    Raman  E2g+A1g FWHM = 7      LA/E2g+A1g = 0.13 (drawn as 13 %)
    PL     Raw FWHM = 40   (the customer spec, judged on the Raw spectrum since v5.32.0;
           on Exciton & Trion at 35 until v5.31.0)

The B2g centre line at 308 was removed in v5.32.0: the customer spec's "~308"
is a presence criterion, which the title states.

There is deliberately **no PL centre spec and no PL ratio spec**. The ancestor
gates its only PL reference line on `if col == "FWHM"`, so the centre panels
never had one, and the 755/780 nm figures in the material preset are fit
*initialisation* guesses — promoting those to specs would invent a tolerance
nobody measured. The unity line the ancestor draws on the PL ratio is grey
dotted with no legend entry and is never called a spec, unlike every line it
does label "(Spec)"; it is left out for the same reason.

Anyone adding a pass/fail verdict needs to decide first what the rule is
(pooled median across the line? any position's mean? a fraction of individual
fits?), and that decision is not encoded here.

**The customer QC spec under the title (v5.31.0).** A WSe2 figure states the
summarized value of each criterion of the customer spec (`CUSTOMER_SPEC`)
beside its limit -- value and limit only, never a verdict or other detail
(v5.32.0). The layer criteria follow the report's reference layer: Raman 1L,
E2g+A1g position (~250 cm-1); Raman 2L, B2g position (~308), E2g+A1g FWHM < 7,
I(LA)/I(E2g+A1g) < 13 %; PL 1L, Exciton FWHM <= 40 nm. Uniformity, for every
layer, is the coefficient of variation (sample std / mean) of the *region
medians* of the main peak's position and FWHM (E2g+A1g, Exciton), so scatter
within a region does not count as wafer non-uniformity; the panels' Robust CV,
over all pooled points, is a different number. Every value comes from the
cleaned records the panels draw, so title and panels cannot disagree.

**Cleaning differs by technique, on purpose.** Raman takes the same 1.5x IQR
cut within each position that `peak_metrics.aggregate_fit_results` takes, so
the figure and the report's summary tables cannot show two different numbers
for one measurement. PL inherits a different rule — a bad-fit floor plus the
widest 5% of each peak's fits dropped across the sample — because that is what
the PL lineage does and its peaks fail differently. It also drops a peak fitted
to near zero (under 2% of its spectrum's strongest), whose centre and width are
then meaningless. Both run downstream of the same R-squared gate.

**The marker statistic differs too.** Raman's ancestor marks each position with
a mean, PL's with a median, and the PL summary schema stores medians only. The
statistic is named in the suptitle, because the difference is invisible in the
mark itself and silently moves every point on the figure.
"""

import io
import dataclasses
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple, Union

import matplotlib

matplotlib.use("Agg")  # no display; must precede the pyplot import

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from core.report import REPORT_DPI  # noqa: E402
from matplotlib import gridspec  # noqa: E402

from ..models.peak import FitResult  # noqa: E402
from ..processing.peak_metrics import (  # noqa: E402
    NEAR_ZERO_FRACTION,
    RAMAN_RATIO_PAIRS,
    iqr_mask,
    peak_intensity,
    peak_is_measured,
    ratio_values_by_point,
    region_values as _region_values,
)

#: Plotly's qualitative sequence, carried over from the inherited scripts so a
#: position keeps the colour it has in the reports people already read. Cycled
#: by index, so more than eleven positions repeat rather than fail.
POSITION_COLORS = (
    "#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
    "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52", "#1CBE4F",
)

_FS_SUPTITLE = 18
_FS_TITLE = 15
_FS_LABEL = 13
_FS_PANEL = 13
_FS_TICK = 12
_FS_REFTEXT = 11

_JITTER = 0.18
_MARKER_OFFSET = 0.28

#: Inches per column and per row. Chosen so a 3x2 grid is the 22x11 the Raman
#: figure has always been, and any other shape scales from it rather than
#: cramming more panels into a fixed canvas.
_COLUMN_W_IN = 22.0 / 3.0
_ROW_H_IN = 11.0 / 2.0


@dataclass(frozen=True)
class PeakPanel:
    """One panel plotting a fitted parameter of a single peak."""

    peak: str
    metric: str          # "center" | "fwhm" | "intensity"
    ylabel: str
    caption: str
    spec: Optional[float] = None
    #: True draws the spec as a nominal value ("~x", grey dashed) rather than a
    #: limit (red dashed) -- MoS2's targets, v5.32.0.
    spec_nominal: bool = False


@dataclass(frozen=True)
class RatioPanel:
    """One panel plotting a per-spectrum intensity ratio of two peaks."""

    numerator: str
    denominator: str
    ylabel: str
    caption: str
    spec: Optional[float] = None
    spec_nominal: bool = False


Panel = Union[PeakPanel, RatioPanel]
PanelColumns = Tuple[Tuple[str, Tuple[Panel, ...]], ...]


@dataclass(frozen=True)
class CleaningRule:
    """Which fitted peaks reach the figure.

    Applied to individual peak records, not whole fits: a spectrum whose Trion
    fit ran wide still contributes its Exciton, exactly as the inherited
    scripts filter rows of a fit table rather than whole spectra.

    ``per_position_iqr`` is the 1.5x Tukey cut taken *within* each grid
    position, matching `peak_metrics.aggregate_fit_results`. It is the whole of
    Raman's rule and none of PL's.

    ``relative_intensity_floor`` drops a peak whose intensity is below that
    fraction of the strongest peak *in the same spectrum*. A component the fit
    drove to zero still reports a centre and a width, but they describe nothing
    -- the parameters are free once the peak has no height -- and plotting them
    puts noise in the same cloud as real fits.
    """

    fwhm_floor: Optional[float] = None
    center_floor: Optional[float] = None
    fwhm_top_fraction: Optional[float] = None
    per_position_iqr: bool = True
    relative_intensity_floor: Optional[float] = None


#: Raman: the same per-position IQR cut the summary tables take, and nothing
#: else. A difference here would put two numbers for one measurement in front
#: of the same reader.
RAMAN_CLEANING = CleaningRule(per_position_iqr=True)

#: PL: `FWHM > 5 nm` rejects fits that collapsed onto noise, a peak under 2% of
#: its own spectrum's strongest is dropped as fitted-to-zero, then the widest 5%
#: of each peak's fits are dropped across the whole sample. No per-position IQR
#: cut: this already removed the tail that cut exists to remove, and taking both
#: would cut real position-to-position spread.
#:
#: The inherited `Center > 700 nm` floor is gone (v5.10.6). It was WSe2's
#: emission band written as a constant, so it discarded every MoS2 peak
#: (620-680 nm) and left MoS2's figure empty. It never removed a WSe2 fit: the
#: fitter's own centre bounds, which carry the preset's tolerances, keep every
#: WSe2 PL peak above 740 nm.
PL_CLEANING = CleaningRule(
    fwhm_floor=5.0,
    fwhm_top_fraction=0.05,
    per_position_iqr=False,
    relative_intensity_floor=NEAR_ZERO_FRACTION,
)

#: How each known Raman ratio is *labelled*: (ylabel, caption, spec). Which
#: ratios exist is not decided here — `peak_metrics.RAMAN_RATIO_PAIRS` decides
#: that, and this module reads it. Splitting it that way is the point: the
#: summary table and this figure must draw the same set of ratios, and an entry
#: added to the constant that nobody labels still gets a panel (below) rather
#: than silently going missing from the figure while appearing in the table.
_RAMAN_RATIO_PRESENTATION = {
    ("LA", "E2g+A1g"): ("I(LA) / I(E₂g+A₁g)", "Defect ratio", 0.13),
    ("2LA", "E2g+A1g"): ("I(2LA) / I(E₂g+A₁g)", "Layer / resonance ratio", None),
    ("C", "LB"): ("I(C) / I(LB)", "Stacking ratio", None),
    ("B2g", "E2g+A1g"): ("I(B₂g) / I(E₂g+A₁g)", "Bilayer ratio", None),
    ("2LA(M)", "A1g"): ("I(2LA(M)) / I(A₁g)", "MoS₂ defect ratio", None),
}


def _raman_ratio_panels(
    pairs: Sequence[Tuple[str, str]] = RAMAN_RATIO_PAIRS,
) -> Tuple[RatioPanel, ...]:
    """A `RatioPanel` per entry of `RAMAN_RATIO_PAIRS`, labelled where known."""
    panels = []
    for numerator, denominator in pairs:
        ylabel, caption, spec = _RAMAN_RATIO_PRESENTATION.get(
            (numerator, denominator),
            (f"I({numerator}) / I({denominator})", f"{numerator} / {denominator}", None),
        )
        panels.append(RatioPanel(numerator, denominator, ylabel, caption, spec=spec))
    return tuple(panels)


#: Ratios drawn in the FWHM column rather than the ratio column.
_UNDER_FWHM = {("B2g", "E2g+A1g")}

#: Pairs in `RAMAN_RATIO_PAIRS` that the inherited (WSe2) figure does not draw.
#: MoS2's defect ratio is reported in the summary table and workbook only; its
#: figure, `MOS2_RAMAN_COLUMNS`, has no ratio column. Left in the WSe2 ratio
#: column it would also make a MoS2 fit "match" the inherited columns.
_NOT_IN_WSE2_FIGURE = {("2LA(M)", "A1g")}

#: Raman's panels. Column order and titles match the inherited script so the
#: output is recognisable to people already reading these reports.
PANEL_COLUMNS: PanelColumns = (
    # 2LA's FWHM panel was dropped in v5.29.0: on most VA films the width sits
    # on its 12 cm-1 cap (it absorbs the second-order continuum), so the panel
    # plotted the limit. Its intensity ratio is in the ratio column instead.
    # The bilayer ratio sits under it (v5.29.0), so a WSe2_QC figure is 2 | 2 | 2.
    ("FWHM", (
        PeakPanel("E2g+A1g", "fwhm", "FWHM (cm⁻¹)", "E₂g + A₁g", spec=7.0),
    ) + tuple(p for p in _raman_ratio_panels(tuple(p for p in RAMAN_RATIO_PAIRS if p not in _NOT_IN_WSE2_FIGURE)) if (p.numerator, p.denominator) in _UNDER_FWHM)),
    ("Peak Centers", (
        PeakPanel("E2g+A1g", "center", "Raman shift (cm⁻¹)", "E₂g + A₁g center"),
        # No spec line since v5.32.0: the customer spec's "~308" is a presence
        # criterion, stated in the title, not a limit to plot against.
        PeakPanel("B2g", "center", "Raman shift (cm⁻¹)", "B₂g center"),
    )),
    ("Diagnostic Ratios", tuple(p for p in _raman_ratio_panels(tuple(p for p in RAMAN_RATIO_PAIRS if p not in _NOT_IN_WSE2_FIGURE))
                                if (p.numerator, p.denominator) not in _UNDER_FWHM)),
)

#: PL's panels. Two peaks, so the ratio column holds one panel where the others
#: hold two and the bottom-right cell stays empty. That gap is honest: the
#: lineage defines exactly one PL ratio, and filling the slot would mean either
#: inventing a second or moving an amplitude panel into a column titled
#: "Diagnostic Ratios". Amplitude is uncalibrated peak height, sensitive to
#: laser power and focus, and carries no reference line — the least diagnostic
#: of the three families the ancestor plots.
#:
#: The column titles are Raman's, not the ancestor's: the two figures are read
#: side by side in one report, and matching headers are worth more than
#: fidelity to strings the PL script never needed to write down.
#: The Raw column (v5.29.0) is the spectrum's own maximum and half-height
#: width, no fit involved -- the Raw row the PL summary table leads with -- as
#: the MoS2 reports drew it. It replaces the Exciton/Trion ratio column, so the
#: figure is 2 | 2 | 2. Raw values come in through `build_peak_quality_figure`'s
#: `raw_by_point`; without them the column is left out.
RAW_LABEL = "Raw"
RAW_COLUMN = ("Raw Spectrum", (
    PeakPanel(RAW_LABEL, "fwhm", "FWHM (nm)", "Raw FWHM"),
    PeakPanel(RAW_LABEL, "center", "Wavelength (nm)", "Raw center"),
))
PL_PANEL_COLUMNS: PanelColumns = (
    # The customer PL spec is judged on the Raw spectrum (v5.32.0), so its 40 nm
    # line sits on Raw FWHM and nowhere else. RAW_COLUMN itself carries no line:
    # MoS2's figure prepends it, and MoS2 has no customer spec.
    (RAW_COLUMN[0], (
        PeakPanel(RAW_LABEL, "fwhm", "FWHM (nm)", "Raw FWHM", spec=40.0),
        RAW_COLUMN[1][1],
    )),
    ("FWHM", (
        PeakPanel("Exciton", "fwhm", "FWHM (nm)", "Exciton"),
        PeakPanel("Trion", "fwhm", "FWHM (nm)", "Trion"),
    )),
    ("Peak Centers", (
        PeakPanel("Exciton", "center", "Wavelength (nm)", "Exciton center"),
        PeakPanel("Trion", "center", "Wavelength (nm)", "Trion center"),
    )),
)


#: Axis labels per technique, for panels built from a material's own peaks
#: rather than from the inherited columns above.
_AXIS_LABELS = {
    "Raman": ("FWHM (cm⁻¹)", "Raman shift (cm⁻¹)"),
    "PL": ("FWHM (nm)", "Wavelength (nm)"),
}


def panels_for_labels(labels: Sequence[str], technique: str) -> PanelColumns:
    """FWHM and centre columns for `labels`, in the order given.

    The inherited `PANEL_COLUMNS` name WSe₂'s peaks (`E2g+A1g`, `2LA`, `B2g`)
    and PL's name the WSe₂ lineage's two. A material whose peaks are called
    something else — MoS₂ fits `E2g` and `A1g` as separate peaks — matches none
    of them, and every panel rendered "no E2g+A1g fits" however good the fits
    were. This builds the columns from the peaks actually present instead.

    **No spec lines, deliberately.** The four in this lineage are inherited
    literals tied to WSe₂'s peaks (see the module docstring); there is no
    measured tolerance for an arbitrary material's peaks, and a grey line the
    reader takes for a spec is worse than no line. Panels built here carry
    `spec=None`.

    **No ratio column either.** `RAMAN_RATIO_PAIRS` is the lineage's two
    diagnostic ratios; which ratios mean something for another material is a
    materials question, not a plotting one.
    """
    fwhm_label, center_label = _AXIS_LABELS.get(technique, _AXIS_LABELS["Raman"])
    return (
        ("FWHM", tuple(
            PeakPanel(label, "fwhm", fwhm_label, label) for label in labels
        )),
        ("Peak Centers", tuple(
            PeakPanel(label, "center", center_label, f"{label} center")
            for label in labels
        )),
    )


@dataclass(frozen=True)
class QualityFigureSpec:
    """Everything that makes this figure one technique's rather than another's.

    Bundled rather than passed as four arguments so a call site cannot pair
    Raman's columns with PL's cleaning — a mismatch that would render without
    error and be wrong only in its numbers.
    """

    technique: str
    columns: PanelColumns
    cleaning: CleaningRule
    statistic: str  # "mean" | "median" — the per-position marker

    def columns_for(self, labels: Sequence[str]) -> PanelColumns:
        """`columns`, or peak-derived ones when they name none of `labels`.

        The test is "does any panel address a peak this sample actually
        fitted", not "is this WSe₂": a material that happens to share the
        inherited peak names keeps the inherited columns, spec lines and all.
        Only a sample that would otherwise render entirely empty falls back.
        """
        # dict, not set: peak order is the order the fitter reports, which is
        # the preset's own — E2g before A1g, ascending in wavenumber. Sorting
        # would put A1g first and read as wrong to anyone who knows the
        # spectrum.
        present = dict.fromkeys(labels)
        for _, panels in self.columns:
            for panel in panels:
                named = (
                    (panel.numerator, panel.denominator)
                    if isinstance(panel, RatioPanel)
                    else (panel.peak,)
                )
                if any(name in present for name in named):
                    return self.columns
        if not present:
            return self.columns
        return panels_for_labels(tuple(present), self.technique)


def _drop_unfitted_ratios(columns: PanelColumns, present) -> PanelColumns:
    """`columns` without the ratio panels whose peaks no fit carries (v5.29.0).

    A preset that does not fit LB or C (WSe2_QC since v5.29.0) would otherwise
    draw an empty "C / LB" panel; the summary table already omits that ratio
    (`compute_peak_intensity_ratio` returns None), so the two keep agreeing.
    Peak panels are left alone: an empty peak panel is a missing fit worth
    seeing. A column left with no panels is dropped.
    """
    out = []
    for title, panels in columns:
        kept = tuple(p for p in panels
                     if not isinstance(p, RatioPanel) or {p.numerator, p.denominator} <= present)
        if kept:
            out.append((title, kept))
    return tuple(out)


#: The customer QC spec the figure title summarizes against (v5.31.0): per
#: technique, the main peak whose uniformity is stated, the layer criteria and
#: the across-wafer limits (CV of region medians, %). Limits are stated, not
#: evaluated -- see the module docstring.
CUSTOMER_SPEC = {
    "Raman": {
        "main": "E2g+A1g",
        "position_cv_max": 0.5,
        "fwhm_cv_max": 20.0,
        "near": {"E2g+A1g": 250.0, "B2g": 308.0},
        "fwhm_max": 7.0,
        "la_ratio_max": 0.13,
    },
    # PL is judged on the Raw spectrum (each spectrum's own maximum and
    # half-height width, no fit), v5.32.0; Exciton marks the material as WSe2.
    "PL": {
        "main": RAW_LABEL,
        "material_peak": "Exciton",
        "position_cv_max": 1.5,
        "fwhm_cv_max": 25.0,
        "fwhm_max": 40.0,
    },
}

#: Peaks a monolayer does not have: B2g is the bilayer mode (v5.32.0).
BILAYER_ONLY_PEAKS = frozenset({"B2g"})


def _drop_bilayer_panels(columns: PanelColumns) -> PanelColumns:
    """`columns` without B2g's panels -- its centre and the bilayer ratio -- for a 1L report (v5.32.0).

    On a monolayer B2g is absent; its centre panel plots where the fit parked a
    component on noise. A column left with no panels is dropped.
    """
    out = []
    for title, panels in columns:
        kept = tuple(p for p in panels if not (
            {p.numerator, p.denominator} & BILAYER_ONLY_PEAKS if isinstance(p, RatioPanel)
            else p.peak in BILAYER_ONLY_PEAKS))
        if kept:
            out.append((title, kept))
    return tuple(out)


#: Which spec lines each layer's criteria draw (v5.32.0), as the quote
#: (20261009W001 §6.5-6.9) lists them: FWHM < 7 and LA < 13 % are 2L WSe2
#: criteria (1L WSe2 Raman has only "E2g & A1g present ~250"), Raw FWHM <=
#: 40 nm is 1L PL. A line is drawn only on a report of that layer -- as the
#: title states only that layer's criteria. Keyed by (technique, layer);
#: values are panel captions.
LAYER_SPEC_LINES = {
    ("Raman", "2L"): frozenset({"E₂g + A₁g", "Defect ratio"}),
    ("PL", "1L"): frozenset({"Raw FWHM"}),
}


def _layer_spec_lines(columns: PanelColumns, technique: str, layer: str) -> PanelColumns:
    """`columns` with every spec line removed that the layer's criteria do not include."""
    keep = LAYER_SPEC_LINES.get((technique, layer), frozenset())
    return tuple(
        (title, tuple(p if p.spec is None or p.caption in keep else dataclasses.replace(p, spec=None)
                      for p in panels))
        for title, panels in columns
    )


RAMAN_QUALITY = QualityFigureSpec("Raman", PANEL_COLUMNS, RAMAN_CLEANING, "mean")
PL_QUALITY = QualityFigureSpec("PL", PL_PANEL_COLUMNS, PL_CLEANING, "median")


class _PeakRecord(NamedTuple):
    """One fitted peak at one grid position.

    ``spectrum`` identifies the fit it came from, which is what lets a ratio be
    formed within a single spectrum after cleaning has dropped peaks from
    others.
    """

    point: int
    spectrum: int
    label: str
    center: float
    fwhm: float
    intensity: float
    measured: bool = True    # peak_metrics.peak_is_measured; ratios use only measured peaks


def _records(fits_by_point: Sequence[Tuple[int, FitResult]]) -> List[_PeakRecord]:
    """Flatten `(position, FitResult)` pairs to one record per fitted peak."""
    out: List[_PeakRecord] = []
    for index, (point, fit) in enumerate(fits_by_point):
        for peak in fit.fitted_peaks:
            out.append(_PeakRecord(
                point=point,
                spectrum=index,
                label=peak.label,
                center=peak.center,
                fwhm=peak.width_fwhm,
                intensity=peak_intensity(peak),
                measured=peak_is_measured(peak, fit),
            ))
    return out


def _clean(records: Sequence[_PeakRecord], rule: CleaningRule) -> List[_PeakRecord]:
    """Apply a rule's record-level filters, in the inherited order.

    Floors first, then the top-fraction cut — which matters, because the cut's
    quantile is taken over what the floors left, not over the raw set.
    """
    kept = list(records)

    if rule.fwhm_floor is not None:
        kept = [r for r in kept if r.fwhm is not None and r.fwhm > rule.fwhm_floor]
    if rule.center_floor is not None:
        kept = [r for r in kept if r.center > rule.center_floor]
    if rule.relative_intensity_floor is not None:
        # Measured against the full record set, so a strongest peak that
        # another floor dropped still sets its spectrum's reference.
        strongest: Dict[int, float] = {}
        for record in records:
            strongest[record.spectrum] = max(
                strongest.get(record.spectrum, record.intensity), record.intensity
            )
        kept = [
            r for r in kept
            if r.intensity >= rule.relative_intensity_floor * strongest[r.spectrum]
        ]

    if rule.fwhm_top_fraction:
        # Per label, not pooled: peaks of different widths would otherwise have
        # the broad one's whole distribution judged against the narrow one's.
        by_label: Dict[str, List[float]] = {}
        for record in kept:
            by_label.setdefault(record.label, []).append(record.fwhm)
        cutoffs = {
            label: float(np.quantile(widths, 1.0 - rule.fwhm_top_fraction))
            for label, widths in by_label.items() if widths
        }
        kept = [r for r in kept if r.fwhm <= cutoffs.get(r.label, np.inf)]

    return kept


def _peak_values(records: Sequence[_PeakRecord], panel: PeakPanel) -> Dict[int, np.ndarray]:
    out: Dict[int, list] = {}
    for record in records:
        if record.label != panel.peak:
            continue
        if panel.metric == "center":
            value = record.center
        elif panel.metric == "fwhm":
            value = record.fwhm
        else:
            value = record.intensity
        out.setdefault(record.point, []).append(value)
    return {p: np.asarray(v, float) for p, v in out.items()}


def _ratio_values(records: Sequence[_PeakRecord], panel: RatioPanel) -> Dict[int, np.ndarray]:
    """Ratios formed within one spectrum, then collected by position --
    selected by `peak_metrics.ratio_values_by_point`, the same function the
    summary table's ratios come from."""
    spectra: Dict[int, Tuple[int, Dict[str, Tuple[float, bool]]]] = {}
    for record in records:
        _point, peaks = spectra.setdefault(record.spectrum, (record.point, {}))
        peaks[record.label] = (record.intensity, record.measured)
    return ratio_values_by_point(spectra.values(), panel.numerator, panel.denominator)


def robust_cv(values) -> float:
    """1.4826 * MAD / |median| * 100, in percent; NaN for no values or a zero median."""
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return float("nan")
    median = float(np.median(values))
    if median == 0:
        return float("nan")
    return 1.4826 * float(np.median(np.abs(values - median))) / abs(median) * 100.0


def region_median_cv(by_point: Dict[int, np.ndarray], per_position_iqr: bool) -> float:
    """CV (sample std / |mean|, %) of the per-region medians; NaN under two regions."""
    medians = np.array([np.median(v) for v in _region_values(by_point, per_position_iqr).values()])
    if medians.size < 2 or medians.mean() == 0:
        return float("nan")
    return float(medians.std(ddof=1) / abs(medians.mean()) * 100.0)


def _pooled_median(by_point: Dict[int, np.ndarray], per_position_iqr: bool) -> float:
    pooled = [x for v in _region_values(by_point, per_position_iqr).values() for x in v]
    return float(np.median(pooled)) if pooled else float("nan")


def spec_summary_lines(records: Sequence[_PeakRecord], technique: str, layer: Optional[str],
                       per_position_iqr: bool) -> List[str]:
    """The customer-spec lines under a WSe2 or MoS2 figure's suptitle; empty for another material.

    `records` are the cleaned records the panels draw, so every value here is
    one a panel shows. Each criterion is its value and its limit, nothing else.
    """
    labels = {r.label for r in records}
    if is_mos2(labels, technique):
        return _mos2_summary_lines(records, technique, layer, per_position_iqr)
    spec = CUSTOMER_SPEC.get(technique)
    main = spec["main"] if spec else None
    if not spec or main not in labels or spec.get("material_peak", main) not in labels:
        return []

    def values(label, metric):
        return _peak_values(records, PeakPanel(label, metric, "", ""))

    def median(label, metric):
        return _pooled_median(values(label, metric), per_position_iqr)

    unit = "cm⁻¹" if technique == "Raman" else "nm"
    criteria = []
    if technique == "Raman" and layer == "1L":
        # Quote §6.5: the only 1L WSe2 Raman criterion.
        criteria.append(f"E₂g+A₁g {median('E2g+A1g', 'center'):.2f} {unit} (spec ~{spec['near']['E2g+A1g']:g})")
    elif technique == "Raman" and layer == "2L":
        b2g = median("B2g", "center")
        if np.isfinite(b2g):
            criteria.append(f"B₂g {b2g:.2f} {unit} (spec ~{spec['near']['B2g']:g})")
        criteria.append(f"E₂g+A₁g FWHM {median('E2g+A1g', 'fwhm'):.2f} {unit} (spec < {spec['fwhm_max']:g})")
        la_panel = RatioPanel("LA", "E2g+A1g", "", "")
        ratio = _pooled_median(_ratio_values(records, la_panel), per_position_iqr)
        if np.isfinite(ratio):
            criteria.append(f"I(LA)/I(E₂g+A₁g) {ratio * 100:.1f} % (spec < {spec['la_ratio_max'] * 100:g} %)")
    elif technique == "PL" and layer == "1L":
        criteria.append(f"Raw FWHM {median(RAW_LABEL, 'fwhm'):.2f} {unit} (spec ≤ {spec['fwhm_max']:g})")

    lines = [f"{layer} spec:  " + "   ·   ".join(criteria)] if criteria else []
    centres = values(main, "center")
    lines.append(
        f"Uniformity:  {'E₂g+A₁g' if main == 'E2g+A1g' else main} position "
        f"{_pct(region_median_cv(centres, per_position_iqr))} (spec ≤ {spec['position_cv_max']:g} %)   ·   "
        f"FWHM {_pct(region_median_cv(values(main, 'fwhm'), per_position_iqr))} "
        f"(spec ≤ {spec['fwhm_cv_max']:g} %)"
    )
    return lines


#: The MoS2 customer spec (quote 20261009W001 §6.4, 6.7, 6.9, 6.10; v5.32.0):
#: 1L Raman |E2g - A1g| 20 cm-1, 1L PL FWHM 40 nm (judged on Raw, as WSe2's),
#: and the same uniformity limits as WSe2. The quote writes the first two as
#: "<="; the report states them as nominal values ("~20", "~40", grey dashed)
#: at Nexstrom's choice (2026-10-22). Uniformity stays a limit.
MOS2_SPEC = {
    "Raman": {"delta_max": 20.0, "position_cv_max": 0.5, "fwhm_cv_max": 20.0},
    "PL": {"fwhm_max": 40.0, "position_cv_max": 1.5, "fwhm_cv_max": 25.0},
}
#: The Δω panel's label, as the MoS2 Raman figure names it.
DELTA_LABEL = "Δω (A1g − E2g)"


#: MoS2's Raman figure: FWHM (E2g, A1g) | Center (E2g, A1g) | Δω, panels
#: 2, 2, 1 -- the layout `run_qc_mos2.py` drew by monkeypatch from 2026-10-19
#: (no 2LA(M) FWHM panel, no defect-ratio panel; the ratio is in the summary
#: table and workbook). In the app since v5.34.0, so a report built on the page
#: and one built by the runner are the same figure.
MOS2_RAMAN_COLUMNS: PanelColumns = (
    ("FWHM", tuple(PeakPanel(lab, "fwhm", "FWHM (cm⁻¹)", lab) for lab in ("E2g", "A1g"))),
    ("Center", tuple(PeakPanel(lab, "center", "Raman shift (cm⁻¹)", f"{lab} center")
                     for lab in ("E2g", "A1g"))),
    ("Δω", (PeakPanel(DELTA_LABEL, "center", "Δω (cm⁻¹)", DELTA_LABEL),)),
)


def delta_omega_summary(fits_by_point: Sequence[Tuple[int, FitResult]],
                        spec: "QualityFigureSpec" = None) -> Optional[Tuple[float, float, int]]:
    """Δω as the summary page's row states it: (median, MAD, n) over exactly the
    values the figure's Δω panel draws -- cleaned records, IQR-cut within each
    position -- so the row and the title's |E2g-A1g| are one number. None when
    no spectrum has both peaks."""
    spec = spec or RAMAN_QUALITY
    records = _clean(_records(fits_by_point), spec.cleaning)
    by_point = _region_values(delta_omega_by_point(records), spec.cleaning.per_position_iqr)
    if not by_point:
        return None
    values = np.concatenate(list(by_point.values()))
    median = float(np.median(values))
    return median, float(np.median(np.abs(values - median))), int(values.size)


def is_mos2(labels, technique: str) -> bool:
    """MoS2 by its own peaks: E2g and A1g fitted apart (Raman), A0 (PL).

    "WSe2 extended V2" also fits E2g and A1g apart, so a Raman fit carrying
    WSe2's own modes (LA, 2LA, B2g) is not MoS2 -- it must not get MoS2's
    layout and Δω row (v5.34.0)."""
    if technique == "Raman":
        labels = set(labels)
        return {"E2g", "A1g"} <= labels and not labels & {"LA", "2LA", "B2g"}
    return "A0" in labels


def delta_omega_by_point(records: Sequence[_PeakRecord]) -> Dict[int, np.ndarray]:
    """A1g centre - E2g centre per spectrum, collected by position."""
    by_spectrum: Dict[int, Tuple[int, Dict[str, float]]] = {}
    for r in records:
        by_spectrum.setdefault(r.spectrum, (r.point, {}))[1][r.label] = r.center
    out: Dict[int, list] = {}
    for point, centres in by_spectrum.values():
        if "A1g" in centres and "E2g" in centres:
            out.setdefault(point, []).append(centres["A1g"] - centres["E2g"])
    return {p: np.asarray(v, float) for p, v in out.items()}


def _mos2_summary_lines(records, technique, layer, per_position_iqr) -> List[str]:
    spec = MOS2_SPEC[technique]

    def values(label, metric):
        return _peak_values(records, PeakPanel(label, metric, "", ""))

    def cv(label, metric):
        return _pct(region_median_cv(values(label, metric), per_position_iqr))

    lines = []
    if technique == "Raman":
        delta = _pooled_median(delta_omega_by_point(records), per_position_iqr)
        if layer == "1L" and np.isfinite(delta):
            lines.append(f"1L spec:  |E₂g−A₁g| {delta:.2f} cm⁻¹ (spec ~{spec['delta_max']:g})")
        lines.append(
            f"Uniformity:  position E₂g {cv('E2g', 'center')} · A₁g {cv('A1g', 'center')} "
            f"(spec ≤ {spec['position_cv_max']:g} %)   ·   FWHM E₂g {cv('E2g', 'fwhm')} · A₁g {cv('A1g', 'fwhm')} "
            f"(spec ≤ {spec['fwhm_cv_max']:g} %)")
    elif any(r.label == RAW_LABEL for r in records):
        if layer == "1L":
            raw_fwhm = _pooled_median(values(RAW_LABEL, "fwhm"), per_position_iqr)
            lines.append(f"1L spec:  Raw FWHM {raw_fwhm:.2f} nm (spec ~{spec['fwhm_max']:g})")
        lines.append(
            f"Uniformity:  Raw position {cv(RAW_LABEL, 'center')} (spec ≤ {spec['position_cv_max']:g} %)   ·   "
            f"FWHM {cv(RAW_LABEL, 'fwhm')} (spec ≤ {spec['fwhm_cv_max']:g} %)")
    return lines


#: The MoS2 PL peaks the figure shows (v5.32.0): the A exciton and A- trion.
#: B (~632 nm) is still fitted -- the fit needs it -- but not drawn.
MOS2_PL_SHOWN = frozenset({"A0", "A-"})


def _mos2_pl_panels(columns: PanelColumns) -> PanelColumns:
    """MoS2 PL columns with only the Raw, A0 and A- panels; a column left empty is dropped."""
    out = []
    for title, panels in columns:
        kept = tuple(p for p in panels if isinstance(p, RatioPanel) or p.peak in MOS2_PL_SHOWN | {RAW_LABEL})
        if kept:
            out.append((title, kept))
    return tuple(out)


def _mos2_spec_lines(columns: PanelColumns, technique: str, layer: Optional[str]) -> PanelColumns:
    """MoS2 1L: the nominal ~20 cm-1 line on Raman's Δω panel, and the 40 nm
    line on PL's Raw FWHM (RAW_COLUMN itself carries none). Grey dashed: the
    quote writes both as "<=", the report states them as targets."""
    if layer != "1L":
        return columns
    if technique == "Raman":
        return tuple(
            (title, tuple(dataclasses.replace(p, spec=MOS2_SPEC["Raman"]["delta_max"], spec_nominal=True)
                          if isinstance(p, PeakPanel) and p.peak == DELTA_LABEL else p
                          for p in panels))
            for title, panels in columns
        )
    if technique != "PL":
        return columns
    return tuple(
        (title, tuple(dataclasses.replace(p, spec=MOS2_SPEC["PL"]["fwhm_max"], spec_nominal=True)
                      if isinstance(p, PeakPanel) and p.peak == RAW_LABEL and p.metric == "fwhm" else p
                      for p in panels))
        for title, panels in columns
    )


def _pct(value: float) -> str:
    """A percentage as the title and the legends both print it.

    A CV needs two regions; with fewer it is NaN, which prints as a dash
    rather than "nan %"."""
    if not np.isfinite(value):
        return "—"
    return f"{value:.2f} %" if value < 10 else f"{value:.1f} %"


def _draw_panel(ax, by_point: Dict[int, np.ndarray], positions: Sequence[int],
                panel: Panel, is_bottom: bool, rng: np.random.Generator,
                statistic: str, per_position_iqr: bool) -> None:
    # Ratios are drawn in percent (v5.32.0), as the customer spec states them.
    is_ratio = isinstance(panel, RatioPanel)
    scale = 100.0 if is_ratio else 1.0
    pooled, region_medians = [], []
    for index, position in enumerate(positions):
        values = by_point.get(position)
        if values is None or values.size == 0:
            continue
        clean = (values[iqr_mask(values)] if per_position_iqr else values) * scale
        if clean.size == 0:
            continue
        region_medians.append(float(np.median(clean)))
        colour = POSITION_COLORS[index % len(POSITION_COLORS)]
        jitter = position + rng.uniform(-_JITTER, _JITTER, size=clean.size)
        ax.scatter(jitter, clean, color=colour, s=18, alpha=0.5,
                   edgecolor="none", zorder=3)
        centre = float(np.median(clean)) if statistic == "median" else float(clean.mean())
        std = clean.std(ddof=1) if clean.size > 1 else 0.0
        ax.errorbar(position + _MARKER_OFFSET, centre, yerr=std,
                    fmt="s", color=colour, ecolor=colour, elinewidth=2,
                    capsize=4, capthick=2, markersize=8, markerfacecolor=colour,
                    markeredgecolor=colour, markeredgewidth=1.2, zorder=5)
        pooled.extend(clean.tolist())

    if pooled:
        median = float(np.median(pooled))
        ax.axhline(median, color="black", linestyle="--", linewidth=1.3,
                   alpha=0.8, zorder=2,
                   label=f"Median = {median:.1f} %" if is_ratio else f"Median = {median:.2f}")
        # Uniformity under the median (v5.32.0, was the pooled Robust CV): the
        # CV of the region medians, the same number the title's "Uniformity"
        # states (`region_median_cv`), so title and panel cannot disagree.
        if len(region_medians) >= 2 and np.mean(region_medians) != 0:
            cv = float(np.std(region_medians, ddof=1) / abs(np.mean(region_medians)) * 100.0)
            ax.plot([], [], linestyle="none", label=f"Uniformity = {_pct(cv)}")
        ax.legend(loc="upper right", fontsize=_FS_REFTEXT, framealpha=0.85)
    else:
        ax.text(0.5, 0.5, f"no {getattr(panel, 'peak', 'data')} fits",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=_FS_LABEL, color="#999999")

    if panel.spec is not None:
        spec = panel.spec * scale
        # A limit is red dashed (v5.32.0, was solid grey), so it reads apart
        # from the black dashed median; a nominal value ("~x", MoS2) grey dashed.
        colour = "grey" if panel.spec_nominal else "red"
        ax.axhline(spec, color=colour, linestyle="--", linewidth=1.3, alpha=0.8, zorder=1)
        # Value at the right edge in axes-fraction x, data y, so it tracks the
        # line without a second legend entry competing with the median's.
        ax.text(1.005, spec, f"{spec:g}", transform=ax.get_yaxis_transform(),
                va="center", ha="left", fontsize=_FS_REFTEXT, color=colour)

    ax.text(0.02, 0.95, panel.caption, transform=ax.transAxes, va="top", ha="left",
            fontsize=_FS_PANEL, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85, lw=0))

    ax.set_ylabel(f"{panel.ylabel} (%)" if is_ratio else panel.ylabel, fontsize=_FS_LABEL)
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    ax.set_xticks(list(positions))
    ax.tick_params(axis="both", labelsize=_FS_TICK)
    ax.set_xlim(min(positions) - 0.7, max(positions) + 0.8)
    if is_bottom:
        ax.set_xlabel("Position", fontsize=_FS_LABEL)
    else:
        ax.tick_params(labelbottom=False)


def build_peak_quality_figure(
    fits_by_point: Sequence[Tuple[int, FitResult]],
    sample_name: str,
    material_name: str = "WSe₂",
    spec: QualityFigureSpec = RAMAN_QUALITY,
    dpi: int = REPORT_DPI,
    raw_by_point: Optional[Sequence[Tuple[int, float, Optional[float]]]] = None,
    layer: Optional[str] = None,
) -> bytes:
    """
    Render one technique's quality figure and return it as PNG bytes.

    `fits_by_point` is `(position, FitResult)` pairs, already passed through
    `peak_metrics.filter_fits_by_quality`; `spec.cleaning` is applied on top of
    that gate, and differs by technique — see the module docstring.

    `spec` carries the columns, the cleaning rule and the per-position marker
    statistic as one value: pass `RAMAN_QUALITY` or `PL_QUALITY`.

    The grid is computed from `spec.columns_for(...)` — the spec's own columns,
    or ones built from this sample's peak labels when the spec's name none of
    them — so a column with fewer panels than its neighbours simply leaves its
    bottom cell empty rather than having its last panel silently dropped.

    `raw_by_point` is `(position, centre, FWHM)` per spectrum from
    `peak_metrics.raw_peak_stats` (PL). It fills the Raw column, cleaned apart
    from the fitted peaks: in one pool the raw maximum -- taller than any single
    component -- would become each spectrum's strongest peak and the relative
    floor would drop weak fitted peaks the figure otherwise keeps. Without it the
    Raw column is left out; a material whose own peaks build the columns gets it
    prepended.

    `layer` is the report's reference layer ("1L", "2L"); it picks which of the
    customer spec's layer criteria the title states (`spec_summary_lines`).
    Uniformity is stated for any layer.

    Returns bytes rather than a Figure so a caller cannot leak the canvas.
    """
    if not fits_by_point:
        raise ValueError("build_peak_quality_figure needs at least one fit")
    if not spec.columns:
        raise ValueError("build_peak_quality_figure needs at least one panel column")

    records = _clean(_records(fits_by_point), spec.cleaning)
    raw = [_PeakRecord(point=point, spectrum=-1 - i, label=RAW_LABEL, center=float(center),
                       fwhm=float(fwhm), intensity=1.0)
           for i, (point, center, fwhm) in enumerate(raw_by_point or ()) if fwhm is not None]
    records += _clean(raw, spec.cleaning)

    # Resolved from the fits, not from `spec`: a material whose peaks the
    # inherited columns never name gets columns built from its own. Taken
    # before cleaning, so the panel set is a property of the material rather
    # than of how well this particular sample fitted — a sample cleaned down to
    # nothing keeps its panels and shows them empty, which is the honest
    # result.
    labels = [peak.label for _, fit in fits_by_point for peak in fit.fitted_peaks]
    columns = _drop_unfitted_ratios(spec.columns_for(labels), set(labels))
    if layer == "1L":
        columns = _drop_bilayer_panels(columns)
    if layer is not None:
        columns = _layer_spec_lines(columns, spec.technique, layer)
    has_raw_column = any(title == RAW_COLUMN[0] for title, _ in columns)
    if raw and not has_raw_column and spec.technique == "PL":
        columns = (RAW_COLUMN,) + tuple(columns)
    elif not raw and has_raw_column:
        columns = tuple(c for c in columns if c[0] != RAW_COLUMN[0])
    if is_mos2(labels, spec.technique):
        if spec.technique == "Raman":
            columns = MOS2_RAMAN_COLUMNS
        columns = _mos2_spec_lines(columns, spec.technique, layer)
        if spec.technique == "PL":
            columns = _mos2_pl_panels(columns)

    # Positions come from the fits, not from the surviving records: a position
    # whose every fit was cleaned away keeps its tick and shows as a gap, which
    # is the thing worth seeing. Dropping it would close the gap and make nine
    # positions look like eight.
    positions = sorted({point for point, _ in fits_by_point})
    spectra = len(fits_by_point)

    n_cols = len(columns)
    n_rows = max(len(panels) for _, panels in columns)

    fig = plt.figure(figsize=(_COLUMN_W_IN * n_cols, _ROW_H_IN * n_rows), dpi=dpi)
    try:
        # Lower top edge when the customer-spec lines sit under the suptitle,
        # so they do not crowd the column titles.
        summary = spec_summary_lines(records, spec.technique, layer, spec.cleaning.per_position_iqr)
        grid = gridspec.GridSpec(n_rows, n_cols, figure=fig, hspace=0.0, wspace=0.30,
                                 top=0.85 if summary else None)

        for column, (title, panels) in enumerate(columns):
            top = None
            for row, panel in enumerate(panels):
                ax = fig.add_subplot(grid[row, column], sharex=top)
                if top is None:
                    top = ax
                    ax.set_title(title, fontsize=_FS_TITLE, fontweight="bold", pad=10)
                if isinstance(panel, RatioPanel):
                    values = _ratio_values(records, panel)
                elif panel.peak == DELTA_LABEL:
                    values = delta_omega_by_point(records)
                else:
                    values = _peak_values(records, panel)
                # Seeded per panel rather than once for the figure: the
                # inherited script draws jitter from one module-level generator
                # consumed in panel order, so inserting or reordering a panel
                # silently moves every later panel's cloud. The stride is the
                # row count, so seeds stay distinct at any grid shape.
                _draw_panel(
                    ax, values, positions, panel,
                    is_bottom=(row == len(panels) - 1),
                    rng=np.random.default_rng(42 + column * n_rows + row),
                    statistic=spec.statistic,
                    per_position_iqr=spec.cleaning.per_position_iqr,
                )

        per_position = spectra / len(positions) if positions else 0
        marker = "median ± std" if spec.statistic == "median" else "mean ± std"
        # PL's extra cleaning (FWHM floor, widest 5 %) is no longer spelled out
        # in the title (v5.32.0); PL_CLEANING documents it.
        fig.suptitle(
            f"{sample_name}  {material_name} — {spec.technique} quality analysis "
            f"({len(positions)} positions × {per_position:.0f} spectra, "
            f"{marker} per position)",
            fontsize=_FS_SUPTITLE, fontweight="bold", y=1.0,
        )
        if summary:
            # Between the suptitle and the column titles; bbox_inches="tight"
            # grows the canvas to take it.
            fig.text(0.5, 0.962, "\n".join(summary), ha="center", va="top",
                     fontsize=_FS_TITLE, color="#222222", linespacing=1.5)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
        return buf.getvalue()
    finally:
        plt.close(fig)
