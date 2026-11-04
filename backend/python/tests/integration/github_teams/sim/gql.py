"""GitHub GraphQL, executed against GitHub's published schema (specs/schema.docs.graphql.gz).

A query the real API would reject for its shape is rejected here by graphql-core's
validation against the same schema. Only the fields below are modelled; asking for any
other field records a gap instead of quietly answering null.

Modelled:
- ``user(login:) { organizationVerifiedDomainEmails(login:) }`` — answered only for an
  owner of that organisation and only when it has a verified domain (GitHub docs).
- ``repository(owner:, name:) { defaultBranchRef { name target { ... on Commit {
  history(first:, path:, after:) { totalCount nodes { authoredDate } pageInfo { endCursor } } } } } }``
  with GitHub's ``"<oid> <offset>"`` history cursors. ``object(expression:)`` answers the
  same Commit for ``HEAD``, a branch name or a full commit sha, and null for anything
  else (live, 2026-10-26).
- ``repository { refs(refPrefix: "refs/heads/", first:, after:, query:, orderBy: ALPHABETICAL)
  { nodes { name } pageInfo { hasNextPage endCursor } } }``. ``query`` matches anywhere in
  the name, ignoring case (live, 2026-10-26: "ix" and "FIX" both match ``fix/...``).
- ``repository { issues(first:, after:) { totalCount nodes { number updatedAt } pageInfo { hasNextPage endCursor } } }``
  (keyset cursors; GitHub's default order is CREATED_AT ASC).
"""

from __future__ import annotations

import gzip
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from graphql import GraphQLError, GraphQLSchema, build_schema, execute, parse, validate

from tests.integration.github_teams.sim.model import Org, User, World, iso

if TYPE_CHECKING:
    from collections.abc import Callable

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "specs" / "schema.docs.graphql.gz"


class NotModelled(Exception):
    pass


@lru_cache(maxsize=1)
def schema() -> GraphQLSchema:
    # GitHub's published schema deprecates Project.id while Node.id is not deprecated,
    # which graphql-core 3.3 rejects as an invalid schema. Queries are still validated
    # against it.
    return build_schema(
        gzip.decompress(SCHEMA_PATH.read_bytes()).decode(), assume_valid_sdl=True, assume_valid=True
    )


def _resolver(ws: World) -> Callable[..., Any]:
    def resolve(parent: Any, info: Any, **args: Any) -> Any:  # noqa: ANN401
        name = info.field_name
        if name == "__typename":
            return parent.get("__typename")
        if not isinstance(parent, dict) or name not in parent:
            ws.gaps.append(f"GraphQL field {info.parent_type.name}.{name} is not modelled by the simulator")
            raise NotModelled(f"{info.parent_type.name}.{name}")
        value = parent[name]
        return value(**args) if callable(value) else value
    return resolve


