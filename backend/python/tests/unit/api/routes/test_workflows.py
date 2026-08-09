"""Unit tests for `app.api.routes.workflows`, focused on the
`_get_user_context` identity resolution -- this is the function that
regressed to require literal `X-Org-Id`/`X-User-Id` headers that Node's
thin proxy never sends (see `workflows.controller.ts`), breaking every
request to the workflows dashboard with a 400. It must mirror
`tasks.py::_get_user_context` and resolve identity from
`request.state.user`, populated by the JWT auth middleware."""
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


class TestGetUserContext:
    def test_resolves_from_state_user(self) -> None:
        from app.api.routes.workflows import _get_user_context

        request = MagicMock()
        request.state.user = {"userId": "user-1", "orgId": "org-1"}
        request.headers = {}

        org_id, user_id = _get_user_context(request)

        assert org_id == "org-1"
        assert user_id == "user-1"

    def test_does_not_fall_back_to_headers_when_state_user_missing(self) -> None:
        """Client-supplied `X-User-Id`/`X-Organization-Id` headers must never
        substitute for JWT-derived identity -- that would let any caller
        spoof org/user by setting a header (plan bug C1)."""
        from app.api.routes.workflows import _get_user_context

        request = MagicMock()
        request.state.user = {}
        request.headers = {"X-User-Id": "user-2", "X-Organization-Id": "org-2"}

        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(request)

        assert exc_info.value.status_code == 401

    def test_state_user_used_even_when_spoofing_headers_present(self) -> None:
        from app.api.routes.workflows import _get_user_context

        request = MagicMock()
        request.state.user = {"userId": "state-user", "orgId": "state-org"}
        request.headers = {"X-User-Id": "header-user", "X-Organization-Id": "header-org"}

        org_id, user_id = _get_user_context(request)

        assert org_id == "state-org"
        assert user_id == "state-user"

    def test_missing_identity_raises_401_not_400(self) -> None:
        from app.api.routes.workflows import _get_user_context

        request = MagicMock()
        request.state.user = {}
        request.headers = {}

        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(request)

        assert exc_info.value.status_code == 401


class TestVersionToDict:
    """Phase 4: `needsRegeneration` is the frontend's only signal that a
    pinned version predates a verifier rule that would fail it -- there is
    no re-verification on run, so this field is what lets a user notice and
    regenerate instead of the workflow failing silently at its next fire."""

    def test_a_version_stamped_with_the_current_verifier_version_is_not_stale(self) -> None:
        from app.api.routes.workflows import _version_to_dict
        from app.services.workflows.codegen.verifier import CURRENT_VERIFIER_VERSION
        from app.services.workflows.domain.models import WorkflowVersion

        version = WorkflowVersion(
            version_id="ver-1", workflow_id="wf-1", org_id="org-1",
            verifier_version=CURRENT_VERIFIER_VERSION,
        )

        result = _version_to_dict(version)

        assert result["needsRegeneration"] is False
        assert result["verifierVersion"] == CURRENT_VERIFIER_VERSION

    def test_a_version_predating_the_verifier_field_is_flagged_stale(self) -> None:
        """`verifier_version` defaults to 0 for every version written before
        this field existed -- the exact case Phase 0 confirmed for the
        reported `ctx.now().date()` crash."""
        from app.api.routes.workflows import _version_to_dict
        from app.services.workflows.domain.models import WorkflowVersion

        version = WorkflowVersion(version_id="ver-1", workflow_id="wf-1", org_id="org-1")

        result = _version_to_dict(version)

        assert result["needsRegeneration"] is True
        assert result["verifierVersion"] == 0


class TestHandleEngineError:
    """BUG-1/BUG-6: a version-store failure used to fall through to a bare
    500 with no actionable detail, indistinguishable from any other bug.
    `VersionStoreUnavailableError` must map to 503 so the frontend can tell
    "store unreachable, retry" apart from "no versions yet" (200) or "workflow
    missing" (404)."""

    def test_version_store_unavailable_maps_to_503(self) -> None:
        from app.api.routes.workflows import _handle_engine_error
        from app.services.workflows.domain.errors import VersionStoreUnavailableError

        exc = _handle_engine_error(VersionStoreUnavailableError("wf-1", "graph down"))

        assert isinstance(exc, HTTPException)
        assert exc.status_code == 503
        assert "wf-1" in exc.detail

    def test_pin_failed_maps_to_409_not_500(self) -> None:
        """BUG-2: pinning failing after a successful save is a conflict to
        retry, not an unhandled server error -- the version itself is not
        lost (see `test_version_writer.py`)."""
        from app.api.routes.workflows import _handle_engine_error
        from app.services.workflows.domain.errors import PinFailedError
        from app.services.workflows.domain.models import ArtifactRef, WorkflowVersion

        version = WorkflowVersion(
            version_id="ver-1", version_number=1, workflow_id="wf-1", org_id="org-1",
            bundle_ref=ArtifactRef(artifact_id="art-1"), content_hash="h",
            created_by_user_id="u-1",
        )
        exc = _handle_engine_error(PinFailedError(version, RuntimeError("engine down")))

        assert isinstance(exc, HTTPException)
        assert exc.status_code == 409

    def test_workflow_version_not_found_maps_to_404(self) -> None:
        from app.api.routes.workflows import _handle_engine_error
        from app.services.workflows.domain.errors import WorkflowVersionNotFoundError

        exc = _handle_engine_error(WorkflowVersionNotFoundError("ver-1"))

        assert isinstance(exc, HTTPException)
        assert exc.status_code == 404
