"""
Response Transformer Utility

A simple, declarative utility for transforming API responses by removing or keeping
specific fields. Supports nested fields and wildcard patterns.

Usage:
    transformed_data = (
        ResponseTransformer(data)
        .remove("expand", "self", "*.avatarUrls", "*.iconUrl")
        .keep("key", "id", "summary", "issues")
        .clean()
    )
"""

from typing import Any, List

# Constants for pattern matching
_PATTERN_SPLIT_PARTS = 2


class ResponseTransformer:
    """Simple response transformer with fluent API for removing/keeping fields.

    Supports nested field paths and wildcard patterns for flexible field matching.
    Works recursively on dictionaries and lists.

    Examples:
        # Remove specific fields
        ResponseTransformer(data).remove("self", "expand").clean()

        # Remove nested fields with wildcards
        ResponseTransformer(data).remove("*.avatarUrls", "*.self").clean()

        # Keep only specific fields
        ResponseTransformer(data).keep("key", "id", "summary").clean()

        # Combine remove and keep
        ResponseTransformer(data)
            .remove("*.avatarUrls", "*.self")
            .keep("key", "id", "summary")
            .clean()
    """

    def __init__(self, data: Any) -> None:  # noqa: ANN401
        """Initialize with data to transform.

        Args:
            data: The data structure to transform (dict, list, or primitive)
        """
        self.data = data
        self._remove_fields: List[str] = []
        self._keep_fields: List[str] = []

    def remove(self, *field_paths: str) -> 'ResponseTransformer':
        """Add field paths to remove (supports nested paths and wildcards).

        Args:
            *field_paths: Field paths to remove. Supports:
                - Exact matches: "self", "expand"
                - Nested paths: "assignee.avatarUrls", "fields.status.self"
                - Wildcard suffix: "*.avatarUrls" matches any field ending with .avatarUrls
                - Wildcard prefix: "assignee.*" matches all fields under assignee
                - Wildcard middle: "assignee.*.url" matches nested paths

        Returns:
            Self for method chaining
        """
        self._remove_fields.extend(field_paths)
        return self

    def keep(self, *field_paths: str) -> 'ResponseTransformer':
        """Add field paths to keep (supports nested paths and wildcards).

        When keep() is used, only fields matching the keep patterns are retained.
        Remove patterns still take precedence - if a field matches both, it's removed.

        Args:
            *field_paths: Field paths to keep. Same pattern support as remove().

        Returns:
            Self for method chaining
        """
        self._keep_fields.extend(field_paths)
        return self

    def clean(self) -> Any:  # noqa: ANN401
        """Transform the data and return transformed result.

        Returns:
            Transformed data structure with same type as input
        """
        if not self._remove_fields and not self._keep_fields:
            return self.data

        return self._clean_recursive(self.data, "")

    def _clean_recursive(self, data: Any, base_path: str) -> Any:  # noqa: ANN401
        """Recursively transform data structure based on patterns.

        Args:
            data: Current data being processed
            base_path: Current path in the data structure (e.g., "issues.0.fields")

        Returns:
            Transformed data structure
        """
        if isinstance(data, dict):
            cleaned = {}

            for key, value in data.items():
                # Build current path from base path and current key
                current_path = f"{base_path}.{key}" if base_path else key

                # Check if this field should be removed
                should_remove = False
                for remove_path in self._remove_fields:
                    if self._path_matches(current_path, remove_path):
                        should_remove = True
                        break

                # Check if this field should be kept (if keep_fields specified)
                should_keep = True
                if self._keep_fields:
                    should_keep = False
                    for keep_path in self._keep_fields:
                        if self._keep_path_matches(current_path, keep_path):
                            should_keep = True
                            break

                # Decision: remove if explicitly marked OR if keep_fields exist and doesn't match
                if should_remove or (self._keep_fields and not should_keep):
                    continue

                # Recursively transform nested structures
                if isinstance(value, (dict, list)):
                    cleaned[key] = self._clean_recursive(value, current_path)
                else:
                    cleaned[key] = value

            return cleaned

        elif isinstance(data, list):
            # For lists, transform each item recursively
            # Use the same base_path for all items (array indices don't affect field matching)
            return [self._clean_recursive(item, base_path) for item in data]

        else:
            # Primitive types (str, int, bool, None) are returned as-is
            return data

    def _path_matches(self, current_path: str, pattern: str) -> bool:
        """Check if current path matches pattern (supports wildcards and nested paths).

        This method handles various pattern matching scenarios:
        - Exact matches: "self" matches "self"
        - Nested paths: "assignee.avatarUrls" matches "assignee.avatarUrls"
        - Wildcard suffix: "*.avatarUrls" matches "assignee.avatarUrls" or "fields.assignee.avatarUrls"
        - Wildcard prefix: "assignee.*" matches "assignee.avatarUrls" or "assignee.active"
        - Wildcard middle: "assignee.*.url" matches "assignee.avatarUrls.url"
        - Field name anywhere: "self" matches "fields.status.self" or "assignee.self"

        Args:
            current_path: The current field path being evaluated
            pattern: The pattern to match against

        Returns:
            True if current_path matches pattern, False otherwise
        """
        # Exact match
        if current_path == pattern:
            return True

        if "*" in pattern:
            return self._wildcard_path_matches(current_path, pattern)

        # Pattern ends with wildcard (e.g., "assignee.*" matches "assignee.avatarUrls" or "fields.assignee.avatarUrls")
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            # Match if path equals prefix or starts with prefix followed by dot
            if current_path == prefix or current_path.startswith(prefix + "."):
                return True

        # Pattern starts with wildcard (e.g., "*.avatarUrls" matches "assignee.avatarUrls" or "fields.assignee.avatarUrls")
        if pattern.startswith("*."):
            suffix = pattern[2:]
            # Match if path equals suffix or ends with dot + suffix
            if current_path == suffix or current_path.endswith("." + suffix):
                return True

        # Pattern has wildcard in middle (e.g., "assignee.*.url" matches "assignee.avatarUrls.url")
        if ".*" in pattern and not pattern.startswith("*") and not pattern.endswith("*"):
            parts = pattern.split(".*", 1)
            if len(parts) == _PATTERN_SPLIT_PARTS:
                prefix, suffix = parts
                # Match if path starts with prefix. and ends with .suffix
                if current_path.startswith(prefix + ".") and current_path.endswith("." + suffix):
                    return True

        # Check if pattern matches as suffix (for nested fields)
        # e.g., pattern "avatarUrls" matches "assignee.avatarUrls" or "fields.assignee.avatarUrls"
        if current_path.endswith("." + pattern):
            return True

        # Check if pattern matches as prefix (for nested fields)
        # e.g., pattern "self" matches "self.url" or "self.field"
        if current_path.startswith(pattern + "."):
            return True

        # Check if pattern appears in the middle of the path (for deeply nested fields)
        # e.g., pattern "self" matches "fields.self.url" (contains ".self.")
        if "." + pattern + "." in current_path:
            return True

        return False

    def _keep_path_matches(self, current_path: str, pattern: str) -> bool:
        """Check keep semantics for the current path.

        Keep matching is narrower than remove matching:
        - exact matches keep the field itself
        - ancestors of explicit keep paths are preserved as scaffolding
        - descendants are preserved only for explicit nested/wildcard subtree keeps
        - simple leaf names still match nested leaf fields anywhere
        """
        if current_path == pattern:
            return True

        if self._is_ancestor_path(current_path, pattern):
            return True

        if self._should_keep_descendant(current_path, pattern):
            return True

        if pattern.startswith("*."):
            suffix = pattern[2:]
            if current_path == suffix or current_path.endswith("." + suffix):
                return True

        if "." not in pattern and "*" not in pattern and current_path.endswith("." + pattern):
            return True

        return False

    def _should_keep_descendant(self, current_path: str, pattern: str) -> bool:
        if not self._is_ancestor_path(pattern, current_path):
            return False

        return "." in pattern or "*" in pattern

    @staticmethod
    def _is_ancestor_path(ancestor_path: str, descendant_path: str) -> bool:
        return descendant_path.startswith(ancestor_path + ".")

    def _wildcard_path_matches(self, current_path: str, pattern: str) -> bool:
        current_parts = current_path.split(".") if current_path else []
        pattern_parts = pattern.split(".") if pattern else []
        return self._match_parts(current_parts, pattern_parts)

    def _match_parts(self, current_parts: list[str], pattern_parts: list[str]) -> bool:
        if not pattern_parts:
            return not current_parts

        head = pattern_parts[0]

        if head == "*":
            if len(pattern_parts) == 1:
                return True

            if not current_parts:
                return False

            for index in range(len(current_parts) + 1):
                if self._match_parts(current_parts[index:], pattern_parts[1:]):
                    return True
            return False

        if not current_parts or current_parts[0] != head:
            return False

        return self._match_parts(current_parts[1:], pattern_parts[1:])

