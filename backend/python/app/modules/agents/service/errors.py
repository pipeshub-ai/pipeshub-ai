"""Errors raised by agent operations. They stay `HTTPException`s so the routes
and the service answer with the same status codes and bodies."""

from typing import Any

from fastapi import HTTPException


class AgentError(HTTPException):
    """Base exception for agent operations"""
    def __init__(self, detail: str, status_code: int = 500) -> None:
        super().__init__(status_code=status_code, detail=detail)


class AgentNotFoundError(AgentError):
    """Agent not found"""
    def __init__(self, agent_id: str) -> None:
        super().__init__(
            detail="Agent not found or you don't have access to it",
            status_code=404
        )


class PermissionDeniedError(AgentError):
    """Permission denied"""
    def __init__(self, action: str) -> None:
        super().__init__(
            detail=f"You don't have permission to {action}",
            status_code=403
        )


class InvalidRequestError(AgentError):
    """Invalid request data"""
    def __init__(self, message: str) -> None:
        super().__init__(
            detail=f"Invalid request: {message}",
            status_code=400
        )


class AccessViolationError(AgentError):
    """The request names something the caller may not attach. `detail` is `{code, message, ids?}`."""
    def __init__(self, code: str, message: str, ids: list[str] | None = None) -> None:
        detail: dict[str, Any] = {"code": code, "message": message}
        if ids:
            detail["ids"] = ids
        super().__init__(detail=detail, status_code=400)


class InvalidKnowledgeError(AccessViolationError):
    def __init__(self, ids: list[str]) -> None:
        super().__init__("INVALID_KNOWLEDGE", "Some knowledge sources are not available to you.", ids)


class InvalidToolsetError(AccessViolationError):
    def __init__(self, ids: list[str]) -> None:
        super().__init__("INVALID_TOOLSET", "Some tools are not set up and signed in for you.", ids)


class ServiceAccountNotAllowedError(AccessViolationError):
    def __init__(self) -> None:
        super().__init__("SERVICE_ACCOUNT_NOT_ALLOWED", "Agents created from a chat cannot be service accounts.")
