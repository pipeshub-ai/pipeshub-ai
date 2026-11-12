"""Viewer permission scopes: what one viewer may reach, computed from the graph behind ports.

`ViewerScopes` is the single input every vector store's scope filter translates. Nothing on the serving path
reads this package while `AclFilterMode` is legacy (the default).
"""

from .errors import ViewerScopesUnavailable
from .mode import AclFilterMode, scope_fields_enabled, scopes_or_legacy
from .ports import HierarchyView, Principal, PrincipalView, ScopeNode, StampGraph
from .projector import Projection, ScopeProjector
from .scope_fields import ScopeFields, Stamp
from .scopes import UNVERSIONED, ViewerScopes
from .service import ViewerScopeService
from .viewer import ViewerScopeResolver

__all__ = [
    "UNVERSIONED",
    "AclFilterMode",
    "HierarchyView",
    "Principal",
    "PrincipalView",
    "Projection",
    "ScopeFields",
    "ScopeNode",
    "ScopeProjector",
    "Stamp",
    "StampGraph",
    "ViewerScopeResolver",
    "ViewerScopeService",
    "ViewerScopes",
    "ViewerScopesUnavailable",
    "scope_fields_enabled",
    "scopes_or_legacy",
]
