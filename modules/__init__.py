"""Analysis modules, one per measurement technique. Each may import `core`; `core` never imports back."""

import core  # noqa: F401  pins the OpenBLAS kernel before a module loads numpy (core/blas.py)
