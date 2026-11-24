"""No hand-kept list of file types outside the registry, beyond the reviewed ones below.

A literal list, tuple, set or dict whose strings are mostly known extensions or mime types, three
or more of them, is a second copy of the registry. Derive it from ``app.config.file_types``
instead. The allowlist holds the lists that decide something other than indexing (agent tools,
previews, the code graph, media) and is exact both ways: a new list fails, and so does a stale
count once a list is removed.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from app.config import file_types

_APP = Path(__file__).resolve().parents[4] / "app"
_REGISTRY = _APP / "config" / "file_types.py"

_AGENT_TOOLS = "agent tools and attachments: what a tool or a chat upload accepts"
_CODE_GRAPH = "code graph and grammar config: language families, not indexing"
_MEDIA = "audio and video handling"
_SOURCE = "a connector's own source-side types"
_IMAGES = "image and artifact handling, not indexing"
_PINNED = "a registration list the pinned and invariant tests check against the registry"

ALLOWED: dict[str, tuple[int, str]] = {
    "agents/actions/artifacts/artifacts.py": (1, _AGENT_TOOLS),
    "agents/actions/code_graph/code_graph.py": (4, _CODE_GRAPH),
    "agents/actions/google/drive/drive.py": (1, _AGENT_TOOLS),
    "agents/actions/util/parse_file.py": (4, _AGENT_TOOLS),
    "agents/agent_loop/evals/decomposition_scorer.py": (1, _AGENT_TOOLS),
    "agents/agent_loop/hooks/attachment_resolver.py": (1, _AGENT_TOOLS),
    "agents/mcp/logo.py": (1, _IMAGES),
    "api/routes/speech.py": (1, _MEDIA),
    "connectors/api/router.py": (2, "preview and conversion choices of the stream route"),
    "connectors/sources/github_teams/comments.py": (1, _IMAGES),
    "connectors/sources/gitlab/attachments.py": (1, _IMAGES),
    "connectors/sources/gitlab/constants.py": (1, _IMAGES),
    "connectors/sources/google/drive/utils/drive_mime.py": (1, "Google Drive export formats"),
    "connectors/sources/linear/connector.py": (1, _SOURCE),
    "connectors/sources/localKB/push/contract.py": (1, _SOURCE),
    "connectors/sources/localKB/push/images.py": (1, _IMAGES),
    "connectors/sources/localKB/push/materialiser_child.py": (1, _IMAGES),
    "connectors/sources/notion/block_parser.py": (2, _MEDIA),
    "connectors/sources/notion/connector.py": (2, _MEDIA),
    "connectors/sources/web/connector.py": (3, _SOURCE),
    "migrations/file_type_requeue/gate_v1.py": (4, "the gate's lists frozen as they were, which the re-queue compares against the registry"),
    "modules/agents/qna/router.py": (1, _AGENT_TOOLS),
    "modules/artifacts/record_backend.py": (1, _IMAGES),
    "modules/artifacts/writer.py": (1, _IMAGES),
    "modules/code_graph/family_rules.py": (1, _CODE_GRAPH),
    "modules/code_graph/guards.py": (1, _CODE_GRAPH),
    "modules/code_graph/import_resolution.py": (3, _CODE_GRAPH),
    "modules/code_graph/module_refs.py": (2, _CODE_GRAPH),
    "modules/code_graph/resolvers/c_family.py": (2, _CODE_GRAPH),
    "modules/parsers/code_parser/engine.py": (3, _CODE_GRAPH),
    "modules/parsers/code_parser/file_role.py": (1, _CODE_GRAPH),
    "modules/parsers/code_parser/lang_config.py": (3, _CODE_GRAPH),
    "modules/parsers/epub/epub_reader.py": (2, "members inside an EPUB container"),
    "modules/parsers/image_parser/image_parser.py": (1, _IMAGES),
    "modules/transformers/document_extraction.py": (1, _IMAGES),
    "parsing_main.py": (1, _PINNED),
    "sandbox/artifact_upload.py": (2, _IMAGES),
    "sandbox/models.py": (1, _IMAGES),
    "services/artifact_registry/registry.py": (1, _IMAGES),
    "services/storage_documents.py": (1, _IMAGES),
    "sources/external/microsoft/sharepoint/sharepoint.py": (1, _SOURCE),
    "telemetry/modules/activity_metrics.py": (1, "telemetry buckets"),
    "utils/aimodels.py": (6, _MEDIA),
    "utils/attachment_mime_types.py": (2, _AGENT_TOOLS),
    "utils/image_utils.py": (3, _IMAGES),
    "utils/libreoffice_convert.py": (1, _PINNED),
    "utils/text_fragments/models.py": (1, "citation link formats"),
    "web/fetch/parsers.py": (1, "content types of a fetched web page"),
}


def _known() -> tuple[frozenset[str], frozenset[str]]:
    exts = frozenset(e for ft in file_types.FILE_TYPES for e in ft.extensions)
    mimes = frozenset(m.lower() for ft in file_types.FILE_TYPES for m in ft.mimes)
    return exts, mimes


def hand_kept_lists(source: str) -> list[tuple[int, list[str]]]:
    """(line, matched strings) of every literal in *source* that reads as a list of file types."""
    exts, mimes = _known()

    def known(value: str) -> bool:
        v = value.lower()
        return v in mimes if "/" in v else v.lstrip(".") in exts

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            elts = node.elts
        elif isinstance(node, ast.Dict):
            elts = [k for k in node.keys if k is not None]
        else:
            continue
        strings = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        matched = [s for s in strings if known(s)]
        if len(matched) >= 3 and 2 * len(matched) >= len(strings):
            found.append((node.lineno, matched))
    return found


def _scan() -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in sorted(_APP.rglob("*.py")):
        if path == _REGISTRY:
            continue
        hits = hand_kept_lists(path.read_text(encoding="utf-8-sig"))
        if hits:
            counts[path.relative_to(_APP).as_posix()] = len(hits)
    return counts


@pytest.mark.timeout(180)  # parses every module under app/
def test_no_new_hand_kept_list_of_file_types() -> None:
    found = _scan()
    allowed = {path: count for path, (count, _) in ALLOWED.items()}
    new = {p: n for p, n in found.items() if n > allowed.get(p, 0)}
    stale = {p: n for p, n in allowed.items() if found.get(p, 0) < n}
    assert new == {}, f"derive these from app.config.file_types: {new}"
    assert stale == {}, f"lower these allowlist counts: {stale}"


def test_the_scan_finds_a_list_the_gate_used_to_keep() -> None:
    old_gate = 'supported = ["application/pdf", "text/plain", "text/markdown", "image/png"]'
    assert hand_kept_lists(old_gate) == [(1, ["application/pdf", "text/plain", "text/markdown", "image/png"])]
    assert hand_kept_lists('SUFFIXES = {".pdf", ".docx", ".xlsx"}')
    assert hand_kept_lists('ROLES = ("admin", "owner", "pdf")') == []
