"""Unit tests for core.io.precursor_rules (run-ID prefix -> W precursor)."""

import pytest

from core.io.precursor_rules import (
    DEFAULT_RULES,
    get_precursor_rules_path,
    group_of,
    load_precursor_rules,
    precursor_for,
    save_precursor_rules,
    validate_rules,
)


class TestLookup:
    @pytest.mark.parametrize("run_id, expected", [
        ("VAAA03", ("W foil", "VAA")),
        ("VAAB12", ("W foil", "VAA")),
        ("VABD42", ("WO3", "VAB")),
        ("VAHA05", ("W(CO)6", "VAHA")),
        ("VA1P012504002", ("W(CO)6", "VA1P0125")),
        ("VA1P012601010", ("W foil", "VA1P0126")),
        ("VBAC13", ("WO3", "VBA")),
        ("VBBF87", ("W(CO)6", "VBB")),
        ("VBCA27", ("W foil", "VBC")),
        ("vbca27", ("W foil", "VBC")),
    ])
    def test_the_default_rules(self, run_id, expected):
        assert precursor_for(run_id, DEFAULT_RULES) == expected

    def test_the_longest_prefix_wins(self):
        rules = {"VB": {"VBB": "W(CO)6", "VBBG": "WO3"}}
        assert precursor_for("VBBG02", rules) == ("WO3", "VBBG")
        assert precursor_for("VBBF02", rules) == ("W(CO)6", "VBB")

    def test_a_run_no_rule_matches_gives_none(self):
        assert precursor_for("VATC02", DEFAULT_RULES) is None
        assert precursor_for("HADI51", DEFAULT_RULES) is None
        assert precursor_for("CLEANING-4", DEFAULT_RULES) is None
        assert precursor_for("", DEFAULT_RULES) is None
        assert precursor_for(None, DEFAULT_RULES) is None

    def test_the_same_letters_differ_by_group(self):
        rules = {"VB": {"VBC": "W foil"}, "HA": {"HAC": "WO3"}}
        assert precursor_for("VBCA01", rules)[0] == "W foil"
        assert precursor_for("HACA01", rules)[0] == "WO3"

    def test_group_is_the_first_two_letters(self):
        assert group_of(" vbca27") == "VB"


class TestStore:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "precursor_rules.json"
        save_precursor_rules({"VB": {"vbc": "W foil "}}, path=path)
        assert load_precursor_rules(path=path) == {"VB": {"VBC": "W foil"}}

    def test_missing_or_malformed_file_gives_the_defaults(self, tmp_path):
        assert load_precursor_rules(path=tmp_path / "none.json") == DEFAULT_RULES
        bad = tmp_path / "bad.json"
        bad.write_text("not json {{")
        assert load_precursor_rules(path=bad) == DEFAULT_RULES

    def test_a_misfiled_prefix_is_dropped_on_load(self, tmp_path):
        path = tmp_path / "precursor_rules.json"
        path.write_text('{"groups": {"VB": {"VBC": "W foil", "VAA": "W foil", "VBX": ""}}}')
        assert load_precursor_rules(path=path) == {"VB": {"VBC": "W foil"}}

    def test_an_ambiguous_set_is_refused(self, tmp_path):
        assert validate_rules({"VB": {"VAA": "W foil"}})
        assert validate_rules({"VBX": {"VBX": "W foil"}})
        assert validate_rules({"VB": {"VBC": " "}})
        with pytest.raises(ValueError):
            save_precursor_rules({"VB": {"VAA": "W foil"}}, path=tmp_path / "x.json")

    def test_the_committed_file_holds_the_defaults(self):
        # data/precursor_rules.json starts from the user's rules (2026-11-08);
        # an edit from the Material Presets page is a deliberate change here too.
        assert load_precursor_rules(get_precursor_rules_path()) == DEFAULT_RULES
