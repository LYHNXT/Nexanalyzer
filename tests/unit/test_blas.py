"""The OpenBLAS kernel is pinned before numpy loads (core/blas.py).

The golden record holds on one kernel only. If something imports numpy
before ``core`` -- a new conftest import, a plugin -- the pin silently does
nothing and the record holds on this machine but not on the next. This test
reads back the kernel that actually loaded.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from core import blas

ROOT = Path(__file__).resolve().parents[2]

needs_avx2 = pytest.mark.skipif(not blas.cpu_has_avx2(), reason="no AVX2: the pin is skipped by design")


@needs_avx2
def test_this_test_process_runs_the_pinned_kernel():
    kernels = blas.active_kernels()
    assert kernels, "no bundled OpenBLAS found under numpy.libs / scipy.libs"
    assert set(kernels.values()) == {blas.CORETYPE}, kernels


@needs_avx2
@pytest.mark.parametrize("entry", ["import core", "import modules.spectra.processing.fitting"])
def test_importing_the_project_first_pins_a_fresh_process(entry):
    code = f"{entry}\nfrom core import blas\nprint(sorted(set(blas.active_kernels().values())))"
    env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("OPENBLAS_")}
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == repr([blas.CORETYPE])
