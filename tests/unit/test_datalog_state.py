"""Unit tests for modules.datalog.ui.datalog_state (Datalog page session state)."""

import importlib

import pytest

from modules.datalog.ui import datalog_state
from modules.datalog.ui.datalog_state import (
    _DETECT_ARTIFACTS,
    _DUPLICATE_ARTIFACTS,
    _FOCUS_ARTIFACTS,
    _RENAME_ARTIFACTS,
    _SELECTIONS,
    get_datalog_state,
    initialize_datalog_state,
    reset_duplicate_results,
    reset_rename_results,
    reset_results,
)


def _fresh_state(monkeypatch):
    """The initial dict, without needing a Streamlit session."""
    store: dict = {}
    monkeypatch.setattr(datalog_state, "st", type("_St", (), {"session_state": store})())
    initialize_datalog_state()
    return store["datalog"]


class TestEveryDerivedKeyIsResettable:
    """A derived key left out of the artifact tuple outlives the folder it came from.

    A RenamePlan holds absolute paths into the *previous* folder. Confirming one
    after a folder switch renames files the operator is no longer looking at,
    and then raises out of `tag_store._relative_key` because those paths are not
    under the new root -- having already renamed them.
    """

    def test_no_key_is_unaccounted_for(self, monkeypatch):
        state = _fresh_state(monkeypatch)

        accounted = (set(_SELECTIONS) | set(_RENAME_ARTIFACTS)
                     | set(_DUPLICATE_ARTIFACTS) | set(_DETECT_ARTIFACTS)
                     | set(_FOCUS_ARTIFACTS))
        assert set(state) - accounted == set()

    def test_a_folder_change_drops_the_detector_and_its_recipe_folder(self, monkeypatch):
        # Another tool's recipe folder carried onto this tool's logs would
        # match every run against the wrong recipes.
        state = _fresh_state(monkeypatch)
        state["folder"] = r"D:\HA1P01\DATALOG"
        state["runcard_folder"] = r"D:\HA1P01\RUNCARD\lcy"
        state["runcard_suggestions"] = {"rows": ["pending"]}
        state["runcard_detect_result"] = 3

        reset_results(state)

        assert state["folder"] == r"D:\HA1P01\DATALOG"
        assert all(state[key] is None for key in _DETECT_ARTIFACTS)


class TestInitializeState:
    def test_a_fresh_state_has_no_folder_and_no_artifacts(self, monkeypatch):
        state = _fresh_state(monkeypatch)

        assert state["folder"] is None
        assert all(state[key] is None for key in _RENAME_ARTIFACTS + _DUPLICATE_ARTIFACTS)

    def test_initializing_twice_does_not_discard_what_is_there(self, monkeypatch):
        state = _fresh_state(monkeypatch)
        state["folder"] = r"D:\HA_DataRecord"

        initialize_datalog_state()

        assert get_datalog_state()["folder"] == r"D:\HA_DataRecord"


class TestResetResults:
    def test_a_folder_change_keeps_the_selection_and_drops_the_artifacts(self, monkeypatch):
        state = _fresh_state(monkeypatch)
        state["folder"] = r"D:\HA_DataRecord"
        state["rename_plans"] = ["a pending sweep"]
        state["rename_result"] = "last sweep's banner"
        state["duplicate_plans"] = ["a pending cleanup"]
        state["duplicate_result"] = "last cleanup's banner"

        reset_results(state)

        assert state["folder"] == r"D:\HA_DataRecord"
        assert all(state[key] is None for key in _RENAME_ARTIFACTS + _DUPLICATE_ARTIFACTS)

    def test_previewing_a_new_sweep_clears_the_previous_ones_banner(self, monkeypatch):
        # They reset together because they are the same conversation: a preview
        # that left the old banner up would appear to report results it has not
        # produced yet.
        state = _fresh_state(monkeypatch)
        state["rename_plans"] = ["a pending sweep"]
        state["rename_result"] = "last sweep's banner"

        reset_rename_results(state)

        assert state["rename_plans"] is None
        assert state["rename_result"] is None

    def test_a_new_rename_preview_leaves_the_cleanup_banner_alone(self, monkeypatch):
        # The two workflows reset independently: a rename preview must not
        # discard a cleanup result the operator has not read yet.
        state = _fresh_state(monkeypatch)
        state["duplicate_result"] = "last cleanup's banner"

        reset_rename_results(state)

        assert state["duplicate_result"] == "last cleanup's banner"

    def test_previewing_a_new_cleanup_clears_the_previous_ones_banner(self, monkeypatch):
        state = _fresh_state(monkeypatch)
        state["duplicate_plans"] = ["a pending cleanup"]
        state["duplicate_result"] = "last cleanup's banner"

        reset_duplicate_results(state)

        assert state["duplicate_plans"] is None
        assert state["duplicate_result"] is None


class TestTheModuleFollowsTheHouseNamingConvention:
    """A state module is named for its page, and its functions carry the same stem.

    `modules.optical.ui.qc_report_state` is the existing instance. This module
    shipped briefly as `ui/state.py` exporting bare `initialize_state` /
    `get_state`, which reads identically to every other page's state module at
    an import site and at the top of a traceback -- and forces an alias on any
    caller that imports two of them.
    """

    def test_the_functions_carry_the_page_stem(self):
        assert callable(datalog_state.initialize_datalog_state)
        assert callable(datalog_state.get_datalog_state)

    def test_no_bare_alias_is_left_behind(self):
        # An alias would keep both spellings live, which is the situation the
        # rename exists to end rather than to document.
        assert not hasattr(datalog_state, "initialize_state")
        assert not hasattr(datalog_state, "get_state")

    def test_the_old_module_path_is_gone(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("modules.datalog.ui.state")
