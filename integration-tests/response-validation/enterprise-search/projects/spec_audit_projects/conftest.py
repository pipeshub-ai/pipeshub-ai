"""Shared fixtures for the strict OpenAPI audit of /api/v1/projects."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[4]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.projects_client import ProjectsClient  # noqa: E402

from projects_audit_support import SeedProject  # noqa: E402


@pytest.fixture
def seed_project(projects_client: ProjectsClient) -> Iterator[SeedProject]:
    """Factory: create one project owned by the admin; every one is deleted on teardown.

    ``seed_project(**fields)`` returns the created project object (id under ``_id``).
    Projects are private by default, so the non-admin member cannot see one until it is
    added with ``projects_client.upsert_members`` or the project is switched to
    ``update_project(id, visibility="org")``.
    """
    created: list[str] = []

    def _seed(**fields: Any) -> dict[str, Any]:
        fields.setdefault("name", f"spec-audit {uuid.uuid4().hex[:8]}")
        resp = projects_client.create_project(**fields)
        if resp.status_code != 201:
            pytest.fail(f"could not seed a project: {resp.status_code} {resp.text[:300]}")
        project: dict[str, Any] = resp.json()["project"]
        created.append(project["_id"])
        return project

    try:
        yield _seed
    finally:
        for project_id in created:
            projects_client.delete_project(project_id)
