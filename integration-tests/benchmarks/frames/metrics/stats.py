"""Statistics: bootstrap CIs over questions, exact McNemar on paired
outcomes, Cohen's kappa between judges. With n = 824 the 95% margin on
accuracy is about ±3 points, so differences under ~4-5 points need the paired test."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import binomtest


@dataclass(frozen=True)
class Interval:
    value: float
    low: float
    high: float


@dataclass(frozen=True)
class McNemar:
    a_only: int
    b_only: int
    p_value: float


def bootstrap_mean(values: Sequence[float], *, samples: int, confidence: float, seed: int) -> Interval | None:
    if not values:
        return None
    data = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = data[rng.integers(0, len(data), size=(samples, len(data)))].mean(axis=1)
    tail = (1.0 - confidence) / 2.0
    return Interval(float(data.mean()), float(np.quantile(means, tail)), float(np.quantile(means, 1.0 - tail)))


def mcnemar_exact(a: Sequence[bool], b: Sequence[bool]) -> McNemar:
    if len(a) != len(b):
        raise ValueError("paired outcomes must be the same length")
    a_only = sum(1 for x, y in zip(a, b, strict=True) if x and not y)
    b_only = sum(1 for x, y in zip(a, b, strict=True) if y and not x)
    discordant = a_only + b_only
    p_value = 1.0 if discordant == 0 else float(binomtest(min(a_only, b_only), discordant, 0.5).pvalue)
    return McNemar(a_only=a_only, b_only=b_only, p_value=p_value)


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    if not a or len(a) != len(b):
        return None
    n = len(a)
    observed = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if expected == 1.0 else (observed - expected) / (1.0 - expected)


def percentile(values: Sequence[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None