def execute_query(ws: World, viewer: User | None, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    try:
        document = parse(query)
    except GraphQLError as e:
        return {"errors": [{"message": e.message}]}
    errors = validate(schema(), document)
    if errors:
        return {"errors": [{"message": e.message} for e in errors]}
    root = {"user": lambda login: _user(ws, viewer, login),
            "repository": lambda owner, name, **_: _repository(ws, viewer, owner, name)}
    result = execute(schema(), document, root_value=root, variable_values=variables,
                     field_resolver=_resolver(ws))
    out: dict[str, Any] = {"data": result.data}
    if result.errors:
        out["errors"] = [{"message": e.message, "type": (e.extensions or {}).get("type", "FORBIDDEN"),
                          "path": e.path} for e in result.errors]
    return out


def _user(ws: World, viewer: User | None, login: str) -> dict[str, Any] | None:
    user = next((u for u in ws.users.values() if u.login.lower() == login.lower()), None)
    if user is None:
        raise GraphQLError(f"Could not resolve to a User with the login of '{login}'.", extensions={"type": "NOT_FOUND"})

    def verified(login: str) -> list[str]:
        org: Org | None = ws.orgs.get(login.lower())
        if org is None or viewer is None or org.members.get(viewer.id) != "admin":
            raise GraphQLError("Must be an organization owner", extensions={"type": "FORBIDDEN"})
        if not org.verified_domain or user.id not in org.members or not user.corporate_email:
            return []
        return [user.corporate_email]

    return {"__typename": "User", "login": user.login, "organizationVerifiedDomainEmails": verified}


def _repository(ws: World, viewer: User | None, owner: str, name: str) -> dict[str, Any] | None:
    repo = ws.repo_by_name(owner, name)
    if repo is None or not ws.can_read(viewer, repo):
        raise GraphQLError(f"Could not resolve to a Repository with the name '{owner}/{name}'.",
                           extensions={"type": "NOT_FOUND"})
    head = repo.head()

    def commit(sha: str) -> dict[str, Any]:
        def history(first: int = 1, path: str | None = None, after: str | None = None, **_: Any) -> dict[str, Any]:  # noqa: ANN401
            commits = ws.path_history(repo, path, sha) if path else [repo.commits[c] for c in ws.ancestors(repo, sha)]
            offset = int(after.split(" ")[1]) + 1 if after else 0
            page = commits[offset:offset + first]
            end = f"{sha} {offset + len(page) - 1}" if page else None
            return {"totalCount": len(commits), "nodes": [{"authoredDate": iso(c.date), "oid": c.sha} for c in page],
                    "pageInfo": {"endCursor": end, "hasNextPage": offset + len(page) < len(commits)}}
        return {"__typename": "Commit", "oid": sha, "history": history}

    def git_object(expression: str) -> dict[str, Any] | None:
        sha = ws.resolve(repo, expression)
        return commit(sha) if sha else None

    def refs(refPrefix: str, first: int | None = None, after: str | None = None,
             query: str | None = None, orderBy: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        if refPrefix != "refs/heads/" or str((orderBy or {}).get("field", "ALPHABETICAL")) != "ALPHABETICAL":
            ws.gaps.append(f"GraphQL refs(refPrefix: {refPrefix!r}, orderBy: {orderBy!r}) is not modelled by the simulator")
            raise NotModelled("Repository.refs")
        if first is None:
            raise GraphQLError("You must provide a `first` or `last` value to properly paginate the `refs` connection.")
        descending = str((orderBy or {}).get("direction", "ASC")) == "DESC"
        names = sorted((n for n in repo.branches if not query or query.lower() in n.lower()), reverse=descending)
        # Keyset cursors: a branch created or deleted between pages cannot shift another.
        last = after[2:] if after else None
        rest = [n for n in names if last is None or (n < last if descending else n > last)]
        page = rest[:first]
        return {"totalCount": len(names), "nodes": [{"name": n} for n in page],
                "pageInfo": {"hasNextPage": len(rest) > len(page),
                             "endCursor": f"r:{page[-1]}" if page else after}}

    def issues(first: int | None = None, after: str | None = None, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        """Default order CREATED_AT ASC; cursors are keysets (the last number seen), so
        an issue removed during a walk cannot shift another out of it."""
        live = sorted((i for i in repo.issues.values() if not i.deleted and i.transferred_to is None),
                      key=lambda i: i.number)
        start = [i for i in live if after is None or i.number > int(after.split(":")[1])]
        page = [] if ws.issue_numbers_lag else start[: first or 100]
        return {"totalCount": len(live),
                "nodes": [{"number": i.number, "title": i.title, "updatedAt": iso(i.updated_at)} for i in page],
                "pageInfo": {"hasNextPage": bool(page) and len(start) > len(page),
                             "endCursor": f"n:{page[-1].number}" if page else after}}

    def pulls(**_: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"totalCount": len(repo.pulls)}

    return {
        "__typename": "Repository",
        "defaultBranchRef": {"name": repo.default_branch, "target": commit(head.sha)} if head else None,
        "object": git_object,
        "refs": refs,
        "issues": issues,
        "pullRequests": pulls,
    }
