"""The skills operations the cross-cutting audit files exercise, one entry per operation.

``path`` is relative to the router; ``body`` is a JSON body the route accepts, or None.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skills_audit_support import (
    MISSING_CANDIDATE_ID,
    MISSING_RESOURCE_PATH,
    MISSING_SKILL_NAME,
    MISSING_VERSION,
    skill_md,
    skill_payload,
    unique_skill_name,
)


@dataclass(frozen=True)
class SkillsOperation:
    method: str
    path: str
    route: str
    scope: str
    body: Any = None
    is_import: bool = False

    @property
    def id(self) -> str:
        return f"{self.method} {self.route.removeprefix('/api/v1')}"


READ, WRITE = "skill:read", "skill:write"

OPERATIONS = [
    SkillsOperation("GET", "", "/api/v1/skills", READ),
    SkillsOperation("GET", "/categories", "/api/v1/skills/categories", READ),
    SkillsOperation("GET", "/search", "/api/v1/skills/search", READ),
    SkillsOperation("GET", "/candidates/pending", "/api/v1/skills/candidates/pending", READ),
    SkillsOperation(
        "POST", f"/candidates/{MISSING_CANDIDATE_ID}/approve",
        "/api/v1/skills/candidates/:candidateId/approve", WRITE,
    ),
    SkillsOperation(
        "POST", f"/candidates/{MISSING_CANDIDATE_ID}/reject",
        "/api/v1/skills/candidates/:candidateId/reject", WRITE,
    ),
    SkillsOperation(
        "POST", "/import/npm/preview", "/api/v1/skills/import/npm/preview", WRITE,
        body={"command_or_name": "spec-audit-no-such-package"}, is_import=True,
    ),
    SkillsOperation(
        "POST", "/import/url/preview", "/api/v1/skills/import/url/preview", WRITE,
        body={"url": "ftp://example.com/spec-audit-skill.zip"}, is_import=True,
    ),
    SkillsOperation(
        "POST", "/import/upload/preview", "/api/v1/skills/import/upload/preview", WRITE, is_import=True,
    ),
    SkillsOperation(
        "POST", "/import/finalize", "/api/v1/skills/import/finalize", WRITE,
        body={"content": skill_md(unique_skill_name())}, is_import=True,
    ),
    SkillsOperation("POST", "", "/api/v1/skills", WRITE, body=skill_payload(unique_skill_name())),
    SkillsOperation("GET", f"/{MISSING_SKILL_NAME}", "/api/v1/skills/:name", READ),
    SkillsOperation("PUT", f"/{MISSING_SKILL_NAME}", "/api/v1/skills/:name", WRITE, body=skill_payload()),
    SkillsOperation(
        "PATCH", f"/{MISSING_SKILL_NAME}/body", "/api/v1/skills/:name/body", WRITE,
        body={"old_string": "spec-audit-old", "new_string": "spec-audit-new"},
    ),
    SkillsOperation(
        "POST", f"/{MISSING_SKILL_NAME}/deprecate", "/api/v1/skills/:name/deprecate", WRITE,
        body={"reason": "spec audit"},
    ),
    SkillsOperation("POST", f"/{MISSING_SKILL_NAME}/disable", "/api/v1/skills/:name/disable", WRITE),
    SkillsOperation("POST", f"/{MISSING_SKILL_NAME}/enable", "/api/v1/skills/:name/enable", WRITE),
    SkillsOperation("GET", f"/{MISSING_SKILL_NAME}/usage", "/api/v1/skills/:name/usage", READ),
    SkillsOperation("DELETE", f"/{MISSING_SKILL_NAME}", "/api/v1/skills/:name", WRITE),
    SkillsOperation("GET", f"/{MISSING_SKILL_NAME}/export", "/api/v1/skills/:name/export", READ),
    SkillsOperation("GET", f"/{MISSING_SKILL_NAME}/versions", "/api/v1/skills/:name/versions", READ),
    SkillsOperation(
        "GET", f"/{MISSING_SKILL_NAME}/versions/{MISSING_VERSION}",
        "/api/v1/skills/:name/versions/:version", READ,
    ),
    SkillsOperation(
        "POST", f"/{MISSING_SKILL_NAME}/rollback", "/api/v1/skills/:name/rollback", WRITE,
        body={"version": MISSING_VERSION},
    ),
    SkillsOperation(
        "GET", f"/{MISSING_SKILL_NAME}/resource?path={MISSING_RESOURCE_PATH}",
        "/api/v1/skills/:name/resource", READ,
    ),
    SkillsOperation(
        "PUT", f"/{MISSING_SKILL_NAME}/resource", "/api/v1/skills/:name/resource", WRITE,
        body={"path": MISSING_RESOURCE_PATH, "content": "spec audit"},
    ),
    SkillsOperation(
        "DELETE", f"/{MISSING_SKILL_NAME}/resource?path={MISSING_RESOURCE_PATH}",
        "/api/v1/skills/:name/resource", WRITE,
    ),
]
