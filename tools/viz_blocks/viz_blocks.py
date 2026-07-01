#!/usr/bin/env python3
"""
PipesHub Block Visualizer

A standalone tool to visualize BlocksContainer JSON from PipesHub records.
Renders a self-contained HTML file showing the block/group hierarchy, content,
and consistency checks.
"""

import argparse
import base64
import glob
import json
import os
import re
import sys
import webbrowser
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import dominate
    from dominate import document
    from dominate.tags import (
        details, div, h1, h2, h3, h4, img, li, ol, p, pre, span, style,
        summary, table, tbody, td, th, thead, tr, ul
    )
    from dominate.util import raw, text
except ImportError:
    print("Error: dominate is required. Install with: pip install dominate")
    sys.exit(1)

try:
    import markdown
except ImportError:
    print("Error: markdown is required. Install with: pip install markdown")
    sys.exit(1)


# =============================================================================
# CLI Interface
# =============================================================================

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Visualize PipesHub block containers as HTML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --org abc123 --record def456
  %(prog)s --path "C:\\path\\to\\record.json" --open
  %(prog)s --org abc123 --record def456 --output viz.html --max-text-len 500
        """
    )
    
    # Input source (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--org",
        help="Organization ID (requires --record)"
    )
    input_group.add_argument(
        "--path",
        help="Direct path to record JSON file"
    )
    
    parser.add_argument(
        "--record",
        help="Virtual record ID (requires --org)"
    )
    
    # Output options
    parser.add_argument(
        "--output",
        help="Output HTML file path (default: auto-generated next to input)"
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the HTML file in default browser after generation"
    )
    parser.add_argument(
        "--no-content",
        action="store_true",
        help="Show structure/IDs only, skip rendering data fields"
    )
    parser.add_argument(
        "--max-text-len",
        type=int,
        default=1000,
        help="Maximum text length before truncation (default: 1000)"
    )
    
    args = parser.parse_args()
    
    # Validate --org requires --record
    if args.org and not args.record:
        parser.error("--org requires --record")
    if args.record and not args.org:
        parser.error("--record requires --org")
    
    return args


# =============================================================================
# Path Resolution
# =============================================================================

def resolve_record_path(org_id: str, virtual_record_id: str) -> str:
    """
    Resolve the full path to a record JSON file from org and record IDs.
    
    Path pattern:
    %USERPROFILE%\\AppData\\PipesHub\\{orgId}\\PipesHub\\records\\
        {virtualRecordId}\\{documentId}\\current\\record_{virtualRecordId}.json
    """
    home = os.path.expanduser("~")
    pattern = os.path.join(
        home, "AppData", "PipesHub",
        org_id, "PipesHub", "records", virtual_record_id,
        "*", "current", f"record_{virtual_record_id}.json"
    )
    
    matches = glob.glob(pattern)
    
    if not matches:
        raise FileNotFoundError(
            f"No record JSON found for org={org_id}, record={virtual_record_id}\n"
            f"Searched: {pattern}\n"
            f"Make sure the record exists in local AppData storage."
        )
    
    if len(matches) > 1:
        # Pick most recent
        matches_with_time = [(p, os.path.getmtime(p)) for p in matches]
        matches_with_time.sort(key=lambda x: x[1], reverse=True)
        latest = matches_with_time[0][0]
        print(f"Found {len(matches)} matches, using most recent: {latest}")
        return latest
    
    return matches[0]


# =============================================================================
# JSON Loading + Decompression
# =============================================================================

def load_record_json(path: str) -> Dict[str, Any]:
    """
    Load and decompress a record JSON file.
    
    Handles both compressed and uncompressed formats.
    """
    with open(path, 'r', encoding='utf-8') as f:
        wrapper = json.load(f)
    
    # Check for wrapper envelope
    if "record" in wrapper and "virtualRecordId" in wrapper:
        if wrapper.get("isCompressed"):
            try:
                import zstandard
                import msgspec.msgpack
            except ImportError:
                raise ImportError(
                    "Record is compressed but required packages are missing.\n"
                    "Install with: pip install zstandard msgspec"
                )
            
            # Decompress: base64 -> zstd -> msgpack
            raw = base64.b64decode(wrapper["record"])
            decompressor = zstandard.ZstdDecompressor()
            decompressed = decompressor.decompress(raw)
            record = msgspec.msgpack.decode(decompressed)
        else:
            record = wrapper["record"]
    else:
        # Assume it's a raw record (testing scenario)
        record = wrapper
    
    return record


# =============================================================================
# Tree Builder
# =============================================================================

def resolve_children(group: Dict[str, Any]) -> Tuple[List[int], List[int]]:
    """
    Resolve child block and block_group indices from a group's children field.
    
    Handles both old list format and new range format.
    """
    children_raw = group.get("children")
    child_block_indices = []
    child_group_indices = []
    
    if not children_raw:
        return child_block_indices, child_group_indices
    
    # New range format
    if isinstance(children_raw, dict):
        for range_obj in children_raw.get("block_ranges", []):
            start = range_obj.get("start")
            end = range_obj.get("end")
            if start is not None and end is not None:
                child_block_indices.extend(range(start, end + 1))
        
        for range_obj in children_raw.get("block_group_ranges", []):
            start = range_obj.get("start")
            end = range_obj.get("end")
            if start is not None and end is not None:
                child_group_indices.extend(range(start, end + 1))
    
    # Old list format
    elif isinstance(children_raw, list):
        for child in children_raw:
            if isinstance(child, dict):
                if child.get("block_index") is not None:
                    child_block_indices.append(child["block_index"])
                if child.get("block_group_index") is not None:
                    child_group_indices.append(child["block_group_index"])
    
    return child_block_indices, child_group_indices


def build_tree(
    block_groups: List[Dict[str, Any]],
    blocks: List[Dict[str, Any]]
) -> Tuple[List[Dict], List[Dict], Dict[int, Dict], Dict[int, Dict]]:
    """
    Build tree structure from flat blocks/block_groups arrays.
    
    Returns:
        (root_groups, orphan_blocks, groups_by_index, blocks_by_index)
    """
    groups_by_index = {g.get("index"): g for g in block_groups if g.get("index") is not None}
    blocks_by_index = {b.get("index"): b for b in blocks if b.get("index") is not None}
    
    root_groups = [g for g in block_groups if g.get("parent_index") is None]
    orphan_blocks = [b for b in blocks if b.get("parent_index") is None]
    
    return root_groups, orphan_blocks, groups_by_index, blocks_by_index


def _group_min_block_index(
    group: Dict[str, Any],
    groups_by_index: Dict[int, Dict],
    blocks_by_index: Dict[int, Dict],
    cache: Dict[int, int],
) -> int:
    """
    Earliest block index in a group's subtree.

    Groups are containers over blocks; document order follows block indices.
    Falls back to the group index when the subtree has no blocks (e.g. comment threads).
    """
    group_index = group.get("index")
    if group_index is None:
        return 10**9
    if group_index in cache:
        return cache[group_index]

    child_block_indices, child_group_indices = resolve_children(group)
    candidates: List[int] = []

    if child_block_indices:
        candidates.append(min(child_block_indices))

    for gi in child_group_indices:
        if gi in groups_by_index:
            candidates.append(
                _group_min_block_index(
                    groups_by_index[gi],
                    groups_by_index,
                    blocks_by_index,
                    cache,
                )
            )

    result = min(candidates) if candidates else group_index
    cache[group_index] = result
    return result


def sort_groups_by_block_order(
    group_indices: List[int],
    groups_by_index: Dict[int, Dict],
    blocks_by_index: Dict[int, Dict],
    cache: Optional[Dict[int, int]] = None,
) -> List[int]:
    """Sort block group indices by the earliest block they contain."""
    if cache is None:
        cache = {}
    return sorted(
        group_indices,
        key=lambda gi: (
            _group_min_block_index(
                groups_by_index[gi],
                groups_by_index,
                blocks_by_index,
                cache,
            ),
            gi,
        ),
    )


def merge_children_by_block_order(
    child_block_indices: List[int],
    child_group_indices: List[int],
    groups_by_index: Dict[int, Dict],
    blocks_by_index: Dict[int, Dict],
    cache: Optional[Dict[int, int]] = None,
) -> List[Tuple[str, int]]:
    """
    Interleave direct child blocks and child groups in block-stream order.

    Returns a list of ('block', index) or ('group', index) tuples.
    """
    if cache is None:
        cache = {}

    ordered: List[Tuple[int, int, str, int]] = []
    for bi in child_block_indices:
        ordered.append((bi, 0, "block", bi))
    for gi in child_group_indices:
        if gi not in groups_by_index:
            continue
        ordered.append(
            (
                _group_min_block_index(
                    groups_by_index[gi],
                    groups_by_index,
                    blocks_by_index,
                    cache,
                ),
                1,
                "group",
                gi,
            )
        )

    ordered.sort(key=lambda item: (item[0], item[1], item[3]))
    return [(kind, idx) for _, _, kind, idx in ordered]


def collect_blocks_in_group(
    group: Dict[str, Any],
    groups_by_index: Dict[int, Dict],
    block_cache: Optional[Dict[int, Set[int]]] = None,
) -> Set[int]:
    """Collect all block indices owned by a group subtree."""
    if block_cache is None:
        block_cache = {}
    group_index = group.get("index")
    if group_index is not None and group_index in block_cache:
        return block_cache[group_index]

    child_block_indices, child_group_indices = resolve_children(group)
    owned: Set[int] = set(child_block_indices)
    for gi in child_group_indices:
        if gi in groups_by_index:
            owned.update(
                collect_blocks_in_group(
                    groups_by_index[gi], groups_by_index, block_cache
                )
            )

    if group_index is not None:
        block_cache[group_index] = owned
    return owned


OwnerKey = Tuple[str, int]


def root_owner_for_block(
    block: Dict[str, Any],
    groups_by_index: Dict[int, Dict],
) -> OwnerKey:
    """
    Top-level document owner for a block: either the block itself or the rootmost
    group wrapping it (parent_index=None ancestor).
    """
    block_index = block.get("index")
    if block_index is None:
        block_index = -1

    parent_gi = block.get("parent_index")
    if parent_gi is None:
        return ("block", block_index)

    gi = parent_gi
    while gi in groups_by_index:
        group = groups_by_index[gi]
        parent = group.get("parent_index")
        if parent is None:
            return ("group", gi)
        gi = parent

    return ("block", block_index)


def first_block_index_for_owner(
    owner: OwnerKey,
    groups_by_index: Dict[int, Dict],
    blocks_by_index: Dict[int, Dict],
    min_block_cache: Dict[int, int],
) -> int:
    """Block index where this owner should appear in the document stream."""
    if owner[0] == "block":
        return owner[1]
    group = groups_by_index.get(owner[1])
    if not group:
        return owner[1]
    return _group_min_block_index(
        group, groups_by_index, blocks_by_index, min_block_cache
    )


def collect_inlined_block_indices(blocks_by_index: Dict[int, Dict]) -> Set[int]:
    """Block indices rendered inside table cells via cell_details.block_indices."""
    inlined: Set[int] = set()
    for block in blocks_by_index.values():
        if block.get("type") != "table_row":
            continue
        data = block.get("data")
        if not isinstance(data, dict):
            continue
        for detail in data.get("cell_details") or []:
            if isinstance(detail, dict):
                inlined.update(detail.get("block_indices") or [])
    return inlined


def render_content_in_block_order(
    content_div: div,
    block_groups: List[Dict[str, Any]],
    blocks_by_index: Dict[int, Dict],
    groups_by_index: Dict[int, Dict],
    max_len: int,
    no_content: bool,
    visited: Set[int],
) -> None:
    """
    Render the document as a single stream in block index order (0 .. n-1).

    Groups are wrappers: a group appears when the walk reaches the first block
    in its subtree. Blocks without a parent group render as themselves.
    """
    min_block_cache: Dict[int, int] = {}
    block_ownership_cache: Dict[int, Set[int]] = {}
    rendered_owners: Set[OwnerKey] = set()
    inlined_block_indices = collect_inlined_block_indices(blocks_by_index)

    for bi in sorted(blocks_by_index.keys()):
        if bi in inlined_block_indices:
            continue

        block = blocks_by_index[bi]
        owner = root_owner_for_block(block, groups_by_index)
        if owner in rendered_owners:
            continue

        first_bi = first_block_index_for_owner(
            owner, groups_by_index, blocks_by_index, min_block_cache
        )
        if bi != first_bi:
            continue

        rendered_owners.add(owner)
        if owner[0] == "block":
            content_div.add(
                render_block(block, depth=1, max_len=max_len, no_content=no_content)
            )
        else:
            group = groups_by_index.get(owner[1])
            if group:
                content_div.add(
                    render_group(
                        group,
                        groups_by_index,
                        blocks_by_index,
                        depth=1,
                        max_len=max_len,
                        no_content=no_content,
                        visited=visited,
                    )
                )

    # Root groups with no blocks at all (e.g. comment threads with only group data)
    blockless_roots = []
    for group in block_groups:
        if group.get("parent_index") is not None:
            continue
        owner: OwnerKey = ("group", group.get("index"))
        if owner in rendered_owners:
            continue
        if collect_blocks_in_group(group, groups_by_index, block_ownership_cache):
            continue
        blockless_roots.append(group)

    for group in sorted(blockless_roots, key=lambda g: g.get("index", 10**9)):
        owner = ("group", group.get("index"))
        if owner in rendered_owners:
            continue
        rendered_owners.add(owner)
        content_div.add(
            render_group(
                group,
                groups_by_index,
                blocks_by_index,
                depth=1,
                max_len=max_len,
                no_content=no_content,
                visited=visited,
            )
        )


# =============================================================================
# Consistency Validator
# =============================================================================

def is_base64_image(s: str) -> bool:
    """Check if string is a valid base64-encoded image."""
    if not isinstance(s, str) or not s.strip():
        return False
    
    # Handle data URL format
    data_url_pattern = r'^data:image/(png|jpeg|jpg|gif|webp|bmp|svg\+xml|tiff);base64,(.+)$'
    match = re.match(data_url_pattern, s.strip(), re.IGNORECASE)
    
    if match:
        b64_data = match.group(2)
    else:
        b64_data = s.strip()
    
    # Validate base64 characters
    if not re.match(r'^[A-Za-z0-9+/]*={0,2}$', b64_data):
        return False
    
    # Check padding
    if len(b64_data) % 4 != 0:
        return False
    
    # Try to decode
    try:
        base64.b64decode(b64_data)
        return True
    except Exception:
        return False


def is_valid_url(s: str) -> bool:
    """Check if string is a valid URL."""
    if not isinstance(s, str):
        return False
    return s.startswith(('http://', 'https://'))


def validate_consistency(
    block_groups: List[Dict[str, Any]],
    blocks: List[Dict[str, Any]],
    groups_by_index: Dict[int, Dict],
    blocks_by_index: Dict[int, Dict]
) -> None:
    """
    Run consistency checks and attach warnings to blocks/groups.
    
    Modifies objects in place by adding a '_warnings' list.
    """
    # Check blocks
    for block in blocks:
        warnings = []
        block_index = block.get("index")
        parent_index = block.get("parent_index")
        
        # Check parent_index points to valid group
        if parent_index is not None:
            if parent_index not in groups_by_index:
                warnings.append(f"parent_index {parent_index} not found in block_groups")
        
        # Check image URIs
        if block.get("type") == "image":
            data = block.get("data")
            if isinstance(data, dict):
                uri = data.get("uri", "")
                if uri and not is_base64_image(uri) and not is_valid_url(uri):
                    warnings.append("Invalid image URI (not base64 or URL)")
        
        block["_warnings"] = warnings
    
    # Check groups
    for group in block_groups:
        warnings = []
        group_index = group.get("index")
        parent_index = group.get("parent_index")
        
        # Check parent_index points to valid group
        if parent_index is not None:
            if parent_index not in groups_by_index:
                warnings.append(f"parent_index {parent_index} not found in block_groups")
        
        # Check children references
        child_block_indices, child_group_indices = resolve_children(group)
        
        for bi in child_block_indices:
            if bi >= len(blocks):
                warnings.append(f"child block index {bi} >= len(blocks) {len(blocks)}")
            elif bi in blocks_by_index:
                # Check if child's parent_index matches this group
                child_block = blocks_by_index[bi]
                if child_block.get("parent_index") != group_index:
                    warnings.append(
                        f"child block {bi} has parent_index={child_block.get('parent_index')}, "
                        f"expected {group_index}"
                    )
        
        for gi in child_group_indices:
            if gi >= len(block_groups):
                warnings.append(f"child group index {gi} >= len(block_groups) {len(block_groups)}")
            elif gi in groups_by_index:
                # Check if child's parent_index matches this group
                child_group = groups_by_index[gi]
                if child_group.get("parent_index") != group_index:
                    warnings.append(
                        f"child group {gi} has parent_index={child_group.get('parent_index')}, "
                        f"expected {group_index}"
                    )
        
        group["_warnings"] = warnings


# =============================================================================
# HTML Renderer - CSS
# =============================================================================

def get_css() -> str:
    """Generate inline CSS for the visualization."""
    return """
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.5;
            color: #1f2937;
            background: #f9fafb;
            margin: 0;
            padding: 0;
        }
        .summary-panel {
            position: sticky;
            top: 0;
            background: white;
            border-bottom: 1px solid #e5e7eb;
            padding: 8px 12px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.06);
            z-index: 100;
            line-height: 1.35;
        }
        .summary-panel h1 {
            margin: 0 0 6px 0;
            font-size: 16px;
            font-weight: 600;
            color: #111827;
        }
        .summary-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 4px 14px;
            margin-bottom: 4px;
        }
        .summary-meta-item {
            display: inline-flex;
            align-items: baseline;
            gap: 4px;
            font-size: 12px;
        }
        .summary-meta-label {
            font-size: 10px;
            text-transform: uppercase;
            color: #6b7280;
            font-weight: 600;
            margin-bottom: 0;
        }
        .summary-meta-value {
            font-size: 12px;
            color: #111827;
        }
        .summary-stats {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            align-items: center;
            margin-top: 4px;
            padding-top: 4px;
            border-top: 1px solid #f3f4f6;
            font-size: 12px;
        }
        .summary-stat {
            font-size: 12px;
            color: #4b5563;
        }
        .summary-stat strong {
            color: #111827;
            font-weight: 600;
        }
        .warning-count {
            color: #dc2626;
            font-weight: 600;
        }
        .content-area {
            padding: 8px;
            max-width: 1400px;
            margin: 0 auto;
        }
        
        /* Block and Group Cards */
        .block-group, .block {
            background: white;
            border-radius: 4px;
            margin: 3px 0;
            padding: 6px 8px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.06);
            border-left: 3px solid #cbd5e1;
        }
        .block-group {
            border-left-width: 4px;
        }
        .block-group > .block-group,
        .block-group > .block {
            margin: 2px 0;
        }
        
        /* Depth-based indentation (compact) */
        .depth-1 { padding-left: 8px; border-left-color: #3b82f6; }
        .depth-2 { padding-left: 16px; border-left-color: #8b5cf6; }
        .depth-3 { padding-left: 24px; border-left-color: #ec4899; }
        .depth-4 { padding-left: 32px; border-left-color: #f59e0b; }
        .depth-5 { padding-left: 40px; border-left-color: #10b981; }
        .depth-6 { padding-left: 48px; border-left-color: #06b6d4; }
        
        /* Type colors */
        .type-text_section { border-left-color: #3b82f6; }
        .type-table { border-left-color: #10b981; }
        .type-list, .type-ordered_list { border-left-color: #f59e0b; }
        .type-sheet { border-left-color: #8b5cf6; }
        .type-commits, .type-patch { border-left-color: #6b7280; }
        .type-comment, .type-comment_thread { background: #fef3c7; border-left-color: #f59e0b; }

        .block-comments {
            margin: 8px 0 12px 0;
            padding: 10px 12px;
            background: #fffbeb;
            border: 1px solid #fcd34d;
            border-radius: 6px;
        }
        .comment-thread-inline {
            margin-top: 8px;
            padding-top: 8px;
            border-top: 1px dashed #f59e0b;
        }
        .comment-thread-inline:first-of-type {
            border-top: none;
            margin-top: 0;
            padding-top: 0;
        }
        .comment-inline-header {
            font-size: 12px;
            font-weight: 600;
            color: #92400e;
            margin-bottom: 4px;
        }
        .comment-quoted {
            font-size: 12px;
            color: #78350f;
            font-style: italic;
            margin-bottom: 6px;
        }
        
        .header {
            display: flex;
            align-items: center;
            gap: 4px;
            margin-bottom: 2px;
            flex-wrap: wrap;
        }
        .badge {
            display: inline-block;
            padding: 1px 6px;
            border-radius: 3px;
            font-size: 12px;
            font-weight: 600;
            font-family: 'Courier New', monospace;
            line-height: 1.4;
        }
        .badge-type {
            background: #dbeafe;
            color: #1e40af;
        }
        .badge-id {
            background: #f3f4f6;
            color: #4b5563;
            font-size: 11px;
        }
        .badge-warning {
            background: #fee2e2;
            color: #991b1b;
        }
        .badge-block-text { background: #dbeafe; color: #1e3a8a; }
        .badge-block-image { background: #fce7f3; color: #9f1239; }
        .badge-block-table-row { background: #d1fae5; color: #065f46; }
        .badge-block-record-summary { background: #ede9fe; color: #5b21b6; }
        
        .meta-line {
            font-size: 13px;
            color: #6b7280;
            margin: 1px 0 2px 0;
        }
        .meta-line strong {
            color: #374151;
        }
        .content {
            margin-top: 2px;
            color: #374151;
        }
        .content p {
            margin: 0.15em 0;
        }
        .content img {
            max-width: 100%;
            height: auto;
            border-radius: 4px;
            margin: 4px 0;
        }
        .content pre {
            background: #f9fafb;
            border: 1px solid #e5e7eb;
            border-radius: 4px;
            padding: 6px 8px;
            overflow-x: auto;
            font-size: 13px;
            margin: 2px 0;
        }
        .content table {
            width: 100%;
            border-collapse: collapse;
            margin: 4px 0;
        }
        .content table th,
        .content table td {
            border: 1px solid #e5e7eb;
            padding: 4px 8px;
            text-align: left;
        }
        .content table th {
            background: #f9fafb;
            font-weight: 600;
        }
        .truncated {
            color: #9ca3af;
            font-style: italic;
            margin-top: 2px;
        }
        .no-content {
            color: #9ca3af;
            font-style: italic;
            margin: 0;
        }
        .broken-image {
            background: #fee2e2;
            color: #991b1b;
            padding: 6px 8px;
            border-radius: 4px;
            font-weight: 600;
        }
        .orphan-section {
            margin-top: 12px;
            padding-top: 12px;
            border-top: 1px dashed #e5e7eb;
        }
        .orphan-section h2 {
            color: #6b7280;
            font-size: 18px;
            margin: 0 0 6px 0;
        }
        details {
            margin-top: 16px;
        }
        summary {
            cursor: pointer;
            font-weight: 600;
            color: #4b5563;
            padding: 8px;
            background: #f9fafb;
            border-radius: 4px;
        }
        summary:hover {
            background: #f3f4f6;
        }
        .nested-cell-content {
            margin-top: 8px;
            padding: 8px;
            background: #f9fafb;
            border: 1px dashed #cbd5e1;
            border-radius: 4px;
        }
        .nested-cell-content .block-group {
            margin: 0;
            box-shadow: none;
            padding: 8px;
        }
        .nested-cell-content .content table {
            font-size: 13px;
        }
        .nested-cell-label {
            font-size: 11px;
            color: #6b7280;
            font-weight: 600;
            text-transform: uppercase;
            margin-bottom: 4px;
        }
        .table-nested-groups {
            margin-top: 16px;
            padding-top: 12px;
            border-top: 1px dashed #e5e7eb;
        }
        .meta-nl-text {
            margin-left: 12px;
        }
    """


# =============================================================================
# HTML Renderer - Components
# =============================================================================

def render_badge(label: str, badge_class: str = "badge-type") -> span:
    """Render a badge."""
    return span(label, cls=f"badge {badge_class}")


def render_warnings(warnings: List[str]) -> List:
    """Render warning badges."""
    return [render_badge(f"⚠ {w}", "badge-warning") for w in warnings]


def truncate_text(text: str, max_len: int) -> Tuple[str, bool]:
    """Truncate text if longer than max_len."""
    if len(text) <= max_len:
        return text, False
    return text[:max_len], True


def render_markdown_content(data: str, max_len: int) -> List:
    """Render markdown content."""
    truncated_data, was_truncated = truncate_text(data, max_len)
    
    try:
        html = markdown.markdown(truncated_data, extensions=['fenced_code', 'tables'])
        elements = [raw(html)]
        if was_truncated:
            elements.append(p("... (truncated)", cls="truncated"))
        return elements
    except Exception:
        # Fallback to plain text
        elements = [p(truncated_data)]
        if was_truncated:
            elements.append(p("... (truncated)", cls="truncated"))
        return elements


def render_block_content(
    block: Dict[str, Any],
    max_len: int,
    no_content: bool
) -> List:
    """Render block content based on type and format."""
    if no_content:
        return [p("[content hidden]", cls="no-content")]
    
    block_type = block.get("type", "")
    data = block.get("data")
    format_type = block.get("format", "")
    
    if data is None:
        return [p("[no content]", cls="no-content")]
    
    # IMAGE block
    if block_type == "image":
        if isinstance(data, dict):
            uri = data.get("uri", "")
            if uri and (is_base64_image(uri) or is_valid_url(uri)):
                return [img(src=uri, style="max-width:600px")]
            else:
                return [div("[BROKEN IMAGE]", cls="broken-image")]
        return [div("[INVALID IMAGE DATA]", cls="broken-image")]
    
    # TABLE_ROW block (will be handled by parent table group)
    if block_type == "table_row":
        if isinstance(data, dict):
            row_text = data.get("row_natural_language_text", "")
            if row_text:
                return [p(row_text)]
        return [p(str(data))]
    
    # TEXT blocks - check format
    if format_type in ("markdown", "md"):
        if isinstance(data, str):
            return render_markdown_content(data, max_len)
    
    # JSON format
    if format_type == "json" or isinstance(data, dict):
        json_str = json.dumps(data, indent=2)
        truncated_str, was_truncated = truncate_text(json_str, max_len)
        elements = [pre(truncated_str)]
        if was_truncated:
            elements.append(p("... (truncated)", cls="truncated"))
        return elements
    
    # Plain text
    if isinstance(data, str):
        truncated_data, was_truncated = truncate_text(data, max_len)
        elements = [p(truncated_data)]
        if was_truncated:
            elements.append(p("... (truncated)", cls="truncated"))
        return elements
    
    # Fallback
    return [p(str(data))]


def render_citation_meta(block: Dict[str, Any]) -> Optional[str]:
    """Render citation metadata if present (page, bbox)."""
    citation = block.get("citation_metadata")
    if not citation:
        return None
    
    parts = []
    page = citation.get("page_number")
    if page:
        parts.append(f"📄 p.{page}")
    
    bbox = citation.get("bounding_boxes")
    if bbox and len(bbox) >= 2:
        # Show first and third points (top-left and bottom-right corners)
        p0 = bbox[0]
        p2 = bbox[2] if len(bbox) > 2 else bbox[1]
        parts.append(f"bbox: ({p0.get('x', 0):.2f}, {p0.get('y', 0):.2f})→({p2.get('x', 0):.2f}, {p2.get('y', 0):.2f})")
    
    return "  ".join(parts) if parts else None


def render_block_comments(block: Dict[str, Any], max_len: int) -> Optional[div]:
    """Render inline comment threads stored on a block."""
    comments = block.get("comments")
    if not comments:
        return None

    section = div(cls="block-comments")
    with section:
        div("Inline comments", cls="nested-cell-label")

        for thread in comments:
            if not isinstance(thread, list) or not thread:
                continue

            thread_div = div(cls="comment-thread-inline")
            with thread_div:
                for comment in thread:
                    if not isinstance(comment, dict):
                        continue

                    author = (
                        comment.get("author_name")
                        or comment.get("author_id")
                        or "Unknown"
                    )
                    body = comment.get("text") or ""
                    truncated, was_trunc = truncate_text(str(body), max_len)

                    header_div = div(cls="comment-inline-header")
                    with header_div:
                        span(str(author), cls="comment-author")
                        status = comment.get("resolution_status")
                        if status:
                            span(f" [{status}]", cls="comment-status")

                    quoted = comment.get("quoted_text")
                    if quoted:
                        div(f'On: "{quoted}"', cls="comment-quoted")

                    if truncated.strip():
                        for elem in render_markdown_content(truncated, max_len):
                            thread_div.add(elem)
                    elif not quoted:
                        p("(empty comment)", cls="truncated")

                    if was_trunc:
                        p("... (truncated)", cls="truncated")

    return section


def render_block(
    block: Dict[str, Any],
    depth: int,
    max_len: int,
    no_content: bool
) -> div:
    """Render a single block."""
    block_type = block.get("type", "unknown")
    block_id = block.get("id", "")
    block_index = block.get("index")
    warnings = block.get("_warnings", [])
    
    container = div(cls=f"block depth-{min(depth, 6)} badge-block-{block_type.replace('_', '-')}")
    
    with container:
        # Header
        header_div = div(cls="header")
        with header_div:
            render_badge(f"block[{block_index}] {block_type}", "badge-type")
            render_badge(f"id: {block_id[:8]}", "badge-id")
            
            # Warnings
            for warning in warnings:
                render_badge(f"⚠ {warning}", "badge-warning")
        
        # Citation metadata
        citation_str = render_citation_meta(block)
        if citation_str:
            div(citation_str, cls="meta-line")

        # Inline comments attached to this block
        if not no_content and block.get("comments"):
            comments_section = render_block_comments(block, max_len)
            if comments_section is not None:
                container.add(comments_section)

        # Content
        content_div = div(cls="content")
        with content_div:
            for element in render_block_content(block, max_len, no_content):
                content_div.add(element)
    
    return container


def _get_row_natural_language_text(block: Dict[str, Any]) -> Optional[str]:
    """Return row_natural_language_text from a TABLE_ROW block."""
    data = block.get("data", {})
    if not isinstance(data, dict):
        return None
    row_text = data.get("row_natural_language_text")
    if row_text is None or not str(row_text).strip():
        return None
    return str(row_text).strip()


def _get_row_number(block: Dict[str, Any]) -> Optional[int]:
    """Return row number from a TABLE_ROW block."""
    row_meta = block.get("table_row_metadata", {}) or {}
    row_number = row_meta.get("row_number")
    if row_number is None and isinstance(block.get("data"), dict):
        row_number = block["data"].get("row_number")
    return row_number


def _append_table_group_meta(
    group: Dict[str, Any],
    child_block_indices: List[int],
    blocks_by_index: Dict[int, Dict],
) -> None:
    """Show summary, columns, and row/col counts for a TABLE group."""
    data = group.get("data", {})
    summary = None
    column_headers = None
    if isinstance(data, dict):
        summary = data.get("table_summary")
        column_headers = data.get("column_headers")
    if not summary:
        summary = group.get("description")
    if summary:
        div(f"Summary: {summary}", cls="meta-line")
    if column_headers:
        div(f"Columns: {', '.join(str(h) for h in column_headers)}", cls="meta-line")

    table_meta = group.get("table_metadata", {})
    if table_meta:
        parts = []
        if table_meta.get("num_of_rows") is not None:
            parts.append(f"rows: {table_meta['num_of_rows']}")
        if table_meta.get("num_of_cols") is not None:
            parts.append(f"cols: {table_meta['num_of_cols']}")
        if parts:
            div("  ".join(parts), cls="meta-line")

    row_lines: List[Tuple[Optional[int], str]] = []
    for bi in child_block_indices:
        block = blocks_by_index.get(bi)
        if not block or block.get("type") != "table_row":
            continue
        row_text = _get_row_natural_language_text(block)
        if row_text:
            row_lines.append((_get_row_number(block), row_text))

    if row_lines:
        div("Natural language text:", cls="meta-line")
        for fallback_idx, (row_number, row_text) in enumerate(row_lines, start=1):
            label = row_number if row_number is not None else fallback_idx
            div(f"row {label}: {row_text}", cls="meta-line meta-nl-text")


def _append_group_summary_meta(parent, group: Dict[str, Any]) -> None:
    """Show description/summary for a non-table nested group."""
    description = group.get("description")
    if description:
        div(f"Summary: {description}", cls="meta-line")


def _get_row_cells_and_details(
    block: Dict[str, Any],
) -> Tuple[List[str], List[Optional[Dict[str, Any]]]]:
    """Extract string cell values and optional per-cell detail metadata from a TABLE_ROW block."""
    block_data = block.get("data", {})
    if not isinstance(block_data, dict):
        return [str(block_data)], []

    cells = block_data.get("cells", [])
    cell_details = block_data.get("cell_details")

    if cell_details:
        texts = [str(c) if not isinstance(c, dict) else str(c.get("text", "")) for c in cells]
        return texts, cell_details

    texts: List[str] = []
    details: List[Optional[Dict[str, Any]]] = []
    for cell in cells:
        if isinstance(cell, dict):
            texts.append(str(cell.get("text", "")))
            details.append(cell)
        else:
            texts.append(str(cell))
            details.append(None)
    return texts, details


def _collect_inlined_group_indices(
    child_block_indices: List[int],
    blocks_by_index: Dict[int, Dict],
) -> Set[int]:
    """Group indices referenced inline via row cell_details (nested cells)."""
    inlined: Set[int] = set()
    for bi in child_block_indices:
        block = blocks_by_index.get(bi)
        if not block:
            continue
        _, cell_details = _get_row_cells_and_details(block)
        for detail in cell_details:
            if isinstance(detail, dict) and detail.get("type") == "nested":
                inlined.update(detail.get("block_group_indices") or [])
    return inlined


def _populate_table_cell(
    cell_container,
    cell_text: str,
    cell_detail: Optional[Dict[str, Any]],
    groups_by_index: Dict[int, Dict],
    blocks_by_index: Dict[int, Dict],
    depth: int,
    max_len: int,
    no_content: bool,
    visited: Set[int],
) -> None:
    """Fill a table cell with text and any nested blocks/groups from cell_details."""
    if no_content:
        cell_container.add(text(cell_text or ""))
        return

    is_nested = isinstance(cell_detail, dict) and cell_detail.get("type") == "nested"
    nested_groups = (cell_detail or {}).get("block_group_indices") or []
    nested_blocks = (cell_detail or {}).get("block_indices") or []

    if not is_nested and not nested_groups and not nested_blocks:
        cell_container.add(text(cell_text or ""))
        return

    if cell_text:
        cell_container.add(text(cell_text))

    if is_nested and (nested_groups or nested_blocks):
        nested_wrap = div(cls="nested-cell-content")
        cell_container.add(nested_wrap)
        with nested_wrap:
            div("Nested content", cls="nested-cell-label")
            for gi in sort_groups_by_block_order(
                [g for g in nested_groups if g in groups_by_index],
                groups_by_index,
                blocks_by_index,
            ):
                if gi in groups_by_index:
                    nested_wrap.add(
                        _render_inline_nested_group(
                            groups_by_index[gi],
                            groups_by_index,
                            blocks_by_index,
                            depth,
                            max_len,
                            no_content,
                            visited,
                        )
                    )
            for bi in nested_blocks:
                if bi in blocks_by_index:
                    nested_wrap.add(
                        render_block(blocks_by_index[bi], depth + 1, max_len, no_content)
                    )


def _render_inline_nested_group(
    group: Dict[str, Any],
    groups_by_index: Dict[int, Dict],
    blocks_by_index: Dict[int, Dict],
    depth: int,
    max_len: int,
    no_content: bool,
    visited: Set[int],
) -> div:
    """Render a nested block group inside a table cell."""
    if group.get("type") == "table":
        return render_table_group(
            group,
            groups_by_index,
            blocks_by_index,
            depth + 1,
            max_len,
            no_content,
            visited,
            nested=True,
        )

    wrapper = div(cls="nested-inline-group")
    with wrapper:
        _append_group_summary_meta(wrapper, group)
        wrapper.add(
            render_group(
                group,
                groups_by_index,
                blocks_by_index,
                depth + 1,
                max_len,
                no_content,
                visited,
            )
        )
    return wrapper


def render_table_group(
    group: Dict[str, Any],
    groups_by_index: Dict[int, Dict],
    blocks_by_index: Dict[int, Dict],
    depth: int,
    max_len: int,
    no_content: bool,
    visited: Set[int],
    nested: bool = False,
) -> div:
    """Render a TABLE group with its rows as a table element."""
    group_index = group.get("index")
    if group_index in visited:
        container = div(cls="block-group type-table")
        with container:
            render_badge("⚠ CIRCULAR TABLE REFERENCE", "badge-warning")
        return container

    visited.add(group_index)

    depth_class = f"depth-{min(depth, 6)}"
    container = div(cls=f"block-group type-table {depth_class}")
    
    with container:
        group_id = group.get("id", "")
        warnings = group.get("_warnings", [])
        
        header_div = div(cls="header")
        with header_div:
            label = f"group[{group_index}] table"
            if nested:
                label += " (nested)"
            render_badge(label, "badge-type")
            render_badge(f"id: {group_id[:8]}", "badge-id")
            for warning in warnings:
                render_badge(f"⚠ {warning}", "badge-warning")

        child_block_indices, child_group_indices = resolve_children(group)
        _append_table_group_meta(group, child_block_indices, blocks_by_index)
        inlined_group_indices = _collect_inlined_group_indices(
            child_block_indices, blocks_by_index
        )

        if not no_content and child_block_indices:
            content_div = div(cls="content")
            with content_div:
                table_elem = table()
                with table_elem:
                    tbody_elem = tbody()
                    with tbody_elem:
                        for bi in child_block_indices:
                            if bi not in blocks_by_index:
                                continue
                            block = blocks_by_index[bi]
                            row_meta = block.get("table_row_metadata", {})
                            is_header = row_meta.get("is_header", False)
                            cells, cell_details = _get_row_cells_and_details(block)

                            row_elem = tr()
                            with row_elem:
                                for col_idx, cell_text in enumerate(cells):
                                    detail = (
                                        cell_details[col_idx]
                                        if col_idx < len(cell_details)
                                        else None
                                    )
                                    cell_tag = th if is_header else td
                                    cell_elem = cell_tag()
                                    with cell_elem:
                                        _populate_table_cell(
                                            cell_elem,
                                            cell_text,
                                            detail,
                                            groups_by_index,
                                            blocks_by_index,
                                            depth,
                                            max_len,
                                            no_content,
                                            visited,
                                        )

        # Child groups not already rendered inside a cell (fallback placement)
        orphan_nested = sort_groups_by_block_order(
            [
                gi for gi in child_group_indices
                if gi not in inlined_group_indices and gi in groups_by_index
            ],
            groups_by_index,
            blocks_by_index,
        )
        if orphan_nested:
            nested_section = div(cls="table-nested-groups")
            container.add(nested_section)
            with nested_section:
                if not nested:
                    div("Nested block groups", cls="nested-cell-label")
                for gi in orphan_nested:
                    nested_section.add(
                        _render_inline_nested_group(
                            groups_by_index[gi],
                            groups_by_index,
                            blocks_by_index,
                            depth,
                            max_len,
                            no_content,
                            visited,
                        )
                    )

    visited.discard(group_index)
    return container


def render_group(
    group: Dict[str, Any],
    groups_by_index: Dict[int, Dict],
    blocks_by_index: Dict[int, Dict],
    depth: int,
    max_len: int,
    no_content: bool,
    visited: Set[int]
) -> div:
    """Recursively render a block group and its children."""
    group_index = group.get("index")
    
    # Detect circular references
    if group_index in visited:
        container = div(cls="block-group")
        with container:
            render_badge("⚠ CIRCULAR REFERENCE", "badge-warning")
        return container
    
    group_type = group.get("type", "unknown")
    sub_type = group.get("sub_type")
    
    # TABLE groups manage their own visited set (including inline nested tables)
    if group_type == "table":
        return render_table_group(
            group,
            groups_by_index,
            blocks_by_index,
            depth,
            max_len,
            no_content,
            visited,
        )
    
    visited.add(group_index)
    
    # Regular group
    type_class = f"type-{group_type.replace('_', '-')}"
    if sub_type and "comment" in sub_type:
        type_class = "type-comment"
    
    container = div(cls=f"block-group {type_class} depth-{min(depth, 6)}")
    
    with container:
        # Header
        group_id = group.get("id", "")
        name = group.get("name")
        warnings = group.get("_warnings", [])
        
        header_div = div(cls="header")
        with header_div:
            type_label = f"{group_type}"
            if sub_type:
                type_label += f" / {sub_type}"
            render_badge(f"group[{group_index}] {type_label}", "badge-type")
            render_badge(f"id: {group_id[:8]}", "badge-id")
            if name:
                render_badge(f'"{name}"', "badge-id")
            for warning in warnings:
                render_badge(f"⚠ {warning}", "badge-warning")
        
        # Citation metadata
        citation_str = render_citation_meta(group)
        if citation_str:
            div(citation_str, cls="meta-line")
        
        # Group data (if not a structural container)
        if not no_content:
            data = group.get("data")
            if data and group_type not in ("list", "ordered_list", "column", "column_list"):
                content_div = div(cls="content")
                with content_div:
                    if isinstance(data, dict):
                        # Show relevant metadata based on type
                        if group_type == "sheet":
                            sheet_name = data.get("sheet_name")
                            if sheet_name:
                                p(f"Sheet: {sheet_name}")
                        else:
                            # Generic dict rendering
                            json_str = json.dumps(data, indent=2)
                            truncated, was_trunc = truncate_text(json_str, max_len)
                            pre(truncated)
                            if was_trunc:
                                p("... (truncated)", cls="truncated")
                    elif isinstance(data, str) and data.strip():
                        for elem in render_markdown_content(data, max_len):
                            content_div.add(elem)
        
        # Render children in block-stream order (groups follow their earliest block)
        child_block_indices, child_group_indices = resolve_children(group)
        child_order_cache: Dict[int, int] = {}
        for kind, child_index in merge_children_by_block_order(
            child_block_indices,
            child_group_indices,
            groups_by_index,
            blocks_by_index,
            child_order_cache,
        ):
            if kind == "block" and child_index in blocks_by_index:
                container.add(
                    render_block(
                        blocks_by_index[child_index],
                        depth + 1,
                        max_len,
                        no_content,
                    )
                )
            elif kind == "group" and child_index in groups_by_index:
                container.add(
                    render_group(
                        groups_by_index[child_index],
                        groups_by_index,
                        blocks_by_index,
                        depth + 1,
                        max_len,
                        no_content,
                        visited,
                    )
                )
    
    visited.remove(group_index)
    return container


# =============================================================================
# HTML Renderer - Summary Panel
# =============================================================================

def render_summary_panel(
    record: Dict[str, Any],
    block_groups: List[Dict],
    blocks: List[Dict]
) -> div:
    """Render the top summary panel."""
    panel = div(cls="summary-panel")
    
    with panel:
        h1(record.get("record_name", "Untitled Record"))
        
        # Metadata grid
        meta_grid = div(cls="summary-meta")
        with meta_grid:
            items = [
                ("Type", record.get("record_type", "N/A")),
                ("Connector", record.get("connector_name", "N/A")),
                ("MIME Type", record.get("mime_type", "N/A")),
                ("Virtual ID", record.get("virtual_record_id", "N/A")),
                ("Origin", record.get("origin", "N/A")),
            ]
            for label, value in items:
                item_div = div(cls="summary-meta-item")
                with item_div:
                    div(label, cls="summary-meta-label")
                    div(str(value), cls="summary-meta-value")
        
        # Stats
        stats_div = div(cls="summary-stats")
        with stats_div:
            span("Total groups: ", cls="summary-stat")
            span(raw(f"<strong>{len(block_groups)}</strong>"), cls="summary-stat")

            span(" | ", cls="summary-stat")

            span("Total blocks: ", cls="summary-stat")
            span(raw(f"<strong>{len(blocks)}</strong>"), cls="summary-stat")
            
            # Type breakdown
            block_types = Counter(b.get("type") for b in blocks)
            if block_types:
                span(" | ", cls="summary-stat")
                type_parts = [f"{t}: {c}" for t, c in block_types.most_common()]
                span(f"Block types: {', '.join(type_parts)}", cls="summary-stat")
            
            # Group type breakdown
            group_types = Counter(g.get("type") for g in block_groups)
            if group_types:
                span(" | ", cls="summary-stat")
                type_parts = [f"{t}: {c}" for t, c in group_types.most_common()]
                span(f"Group types: {', '.join(type_parts)}", cls="summary-stat")
            
            # Warning count
            total_warnings = sum(len(b.get("_warnings", [])) for b in blocks)
            total_warnings += sum(len(g.get("_warnings", [])) for g in block_groups)
            if total_warnings > 0:
                span(" | ", cls="summary-stat")
                span(f"⚠ {total_warnings} warnings", cls="warning-count")
    
    return panel


# =============================================================================
# Main Renderer
# =============================================================================

def render_html(
    record: Dict[str, Any],
    max_len: int,
    no_content: bool
) -> str:
    """Render the complete HTML visualization."""
    block_containers = record.get("block_containers", {})
    block_groups = block_containers.get("block_groups", [])
    blocks = block_containers.get("blocks", [])
    
    # Build lookup maps
    _, _, groups_by_index, blocks_by_index = build_tree(block_groups, blocks)
    
    # Validate consistency
    validate_consistency(block_groups, blocks, groups_by_index, blocks_by_index)
    
    # Create document
    doc = document(title=f"Block Viz: {record.get('record_name', 'Record')}")
    
    with doc.head:
        style(get_css())
    
    with doc.body:
        # Summary panel
        render_summary_panel(record, block_groups, blocks)
        
        # Content area
        content = div(cls="content-area")
        with content:
            if not block_groups and not blocks:
                h2("No blocks found")
                p("This record has an empty block_containers object.")
            else:
                visited: Set[int] = set()
                render_content_in_block_order(
                    content,
                    block_groups,
                    blocks_by_index,
                    groups_by_index,
                    max_len,
                    no_content,
                    visited,
                )
    
    return str(doc)


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main entry point."""
    args = parse_args()
    
    try:
        # Resolve path
        if args.path:
            input_path = args.path
            if not os.path.exists(input_path):
                print(f"Error: File not found: {input_path}")
                sys.exit(1)
        else:
            print(f"Resolving path for org={args.org}, record={args.record}...")
            input_path = resolve_record_path(args.org, args.record)
            print(f"Found: {input_path}")
        
        # Load record
        print("Loading record JSON...")
        record = load_record_json(input_path)
        
        # Determine output path
        if args.output:
            output_path = args.output
        else:
            virtual_record_id = record.get("virtual_record_id", "unknown")
            input_dir = os.path.dirname(input_path)
            output_path = os.path.join(input_dir, f"record_{virtual_record_id}_viz.html")
        
        # Render HTML
        print("Rendering HTML...")
        html_content = render_html(record, args.max_text_len, args.no_content)
        
        # Write output
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"Visualization written to: {output_path}")
        
        # Open in browser
        if args.open:
            print("Opening in browser...")
            webbrowser.open(f"file://{os.path.abspath(output_path)}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
