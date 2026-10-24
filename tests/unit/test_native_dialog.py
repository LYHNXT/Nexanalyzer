"""core.io.native_dialog: the one subprocess round trip behind every file dialog.

The real `_SCRIPT` runs in a real child interpreter here, against a stub
`tkinter` put first on its PYTHONPATH, so the JSON-in/JSON-out protocol is
exercised end to end without a display. The stub records the options it was
called with, which is how a title with quotes and non-Latin characters is
shown to arrive intact -- the defect the four pasted copies had.
"""

import json
import os
import subprocess

import pytest

from core.io import export, folder_picker, native_dialog

_STUB_TK = '''
class Tk:
    def withdraw(self): pass
    def wm_attributes(self, *a): pass
    def destroy(self): pass
'''

_STUB_FILEDIALOG = '''
import json, os

def _answer(kind, options):
    with open(os.environ["STUB_DIALOG_LOG"], "w", encoding="utf-8") as f:
        json.dump({"kind": kind, "options": options}, f)
    return json.loads(os.environ["STUB_DIALOG_ANSWER"])

def asksaveasfilename(**options): return _answer("asksaveasfilename", options)
def askdirectory(**options): return _answer("askdirectory", options)
def askopenfilenames(**options): return tuple(_answer("askopenfilenames", options))
'''


@pytest.fixture
def stub_tk(tmp_path, monkeypatch):
    """A fake `tkinter` for the child, and a way to read what it was asked."""
    pkg = tmp_path / "stub" / "tkinter"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(_STUB_TK, encoding="utf-8")
    (pkg / "filedialog.py").write_text(_STUB_FILEDIALOG, encoding="utf-8")
    log = tmp_path / "call.json"
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "stub") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("STUB_DIALOG_LOG", str(log))

    def answer(value):
        monkeypatch.setenv("STUB_DIALOG_ANSWER", json.dumps(value))

    def call():
        return json.loads(log.read_text(encoding="utf-8"))

    answer("")
    return answer, call


class TestRunDialog:
    def test_a_title_with_quotes_and_non_latin_text_arrives_intact(self, stub_tk):
        answer, call = stub_tk
        answer("C:/out/x.csv")
        title = 'Save "WSe₂" — 測試 \\ report'

        assert native_dialog.run_dialog("asksaveasfilename", title=title) == "C:/out/x.csv"
        assert call()["options"]["title"] == title

    def test_a_non_ascii_path_comes_back_intact(self, stub_tk):
        answer, _ = stub_tk
        answer("C:/資料/wafer 1")
        assert native_dialog.run_dialog("askdirectory") == "C:/資料/wafer 1"

    def test_a_cancelled_dialog_is_empty(self, stub_tk):
        answer, _ = stub_tk
        answer("")
        assert native_dialog.run_dialog("askdirectory") == ""

    def test_multiple_files_come_back_as_a_list(self, stub_tk):
        answer, _ = stub_tk
        answer(["a b.txt", "c,d.txt"])
        assert native_dialog.run_dialog("askopenfilenames") == ["a b.txt", "c,d.txt"]

    def test_a_cancelled_multi_file_dialog_is_an_empty_list(self, stub_tk):
        answer, _ = stub_tk
        answer([])
        assert native_dialog.run_dialog("askopenfilenames") == []

    def test_filetypes_reach_tkinter_as_pairs(self, stub_tk):
        answer, call = stub_tk
        answer("x.png")
        native_dialog.run_dialog("asksaveasfilename", filetypes=[["PNG", "*.png"]])
        assert call()["options"]["filetypes"] == [["PNG", "*.png"]]

    def test_an_unknown_kind_is_refused_before_spawning(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("spawned"))
        with pytest.raises(ValueError, match="unknown dialog kind"):
            native_dialog.run_dialog("askopenfilename")

    def test_no_temp_file_is_written(self, stub_tk, tmp_path, monkeypatch):
        """The script goes on the command line; nothing lands in TEMP to leak."""
        answer, _ = stub_tk
        temp = tmp_path / "temp"
        temp.mkdir()
        monkeypatch.setenv("TMP", str(temp))
        monkeypatch.setenv("TEMP", str(temp))
        native_dialog.run_dialog("askdirectory")
        assert list(temp.iterdir()) == []


class TestExistingDir:
    def test_an_existing_folder_is_kept(self, tmp_path):
        assert native_dialog.existing_dir(str(tmp_path)) == str(tmp_path)

    @pytest.mark.parametrize("value", [None, "", "Z:/does/not/exist"])
    def test_anything_else_falls_back_to_the_os_default(self, value):
        assert native_dialog.existing_dir(value) == ""


class TestWrappers:
    """The public wrappers keep their old contract: a path, or None on cancel."""

    @pytest.fixture
    def calls(self, monkeypatch):
        seen = []

        def fake(kind, timeout=300, **options):
            seen.append((kind, options))
            return seen_answer[0]

        seen_answer = [""]
        for module in (export, folder_picker):
            monkeypatch.setattr(module, "run_dialog", fake)
        return seen, seen_answer

    def test_save_path_passes_the_name_extension_and_existing_folder(self, calls, tmp_path):
        seen, answer = calls
        answer[0] = str(tmp_path / "r.csv")
        got = export.prompt_save_path(str(tmp_path), "r.csv", title="T", default_extension=".csv")
        assert got == str(tmp_path / "r.csv")
        kind, options = seen[-1]
        assert kind == "asksaveasfilename"
        assert options["initialdir"] == str(tmp_path)
        assert options["initialfile"] == "r.csv"
        assert options["defaultextension"] == ".csv"

    def test_save_path_cancel_is_none(self, calls):
        assert export.prompt_save_path(None, "r.csv") is None

    def test_save_folder_cancel_is_none(self, calls):
        assert export.prompt_save_folder(None) is None

    def test_folder_picker_ignores_a_missing_start_folder(self, calls):
        seen, answer = calls
        answer[0] = "C:/picked"
        assert folder_picker.prompt_folder_path("Z:/gone") == "C:/picked"
        assert seen[-1] == ("askdirectory", {"title": "Select Sample Folder", "initialdir": ""})
