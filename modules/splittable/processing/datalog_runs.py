"""
Name every datalog run after the runcard it ran: the split table's run list.

The incremental form of ``VB1P01/SPLITTABLE/match_datalogs.py``, which the
split-table refresh runs before building rows. Same rules:

* A tagged run names itself: the ``~TAG`` in its file name, else the
  ``DATALOG/runcard_tags.json`` entry.
* An untagged run is named by `runcard_match.suggest_runcards` (the Datalog
  page's matcher), with every tagged run as an anchor and every card under
  RUNCARD as a candidate.
* One run, two files (the copier re-creates a renamed file under its old
  name): runs are keyed by their first row's time; the tagged file is cited,
  and of two tagged ones the file ``runcard_tags.json`` records.

**What is incremental, and what is not.** Reading a log is the slow part --
the whole of VB1P01's 1972 took over ten minutes from OneDrive -- so a log is
read only when it is new or its modified time or size moved; what was read is
kept in a cache beside the table. A run already in the previous list keeps
its name: a later tag renames it (tags are always re-read), but a matched run
is never re-matched (an unmatched one is retried), so pressing refresh cannot shuffle names that were
already reviewed. New untagged runs are matched with every named run as
taken -- the one-shot script matched all untagged runs jointly.
"""

import pickle
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Union

import pandas as pd

from modules.datalog.io.runcard_index import read_run_steps, read_runcards
from modules.datalog.processing.runcard_match import cleaning_name, suggest_runcards

LOG_GLOB = "20[0-9][0-9]_[0-9][0-9]/*.csv"
TAG_RE = re.compile(r"~(.+)\.csv$", re.I)
COLUMNS = ["Start", "End", "Minutes", "Wait_s", "Ended", "Datalog", "Name", "How",
           "Confidence", "Cost", "Card"]


@dataclass
class _Log:
    """One log file as far as the run list needs it."""

    mtime: float
    size: int
    start: Optional[pd.Timestamp]
    end: Optional[pd.Timestamp] = None
    wait_s: float = 0.0
    ended: bool = False
    steps: object = None          # RunSteps when read, None when taken from the old list


@dataclass
class RunsRefresh:
    """The new run list, and what changed, for the page to report."""

    runs: pd.DataFrame
    read: int = 0
    new_runs: List[str] = field(default_factory=list)
    renamed: List[str] = field(default_factory=list)
    matched: List[str] = field(default_factory=list)


