from typing import Any

from pydantic import BaseModel  # type: ignore


class GraphQLError(BaseModel):
    """GraphQL error representation."""

    message: str
    locations: list[dict[str, int]] | None = None
    path: list[str | int] | None = None
    extensions: dict[str, Any] | None = None

class GraphQLResponse(BaseModel):
    """Standardized GraphQL response wrapper."""

    success: bool
    data: dict[str, Any] | None = None
    errors: list[GraphQLError] | None = None
    extensions: dict[str, Any] | None = None
    message: str | None = None

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_response(cls, response_data: dict[str, Any]) -> "GraphQLResponse":
        """Create GraphQLResponse from raw GraphQL response."""
        success = "errors" not in response_data or not response_data["errors"]

        errors = None
        if response_data.get("errors"):
            errors = [
                GraphQLError(
                    message=error.get("message", "Unknown error"),
                    locations=error.get("locations"),
                    path=error.get("path"),
                    extensions=error.get("extensions"),
                )
                for error in response_data["errors"]
            ]

        return cls(
            success=success,
            data=response_data.get("data"),
            errors=errors,
            extensions=response_data.get("extensions"),
            message=errors[0].message if errors else None,
        )
