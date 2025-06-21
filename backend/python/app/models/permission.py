from typing import List, Optional

from pydantic import BaseModel


class Permission(BaseModel):
    permission_id: str
    type: str
    role: str
    email: Optional[str] = None


class AccessControl(BaseModel):
    owners: List[str] = []
    editors: List[str] = []
    viewers: List[str] = []
    domains: List[str] = []
    anyone_with_link: bool = False
