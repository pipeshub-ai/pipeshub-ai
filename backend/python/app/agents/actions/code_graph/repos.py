"""Which indexed repositories a caller can read, addressed by name.

The in-product tools take a ``connector_id`` because the agent copies one from
a search hit. A caller outside PipesHub (an MCP client) has no search hit and
no reason to know internal ids, so it names a repo the way a person does --
``acme/shop`` -- and this module turns that into the connector and record
group the tools need.

A repo is listed only when the caller can read at least one of its code files,
so a name is never confirmed to someone who could not read what it holds.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.models.entities import RecordType
from app.modules.code_graph.connectors import (
    NORMALIZED_CODE_TYPES,
    normalize_connector_type,
)

from .ops import RecordAccess, _unwrap, repo_names

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

__all__ = [
    "AmbiguousRepoNameError",
    "CodeRepo",
    "RepoNotFoundError",
    "list_code_repos",
    "ambiguous_repo_message",
    "matching_code_repos",
    "repo_for_record",
    "repo_not_found_message",
    "resolve_code_repo",
]

# Every git connector files code under `<repo id>-code-repository`
# (github_teams/projects.py, gitlab/projects.py); the plain GitHub and
# GitLab Personal connectors reuse those syncs.
_CODE_GROUP_SUFFIX = "-code-repository"

# Code groups are permissioned as a whole (RECORD_GROUP_LEVEL, inherited), so
# a handful of files answers "can this caller read the repo".
_ACCESS_SAMPLE = 5
_ACCESS_CONCURRENCY = 8


@dataclass(frozen=True)
class CodeRepo:
    name: str
    connector_id: str
    connector_name: str
    group_id: str

    @property
    def qualified(self) -> str:
        """``<connector name>:<repo>``, which tells apart one repo synced twice."""
        return f"{self.connector_name}:{self.name}"


class RepoNotFoundError(Exception):
    pass


class AmbiguousRepoNameError(Exception):
    def __init__(self, repo: str, repos: list[str]) -> None:
        self.repos = repos
        super().__init__(ambiguous_repo_message(repo, repos))


async def _code_connectors(graph_provider: "IGraphDBProvider", org_id: str, user_id: str) -> dict[str, str]:
    instances = await graph_provider.get_user_connector_instances(
        collection=CollectionNames.APPS.value,
        user_id=user_id,
        org_id=org_id,
        team_scope=ConnectorScope.TEAM.value,
        personal_scope=ConnectorScope.PERSONAL.value,
    ) or []
    out: dict[str, str] = {}
    for inst in instances:
        key = inst.get("_key") or inst.get("id")
        if key and normalize_connector_type(inst.get("type")) in NORMALIZED_CODE_TYPES:
            out[key] = inst.get("name") or inst.get("type") or key
    return out


async def _readable(
    graph_provider: "IGraphDBProvider", org_id: str, repo: CodeRepo, access: RecordAccess,
    gate: asyncio.Semaphore,
) -> bool:
    async with gate:
        records = await graph_provider.get_records_by_record_group(
            record_group_id=repo.group_id, connector_id=repo.connector_id,
            org_id=org_id, depth=0, limit=_ACCESS_SAMPLE,
        ) or []
        ids = [
            getattr(r, "id", None) for r in records
            if getattr(r, "record_type", None) == RecordType.CODE_FILE
        ]
        return bool(await access.readable(ids))


async def _candidates(
    graph_provider: "IGraphDBProvider", org_id: str, user_id: str,
) -> list[CodeRepo]:
    """Code repos in the caller's code connectors, before the per-repo access check."""
    connectors = await _code_connectors(graph_provider, org_id, user_id)
    if not connectors:
        return []
    groups = [
        g for g in (
            _unwrap(row) for row in await graph_provider.get_nodes_by_field_in(
                collection=CollectionNames.RECORD_GROUPS.value, field_name="connectorId",
                field_values=sorted(connectors),
                return_fields=["_key", "orgId", "connectorId", "externalGroupId"],
                raise_on_error=True,
            ) or []
        )
        if g.get("orgId") == org_id and g.get("_key") and g.get("connectorId") in connectors
        and str(g.get("externalGroupId") or "").endswith(_CODE_GROUP_SUFFIX)
    ]
    names = await repo_names(graph_provider, org_id, (g["_key"] for g in groups))
    return [
        CodeRepo(
            name=names.get(g["_key"]) or g["_key"],
            connector_id=g["connectorId"],
            connector_name=connectors[g["connectorId"]],
            group_id=g["_key"],
        )
        for g in groups
    ]


