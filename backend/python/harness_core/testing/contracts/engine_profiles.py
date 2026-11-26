"""Contract suite for ``EngineCeilingSource`` (E4a): provide ``ceiling_env`` (``CeilingEnv``).

The env stores an org's ceiling the way an admin would (the wire narrowing, or ``None``
to clear it), can store bytes that are not a narrowing, and can make reads fail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.engines.errors import ProfileCeilingUnavailable, ProfileInvalid
from harness_core.engines.profiles import constraint_from_wire

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.engines.ports import EngineCeilingSource

ORG = "org-1"
CEILING: JsonValue = {
    "engines": ["opencode"],
    "isolationMin": "gvisor",
    "limits": {"maxUsd": 2.5, "maxSteps": 30},
    "approvals": {"write": "deny"},
    "skills": ["pdf", "xlsx"],
}


class CeilingEnv(Protocol):
    @property
    def source(self) -> EngineCeilingSource: ...

    async def store(self, org_id: str, ceiling: JsonValue) -> None:
        """Set (or, with ``None``, clear) the org's ceiling as the admin route writes it."""
        ...

    async def store_raw(self, org_id: str, raw: JsonValue) -> None:
        """Store something that is not a narrowing under the org."""
        ...

    def break_reads(self) -> None: ...


class EngineCeilingSourceContract:
    @pytest.fixture
    def ceiling_env(self) -> CeilingEnv:
        raise NotImplementedError("provide a ceiling_env fixture")

    async def test_ec_1_no_ceiling_is_none(self, ceiling_env: CeilingEnv) -> None:
        assert await ceiling_env.source.ceiling(ORG) is None

    async def test_ec_2_a_stored_ceiling_reads_back_as_its_narrowing(self, ceiling_env: CeilingEnv) -> None:
        await ceiling_env.store(ORG, CEILING)
        assert await ceiling_env.source.ceiling(ORG) == constraint_from_wire(CEILING)
        assert await ceiling_env.source.ceiling("org-2") is None

    async def test_ec_3_a_change_is_seen_by_the_next_read(self, ceiling_env: CeilingEnv) -> None:
        await ceiling_env.store(ORG, CEILING)
        assert await ceiling_env.source.ceiling(ORG) is not None
        await ceiling_env.store(ORG, {"mode": "plan"})
        assert await ceiling_env.source.ceiling(ORG) == constraint_from_wire({"mode": "plan"})
        await ceiling_env.store(ORG, None)
        assert await ceiling_env.source.ceiling(ORG) is None

    async def test_ec_4_a_stored_value_that_is_not_a_narrowing_is_refused(self, ceiling_env: CeilingEnv) -> None:
        await ceiling_env.store_raw(ORG, {"limits": {"maxSteps": None}})
        with pytest.raises(ProfileInvalid):
            _ = await ceiling_env.source.ceiling(ORG)

    async def test_ec_5_an_unreadable_store_is_refused_not_absent(self, ceiling_env: CeilingEnv) -> None:
        await ceiling_env.store(ORG, CEILING)
        ceiling_env.break_reads()
        with pytest.raises(ProfileCeilingUnavailable) as refused:
            _ = await ceiling_env.source.ceiling(ORG)
        assert refused.value.reason_code == "profile_ceiling_unavailable"


__all__ = ["CEILING", "CeilingEnv", "EngineCeilingSourceContract"]
