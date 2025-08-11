from typing import Dict, List


class PermissionManager:
    """
    Simple role-based permissions manager.
    permissions: { role: [tool_name, ...] }
    user_roles: { user_id: [role1, role2] }
    """
    def __init__(self, permissions: Dict[str, List[str]], user_roles: Dict[str, List[str]]):
        self.permissions = permissions or {}
        self.user_roles = user_roles or {}

    def user_allowed(self, user_id: str, tool_name: str) -> bool:
        roles = self.user_roles.get(user_id, [])
        for r in roles:
            allowed = self.permissions.get(r, [])
            if tool_name in allowed or "*" in allowed:
                return True
        return False

    def set_user_roles(self, user_id: str, roles: List[str]):
        self.user_roles[user_id] = roles
