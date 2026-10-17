"""Unit tests for modules.spectra.viz.fit_plot's shared-scale helpers.

These exist for the Sample Report's 3x3 grids: nine spectra shown together
need one scale and one legend, which means deriving both from the whole set
rather than from each figure.
"""

import numpy as np
import pytest

from modules.spectra.viz.fit_plot import (
    FIT_TOTAL_COLOR,
    PROCESSED_COLOR,
    fit_legend_entries,
    peak_normalization_scale,
    plot_composite,
    plot_fit_column,
    shared_axis_ranges,
)


class _Peak:
    def __init__(self, label, color="#123456", curve=None):
        self.label = label
        self.color = color
        self.component_curve = curve


class _Fit:
    def __init__(self, peaks=(), total=None):
        self.fitted_peaks = list(peaks)
        self.total_fit_curve = total
        self.residuals = None


class TestSharedAxisRanges:
    def test_empty_series_yields_no_ranges(self):
        assert shared_axis_ranges([]) == (None, None)

    def test_spans_every_series(self):
        series = [
            (np.array([10.0, 20.0]), np.array([0.0, 5.0]), None),
            (np.array([5.0, 30.0]), np.array([-2.0, 8.0]), None),
        ]
        x_range, y_range = shared_axis_ranges(series, pad=0.0)

        assert x_range == (5.0, 30.0)
        assert y_range == (-2.0, 8.0)

    def test_fit_curve_can_exceed_the_data(self):
        """The fit's peak can overshoot the samples; the frame must still hold it."""
        series = [(np.array([0.0, 1.0]), np.array([0.0, 1.0]), np.array([0.0, 50.0]))]
        _x, y_range = shared_axis_ranges(series, pad=0.0)

        assert y_range == (0.0, 50.0)

    def test_pads_y_but_not_x(self):
        series = [(np.array([0.0, 100.0]), np.array([0.0, 200.0]), None)]
        x_range, y_range = shared_axis_ranges(series, pad=0.10)

        assert x_range == (0.0, 100.0)  # the crop range is meaningful as-is
        assert y_range == pytest.approx((-20.0, 220.0))

    def test_flat_spectrum_still_gets_a_visible_range(self):
        """A zero-height range renders as a single line across the middle."""
        series = [(np.array([0.0, 1.0]), np.array([7.0, 7.0]), None)]
        _x, y_range = shared_axis_ranges(series)

        assert y_range[0] < 7.0 < y_range[1]

    def test_all_zero_spectrum_does_not_collapse(self):
        series = [(np.array([0.0, 1.0]), np.array([0.0, 0.0]), None)]
        _x, y_range = shared_axis_ranges(series)

        assert y_range[1] > y_range[0]

    def test_empty_x_is_skipped_not_fatal(self):
        series = [
            (np.array([]), np.array([]), None),
            (np.array([1.0, 2.0]), np.array([3.0, 4.0]), None),
        ]
        x_range, _y = shared_axis_ranges(series, pad=0.0)

        assert x_range == (1.0, 2.0)


class TestFitLegendEntries:
    def test_data_and_total_fit_come_first(self):
        entries = fit_legend_entries([])

        assert entries == [("Data", PROCESSED_COLOR), ("Total Fit", FIT_TOTAL_COLOR)]

    def test_peaks_follow_in_first_seen_order_with_their_colors(self):
        fit = _Fit([_Peak("E2g", "#EF482E"), _Peak("2LA", "#D5EF2E")])

        assert fit_legend_entries([fit])[2:] == [("E2g", "#EF482E"), ("2LA", "#D5EF2E")]

    def test_takes_the_union_across_points(self):
        """One point failing to resolve a peak shouldn't drop it from the key."""
        entries = fit_legend_entries([_Fit([_Peak("A")]), _Fit([_Peak("A"), _Peak("B")])])

        assert [label for label, _c in entries[2:]] == ["A", "B"]

    def test_repeated_peaks_appear_once(self):
        entries = fit_legend_entries([_Fit([_Peak("A")])] * 9)

        assert [label for label, _c in entries[2:]] == ["A"]

    def test_none_and_unfitted_results_are_skipped(self):
        entries = fit_legend_entries([None, _Fit([]), _Fit([_Peak("A")])])

        assert [label for label, _c in entries[2:]] == ["A"]

    def test_unlabeled_peak_gets_a_positional_name(self):
        entries = fit_legend_entries([_Fit([_Peak("")])])

        assert entries[2][0] == "Peak 1"


