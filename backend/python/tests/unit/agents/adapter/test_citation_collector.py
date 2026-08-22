"""`CitationCollector` (`app/agents/agent_loop/hooks/citations.py`) — a
read-only view over `AgentContext.tool_state`'s citation-related fields, and
`_FetchFullRecordTool`'s rebuild-fresh-every-call contract."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.citations import (
    CitationCollector,
    _FetchFullRecordTool,
)


def _make_context(**tool_state_overrides) -> AgentContext:
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
    )
    context.tool_state.update(tool_state_overrides)
    return context


class TestCitationCollector:
    def test_defaults_are_empty(self) -> None:
        collector = CitationCollector(_make_context())
        assert collector.final_results == []
        assert collector.virtual_records == {}
        assert collector.tool_records == []
        assert collector.citation_ref_mapper is None

    def test_reflects_live_tool_state_mutations(self) -> None:
        context = _make_context()
        collector = CitationCollector(context)
        assert collector.final_results == []

        context.tool_state["final_results"].append({"id": "rec1"})
        context.tool_state["virtual_record_id_to_result"]["rec1"] = {"content": "hi"}

        assert collector.final_results == [{"id": "rec1"}]
        assert collector.virtual_records == {"rec1": {"content": "hi"}}

    def test_none_values_normalize_to_empty(self) -> None:
        context = _make_context(final_results=None, virtual_record_id_to_result=None)
        collector = CitationCollector(context)
        assert collector.final_results == []
        assert collector.virtual_records == {}


class TestFetchFullRecordTool:
    def test_identity_and_schema(self) -> None:
        context = _make_context()
        collector = CitationCollector(context)
        tool = _FetchFullRecordTool(collector, context)
        assert tool.name == "knowledgegraph__fetch_record"
        assert tool.path == "/dynamic/dynamic/fetch_full_record"
        param_names = {p.name for p in tool.parameters}
        assert "record_ids" in param_names

    def test_validate_never_raises(self) -> None:
        context = _make_context()
        tool = _FetchFullRecordTool(CitationCollector(context), context)
        tool.validate({"anything": "goes"})

    async def test_execute_rebuilds_from_live_virtual_records(self) -> None:
        context = _make_context(virtual_record_id_to_result={"rec1": {"content": "v1"}})
        collector = CitationCollector(context)
        tool = _FetchFullRecordTool(collector, context)

        fake_structured_tool = MagicMock()
        fake_structured_tool.coroutine = AsyncMock(return_value="fetched content")

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_structured_tool,
        ) as create_mock:
            output = await tool.execute(record_ids=["rec1"])

        assert output.success is True
        create_mock.assert_called_once()
        _, kwargs = create_mock.call_args
        assert kwargs["org_id"] == context.org_id
        # First positional arg is the live virtual_records mapping, taken
        # fresh from the collector at execute() time (not a frozen snapshot).
        assert create_mock.call_args[0][0] == {"rec1": {"content": "v1"}}

    async def test_execute_wraps_coroutine_failure_as_failed_output(self) -> None:
        """Errors raised by the wrapped LangChain tool's own coroutine (not
        errors constructing it) are the ones `execute()` catches — see its
        `try`/`except` scope in `hooks/citations.py`."""
        context = _make_context()
        tool = _FetchFullRecordTool(CitationCollector(context), context)

        fake_structured_tool = MagicMock()
        fake_structured_tool.coroutine = AsyncMock(side_effect=RuntimeError("boom"))

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_structured_tool,
        ):
            output = await tool.execute(record_ids=["rec1"])

        assert output.success is False
        assert "boom" in output.error

    async def test_execute_formats_records_as_record_text_not_raw_json(self) -> None:
        """Mirrors the chatbot path's `RecordsHandler` + `record_to_message_content()`
        formatting instead of handing the LLM a raw `{"ok": ..., "records": [...]}`
        dict — the record must come back as `<record>` text, and the raw dict
        keys (`block_containers`, `ok`, `records`) must not leak into the output."""
        context = _make_context()
        tool = _FetchFullRecordTool(CitationCollector(context), context)

        raw_result = {
            "ok": True,
            "records": [{"id": "rec-1", "context_metadata": "Record ID : rec-1"}],
            "record_count": 1,
            "not_available_ids": [],
        }
        fake_structured_tool = MagicMock()
        fake_structured_tool.coroutine = AsyncMock(return_value=raw_result)

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_structured_tool,
        ), patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=(
                [{"type": "text", "text": "<record>\nRecord ID : rec-1\n\nRecord blocks (sorted):\n\n* Block Content: hello\n"}],
                MagicMock(),
            ),
        ) as format_mock:
            output = await tool.execute(record_ids=["rec-1"])

        assert output.success is True
        assert "<record>" in output.data
        assert "Record ID : rec-1" in output.data
        assert "block_containers" not in output.data
        assert '"ok"' not in output.data
        format_mock.assert_called_once()

    async def test_execute_reports_not_available_ids_in_formatted_text(self) -> None:
        # Opt into the RecordIdShortener (disabled by default — see
        # `ChatQuery.enableRecordIdShortening`) since this test asserts on
        # the shortened-id path.
        context = _make_context(enable_record_id_shortening=True)
        tool = _FetchFullRecordTool(CitationCollector(context), context)

        raw_result = {
            "ok": True,
            "records": [{"id": "rec-1", "context_metadata": "Record ID : rec-1"}],
            "record_count": 1,
            "not_available_ids": ["rec-missing"],
        }
        fake_structured_tool = MagicMock()
        fake_structured_tool.coroutine = AsyncMock(return_value=raw_result)

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_structured_tool,
        ), patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=([{"type": "text", "text": "<record>\nRecord ID : rec-1\n"}], MagicMock()),
        ):
            output = await tool.execute(record_ids=["rec-1", "rec-missing"])

        assert output.success is True
        assert "not available" in output.data.lower()
        # "rec-missing" was never surfaced elsewhere (no prior Record ID:
        # occurrence), so shorten_if_known() must NOT mint a new label for
        # it — it passes through unchanged instead of polluting the
        # shortener's namespace with an id the model never saw.
        assert "rec-missing" in output.data
        shortener = context.tool_state["record_id_shortener"]
        assert "rec-missing" not in shortener._full_to_short

    async def test_execute_skips_resolution_when_flag_off(self) -> None:
        """Flag off (default) — no shortener is created, and whatever
        `record_ids` the model passed must reach the wrapped tool call
        byte-for-byte, never run through `.resolve()`."""
        context = _make_context()
        tool = _FetchFullRecordTool(CitationCollector(context), context)

        fake_structured_tool = MagicMock()
        fake_structured_tool.coroutine = AsyncMock(
            return_value={"ok": True, "records": [], "record_count": 0, "not_available_ids": []}
        )

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_structured_tool,
        ):
            await tool.execute(record_ids=["rec-1", "rec-2"])

        assert context.tool_state.get("record_id_shortener") is None
        _, call_kwargs = fake_structured_tool.coroutine.call_args
        assert call_kwargs["record_ids"] == ["rec-1", "rec-2"]

    async def test_execute_resolves_short_labels_when_flag_on(self) -> None:
        """Flag on — a short "R<n>" label minted by an earlier tool call
        (search/navigate/lookup_record) must resolve back to its full id
        before reaching the wrapped tool call."""
        from app.utils.chat_helpers import RecordIdShortener

        shortener = RecordIdShortener()
        short_label = shortener.get_or_create_short_id("rec-full-uuid")
        context = _make_context(enable_record_id_shortening=True, record_id_shortener=shortener)
        tool = _FetchFullRecordTool(CitationCollector(context), context)

        fake_structured_tool = MagicMock()
        fake_structured_tool.coroutine = AsyncMock(
            return_value={"ok": True, "records": [], "record_count": 0, "not_available_ids": []}
        )

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_structured_tool,
        ):
            await tool.execute(record_ids=[short_label])

        _, call_kwargs = fake_structured_tool.coroutine.call_args
        assert call_kwargs["record_ids"] == ["rec-full-uuid"]

    async def test_execute_updates_citation_ref_mapper_in_tool_state(self) -> None:
        """The (possibly new) ref_mapper returned by record_to_message_content
        must be written back so later fetches/citations stay consistent."""
        context = _make_context()
        collector = CitationCollector(context)
        tool = _FetchFullRecordTool(collector, context)

        new_ref_mapper = MagicMock(name="updated_ref_mapper")
        raw_result = {
            "ok": True,
            "records": [{"id": "rec-1", "context_metadata": "Record ID : rec-1"}],
            "not_available_ids": [],
        }
        fake_structured_tool = MagicMock()
        fake_structured_tool.coroutine = AsyncMock(return_value=raw_result)

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_structured_tool,
        ), patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=([{"type": "text", "text": "<record>\n"}], new_ref_mapper),
        ):
            await tool.execute(record_ids=["rec-1"])

        assert context.tool_state["citation_ref_mapper"] is new_ref_mapper

    async def test_second_fetch_of_the_same_record_does_not_redownload(self) -> None:
        """`_fetch_multiple_records_impl` is a write-back cache: it stores what
        it downloads into the `virtual_record_id_to_result` mapping it is
        handed, so a repeat fetch is a map hit. That only holds if `execute()`
        hands it the SAME mapping every call — passing a fresh dict makes the
        write-back unreachable and every repeat re-downloads. Empty map here
        because that is the navigate/lookup path, where retrieval never ran."""
        context = _make_context()
        tool = _FetchFullRecordTool(CitationCollector(context), context)

        downloads: list[str] = []

        def _factory(virtual_records: dict, **_kwargs: object) -> SimpleNamespace:
            async def _coroutine(record_ids: list[str], reason: str = "") -> dict:
                records = []
                for rid in record_ids:
                    cached = next(
                        (r for r in virtual_records.values() if r and r.get("id") == rid),
                        None,
                    )
                    if cached is None:
                        downloads.append(rid)
                        cached = {"id": rid, "context_metadata": f"Record ID : {rid}"}
                        virtual_records[f"v-{rid}"] = cached
                    records.append(cached)
                return {"ok": True, "records": records, "not_available_ids": []}

            return SimpleNamespace(coroutine=_coroutine)

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            side_effect=_factory,
        ), patch(
            "app.utils.chat_helpers.record_to_message_content",
            side_effect=lambda record, ref_mapper=None, **_k: (
                [{"type": "text", "text": f"<record>\n{record['id']}\n"}], ref_mapper
            ),
        ):
            first = await tool.execute(record_ids=["rec-1"])
            second = await tool.execute(record_ids=["rec-1"])

        assert first.success is True
        assert second.success is True
        assert downloads == ["rec-1"], "the second fetch must hit the map, not re-download"

    async def test_repeated_ids_within_one_call_are_collapsed(self) -> None:
        """One call naming the same record twice would render the document
        twice into the same message."""
        context = _make_context()
        tool = _FetchFullRecordTool(CitationCollector(context), context)

        seen: list[list[str]] = []

        async def _coroutine(record_ids: list[str], reason: str = "") -> dict:
            seen.append(list(record_ids))
            return {
                "ok": True,
                "records": [{"id": r, "context_metadata": r} for r in record_ids],
                "not_available_ids": [],
            }

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=SimpleNamespace(coroutine=_coroutine),
        ), patch(
            "app.utils.chat_helpers.record_to_message_content",
            side_effect=lambda record, ref_mapper=None, **_k: (
                [{"type": "text", "text": f"<record>\n{record['id']}\n"}], ref_mapper
            ),
        ):
            await tool.execute(record_ids=["rec-a", "rec-a", "rec-b"])

        assert seen == [["rec-a", "rec-b"]]

    async def test_a_second_agent_in_the_tree_gets_real_content_not_a_pointer(self) -> None:
        """`AgentRuntime` — and so the tool registry, the tool instance and
        `AgentContext` — is shared across a spawn tree, but every child gets a
        fresh `ContextManager()`. A cross-call skip guard keyed on the shared
        context would therefore tell the parent to "re-read it above" for a
        record only its child ever read, leaving it holding prose and no text.
        Fetching is idempotent by design; keep it that way."""
        context = _make_context()
        collector = CitationCollector(context)
        first_agent = _FetchFullRecordTool(collector, context)
        second_agent = _FetchFullRecordTool(collector, context)

        async def _coroutine(record_ids: list[str], reason: str = "") -> dict:
            return {
                "ok": True,
                "records": [{"id": r, "context_metadata": r} for r in record_ids],
                "not_available_ids": [],
            }

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=SimpleNamespace(coroutine=_coroutine),
        ), patch(
            "app.utils.chat_helpers.record_to_message_content",
            side_effect=lambda record, ref_mapper=None, **_k: (
                [{"type": "text", "text": f"<record>\ncontent of {record['id']}\n"}],
                ref_mapper,
            ),
        ):
            await first_agent.execute(record_ids=["rec-1"])
            output = await second_agent.execute(record_ids=["rec-1"])

        assert output.success is True
        assert "content of rec-1" in output.data
        for pointer in ("already", "re-read", "not repeated"):
            assert pointer not in output.data.lower()

    async def test_unexpected_argument_returns_a_correctable_error(self) -> None:
        """`record_id=` (singular) is a plausible model slip. It must surface as
        an error naming the real parameters, never as a silent empty fetch."""
        context = _make_context()
        tool = _FetchFullRecordTool(CitationCollector(context), context)

        output = await tool.execute(record_id="rec-1")

        assert output.success is False
        assert "record_id" in output.error
        assert "record_ids" in output.error

    async def test_execute_falls_back_to_to_tool_output_when_not_ok(self) -> None:
        """When the underlying fetch returns `{"ok": False, ...}` (no
        records to format), the generic `_to_tool_output()` path handles it
        instead of the record-formatting branch."""
        context = _make_context()
        tool = _FetchFullRecordTool(CitationCollector(context), context)

        fake_structured_tool = MagicMock()
        fake_structured_tool.coroutine = AsyncMock(
            return_value={"ok": False, "error": "None of the requested records were found."}
        )

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_structured_tool,
        ):
            output = await tool.execute(record_ids=["missing"])

        assert output.success is False


def test_not_available_ids_do_not_mint_new_short_labels():
    """not_available_ids should use shorten_if_known, not get_or_create_short_id.
    Unknown IDs must pass through unchanged — no namespace pollution."""
    from app.utils.chat_helpers import RecordIdShortener

    shortener = RecordIdShortener()
    shortener.get_or_create_short_id("rec-known")  # R1 — already seen

    # Simulate what citations.py does with not_available_ids
    not_available = ["rec-known", "rec-never-seen"]
    result = [shortener.shorten_if_known(rid) for rid in not_available]

    assert result == ["R1", "rec-never-seen"]
    # Confirm "rec-never-seen" was NOT added to the shortener's mappings
    assert shortener.get_or_create_short_id("rec-new") == "R2"  # counter at 2, not 3
