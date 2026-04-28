"""
Cursor utilities for offset-based multi-partition pagination.

This module provides database-agnostic cursor encoding, decoding, and state management
for offset-based pagination across multiple partitions (connectors). The cursor 
encapsulates the complete state needed for navigation including per-partition offsets,
filters, sort settings, and parent context - enabling stateless pagination that works 
with shareable links and browser refresh.

Cursor Format:
{
    "dir": "next" | "prev",       # Navigation direction
    "sf": "sort_field",           # Sort field name
    "sd": "ASC" | "DESC",         # Sort direction
    "lm": 50,                     # Limit (page size)
    "src": [                      # Per-partition state (sources)
        # idx is 0-based: index of last item taken (next) or first item taken (prev)
        {"cid": "connector-id", "idx": 15},
        ...
    ],
    "seen": 50,                   # Items consumed so far (for next cursor; used for startIndex/endIndex)
    "f": {filters...},            # Optional filter state (compact keys)
    "pi": "parent_id",            # Optional parent context
    "pt": "parent_type",          # Optional parent type
    "tc": 1234                    # Cached total count
}

Filter key mapping (compact -> full):
    q  -> search_query
    nt -> node_types
    rt -> record_types
    or -> origins
    ci -> connector_ids
    is -> indexing_status
    ca -> created_at
    ua -> updated_at
    sz -> size
    oc -> only_containers
    fl -> flattened
"""

import base64
import json
import logging
from typing import Any, Dict, List, Optional, Tuple


def encode_cursor(data: Dict[str, Any]) -> str:
    """
    Encode cursor data to a base64 string.
    
    Args:
        data: Cursor data dictionary
        
    Returns:
        Base64 URL-safe encoded JSON string
    """
    json_str = json.dumps(data, separators=(',', ':'))
    return base64.urlsafe_b64encode(json_str.encode('utf-8')).decode('utf-8')


def decode_cursor(
    cursor: str,
    logger: Optional[logging.Logger] = None
) -> Optional[Dict[str, Any]]:
    """
    Decode a base64 cursor string to cursor data.
    
    Args:
        cursor: Base64 encoded cursor string
        logger: Optional logger for warnings
        
    Returns:
        Decoded cursor data dict, or None if invalid/empty
    """
    if not cursor:
        return None
    try:
        json_str = base64.urlsafe_b64decode(cursor.encode('utf-8')).decode('utf-8')
        return json.loads(json_str)
    except Exception as e:
        if logger:
            logger.warning(f"Failed to decode cursor: {e}")
        return None


