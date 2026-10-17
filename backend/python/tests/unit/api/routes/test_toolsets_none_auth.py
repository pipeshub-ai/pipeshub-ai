"""AuthType.NONE instances need no credential record (issue #3065)."""

from enum import Enum
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.toolsets import (
    auth_type_requires_no_credentials,
    get_authenticated_toolsets,
    get_my_toolsets,
    is_toolset_authenticated,
)


class AuthType(str, Enum):
    NONE = "NONE"
    OAUTH = "OAUTH"


class TestNoneAuthHelpers:
    def test_helper_treats_none_string_and_enum_as_ready(self) -> None:
        assert auth_type_requires_no_credentials("NONE") is True
        assert auth_type_requires_no_credentials("none") is True
        assert auth_type_requires_no_credentials(AuthType.NONE) is True
        assert auth_type_requires_no_credentials("OAUTH") is False
        assert auth_type_requires_no_credentials(None) is False

        assert is_toolset_authenticated("NONE", None) is True
        assert is_toolset_authenticated(AuthType.NONE, None) is True
        assert is_toolset_authenticated("OAUTH", None) is False
        assert is_toolset_authenticated("OAUTH", {"isAuthenticated": True}) is True
