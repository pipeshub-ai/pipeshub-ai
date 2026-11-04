"""Viewer permission scopes: what one viewer may reach, computed from the graph behind ports.

`ViewerScopes` is the single input every vector store's scope filter translates. Nothing on the serving path
reads this package while `AclFilterMode` is legacy (the default).
"""

from .errors import ViewerScopesUnavailable
from .mode import AclFilterMode, scopes_or_legacy
from .ports import HierarchyView, Principal, PrincipalView, ScopeNode
from .scopes import UNVERSIONED, ViewerScopes
from .service import ViewerScopeService
from .viewer import ViewerScopeResolver

__all__ = [
    "UNVERSIONED",
    "AclFilterMode",
    "HierarchyView",
    "Principal",
    "PrincipalView",
    "ScopeNode",
    "ViewerScopeResolver",
    "ViewerScopeService",
    "ViewerScopes",
    "ViewerScopesUnavailable",
    "scopes_or_legacy",
]
