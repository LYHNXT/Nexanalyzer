"""
The Split Table page's **Refresh now**: bring a tool's split table up to date
with what has landed in its folders since the last refresh.

One press, in this order, each step reading only what changed:

1. **QC summary** (``OPTICAL/<tool>_QC_summary.xlsx``) -- new or regenerated
   QC Reports read into it (`qc_summary`); the frozen legacy values copied in
   the first time.
2. **Run list** (``SPLITTABLE/datalog_runs.csv``) -- new logs read, new runs
   named by tag or matched to a runcard (`datalog_runs`).
3. **Rows** -- every run's runcard, Milestone and QC columns (`vb_build`).
4. **Write** -- merged over the workbook, hand edits kept, old file backed up
   (`table_writer`).

Only tools with a profile here get the button; HA1P01's table is still built
by its own scripts (user, 2026-11-08: "stay as it is until I ask").
"""

import io
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from core.io.precursor_rules import load_precursor_rules
from core.io.shared_read import read_bytes_shared
from core.io.tool_layout import DATALOG, OPTICAL, ToolLayout
from core.version import __version__

from ..processing import datalog_runs, vb_build
from ..processing.qc_select import select_qc
from . import qc_summary, table_writer


@dataclass(frozen=True)
class RefreshProfile:
    """What one tool's refresh needs beyond its layout entry."""

    builder: str
    qc_summary: str                     # relative to the optical folder
    milestone: str                      # relative to the tool folder
    runcard: str                        # all recipes, relative to the tool folder
    runs_csv: str
    w_source_csv: str
    cache: str
    legacy: Tuple[dict, ...] = ()       # paths relative to the optical folder
    card_renames: Tuple[Tuple[str, str], ...] = ()
    superseded_cards: frozenset = frozenset()
    force_unmatched: frozenset = frozenset()


#: Name decisions the matcher cannot make (match_datalogs.py, user 2026-10-19):
#: VBBG00..04 are VBCA00..04 before the rename; 2026_10/12173541.csv matches no card.
VB1P01 = RefreshProfile(
    builder="vb",
    qc_summary="VB_QC_summary.xlsx",
    milestone="../HA1P01/SPLITTABLE/Milestone and Schedule.xlsx",
    runcard="RUNCARD",
    runs_csv="SPLITTABLE/datalog_runs.csv",
    w_source_csv="SPLITTABLE/wafer_w_source.csv",
    cache="SPLITTABLE/_cache/refresh_cache.pkl",
    legacy=(
        {"path": "OM_Raman_PL_summary.xlsx", "sheet": "1_WSe2_-5-500_all"},
        {"path": "VB_Raman_PL_summary_WSe2_QC.xlsx", "sheet": "3_WSe2_QC",
         "series": ("VBCA",), "om_csv": "OM_WSe2_QC.csv"},
    ),
    card_renames=tuple((f"VBBG0{i}", f"VBCA0{i}") for i in range(5)),
    superseded_cards=frozenset(f"VBBG0{i}" for i in range(5)),
    force_unmatched=frozenset({"2026_10/12173541.csv"}),
)
PROFILES: Dict[str, RefreshProfile] = {"VB1P01": VB1P01}


def profile_for(tool: ToolLayout) -> Optional[RefreshProfile]:
    return PROFILES.get(tool.name)


@dataclass
class RefreshResult:
    """What the press did, for the page to say."""

    table: Path
    rows: int = 0
    qc_added: List[str] = field(default_factory=list)
    qc_updated: List[str] = field(default_factory=list)
    qc_kept_preset: List[str] = field(default_factory=list)
    qc_unreadable: List[str] = field(default_factory=list)
    logs_read: int = 0
    new_runs: List[str] = field(default_factory=list)
    renamed_runs: List[str] = field(default_factory=list)
    matched_runs: List[str] = field(default_factory=list)
    rows_added: List[str] = field(default_factory=list)
    rows_removed: List[str] = field(default_factory=list)
    hand_edits: List[str] = field(default_factory=list)
    first_refresh_changes: int = 0
    unchanged: bool = False             # nothing new: the workbook was not rewritten
    backup: Optional[Path] = None
    seconds: float = 0.0


