"""Unit tests for app.sources.client.http.exception.exception."""

import pytest

from app.sources.client.http.exception.exception import (
    BadGatewayError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    HTTPException,
    HttpStatusCode,
    InternalServerError,
    MethodNotAllowedError,
    NotFoundError,
    ServiceUnavailableError,
    TooManyRequestsError,
    UnauthorizedError,
    UnprocessableEntityError,
    VectorDBEmptyError,
)


# ---------------------------------------------------------------------------
# HttpStatusCode enum
# ---------------------------------------------------------------------------
class TestHttpStatusCode:
    def test_success_codes(self):
        assert HttpStatusCode.SUCCESS.value == 200
        assert HttpStatusCode.CREATED.value == 201
        assert HttpStatusCode.NO_CONTENT.value == 204

    def test_client_error_codes(self):
        assert HttpStatusCode.BAD_REQUEST.value == 400
        assert HttpStatusCode.UNAUTHORIZED.value == 401
        assert HttpStatusCode.FORBIDDEN.value == 403
        assert HttpStatusCode.NOT_FOUND.value == 404
        assert HttpStatusCode.METHOD_NOT_ALLOWED.value == 405
        assert HttpStatusCode.CONFLICT.value == 409
        assert HttpStatusCode.GONE.value == 410
        assert HttpStatusCode.UNPROCESSABLE_ENTITY.value == 422
        assert HttpStatusCode.TOO_MANY_REQUESTS.value == 429

    def test_server_error_codes(self):
        assert HttpStatusCode.INTERNAL_SERVER_ERROR.value == 500
        assert HttpStatusCode.BAD_GATEWAY.value == 502
        assert HttpStatusCode.SERVICE_UNAVAILABLE.value == 503


# ---------------------------------------------------------------------------
# HTTPException
# ---------------------------------------------------------------------------
class TestHTTPException:
    def test_with_status_and_message(self):
        exc = HTTPException(status_code=400, message="bad")
        assert exc.status_code == 400
        assert exc.detail == "bad"

    def test_default_message(self):
        exc = HTTPException(status_code=500)
        assert exc.status_code == 500
        assert exc.detail == ""

    def test_with_headers(self):
        exc = HTTPException(status_code=401, message="auth", headers={"WWW-Authenticate": "Bearer"})
        assert exc.status_code == 401
        assert exc.detail == "auth"

    def test_is_exception(self):
        exc = HTTPException(status_code=500, message="fail")
        assert isinstance(exc, Exception)


# ---------------------------------------------------------------------------
# BadRequestError (400)
# ---------------------------------------------------------------------------
class TestBadRequestError:
    def test_default_message(self):
        exc = BadRequestError()
        assert exc.status_code == 400
        assert exc.detail == "Bad Request"

    def test_custom_message(self):
        exc = BadRequestError("Missing field 'name'")
        assert exc.status_code == 400
        assert exc.detail == "Missing field 'name'"

    def test_is_http_exception(self):
        assert isinstance(BadRequestError(), HTTPException)


# ---------------------------------------------------------------------------
# UnauthorizedError (401)
# ---------------------------------------------------------------------------
class TestUnauthorizedError:
    def test_default_message(self):
        exc = UnauthorizedError()
        assert exc.status_code == 401
        assert exc.detail == "Unauthorized"

    def test_custom_message(self):
        exc = UnauthorizedError("Token expired")
        assert exc.status_code == 401
        assert exc.detail == "Token expired"

    def test_is_http_exception(self):
        assert isinstance(UnauthorizedError(), HTTPException)


# ---------------------------------------------------------------------------
# ForbiddenError (403)
# ---------------------------------------------------------------------------
class TestForbiddenError:
    def test_default_message(self):
        exc = ForbiddenError()
        assert exc.status_code == 403
        assert exc.detail == "Forbidden"

    def test_custom_message(self):
        exc = ForbiddenError("Access denied")
        assert exc.status_code == 403
        assert exc.detail == "Access denied"

    def test_is_http_exception(self):
        assert isinstance(ForbiddenError(), HTTPException)


# ---------------------------------------------------------------------------
# NotFoundError (404)
# ---------------------------------------------------------------------------
class TestNotFoundError:
    def test_default_message(self):
        exc = NotFoundError()
        assert exc.status_code == 404
        assert exc.detail == "Not Found"

    def test_custom_message(self):
        exc = NotFoundError("User 42 not found")
        assert exc.status_code == 404
        assert exc.detail == "User 42 not found"

    def test_is_http_exception(self):
        assert isinstance(NotFoundError(), HTTPException)


