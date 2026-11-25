"""AG1 WV: an engine agent's settings are parsed and checked under the org ceiling when written."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.agents.engine_settings import (
    AgentEngineSettings,
    EngineSettingsRefused,
    agent_engine_settings,
)
from harness_core.engines.memory import InMemoryEngineCeilings
from harness_core.engines.profiles import EngineProfileResolver
from harness_engines.release import release_profiles

_RELEASE_MAX_STEPS: int = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts/harness/core/policy/release.json").read_text(encoding="utf-8")
)["engineProfile"]["limits"]["maxSteps"]

if TYPE_CHECKING:
    from harness_core.engines.types import ProfileConstraint

ORG = "org-1"


class _Counting(InMemoryEngineCeilings):
    reads: int = 0

    async def ceiling(self, org_id: str) -> ProfileConstraint | None:
        self.reads += 1
        return await super().ceiling(org_id)


@pytest.fixture
def ceilings() -> _Counting:
    return _Counting()


async def _settings(
    ceilings: _Counting,
    body: dict[str, object],
    stored: dict[str, object] | None = None,
    *,
    admin: bool = True,
) -> AgentEngineSettings | None:
    return await agent_engine_settings(
        ORG,
        "agent-1",
        body,
        stored,
        lambda: EngineProfileResolver(release_profiles, ceilings),
        caller_is_admin=admin,
    )


async def _refused(
    ceilings: _Counting,
    body: dict[str, object],
    stored: dict[str, object] | None = None,
    *,
    admin: bool = True,
) -> EngineSettingsRefused:
    with pytest.raises(EngineSettingsRefused) as refused:
        await _settings(ceilings, body, stored, admin=admin)
    return refused.value


@pytest.mark.asyncio
async def test_wv1_a_valid_narrowing_is_stored_canonical_with_its_cloud_account(ceilings: _Counting) -> None:
    settings = await _settings(
        ceilings,
        {"engineProfile": {"accountKinds": ["user_api_key", "org_cloud"], "mode": "plan"}, "modelAccountId": "cloud/acc-1"},
    )
    assert settings == AgentEngineSettings({"accountKinds": ["org_cloud", "user_api_key"], "mode": "plan"}, "cloud/acc-1")
    assert settings.document_fields() == {
        "engineProfile": {"accountKinds": ["org_cloud", "user_api_key"], "mode": "plan"},
        "modelAccountId": "cloud/acc-1",
    }
    assert (await _settings(ceilings, {"engineProfile": {}})) == AgentEngineSettings({}, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        {"accountKind": ["user_api_key"]},
        {"limits": {"maxSteps": None}},
        {"accountKinds": ["user_api_key", "user_api_key"]},
        {"accountKinds": ["someone_elses_key"]},
        ["user_api_key"],
        "user_api_key",
    ],
    ids=["unknown-key", "null", "repeat", "unknown-kind", "list", "text"],
)
async def test_wv2_an_invalid_narrowing_is_refused_invalid(ceilings: _Counting, profile: object) -> None:
    refused = await _refused(ceilings, {"engineProfile": profile})
    assert (refused.code, refused.status) == ("ENGINE_PROFILE_INVALID", 400)
    assert ceilings.reads == 0


@pytest.mark.asyncio
async def test_wv3_a_narrowing_wider_than_the_org_ceiling_is_refused_naming_the_dimension(ceilings: _Counting) -> None:
    ceilings.store(ORG, {"accountKinds": ["org_api_key"]})
    refused = await _refused(ceilings, {"engineProfile": {"accountKinds": ["user_api_key"]}})
    assert (refused.code, refused.status, refused.dimension, refused.layer) == (
        "ENGINE_PROFILE_WIDENING",
        400,
        "accountKinds",
        "agent",
    )
    ceilings.store(ORG, {"limits": {"maxSteps": 10}})
    refused = await _refused(ceilings, {"engineProfile": {"limits": {"maxSteps": 11}}})
    assert (refused.code, refused.dimension) == ("ENGINE_PROFILE_WIDENING", "limits.maxSteps")
    assert (await _settings(ceilings, {"engineProfile": {"limits": {"maxSteps": 10}}})) is not None


@pytest.mark.asyncio
async def test_wv4_an_unreadable_or_invalid_ceiling_refuses_503(ceilings: _Counting) -> None:
    ceilings.unreadable = True
    refused = await _refused(ceilings, {"engineProfile": {}})
    assert (refused.code, refused.status) == ("ENGINE_PROFILE_UNAVAILABLE", 503)
    ceilings.unreadable = False
    ceilings.store(ORG, {"nonsense": True})
    assert (await _refused(ceilings, {"engineProfile": {}})).status == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "stored"),
    [
        ({"engineProfile": {}, "modelAccountId": "users/user-1/openAI"}, None),
        ({"engineProfile": {}, "modelAccountId": "cloud/../x"}, None),
        ({"engineProfile": {}, "modelAccountId": "cloud/a/b"}, None),
        ({"engineProfile": {}, "modelAccountId": 7}, None),
        ({"modelAccountId": "cloud/acc-1"}, None),
        ({"engineProfile": None, "modelAccountId": "cloud/acc-1"}, None),
        ({"engineProfile": {"accountKinds": ["user_api_key"]}, "modelAccountId": "cloud/acc-1"}, None),
        ({"modelAccountId": "cloud/acc-1"}, {"engineProfile": {"accountKinds": ["org_api_key"]}}),
    ],
    ids=["person-key", "dot-dot", "nested", "not-text", "no-profile", "null-profile", "kinds-exclude", "stored-excludes"],
)
async def test_wv5_a_cloud_account_pin_is_refused_unless_it_fits(
    ceilings: _Counting, body: dict[str, object], stored: dict[str, object] | None
) -> None:
    refused = await _refused(ceilings, body, stored)
    assert (refused.code, refused.status) == ("MODEL_ACCOUNT_INVALID", 400)


@pytest.mark.asyncio
async def test_wv6_a_narrowing_without_org_cloud_clears_the_stored_pin_and_null_clears_both(ceilings: _Counting) -> None:
    stored = {"engineProfile": {"accountKinds": ["org_cloud"]}, "modelAccountId": "cloud/acc-1"}
    assert (await _settings(ceilings, {"engineProfile": {"accountKinds": ["user_api_key"]}}, stored)) == AgentEngineSettings(
        {"accountKinds": ["user_api_key"]}, None
    )
    assert (await _settings(ceilings, {"engineProfile": {}}, stored)) == AgentEngineSettings({}, "cloud/acc-1")
    assert (await _settings(ceilings, {"engineProfile": None}, stored)) == AgentEngineSettings(None, None)


@pytest.mark.asyncio
async def test_wv7_a_body_without_either_field_reads_nothing(ceilings: _Counting) -> None:
    assert (await _settings(ceilings, {"name": "x"}, {"engineProfile": {"accountKinds": ["org_cloud"]}})) is None
    assert ceilings.reads == 0

    def no_ceiling_source() -> InMemoryEngineCeilings:
        raise AssertionError("a loop agent's write built a ceiling source")

    assert (await agent_engine_settings(ORG, "agent-1", {"name": "x"}, None, no_ceiling_source, caller_is_admin=False)) is None


@pytest.mark.asyncio
async def test_wv8_an_account_alone_is_checked_against_the_stored_narrowing(ceilings: _Counting) -> None:
    stored = {"engineProfile": {"accountKinds": ["org_cloud"]}}
    assert (await _settings(ceilings, {"modelAccountId": "cloud/acc-2"}, stored)) == AgentEngineSettings(
        {"accountKinds": ["org_cloud"]}, "cloud/acc-2"
    )
    assert ceilings.reads == 1
    assert (await _settings(ceilings, {"modelAccountId": None}, stored)) == AgentEngineSettings(
        {"accountKinds": ["org_cloud"]}, None
    )


@pytest.mark.asyncio
async def test_wv9_a_refusal_is_logged_once_with_ids_and_no_content(
    ceilings: _Counting, caplog: pytest.LogCaptureFixture
) -> None:
    ceilings.store(ORG, {"accountKinds": ["org_api_key"]})
    with caplog.at_level(logging.WARNING, logger="app.agents.engine_settings"):
        await _refused(ceilings, {"engineProfile": {"accountKinds": ["user_api_key"], "skills": ["secret-skill"]}})
    records = [r for r in caplog.records if r.getMessage() == "agent.engine_profile.refused"]
    assert len(records) == 1
    record = records[0]
    assert (record.orgId, record.agentKey, record.reasonCode, record.dimension, record.layer) == (  # type: ignore[attr-defined]
        ORG,
        "agent-1",
        "ENGINE_PROFILE_WIDENING",
        "accountKinds",
        "agent",
    )
    assert "secret-skill" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [None, {"engineProfile": None}, {"name": "loop agent"}],
    ids=["create", "loop-null", "loop-absent"],
)
async def test_ae1_ae2_only_an_admin_makes_an_agent_an_engine_agent(
    ceilings: _Counting, stored: dict[str, object] | None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    refused = await _refused(ceilings, {"engineProfile": {"mode": "plan"}}, stored, admin=False)
    assert (refused.code, refused.status) == ("ENGINE_AGENT_ADMIN_REQUIRED", 403)
    assert ceilings.reads == 0
    lines = [r for r in caplog.records if r.getMessage() == "agent.engine_profile.refused"]
    assert len(lines) == 1
    assert lines[0].__dict__["reasonCode"] == "ENGINE_AGENT_ADMIN_REQUIRED"
    # Only the line's own fields: the record's path and module name are the checkout's (a worktree named *plan*).
    standard = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
    assert "plan" not in str({k: v for k, v in lines[0].__dict__.items() if k not in standard})


@pytest.mark.asyncio
async def test_ae3_an_admin_makes_one_within_the_ceiling(ceilings: _Counting) -> None:
    assert await _settings(ceilings, {"engineProfile": {"mode": "plan"}}, {"engineProfile": None}) == AgentEngineSettings(
        {"mode": "plan"}, None
    )


@pytest.mark.asyncio
async def test_ae4_an_editor_changes_an_engine_agent_or_turns_it_back(ceilings: _Counting) -> None:
    stored: dict[str, object] = {"engineProfile": {"mode": "build"}}
    assert await _settings(ceilings, {"engineProfile": {"mode": "plan"}}, stored, admin=False) == AgentEngineSettings(
        {"mode": "plan"}, None
    )
    assert await _settings(ceilings, {"engineProfile": None}, stored, admin=False) == AgentEngineSettings(None, None)
    assert await _settings(ceilings, {"modelAccountId": "cloud/acc-1"}, stored, admin=False) == AgentEngineSettings(
        {"mode": "build"}, "cloud/acc-1"
    )
    # One step above the release's ceiling (WP-70 raised it from 50 to 1,000), whatever it is.
    above = _RELEASE_MAX_STEPS + 1
    widening = await _refused(ceilings, {"engineProfile": {"limits": {"maxSteps": above}}}, stored, admin=False)
    assert widening.code == "ENGINE_PROFILE_WIDENING"


@pytest.mark.asyncio
async def test_ae5_a_loop_agents_write_needs_no_role(ceilings: _Counting) -> None:
    assert await _settings(ceilings, {"name": "x"}, None, admin=False) is None
    assert await _settings(ceilings, {"engineProfile": None}, None, admin=False) == AgentEngineSettings(None, None)
    assert ceilings.reads == 0
