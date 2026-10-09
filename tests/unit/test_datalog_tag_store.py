"""Unit tests for modules.datalog.io.tag_store (the runcard_tags.json sidecar)."""

import json

import pytest

from modules.datalog.io.tag_store import (
    RUNCARD_TAGS_FILENAME,
    SidecarUnreadable,
    clear_runcard_tag,
    get_runcard_tag,
    load_runcard_tags,
    move_runcard_tag,
    set_runcard_tag,
    set_runcard_tags,
)


def _run(folder, name="2026-08-06_173312~VBBE00.csv"):
    """An empty file standing in for a run: nothing here reads its contents."""
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    return str(path)


def _sidecar(folder):
    return json.loads((folder / RUNCARD_TAGS_FILENAME).read_text(encoding="utf-8"))


class TestSetAndGetRuncardTag:
    def test_a_stored_tag_round_trips(self, tmp_path):
        path = _run(tmp_path, "raw_dump.csv")

        set_runcard_tag(str(tmp_path), path, "VBBE00")

        assert get_runcard_tag(str(tmp_path), path) == "VBBE00"

    def test_an_empty_tag_deletes_the_entry_rather_than_storing_it(self, tmp_path):
        # That is how "clear this tag" is expressed on disk, and it hands the
        # question back to the filename.
        path = _run(tmp_path)
        set_runcard_tag(str(tmp_path), path, "EDITED")

        set_runcard_tag(str(tmp_path), path, "")

        assert _sidecar(tmp_path) == {}
        assert get_runcard_tag(str(tmp_path), path) == "VBBE00"

    def test_clearing_a_tag_that_was_never_set_is_a_no_op(self, tmp_path):
        path = _run(tmp_path, "raw_dump.csv")

        set_runcard_tag(str(tmp_path), path, "")

        assert _sidecar(tmp_path) == {}

    def test_keys_are_forward_slash_paths_relative_to_the_data_folder(self, tmp_path):
        # So the whole folder can be copied to another machine, or to a share
        # with a different drive letter, without orphaning a tag.
        path = _run(tmp_path / "2026-08" / "tool-b", "raw_dump.csv")

        set_runcard_tag(str(tmp_path), path, "VBBE02")

        assert _sidecar(tmp_path) == {"2026-08/tool-b/raw_dump.csv": "VBBE02"}


class TestTagFallsBackToTheFilename:
    """A folder of already-named runs reads as tagged without a tagging step.

    datalog_monitor names runs `<timestamp>~<tag>.csv`, so the tag has a second
    home in the filename itself. Reading it from there is what lets the page
    show the right runcard against several hundred runs while leaving
    runcard_tags.json entirely unwritten.
    """

    def test_the_tag_is_read_out_of_the_filename(self, tmp_path):
        path = _run(tmp_path)

        assert get_runcard_tag(str(tmp_path), path) == "VBBE00"

    def test_reading_a_tag_writes_nothing(self, tmp_path):
        path = _run(tmp_path)

        get_runcard_tag(str(tmp_path), path)

        assert not (tmp_path / RUNCARD_TAGS_FILENAME).exists()

    def test_a_filename_with_no_delimiter_has_no_tag(self, tmp_path):
        path = _run(tmp_path, "HADH01.csv")

        assert get_runcard_tag(str(tmp_path), path) == ""

    def test_a_hand_named_file_splits_on_the_last_delimiter(self, tmp_path):
        # The renamer never writes more than one delimiter; an operator can.
        path = _run(tmp_path, "2026-08-06_173312~retry~VBBE00.csv")

        assert get_runcard_tag(str(tmp_path), path) == "VBBE00"

    def test_a_stored_tag_beats_the_filename(self, tmp_path):
        path = _run(tmp_path)

        set_runcard_tag(str(tmp_path), path, "EDITED")

        assert get_runcard_tag(str(tmp_path), path) == "EDITED"


class TestPreloadedTagMap:
    def test_a_supplied_map_is_used_instead_of_reading_the_file(self, tmp_path):
        path = _run(tmp_path, "raw_dump.csv")

        assert get_runcard_tag(str(tmp_path), path, {"raw_dump.csv": "SUPPLIED"}) == "SUPPLIED"

    def test_an_empty_but_loaded_map_is_respected_not_re_read(self, tmp_path):
        # Tested with `is not None` rather than for truthiness: the run list
        # loads the map once for several hundred rows, and a folder with no
        # tags at all must not turn that into several hundred file reads.
        path = _run(tmp_path)
        set_runcard_tag(str(tmp_path), path, "ON DISK")

        assert get_runcard_tag(str(tmp_path), path, {}) == "VBBE00"


