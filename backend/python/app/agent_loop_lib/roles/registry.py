from __future__ import annotations

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.roles.base import Role


class RoleRegistry:
    """Stores named Role instances."""

    def __init__(self) -> None:
        self._roles: dict[str, Role] = {}

    def register(self, role: Role) -> None:
        """Store role keyed by role.name; silently overwrites if already exists."""
        self._roles[role.name] = role

    def resolve(self, name: str) -> Role:
        """Return role by name, or raise RegistryError if not found."""
        try:
            return self._roles[name]
        except KeyError:
            raise RegistryError(f"Role not found in registry: {name!r}")

    def has(self, name: str) -> bool:
        """Return True if a role with the given name is registered."""
        return name in self._roles

    def names(self) -> list[str]:
        """Return list of all registered role names."""
        return list(self._roles.keys())


def default_registry() -> RoleRegistry:
    """Returns a RoleRegistry pre-populated with all builtin roles."""
    from app.agent_loop_lib.roles.builtin.assistant import ASSISTANT_ROLE
    from app.agent_loop_lib.roles.builtin.coder import CODER_ROLE
    from app.agent_loop_lib.roles.builtin.critic import CRITIC_ROLE
    from app.agent_loop_lib.roles.builtin.planner import PLANNER_ROLE
    from app.agent_loop_lib.roles.builtin.researcher import RESEARCHER_ROLE
    from app.agent_loop_lib.roles.builtin.skill_writer import SKILL_WRITER_ROLE
    from app.agent_loop_lib.roles.builtin.verifier import VERIFIER_ROLE
    from app.agent_loop_lib.roles.builtin.web_search import WEB_SEARCH_ROLE
    from app.agent_loop_lib.roles.builtin.writer import WRITER_ROLE

    registry = RoleRegistry()
    for role in (
        ASSISTANT_ROLE,
        PLANNER_ROLE,
        RESEARCHER_ROLE,
        CRITIC_ROLE,
        VERIFIER_ROLE,
        WRITER_ROLE,
        WEB_SEARCH_ROLE,
        CODER_ROLE,
        SKILL_WRITER_ROLE,
    ):
        registry.register(role)
    return registry
