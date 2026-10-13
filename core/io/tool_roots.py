"""Where each tool folder lives on *this* PC: ``data/tool_roots.json``.

The other half of `tool_layout`. That file says where a tool keeps its
datalogs, runcards and optical data *relative to the tool folder*, which is
true on every PC and so is committed. Where the tool folder itself sits is
not: it is a OneDrive path with the operator's Windows user name in it —
``C:/Users/<name>/OneDrive - Nexstrom/.../HA1P01`` — so it is recorded here,
per installation, and gitignored. Same split as ``material_prefixes.json``
and its ``.local`` overlay.

Shape::

    {"schema_version": 1,
     "roots": {"HA1P01": "C:/.../HA1P01", "VB1P01": "C:/.../VB1P01"},
     "last_tool": "HA1P01"}

``last_tool`` is the tool the QC Report and OneNote pages open on. The
Datalog and Runcard pages need no such key: each already remembers its last
folder, and the tool whose folder that is can be read back from it.

Malformed or missing reads return empty rather than raising, as the other
per-installation stores do: a lost preference must never stop a page opening.
"""

import json
from pathlib import Path
from typing import Optional, Union

from ..paths import DATA_DIR

SCHEMA_VERSION = 1


def get_tool_roots_path() -> Path:
    """Absolute path to this installation's tool_roots.json."""
    return DATA_DIR / "tool_roots.json"


def _read(path: Optional[Union[str, Path]]) -> dict:
    path = Path(path) if path is not None else get_tool_roots_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return {}
    return data


def _write(data: dict, path: Optional[Union[str, Path]]) -> None:
    path = Path(path) if path is not None else get_tool_roots_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "schema_version": SCHEMA_VERSION,
        "roots": dict(sorted((data.get("roots") or {}).items())),
    }
    if data.get("last_tool"):
        out["last_tool"] = data["last_tool"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")


def load_roots(path: Optional[Union[str, Path]] = None) -> dict[str, str]:
    """Tool name -> absolute root folder. Existence is not checked here."""
    roots = _read(path).get("roots")
    if not isinstance(roots, dict):
        return {}
    return {k: v for k, v in roots.items() if isinstance(k, str) and isinstance(v, str) and v}


def set_root(tool: str, folder: Optional[str], path: Optional[Union[str, Path]] = None) -> None:
    """Record `folder` as `tool`'s root, or forget the tool with None."""
    data = _read(path)
    roots = load_roots(path)
    if folder:
        roots[tool] = folder
    else:
        roots.pop(tool, None)
    data["roots"] = roots
    _write(data, path)


def load_last_tool(path: Optional[Union[str, Path]] = None) -> Optional[str]:
    tool = _read(path).get("last_tool")
    return tool if isinstance(tool, str) and tool else None


def save_last_tool(tool: str, path: Optional[Union[str, Path]] = None) -> None:
    data = _read(path)
    data["roots"] = load_roots(path)
    data["last_tool"] = tool
    _write(data, path)
