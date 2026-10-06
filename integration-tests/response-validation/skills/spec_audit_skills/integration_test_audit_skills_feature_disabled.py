"""Every skills route answers 403 while the org's ENABLE_SKILLS platform flag is off.

The flag is org-wide, so the module turns it off once, keeps the window short, and puts the
previous value back. The calls run as a disposable member: the flag check comes before
every other check, and the import routes count each call against the caller's limiter.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from helper.second_user import SecondUser
from skills_audit_support import SKILLS_DISABLED_DETAIL, request_as, skills_feature_disabled
from skills_operations import OPERATIONS, SkillsOperation
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.fixture(scope="module")
def skills_off(pipeshub_client, import_user: SecondUser) -> Iterator[SecondUser]:
    with skills_feature_disabled(pipeshub_client):
        yield import_user


@pytest.mark.parametrize("operation", OPERATIONS, ids=[op.id for op in OPERATIONS])
def test_route_is_forbidden_while_skills_are_disabled(
    skills_off: SecondUser, operation: SkillsOperation
) -> None:
    kwargs = {"json": operation.body} if operation.body is not None else {}
    if operation.path == "/import/upload/preview":
        kwargs = {"files": {"file": ("spec-audit.zip", b"not read", "application/zip")}}
    resp = request_as(skills_off, operation.method, operation.path, **kwargs)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, operation.route)
    assert resp.json() == {"detail": SKILLS_DISABLED_DETAIL}
