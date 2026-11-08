"""Unit tests for core.io.shared_read: reading a file another program holds open."""

import sys

import pandas as pd
import pytest

from core.io.shared_read import read_bytes_shared
from modules.splittable.io.workbook import read_split_table

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows share modes")


def _hold_like_excel(path):
    """Open `path` the way Excel and OneDrive do: with DELETE access.

    Windows then refuses Python's own ``open()``, which does not share delete --
    the [Errno 13] the Split Table page showed on 2026-11-06.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE]
    generic_read, delete, share_all = 0x80000000, 0x10000, 0x7
    handle = kernel32.CreateFileW(str(path), generic_read | delete, share_all, None, 3, 0x80, None)
    assert handle not in (None, wintypes.HANDLE(-1).value)
    return handle, kernel32.CloseHandle


class TestReadBytesShared:
    def test_a_plain_file_reads_as_read_bytes(self, tmp_path):
        path = tmp_path / "a.bin"
        path.write_bytes(b"split table")
        assert read_bytes_shared(path) == b"split table"

    def test_a_missing_file_raises_as_read_bytes_does(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_bytes_shared(tmp_path / "missing.xlsx")

    @windows_only
    def test_a_file_held_with_delete_access_still_reads(self, tmp_path):
        path = tmp_path / "held.bin"
        path.write_bytes(b"last save")
        handle, close = _hold_like_excel(path)
        try:
            with pytest.raises(PermissionError):
                path.read_bytes()          # the failure this module exists for
            assert read_bytes_shared(path) == b"last save"
        finally:
            close(handle)

    @windows_only
    def test_a_split_table_open_in_excel_still_loads(self, tmp_path):
        path = tmp_path / "VB_SplitTable_from_runcards.xlsx"
        pd.DataFrame({"WaferID": ["VBCA27"], "Month": [202611.0]}).to_excel(path, index=False)
        handle, close = _hold_like_excel(path)
        try:
            table = read_split_table(path)
        finally:
            close(handle)
        assert list(table.data["WaferID"]) == ["VBCA27"]
