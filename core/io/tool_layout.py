"""Where a tool folder keeps each artifact, and how to find one wafer's.

Point the app at a tool folder — ``...\\Data Collection\\HA1P01`` — and this
resolves a wafer ID to its optical folder, its runcard and its datalog, which
live in three unrelated subfolders.

**The layout is declared, not inferred, because there is no convention to
infer.** A survey of all twelve tool folders on the share found the runcard
folder spelled four ways (``RUNCARD``, ``Runcard``, ``Run Card``, absent), the
optical folder six (``OPTICALS``, ``Optical Properties``, ``Raman&PL``,
``RAMAN&PL``, ``RAMAN``, ``Characterization``), a depth to a wafer of anywhere
from one to four levels, and an intermediate tier that is a month in one tool,
a wafer-prefix in another and a technique in a third. Only two of the twelve
have a datalog at all. Worse, the older tools key their files on a run serial
(``NSHA1P012512001``) rather than a wafer ID, so a wafer-ID lookup can never
find anything in them.

A loader that guessed would therefore return an empty result for most tools
while looking like it had searched. ``data/tool_layouts.json`` holds one entry
per tool instead, and an unknown tool raises rather than resolving to nothing:
a QC tool that silently finds no data is worse than one that says it does not
know where to look.

**It ships with HA1P01 alone.** Adding a tool is an entry in that file, not a
change here.

**Two things no rule can fix**, and callers must render them as *not linked*
rather than *missing* — the file exists, it just cannot be addressed by name:

- 219 of HA1P01's 378 wafers have no findable datalog. 1186 of its 1404
  datalog CSVs carry no ``~<WAFER>`` at all; the logger never wrote the ID.
  ``runcard_tags.json`` recovers 20 more wafers and no more.
- 19 wafers have optical data and no runcard under any glob.

The optical side needs two patterns because the naming changed mid-2026:
``OPTICALS/202609/HADH51/`` is a folder per wafer, while the twelve earlier
months are flat ``.wip`` files. Five months before that key on a run serial and
are unreachable by wafer ID at all — 130 entries that are archive, not a bug.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ..paths import DATA_DIR
from .tool_roots import load_roots

#: The three artifact roles a tool folder holds. Used as dict keys in the
#: layout file and as the labels callers show.
OPTICAL = "optical"
RUNCARD = "runcard"
DATALOG = "datalog"
ROLES = (OPTICAL, RUNCARD, DATALOG)


class UnknownToolError(LookupError):
    """Raised for a tool folder with no entry in the layout table.

    Deliberately an exception rather than an empty result: a tool whose
    layout nobody has written down must say so, not read as a tool with no
    data in it.
    """


def get_layouts_path() -> Path:
    """Absolute path to the committed tool-layout table."""
    return DATA_DIR / "tool_layouts.json"


def load_layouts(path: Optional[Union[str, Path]] = None) -> dict:
    """Read the layout table, or {} if it is absent or malformed.

    Never raises on a bad file: a hand-edit with a trailing comma becomes
    "no tool is known", which `resolve_tool` already reports clearly, rather
    than an import-time crash on a page the operator cannot get past.
    """
    path = Path(path) if path is not None else get_layouts_path()
    if not path.exists():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    layouts = data.get("layouts") if isinstance(data, dict) else None
    return layouts if isinstance(layouts, dict) else {}


def save_layouts(layouts: dict, path: Optional[Union[str, Path]] = None) -> None:
    """Write the layout table back, tools sorted by name.

    Only the Tools page calls this, to add a tool or change a subfolder. The
    file is committed, so what it writes is a change for review like any other.
    """
    path = Path(path) if path is not None else get_layouts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"schema_version": 1, "layouts": dict(sorted(layouts.items()))},
            f, indent=2, ensure_ascii=False,
        )
        f.write("\n")


def tool_name(folder: Union[str, Path]) -> str:
    """The tool folder's own name, which is the key into the table."""
    return Path(folder).name


