"""C-35 (05-migration-plan §6.3, CPD-3): one digest scheme. A digest over JSON is the canonical one (RFC 8785 +
SHA-256, ``harness_core.contracts.canonical_json``); nothing under ``app/`` or ``harness_core/`` hashes
``json.dumps`` text of its own, which drifts from the canonical form on key order, separators, unicode escapes and
numbers. ``ALLOWED`` lists today's hand-rolled digests, each with why it is not canonical yet; it only shrinks, and
an entry that no longer hashes JSON fails. The Node half is ``backend/nodejs/apps/tests/cohesion/c35-one-digest-scheme.test.ts``.
"""

from __future__ import annotations

import ast
from functools import cache
from typing import TYPE_CHECKING

import pytest

from tests.cohesion.support import PYTHON_ROOT, modules

if TYPE_CHECKING:
    from pathlib import Path

# A hang guard, not a bound: the scan parses every module of both trees, which a loaded host slows.
pytestmark = pytest.mark.timeout(180)

ROOTS = ("app", "harness_core")
CANONICAL = "harness_core/contracts/canonical_json.py"

# module:function -> why it is not the canonical digest yet (and who replaces it)
ALLOWED: dict[str, str] = {
    "app/services/vector_db/precision_guard.py:note_debt": (
        "main's weight-precision debt key (#5767), stored as the debt entry's id so a retry adds nothing; a new scheme "
        "lets a retry across the upgrade record an entry twice (outside the harness)"
    ),
    "app/modules/reconciliation/service.py:_content_hash": (
        "indexing's block content hash (main moved it out of build_metadata), stored in the graph beside an md5; a "
        "new scheme re-indexes every record (outside the harness and the control plane)"
    ),
    "app/modules/pipeline/fingerprint.py:_content_hash": (
        "main's staged indexing (#3270): an image block's content hash inside a stage fingerprint, stored with the "
        "stage state; a new scheme reruns every stage once (outside the harness)"
    ),
    "app/modules/pipeline/stages/classify.py:fingerprint": (
        "main's staged indexing (#3270): the classify stage's input fingerprint, stored with the stage state; a new "
        "scheme reruns the stage once (outside the harness)"
    ),
    "app/modules/pipeline/stages/entities.py:fingerprint": (
        "main's staged indexing (#3270): the entities stage's input fingerprint, stored with the stage state; a new "
        "scheme reruns the stage once (outside the harness)"
    ),
    "app/agents/mcp/tool_cache.py:target_fingerprint": (
        "main's MCP tool-cache key (#4003), stored with each cached tool list; a new scheme misses every cached entry "
        "once (outside the harness)"
    ),
    "app/connectors/sources/mariadb/connector.py:_table_fingerprint": (
        "main's MariaDB change detection, stored in the sync point; a new scheme re-reads every table once (outside "
        "the harness)"
    ),
    "app/connectors/sources/web/checkpoint.py:settings_fingerprint": (
        "main's Web crawl checkpoint key (#4219), stored with the checkpoint; a new scheme restarts every resumable "
        "crawl (outside the harness)"
    ),
    "app/connectors/sources/zendesk/connector.py:_content_revision": (
        "main's Zendesk connector (#2836) change detection, stored as each record's external revision; a new scheme "
        "re-indexes every ticket and article once (outside the harness)"
    ),
    # Main's (89767b97e4), merged by WP-106: each is stored or shared across processes, so a new scheme is main's change.
    "app/connectors/sources/atlassian/confluence_cloud/connector.py:_sync_listed_but_not_stored": (
        "main's Confluence content-filter tag, stored in the unstored-pages sync point (outside the harness)"
    ),
    "app/connectors/sources/localKB/handlers/kh_bounded.py:bound_key": (
        "main's Knowledge Hub cursor bound, carried in next-page cursors already handed out (outside the harness)"
    ),
    "app/connectors/sources/microsoft/common/outlook_constants.py:mail_content_revision": (
        "main's Outlook change detection, stored as each mail's external revision; a new scheme re-indexes every "
        "mail once (outside the harness)"
    ),
    "app/connectors/sources/servicenow/servicenow/connector.py:_content_revision": (
        "main's ServiceNow change detection, stored as each record's external revision (outside the harness)"
    ),
    "app/connectors/sources/slack/team/connector.py:_content_revision": (
        "main's Slack change detection, stored as each message's external revision (outside the harness)"
    ),
    "app/modules/record_metadata/schemas.py:properties_hash": (
        "main's connector property-schema hash, compared with the stored schema to decide a new version (outside the "
        "harness)"
    ),
    "app/services/graph_db/postgres/kg/stamp.py:stamp_keys": (
        "main's PostgreSQL permission-class hash, interned in the graph (outside the harness)"
    ),
    "app/services/vector_db/pgvector/visibility.py:key": (
        "main's pgvector visibility-scope cache key, equal across processes (outside the harness)"
    ),
    "app/utils/aimodels.py:embedding_config_hash": (
        "an in-process cache key for embedding clients, never stored or compared across processes (outside the "
        "harness)"
    ),
    "app/harness/access/configured.py:_digest": (
        "the model-access route digest pinned on runs (A1); a new scheme changes every pinned route. CPD-6 pins the "
        "LLM configuration snapshot under the canonical digest and retires it"
    ),
    "app/harness/adapters/checkpoint_store.py:_arguments_hash": (
        "the legacy loop's checkpoint argument hash, stored with its checkpoints; retired with the legacy loop"
    ),
    "harness_core/artifacts/types.py:publish_intent_id": (
        "publish intent ids already journaled and stored; a new scheme would not find a retried intent"
    ),
    "app/connectors/sources/google/drive/team/connector.py:_owner_view": (
        "main's Google Drive domain-share reach check (#5726): how a file looked, stored with each candidate and "
        "compared on the next run; a new scheme makes every candidate wait one more run (outside the harness)"
    ),
    "app/config/file_types.py:content_sha256": (
        "main's file-type registry (#5749): the hash written into the generated Node upload table and recomputed by "
        "main's own mimetypes test, so a hand edit fails; a new scheme changes both halves of main's check (outside "
        "the harness)"
    ),
    "harness_core/mcp_hub/types.py:digest": (
        "the MCP hub session key (tenant, server, owner), in-process only; CPD-7's McpItemRuntime keys sessions by "
        "installation and binding"
    ),
    "harness_core/sandbox/mcp_runner/types.py:digest": (
        "MCP runner container names; a new scheme replaces every running runner. CPD-7 keys runners by installation"
    ),
    "harness_core/workspace/types.py:workspace_manifest_content_hash": (
        "workspace manifest hashes stored with each revision and computed the same way by Node "
        "(domain/workspace.ts); a new scheme needs a manifest migration"
    ),
}


