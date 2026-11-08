"""
Read a whole file that another program holds open -- Excel, OneDrive.

Python's ``open()`` on Windows asks to share the file for reading and writing
but not for **deletion**. Excel, and OneDrive while it syncs, hold their
workbooks with ``DELETE`` access, and Windows then refuses any opener that
does not share delete: ``[Errno 13] Permission denied``, on a file anyone can
open in Explorer. That is what the Split Table page showed for
VB_SplitTable_from_runcards.xlsx while it was open in Excel (2026-11-06).

`read_bytes_shared` asks for read access only and shares everything, which is
what Explorer and .NET's ``FileShare.ReadWrite | FileShare.Delete`` do. It
reads the bytes on disk -- the last save, not unsaved edits in Excel.
"""

import sys
from pathlib import Path
from typing import Union

_GENERIC_READ = 0x80000000
_SHARE_ALL = 0x1 | 0x2 | 0x4          # FILE_SHARE_READ | WRITE | DELETE
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80


def read_bytes_shared(path: Union[str, Path]) -> bytes:
    """The file's bytes, even while another program holds it open.

    Tries a plain read first; only a refused read on Windows takes the shared
    route, so every other platform and every other error behaves exactly as
    ``Path.read_bytes()``. A file that cannot be read either way re-raises the
    original ``PermissionError``.
    """
    path = Path(path)
    try:
        return path.read_bytes()
    except PermissionError:
        if sys.platform != "win32":
            raise
        try:
            return _read_windows_shared(path)
        except OSError:
            pass
        raise


def _read_windows_shared(path: Path) -> bytes:
    import ctypes
    import msvcrt
    import os
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.restype = wintypes.HANDLE
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = create(str(path), _GENERIC_READ, _SHARE_ALL, None, _OPEN_EXISTING,
                    _FILE_ATTRIBUTE_NORMAL, None)
    if handle is None or handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    # The CRT descriptor takes ownership of the handle: closing the file
    # object closes it.
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    with open(fd, "rb") as f:
        return f.read()