# ---------------------------------------------------------------------------
# MethodNotAllowedError (405)
# ---------------------------------------------------------------------------
class TestMethodNotAllowedError:
    def test_default_message(self):
        exc = MethodNotAllowedError()
        assert exc.status_code == 405
        assert exc.detail == "Method Not Allowed"

    def test_custom_message(self):
        exc = MethodNotAllowedError("PATCH not supported")
        assert exc.status_code == 405
        assert exc.detail == "PATCH not supported"

    def test_is_http_exception(self):
        assert isinstance(MethodNotAllowedError(), HTTPException)


# ---------------------------------------------------------------------------
# ConflictError (409)
# ---------------------------------------------------------------------------
class TestConflictError:
    def test_default_message(self):
        exc = ConflictError()
        assert exc.status_code == 409
        assert exc.detail == "Conflict"

    def test_custom_message(self):
        exc = ConflictError("Duplicate entry")
        assert exc.status_code == 409
        assert exc.detail == "Duplicate entry"

    def test_is_http_exception(self):
        assert isinstance(ConflictError(), HTTPException)


# ---------------------------------------------------------------------------
# UnprocessableEntityError (422)
# ---------------------------------------------------------------------------
class TestUnprocessableEntityError:
    def test_default_message(self):
        exc = UnprocessableEntityError()
        assert exc.status_code == 422
        assert exc.detail == "Unprocessable Entity"

    def test_custom_message(self):
        exc = UnprocessableEntityError("Invalid JSON schema")
        assert exc.status_code == 422
        assert exc.detail == "Invalid JSON schema"

    def test_is_http_exception(self):
        assert isinstance(UnprocessableEntityError(), HTTPException)


# ---------------------------------------------------------------------------
# TooManyRequestsError (429)
# ---------------------------------------------------------------------------
class TestTooManyRequestsError:
    def test_default_message(self):
        exc = TooManyRequestsError()
        assert exc.status_code == 429
        assert exc.detail == "Too Many Requests"

    def test_custom_message(self):
        exc = TooManyRequestsError("Rate limit exceeded")
        assert exc.status_code == 429
        assert exc.detail == "Rate limit exceeded"

    def test_is_http_exception(self):
        assert isinstance(TooManyRequestsError(), HTTPException)


# ---------------------------------------------------------------------------
# InternalServerError (500)
# ---------------------------------------------------------------------------
class TestInternalServerError:
    def test_default_message(self):
        exc = InternalServerError()
        assert exc.status_code == 500
        assert exc.detail == "Internal Server Error"

    def test_custom_message(self):
        exc = InternalServerError("Database connection lost")
        assert exc.status_code == 500
        assert exc.detail == "Database connection lost"

    def test_is_http_exception(self):
        assert isinstance(InternalServerError(), HTTPException)


# ---------------------------------------------------------------------------
# BadGatewayError (502)
# ---------------------------------------------------------------------------
class TestBadGatewayError:
    def test_default_message(self):
        exc = BadGatewayError()
        assert exc.status_code == 502
        assert exc.detail == "Bad Gateway"

    def test_custom_message(self):
        exc = BadGatewayError("Upstream timeout")
        assert exc.status_code == 502
        assert exc.detail == "Upstream timeout"

    def test_is_http_exception(self):
        assert isinstance(BadGatewayError(), HTTPException)


# ---------------------------------------------------------------------------
# ServiceUnavailableError (503)
# ---------------------------------------------------------------------------
class TestServiceUnavailableError:
    def test_default_message(self):
        exc = ServiceUnavailableError()
        assert exc.status_code == 503
        assert exc.detail == "Service Unavailable"

    def test_custom_message(self):
        exc = ServiceUnavailableError("Under maintenance")
        assert exc.status_code == 503
        assert exc.detail == "Under maintenance"

    def test_is_http_exception(self):
        assert isinstance(ServiceUnavailableError(), HTTPException)


# ---------------------------------------------------------------------------
# VectorDBEmptyError
# ---------------------------------------------------------------------------
class TestVectorDBEmptyError:
    def test_is_value_error(self):
        exc = VectorDBEmptyError()
        assert isinstance(exc, ValueError)

    def test_with_message(self):
        exc = VectorDBEmptyError("Collection is empty")
        assert str(exc) == "Collection is empty"

    def test_default_no_args(self):
        exc = VectorDBEmptyError()
        assert str(exc) == ""

    def test_can_be_raised_and_caught_as_value_error(self):
        with pytest.raises(ValueError):
            raise VectorDBEmptyError("empty")

    def test_can_be_raised_and_caught_specifically(self):
        with pytest.raises(VectorDBEmptyError):
            raise VectorDBEmptyError("no vectors")