async def _keep_readable(
    graph_provider: "IGraphDBProvider", org_id: str, user_id: str, repos: list[CodeRepo],
) -> list[CodeRepo]:
    access = RecordAccess(graph_provider, org_id, user_id)
    gate = asyncio.Semaphore(_ACCESS_CONCURRENCY)
    readable = await asyncio.gather(
        *(_readable(graph_provider, org_id, r, access, gate) for r in repos)
    )
    return sorted((r for r, ok in zip(repos, readable) if ok),
                  key=lambda r: (r.name.casefold(), r.connector_name))


async def list_code_repos(
    graph_provider: "IGraphDBProvider", org_id: str, user_id: str,
) -> list[CodeRepo]:
    """Every code repo the caller can read, sorted by name."""
    return await _keep_readable(
        graph_provider, org_id, user_id, await _candidates(graph_provider, org_id, user_id),
    )


def _matches(repo: CodeRepo, wanted: str) -> bool:
    wanted = wanted.strip().strip("/")
    connector, sep, rest = wanted.partition(":")
    if sep and connector.casefold() == repo.connector_name.casefold():
        wanted = rest.strip("/")
    name, wanted = repo.name.casefold(), wanted.casefold()
    return wanted in (name, repo.group_id.casefold()) or name.endswith("/" + wanted)


def repo_not_found_message(repo: str | None, record_id: str | None = None) -> str:
    """What a caller is told when *repo* (or *record_id*, given no repo) names nothing readable.

    Built from the caller's own input, never from an exception.
    """
    if not (repo or "").strip():
        if record_id:
            return f"record_id {record_id!r} is not an indexed code file you can read."
        return "Give `repo`, e.g. `acme/shop` -- pipeshub_code_repos lists them."
    return f"No indexed repository you can read matches {repo!r}. pipeshub_code_repos lists the ones you can."


def ambiguous_repo_message(repo: str, names: list[str]) -> str:
    return (
        f"{repo!r} matches {len(names)} repositories: {', '.join(names)}. "
        "Call again with `repo` set to one of them."
    )


async def matching_code_repos(
    graph_provider: "IGraphDBProvider", org_id: str, user_id: str, repo: str,
) -> list[CodeRepo]:
    """The readable repos *repo* names: ``owner/name``, ``name`` or ``<connector>:owner/name``.

    An exact name wins over suffix matches; a blank name matches nothing.
    """
    if not (repo or "").strip():
        return []
    # Only the repos the name picks pay for an access check.
    named = [r for r in await _candidates(graph_provider, org_id, user_id) if _matches(r, repo)]
    matches = await _keep_readable(graph_provider, org_id, user_id, named)
    exact = [r for r in matches if r.name.casefold() == repo.strip().strip("/").casefold()]
    return exact or matches


async def resolve_code_repo(
    graph_provider: "IGraphDBProvider", org_id: str, user_id: str, repo: str,
) -> CodeRepo:
    """The one readable repo *repo* names.

    Raises ``RepoNotFoundError`` when nothing readable matches, and
    ``AmbiguousRepoNameError`` listing the qualified names when several do.
    """
    matches = await matching_code_repos(graph_provider, org_id, user_id, repo)
    if not matches:
        raise RepoNotFoundError(repo_not_found_message(repo))
    if len(matches) > 1:
        raise AmbiguousRepoNameError(repo, sorted(r.qualified for r in matches))
    return matches[0]


async def repo_for_record(
    graph_provider: "IGraphDBProvider", org_id: str, user_id: str, record_id: str,
) -> CodeRepo:
    """The readable repo holding *record_id*, for a caller that has a search hit and no repo name.

    An unreadable, missing and non-code record all get the same answer.
    """
    not_found = RepoNotFoundError(repo_not_found_message(None, record_id))
    if not await RecordAccess(graph_provider, org_id, user_id).readable([record_id]):
        raise not_found
    record = await graph_provider.get_document(
        record_id, CollectionNames.RECORDS.value, raise_on_error=True,
    ) or {}
    group_id = record.get("recordGroupId")
    if record.get("orgId") != org_id or record.get("recordType") != RecordType.CODE_FILE.value:
        raise not_found
    # The record itself was readable, so its repo needs no further check.
    for repo in await _candidates(graph_provider, org_id, user_id):
        if repo.group_id == group_id:
            return repo
    raise not_found
