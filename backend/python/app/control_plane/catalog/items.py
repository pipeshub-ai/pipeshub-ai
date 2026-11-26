"""Release catalog items (02-lld §2.1) as built from code: shared rows (`orgId: null`), `provenance: release`.

``to_json`` is the wire form ``contracts/control-plane/catalog-item.schema.json`` describes; Node reads
it through the generated zod schema.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from app.control_plane.catalog.digests import (
    descriptor_set_digest,
    engine_digest,
    skill_descriptor_digest,
    tool_descriptor_digest,
)
from app.control_plane.catalog.refs import RELEASE_PUBLISHER, ItemRef
from harness_core.contracts.canonical_json import canonical_sha256
from harness_core.skills import BUNDLE_FORMAT

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from harness_core.skills import BundleFileEntry

SCHEMA_VERSION = 1
RiskClass = Literal["read", "write-internal", "write-external", "execute"]
_RISK_ORDER: tuple[RiskClass, ...] = ("read", "write-internal", "write-external", "execute")
# An unclassified tool is a write that may leave the deployment (tool-descriptor.schema.json).
_EFFECT_RISK: dict[str | None, RiskClass] = {
    "read": "read",
    "workspace": "write-internal",
    "write": "write-external",
    "execute": "execute",
    None: "write-external",
}
_UNVERSIONED = "0"
_CONTENT_VERSION_HEX = 12


def risk_class_of(effects: Iterable[str | None]) -> RiskClass:
    ranks = [_RISK_ORDER.index(_EFFECT_RISK.get(effect, "write-external")) for effect in effects]
    return _RISK_ORDER[max(ranks, default=0)]


class Descriptor(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def digest(self) -> str: ...

    def to_json(self) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    name: str
    description: str
    input_schema: Mapping[str, object]
    effect: str | None = None
    sink: str | None = None
    # Who provides the tool, as its toolset declares it (KN2). Not in the digest: it decides how a run is granted
    # the tool, never what the tool does, and pins and approvals keep their digests.
    provider: str | None = None
    # An MCP tool's declared behaviour hints. In the digest, so the item's digest is the one an admin approves.
    hints: Mapping[str, bool] = field(default_factory=dict[str, bool])

    def _fields(self) -> dict[str, object]:
        hints = {"annotations": dict(self.hints)} if self.hints else {}
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
            "effect": self.effect,
            "sink": self.sink,
            **hints,
        }

    @property
    def digest(self) -> str:
        return tool_descriptor_digest(self._fields())

    def to_json(self) -> dict[str, object]:
        provider = {} if self.provider is None else {"provider": self.provider}
        return {**self._fields(), **provider, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class SkillBundleDescriptor:
    name: str
    files: Mapping[str, BundleFileEntry]

    @property
    def digest(self) -> str:
        return skill_descriptor_digest(self.files)

    def to_json(self) -> dict[str, object]:
        return {"name": self.name, "format": BUNDLE_FORMAT, "files": dict(self.files), "digest": self.digest}


@dataclass(frozen=True, slots=True)
class EngineDescriptor:
    kind: str
    version: str
    binary_digest: str
    options_digest: str | None = None

    @property
    def name(self) -> str:
        return self.kind

    def _fields(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "version": self.version,
            "binaryDigest": self.binary_digest,
            "optionsDigest": self.options_digest,
        }

    @property
    def digest(self) -> str:
        return engine_digest(self._fields())

    def to_json(self) -> dict[str, object]:
        return {"name": self.name, **self._fields(), "digest": self.digest}


@dataclass(frozen=True, slots=True)
class Display:
    name: str
    description: str
    icon: str | None = None
    categories: tuple[str, ...] = ()
    hidden: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "categories": list(self.categories),
            "hidden": self.hidden,
        }


@dataclass(frozen=True, slots=True)
class CredentialRequirement:
    mode: str
    owners: tuple[str, ...] = ("agent", "user")
    provider: str | None = None

    def to_json(self) -> dict[str, object]:
        return {"mode": self.mode, "owners": list(self.owners), "provider": self.provider}


@dataclass(frozen=True, slots=True)
class ReleaseItem:
    """One release item version. ``version`` is the source's own where it has one; otherwise
    ``with_content_version`` derives it from the content, so a changed item is a new ref."""

    kind: str
    name: str
    display: Display
    descriptor_kind: str
    descriptors: tuple[Descriptor, ...] = ()
    version: str = _UNVERSIONED
    risk_class: RiskClass = "read"
    config_schema: Mapping[str, object] | None = None
    credential_requirements: tuple[CredentialRequirement, ...] = ()
    publisher: str = RELEASE_PUBLISHER
    provenance: Literal["builtin", "release"] = "release"

    @property
    def ref(self) -> ItemRef:
        return ItemRef(self.kind, self.publisher, self.name, self.version)

    @property
    def descriptor_set_digest(self) -> str:
        return descriptor_set_digest((d.name, d.digest) for d in self.descriptors)

    def with_content_version(self) -> ReleaseItem:
        content = {key: value for key, value in self.to_json().items() if key not in ("ref", "version")}
        return dataclasses.replace(self, version="c." + canonical_sha256(content)[:_CONTENT_VERSION_HEX])

    def to_json(self) -> dict[str, object]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "ref": str(self.ref),
            "kind": self.kind,
            "publisher": self.publisher,
            "name": self.name,
            "version": self.version,
            "orgId": None,
            "provenance": self.provenance,
            "source": {"type": "release"},
            "display": self.display.to_json(),
            "riskClass": self.risk_class,
            "configSchema": None if self.config_schema is None else dict(self.config_schema),
            "credentialRequirements": [c.to_json() for c in self.credential_requirements],
            "descriptors": {"kind": self.descriptor_kind, "items": [d.to_json() for d in self.descriptors]},
            "descriptorSetDigest": self.descriptor_set_digest,
            "deprecated": False,
            "createdAt": 0,
        }