def refresh_split_table(
    tool: ToolLayout,
    out_dir: Optional[Path] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> RefreshResult:
    """Run the four steps; raise `table_writer.TableBusy` before touching anything busy.

    `out_dir` set is a dry run: every file is written there instead (the table
    copied in first, so the merge sees the real one), and nothing in the tool
    folder changes.
    """
    started = time.time()
    profile = profile_for(tool)
    if profile is None:
        raise ValueError(f"{tool.name} has no split-table refresh.")
    say = progress or (lambda _m: None)
    table_path = tool.splittable_file
    optical = tool.subfolder(OPTICAL)
    datalog = tool.subfolder(DATALOG)
    runcard = tool.root / profile.runcard
    milestone_path = (tool.root / profile.milestone).resolve()
    if not milestone_path.is_file():
        raise FileNotFoundError(f"Milestone sheet not found at {milestone_path}.")

    def target(path: Path) -> Path:
        return path if out_dir is None else Path(out_dir) / path.name

    dest = target(table_path)
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        if table_path.exists() and not dest.exists():
            dest.write_bytes(read_bytes_shared(table_path))
    table_writer.check_writable(dest)
    result = RefreshResult(table=dest)

    with table_writer.refresh_lock(dest):
        when = datetime.now()
        cache_path = tool.root / profile.cache
        cache = datalog_runs.load_cache(target(cache_path) if out_dir else cache_path)
        if not cache and out_dir is not None:
            cache = datalog_runs.load_cache(cache_path)
        log_cache = cache.setdefault("logs", {})
        card_cache = cache.setdefault("cards", {})

        # 1. QC summary
        say("Collecting QC Reports")
        summary_path = optical / profile.qc_summary
        old = qc_summary.read_qc_summary(target(summary_path) if out_dir and target(
            summary_path).exists() else summary_path)
        qc = qc_summary.collect_reports(optical, old.reports if old else None)
        legacy = old.legacy if old is not None and not old.legacy.empty else qc_summary.build_legacy(
            [{**s, "path": optical / s["path"],
              **({"om_csv": optical / s["om_csv"]} if s.get("om_csv") else {})}
             for s in profile.legacy])
        reports = qc.summary.reports
        summary = qc_summary.QCSummary(
            reports=reports, legacy=legacy, missing=qc_summary.missing_reports(optical, reports),
            notes=[f"NexAnalyzer {__version__}, Split Table > Refresh now.",
                   "QC_Reports: one row per report file and preset; a report regenerated "
                   "with another preset adds a row and keeps the old one (Current = False).",
                   "Legacy: frozen copy of the values the split table carried before QC "
                   "Reports were collected; never rewritten.",
                   "The split table picks per wafer: a fit mode containing 'QC' (newest "
                   "report first, legacy last), else the newest report, else legacy."])
        result.qc_added, result.qc_updated = qc.added, qc.updated
        result.qc_kept_preset, result.qc_unreadable = qc.kept_preset, qc.unreadable

        # 2. Run list
        say("Checking datalogs")
        runs_path = tool.root / profile.runs_csv
        prev_runs, prev_written = None, None
        source_runs = target(runs_path) if out_dir and target(runs_path).exists() else runs_path
        if source_runs.exists():
            prev_runs = pd.read_csv(io.BytesIO(read_bytes_shared(source_runs)), encoding="utf-8-sig")
            prev_written = source_runs.stat().st_mtime
        runs = datalog_runs.update_runs(
            datalog, runcard, prev_runs, prev_written, log_cache,
            datalog_runs.read_tags(datalog), dict(profile.card_renames),
            profile.force_unmatched, progress=say)
        result.logs_read = runs.read
        result.new_runs, result.renamed_runs, result.matched_runs = (
            runs.new_runs, runs.renamed, runs.matched)

        # 3. Rows
        say("Reading Milestone and runcards")
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(read_bytes_shared(milestone_path)), read_only=True,
                             data_only=True)
        milestone = vb_build.read_milestone(book)
        built = vb_build.build_rows(vb_build.BuildInputs(
            runcard_dir=runcard, runs=runs.runs, milestone=milestone,
            qc=select_qc(reports, legacy, key=vb_build.canonical), rules=load_precursor_rules(),
            superseded_cards=profile.superseded_cards, card_cache=card_cache))

        # 4. Merge and write
        say("Writing the split table")
        visible, last_computed = table_writer.read_sheets(dest, vb_build.SHEET)
        computed = built.table
        keep = [c for c in computed.columns
                if computed[c].notna().any() or c in vb_build.ALWAYS
                or (visible is not None and c in visible.columns)]
        computed = computed[keep]
        table, changes, report = table_writer.merge(computed, visible, last_computed,
                                                    vb_build.sort_rows)
        # The write is most of a press's time (openpyxl load + save of the
        # whole book), so a press that changes no cell skips it -- and makes
        # no backup, as there is nothing to go back from.
        result.unchanged = last_computed is not None and report.unchanged
        if result.unchanged:
            say("Nothing changed -- the workbook is left as it is")
        else:
            design = _design(table)
            notes = _notes(tool, profile, milestone_path, table, built.notes, when)
            result.backup = table_writer.write_table(
                dest, vb_build.SHEET, table, computed,
                {c: g for c, g, *_ in vb_build.DESIGN},
                [("Column_Design", design), ("Notes", notes), ("Datalog_runs", runs.runs),
                 (table_writer.CHANGES_SHEET, changes if last_computed is None else None)])
        result.rows = report.rows
        result.rows_added, result.rows_removed = report.added, report.removed
        result.hand_edits = report.hand_edits
        result.first_refresh_changes = report.first_refresh_changes

        # Companions written after the table, so a failed table write leaves
        # them as they were and the next press redoes the same work.
        if old is None or old.legacy.empty or qc.changed or not _same_missing(old, summary):
            qc_summary.write_qc_summary(summary, target(summary_path), when)
        runs.runs.to_csv(target(runs_path), index=False, encoding="utf-8-sig")
        built.w_source_recipe.to_csv(target(tool.root / profile.w_source_csv), index=False,
                                     encoding="utf-8-sig")
        datalog_runs.save_cache(cache, target(cache_path) if out_dir else cache_path)

    result.seconds = time.time() - started
    return result


