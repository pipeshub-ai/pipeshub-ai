"""A run knows the asker's graph user key as well as their account id, each as its own field.

The agent route reads the user's graph record into `user_info["_key"]`, the chat route into
`user_info["userKey"]`; neither is ever taken from the account id the request was signed with.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.agent_loop.context import AgentContext

ACCOUNT, KEY = "6650f0aa00000000000000d1", "0efd15e5-0000-4000-8000-0000000000d1"


def _context(user_info: Any) -> AgentContext:  # noqa: ANN401
    return AgentContext.from_chat_state({"org_id": "org-1", "user_id": ACCOUNT, "user_info": user_info})


@pytest.mark.parametrize("field", ["_key", "userKey"], ids=["agent route", "chat route"])
def test_the_key_the_route_read_is_the_runs_user_key(field: str) -> None:
    context = _context({"userId": ACCOUNT, field: KEY})
    assert context.user_key == KEY and context.user_id == ACCOUNT


@pytest.mark.parametrize("user_info", [{"userId": ACCOUNT}, {"userId": ACCOUNT, "_key": None}, {"userId": ACCOUNT, "userKey": 7}, None])
def test_without_one_the_key_is_empty_never_the_account_id(user_info: Any) -> None:  # noqa: ANN401
    assert _context(user_info).user_key == ""
