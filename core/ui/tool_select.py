"""The "Tool" dropdown the folder-reading pages share.

Lists every tool set up on the Tools page — a layout in
``data/tool_layouts.json`` *and* a root in ``data/tool_roots.json`` — and hands
back the one the operator switched to. What the page then does with it (point
a loader at the datalog subfolder, list the tool's wafers) stays on the page.

The shown value is derived from the folder the page already holds, not stored:
a page that remembers ``.../HA1P01/DATALOG`` is on HA1P01, and one whose folder
was browsed to by hand is on no tool. Storing the tool as well would give the
page two answers to "where am I" that could disagree.
"""

from pathlib import Path
from typing import Optional, Union

import streamlit as st

from core.io.tool_layout import ToolLayout, configured_tools, tool_owning

#: Shown when the page's folder belongs to no set-up tool.
NO_TOOL = "—"


def render_tool_select(
    role: Optional[str],
    current_folder: Optional[Union[str, Path]],
    key: str,
    fallback: Optional[str] = None,
) -> tuple[Optional[ToolLayout], bool]:
    """Draw the dropdown; return (selected tool or None, whether it just changed).

    `fallback` names the tool to show when the page has no folder yet.
    """
    tools = configured_tools()
    if not tools:
        st.caption("No tool folders set up yet. Add them on the **Tools** page "
                   "to pick a tool here instead of browsing.")
        return None, False

    by_name = {t.name: t for t in tools}
    current = tool_owning(current_folder, role, tools)
    if current is None and not current_folder and fallback in by_name:
        current = by_name[fallback]

    options = [t.name for t in tools]
    if current is None:
        options = [NO_TOOL] + options
    shown = current.name if current else NO_TOOL

    # The key carries the shown value: when the folder changes underneath the
    # widget (a Browse..., the OneNote page's hand-off), a fresh widget opens on
    # the new tool instead of keeping the stale pick.
    choice = st.selectbox(
        "Tool",
        options,
        index=options.index(shown),
        key=f"{key}__{shown}",
        help="Tool folders are set up on the Tools page.",
    )
    tool = by_name.get(choice)
    changed = tool is not None and choice != shown
    return tool, changed