class TestSetRuncardTags:
    """The detector's batch save: the single call's rules, in one write."""

    def test_several_tags_land_in_one_write(self, tmp_path, monkeypatch):
        import modules.datalog.io.tag_store as tag_store

        a = _run(tmp_path, "a.csv")
        b = _run(tmp_path / "2026_09", "b.csv")
        writes = []
        real_write = tag_store._write_json
        monkeypatch.setattr(tag_store, "_write_json", lambda path, data: (writes.append(1), real_write(path, data)))

        set_runcard_tags(str(tmp_path), {a: "HADH75", b: "CLEANING-4"})

        assert _sidecar(tmp_path) == {"a.csv": "HADH75", "2026_09/b.csv": "CLEANING-4"}
        assert len(writes) == 1

    def test_existing_tags_are_kept_and_an_empty_tag_deletes(self, tmp_path):
        a = _run(tmp_path, "a.csv")
        b = _run(tmp_path, "b.csv")
        set_runcard_tag(str(tmp_path), a, "KEEP")
        set_runcard_tag(str(tmp_path), b, "OLD")

        set_runcard_tags(str(tmp_path), {b: ""})

        assert _sidecar(tmp_path) == {"a.csv": "KEEP"}


class TestMoveRuncardTag:
    def test_a_tag_follows_its_file_to_the_new_name(self, tmp_path):
        old = _run(tmp_path, "raw_dump.csv")
        new = str(tmp_path / "2026-08-06_173312~VBBE00.csv")
        set_runcard_tag(str(tmp_path), old, "VBBE00")

        move_runcard_tag(str(tmp_path), old, new)

        assert _sidecar(tmp_path) == {"2026-08-06_173312~VBBE00.csv": "VBBE00"}

    def test_renaming_an_untagged_file_writes_nothing(self, tmp_path):
        # A tag that was only ever derived from the filename has no entry to
        # move, and the new name still encodes it. Writing one here would turn
        # an implicit tag into an explicit one as a side effect of a rename.
        old = _run(tmp_path, "raw_dump.csv")
        new = str(tmp_path / "2026-08-06_173312.csv")

        move_runcard_tag(str(tmp_path), old, new)

        assert not (tmp_path / RUNCARD_TAGS_FILENAME).exists()


