"""Small, deterministic helpers for timing and measurement intervals."""

from __future__ import annotations

import math
from typing import Optional


def elapsed_sample_interval(
    previous_cycle_start: Optional[float],
    current_cycle_start: float,
    nominal_interval_seconds: float,
) -> float:
    """Return the elapsed state-sampling interval in seconds.

    The first completed sample has no previous sample timestamp, so it uses
    the configured nominal interval. Subsequent samples use the monotonic
    start-to-start interval; this includes any monitoring-cycle overruns and
    avoids integrating energy over an assumed fixed cadence.
    """
    nominal = float(nominal_interval_seconds)
    current = float(current_cycle_start)
    if not math.isfinite(nominal) or nominal <= 0:
        raise ValueError("nominal_interval_seconds must be finite and positive")
    if not math.isfinite(current):
        raise ValueError("current_cycle_start must be finite")
    if previous_cycle_start is None:
        return nominal

    previous = float(previous_cycle_start)
    if not math.isfinite(previous):
        raise ValueError("previous_cycle_start must be finite")
    elapsed = current - previous
    if not math.isfinite(elapsed) or elapsed <= 0:
        raise ValueError("cycle start timestamps must be strictly increasing")
    return elapsed
