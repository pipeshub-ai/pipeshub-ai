"""Skill trust (C3.57, C6.34, AD-05): whether agents may use a skill's exact content.

A skill written in PipesHub, or shipped by the release, is first party. A skill imported from outside PipesHub is
decided by the skill admission policy (``SkillAdmission``, an extension point read from ``edition_config``): this
build's uses it at once unless the content scanner (SK-06) flags it, and holds flagged content back. Where a skill
came from is PipesHub's record (``source`` in the skill's metadata: the import route sets ``imported`` and every edit
keeps it), never a claim of the package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Protocol, cast

import yaml

from harness_core.logs import HarnessLog
from harness_core.skills.types import SkillBundleUnavailable
from harness_core.supply_chain.skill_scan import scan_skill
from harness_core.supply_chain.trust import (
    ArtefactKind,
    ArtefactUntrusted,
    TrustRoots,
    TrustVerdict,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.engines.types import JsonValue

IMPORTED: Final = "imported"
# An imported skill the content scanner flags: held back until its content changes.
CONTENT_FLAGGED: Final = "skill_content_flagged"
_AGENT_LOOP: Final = "agent-loop"
_LOG = HarnessLog.named("app.harness.supply_chain")


class TrustConfig(Protocol):
    """The part of ``ConfigurationService`` an admission policy may keep its records in."""

    async def get_config(
        self,
        key: str,
        default: Any = None,  # noqa: ANN401
        use_cache: bool = False,  # noqa: FBT001, FBT002
        *,
        raise_on_error: bool = False,
    ) -> Any: ...  # noqa: ANN401

    async def set_config(self, key: str, value: Any) -> bool: ...  # noqa: ANN401


class SkillUntrusted(SkillBundleUnavailable):
    """An imported skill the admission policy does not trust at this content: never mounted or loaded."""

    reason = "skill_untrusted"

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _mapping(value: object) -> dict[str, object]:
    return dict(cast("dict[str, object]", value)) if isinstance(value, dict) else {}


def skill_source(files: Mapping[str, bytes]) -> str:
    """Where the skill came from, as PipesHub recorded it in the bundle's SKILL.md metadata (``manual`` when unset,
    as the skill store reads it)."""
    content = files.get("SKILL.md", b"").decode("utf-8", errors="replace")
    if not content.startswith("---"):
        return "manual"
    parts = content.split("---", 2)
    try:
        front = yaml.safe_load(parts[1]) if len(parts) >= 3 else None
    except yaml.YAMLError:
        return IMPORTED
    source = _mapping(_mapping(_mapping(front).get("metadata")).get(_AGENT_LOOP)).get("source")
    return source if isinstance(source, str) and source else "manual"


def with_imported_source(content: str) -> str:
    """``content`` whose metadata says it was imported, whatever the package claimed (``builtin`` or ``manual``
    included): the import route is the one place that knows."""
    if not content.startswith("---"):
        raise ValueError("an imported SKILL.md has YAML frontmatter")
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError("an imported SKILL.md has YAML frontmatter")
    loaded: object = yaml.safe_load(parts[1]) or {}
    if not isinstance(loaded, dict):
        raise ValueError("YAML frontmatter must be a mapping of key: value pairs")
    front = _mapping(cast("object", loaded))
    metadata = _mapping(front.get("metadata"))
    loop = _mapping(metadata.get(_AGENT_LOOP))
    loop["source"] = IMPORTED
    _ = loop.pop("status", None)
    metadata[_AGENT_LOOP] = loop
    front["metadata"] = metadata
    return f"---\n{yaml.safe_dump(front, sort_keys=False)}---{parts[2]}"


class SkillAdmission(Protocol):
    """Whether an imported skill's exact content may be used (``edition_config.skill_admission``)."""

    async def verdict(
        self,
        config: TrustConfig | None,
        roots: TrustRoots,
        org_id: str,
        name: str,
        digest: str,
        files: Mapping[str, bytes],
    ) -> TrustVerdict:
        """How the content is trusted; ``ArtefactUntrusted`` when it is not."""
        ...

    async def imported(
        self,
        config: TrustConfig,
        roots: TrustRoots,
        *,
        org_id: str,
        name: str,
        digest: str,
        attestation: Mapping[str, Any] | None,
        by: str,
    ) -> None:
        """Called once an import is stored, with what the importer sent; an ``HTTPException`` refuses it, worded for
        the person."""
        ...