def _same_missing(old, new) -> bool:
    a, b = old.missing, new.missing
    if a is None or b is None:
        return a is None and b is None
    return a.astype(str).reset_index(drop=True).equals(b.astype(str).reset_index(drop=True))


def _design(table: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for i, (c, g, u, s, d) in enumerate(vb_build.DESIGN):
        populated = f"{table[c].notna().sum()}/{len(table)}" if c in table else f"0/{len(table)}"
        rows.append((i + 1, c, g, u, s, d, populated))
    return pd.DataFrame(rows, columns=["#", "Column", "Group", "Unit", "Source", "Definition",
                                       "Populated"])


def _notes(tool, profile, milestone_path, table, build_notes, when) -> pd.DataFrame:
    counts = table["Run_Type"].value_counts()
    fwhm, b2g, defect = vb_build.spec_limits()
    rows = [
        (f"{tool.name} split table - generated from runcards + datalogs, not hand-entered", ""),
        ("Generated", f"{when:%Y-%m-%d %H:%M} by NexAnalyzer {__version__} (Split Table > Refresh now)"),
        ("Rows", "{} ({})".format(len(table), ", ".join(f"{n} {k}" for k, n in counts.items()))),
        ("Runcards", str(tool.root / profile.runcard)),
        ("Datalogs", str(tool.subfolder(DATALOG))),
        ("Datalog -> runcard", f"{profile.runs_csv}: tagged runs name themselves, untagged ones "
                               "are matched (NexAnalyzer runcard_match) once and keep that name."),
        ("Process columns", "NexAnalyzer runcard model: growth window = time at the peak heater "
                            "target, ended by heater-off or ramp-down. Values at the window midpoint."),
        ("Joined metadata", f"{milestone_path}  ({', '.join(vb_build.MILESTONE_SHEETS)})"),
        ("W_source", "Milestone W column; where NA or blank, the run-ID rule (Material Presets > "
                     "Precursor by run ID); else the series majority. W_source_rule says which."),
        ("QC measured", f"{tool.subfolder(OPTICAL) / profile.qc_summary}: per wafer a fit mode "
                        "containing 'QC' first (newest report, legacy last), else the newest "
                        "QC Report, else the legacy value. QC_Source / QC_Fit_Mode / QC_Layer say which."),
        ("Spec gates", f"E2g+A1g FWHM <= {fwhm:g}; B2g centre >= {b2g:g}; LA/E2g+A1g <= {defect:g} (peak_quality)"),
        ("Hand edits", "A cell changed by hand is kept on refresh and named in Hand_Edits; the "
                       "hidden _Computed sheet holds what the refresh computed."),
        ("Backups", "SPLITTABLE/_backups/<name>.bak_<time>, one per refresh."),
        ("", ""),
    ] + [("", n) for n in build_notes]
    return pd.DataFrame(rows, columns=["Item", "Value"])