@dataclass(frozen=True)
class ToolLayout:
    """One tool folder's declared layout."""

    name: str
    root: Path
    spec: dict

    def subfolder(self, role: str) -> Optional[Path]:
        """The folder a page's existing loader should be pointed at.

        This is the whole reason the runcard and datalog loaders need no
        change: both already recurse and both already gate on content — a
        recipe's clock advances where a datalog's does not, and a datalog's
        header names `Time` and `Program`. They only ever needed a better
        starting folder.

        Pointing them at the tool root instead would be actively harmful, not
        merely slow: the Datalog page's bulk rename walks from its root, so a
        root of `HA1P01` turns it into a rename of every CSV under the tool;
        and `runcard_tags.json` is keyed by path relative to that root, so
        moving the root orphans all 231 existing tags.
        """
        declared = (self.spec.get(role) or {}).get("subfolder")
        if not declared:
            return None
        return self.root / declared

    def patterns(self, role: str) -> tuple[str, ...]:
        return tuple((self.spec.get(role) or {}).get("patterns") or ())

    @property
    def excluded(self) -> tuple[str, ...]:
        """Top-level folders the wafer search must not descend into.

        `BACKUP/` holds a full parallel `OM/<YYYYMM>/<WAFER>/` tree, so every
        wafer in it appears twice. Excluded by choice: a picker that offered
        the same wafer under two paths would make the operator pick between
        two things they cannot tell apart.
        """
        return tuple(self.spec.get("exclude") or ())


def resolve_tool(
    folder: Union[str, Path],
    layouts: Optional[dict] = None,
) -> ToolLayout:
    """The layout for a picked folder, or for the tool folder above it.

    Walks up from the pick, because the folder dialog reopens wherever it was
    last used and the tool folder is four levels above a wafer: picking
    ``OPTICALS/202609/HADH96`` is the easy slip, and it means the operator is
    standing *inside* HA1P01 while being told HA1P01 is unknown. Anything at
    or under a declared tool resolves to that tool.

    Raises `UnknownToolError` only when no ancestor is a known tool — a
    genuinely undeclared tool — naming the folder and the file to add it to,
    because the fix is a one-line edit and the operator holds the information.
    """
    layouts = load_layouts() if layouts is None else layouts
    picked = Path(folder)

    for candidate in (picked, *picked.parents):
        spec = layouts.get(candidate.name)
        if spec is not None:
            return ToolLayout(name=candidate.name, root=candidate, spec=spec)

    known = ", ".join(sorted(layouts)) or "none"
    raise UnknownToolError(
        f"No layout for {picked.name!r} or any folder above it. "
        f"Known tools: {known}. Pick a tool folder, or add an entry to "
        f"{get_layouts_path().name}."
    )


@dataclass(frozen=True)
class WaferPaths:
    """What was found on disk for one wafer, per artifact role.

    A role with no path is *not linked*, never *missing* — for the datalog
    that distinction is the difference between a file nobody wrote and a file
    whose name simply never carried the wafer ID. 58% of HA1P01's wafers are
    in the second case, and telling an operator their log is missing would
    send them looking for something that is sitting in the folder.
    """

    wafer_id: str
    optical: Optional[Path] = None
    runcard: Optional[Path] = None
    datalog: Optional[Path] = None

    def path_for(self, role: str) -> Optional[Path]:
        return getattr(self, role, None)

    def linked(self, role: str) -> bool:
        return self.path_for(role) is not None

    @property
    def unlinked_roles(self) -> tuple[str, ...]:
        return tuple(role for role in ROLES if not self.linked(role))


