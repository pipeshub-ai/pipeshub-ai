"""A small org for the knowledge service tests (WP-12): two people, three sources, a retrieval that enforces ACL.

Alice reads a Drive plan and a Slack thread; Bob reads only his own Drive note. Retrieval answers only what the
asking user may read, in the sources the call's filter groups name, as ``get_accessible_virtual_record_ids`` scopes
it in production. The renderer stand-ins label each block with the run's ``refN``, so citations resolve.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.utils.chat_helpers import CitationRefMapper

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-1"
DRIVE, SLACK, KB = "drive-1", "slack-1", "kb-1"
RECORDS: dict[str, dict[str, Any]] = {
    "vr-a": {
        "id": "rec-a", "_key": "rec-a", "virtual_record_id": "vr-a", "record_name": "Alpha plan",
        "connector_name": "DRIVE", "connectorId": DRIVE, "webUrl": "https://drive.example/rec-a",
    },
    "vr-s": {
        "id": "rec-s", "_key": "rec-s", "virtual_record_id": "vr-s", "record_name": "Standup thread",
        "connector_name": "SLACK", "connectorId": SLACK, "webUrl": "https://slack.example/rec-s",
    },
    "vr-k": {
        "id": "rec-k", "_key": "rec-k", "virtual_record_id": "vr-k", "record_name": "Handbook",
        "connector_name": "KB", "connectorId": KB, "webUrl": "https://pipeshub.example/rec-k",
    },
    "vr-b": {
        "id": "rec-b", "_key": "rec-b", "virtual_record_id": "vr-b", "record_name": "Bob's private note",
        "connector_name": "DRIVE", "connectorId": DRIVE, "webUrl": "https://drive.example/rec-b",
    },
}
TEXT = {"vr-a": "ALPHA7319 launch in May", "vr-s": "SLACK4410 ship it", "vr-k": "KB5501 holidays", "vr-b": "BRAVO5581"}
SOURCE = {"vr-a": DRIVE, "vr-s": SLACK, "vr-k": KB, "vr-b": DRIVE}
ACL = {"alice": ("vr-a", "vr-s", "vr-k"), "bob": ("vr-b",)}


@dataclass
class FakeRetrieval:
    """``search_with_filters`` as the ACL-scoped retrieval answers it."""

    calls: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    async def search_with_filters(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.calls.append(kwargs)
        groups = kwargs.get("filter_groups") or {}
        named = set(groups.get("apps") or []) | {k for k in groups.get("kb") or [] if k != "NO_KB_SELECTED"}
        readable = [v for v in ACL.get(kwargs["user_id"], ()) if not named or SOURCE[v] in named]
        return {
            "status_code": 200,
            "searchResults": [
                {
                    "virtual_record_id": v, "block_index": 0, "score": 1.0 - i / 10, "content": TEXT[v],
                    "metadata": {
                        "virtualRecordId": v, "recordId": RECORDS[v]["id"], "recordName": RECORDS[v]["record_name"],
                        "connector": RECORDS[v]["connector_name"], "blockIndex": 0,
                    },
                }
                for i, v in enumerate(readable)
            ],
            "virtual_to_record_map": {v: RECORDS[v] for v in readable},
        }


def chat_state(user: str, retrieval: FakeRetrieval, **changes: Any) -> dict[str, Any]:  # noqa: ANN401
    """The ``ChatState`` a run of ``user`` builds for an agent with the three sources."""
    knowledge = [{"connectorId": DRIVE}, {"connectorId": SLACK}, {"connectorId": KB, "type": "KB"}]
    state: dict[str, Any] = {
        "logger": MagicMock(),
        "retrieval_service": retrieval,
        "graph_provider": AsyncMock(),
        "config_service": MagicMock(),
        "org_id": ORG,
        "user_id": user,
        "user_info": {"userId": user, "orgId": ORG},
        "filters": {"apps": [DRIVE, SLACK], "kb": [KB]},
        "agent_knowledge": knowledge,
        "apps": [DRIVE, SLACK],
        "kb": [KB],
        "has_knowledge": True,
        "is_placeholder_agent": False,
        "final_results": [],
        "llm": None,
    }
    state.update(changes)
    return state


async def _flatten(results: list[dict[str, Any]], _blob: Any, _org: Any, _mm: Any, records: dict[str, Any], *_a: Any, **_k: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    for result in results:
        records[result["virtual_record_id"]] = RECORDS[result["virtual_record_id"]]
    return [dict(r) for r in results]


def _render(units: list[dict[str, Any]], records: dict[str, Any], *_a: Any, ref_mapper: CitationRefMapper | None = None, **_k: Any) -> tuple[list[list[dict[str, Any]]], CitationRefMapper]:  # noqa: ANN401
    mapper = ref_mapper if ref_mapper is not None else CitationRefMapper()
    blocks = []
    for unit in units:
        record = records[unit["virtual_record_id"]]
        ref = mapper.get_or_create_ref(f"{record['webUrl']}#block-{unit['block_index']}")
        blocks.append([{"type": "text", "text": f"[{ref}] {record['record_name']}: {unit['content']}"}])
    return blocks, mapper


@contextlib.contextmanager
def rendering() -> Iterator[None]:
    """The search's I/O edges (blob storage, graph enrichment, the record table) as stand-ins."""
    search = "app.agents.actions.knowledge_graph.ops.search"
    escalation = "app.modules.agents.record_escalation"
    with contextlib.ExitStack() as stack:
        for target, kwargs in (
            (f"{search}.BlobStorage", {}),
            (f"{search}.get_record_id_shortener_if_enabled", {"return_value": None}),
            (f"{escalation}.render_coverage_note", {"return_value": ""}),
            (f"{escalation}.render_candidate_table", {"return_value": ""}),
            (f"{escalation}.build_candidates", {"return_value": MagicMock(has_candidates=False)}),
            (f"{escalation}.analyze_coverage", {"return_value": {}}),
            ("app.modules.retrieval.context.builder.enrich_records_with_graph_context", {"new_callable": AsyncMock}),
            ("app.modules.retrieval.context.builder.get_flattened_results", {"side_effect": _flatten}),
            ("app.modules.retrieval.context.renderer.build_message_content_array", {"side_effect": _render}),
        ):
            stack.enter_context(patch(target, **kwargs))
        yield


__all__ = ["ACL", "DRIVE", "KB", "ORG", "RECORDS", "SLACK", "TEXT", "FakeRetrieval", "chat_state", "rendering"]
