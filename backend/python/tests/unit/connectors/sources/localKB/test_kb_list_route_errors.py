"""The KB list routes answer a failed listing with an error status, not a broken body."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.connectors.sources.localKB.api.kb_router import _listing_or_error
from app.utils.user_messages import action_failed


def test_a_listing_passes_through() -> None:
    body = {"records": [], "pagination": {}, "filters": {}}
    assert _listing_or_error(body) is body


def test_a_failed_read_is_a_500_with_the_users_message() -> None:
    """The error payload fails ListRecordsResponse, so returned as-is its message never arrived."""
    with pytest.raises(HTTPException) as caught:
        _listing_or_error({"records": [], "error": action_failed("load these files")})
    assert caught.value.status_code == 500
    assert caught.value.detail == action_failed("load these files")


def test_an_unknown_user_keeps_its_status() -> None:
    with pytest.raises(HTTPException) as caught:
        _listing_or_error({"success": False, "code": 404, "reason": "User not found for user_id: u1"})
    assert caught.value.status_code == 404
