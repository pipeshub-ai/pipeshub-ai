"""Typed refusals of the tool gateway, each with a stable ``reason_code`` (C-6: unique)."""

from __future__ import annotations

from typing import Literal

from harness_core.refusals import HarnessRefusal

TokenRefusalReason = Literal[
    "token_missing",
    "token_malformed",
    "token_algorithm",
    "token_unknown_key",
    "token_signature",
    "token_issuer",
    "token_audience",
    "token_expired",
]
TOKEN_REFUSAL_CODES: tuple[TokenRefusalReason, ...] = (
    "token_missing",
    "token_malformed",
    "token_algorithm",
    "token_unknown_key",
    "token_signature",
    "token_issuer",
    "token_audience",
    "token_expired",
)


class GatewayRefusal(HarnessRefusal):
    reason_code: str = "gateway_refused"
    http_status: int = 401


class TokenRefused(GatewayRefusal):
    """The presented gateway token is not one this gateway accepts (HTTP 401)."""

    def __init__(self, reason: TokenRefusalReason, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason


class BindingMismatch(GatewayRefusal):
    """A valid token for another org, session, request, run, attempt or grant (HTTP 401)."""

    reason_code = "binding_mismatch"


class ForbiddenArguments(GatewayRefusal):
    """A call's arguments carry a credential shape; the call is refused, never executed."""

    reason_code = "forbidden_arguments"

    def __init__(self, kinds: tuple[str, ...]) -> None:
        super().__init__(f"arguments carry a forbidden credential shape: {', '.join(kinds)}")
        self.kinds = kinds


class ModelNotGranted(GatewayRefusal):
    """The request names a model id the run's grant does not list."""

    reason_code = "model_not_granted"
    http_status = 404


class ForbiddenCredential(GatewayRefusal):
    """A request header or body carries a vendor credential shape; refused, never forwarded."""

    reason_code = "forbidden_credential"
    http_status = 403

    def __init__(self, kinds: tuple[str, ...]) -> None:
        super().__init__(f"the request carries a forbidden credential shape: {', '.join(kinds)}")
        self.kinds = kinds


class CallerCredentialHeader(GatewayRefusal):
    """A provider credential header (``api-key``, ``x-api-key``, ...): the gateway holds the credential."""

    reason_code = "caller_credential_header"
    http_status = 403


class InvalidChatRequest(GatewayRefusal):
    reason_code = "invalid_chat_request"
    http_status = 400

    def __init__(self, message: str, *, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


class UnsupportedParameter(GatewayRefusal):
    """A parameter whose meaning the gateway cannot honour: refused rather than ignored."""

    reason_code = "unsupported_parameter"
    http_status = 400

    def __init__(self, param: str, message: str | None = None, *, detail: str | None = None) -> None:
        """``message``: when a client acts on the text (Claude Code reads ``capability_rejected: …``);
        ``detail``: what the warning names besides the parameter, never a value the caller sent."""
        super().__init__(message or f"parameter '{param}' is not supported by this gateway")
        self.param = param
        self.detail = detail


class InvalidResponsesRequest(GatewayRefusal):
    """A ``/v1/responses`` body the gateway cannot read (E3b)."""

    reason_code = "invalid_responses_request"
    http_status = 400

    def __init__(self, message: str, *, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


class InvalidMessagesRequest(GatewayRefusal):
    """A ``/v1/messages`` body the gateway cannot read (E3c)."""

    reason_code = "invalid_messages_request"
    http_status = 400

    def __init__(self, message: str, *, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


class InvalidGeminiRequest(GatewayRefusal):
    """A Gemini ``generateContent`` body the gateway cannot read."""

    reason_code = "invalid_gemini_request"
    http_status = 400

    def __init__(self, message: str, *, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


class ResponseStateUnsupported(GatewayRefusal):
    """A Responses request that needs a stored response or conversation (``previous_response_id``,
    ``conversation``, ``store: true``, ``background``, ``item_reference``, retrieval): the gateway
    keeps no conversation state, so an engine sends its whole history with ``store: false``."""

    reason_code = "response_state_unsupported"
    http_status = 400

    def __init__(self, message: str, *, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


class BodyTooLarge(GatewayRefusal):
    """A request body over the gateway's limit, refused before it is parsed. The egress proxy refuses the same
    cause at the same limit as ``body_too_large``; C-6 keeps one code per refusal, and ``request_too_large`` is
    the kernel's (a request too large for the model)."""

    reason_code = "body_over_limit"
    http_status = 413


class ModelCallEncoded(GatewayRefusal):
    """WP103-FU-2: the egress proxy refused an engine's model call whose body was compressed (``body_encoded``), which
    it cannot inspect. The proxy's refusal carries this code (``[gateway:model_call_encoded]``), so the run stops as
    PipesHub's own refusal: only an admin can change how the engine sends its calls."""

    reason_code = "model_call_encoded"
    http_status = 415


class RouteNotAllowed(GatewayRefusal):
    """A path or method the model gateway does not serve; answered before any token is read."""

    reason_code = "route_not_allowed"
    http_status = 404

    def __init__(self, message: str, *, status: int = 404) -> None:
        super().__init__(message)
        self.http_status = status


class EgressVolumeExceeded(GatewayRefusal):
    """C6.12 (WP-61): the run's sandboxes moved more to outside destinations than the deployment's egress cap allows,
    as the egress proxy marked the call. Each model call is refused, so the engine ends the run with this code (its
    refusal's ``[gateway:<code>]`` tag), which the chat words; a model stream already flowing is never cut."""

    reason_code = "egress_volume_exceeded"
    http_status = 403

    def __init__(self, message: str = "this run moved more data than this deployment allows one run to move") -> None:
        super().__init__(message)


__all__ = [
    "TOKEN_REFUSAL_CODES",
    "BindingMismatch",
    "BodyTooLarge",
    "CallerCredentialHeader",
    "EgressVolumeExceeded",
    "ForbiddenArguments",
    "ForbiddenCredential",
    "GatewayRefusal",
    "InvalidChatRequest",
    "InvalidGeminiRequest",
    "InvalidMessagesRequest",
    "InvalidResponsesRequest",
    "ModelNotGranted",
    "ResponseStateUnsupported",
    "RouteNotAllowed",
    "TokenRefusalReason",
    "TokenRefused",
    "UnsupportedParameter",
]
