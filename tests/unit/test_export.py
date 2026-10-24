"""Unit tests for core.io.export (figure rasterization and output filenames).

The dialog wrappers (prompt_save_path/prompt_save_folder) and the subprocess
round trip under them are covered in test_native_dialog.py.
Fit-results CSVs are covered by test_results_csv.py.
"""

import plotly.graph_objects as go

import pytest

from core.io.export import export_figure_html, export_figure_png, create_filename


class TestExportFigureHtml:
    def test_produces_standalone_html(self):
        fig = go.Figure(data=[go.Scatter(x=[1, 2, 3], y=[1, 4, 9])])
        html = export_figure_html(fig)
        assert html.strip().lower().startswith("<!doctype html>") or "<html" in html.lower()
        assert "plotly" in html.lower()


class TestCreateFilename:
    def test_strips_txt_extension(self):
        assert create_filename("sample_raman.txt", "fit", "csv") == "sample_raman_fit.csv"

    def test_no_extension_to_strip(self):
        assert create_filename("sample", "preview", "png") == "sample_preview.png"


class TestExportFigurePng:
    """Plotly 6 deprecated ``to_image(engine=...)`` and Plotly 7 removed it.

    Passing it raised ``TypeError: to_image() got an unexpected keyword
    argument 'engine'`` on every Sample Report build under Plotly 7, so these
    fakes take the strict signature and would fail again if it came back.
    """

    class _Figure:
        def __init__(self):
            self.calls = []

        def to_image(self, *, format, width, height, scale):
            self.calls.append(
                {"format": format, "width": width, "height": height, "scale": scale}
            )
            return b"PNG-BYTES"

    def test_passes_no_engine_argument(self):
        fig = self._Figure()
        assert export_figure_png(fig, width=800, height=400, scale=1.0) == b"PNG-BYTES"
        assert fig.calls == [
            {"format": "png", "width": 800, "height": 400, "scale": 1.0}
        ]

    def test_wraps_failures_in_runtimeerror_keeping_the_cause(self):
        class _Broken:
            def to_image(self, **kwargs):
                raise ValueError("chrome not found")

        with pytest.raises(RuntimeError, match="PNG export failed") as excinfo:
            export_figure_png(_Broken())
        assert isinstance(excinfo.value.__cause__, ValueError)


class TestRenderingIsBoundedAndSerialised:
    """v5.39.0: PNGs render through one persistent kaleido server, one at a
    time, with a timeout. One-shot renders each launched a Chrome; one in
    fifteen never answered, which hung a QC test for 12 h."""

    @pytest.fixture(autouse=True)
    def _no_real_server(self, monkeypatch):
        import core.io.export as export
        calls = {"ensure": 0, "abandon": 0}
        monkeypatch.setattr(export, "_ensure_render_server", lambda: calls.__setitem__("ensure", calls["ensure"] + 1))
        monkeypatch.setattr(export, "_abandon_render_server", lambda: calls.__setitem__("abandon", calls["abandon"] + 1))
        self.calls = calls

    def test_a_render_that_never_returns_times_out_with_a_clear_error(self, monkeypatch):
        import threading
        import core.io.export as export
        monkeypatch.setattr(export, "RENDER_TIMEOUT_S", 0.2)
        release = threading.Event()

        class _Stuck:
            def to_image(self, **kwargs):
                release.wait(5)
                return b"late"

        try:
            with pytest.raises(RuntimeError, match="timed out"):
                export_figure_png(_Stuck())
            assert self.calls["abandon"] == 1
        finally:
            release.set()

    def test_concurrent_renders_each_get_their_own_image(self):
        import threading
        import time

        class _Fig:
            def __init__(self, tag):
                self.tag = tag

            def to_image(self, **kwargs):
                time.sleep(0.01)
                return self.tag

        results = {}

        def run(i):
            results[i] = export_figure_png(_Fig(f"png-{i}".encode()))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results == {i: f"png-{i}".encode() for i in range(8)}

    def test_a_render_error_is_reported_not_swallowed(self):
        class _Broken:
            def to_image(self, **kwargs):
                raise ValueError("bad figure")

        with pytest.raises(RuntimeError, match="bad figure"):
            export_figure_png(_Broken())
        assert self.calls["abandon"] == 0
