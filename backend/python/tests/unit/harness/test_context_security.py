from __future__ import annotations

from app.agents.agent_loop.context import AgentContext


def test_runtime_vfs_services_are_excluded_from_context_serialization() -> None:
    context = AgentContext(
        org_id="org-1",
        user_id="user-1",
        user_email="user@example.com",
        virtual_filesystem=object(),
        workspace_manifest=object(),
    )

    serialized = context.model_dump()

    assert "virtual_filesystem" not in serialized
    assert "workspace_manifest" not in serialized
