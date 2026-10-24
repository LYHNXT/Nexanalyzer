"""
Native file and folder dialogs, run in a subprocess.

tkinter does not coexist with Streamlit's own event loop in-process, so each
dialog runs in a fresh interpreter. This is the one copy of that round trip;
`export.prompt_save_path`, `export.prompt_save_folder`,
`folder_picker.prompt_folder_path` and the Spectra sidebar's file browser are
thin wrappers over `run_dialog`.

The request goes in on stdin and the answer comes back on stdout, both JSON,
so a title or path with quotes, backslashes or non-Latin characters
round-trips intact. Until this module, four copies pasted the title into the
script's source with `str.replace` (a `"` in it broke the dialog) and wrote
that source to a temp file in the locale encoding -- one copy without a
`finally`, so a timeout left the file behind.
"""

import json
import os
import subprocess
import sys
from typing import Any, Optional

#: The dialog program. Static: everything variable arrives as JSON on stdin.
_SCRIPT = r"""
import json, sys
import tkinter as tk
from tkinter import filedialog

# Bytes, decoded here: the child's stdin encoding is the locale's, not UTF-8.
req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
root = tk.Tk()
root.withdraw()
root.wm_attributes('-topmost', 1)
kind, options = req["kind"], req["options"]
if "filetypes" in options:
    options["filetypes"] = [tuple(ft) for ft in options["filetypes"]]
answer = getattr(filedialog, kind)(**options)
root.destroy()
# A cancel is "" for a single path and () or "" for several, normalised here.
answer = list(answer or []) if kind == "askopenfilenames" else (answer or "")
# json.dumps escapes to ASCII, so stdout's encoding cannot mangle a path.
sys.stdout.write(json.dumps(answer))
"""

#: The tkinter dialogs `run_dialog` may open.
KINDS = ("asksaveasfilename", "askdirectory", "askopenfilenames")


def run_dialog(kind: str, timeout: float = 300, **options: Any) -> Any:
    """Open `tkinter.filedialog.<kind>(**options)` in a subprocess and return its answer.

    Returns the dialog's value: a path string, a list of paths for
    `askopenfilenames`, or "" / [] when the user cancelled. `options` must be
    JSON-serialisable; `filetypes` may be given as (label, pattern) pairs.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown dialog kind {kind!r}; expected one of {KINDS}")
    request = json.dumps({"kind": kind, "options": options})
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        input=request, capture_output=True, text=True, encoding="utf-8",
        timeout=timeout,
    )
    raw = result.stdout.strip()
    if not raw:
        return [] if kind == "askopenfilenames" else ""
    return json.loads(raw)


def existing_dir(path: Optional[str]) -> str:
    """`path` if it is an existing folder, else "" (the OS default)."""
    return path if (path and os.path.isdir(path)) else ""
