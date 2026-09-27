from app.agents.agent_loop.sub_agent_prompt import (
    build_sub_agent_prompt,
    build_user_context_block,
)
from tests.unit.agents.adapter.conftest import make_context


def test_build_user_context_block_empty_when_send_user_info_is_false() -> None:
    context = make_context(
        send_user_info=False,
        user_email="jane@example.com",
        user_info={"fullName": "Jane Doe"},
        org_info={"name": "Acme"},
    )
    assert build_user_context_block(context) == ""


def test_build_user_context_block_includes_identity_when_enabled() -> None:
    context = make_context(
        send_user_info=True,
        user_email="jane@example.com",
        user_info={"fullName": "Jane Doe"},
        org_info={"name": "Acme"},
    )
    block = build_user_context_block(context)
    assert "Jane Doe" in block
    assert "jane@example.com" in block
    assert "Acme" in block


def test_build_sub_agent_prompt_omits_user_block_when_disabled() -> None:
    context = make_context(
        send_user_info=False,
        user_email="jane@example.com",
        user_info={"fullName": "Jane Doe"},
        org_info={"name": "Acme"},
    )
    prompt = build_sub_agent_prompt("jira", context)
    assert "## Current User" not in prompt
    assert "Jane Doe" not in prompt


def test_sub_agent_prompt_explains_the_demo_data() -> None:
    from app.modules.agents.context.source_catalog import DEMO_SOURCE_NOTE

    demo = {"displayName": "Acme Corp demo data", "type": "Demo", "connectorId": "demo-1"}
    context = make_context(send_user_info=True, agent_knowledge=[demo], org_info={"name": "Initech"})
    assert DEMO_SOURCE_NOTE in build_sub_agent_prompt("research", context)
    # The exploration agent's source table already carries it: once is enough.
    prompt = build_sub_agent_prompt("internal_exploration", context, extra_instructions=DEMO_SOURCE_NOTE)
    assert prompt.count(DEMO_SOURCE_NOTE) == 1


def test_sub_agent_prompt_has_no_demo_note_without_the_demo() -> None:
    jira = {"displayName": "Engineering Jira", "type": "JIRA", "connectorId": "jira-1"}
    context = make_context(send_user_info=True, agent_knowledge=[jira])
    assert "Acme Corp" not in build_sub_agent_prompt("research", context)
