"""CPD-9 TM-5: an agent's ``ownerTeam`` is a team of its org, given by an org admin or an OWNER of that team.

The decision is ``owner_team_of`` over the org's teams, the caller's live role and the caller's team role; the
agent routes map its refusals to 400 and 403, and the stored agent document accepts the field.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import jsonschema
import pytest

from app.api.routes.agent import InvalidRequestError, PermissionDeniedError, _owner_team
from app.control_plane.directory.owner_team import OwnerTeamRefused, owner_team_of
from app.schema.arango.documents import agent_schema

TEAMS = {"t-org": "org-1", "t-other": "org-2"}


def _facts(*, admin: bool, roles: dict[str, str]) -> dict[str, Any]:
    async def team_in_org(team_id: str) -> bool:
        return TEAMS.get(team_id) == "org-1"

    async def is_org_admin() -> bool:
        return admin

    async def team_role(team_id: str) -> str | None:
        return roles.get(team_id)

    return {"team_in_org": team_in_org, "is_org_admin": is_org_admin, "team_role": team_role}


@pytest.mark.parametrize(
    ("raw", "admin", "roles", "expected"),
    [
        (None, False, {}, None),
        ("t-org", True, {}, "t-org"),
        ("t-org", False, {"t-org": "OWNER"}, "t-org"),
        ("t-org", False, {"t-org": "WRITER"}, "owner_team_forbidden"),
        ("t-org", False, {}, "owner_team_forbidden"),
        ("t-other", True, {"t-other": "OWNER"}, "owner_team_unknown"),
        ("no-such", True, {}, "owner_team_unknown"),
        ("", True, {}, "owner_team_invalid"),
        (7, True, {}, "owner_team_invalid"),
    ],
    ids=["clear", "org-admin", "team-owner", "team-writer", "not-in-team", "other-org-team", "unknown", "empty", "not-a-string"],
)
async def test_tm5_who_may_give_an_agent_a_team(raw: object, admin: bool, roles: dict[str, str], expected: str | None) -> None:
    facts = _facts(admin=admin, roles=roles)
    if expected is None or expected.startswith("t-"):
        assert await owner_team_of(raw, **facts) == expected
        return
    with pytest.raises(OwnerTeamRefused) as refused:
        await owner_team_of(raw, **facts)
    assert refused.value.reason_code == expected


@pytest.mark.parametrize(
    ("reason", "error", "status"),
    [("owner_team_forbidden", PermissionDeniedError, 403), ("owner_team_unknown", InvalidRequestError, 400)],
)
async def test_tm5_the_agent_routes_answer_a_refusal_as_403_or_400(reason: str, error: type[Exception], status: int) -> None:
    async def refuse(*_args: Any, **_kwargs: Any) -> str | None:  # noqa: ANN401
        raise OwnerTeamRefused(reason, "no")

    with patch("app.api.routes.agent.owner_team_from", new=refuse), pytest.raises(error) as raised:
        await _owner_team(MagicMock(), {"ownerTeam": "t-org"}, {"graph_provider": MagicMock()}, "org-1", "u-key")
    assert raised.value.status_code == status  # type: ignore[attr-defined]


def test_tm5_the_agent_document_holds_an_owner_team_or_null() -> None:
    base = {"name": "a", "description": "d", "startMessage": "s", "systemPrompt": "p", "models": [], "createdBy": "u", "createdAtTimestamp": 1}
    jsonschema.validate({**base, "ownerTeam": "t-org"}, agent_schema["rule"])
    jsonschema.validate({**base, "ownerTeam": None}, agent_schema["rule"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**base, "ownerTeam": 3}, agent_schema["rule"])
