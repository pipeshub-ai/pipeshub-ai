"""`ChatQuery.strictScope` -> `query_dict["filters"]["strictScope"]` wiring.

A project-scoped chat (Node's `applyProjectScope`) sets `strictScope=true`
so `get_accessible_virtual_record_ids` returns no records instead of
falling back to "search everything the user can access" when the
project's effective apps/kb selection is empty (e.g. before the project's
first file upload). This file only covers the route-layer merge into
`filters` -- `tests/unit/modules/retrieval/test_retrieval_service.py`
covers the `search_with_filters` key-casing seam, and the graph-provider
tests cover the actual short-circuit.
"""
from unittest.mock import AsyncMock, MagicMock, patch


class TestStrictScopeMergedIntoFilters:
    @staticmethod
    def _mock_request() -> MagicMock:
        request = MagicMock()
        request.state.user = {"orgId": "org-1", "userId": "user-1", "email": "u@corp.com"}
        request.query_params = {"sendUserInfo": True}
        request.app.container.logger.return_value = MagicMock()
        return request

    async def _run(self, query_info):
        from app.api.routes.chatbot import _generate_chat_stream_via_agent_loop

        request = self._mock_request()

        async def _fake_run_chat_stream(*args, **kwargs):
            yield "event: complete\ndata: {}\n\n"

        with (
            patch(
                "app.api.routes.chatbot.get_llm_for_chat",
                new=AsyncMock(return_value=(MagicMock(), {"provider": "openai", "isMultimodal": False}, {})),
            ),
            patch(
                "app.api.routes.chatbot.run_chat_stream", side_effect=_fake_run_chat_stream,
            ) as mock_run_chat_stream,
        ):
            [
                chunk
                async for chunk in _generate_chat_stream_via_agent_loop(
                    request, query_info, AsyncMock(), MagicMock(), AsyncMock(),
                )
            ]
        return mock_run_chat_stream.call_args.args[0]

    async def test_strict_scope_true_is_merged_into_filters(self):
        from app.api.routes.chatbot import ChatQuery

        query_info = ChatQuery(
            query="what is in this project?",
            filters={"apps": [], "kb": []},
            strictScope=True,
        )

        query_dict = await self._run(query_info)

        assert query_dict["filters"] == {"apps": [], "kb": [], "strictScope": True}

    async def test_strict_scope_false_by_default_does_not_add_key(self):
        """Non-project chats never set strictScope -- the key must be
        absent so existing "search everything" behavior for the default
        Chat Assistant is unaffected."""
        from app.api.routes.chatbot import ChatQuery

        query_info = ChatQuery(query="hello", filters={"apps": ["confluence"]})

        query_dict = await self._run(query_info)

        assert query_dict["filters"] == {"apps": ["confluence"]}
        assert "strictScope" not in query_dict["filters"]

    async def test_strict_scope_true_with_no_filters_at_all(self):
        """A brand-new project with zero explicit sources sends no
        `filters` at all -- strictScope must still land in the merged dict
        rather than being dropped because `filters` was None."""
        from app.api.routes.chatbot import ChatQuery

        query_info = ChatQuery(query="hello", filters=None, strictScope=True)

        query_dict = await self._run(query_info)

        assert query_dict["filters"] == {"strictScope": True}

    async def test_strict_scope_does_not_mutate_caller_filters_dict(self):
        """`effective_filters` must be a copy -- mutating `query_info.filters`
        in place would leak `strictScope` across model re-use/retries."""
        from app.api.routes.chatbot import ChatQuery

        original_filters = {"apps": ["confluence"]}
        query_info = ChatQuery(query="hello", filters=original_filters, strictScope=True)

        await self._run(query_info)

        assert "strictScope" not in original_filters
