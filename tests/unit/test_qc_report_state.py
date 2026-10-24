"""Unit tests for modules.optical.ui.qc_report_state (QC Report session state)."""

from modules.optical.ui import qc_report_state
from modules.optical.ui.qc_report_state import (
    _COMPOSITE_ARTIFACTS,
    _OPTICAL_ARTIFACTS,
    _PL_ARTIFACTS,
    _RAMAN_ARTIFACTS,
    _SELECTIONS,
    reset_optical_results,
    reset_results,
    reset_spectra_results,
)

_DERIVED = _OPTICAL_ARTIFACTS + _RAMAN_ARTIFACTS + _PL_ARTIFACTS + _COMPOSITE_ARTIFACTS


def _fresh_state(monkeypatch):
    """The initial dict, without needing a Streamlit session."""
    store: dict = {}
    monkeypatch.setattr(qc_report_state, "st", type("_St", (), {"session_state": store})())
    qc_report_state.initialize_qc_report_state()
    return store["qc_report"]


class TestEveryDerivedKeyIsResettable:
    """A derived key left out of the artifact tuples outlives its run.

    The QC Report's results are rendered from persisted state, so a key that
    `reset_results` does not clear keeps showing last run's answer under this
    run's heading — the failure the module docstring describes, and the one the
    gate verdict ("check that MoS2 is what this sample is") would repeat
    against the newly chosen material.
    """

    def test_no_key_is_unaccounted_for(self, monkeypatch):
        state = _fresh_state(monkeypatch)

        assert set(state) - (set(_SELECTIONS) | set(_DERIVED)) == set()

    def test_no_tuple_names_a_key_that_does_not_exist(self, monkeypatch):
        state = _fresh_state(monkeypatch)

        assert (set(_SELECTIONS) | set(_DERIVED)) - set(state) == set()

    def test_no_key_is_claimed_by_two_tuples(self, monkeypatch):
        """Overlap would make one reset's behaviour depend on the order the
        other's keys happen to be listed in."""
        tuples = (_SELECTIONS, _OPTICAL_ARTIFACTS, _RAMAN_ARTIFACTS,
                  _PL_ARTIFACTS, _COMPOSITE_ARTIFACTS)
        named = [key for group in tuples for key in group]

        assert len(named) == len(set(named))

    def test_reset_results_clears_every_derived_key(self, monkeypatch):
        state = _fresh_state(monkeypatch)
        for key in state:
            state[key] = "set"

        reset_results(state)

        assert [k for k in _DERIVED if state[k] is not None] == []
        assert all(state[k] == "set" for k in _SELECTIONS)


class TestPerBlockResets:
    """A Raman peak edit must not discard a 30-second OM segmentation."""

    def test_spectra_reset_leaves_the_optical_artifacts(self, monkeypatch):
        state = _fresh_state(monkeypatch)
        for key in state:
            state[key] = "set"

        reset_spectra_results(state)

        assert all(state[k] is None for k in _RAMAN_ARTIFACTS + _PL_ARTIFACTS)
        assert all(state[k] == "set" for k in _OPTICAL_ARTIFACTS)

    def test_optical_reset_leaves_the_fitted_artifacts(self, monkeypatch):
        state = _fresh_state(monkeypatch)
        for key in state:
            state[key] = "set"

        reset_optical_results(state)

        assert all(state[k] is None for k in _OPTICAL_ARTIFACTS)
        assert all(state[k] == "set" for k in _RAMAN_ARTIFACTS + _PL_ARTIFACTS)


class TestCompositeArtifacts:
    """The summary page and the workbook are built from everything, so no
    partial reset may leave one standing.

    A surviving summary page would name one material beside a figure drawn from
    another — the exact staleness these resets exist to prevent, and harder to
    spot than a stale figure because the page looks complete.
    """

    def test_an_optical_reset_drops_them(self, monkeypatch):
        state = _fresh_state(monkeypatch)
        for key in state:
            state[key] = "set"

        reset_optical_results(state)

        assert all(state[k] is None for k in _COMPOSITE_ARTIFACTS)

    def test_a_spectra_reset_drops_them(self, monkeypatch):
        state = _fresh_state(monkeypatch)
        for key in state:
            state[key] = "set"

        reset_spectra_results(state)

        assert all(state[k] is None for k in _COMPOSITE_ARTIFACTS)

    def test_the_batch_result_goes_with_the_fits_that_produced_it(self, monkeypatch):
        """Raman and PL come out of one `run_sample_batch` call, so keeping the
        batch after dropping either technique's artifacts would leave figures
        standing beside a result that no longer produced them."""
        assert "batch_result" in _COMPOSITE_ARTIFACTS

        state = _fresh_state(monkeypatch)
        state["batch_result"] = "set"

        reset_spectra_results(state)

        assert state["batch_result"] is None


class TestSelectFolder:
    """One way to change folders, shared by the QC Report and OneNote pages.

    OneNote's "Build for <wafer>" used to set the folder and clear results but
    keep `layer_source == "operator"`, so a 1L picked by hand for one wafer was
    silently applied to the next.
    """

    def test_an_operator_layer_choice_does_not_follow_into_the_next_folder(self, monkeypatch):
        state = _fresh_state(monkeypatch)
        state["layer_source"] = "operator"
        state["layer_context"] = ("A", "50X", "WSe2")

        qc_report_state.select_folder(state, "B", scan="scan-B", magnification="100X")

        assert state["layer_source"] is None
        assert state["layer_context"] is None

    def test_it_sets_the_folder_scan_and_magnification(self, monkeypatch):
        state = _fresh_state(monkeypatch)

        qc_report_state.select_folder(state, "B", scan="scan-B", magnification="100X")

        assert (state["folder"], state["scan"], state["magnification"]) == ("B", "scan-B", "100X")

    def test_every_derived_result_of_the_previous_folder_is_dropped(self, monkeypatch):
        state = _fresh_state(monkeypatch)
        for key in _DERIVED:
            state[key] = "stale"

        qc_report_state.select_folder(state, "B", scan=None, magnification=None)

        assert all(state[key] is None for key in _DERIVED)

    def test_the_material_and_layer_are_kept(self, monkeypatch):
        """Only who chose the layer is forgotten; the page re-suggests from there."""
        state = _fresh_state(monkeypatch)
        state["material"], state["reference_layer"] = "WSe2_QC", "1L"

        qc_report_state.select_folder(state, "B", scan=None, magnification=None)

        assert (state["material"], state["reference_layer"]) == ("WSe2_QC", "1L")
