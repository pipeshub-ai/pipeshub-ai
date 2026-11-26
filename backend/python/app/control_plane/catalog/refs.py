"""``ItemRef``: ``{kind}:{publisher}/{name}@{version}`` (02-lld §1.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import override

# The pattern of `ItemRef` in contracts/control-plane/common.schema.json.
_REF = re.compile(
    r"^(?P<kind>[a-z][a-z0-9-]{0,31}):(?P<publisher>[a-z0-9][a-z0-9._-]{0,63})"
    r"/(?P<name>[a-z0-9][a-z0-9._-]{0,127})@(?P<version>[0-9A-Za-z][0-9A-Za-z.+-]{0,63})$"
)

RELEASE_PUBLISHER = "pipeshub"


class InvalidItemRef(ValueError):
    reason = "invalid_item_ref"


@dataclass(frozen=True, slots=True)
class ItemRef:
    kind: str
    publisher: str
    name: str
    version: str

    def __post_init__(self) -> None:
        if not _REF.fullmatch(f"{self.kind}:{self.publisher}/{self.name}@{self.version}"):
            raise InvalidItemRef(f"not an item ref: {self.kind}:{self.publisher}/{self.name}@{self.version}")

    @classmethod
    def parse(cls, value: str) -> ItemRef:
        match = _REF.fullmatch(value)
        if match is None:
            raise InvalidItemRef(f"not an item ref: {value!r}")
        return cls(match["kind"], match["publisher"], match["name"], match["version"])

    @override
    def __str__(self) -> str:
        return f"{self.kind}:{self.publisher}/{self.name}@{self.version}"
