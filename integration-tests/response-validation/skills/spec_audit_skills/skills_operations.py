"""The skills operations the cross-cutting audit files exercise, one entry per operation.

``path`` is relative to the router; ``body`` is a JSON body the route accepts, or None.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skills_audit_support import (
    MISSING_CANDIDATE_ID,
    MISSING_SKILL_NAME,
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
]
