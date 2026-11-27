"""Slack Web API rate-limit tiers."""

from enum import IntEnum


class Tier(IntEnum):
    """Slack Web API rate-limit tier."""

    T2 = 2
    T3 = 3
    T4 = 4
