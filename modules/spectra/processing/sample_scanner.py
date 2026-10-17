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

    scan = SampleScan(folder=str(folder_path.resolve()), sample_name=folder_path.name)

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
