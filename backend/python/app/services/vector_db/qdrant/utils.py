from typing import Any, Dict, List, Optional, Union
from datetime import datetime

from qdrant_client.http.models import (  # type: ignore
    FieldCondition,
    MatchAny,
    MatchValue,
)

# Type alias for filter values
FilterValue = Union[str, int, float, bool, List[Union[str, int, float, bool]]]


class QdrantUtils:
    @staticmethod
    def build_conditions(filters: Dict[str, Any]) -> List[FieldCondition]:
        """
        Build list of FieldCondition objects from filter dictionary
        Args:
            filters: Dictionary of field: value pairs
        Returns:
            List of FieldCondition objects
        """
        conditions = []

        for key, value in filters.items():
            if value is not None:
                # Handle lists/tuples - use MatchAny
                if isinstance(value, (list, tuple)):
                    # Filter out None values
                    filtered_list = [v for v in value if v is not None]
                    if filtered_list:
                        conditions.append(
                            FieldCondition(
                                key=f"metadata.{key}",
                                match=MatchAny(any=filtered_list)
                            )
                        )
                # Handle single values - use MatchValue
                elif QdrantUtils._is_valid_value(value):
                    conditions.append(
                        FieldCondition(
                            key=f"metadata.{key}",
                            match=MatchValue(value=value)
                        )
                    )

        return conditions

    @staticmethod
    def _is_valid_value(value: FilterValue) -> bool:
        """Check if value is valid for filtering"""
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
        return True

    @staticmethod
    def format_search_results(results: Any, include_scores: bool = True, metadata: Dict[str, Any] = {}) -> Dict[str, Any]:
        """Format raw search results for API response"""
        formatted = []
        timestamp = datetime.now()

        for item in results:
            entry = {
                "id": item.id,
                "text": item.payload.get("text", None),
                "metadata": item.payload.get("metadata", {}),
            }
            if include_scores:
                entry["score"] = item.score
            formatted.append(entry)

        print(f"Formatted {len(formatted)} results at {timestamp}")

        if len(formatted) == 0:
            return

        return {
            "results": formatted,
            "total": len(formatted),
            "timestamp": str(timestamp),
            **metadata,
        }

    @staticmethod
    def validate_collection(name, config) -> Optional[str]:
        """Validate collection name and config"""
        if not name:
            return "Collection name is required"
        if "vectors" not in config.keys():
            return "Vectors config is required"
        return None
