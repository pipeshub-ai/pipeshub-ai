"""The parsing service answers a password-protected file with 422 PASSWORD_REQUIRED."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.services.parsing.interface import ParseError, ParseErrorCode
from app.services.parsing.providers.smart_pdf_parser import SmartPDFParser
from app.services.parsing.registry import ParserRegistry
from tests.support.office_samples import ENCRYPTED_BY_EXTENSION, PLAIN_BY_EXTENSION
from tests.support.pdf_samples import owner_password_pdf, password_protected_pdf
from tests.unit.api.routes.test_parsing_routes import _build_app, _ok_result

if TYPE_CHECKING:
    from httpx import Response

MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
}


def _post(client: TestClient, name: str, content: bytes, ext: str) -> Response:
    return client.post(
        "/api/v1/parse",
        files={"file": (name, content, MIME[ext])},
        data={"mime_type": MIME[ext], "extension": ext, "provider": "default"},
    )


def _registry_with(parser: object) -> MagicMock:
    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=parser)
    return registry


@pytest.mark.parametrize("ext", ["docx", "xlsx", "pptx"])
def test_an_encrypted_office_file_is_answered_422_before_any_parser_runs(ext: str) -> None:
    parser = MagicMock()
    parser.parse = AsyncMock(return_value=_ok_result())
    client = TestClient(_build_app(_registry_with(parser)))

    response = _post(client, f"salaries.{ext}", ENCRYPTED_BY_EXTENSION[ext](), ext)

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "PASSWORD_REQUIRED"
    parser.parse.assert_not_called()


@pytest.mark.parametrize("ext", ["docx", "xlsx", "pptx"])
def test_a_plain_office_file_reaches_the_parser(ext: str) -> None:
    parser = MagicMock()
    parser.parse = AsyncMock(return_value=_ok_result())
    client = TestClient(_build_app(_registry_with(parser)))

    response = _post(client, f"notes.{ext}", PLAIN_BY_EXTENSION[ext](), ext)

    assert response.status_code == 200
    parser.parse.assert_awaited_once()


def _smart_parser() -> tuple[SmartPDFParser, MagicMock, MagicMock]:
    primary = MagicMock()
    primary.parse = AsyncMock(side_effect=ParseError(ParseErrorCode.PARSE_FAILED, "cannot open"))
    ocr = MagicMock()
    ocr.parse = AsyncMock(return_value=_ok_result())
    return SmartPDFParser(primary, ocr), primary, ocr


def test_a_password_protected_pdf_is_answered_422_and_never_reaches_ocr() -> None:
    parser, primary, ocr = _smart_parser()
    client = TestClient(_build_app(_registry_with(parser)))

    response = _post(client, "salaries.pdf", password_protected_pdf(), "pdf")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PASSWORD_REQUIRED"
    ocr.parse.assert_not_called()
    primary.parse.assert_not_called()


def test_an_owner_password_pdf_is_parsed_as_usual() -> None:
    primary = MagicMock()
    primary.parse = AsyncMock(return_value=_ok_result())
    parser = SmartPDFParser(primary, MagicMock())
    client = TestClient(_build_app(_registry_with(parser)))

    response = _post(client, "restricted.pdf", owner_password_pdf(), "pdf")

    assert response.status_code == 200
    primary.parse.assert_awaited_once()


def test_a_parser_raising_the_typed_error_is_also_a_422() -> None:
    parser = MagicMock()
    parser.parse = AsyncMock(side_effect=PasswordProtectedError("needs a password"))
    client = TestClient(_build_app(_registry_with(parser)))

    response = _post(client, "locked.pdf", b"%PDF-1.7 anything", "pdf")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PASSWORD_REQUIRED"
    assert response.json()["error"]["message"] == "needs a password"


@pytest.mark.parametrize("ext", ["docm", "xlsm", "pptm"])
def test_an_encrypted_macro_enabled_file_is_answered_422(ext: str) -> None:
    from tests.support.office_samples import ENCRYPTED_MACRO_BY_EXTENSION, MACRO_MIME

    parser = MagicMock()
    parser.parse = AsyncMock(return_value=_ok_result())
    client = TestClient(_build_app(_registry_with(parser)))

    response = client.post(
        "/api/v1/parse",
        files={"file": (f"salaries.{ext}", ENCRYPTED_MACRO_BY_EXTENSION[ext](), MACRO_MIME[ext])},
        data={"mime_type": MACRO_MIME[ext], "extension": ext, "provider": "default"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PASSWORD_REQUIRED"
    parser.parse.assert_not_called()
