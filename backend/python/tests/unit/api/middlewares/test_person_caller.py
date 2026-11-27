"""Who counts as a person acting as themself, where the edition runs every agent only as the identified caller."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app import edition
from app.api.middlewares.auth import SERVICE_IDENTITY_CLAIM
from app.api.middlewares.person_caller import acts_as_a_person, refuse_unless_a_person
from app.api.middlewares.token_policy import AuthTokenType

PERSON = {"userId": "u-1", "orgId": "org-1"}
NOT_A_PERSON: dict[str, dict[str, Any] | None] = {
    "service token": {**PERSON, "token_type": AuthTokenType.SCOPED.value},
    "client credentials": {**PERSON, "isOAuth": True, SERVICE_IDENTITY_CLAIM: False},
    "client credentials for a service account": {**PERSON, "isOAuth": True, SERVICE_IDENTITY_CLAIM: True},
    "service account": {**PERSON, "isServiceAccount": True},
    "no org": {"userId": "u-1"},
    "no user": {"orgId": "org-1"},
    "nobody": None,
}


def test_a_person_and_their_own_tokens_act_as_a_person() -> None:
    assert acts_as_a_person(PERSON)
    assert acts_as_a_person({**PERSON, "isOAuth": True, "oauthScopes": ["agent:execute"]})


@pytest.mark.parametrize("who", list(NOT_A_PERSON))
def test_everyone_else_is_refused_where_agents_run_as_the_caller(monkeypatch: pytest.MonkeyPatch, who: str) -> None:
    assert not acts_as_a_person(NOT_A_PERSON[who])
    monkeypatch.setattr(edition, "AGENTS_RUN_AS_THE_CALLER", True)
    with pytest.raises(HTTPException) as refused:
        refuse_unless_a_person(NOT_A_PERSON[who])
    assert refused.value.status_code == 403
    refuse_unless_a_person(PERSON)


@pytest.mark.parametrize("who", list(NOT_A_PERSON))
def test_with_the_switch_off_nobody_is_refused_here(monkeypatch: pytest.MonkeyPatch, who: str) -> None:
    monkeypatch.setattr(edition, "AGENTS_RUN_AS_THE_CALLER", False)
    refuse_unless_a_person(NOT_A_PERSON[who])
