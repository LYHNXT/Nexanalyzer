"""
Filename-pattern discovery of a sample folder's Raman/PL spectra and OM
images for the QC Report feature.

Convention: ``<prefix>[-_]<N>.<ext>``, where ``<N>`` is the grid point
index. Raman prefix is "Raman" or "RM" (matching parser.detect_mode_from_
filename's existing RM* convention); PL prefix is "PL". Both are matched
case-insensitively with either "-" or "_" before the point number (e.g.
``RM_1.txt``, ``RM-8.txt``, ``rm-9.txt`` all resolve to Raman points 1, 8,
9). OM images use any other prefix, e.g. ``100x_3.bmp``. Any file that
doesn't match this shape (old exports, project files, PL files with no
recognizable point suffix, etc.) is ignored rather than guessed at.
"""

import os
import re
from pathlib import Path
from typing import Optional

from core.report.models import SampleScan
from .parser import RAMAN_PREFIXES, PL_PREFIXES

_NAME_RE = re.compile(r"^(?P<prefix>.+)[-_](?P<point>\d+)$")
#: A map export names its technique with a "MAPPING" infix: RM_MAPPING_3.txt,
#: PL_MAPPING_3.txt (v5.23.0). Stripped before the prefix is matched, so those
#: count as Raman / PL point 3 instead of being ignored.
_MAPPING_RE = re.compile(r"[-_]MAPPING$", re.IGNORECASE)
_IMAGE_EXTENSIONS = {".bmp", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
#: A wafer ID inside a folder name: tool-series letters then digits (HADH82,
#: VABD42, VM2610012), dropped from the middle of a folder name only.
_WAFER_ID_RE = re.compile(r"^[A-Z]{2,4}\d{2,8}$")


def sample_title(folder_name: str) -> str:
    """The report title for a sample folder: its name without the wafer ID (v5.32.0).

    Customer folders are named ``<sample ID>_<wafer ID>_<layer or material>``,
    e.g. ``KRX261001_HADH82_2L`` or ``KRX261005_VM2610012_MoS2``; the report
    is titled ``KRX261001_2L`` / ``KRX261005_MoS2``. Only a *middle* token that
    looks like a wafer ID is dropped -- never the first or the last, so
    ``QC_KRX261001`` keeps its sample ID; a name with none is returned as is.
    """
    tokens = folder_name.split("_")
    if len(tokens) < 3:
        return folder_name
    middle = [token for token in tokens[1:-1] if not _WAFER_ID_RE.match(token)]
    return "_".join([tokens[0], *middle, tokens[-1]])


def scan_sample_folder(folder: str) -> SampleScan:
    """
    Scan `folder` for Raman/PL spectra and OM images by filename pattern.

    Raises
    ------
    FileNotFoundError
        If `folder` does not exist or is not a directory.
    """
    folder_path = Path(folder)
    if not folder_path.is_dir():
        raise FileNotFoundError(f"Sample folder not found: {folder}")

    scan = SampleScan(folder=str(folder_path.resolve()), sample_name=sample_title(folder_path.name))

    for entry in sorted(os.listdir(folder_path)):
        entry_path = folder_path / entry
        if not entry_path.is_file():
            continue

        stem, ext = os.path.splitext(entry)
        match = _NAME_RE.match(stem)
        if not match:
            scan.ignored_files.append(entry)
            continue

        prefix = match.group("prefix")
        if ext.lower() == ".txt":
            prefix = _MAPPING_RE.sub("", prefix)
        point = int(match.group("point"))
        full_path = str(entry_path.resolve())

        if prefix.upper() in RAMAN_PREFIXES and ext.lower() == ".txt":
            scan.raman_files[point] = full_path
        elif prefix.upper() in PL_PREFIXES and ext.lower() == ".txt":
            scan.pl_files[point] = full_path
        elif ext.lower() in _IMAGE_EXTENSIONS:
            scan.image_files.setdefault(prefix, {})[point] = full_path
        else:
            scan.ignored_files.append(entry)

    return scan


def default_magnification(scan: SampleScan) -> Optional[str]:
    """"100x" if present, else the first magnification alphabetically, else None."""
    mags = scan.magnifications()
    if not mags:
        return None
    for candidate in mags:
        if candidate.lower() == "100x":
            return candidate
    return mags[0]
