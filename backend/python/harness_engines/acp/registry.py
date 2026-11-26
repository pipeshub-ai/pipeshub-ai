"""The packaged ACP agent profiles (``acp_agents.json`` and its signature), verified once per process.

The tree never holds the signature: a release build writes it next to the document (ADR-SEC-01 D7), and a test stack
signs the document with its own test root (``harness_engines.profile_trust``)."""

from __future__ import annotations

import logging
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Final

from harness_core.access.errors import VendorRulesInvalid
from harness_core.logs import HarnessLog
from harness_engines.acp.profiles import (
    AcpProfileInvalid,
    load_acp_agents,
    parse_acp_agents,
)

if TYPE_CHECKING:
    from harness_engines.acp.profiles import AcpAgentProfile
    from harness_engines.profile_trust import ProfileTestRoot

AGENTS_RESOURCE: Final = "acp_agents.json"
AGENTS_SIGNATURE_RESOURCE: Final = "acp_agents.json.sig"
_LOG = HarnessLog(logging.getLogger(__name__))


def _packaged(name: str) -> bytes | None:
    resource = resources.files("harness_engines").joinpath(name)
    return resource.read_bytes() if resource.is_file() else None


def release_signed() -> bool:
    """Whether this is a release build: only the release build's signing step packages the profiles' signature."""
    return _packaged(AGENTS_SIGNATURE_RESOURCE) is not None


@cache
def packaged_acp_agents(
    *, unsigned_ok: bool, test_root: ProfileTestRoot | None = None
) -> tuple[AcpAgentProfile, ...]:
    """The release's ACP profiles, verified against the release keys and, on a test stack, its test root; none while
    the document is unsigned (or broken), except with ``unsigned_ok`` (``PIPESHUB_ACP_PROFILES_UNSIGNED``), which
    reads an unsigned document."""
    # The keys live with the vendor rules; importing them here keeps this module free of the release module.
    from harness_engines.release_keys import trusted_release_keys

    keys = trusted_release_keys()
    signature = _packaged(AGENTS_SIGNATURE_RESOURCE)
    if test_root is not None:
        keys[test_root.key_id] = test_root.public_key
        signature = signature if signature is not None else test_root.signature
    try:
        return load_acp_agents(_packaged(AGENTS_RESOURCE), signature, keys, unsigned_ok=unsigned_ok)
    except VendorRulesInvalid as refused:
        _LOG.warning("engine.acp.profiles_unsigned", reasonCode="acp_profiles_unsigned", detail=refused.detail)
    except AcpProfileInvalid:
        _LOG.error("engine.acp.profiles_invalid", reasonCode=AcpProfileInvalid.reason_code)
    return ()


@cache
def packaged_unverified_acp_agents() -> tuple[AcpAgentProfile, ...]:
    """The packaged profiles as written, signature unchecked: only to name the engines an untrusted profile keeps
    from running (``harness_engines.release.untrusted_engines``), never to run one."""
    document = _packaged(AGENTS_RESOURCE)
    if document is None:
        return ()
    try:
        return parse_acp_agents(document)
    except AcpProfileInvalid:
        return ()


__all__ = [
    "AGENTS_RESOURCE",
    "AGENTS_SIGNATURE_RESOURCE",
    "packaged_acp_agents",
    "packaged_unverified_acp_agents",
    "release_signed",
]