def _is_json_dumps(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Attribute) and node.func.attr == "dumps" and isinstance(node.func.value, ast.Name)
         and node.func.value.id == "json")
        or (isinstance(node.func, ast.Name) and node.func.id == "dumps")
    )  # fmt: skip


def _is_sha256(node: ast.Call) -> bool:
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    if name == "sha256":
        return True
    return name == "new" and bool(node.args) and isinstance(node.args[0], ast.Constant) and node.args[0].value == "sha256"


def _hashed_json(scope: ast.AST) -> bool:
    """Whether ``scope`` hashes JSON text: ``sha256(json.dumps(...)...)``, or a name bound to it, or ``.update`` of one
    on a sha256 object."""
    tainted: set[str] = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and any(_is_json_dumps(n) for n in ast.walk(node.value)):
            tainted |= {t.id for t in node.targets if isinstance(t, ast.Name)}

    def json_in(args: list[ast.expr]) -> bool:
        return any(_is_json_dumps(n) or (isinstance(n, ast.Name) and n.id in tainted) for a in args for n in ast.walk(a))

    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        if _is_sha256(node) and json_in(node.args[1:] if isinstance(node.func, ast.Attribute) and node.func.attr == "new" else node.args):
            return True
        updates_a_hash = isinstance(node.func, ast.Attribute) and node.func.attr == "update"
        if updates_a_hash and isinstance(node.func.value, ast.Call) and _is_sha256(node.func.value) and json_in(node.args):  # pyright: ignore[reportAttributeAccessIssue]
            return True
    return False


def hand_rolled_digests(path: Path, root: Path = PYTHON_ROOT) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    rel = path.relative_to(root).as_posix()
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    found = [f"{rel}:{function.name}" for function in functions if _hashed_json(function)]
    outside = ast.Module(
        body=[n for n in tree.body if not isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)],
        type_ignores=[],
    )
    module_level = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    if _hashed_json(outside) or any(
        _hashed_json(ast.Module(body=[b for b in c.body if not isinstance(b, ast.FunctionDef | ast.AsyncFunctionDef)], type_ignores=[]))
        for c in module_level
    ):
        found.append(f"{rel}:<module>")
    return found


def _may_hash_json(path: Path) -> bool:
    # Every form the scan finds names both; the two trees are read, and only such modules parsed.
    source = path.read_text(encoding="utf-8-sig")
    return "sha256" in source and "dumps" in source


@cache
def _scan() -> list[str]:
    return sorted({
        hit
        for path in modules(*ROOTS)
        if path.relative_to(PYTHON_ROOT).as_posix() != CANONICAL and _may_hash_json(path)
        for hit in hand_rolled_digests(path)
    })


def test_c35_no_new_digest_over_json_text() -> None:
    unlisted = [hit for hit in _scan() if hit not in ALLOWED]
    assert unlisted == [], f"hash JSON with harness_core.contracts.canonical_json (canonical_digest): {unlisted}"


def test_c35_every_allowed_entry_still_hashes_json() -> None:
    stale = sorted(set(ALLOWED) - set(_scan()))
    assert stale == [], f"no longer a hand-rolled digest; remove from ALLOWED: {stale}"


def test_c35_the_scan_finds_each_form(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import hashlib, json\n"
        "def direct(x):\n    return hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()\n"
        "def named(x):\n    text = json.dumps(x)\n    return hashlib.sha256(text.encode()).hexdigest()\n"
        "def updated(x):\n    h = hashlib.sha256()\n    return hashlib.sha256().update(json.dumps(x).encode())\n"
        "def by_name(x):\n    return hashlib.new('sha256', json.dumps(x).encode()).hexdigest()\n"
        "def fine(raw):\n    return hashlib.sha256(raw).hexdigest()\n",
        encoding="utf-8",
    )
    assert hand_rolled_digests(planted, tmp_path) == [
        "planted.py:direct", "planted.py:named", "planted.py:updated", "planted.py:by_name",
    ]  # fmt: skip
