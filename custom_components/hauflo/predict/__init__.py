"""Predictive detectors: warn before something breaks, from the trend toward the failure."""
from __future__ import annotations
from ..insight.detector import Detector
from .climate import HeatSlowing, ShortCycling
from .devices import BatteryForecast, HaHost, LinkQuality
from .episodes import Flapping, NightWater
TRENDS: tuple[type[Detector], ...] = (BatteryForecast, HeatSlowing, ShortCycling, LinkQuality, Flapping, HaHost, NightWater)
