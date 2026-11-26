"""Id source port for run, invocation and event ids."""

from __future__ import annotations

import uuid
from typing import Protocol


class IdSource(Protocol):
    def new_id(self, prefix: str) -> str: ...


class RandomIds:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"
