"""The cost classes of heavy-format work: the one definition the member budget and the parse engine share.

A class comes from an estimated parse time (see ``app.modules.parse_engine.cost``), never from bytes.
The values are the strings the ledger stores and the ``X-Parse-Class`` header carries, so a
``CostClass`` compares equal to its string. Standard library only: ``app.capacity`` must not import
``app.modules``.
"""
from __future__ import annotations

from enum import StrEnum

__all__ = ["BIG_CLASSES", "CostClass"]


class CostClass(StrEnum):
    SMALL = "small"
    LARGE = "large"
    HUGE = "huge"


BIG_CLASSES = (CostClass.LARGE, CostClass.HUGE)
