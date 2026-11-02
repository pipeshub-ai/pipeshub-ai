"""Response-validation ITs for the code-graph dashboard read.

- GET /api/v1/code-graph/repos

The list is relayed from the query service; the stack under test may hold no
code repository, so an empty list is a valid answer. Each response must be
fully described by pipeshub-openapi.yaml.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from openapi_schema_validator import (  # noqa: E402
    assert_error_response_matches_openapi_operation,
    assert_response_strictly_matches_openapi_operation,
    operation_oauth2_scopes,
)
from helper.pipeshub_client import PipeshubClient  # noqa: E402

_REPOS = "listCodeGraphRepos"
_PATH = "/api/v1/code-graph/repos"


@pytest.mark.integration
class TestCodeGraphRepos:
    def test_repos_match_spec(self, pipeshub_client: PipeshubClient) -> None:
        """GET /code-graph/repos needs code:read and answers with the documented body."""
        assert operation_oauth2_scopes(_REPOS) == ["code:read"]
        resp = pipeshub_client.request("GET", _PATH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body["repos"], list), body
        assert_response_strictly_matches_openapi_operation(body, _REPOS)

    def test_repos_unauthenticated_is_401(self, pipeshub_client: PipeshubClient) -> None:
        """Without a token the route answers 401 with the documented error body."""
        resp = pipeshub_client.request("GET", _PATH, auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _REPOS, 401)
