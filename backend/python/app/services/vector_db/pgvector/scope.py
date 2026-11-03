"""Which org a call acts for. The org comes only from the caller (a filter's ``metadata.orgId``, a point's payload, ``org_id=``);
the ambient :mod:`app.core.org_context` is a cross-check, never a source, and a call with no ambient context binds the explicit org
for the unit of work. A missing or blank org raises :class:`OrgRequired`; one that differs from the ambient org, :class:`OrgMismatch`.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import TYPE_CHECKING

from app.core import org_context
from app.services.vector_db.pgvector.errors import OrgMismatch, OrgRequired

if TYPE_CHECKING:
    from collections.abc import Iterator

_ORG = re.compile(r"^[0-9a-f]{24}$")


def require_org(value: object) -> str:
    """The org id as a string, or OrgRequired (None, empty, blank or not an org id)."""
    if not isinstance(value, str) or not value.strip():
        raise OrgRequired("the call carries no organisation (a non-empty org id is required)")
    if not _ORG.match(value):
        raise OrgRequired("the organisation id is not well formed")
    return value


def ambient_org() -> str | None:
    try:
        return org_context.current().org_id
    except (org_context.MissingOrgContext, org_context.SystemContextNotAllowed):
        return None


@contextmanager
def org_scope(org: object) -> Iterator[str]:
    """Bind ``org`` for the unit of work, after checking it against the ambient org."""
    org = require_org(org)
    ambient = ambient_org()
    if ambient is not None and ambient != org:
        raise OrgMismatch("the call names a different organisation than the one it runs for")
    if ambient is not None:
        yield org
        return
    with org_context.bind(org_context.OrgContext(org, None, "service", "pgvector:explicit-org", "-")):
        yield org


@contextmanager
def catalog_scope() -> Iterator[None]:
    """For global catalog reads, which concern no org: the ambient context when there is one, else the placeholder org
    ``system``, which no row can belong to (the org id format check)."""
    if ambient_org() is not None:
        yield
        return
    with org_context.bind(org_context.OrgContext(org_context.SYSTEM_ORG_ID, None, "service", "pgvector:catalog", "-")):
        yield


def ambient_or_required() -> str:
    """For calls whose interface has no org parameter (retrieve_points, get_collection_info): the ambient org."""
    org = ambient_org()
    if org is None:
        raise OrgRequired("this call takes its organisation from the request context and none is bound")
    return org
