from typing import Any, Dict, List, Union

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
