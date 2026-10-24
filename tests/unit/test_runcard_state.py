"""Unit tests for modules.runcard.ui.runcard_state (Runcard page session state)."""

from modules.runcard.ui import runcard_state as state
from modules.runcard.ui.runcard_state import (
    _COMPOSITE_ARTIFACTS,
    _FOCUS_ARTIFACTS,
    _SCAN_ARTIFACTS,
    _SELECTIONS,
    chartable_selection,
    get_runcard_state,
    initialize_runcard_state,
    reset_figures,
    reset_results,
    reset_scan_results,
    runcard_table_key,
)

_DERIVED = _SCAN_ARTIFACTS + _COMPOSITE_ARTIFACTS + _FOCUS_ARTIFACTS


def _fresh_state(monkeypatch):
    """The initial dict, without needing a Streamlit session."""
    store: dict = {}
    monkeypatch.setattr(state, "st", type("_St", (), {"session_state": store})())
    initialize_runcard_state()
    return store["runcard"]


def _fill(state_dict):
    """Give every key a value, so a reset has something to drop."""
    state_dict["folder"] = r"C:\recipes"
    state_dict["selected"] = ("VBBE00.csv",)
    for key in _DERIVED:
        state_dict[key] = "derived"


class TestEveryDerivedKeyIsResettable:
    """A derived key left out of the artifact tuples outlives the run that made it.

    The page renders from persisted state, so a scan key that `reset_results`
    does not clear leaves the previous folder's forty recipes in the table
    under the new folder's path -- and ticking one charts a file that is not
    there.
    """

    def test_no_key_is_unaccounted_for(self, monkeypatch):
        state_dict = _fresh_state(monkeypatch)

        assert set(state_dict) - (set(_SELECTIONS) | set(_DERIVED)) == set()

    def test_reset_results_drops_every_derived_key(self, monkeypatch):
        state_dict = _fresh_state(monkeypatch)
        _fill(state_dict)

        reset_results(state_dict)

        assert all(state_dict[key] is None for key in _DERIVED)

    def test_reset_results_keeps_the_operators_own_choices(self, monkeypatch):
        state_dict = _fresh_state(monkeypatch)
        _fill(state_dict)

        reset_results(state_dict)

        assert state_dict["folder"] == r"C:\recipes"
        assert state_dict["selected"] == ("VBBE00.csv",)


class TestTickingARowDoesNotRewalkTheFolder:
    """The scan is the expensive artifact; the figures are arithmetic.

    Fifty-three files parsed and replayed is what a folder walk costs on the
    example archive, and re-ticking a row must not pay it again.
    """

    def test_reset_figures_leaves_the_scan_alone(self, monkeypatch):
        state_dict = _fresh_state(monkeypatch)
        _fill(state_dict)

        reset_figures(state_dict)

        assert state_dict["figures"] is None
        assert all(state_dict[key] == "derived" for key in _SCAN_ARTIFACTS)

    def test_reset_scan_results_drops_the_whole_walk_together(self, monkeypatch):
        # They are produced in one pass -- the list, each profile, each table
        # row, the gas columns those needed, and the failures -- so keeping any
        # one of them across a re-scan describes a folder by two readings of it.
        state_dict = _fresh_state(monkeypatch)
        _fill(state_dict)

        reset_scan_results(state_dict)

        assert all(state_dict[key] is None for key in _SCAN_ARTIFACTS)


class TestInitialize:
    def test_a_fresh_state_has_no_folder_and_nothing_derived(self, monkeypatch):
        state_dict = _fresh_state(monkeypatch)

        assert state_dict["folder"] is None
        assert state_dict["selected"] == ()
        assert all(state_dict[key] is None for key in _DERIVED)

    def test_initializing_twice_does_not_wipe_a_scan(self, monkeypatch):
        # It runs on every page render, and the page's own results live here.
        state_dict = _fresh_state(monkeypatch)
        state_dict["runcards"] = ["VBBE00.csv"]

        initialize_runcard_state()

        assert state_dict["runcards"] == ["VBBE00.csv"]

    def test_get_state_initializes_on_first_call(self, monkeypatch):
        store: dict = {}
        monkeypatch.setattr(state, "st", type("_St", (), {"session_state": store})())

        returned = get_runcard_state()

        assert returned is store["runcard"]