class ScannedSkillAdmission:
    """An imported skill is used at once unless the content scanner flags it; flagged content is held back, so its
    owner can read the findings and change it. Nothing is stored."""

    async def verdict(
        self,
        config: TrustConfig | None,
        roots: TrustRoots,
        org_id: str,
        name: str,
        digest: str,
        files: Mapping[str, bytes],
    ) -> TrustVerdict:
        if scan_skill(files):
            raise ArtefactUntrusted(
                CONTENT_FLAGGED,
                "the content scanner flagged this skill",
                kind=ArtefactKind.SKILL,
                subject=name,
            )
        return TrustVerdict("scan")

    async def imported(
        self,
        config: TrustConfig,
        roots: TrustRoots,
        *,
        org_id: str,
        name: str,
        digest: str,
        attestation: Mapping[str, Any] | None,
        by: str,
    ) -> None:
        return None


SCANNED_SKILL_ADMISSION: Final = ScannedSkillAdmission()


def skill_admission() -> SkillAdmission:
    """The edition's policy, read at call time so an edition extension that sets it is honoured."""
    from app import edition_config

    policy = getattr(edition_config, "skill_admission", None)
    return cast("SkillAdmission", policy) if policy is not None else SCANNED_SKILL_ADMISSION


class SkillTrust:
    """Skill trust over the admission policy and the roots in force now."""

    def __init__(
        self, config: TrustConfig | None, roots: Callable[[], TrustRoots] | None = None
    ) -> None:
        from app.harness.supply_chain.roots import trust_roots

        self._config = config
        self._roots = roots or trust_roots

    async def verdict(
        self, org_id: str, name: str, digest: str, files: Mapping[str, bytes]
    ) -> TrustVerdict:
        """How the skill at ``digest`` is trusted; ``ArtefactUntrusted`` when it is not."""
        if skill_source(files) != IMPORTED:
            return TrustVerdict("first_party")
        return await skill_admission().verdict(
            self._config, self._roots(), org_id, name, digest, files
        )

    async def imported(
        self,
        org_id: str,
        name: str,
        digest: str,
        *,
        attestation: Mapping[str, Any] | None,
        by: str,
    ) -> None:
        """An import just stored, for the policy to see what the importer sent with it."""
        if self._config is None:
            return
        await skill_admission().imported(
            self._config,
            self._roots(),
            org_id=org_id,
            name=name,
            digest=digest,
            attestation=attestation,
            by=by,
        )

    async def check(
        self, org_id: str, name: str, digest: str, files: Mapping[str, bytes]
    ) -> None:
        """Refuses an untrusted skill as ``SkillUntrusted`` (logged), so every reader treats it as not there."""
        try:
            _ = await self.verdict(org_id, name, digest, files)
        except ArtefactUntrusted as refused:
            _LOG.warning(
                "supply_chain.skill_refused",
                orgId=org_id,
                skill=name,
                digest=digest,
                reasonCode=refused.reason,
            )
            raise SkillUntrusted(
                f"Skill {name!r} is not approved for use", code=refused.reason
            ) from None
        except Exception as exc:
            _LOG.warning(
                "supply_chain.skill_refused",
                orgId=org_id,
                skill=name,
                reasonCode="review_unreadable",
            )
            raise SkillUntrusted(
                f"Skill {name!r} could not be checked", code="review_unreadable"
            ) from exc

    async def standing(
        self, org_id: str, name: str, digest: str, files: Mapping[str, bytes]
    ) -> dict[str, JsonValue] | None:
        """What the skills page shows of an imported skill: whether agents may use it and why not; None for first
        party content, which needs no check."""
        if skill_source(files) != IMPORTED:
            return None
        try:
            verdict = await self.verdict(org_id, name, digest, files)
        except ArtefactUntrusted as refused:
            state = {
                "artefact_denied": "denied",
                "artefact_pending_review": "pending",
            }.get(refused.reason, "untrusted")
            return {
                "state": state,
                "reasonCode": refused.reason,
                "digest": digest,
                "findings": _findings(files),
            }
        return {
            "state": "trusted",
            "route": verdict.route,
            "digest": digest,
            "findings": _findings(files),
        }


def _findings(files: Mapping[str, bytes]) -> list[JsonValue]:
    return [f.as_json() for f in scan_skill(files)]  # type: ignore[misc]


__all__ = [
    "CONTENT_FLAGGED",
    "IMPORTED",
    "SCANNED_SKILL_ADMISSION",
    "ScannedSkillAdmission",
    "SkillAdmission",
    "SkillTrust",
    "SkillUntrusted",
    "TrustConfig",
    "skill_admission",
    "skill_source",
    "with_imported_source",
]
