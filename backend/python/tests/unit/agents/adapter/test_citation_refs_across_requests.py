"""HG-06: a citation `refN` only ever resolves to content shown in this request.

`CitationRefMapper` numbers refs per request, but the text that carries them
outlives the request twice over: the transcript's tool-result preview, which
the next request replays, and the durable artifact a large web or fetch
result is stored as, which the next request reads back with
`retrieve_artifact_content`. A ref copied from either resolved to whatever
the next request numbered the same, so an answer linked a different record,
or a competitor's page.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

from app.agent_loop_lib.events.base import AgentEvent, EventType, RunContext
from app.agent_loop_lib.tools.builtin.data.retrieve_artifact import (
    RetrieveArtifactContentTool,
)
from app.agents.agent_loop.artifact_store import RegistryBackedStore
from app.agents.agent_loop.factory import _convert_conversation_turn
from app.agents.agent_loop.hooks.artifact_context import _extract_conversation_metadata
from app.agents.agent_loop.protocol.transcript_collector import TranscriptCollector
from app.services.artifact_registry.models import Actor
from app.utils.chat_helpers import CitationRefMapper
from app.utils.citations import _resolve_ref, display_url_for_llm

_REFUND_BLOCK = "https://app/record/AAA/preview#blockIndex=4"
_UNRELATED_BLOCK = "https://app/record/BBB/preview#blockIndex=0"
_VENDOR_PAGE = "https://vendor.example/pricing"
_COMPETITOR_PAGE = "https://competitor.example/blog"
_REF_TOKEN = re.compile(r"ref\d+")
_ROOT = RunContext(role_name="pipeshub-agent", model="gpt-4")


def _resolutions(text: str, mapper: CitationRefMapper) -> set[str]:
    """Every URL a ref token in `text` would resolve to through `mapper`."""
    found = set()
    for token in _REF_TOKEN.findall(text):
        for target in (token, f"https://{token}.xyz"):
            resolved = _resolve_ref(target, mapper.ref_to_url)
            if resolved != target:
                found.add(resolved)
    return found


async def _persist_tool_call(collector: TranscriptCollector, content: str, args: dict[str, Any]) -> dict[str, Any]:
    await collector.emit(AgentEvent(event_type=EventType.RUN_STARTED, run_context=_ROOT, payload={}))
    await collector.emit(AgentEvent(event_type=EventType.TOOL_CALL_START, run_context=_ROOT, payload={
        "tool": "knowledgegraph__search", "args": args, "tool_call_id": "c1",
    }))
    await collector.emit(AgentEvent(event_type=EventType.TOOL_CALL_END, run_context=_ROOT, payload={
        "tool": "knowledgegraph__search", "content": content, "is_error": False, "tool_call_id": "c1",
    }))
    return collector.parts[-1]


def _node_turn(part: dict[str, Any]) -> dict[str, Any]:
    """The `previousConversations` entry Node builds from a saved tool part
    (`toolResultsFromParts` in enterprise_search/utils/utils.ts)."""
    return {
        "role": "bot_response",
        "content": "Refunds are accepted within 30 days.",
        "tool_results": [{
            "tool_id": part["toolCallId"],
            "tool_name": part["toolName"],
            "args": json.loads(part["args"]),
            "result": part.get("resultSummary") or part["resultPreview"],
            "status": "success",
        }],
    }


def _replayed_text(messages) -> str:
    parts = []
    for msg in messages:
        if msg.role == "tool":
            parts.append(str(msg.content))
        parts.extend(str(call.arguments) for call in getattr(msg, "tool_calls", None) or ())
    return "\n".join(parts)


class TestReplayedPreview:
    async def test_a_ref_in_a_replayed_preview_never_resolves_to_this_requests_record(self) -> None:
        first = CitationRefMapper()
        ref = first.get_or_create_ref(_REFUND_BLOCK)
        collector = TranscriptCollector(ref_to_url=lambda: first.ref_to_url)
        part = await _persist_tool_call(
            collector,
            f"Top 1 blocks from 1 records\n[{ref}] Refunds are accepted within 30 days.",
            {"query": "refund policy", "cite": f"https://{ref}.xyz"},
        )

        second = CitationRefMapper()
        second.get_or_create_ref(_UNRELATED_BLOCK)
        replayed = _replayed_text(_convert_conversation_turn(_node_turn(part)))

        assert _UNRELATED_BLOCK not in _resolutions(replayed, second)
        assert _REFUND_BLOCK in replayed

    async def test_a_preview_saved_before_the_fix_cannot_be_cited_into_this_request(self) -> None:
        turn = {
            "role": "bot_response",
            "content": "30 days.",
            "tool_results": [{
                "tool_name": "knowledgegraph__search", "tool_id": "c1", "status": "success",
                "args": {"url": "https://ref1.xyz"},
                "result": "Top 1 blocks from 1 records\n[ref1] Refunds are accepted within 30 days.",
            }],
        }
        second = CitationRefMapper()
        second.get_or_create_ref(_UNRELATED_BLOCK)

        replayed = _replayed_text(_convert_conversation_turn(turn))

        assert _resolutions(replayed, second) == set()
        assert "Refunds are accepted within 30 days." in replayed

    async def test_a_preview_cut_inside_a_ref_does_not_keep_a_shorter_ref(self) -> None:
        mapper = CitationRefMapper()
        for i in range(12):
            mapper.get_or_create_ref(f"https://app/record/R{i}/preview#blockIndex=0")
        collector = TranscriptCollector(ref_to_url=lambda: mapper.ref_to_url)
        # tool_loop cut the preview between "ref1" and "2" (`_result_preview`).
        part = await _persist_tool_call(collector, "rows\n[ref1… [truncated: 9000 chars total]", {})

        assert "R0/" not in part["resultPreview"]
        assert _REF_TOKEN.search(part["resultPreview"]) is None


class _BlobRegistry:
    """The two `ArtifactRegistryService` calls the store makes, over a dict:
    what `register` saves is what a later request's `get_content` returns."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def register(self, *, content: bytes, **_: object) -> SimpleNamespace:
        artifact_id = f"artifact-{len(self.blobs) + 1}"
        self.blobs[artifact_id] = content
        return SimpleNamespace(artifact_id=artifact_id)

    async def get_content(self, *, actor: Actor, artifact_id: str) -> bytes | None:
        return self.blobs.get(artifact_id)