def load_cache(path: Union[str, Path]) -> Dict[str, _Log]:
    """The read-log cache, or {} when absent or unreadable (it is rebuilt)."""
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cache(cache: Mapping[str, _Log], path: Union[str, Path]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(dict(cache), f)
    tmp.replace(path)


def run_tag(rel: str, tags_json: Mapping[str, str]) -> Optional[str]:
    """The run's own tag: its file name's ``~TAG``, else its runcard_tags.json entry."""
    m = TAG_RE.search(rel.rsplit("/", 1)[-1])
    return m.group(1) if m else tags_json.get(rel)


def _from_steps(stat, steps) -> _Log:
    if steps is None or steps.start is None or pd.isna(steps.start):
        return _Log(stat.st_mtime, stat.st_size, None)
    return _Log(stat.st_mtime, stat.st_size, pd.Timestamp(steps.start), pd.Timestamp(steps.end),
                float(steps.total), bool(steps.ended), steps)


def _from_row(stat, row) -> _Log:
    return _Log(stat.st_mtime, stat.st_size, pd.Timestamp(row["Start"]), pd.Timestamp(row["End"]),
                float(row["Wait_s"]), bool(row["Ended"]))


def update_runs(
    datalog_dir: Union[str, Path],
    runcard_dir: Union[str, Path],
    previous: Optional[pd.DataFrame],
    previous_written: Optional[float],
    cache: Dict[str, _Log],
    tags_json: Mapping[str, str],
    card_renames: Mapping[str, str] = None,
    force_unmatched: frozenset = frozenset(),
    progress: Optional[Callable[[str], None]] = None,
) -> RunsRefresh:
    """The run list brought up to date; `cache` is updated in place.

    `previous_written` is the old list's modified time: a log cited in it and
    not modified since is taken from it without being read.
    """
    datalog_dir = Path(datalog_dir)
    card_renames = dict(card_renames or {})
    say = progress or (lambda _m: None)
    known = {} if previous is None or previous.empty else {
        str(r["Datalog"]): r for r in previous.to_dict("records")}

    files = sorted(datalog_dir.glob(LOG_GLOB))
    result = RunsRefresh(runs=pd.DataFrame(columns=COLUMNS))
    logs: Dict[str, _Log] = {}
    to_read = []
    for path in files:
        rel = path.relative_to(datalog_dir).as_posix()
        stat = path.stat()
        hit = cache.get(rel)
        if hit is not None and hit.mtime == stat.st_mtime and hit.size == stat.st_size:
            logs[rel] = hit
        elif (hit is None and rel in known and previous_written is not None
              and stat.st_mtime <= previous_written):
            logs[rel] = cache[rel] = _from_row(stat, known[rel])
        else:
            to_read.append((path, rel, stat))
    for i, (path, rel, stat) in enumerate(to_read, 1):
        if i == 1 or i % 25 == 0 or i == len(to_read):
            say(f"Reading datalogs {i}/{len(to_read)}")
        logs[rel] = cache[rel] = _from_steps(stat, read_run_steps(path))
    result.read = len(to_read)
    for rel in [r for r in cache if r not in logs]:
        del cache[rel]

    # One run per start time. The file cited: a tagged one; of two tagged, the
    # one runcard_tags.json records (VB1P01's copier re-creates a renamed log
    # under its old name, so ~VBCA02 and ~VBAC02 are the same run and only the
    # tag file says which name was decided); then a renamed one ("-" in it).
    def rank(rel, tag):
        return (tag is not None, rel in tags_json, "-" in rel.rsplit("/", 1)[-1], rel)

    runs: Dict[pd.Timestamp, tuple] = {}
    for rel, log in sorted(logs.items()):
        if log.start is None:
            continue
        tag = run_tag(rel, tags_json)
        old = runs.get(log.start)
        if old is None or rank(rel, tag) > rank(*old):
            runs[log.start] = (rel, tag)

    known_by_start = {pd.Timestamp(r["Start"]): r for r in known.values()}
    rows, todo, used, refs = {}, [], {}, []
    for start, (rel, tag) in runs.items():
        log = logs[rel]
        base = {"Start": start, "End": log.end,
                "Minutes": round((log.end - start).total_seconds() / 60, 1),
                "Wait_s": round(log.wait_s, 1), "Ended": log.ended, "Datalog": rel}
        before = known_by_start.get(start)
        if tag:
            name = cleaning_name(tag) or tag.upper()
            rows[start] = {**base, "Name": name, "How": "tag", "Confidence": "", "Cost": "", "Card": ""}
            if cleaning_name(tag) or "CLEAN" in tag.upper():
                refs.append((tag, rel))
            else:
                used[tag] = start
            if before is None:
                result.new_runs.append(f"{start:%Y-%m-%d %H:%M} {name}")
            elif str(before.get("Name")) != name:
                result.renamed.append(f"{start:%Y-%m-%d %H:%M} {before.get('Name') or '(unnamed)'} -> {name}")
        elif before is not None and before.get("How") == "matched":
            rows[start] = {**base, **{k: before.get(k) for k in ("Name", "How", "Confidence", "Cost", "Card")}}
            if isinstance(before.get("Name"), str):
                used.setdefault(before["Name"], start)
        elif rel in force_unmatched:
            rows[start] = {**base, "Name": "", "How": "no match", "Confidence": "", "Cost": "", "Card": ""}
        else:
            todo.append((start, rel))
            if before is None:
                result.new_runs.append(f"{start:%Y-%m-%d %H:%M} (untagged)")

    if todo:
        say(f"Matching {len(todo)} untagged run(s) to runcards")
        steps_of = {}
        for _start, rel in todo + [(None, r) for _t, r in refs]:
            if logs[rel].steps is None:
                path = datalog_dir / rel
                logs[rel] = cache[rel] = _from_steps(path.stat(), read_run_steps(path))
            steps_of[rel] = logs[rel].steps
        cards = read_runcards(runcard_dir)
        suggestions = suggest_runcards(
            [(str(datalog_dir / rel), steps_of[rel]) for _s, rel in todo if steps_of[rel] is not None],
            cards, used=used,
            references=[(tag, steps_of[rel]) for tag, rel in refs if steps_of[rel] is not None])
        for start, rel in todo:
            log = logs[rel]
            base = {"Start": start, "End": log.end,
                    "Minutes": round((log.end - start).total_seconds() / 60, 1),
                    "Wait_s": round(log.wait_s, 1), "Ended": log.ended, "Datalog": rel}
            s = suggestions.get(str(datalog_dir / rel))
            if s is None:
                rows[start] = {**base, "Name": "", "How": "no match", "Confidence": "", "Cost": "", "Card": ""}
                continue
            name = cleaning_name(s.tag) or s.tag.upper()
            conf = s.confidence
            if name in card_renames:
                conf += f"; card {name} = {card_renames[name]} renamed"
                name = card_renames[name]
            card = Path(s.card_path).relative_to(runcard_dir).as_posix() if Path(
                s.card_path).is_relative_to(runcard_dir) else s.card_path
            rows[start] = {**base, "Name": name, "How": "matched", "Confidence": conf,
                           "Cost": s.cost, "Card": card.replace("/", "\\")}
            result.matched.append(f"{start:%Y-%m-%d %H:%M} -> {name} ({conf})")

    result.runs = pd.DataFrame([rows[s] for s in sorted(rows)], columns=COLUMNS)
    return result


def read_tags(datalog_dir: Union[str, Path]) -> Dict[str, str]:
    """DATALOG/runcard_tags.json, keys with forward slashes; {} when absent."""
    import json

    path = Path(datalog_dir) / "runcard_tags.json"
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        return {k.replace("\\", "/"): v for k, v in json.load(f).items()}
