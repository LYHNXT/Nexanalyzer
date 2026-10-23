"""Technique-agnostic platform: IO, reporting, and rendering shared by every analysis module."""

from . import blas

# Before anything below us imports numpy: see core/blas.py.
blas.pin()