class TestPlotCompositeGridOptions:
    """The options the Sample Report grid relies on."""

    def _fig(self, **kwargs):
        x = np.linspace(0.0, 10.0, 20)
        return plot_composite(
            x=x, y_data=np.sin(x), fit_result=None, mode="Raman", show_residuals=False, **kwargs
        )

    def test_legend_can_be_suppressed(self):
        assert self._fig(show_legend=False).layout.showlegend is False
        assert self._fig().layout.showlegend is True

    def test_explicit_ranges_are_applied(self):
        fig = self._fig(x_range=(0.0, 400.0), y_range=(-10.0, 800.0))

        assert tuple(fig.layout.xaxis.range) == (0.0, 400.0)
        assert tuple(fig.layout.yaxis.range) == (-10.0, 800.0)

    def test_no_ranges_leaves_autoscaling_alone(self):
        fig = self._fig()

        assert fig.layout.xaxis.range is None
        assert fig.layout.yaxis.range is None

    def test_compact_drops_the_axis_titles_the_slide_provides(self):
        compact = self._fig(compact=True)
        normal = self._fig()

        assert compact.layout.xaxis.title.text is None
        assert compact.layout.yaxis.title.text is None
        assert normal.layout.xaxis.title.text == "Raman Shift (cm⁻¹)"
        assert normal.layout.yaxis.title.text == "Intensity (a.u.)"

    def test_compact_enlarges_text_for_the_shrunken_render(self):
        assert self._fig(compact=True).layout.font.size > 20

    def test_components_use_each_peak_s_configured_color(self):
        x = np.linspace(0.0, 10.0, 20)
        fit = _Fit([_Peak("A", "#EF482E", np.sin(x))], total=np.sin(x))

        fig = plot_composite(x=x, y_data=np.sin(x), fit_result=fit, show_residuals=False)
        component = next(t for t in fig.data if t.name == "A")

        assert component.line.color == "#EF482E"


class TestPeakNormalizationScale:
    def test_uses_the_fitted_peak_not_the_raw_maximum(self):
        """A cosmic ray or the Rayleigh edge routinely tops the raw data.
        Dividing by that would squash the real peaks to a fraction of the frame."""
        y = np.array([0.0, 500.0, 900.0])  # 900 is a one-sample spike
        fit = _Fit(total=np.array([0.0, 500.0, 10.0]))

        assert peak_normalization_scale(y, fit) == 500.0

    def test_falls_back_to_the_data_maximum_without_a_fit(self):
        assert peak_normalization_scale(np.array([0.0, 42.0]), None) == 42.0

    def test_falls_back_when_the_fit_has_no_curve(self):
        assert peak_normalization_scale(np.array([0.0, 42.0]), _Fit(total=None)) == 42.0

    def test_non_positive_data_is_left_alone(self):
        """Returning 1.0 leaves the spectrum untouched, rather than inverting
        or blanking it."""
        assert peak_normalization_scale(np.array([0.0, -5.0]), None) == 1.0
        assert peak_normalization_scale(np.array([]), None) == 1.0

    def test_normalizing_puts_the_fitted_peak_at_one(self):
        y = np.array([0.0, 250.0])
        fit = _Fit(total=np.array([0.0, 250.0]))

        assert (y / peak_normalization_scale(y, fit)).max() == 1.0


