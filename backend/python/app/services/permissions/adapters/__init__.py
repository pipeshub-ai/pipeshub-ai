"""Graph adapters of the permission ports. The `PrincipalView` works over any `IGraphDBProvider`; each provider
hands out its own `HierarchyView` (`permission_hierarchy_view()`), because its queries live next to its client."""

from .graph import GraphPrincipalView

__all__ = ["GraphPrincipalView"]
