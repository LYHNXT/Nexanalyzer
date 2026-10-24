"""One OpenBLAS kernel on every machine, so a fit reproduces across CPUs.

numpy and scipy each bundle OpenBLAS, which picks its kernel for the CPU it
finds. The kernels round differently, and an ill-conditioned fit settles
somewhere else: the then-golden HKU RM_1 (8 peaks, R2 0.896; retired from the
record in v5.34.0) moved its chi-squared from
28637.11 to 28637.44 under Haswell and to 28641.97 on a GitHub runner, against
a 1e-5 record taken on a SkylakeX laptop. Physically meaningless, but it fails
the tripwire and lets two PCs disagree on the same spectrum.

OpenBLAS reads ``OPENBLAS_CORETYPE`` once, when its DLL loads, so ``pin()`` must
run before the first ``import numpy``. ``core/__init__.py`` calls it, which
covers any entry point that imports ``core`` or ``modules`` first; under
``streamlit run`` numpy is already loaded by Streamlit, so the launchers set
the same variables. ``active_kernels()`` reads back what actually loaded.

Haswell (AVX2) is the newest kernel every supported x86 CPU runs. A CPU
without AVX2 would fault on it, so there the pin is skipped and the machine
keeps its own kernel. One thread as well: a threaded reduction is free to sum
in a different order.
"""

from __future__ import annotations

import ctypes
import glob
import os
import sys

CORETYPE = "Haswell"
THREADS = "1"

# winnt.h PF_AVX2_INSTRUCTIONS_AVAILABLE
_PF_AVX2 = 40


def cpu_has_avx2() -> bool:
    if sys.platform == "win32":
        try:
            return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(_PF_AVX2))
        except (AttributeError, OSError):
            return False
    try:
        with open("/proc/cpuinfo", encoding="ascii", errors="ignore") as f:
            return " avx2" in f.read()
    except OSError:
        return False


def pin() -> bool:
    """Select the kernel for OpenBLAS libraries not loaded yet; True if pinned."""
    if not cpu_has_avx2():
        return False
    os.environ["OPENBLAS_CORETYPE"] = CORETYPE
    os.environ["OPENBLAS_NUM_THREADS"] = THREADS
    return True


def active_kernels() -> dict[str, str]:
    """The kernel each bundled OpenBLAS is running, keyed by its DLL name.

    Imports numpy and scipy, so call it only after ``pin()`` has had its chance.
    """
    import numpy
    import scipy.linalg  # noqa: F401  loads scipy's own OpenBLAS

    found: dict[str, str] = {}
    for pkg in (numpy, sys.modules["scipy"]):
        libs = os.path.join(os.path.dirname(pkg.__file__), os.pardir, f"{pkg.__name__}.libs")
        for path in glob.glob(os.path.join(libs, "*openblas*")):
            try:
                lib = ctypes.CDLL(path)
            except OSError:
                continue
            for name in ("scipy_openblas_get_corename64_", "scipy_openblas_get_corename"):
                fn = getattr(lib, name, None)
                if fn is not None:
                    fn.restype = ctypes.c_char_p
                    found[os.path.basename(path)] = fn().decode()
                    break
    return found