class TestPlotFitColumn:
    def _points(self, indices=(1, 4, 7)):
        x = np.linspace(0.0, 400.0, 50)
        return [(i, x, np.sin(x / 40) * (i * 100), _Fit(total=np.sin(x / 40) * (i * 100))) for i in indices]

    def test_one_panel_per_point_titled_by_point_number(self):
        fig = plot_fit_column(self._points())
        titles = [a.text for a in fig.layout.annotations]

        assert titles[:3] == ["Point 1", "Point 4", "Point 7"]

    def test_panels_share_one_x_axis(self):
        """Only the bottom panel keeps tick labels; that is both what makes the
        three read as one measurement and where the extra height comes from."""
        fig = plot_fit_column(self._points())

        assert fig.layout.xaxis.matches or fig.layout.xaxis2.matches or fig.layout.xaxis3.matches
        assert fig.layout.xaxis.showticklabels is False
        assert fig.layout.xaxis2.showticklabels is False

    def test_normalizing_brings_every_panel_to_one(self):
        """Points of very different intensity all peak at 1.0."""
        fig = plot_fit_column(self._points((1, 4, 7)), normalize=True)
        fits = [t for t in fig.data if t.name == "Total Fit"]

        assert len(fits) == 3
        for trace in fits:
            assert max(trace.y) == pytest.approx(1.0)

    def test_without_normalizing_the_panels_keep_their_own_heights(self):
        fig = plot_fit_column(self._points((1, 4, 7)), normalize=False)
        peaks = [max(t.y) for t in fig.data if t.name == "Total Fit"]

        assert peaks[0] < peaks[1] < peaks[2]

    def test_never_draws_its_own_legend(self):
        """The slide draws one legend for the whole grid."""
        fig = plot_fit_column(self._points())

        assert fig.layout.showlegend is False
        assert all(t.showlegend is False for t in fig.data)

    def test_a_short_column_leaves_the_lower_panels_empty(self):
        """Restacking would misalign this column against the others, so the
        third row is still laid out - just with nothing drawn in it."""
        fig = plot_fit_column(self._points((1, 4)))

        assert [a.text for a in fig.layout.annotations] == ["Point 1", "Point 4"]
        assert fig.layout.yaxis3 is not None, "the third row should still exist"
        assert len([t for t in fig.data if t.name == "Data"]) == 2

    def test_a_missing_middle_point_keeps_its_row(self):
        """Point 4 absent: point 7 is still drawn in row 3, not moved up into
        row 2 where it would sit beside the other columns' middle points."""
        p1, _p4, p7 = self._points((1, 4, 7))
        fig = plot_fit_column([p1, (4, None, None, None), p7])

        data_axes = [t.yaxis for t in fig.data if t.name == "Data"]
        assert data_axes == ["y", "y3"]
        assert [a.text for a in fig.layout.annotations] == [
            "Point 1", "Point 4 · no spectrum", "Point 7"]

    def test_ranges_apply_to_every_panel(self):
        fig = plot_fit_column(self._points(), x_range=(0.0, 400.0), y_range=(-0.1, 1.1))

        for axis in (fig.layout.xaxis, fig.layout.xaxis2, fig.layout.xaxis3):
            assert tuple(axis.range) == (0.0, 400.0)
        for axis in (fig.layout.yaxis, fig.layout.yaxis2, fig.layout.yaxis3):
            assert tuple(axis.range) == (-0.1, 1.1)

    def test_no_y_ticks_or_horizontal_grid(self):
        """Every panel peaks at 1.0 by construction, so a Y scale would repeat
        the same numbers nine times across the slide."""
        fig = plot_fit_column(self._points())

        for axis in (fig.layout.yaxis, fig.layout.yaxis2, fig.layout.yaxis3):
            assert axis.showticklabels is False
            assert axis.showgrid is False

    def test_panels_sit_flush_against_each_other(self):
        """With no Y tick labels there is nothing to keep apart, so the gap
        that separated them goes too."""
        fig = plot_fit_column(self._points())

        # Row 1 is the top panel, so its domain sits above row 2's.
        top, middle, bottom = fig.layout.yaxis, fig.layout.yaxis2, fig.layout.yaxis3
        assert middle.domain[1] == pytest.approx(top.domain[0])
        assert bottom.domain[1] == pytest.approx(middle.domain[0])

    def test_x_ticks_are_kept_on_the_bottom_panel(self):
        """Dropping the Y scale doesn't cost the X one, which is still read."""
        fig = plot_fit_column(self._points())

        assert fig.layout.xaxis3.showticklabels is not False

    def test_an_empty_column_is_still_a_valid_figure(self):
        fig = plot_fit_column([])

        assert fig.data == ()
