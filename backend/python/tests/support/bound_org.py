"""An autouse fixture body for tests of code that acts for one org through ``app.core.org_context``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core import org_context

if TYPE_CHECKING:
    from collections.abc import Iterator

TEST_ORG = "org-1"


def bind_test_org(org_id: str = TEST_ORG) -> Iterator[str]:
    token = org_context.push(org_context.OrgContext(org_id, None, "job", "job:test", "-"))
    try:
        yield org_id
    finally:
        org_context.pop(token)