class TestOneSpellingPerFunction:
    """Two names for one function have nothing keeping them in step.

    The stemmed spelling is the API, after `optical/ui/qc_report_state.py`.
    The bare `initialize_state`/`get_state` aliases that used to sit beside it
    are the shape this repo already refuses for the diagnostic ratios -- the
    day one name grows a guard the other lacks, the same call reads as correct
    at both call sites and only one of them is.
    """

    def test_the_bare_aliases_are_gone(self):
        assert not hasattr(state, "initialize_state")
        assert not hasattr(state, "get_state")

    def test_the_stemmed_spelling_is_what_is_exported(self):
        assert callable(state.initialize_runcard_state)
        assert callable(state.get_runcard_state)


class TestTheTableIsKeyedByItsFolder:
    """st.dataframe identifies a selection by key + selection_mode, not by data.

    So a tick on row 3 of folder A survived the switch to folder B under one
    fixed key, and the page charted whatever recipe B listed third. A new key
    is a new widget, and a new widget starts unticked.
    """

    def test_two_folders_do_not_share_a_widget(self):
        assert runcard_table_key(r"C:\archive\A") != runcard_table_key(r"C:\archive\B")

    def test_the_same_folder_keeps_its_widget(self):
        # A re-scan of one folder must keep the tick: those row indices still
        # mean what they meant.
        assert runcard_table_key(r"C:\archive\A") == runcard_table_key(r"C:\archive\A")


class TestOnlyTickedRowsWithAProfileAreCharted:
    """A selection outlives the scan that produced it.

    The table's key unticks it on a folder change -- but only when there is a
    table. A folder whose CSVs all read as datalogs draws none, so `selected`
    keeps naming the previous folder's paths with no profile behind them, and
    the page drew "3. Profile" over zero charts.
    """

    def test_a_selection_with_no_profiles_charts_nothing(self, monkeypatch):
        state_dict = _fresh_state(monkeypatch)
        state_dict["selected"] = (r"C:\A\VBBE00.csv",)
        state_dict["profiles"] = {}

        assert chartable_selection(state_dict) == []

    def test_a_selection_left_over_from_the_previous_folder_charts_nothing(self, monkeypatch):
        state_dict = _fresh_state(monkeypatch)
        state_dict["selected"] = (r"C:\A\VBBE00.csv",)
        state_dict["profiles"] = {r"C:\B\VBBE01.csv": "profile"}

        assert chartable_selection(state_dict) == []

    def test_a_recipe_that_failed_to_replay_is_dropped_and_the_rest_kept(self, monkeypatch):
        # One malformed file must not cost the other ticked rows their charts.
        state_dict = _fresh_state(monkeypatch)
        state_dict["selected"] = (r"C:\A\good.csv", r"C:\A\broken.csv")
        state_dict["profiles"] = {r"C:\A\good.csv": "profile"}

        assert chartable_selection(state_dict) == [r"C:\A\good.csv"]

    def test_tick_order_is_kept(self, monkeypatch):
        state_dict = _fresh_state(monkeypatch)
        state_dict["selected"] = (r"C:\A\b.csv", r"C:\A\a.csv")
        state_dict["profiles"] = {r"C:\A\a.csv": "profile", r"C:\A\b.csv": "profile"}

        assert chartable_selection(state_dict) == [r"C:\A\b.csv", r"C:\A\a.csv"]

    def test_an_unscanned_folder_charts_nothing(self, monkeypatch):
        # `profiles` is None before the first scan, not {}.
        state_dict = _fresh_state(monkeypatch)
        state_dict["selected"] = (r"C:\A\VBBE00.csv",)

        assert chartable_selection(state_dict) == []
