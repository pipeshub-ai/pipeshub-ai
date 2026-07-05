"""`ToolResultExtractor`: reliable extraction of data from tool results,
extracted from `modules/agents/qna/nodes.py` (Phase 0 of the agent-loop
migration). Heavily used both by the legacy LangGraph node functions and
by `modules/agents/context/tool_results_context.py`.
"""


from __future__ import annotations

import json
from typing import Any

# Tool execution constants (duplicated from nodes.py's module-level
# constant of the same name/value — this is the only cross-reference
# ToolResultExtractor had, so a literal is clearer than an import cycle).
TOOL_RESULT_TUPLE_LENGTH = 2


class ToolResultExtractor:
    """Reliable extraction of data from tool results"""

    @staticmethod
    def extract_success_status(result: dict[str, Any] | str | tuple[bool, Any] | None) -> bool:
        """
        Reliably detect if a tool execution succeeded.

        Handles multiple result formats:
        - Tuple: (bool, data)
        - Dict: {"success": bool, ...}
        - String: checks for error indicators
        """
        if result is None:
            return False

        # Tuple format: (success, data)
        if isinstance(result, tuple) and len(result) >= 1 and isinstance(result[0], bool):
            return result[0]

        # Dict format
        if isinstance(result, dict):
            # Check success field
            if "success" in result and isinstance(result["success"], bool):
                return result["success"]
            # Check ok field
            if "ok" in result and isinstance(result["ok"], bool):
                return result["ok"]
            # Check for error field
            if "error" in result and result["error"] not in (None, "", "null"):
                return False
            # Status-style failure shapes — connectors that don't follow the
            # `error` key convention often signal failure via `status` instead.
            #   {"status": 500, ...}            → HTTP failure
            #   {"status": "error", ...}        → explicit failure status
            #   {"status_code": 4xx/5xx, ...}   → HTTP failure (alt key)
            status = result.get("status")
            if isinstance(status, int) and status >= 400:
                return False
            if isinstance(status, str) and status.lower() in ("error", "failed", "failure"):
                return False
            status_code = result.get("status_code")
            if isinstance(status_code, int) and status_code >= 400:
                return False
            # Dict with no explicit success/ok/error/status marker is treated
            # as success. Without this return, control falls through to the
            # str(result).lower() substring scan below, which produces
            # false-positive errors whenever a legitimate result excerpt
            # contains words like "failed", "failure", "exception",
            # "traceback" or "error:" (common in incident, testing,
            # debugging or troubleshooting content).
            return True

        # String format - try JSON parse first to avoid false negatives from content
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                return ToolResultExtractor.extract_success_status(parsed)
            except (json.JSONDecodeError, ValueError):
                pass

        # Plain string fallback - check for error indicators
        # NOTE: only reaches here for non-JSON strings; JSON results are handled above
        result_str = str(result).lower()
        error_indicators = [
            "error:", '"error": "', "'error': '",
            "failed", "failure", "exception",
            "traceback", "status_code: 4", "status_code: 5"
        ]

        # Ignore null errors
        if '"error": null' in result_str or "'error': none" in result_str:
            return True

        return not any(ind in result_str for ind in error_indicators)

    @staticmethod
    def extract_data_from_result(result: dict[str, Any] | str | tuple[bool, Any] | list[Any] | None) -> dict[str, Any] | str | list[Any] | None:
        """
        Extract the actual data from a tool result.

        Handles:
        - Tuple: (success, data) → returns data
        - Dict: tries to parse JSON strings
        - Other: returns as-is
        """
        # Handle tuple format
        if isinstance(result, tuple) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
            _, data = result
            return ToolResultExtractor.extract_data_from_result(data)

        # Handle JSON strings
        if isinstance(result, str):
            try:
                return json.loads(result)
            except (json.JSONDecodeError, TypeError):
                # ✅ NEW: Return the string directly (for retrieval tool)
                # Retrieval returns formatted string, not JSON
                return result

        return result

    @staticmethod
    def extract_field_from_data(data: dict[str, Any] | list[Any] | str | None, field_path: list[str]) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
        """
        Extract a specific field from data using a field path.

        Examples:
        - ["data", "key"] → data.key
        - ["data", "0", "accountId"] → data[0].accountId
        - ["data", "results", "0", "id"] → data.results[0].id (with fallback if results doesn't exist)

        Handles:
        - Nested dicts
        - Arrays with numeric indices
        - Auto-fallback when incorrect paths are used (e.g., .results when it doesn't exist)
        - JSON strings
        """
        current = data
        i = 0

        while i < len(field_path):
            if current is None:
                return None

            field = field_path[i]

            # Handle dict
            if isinstance(current, dict):
                # Check if field exists
                if field in current:
                    current = current.get(field)
                else:
                    # Fallback: if we're looking for "results" but it doesn't exist,
                    # and we have "data" that's a list, skip "results" and use data[index] directly
                    if field == "results" and "data" in current and isinstance(current.get("data"), list):
                        # Skip "results" and check if next field is an index
                        if i + 1 < len(field_path):
                            try:
                                index = int(field_path[i + 1])
                                data_list = current.get("data")
                                if 0 <= index < len(data_list):
                                    current = data_list[index]
                                    i += 2  # Skip both "results" and the index
                                    continue
                            except (ValueError, TypeError):
                                pass
                    # Generic "data" prefix skip: if the LLM used "data" as a wrapper
                    # but the actual API response doesn't have a "data" key (e.g., Google APIs
                    # return top-level keys directly), skip "data" and continue navigating
                    # the current dict with the remaining path.
                    # Special case: if the NEXT field after "data" is a numeric index (e.g.,
                    # {{tool.data[0].id}} instead of {{tool.data.items[0].id}}), find the
                    # first list in the current dict (items, results, records, ...) and index into it.
                    elif field == "data":
                        if i + 1 < len(field_path):
                            try:
                                next_idx = int(field_path[i + 1])
                                # Next field is numeric — LLM wrote data[N] instead of data.items[N]
                                # Search for a list value in the current dict (priority order)
                                list_data = None
                                for list_key in ("items", "results", "records", "messages", "values", "data", "value"):
                                    candidate = current.get(list_key)
                                    if isinstance(candidate, list):
                                        list_data = candidate
                                        break
                                if list_data is None:
                                    # Fall back to any list value in the dict
                                    for v in current.values():
                                        if isinstance(v, list):
                                            list_data = v
                                            break
                                if list_data is not None and 0 <= next_idx < len(list_data):
                                    current = list_data[next_idx]
                                    i += 2  # consume 'data' and the numeric index
                                    continue
                            except (ValueError, TypeError):
                                pass
                        i += 1
                        continue
                    # Fallback: "results" → prefixed variants (e.g. web_results)
                    elif field == "results":
                        matched = None
                        for key in current:
                            if key.endswith("_results") and isinstance(current[key], list):
                                matched = key
                                break
                        if matched is not None:
                            current = current[matched]
                        else:
                            return None
                    # Bidirectional alias fallbacks
                    elif field == "content" and "body" in current:
                        current = current.get("body")
                    elif field == "body" and "content" in current:
                        current = current.get("content")
                    elif field == "url" and "link" in current:
                        current = current.get("link")
                    elif field == "link" and "url" in current:
                        current = current.get("url")
                    else:
                        return None

                # After getting a field from dict, check if result is a list
                # and if the next field is an index
                if isinstance(current, list):
                    # Check if list is empty first
                    if len(current) == 0:
                        # If we're trying to access an index in an empty list, return None
                        if i + 1 < len(field_path):
                            try:
                                next_index = int(field_path[i + 1])
                                # Empty list, can't access index
                                return None
                            except (ValueError, TypeError):
                                pass
                        return None  # Empty list, nothing to extract

                    # If next field is a numeric index, use it
                    if i + 1 < len(field_path):
                        try:
                            next_index = int(field_path[i + 1])
                            if 0 <= next_index < len(current):
                                current = current[next_index]
                                i += 2  # Skip both current field (already processed) and index
                                continue
                            else:
                                # Index out of bounds
                                return None
                        except (ValueError, TypeError):
                            pass
                    # If we're at the end of the path and current is a list, return the list
                    if i + 1 >= len(field_path):
                        return current
                    # Otherwise, auto-extract from first item for next navigation
                    if len(current) > 0:
                        current = current[0]
                    else:
                        return None

            # Handle array with index
            elif isinstance(current, list):
                try:
                    # Try to parse as numeric index; treat wildcards as 0
                    if field in ('?', '*') or not field.lstrip('-').isdigit():
                        raise ValueError("non-numeric index — use first element")
                    index = int(field)
                    if 0 <= index < len(current):
                        current = current[index]
                    else:
                        return None
                except ValueError:
                    # Wildcard or non-numeric index — treat as field lookup on first item
                    # or default to first element if the field is a known wildcard
                    if field in ('?', '*'):
                        # Wildcard: use first element and continue navigating
                        if len(current) > 0:
                            current = current[0]
                        else:
                            return None
                    elif len(current) > 0 and isinstance(current[0], dict):
                        # Try direct field access on first item
                        if field in current[0]:
                            current = current[0].get(field)
                        # Bidirectional alias fallback: content ↔ body
                        elif field == "content" and "body" in current[0]:
                            current = current[0].get("body")
                        elif field == "body" and "content" in current[0]:
                            current = current[0].get("content")
                        else:
                            return None
                    else:
                        return None

            # Handle JSON string
            elif isinstance(current, str):
                try:
                    parsed = json.loads(current)
                    if isinstance(parsed, dict):
                        # Try direct field access first
                        if field in parsed:
                            current = parsed.get(field)
                        # Bidirectional alias fallback: content ↔ body
                        elif field == "content" and "body" in parsed:
                            current = parsed.get("body")
                        elif field == "body" and "content" in parsed:
                            current = parsed.get("content")
                        else:
                            return None
                    else:
                        return None
                except (json.JSONDecodeError, TypeError):
                    return None
            else:
                return None

            i += 1

        # Post-processing: If we extracted a Confluence body/content object with storage format,
        # automatically extract the value if we're at the end of the path
        # This handles cases like: data.body → automatically extract body.storage.value
        # or data.content → automatically extract content.storage.value
        if isinstance(current, dict):
            # Handle Confluence storage format: {"storage": {"value": "..."}}
            if "storage" in current:
                storage = current.get("storage", {})
                if isinstance(storage, dict) and "value" in storage:
                    # This is a Confluence storage format object, return the actual content
                    return storage.get("value")
            # Handle direct value fields (some APIs return content/body as {"value": "..."})
            # Check if it's a simple dict with just a "value" key
            elif "value" in current and len(current) == 1:
                return current.get("value")

        # If current is already a string (direct content), return it as-is
        if isinstance(current, str):
            return current

        # If current is None here and the last requested field was "id",
        # check whether the parent dict has a usable "key" field instead.
        # This handles Confluence spaces where the API returns id=null but key is set.
        # The _resolve_space_id method will convert the key to a numeric ID at call time.
        if current is None and field_path and field_path[-1] == "id":
            # Re-navigate to the parent to check for a "key" fallback
            try:
                parent = data
                for f in field_path[:-1]:
                    if isinstance(parent, dict):
                        parent = parent.get(f)
                    elif isinstance(parent, list):
                        try:
                            parent = parent[int(f)]
                        except (ValueError, IndexError):
                            parent = None
                            break
                if isinstance(parent, dict) and parent.get("key"):
                    return parent.get("key")
            except Exception:
                pass

        return current

