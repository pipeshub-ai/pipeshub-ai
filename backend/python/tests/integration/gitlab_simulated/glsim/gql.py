"""GitLab GraphQL for the repository tree walk.

The connector's query was validated against the schema introspected from gitlab.com when
``specs/connector_tree_query.graphql`` was saved; that schema is not kept here. This module
answers only that exact query: any other query is a gap and fails the test, so a changed
query is noticed and validated again.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from tests.integration.gitlab_simulated.glsim.model import (
    Project,
    User,
    World,
    git_blob_sha,
)

_QUERY = Path(__file__).resolve().parent.parent / "specs" / "connector_tree_query.graphql"


def _normal(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip()


def _tree_page(gl: World, p: Project, ref: str, after: str) -> dict[str, Any]:
    branch = p.default_branch if ref in ("HEAD", "") else ref
    sha = p.branches.get(branch) or (ref if ref in p.commits else None)
    if sha is None:
        return {"nodes": [], "pageInfo": {"endCursor": None, "hasNextPage": False}}
    tree = p.commits[sha].tree
    dirs = sorted({"/".join(path.split("/")[:k]) for path in tree for k in range(1, path.count("/") + 1)})
    entries = sorted([(d, "tree") for d in dirs] + [(f, "blob") for f in tree])
    start = 0
    if after:
        last = base64.b64decode(after.encode()).decode()
        start = next((n + 1 for n, (path, _) in enumerate(entries) if path == last), len(entries))
    size = gl.behaviour.graphql_tree_page_size
    chunk = entries[start:start + size]

    def node(path: str, kind: str) -> dict[str, Any]:
        web = f"/{p.path_with_namespace}/-/{kind}/{ref}/{path}"
        ident = git_blob_sha(tree[path]) if kind == "blob" else git_blob_sha(path.encode())
        return {"name": path.rsplit("/", 1)[-1], "path": path, "sha": ident, "type": kind, "webPath": web,
                "webUrl": f"{gl.base_url}{web}"}

    end = base64.b64encode(chunk[-1][0].encode()).decode() if chunk else None
    return {"nodes": [{"trees": {"nodes": [node(x, k) for x, k in chunk if k == "tree"]},
                       "blobs": {"nodes": [node(x, k) for x, k in chunk if k == "blob"]}}],
            "pageInfo": {"endCursor": end, "hasNextPage": start + size < len(entries)}}


def execute(gl: World, render: Any, me: User, body: bytes) -> Any:  # noqa: ANN401
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        return {"errors": [{"message": "Invalid JSON"}]}
    if _normal(req.get("query") or "") != _normal(_QUERY.read_text()):
        gl.gaps.append("GraphQL: the connector sends a query other than the one validated against GitLab's "
                       "schema (specs/connector_tree_query.graphql); validate the new one and update the file")
        return {"errors": [{"message": "unvalidated query"}]}
    v = req.get("variables") or {}
    p = gl.project_by_ref(v.get("fullPath") or "")
    if p is None or not gl.can_read_project(me, p):
        return {"data": {"project": None}}
    repo = None
    if gl.can_read_code(me, p):
        repo = {"rootRef": p.default_branch if p.head() else None,
                "paginatedTree": _tree_page(gl, p, v.get("branch") or "HEAD", v.get("afterCursor") or "")}
    return {"data": {"project": {"name": p.name, "repository": repo}}}