_ACTOR = Actor(org_id="org-1", user_id="user-1")


class TestArtifactReadBack:
    async def test_a_ref_in_an_artifact_read_next_request_never_resolves_to_this_requests_page(self) -> None:
        registry, actor = _BlobRegistry(), _ACTOR
        first = CitationRefMapper()
        shown = display_url_for_llm(_VENDOR_PAGE, first)
        store_1 = RegistryBackedStore(registry, actor, "conv-1", ref_to_url=lambda: first.ref_to_url)
        artifact_id = await store_1.store(
            f"title: Pricing\nurl/Citation ID: {shown}\ncontent: $40/seat", tool_name="dynamic__web_search",
        )

        second = CitationRefMapper()
        display_url_for_llm(_COMPETITOR_PAGE, second)
        store_2 = RegistryBackedStore(registry, actor, "conv-1", ref_to_url=lambda: second.ref_to_url)
        out = await RetrieveArtifactContentTool(store=store_2).execute(artifact_id=artifact_id)

        assert out.success
        assert _COMPETITOR_PAGE not in _resolutions(out.data, second)
        assert _VENDOR_PAGE in out.data

    async def test_the_same_request_still_reads_its_own_refs(self) -> None:
        registry, actor = _BlobRegistry(), _ACTOR
        mapper = CitationRefMapper()
        shown = display_url_for_llm(_VENDOR_PAGE, mapper)
        store = RegistryBackedStore(registry, actor, "conv-1", ref_to_url=lambda: mapper.ref_to_url)
        artifact_id = await store.store(f"url/Citation ID: {shown}\ncontent: $40/seat", tool_name="web")

        out = await RetrieveArtifactContentTool(store=store).execute(artifact_id=artifact_id)

        assert _resolutions(out.data, mapper) == {_VENDOR_PAGE}

    async def test_an_artifact_saved_before_the_fix_cannot_be_cited_into_this_request(self) -> None:
        registry, actor = _BlobRegistry(), _ACTOR
        legacy = await registry.register(content=b"url/Citation ID: https://ref1.xyz\ncontent: $40/seat")
        second = CitationRefMapper()
        display_url_for_llm(_COMPETITOR_PAGE, second)
        store = RegistryBackedStore(registry, actor, "conv-1")

        out = await RetrieveArtifactContentTool(store=store).execute(artifact_id=legacy.artifact_id)

        assert _resolutions(out.data, second) == set()
        assert "$40/seat" in out.data



class TestArtifactReminder:
    def test_the_earlier_artifact_reminder_carries_no_earlier_refs(self) -> None:
        meta = _extract_conversation_metadata([{
            "role": "bot_response",
            "tool_results": [{
                "artifact_id": "a1", "args": {"url": "https://ref2.xyz"},
                "result_summary": "[ref1] Refunds are accepted within 30 days.",
            }],
        }])
        second = CitationRefMapper()
        second.get_or_create_ref(_UNRELATED_BLOCK)
        second.get_or_create_ref(_COMPETITOR_PAGE)

        assert _resolutions(json.dumps(meta), second) == set()
        assert "Refunds are accepted" in meta["a1"]["summary"]