class TestPathsFromOutsideTheDataFolder:
    """Keying is lexical, so a path from outside the root raises rather than degrading.

    That is the contract, not an oversight: paths reaching this module come from
    `scanner.discover_csv_files(root_folder)`, which by construction yields
    absolute paths under the root in the same spelling. The caller that breaks
    it is the one holding a path across a folder change, which is why
    `ui.state.reset_results` drops a pending rename plan the moment the folder
    moves.
    """

    def test_reading_a_tag_for_a_file_outside_the_root_raises(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        outside = _run(tmp_path / "elsewhere")

        with pytest.raises(ValueError):
            get_runcard_tag(str(root), outside)

    def test_writing_a_tag_for_a_file_outside_the_root_raises(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        outside = _run(tmp_path / "elsewhere")

        with pytest.raises(ValueError):
            set_runcard_tag(str(root), outside, "VBBE00")

    def test_a_relative_path_against_an_absolute_root_raises_too(self, tmp_path):
        # Lexically not under the root, even though it obviously belongs.
        _run(tmp_path)

        with pytest.raises(ValueError):
            get_runcard_tag(str(tmp_path), "2026-08-06_173312~VBBE00.csv")


class TestSidecarFileHandling:
    def test_a_missing_sidecar_reads_as_an_empty_map(self, tmp_path):
        assert load_runcard_tags(str(tmp_path)) == {}

    def test_a_malformed_sidecar_degrades_instead_of_refusing_to_open_the_page(self, tmp_path):
        (tmp_path / RUNCARD_TAGS_FILENAME).write_text("{ not json", encoding="utf-8")

        assert load_runcard_tags(str(tmp_path)) == {}

    def test_a_malformed_sidecar_is_left_alone_for_a_human_to_look_at(self, tmp_path):
        path = tmp_path / RUNCARD_TAGS_FILENAME
        path.write_text("{ not json", encoding="utf-8")

        load_runcard_tags(str(tmp_path))

        assert path.read_text(encoding="utf-8") == "{ not json"

    def test_a_sidecar_opened_and_saved_in_notepad_still_reads(self, tmp_path):
        # Notepad writes a BOM. Operators do open these files.
        (tmp_path / RUNCARD_TAGS_FILENAME).write_text(
            '{"raw_dump.csv": "VBBE00"}', encoding="utf-8-sig"
        )

        assert load_runcard_tags(str(tmp_path)) == {"raw_dump.csv": "VBBE00"}

    def test_the_sidecar_is_written_sorted_and_without_a_bom(self, tmp_path):
        # Deterministic output, so the file diffs cleanly and datalog_monitor
        # reading it concurrently never sees a reordered map.
        set_runcard_tag(str(tmp_path), _run(tmp_path, "b.csv"), "SECOND")
        set_runcard_tag(str(tmp_path), _run(tmp_path, "a.csv"), "FIRST")

        raw = (tmp_path / RUNCARD_TAGS_FILENAME).read_bytes()

        assert not raw.startswith(b"\xef\xbb\xbf")
        assert raw.decode("utf-8").index('"a.csv"') < raw.decode("utf-8").index('"b.csv"')

    def test_the_temp_file_of_the_atomic_write_does_not_survive(self, tmp_path):
        # Another application reads this file while this one writes it, so the
        # write is a temp file plus an atomic rename -- and the temp file must
        # not be left behind for the next scan to find.
        set_runcard_tag(str(tmp_path), _run(tmp_path), "VBBE00")

        assert [p.name for p in tmp_path.glob("*.tmp")] == []


class TestASaveNeverWipesASidecarItCouldNotRead:
    """Every save is read-modify-write of the whole file. A read that degraded
    to {} used to be written back, replacing every stored tag with one."""

    @pytest.fixture(autouse=True)
    def _no_retry_wait(self, monkeypatch):
        monkeypatch.setattr("modules.datalog.io.tag_store._UPDATE_READ_RETRY_S", 0)

    def _damaged(self, tmp_path, text='{"a.csv": "HADH01", "b.csv": "HAD'):
        (tmp_path / RUNCARD_TAGS_FILENAME).write_text(text, encoding="utf-8")
        return (tmp_path / RUNCARD_TAGS_FILENAME).read_text(encoding="utf-8")

    @pytest.mark.parametrize("save", [
        lambda root, run: set_runcard_tag(root, run, "HADH99"),
        lambda root, run: set_runcard_tags(root, {run: "HADH99"}),
        lambda root, run: clear_runcard_tag(root, run),
        lambda root, run: move_runcard_tag(root, run, run + ".new"),
    ])
    def test_a_damaged_sidecar_is_left_untouched(self, tmp_path, save):
        before = self._damaged(tmp_path)
        run = _run(tmp_path)

        with pytest.raises(SidecarUnreadable):
            save(str(tmp_path), run)

        assert (tmp_path / RUNCARD_TAGS_FILENAME).read_text(encoding="utf-8") == before

    def test_it_is_an_oserror_so_the_pages_report_it(self):
        assert issubclass(SidecarUnreadable, OSError)

    def test_a_sidecar_holding_a_list_is_refused_for_writing(self, tmp_path):
        self._damaged(tmp_path, '["HADH01"]')
        with pytest.raises(SidecarUnreadable):
            set_runcard_tag(str(tmp_path), _run(tmp_path), "HADH99")

    def test_a_sidecar_holding_a_list_reads_as_empty(self, tmp_path):
        self._damaged(tmp_path, '["HADH01"]')
        run = _run(tmp_path)
        assert load_runcard_tags(str(tmp_path)) == {}
        assert get_runcard_tag(str(tmp_path), run) == "VBBE00"  # the filename's

    def test_a_briefly_locked_sidecar_is_retried(self, tmp_path, monkeypatch):
        set_runcard_tag(str(tmp_path), _run(tmp_path, "a.csv"), "HADH01")
        import builtins
        real_open, failures = builtins.open, [1]

        def flaky(path, mode="r", *a, **kw):
            if str(path).endswith(RUNCARD_TAGS_FILENAME) and "r" in mode and failures:
                failures.pop()
                raise PermissionError(32, "being used by another process")
            return real_open(path, mode, *a, **kw)

        monkeypatch.setattr(builtins, "open", flaky)
        set_runcard_tag(str(tmp_path), _run(tmp_path, "b.csv"), "HADH02")

        assert _sidecar(tmp_path) == {"a.csv": "HADH01", "b.csv": "HADH02"}
