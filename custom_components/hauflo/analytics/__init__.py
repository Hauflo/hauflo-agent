"""Diagnostics analytics: learned norms, correlations and loop tuning hints for the staff."""
from __future__ import annotations
from .correlation import Correlation
from .loops import LoopTuning
from .series import SeriesNorm
ANALYTICS = (SeriesNorm, Correlation, LoopTuning)