def _mtime_or_zero(path: Path) -> float:
    """A file OneDrive removed or locked mid-scan sorts oldest, not raises."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def find_wafer(
    layout: ToolLayout,
    wafer_id: str,
    tags: Optional[dict] = None,
) -> WaferPaths:
    """Locate one wafer's three artifacts under a tool folder.

    Globs rather than computing paths: the month tier is not contiguous —
    202511 and 202611 do not exist — so any attempt to derive a month from a
    wafer ID would miss. Wafer IDs are unique across months, so globbing the
    tier is safe.
    """
    wafer = (wafer_id or "").strip()
    if not wafer:
        return WaferPaths(wafer_id="")

    found: dict[str, Optional[Path]] = {role: None for role in ROLES}

    for role in ROLES:
        for pattern in layout.patterns(role):
            matches = sorted(layout.root.glob(pattern.format(wafer=wafer)))
            matches = [m for m in matches if not _is_excluded(m, layout)]
            if matches:
                # Newest wins. Six wafers have a runcard in two adjacent
                # months; the later file is the one that ran.
                found[role] = max(matches, key=_mtime_or_zero)
                break

    if found[DATALOG] is None:
        found[DATALOG] = _datalog_from_tags(layout, wafer, tags)

    return WaferPaths(
        wafer_id=wafer,
        optical=found[OPTICAL],
        runcard=found[RUNCARD],
        datalog=found[DATALOG],
    )


def _is_excluded(path: Path, layout: ToolLayout) -> bool:
    try:
        parts = path.relative_to(layout.root).parts
    except ValueError:
        return False
    return bool(parts) and parts[0] in layout.excluded


def load_tags(layout: ToolLayout) -> dict:
    """`runcard_tags.json`, inverted to wafer -> relative path.

    Most of its 231 keys merely restate the `~<WAFER>` already in the
    filename, so it is not the index it first appears to be — but 24 of them
    point at files carrying no tilde at all, and those are the only route to
    20 wafers' logs. Worth reading, not worth relying on.
    """
    declared = (layout.spec.get(DATALOG) or {}).get("tag_index")
    if not declared:
        return {}

    path = layout.root / declared
    if not path.exists():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(raw, dict):
        return {}

    inverted: dict[str, str] = {}
    for rel_path, tag in raw.items():
        if isinstance(tag, str) and isinstance(rel_path, str):
            inverted.setdefault(tag.strip().upper(), rel_path)
    return inverted


def _datalog_from_tags(
    layout: ToolLayout,
    wafer: str,
    tags: Optional[dict],
) -> Optional[Path]:
    """The tag index's answer, when the filename did not carry the wafer."""
    tags = load_tags(layout) if tags is None else tags
    rel = tags.get(wafer.upper())
    if not rel:
        return None

    subfolder = layout.subfolder(DATALOG)
    if subfolder is None:
        return None

    candidate = subfolder / rel
    return candidate if candidate.exists() else None


def list_wafers(layout: ToolLayout) -> list[str]:
    """Every wafer with optical data under this tool, newest first.

    Drives the picker. Reads the optical tree alone: it is the one whose
    entries are named for wafers by construction, where the runcard tree is
    keyed by operator and the datalog tree mostly by timestamp.
    """
    wafers: dict[str, float] = {}

    for pattern in layout.patterns(OPTICAL):
        # `{wafer}*` with a `*` wafer collapses to `**`, which pathlib rejects
        # as a path component. Listing every wafer is the one case where the
        # placeholder is itself a wildcard, so the duplicate is dropped here
        # rather than by writing a second set of patterns into the table.
        glob_pattern = pattern.format(wafer="*").replace("**.", "*.")
        for match in layout.root.glob(glob_pattern):
            if _is_excluded(match, layout):
                continue
            name = match.stem if match.is_file() else match.name
            try:
                mtime = match.stat().st_mtime
            except OSError:
                continue
            if name not in wafers or mtime > wafers[name]:
                wafers[name] = mtime

    return sorted(wafers, key=lambda name: (-wafers[name], name))


def configured_tools(
    layouts: Optional[dict] = None,
    roots: Optional[dict] = None,
) -> list[ToolLayout]:
    """Every declared tool whose root this PC has recorded, by name.

    A tool needs both halves: the committed layout (which subfolder is which)
    and the per-PC root from ``tool_roots.json`` (where the tool folder is).
    One without the other is not offered — a root with no layout cannot say
    where its datalogs are, and a layout with no root has nowhere to look.
    """
    layouts = load_layouts() if layouts is None else layouts
    roots = load_roots() if roots is None else roots
    return [
        ToolLayout(name=name, root=Path(roots[name]), spec=layouts[name])
        for name in sorted(layouts)
        if roots.get(name)
    ]


def tool_owning(
    folder: Optional[Union[str, Path]],
    role: Optional[str],
    tools: list[ToolLayout],
) -> Optional[ToolLayout]:
    """The tool whose `role` subfolder is `folder` or contains it, if any.

    How a page that remembers only a folder knows which tool it is on, so the
    selector opens showing it without storing the tool a second time. A `role`
    of None matches against the tool folder itself.
    """
    if not folder:
        return None
    picked = Path(folder)
    for tool in tools:
        sub = tool.root if role is None else tool.subfolder(role)
        if sub is not None and (picked == sub or sub in picked.parents):
            return tool
    return None
