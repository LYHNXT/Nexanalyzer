"""
Tools: where each deposition tool's folders are, recorded once.

Every page that reads a tool's files — Datalog, Runcard, QC Report, OneNote —
offers a **Tool** dropdown, filled from two files:

- ``data/tool_layouts.json`` (committed): which subfolder of a tool holds its
  datalogs, runcards and optical data, e.g. HA1P01's ``RUNCARD/lcy/NSHA1P01``.
  The same on every PC, so it is shared through the repo.
- ``data/tool_roots.json`` (gitignored): where the tool folder itself is on
  this PC. A OneDrive path carries the Windows user name, so it cannot be.

A tool is offered once it has both. This page sets the root, shows whether
each subfolder is really there, and edits or adds layouts.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from core.io.folder_picker import prompt_folder_path
from core.io.tool_layout import (
    DATALOG,
    OPTICAL,
    RUNCARD,
    get_layouts_path,
    load_layouts,
    save_layouts,
)
from core.io.tool_roots import get_tool_roots_path, load_roots, set_root

_ROLE_LABELS = ((DATALOG, "Datalog"), (RUNCARD, "Runcard"), (OPTICAL, "Optical"))

st.title("🧰 Tools")
st.markdown(
    "Record each tool's folder once; the Datalog, Runcard, QC Report and OneNote "
    "pages then offer it in their **Tool** dropdown."
)

layouts = load_layouts()
roots = load_roots()

if not layouts:
    st.warning(f"No tools declared. Add one below; it is written to `{get_layouts_path().name}`.")


def _status_rows(root, spec: dict) -> pd.DataFrame:
    rows = []
    for role, label in _ROLE_LABELS:
        sub = (spec.get(role) or {}).get("subfolder") or ""
        full = Path(root) / sub if (root and sub) else None
        rows.append({
            "Folder": label,
            "Subfolder": sub or "—",
            "Path": str(full) if full else "—",
            "Found": ("✅" if full.is_dir() else "❌") if full else "—",
        })
    return pd.DataFrame(rows)


for name in sorted(layouts):
    spec = layouts[name]
    root = roots.get(name)
    with st.container(border=True):
        col_name, col_set, col_clear = st.columns([3, 1, 1])
        with col_name:
            st.markdown(f"### {name}")
            if root:
                found = Path(root).is_dir()
                st.caption(("" if found else "⚠️ not found on this PC — ") + root)
            else:
                st.caption("Folder not set on this PC.")
        with col_set:
            if st.button("Set folder...", key=f"tools_set_{name}", width="stretch"):
                try:
                    picked = prompt_folder_path(default_dir=root or "", title=f"Select the {name} folder")
                except Exception as e:
                    st.error(f"Failed to open folder browser: {e}")
                    picked = None
                if picked:
                    try:
                        set_root(name, picked)
                    except OSError as e:
                        st.error(f"Could not save: {e}")
                    else:
                        st.rerun()
        with col_clear:
            if st.button("Clear", key=f"tools_clear_{name}", width="stretch", disabled=not root):
                set_root(name, None)
                st.rerun()

        if root and Path(root).name != name:
            st.warning(
                f"This folder is named `{Path(root).name}`, not `{name}`. "
                "That is allowed, but check it is the right tool."
            )

        st.dataframe(_status_rows(root, spec), hide_index=True, width="stretch")

        with st.expander("Edit subfolders"):
            st.caption(
                "Relative to the tool folder. Saved to the committed "
                f"`{get_layouts_path().name}`, so commit the change to share it. "
                "The OneNote page's wafer search uses the `patterns` in that file, "
                "which are edited there."
            )
            with st.form(f"tools_edit_{name}"):
                values = {
                    role: st.text_input(label, value=(spec.get(role) or {}).get("subfolder") or "",
                                        key=f"tools_edit_{name}_{role}")
                    for role, label in _ROLE_LABELS
                }
                if st.form_submit_button("Save"):
                    for role, value in values.items():
                        block = dict(spec.get(role) or {})
                        value = value.strip().strip("/\\").replace("\\", "/")
                        if value:
                            block["subfolder"] = value
                        else:
                            block.pop("subfolder", None)
                        spec[role] = block
                    layouts[name] = spec
                    save_layouts(layouts)
                    st.rerun()

st.subheader("Add a tool")
with st.form("tools_add", clear_on_submit=True):
    new_name = st.text_input("Tool name", placeholder="e.g. DD1P01 (the tool folder's name)")
    new_values = {
        role: st.text_input(f"{label} subfolder", placeholder=default, key=f"tools_add_{role}")
        for (role, label), default in zip(_ROLE_LABELS, ("DATALOG", "RUNCARD", "OPTICAL"))
    }
    if st.form_submit_button("Add"):
        new_name = new_name.strip()
        if not new_name:
            st.error("Give the tool a name.")
        elif new_name in layouts:
            st.error(f"{new_name} already exists; edit it above.")
        else:
            layouts[new_name] = {
                role: {"subfolder": value.strip().strip("/\\").replace("\\", "/")}
                for role, value in new_values.items()
                if value.strip()
            }
            save_layouts(layouts)
            st.rerun()

st.caption(
    f"Tool folders on this PC: `{get_tool_roots_path()}` · "
    f"Layouts (shared): `{get_layouts_path()}`"
)
