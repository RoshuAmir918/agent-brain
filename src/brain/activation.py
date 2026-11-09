"""ACT-R base-level activation with the Memory Spec's calibration."""
from __future__ import annotations

import math
from datetime import date

EXPONENT = 1.0
INFERRED_WEIGHT = 0.5
H_DAYS = 7      # a single use stays hot this long
W_DAYS = 28     # a single use stays warm this long


def thresholds(h: int = H_DAYS, w: int = W_DAYS) -> tuple[float, float]:
    return -math.log(h + 1), -math.log(w + 1)


def activation(ages: list[float], inferred_ages: list[float] = (), d: float = EXPONENT) -> float:
    total = sum((t + 1) ** -d for t in ages) + INFERRED_WEIGHT * sum((t + 1) ** -d for t in inferred_ages)
    return math.log(total) if total > 0 else float("-inf")


def ages_from_dates(dates: list[str], today: date) -> list[float]:
    out = []
    for s in dates:
        try:
            dt = date.fromisoformat(str(s)[:10])
        except ValueError:
            continue
        out.append(max(0.0, float((today - dt).days)))
    return out


def tier_for(b: float, pinned: bool = False, h: int = H_DAYS, w: int = W_DAYS) -> str:
    if pinned:
        return "hot"
    hot_t, warm_t = thresholds(h, w)
    if b >= hot_t:
        return "hot"
    if b >= warm_t:
        return "warm"
    return "cold"

