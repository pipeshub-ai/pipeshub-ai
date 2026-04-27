from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel  # type: ignore


class GraphQLError(BaseModel):
    """GraphQL error representation."""
    message: str
    locations: Optional[List[Dict[str, int]]] = None
    path: Optional[List[Union[str, int]]] = None
    extensions: Optional[Dict[str, Any]] = None

class GraphQLResponse(BaseModel):
    """Standardized GraphQL response wrapper."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    errors: Optional[List[GraphQLError]] = None
    extensions: Optional[Dict[str, Any]] = None
    message: Optional[str] = None

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_response(cls, response_data: Dict[str, Any]) -> "GraphQLResponse":
        """Create GraphQLResponse from raw GraphQL response."""
        success = "errors" not in response_data or not response_data["errors"]

        errors = None
        if "errors" in response_data and response_data["errors"]:
            errors = [
                GraphQLError(
                    message=error.get("message", "Unknown error"),
                    locations=error.get("locations"),
                    path=error.get("path"),
                    extensions=error.get("extensions")
                )
                for error in response_data["errors"]
            ]

        return cls(
            success=success,
            data=response_data.get("data"),
            errors=errors,
            extensions=response_data.get("extensions"),
            message=errors[0].message if errors else None
        )
