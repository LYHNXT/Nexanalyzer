"""
Native folder-picker dialog for the QC Report, Datalog, Runcard, OneNote and
Tools pages. The subprocess round trip lives in `native_dialog`.
"""

from typing import Optional

from .native_dialog import existing_dir, run_dialog


def prompt_folder_path(default_dir: Optional[str] = None, title: str = "Select Sample Folder") -> Optional[str]:
    """
    Open a native OS folder-picker dialog and return the chosen path.

    Returns
    -------
    Optional[str]
        Absolute folder path chosen by the user, or None if cancelled.
    """
    path = run_dialog("askdirectory", title=title, initialdir=existing_dir(default_dir))
    return path or None
