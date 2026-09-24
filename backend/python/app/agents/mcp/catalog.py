"""Search/pagination helpers over the MCP registry catalog — kept separate from
`registry.py` (which only owns discovery/storage) so routes stay thin.
"""
from app.agents.mcp.models import MCPServerTemplate

DEFAULT_PAGE = 1
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def search_templates(templates: list[MCPServerTemplate], search: str | None) -> list[MCPServerTemplate]:
    """Case-insensitive substring match over type id, display name, description, and tags."""
    if not search or not search.strip():
        return templates

    query = search.strip().lower()

    def _matches(template: MCPServerTemplate) -> bool:
        if query in template.type_id.lower():
            return True
        if query in template.display_name.lower():
            return True
        if query in template.description.lower():
            return True
        return any(query in tag.lower() for tag in template.tags)

    return [t for t in templates if _matches(t)]


def paginate(items: list, page: int | None, limit: int | None) -> tuple[list, int]:
    """Slice `items` for the given 1-indexed page/limit. Returns (page_items, total_count)."""
    total = len(items)
    page = max(1, page or DEFAULT_PAGE)
    limit = min(MAX_LIMIT, max(1, limit or DEFAULT_LIMIT))
    start = (page - 1) * limit
    end = start + limit
    return items[start:end], total