def extract_filters_from_cursor(cursor_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract filter parameters from decoded cursor data.
    
    Converts compact cursor keys back to full parameter names for use in queries.
    
    Args:
        cursor_data: Decoded cursor data dictionary
        
    Returns:
        Dict with filter parameters in their original form
    """
    filters = cursor_data.get("f", {})
    
    return {
        "search_query": filters.get("q"),
        "node_types": filters.get("nt"),
        "record_types": filters.get("rt"),
        "origins": filters.get("or"),
        "connector_ids": filters.get("ci"),
        "indexing_status": filters.get("is"),
        "created_at": filters.get("ca"),
        "updated_at": filters.get("ua"),
        "size": filters.get("sz"),
        "only_containers": filters.get("oc", False),
        "flattened": filters.get("fl", False),
        "parent_id": cursor_data.get("pi"),
        "parent_type": cursor_data.get("pt"),
        "sort_field": cursor_data.get("sf"),
        "sort_dir": cursor_data.get("sd"),
        "limit": cursor_data.get("lm", 50),
        "total_items": cursor_data.get("tc"),
        "items_seen": cursor_data.get("seen", 0),  # 1-based display range: startIndex = items_seen + 1
    }


def extract_partition_offsets(cursor_data: Dict[str, Any]) -> Dict[str, int]:
    """
    Extract per-connector offsets from decoded cursor data.
    
    Args:
        cursor_data: Decoded cursor data dictionary
        
    Returns:
        Dict mapping connector_id to offset: {connector_id: offset}
    """
    sources = cursor_data.get("src", [])
    offsets = {}
    
    for source in sources:
        cid = source.get("cid")
        idx = source.get("idx", 0)
        if cid:
            offsets[cid] = idx
    
    return offsets


def build_partition_cursor_state(
    partition_ranges: Dict[str, Tuple[int, int]],
    direction: str,
    sort_field: str,
    sort_dir: str,
    limit: int = 50,
    search_query: Optional[str] = None,
    node_types: Optional[List[str]] = None,
    record_types: Optional[List[str]] = None,
    origins: Optional[List[str]] = None,
    connector_ids: Optional[List[str]] = None,
    indexing_status: Optional[List[str]] = None,
    created_at: Optional[Dict[str, Optional[int]]] = None,
    updated_at: Optional[Dict[str, Optional[int]]] = None,
    size: Optional[Dict[str, Optional[int]]] = None,
    only_containers: bool = False,
    parent_id: Optional[str] = None,
    parent_type: Optional[str] = None,
    flattened: bool = False,
    total_items: Optional[int] = None,
    base_offsets: Optional[Dict[str, int]] = None,
    items_seen_after_page: Optional[int] = None,
    items_seen_before_page: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build cursor state with per-partition offsets.
    
    Args:
        partition_ranges: Dict mapping connector_id to (first_idx, last_idx) taken from query results
            - (-1, -1) means connector is exhausted
            - (0, -1) means connector had items but none were selected
        direction: "next" or "prev" - determines how to compute offsets
        sort_field: Field used for sorting
        sort_dir: Sort direction ('ASC' or 'DESC')
        limit: Page size
        base_offsets: The offsets used for the current page query (to calculate new offsets)
        ... other filter params
        
    Returns:
        Cursor state dict ready for encoding
    """
    base_offsets = base_offsets or {}
    
    # Build sources array with computed offsets
    sources = []
    for cid, (first_idx, last_idx) in partition_ranges.items():
        base = base_offsets.get(cid, 0)
        
        if first_idx == -1 and last_idx == -1:
            # Connector exhausted
            offset = -1
        elif first_idx == 0 and last_idx == -1:
            # Connector had items but none selected - keep at current position
            offset = base
        else:
            if direction == "next":
                # For next cursor: store the absolute position of last item taken
                offset = base + last_idx
            else:  # prev
                # For prev cursor: store the absolute position of first item taken
                offset = base + first_idx
        
        sources.append({"cid": cid, "idx": offset})
    
    # Build filter state (compact keys)
    filters: Dict[str, Any] = {}
    if search_query:
        filters["q"] = search_query
    if node_types:
        filters["nt"] = node_types
    if record_types:
        filters["rt"] = record_types
    if origins:
        filters["or"] = origins
    if connector_ids:
        filters["ci"] = connector_ids
    if indexing_status:
        filters["is"] = indexing_status
    if created_at:
        filters["ca"] = created_at
    if updated_at:
        filters["ua"] = updated_at
    if size:
        filters["sz"] = size
    if only_containers:
        filters["oc"] = only_containers
    if flattened:
        filters["fl"] = flattened
    
    # Build cursor state
    cursor_state: Dict[str, Any] = {
        "dir": direction,
        "sf": sort_field,
        "sd": sort_dir,
        "lm": limit,
        "src": sources,
    }
    
    if total_items is not None:
        cursor_state["tc"] = total_items
    
    # Store "seen" = items consumed before the page returned when this cursor is used (for API startIndex/endIndex)
    if direction == "next" and items_seen_after_page is not None:
        cursor_state["seen"] = items_seen_after_page
    elif direction == "prev" and items_seen_before_page is not None:
        cursor_state["seen"] = items_seen_before_page

    if filters:
        cursor_state["f"] = filters
    
    if parent_id:
        cursor_state["pi"] = parent_id
    if parent_type:
        cursor_state["pt"] = parent_type
    
    return cursor_state


def build_partition_cursors(
    partition_ranges: Dict[str, Tuple[int, int]],
    is_first_page: bool,
    has_more_items: bool,
    sort_field: str,
    sort_dir: str,
    limit: int = 50,
    search_query: Optional[str] = None,
    node_types: Optional[List[str]] = None,
    record_types: Optional[List[str]] = None,
    origins: Optional[List[str]] = None,
    connector_ids: Optional[List[str]] = None,
    indexing_status: Optional[List[str]] = None,
    created_at: Optional[Dict[str, Optional[int]]] = None,
    updated_at: Optional[Dict[str, Optional[int]]] = None,
    size: Optional[Dict[str, Optional[int]]] = None,
    only_containers: bool = False,
    parent_id: Optional[str] = None,
    parent_type: Optional[str] = None,
    flattened: bool = False,
    total_items: Optional[int] = None,
    base_offsets: Optional[Dict[str, int]] = None,
    items_seen_after_page: Optional[int] = None,
    items_seen_before_prev_page: Optional[int] = None,
) -> Dict[str, Optional[str]]:
    """
    Build encoded next and previous cursor strings for partition-based pagination.
    
    Args:
        partition_ranges: Dict mapping connector_id to (first_idx, last_idx) from merge results
        is_first_page: Whether this is the first page
        has_more_items: Whether there are more items beyond current page
        base_offsets: The offsets used for the current page query
        items_seen_after_page: Total items consumed after this page (for next cursor; used for startIndex/endIndex)
        items_seen_before_prev_page: Items consumed before the previous page (for prev cursor; used for startIndex/endIndex when user goes prev)
        ... other params same as build_partition_cursor_state
        
    Returns:
        Dict with 'next' and 'prev' cursor strings (or None if not applicable)
    """
    if not partition_ranges:
        return {"next": None, "prev": None}
    
    base_offsets = base_offsets or {}
    
    # Build next cursor
    next_cursor = None
    if has_more_items:
        next_state = build_partition_cursor_state(
            partition_ranges=partition_ranges,
            direction="next",
            sort_field=sort_field,
            sort_dir=sort_dir,
            limit=limit,
            search_query=search_query,
            node_types=node_types,
            record_types=record_types,
            origins=origins,
            connector_ids=connector_ids,
            indexing_status=indexing_status,
            created_at=created_at,
            updated_at=updated_at,
            size=size,
            only_containers=only_containers,
            parent_id=parent_id,
            parent_type=parent_type,
            flattened=flattened,
            total_items=total_items,
            base_offsets=base_offsets,
            items_seen_after_page=items_seen_after_page,
        )
        next_cursor = encode_cursor(next_state)
    
    # Build prev cursor (only if not first page)
    prev_cursor = None
    at_beginning = all(offset == 0 for offset in base_offsets.values()) if base_offsets else True
    
    if not is_first_page and not at_beginning:
        prev_state = build_partition_cursor_state(
            partition_ranges=partition_ranges,
            direction="prev",
            sort_field=sort_field,
            sort_dir=sort_dir,
            limit=limit,
            search_query=search_query,
            node_types=node_types,
            record_types=record_types,
            origins=origins,
            connector_ids=connector_ids,
            indexing_status=indexing_status,
            created_at=created_at,
            updated_at=updated_at,
            size=size,
            only_containers=only_containers,
            parent_id=parent_id,
            parent_type=parent_type,
            flattened=flattened,
            total_items=total_items,
            base_offsets=base_offsets,
            items_seen_before_page=items_seen_before_prev_page,
        )
        prev_cursor = encode_cursor(prev_state)
    
    return {"next": next_cursor, "prev": prev_cursor}


def compute_query_offsets_for_prev(
    cursor_offsets: Dict[str, int],
    limit: int,
) -> Dict[str, int]:
    """
    Compute query offsets for prev page navigation.
    
    For prev page, we need to query items BEFORE the current cursor position.
    
    Args:
        cursor_offsets: Offsets from prev cursor (first_idx of current page)
        limit: Page size
        
    Returns:
        Dict with computed offsets for each connector's query
    """
    query_offsets = {}
    
    for cid, idx in cursor_offsets.items():
        if idx == -1:
            # Connector exhausted - skip
            query_offsets[cid] = -1
        elif idx == 0:
            # At beginning - can't go prev
            query_offsets[cid] = 0
        else:
            # Go back by limit, but not below 0
            query_offsets[cid] = max(0, idx - limit)
    
    return query_offsets
