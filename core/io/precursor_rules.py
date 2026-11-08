"""
Which W precursor a run used, read off its run ID: the run-ID rules.

Shared and committed (data/precursor_rules.json), like om_calibration.json:
the naming of a tool's runs is a fact about the lab, not a personal setting,
and every PC filling a split table must fill the same precursor.

Grouped by the run ID's **first two letters** -- the system (VA, VB, ...),
because the same series letters can mean different precursors on different
tools. Inside a group the **longest matching prefix wins**, so an exception
(``VAHA`` -> W(CO)6) overrides a broader rule (``VA...``) without editing it,
and the VA1P01 serials split by year (``VA1P0125`` / ``VA1P0126``).

Kept apart from the material presets on purpose: a fit preset changes with
the fitting, a run's precursor never does.

A rule fills the split table's ``W_source`` only where the Milestone sheet's
``W`` column is NA or blank; a wafer no rule matches falls through to its
series' majority. The lookup here answers only "what does the rule say".
"""

import json
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from ..paths import DATA_DIR

SCHEMA_VERSION = 1

#: Group -> {prefix: precursor}. The committed file starts from these (user,
#: 2026-11-08); a missing or unreadable file falls back to them too, so a
#: fresh clone never fills the split table with blanks.
DEFAULT_RULES: Dict[str, Dict[str, str]] = {
    "VA": {
        "VAA": "W foil",
        "VAB": "WO3",
        "VAHA": "W(CO)6",
        "VA1P0125": "W(CO)6",
        "VA1P0126": "W foil",
    },
    "VB": {
        "VBA": "WO3",
        "VBB": "W(CO)6",
        "VBC": "W foil",
    },
}

GROUP_LENGTH = 2


def get_precursor_rules_path() -> Path:
    """Absolute path to the shared precursor_rules.json store."""
    return DATA_DIR / "precursor_rules.json"


def group_of(run_id: str) -> str:
    """The group a run ID or prefix belongs to: its first two letters, upper case."""
    return str(run_id).strip()[:GROUP_LENGTH].upper()


def load_precursor_rules(path: Optional[Union[str, Path]] = None) -> Dict[str, Dict[str, str]]:
    """
    Group -> {prefix: precursor}. A missing or malformed file gives the
    defaults (never raises -- a broken rules file must not stop a refresh).
    Prefixes are upper-cased; an entry whose prefix does not start with its
    group, or that has no precursor, is dropped rather than misfiled.
    """
    path = Path(path) if path is not None else get_precursor_rules_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {g: dict(r) for g, r in DEFAULT_RULES.items()}
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, dict):
        return {g: dict(r) for g, r in DEFAULT_RULES.items()}

    out: Dict[str, Dict[str, str]] = {}
    for group, rules in groups.items():
        group = str(group).strip().upper()
        if len(group) != GROUP_LENGTH or not isinstance(rules, dict):
            continue
        for prefix, precursor in rules.items():
            prefix = str(prefix).strip().upper()
            precursor = str(precursor).strip() if precursor is not None else ""
            if prefix.startswith(group) and precursor:
                out.setdefault(group, {})[prefix] = precursor
    return out


def validate_rules(rules: Dict[str, Dict[str, str]]) -> list:
    """Problems that would make a rules set ambiguous; empty when it is fine."""
    problems = []
    seen = {}
    for group, entries in rules.items():
        if len(group) != GROUP_LENGTH or not group.isalpha():
            problems.append(f"Group {group!r} must be {GROUP_LENGTH} letters.")
        for prefix, precursor in entries.items():
            if not prefix:
                problems.append(f"Group {group}: a prefix is blank.")
            elif not prefix.upper().startswith(group.upper()):
                problems.append(f"Prefix {prefix!r} does not start with its group {group!r}.")
            if not str(precursor).strip():
                problems.append(f"Prefix {prefix!r} has no precursor.")
            key = prefix.upper()
            if key in seen:
                problems.append(f"Prefix {prefix!r} is listed twice.")
            seen[key] = group
    return problems


def save_precursor_rules(
    rules: Dict[str, Dict[str, str]], path: Optional[Union[str, Path]] = None
) -> None:
    """Persist the whole rules set, groups and prefixes sorted."""
    problems = validate_rules(rules)
    if problems:
        raise ValueError("; ".join(problems))
    path = Path(path) if path is not None else get_precursor_rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": SCHEMA_VERSION,
        "groups": {
            group.upper(): {p.upper(): rules[group][p].strip() for p in sorted(rules[group])}
            for group in sorted(rules)
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, ensure_ascii=False)
        f.write("\n")


def precursor_for(
    run_id: str, rules: Dict[str, Dict[str, str]]
) -> Optional[Tuple[str, str]]:
    """(precursor, matching prefix) by the longest prefix in the run's group, or None."""
    key = str(run_id or "").strip().upper()
    best = None
    for prefix, precursor in rules.get(group_of(key), {}).items():
        if key.startswith(prefix) and (best is None or len(prefix) > len(best[1])):
            best = (precursor, prefix)
    return best
