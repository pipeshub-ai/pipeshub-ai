"""Tests for the Linear API gateway: what a failed response means, when a call is
retried, and paging that never takes a failed page for the end of a list."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.linear.api_gateway import (
    RATE_LIMIT_WAITS,
    TRANSIENT_WAITS,
    LinearApiError,
    LinearAuthError,
    LinearForbidden,
    LinearGateway,
    LinearNotFound,
    LinearPaginationError,
    LinearRateLimited,
    LinearTransientError,
    classify,
)
from app.sources.client.graphql.response import GraphQLError, GraphQLResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(data=None) -> GraphQLResponse:
    return GraphQLResponse(success=True, data=data if data is not None else {})


def _failed(message="boom", status=None, code=None, error_type=None, error_message=None) -> GraphQLResponse:
    errors = None
    if code or error_type or error_message:
        extensions = {}
        if code:
            extensions["code"] = code
        if error_type:
            extensions["type"] = error_type
        errors = [GraphQLError(message=error_message or message, extensions=extensions or None)]
    return GraphQLResponse(success=False, message=message, status_code=status, errors=errors)


def _page(nodes, has_next=False, end_cursor=None) -> dict:
    return {"nodes": nodes, "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor}}


def _make_gateway(datasource=None) -> tuple:
    datasource = datasource if datasource is not None else MagicMock()
    provider = AsyncMock(return_value=datasource)
    sleep = AsyncMock()
    gateway = LinearGateway(provider, MagicMock(), sleep=sleep)
    return gateway, datasource, provider, sleep


def _waits(sleep) -> list:
    return [call.args[0] for call in sleep.await_args_list]


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


class TestClassify:
    def test_success_is_not_an_error(self) -> None:
        assert classify(_ok({"viewer": {}})) is None

    @pytest.mark.parametrize("response", [
        _failed(status=429),
        _failed(status=400, code="RATELIMITED"),
        _failed(status=400, code="ratelimited"),
        _failed(status=400, error_type="Ratelimited"),
        _failed(status=None, code="RATELIMITED"),
    ])
    def test_rate_limited(self, response) -> None:
        error = classify(response)
        assert type(error) is LinearRateLimited
        assert error.status == response.status_code

    @pytest.mark.parametrize("response", [
        _failed(status=403),
        _failed(status=400, code="FORBIDDEN"),
    ])
    def test_forbidden_is_about_one_request_not_the_token(self, response) -> None:
        error = classify(response)
        assert type(error) is LinearForbidden
        assert not isinstance(error, LinearAuthError)

    @pytest.mark.parametrize("response", [
        _failed(status=401),
        _failed(status=400, code="AUTHENTICATION_ERROR"),
        _failed(status=None, code="AUTHENTICATION_ERROR"),
    ])
    def test_auth(self, response) -> None:
        assert type(classify(response)) is LinearAuthError

    @pytest.mark.parametrize("response", [
        _failed(message="Entity not found: Issue", status=400),
        _failed(message="entity NOT found", status=200),
        _failed(message="Bad request", status=400, error_message="Could not find referenced Project."),
        _failed(message="Entity not found: Document", status=400, code="INPUT_ERROR"),
    ])
    def test_not_found(self, response) -> None:
        assert type(classify(response)) is LinearNotFound

    @pytest.mark.parametrize("response", [
        _failed(status=None),
        _failed(status=500),
        _failed(status=502),
        _failed(status=503),
        _failed(status=400, code="INTERNAL_SERVER_ERROR"),
        _failed(message="Failed to execute query issues: timed out"),
    ])
    def test_transient(self, response) -> None:
        assert type(classify(response)) is LinearTransientError

    @pytest.mark.parametrize("response", [
        _failed(message="Variable $filter got invalid value", status=400),
        _failed(message="Not found", status=404),
        _failed(message="Query too complex", status=400, code="GRAPHQL_VALIDATION_FAILED"),
    ])
    def test_anything_else_is_a_plain_api_error(self, response) -> None:
        error = classify(response)
        assert type(error) is LinearApiError
        assert error.status == response.status_code

    def test_rate_limit_outranks_auth_and_not_found(self) -> None:
        response = _failed(message="Entity not found", status=401, code="RATELIMITED")
        assert type(classify(response)) is LinearRateLimited

    def test_auth_outranks_not_found(self) -> None:
        response = _failed(message="Entity not found: Team", status=401)
        assert type(classify(response)) is LinearAuthError

    def test_not_found_outranks_missing_status(self) -> None:
        # A transport-level wrapper has no status, but the text is a definite answer.
        assert type(classify(_failed(message="Entity not found: Issue"))) is LinearNotFound

    def test_message_and_status_are_carried(self) -> None:
        error = classify(_failed(message="Unauthorized", status=401))
        assert str(error) == "Unauthorized"
        assert error.status == 401

    def test_missing_message_gets_a_default(self) -> None:
        error = classify(GraphQLResponse(success=False, status_code=500))
        assert str(error) == "Linear request failed"

    def test_bare_mock_failure_is_transient(self) -> None:
        # What most connector tests hand over: success=False and nothing else usable.
        response = MagicMock()
        response.success = False
        error = classify(response)
        assert type(error) is LinearTransientError
        assert error.status is None
        assert str(error) == "Linear request failed"

    def test_boolean_status_is_not_a_status(self) -> None:
        response = MagicMock(success=False, message="odd", status_code=True, errors=None)
        error = classify(response)
        assert type(error) is LinearTransientError
        assert error.status is None

    def test_errors_without_extensions_are_tolerated(self) -> None:
        response = GraphQLResponse(
            success=False, message="first", status_code=400,
            errors=[GraphQLError(message="first"), GraphQLError(message="second", extensions={})],
        )
        assert type(classify(response)) is LinearApiError

    def test_all_typed_errors_are_api_errors(self) -> None:
        for cls in (LinearRateLimited, LinearAuthError, LinearNotFound, LinearTransientError):
            assert issubclass(cls, LinearApiError)


# ---------------------------------------------------------------------------
# LinearGateway.run
# ---------------------------------------------------------------------------


class TestRun:
    @pytest.mark.asyncio
    async def test_returns_data(self) -> None:
        gateway, datasource, provider, sleep = _make_gateway()
        invoke = AsyncMock(return_value=_ok({"viewer": {"id": "u1"}}))

        assert await gateway.run("viewer", invoke) == {"viewer": {"id": "u1"}}

        invoke.assert_awaited_once_with(datasource)
        provider.assert_awaited_once()
        sleep.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data", [None, [], "text"])
    async def test_missing_data_is_an_empty_dict(self, data) -> None:
        gateway, *_ = _make_gateway()
        response = MagicMock(success=True, data=data)
        assert await gateway.run("viewer", AsyncMock(return_value=response)) == {}

    @pytest.mark.asyncio
    async def test_rate_limit_is_waited_out(self) -> None:
        gateway, _, provider, sleep = _make_gateway()
        invoke = AsyncMock(side_effect=[_failed(status=429), _failed(status=429), _ok({"ok": True})])

        assert await gateway.run("issues", invoke) == {"ok": True}

        assert _waits(sleep) == [5, 15]
        assert invoke.await_count == 3
        # The token may have been refreshed while waiting: a new data source each try.
        assert provider.await_count == 3

    @pytest.mark.asyncio
    async def test_rate_limit_gives_up_after_every_wait_is_spent(self) -> None:
        gateway, _, _, sleep = _make_gateway()
        invoke = AsyncMock(return_value=_failed(message="Rate limit exceeded", status=400, code="RATELIMITED"))

        with pytest.raises(LinearRateLimited, match="Rate limit exceeded"):
            await gateway.run("issues", invoke)

        assert _waits(sleep) == list(RATE_LIMIT_WAITS) == [5, 15, 30, 60, 120, 240]
        assert invoke.await_count == len(RATE_LIMIT_WAITS) + 1

    @pytest.mark.asyncio
    async def test_transient_failure_is_retried_three_times(self) -> None:
        gateway, _, _, sleep = _make_gateway()
        invoke = AsyncMock(return_value=_failed(status=503))

        with pytest.raises(LinearTransientError) as exc_info:
            await gateway.run("issues", invoke)

        assert exc_info.value.status == 503
        assert _waits(sleep) == list(TRANSIENT_WAITS) == [1, 3, 9]
        assert invoke.await_count == 4

    @pytest.mark.asyncio
    async def test_transient_failure_that_clears(self) -> None:
        gateway, _, _, sleep = _make_gateway()
        invoke = AsyncMock(side_effect=[_failed(status=None), _ok({"ok": True})])

        assert await gateway.run("issues", invoke) == {"ok": True}
        assert _waits(sleep) == [1]

    @pytest.mark.asyncio
    async def test_rate_limit_and_transient_waits_are_counted_separately(self) -> None:
        gateway, _, _, sleep = _make_gateway()
        invoke = AsyncMock(side_effect=[
            _failed(status=502), _failed(status=429), _failed(status=502),
            _failed(status=429), _failed(status=502), _failed(status=502),
        ])

        with pytest.raises(LinearTransientError):
            await gateway.run("issues", invoke)

        assert _waits(sleep) == [1, 5, 3, 15, 9]
        assert invoke.await_count == 6

    @pytest.mark.asyncio
    @pytest.mark.parametrize("response,expected", [
        (_failed(status=401), LinearAuthError),
        (_failed(message="Entity not found: Issue", status=400), LinearNotFound),
        (_failed(message="Bad variable", status=400), LinearApiError),
    ])
    async def test_definite_answers_are_not_retried(self, response, expected) -> None:
        gateway, _, _, sleep = _make_gateway()
        invoke = AsyncMock(return_value=response)

        with pytest.raises(expected) as exc_info:
            await gateway.run("issue", invoke)

        assert type(exc_info.value) is expected
        invoke.assert_awaited_once()
        sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_each_run_starts_with_fresh_waits(self) -> None:
        gateway, _, _, sleep = _make_gateway()
        failing = AsyncMock(return_value=_failed(status=500))

        for _ in range(2):
            with pytest.raises(LinearTransientError):
                await gateway.run("issues", failing)

        assert _waits(sleep) == [1, 3, 9, 1, 3, 9]

    @pytest.mark.asyncio
    async def test_datasource_failure_is_not_swallowed(self) -> None:
        gateway, _, provider, sleep = _make_gateway()
        provider.side_effect = RuntimeError("No access token available")
        invoke = AsyncMock()

        with pytest.raises(RuntimeError, match="No access token"):
            await gateway.run("issues", invoke)

        invoke.assert_not_awaited()
        sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retry_is_logged_with_the_operation_name(self) -> None:
        gateway, *_ = _make_gateway()
        invoke = AsyncMock(side_effect=[_failed(status=429), _ok()])

        await gateway.run("trashedIssues", invoke)

        args = gateway._logger.warning.call_args.args
        assert "trashedIssues" in args
        assert 5 in args


class TestCall:
    @pytest.mark.asyncio
    async def test_runs_a_registered_operation_by_name(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(return_value=_ok({"team": {"id": "t1"}}))

        assert await gateway.call("team", {"id": "t1"}) == {"team": {"id": "t1"}}
        datasource.execute_operation.assert_awaited_once_with("team", {"id": "t1"})

    @pytest.mark.asyncio
    async def test_no_variables(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(return_value=_ok({"viewer": {}}))

        await gateway.call("viewer")
        datasource.execute_operation.assert_awaited_once_with("viewer", {})

    @pytest.mark.asyncio
    async def test_failure_is_typed(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(
            return_value=_failed(message="Entity not found: Team", status=400))

        with pytest.raises(LinearNotFound):
            await gateway.call("team", {"id": "gone"})


# ---------------------------------------------------------------------------
# LinearGateway.pages / all_nodes
# ---------------------------------------------------------------------------


async def _collect(pages) -> list:
    return [page async for page in pages]


class TestPages:
    @pytest.mark.asyncio
    async def test_follows_the_cursor_to_the_end(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(side_effect=[
            _ok({"issues": _page([{"id": "a"}], True, "c1")}),
            _ok({"issues": _page([{"id": "b"}], True, "c2")}),
            _ok({"issues": _page([{"id": "c"}], False, "c3")}),
        ])
        variables = {"filter": {"team": {"id": {"eq": "t1"}}}}

        pages = await _collect(gateway.pages("issueIds", ["issues"], variables, page_size=2))

        assert pages == [
            ([{"id": "a"}], "c1", True),
            ([{"id": "b"}], "c2", True),
            ([{"id": "c"}], "c3", False),
        ]
        assert [call.args for call in datasource.execute_operation.await_args_list] == [
            ("issueIds", {"filter": {"team": {"id": {"eq": "t1"}}}, "first": 2}),
            ("issueIds", {"filter": {"team": {"id": {"eq": "t1"}}}, "first": 2, "after": "c1"}),
            ("issueIds", {"filter": {"team": {"id": {"eq": "t1"}}}, "first": 2, "after": "c2"}),
        ]
        # The caller's variables are left as they were.
        assert variables == {"filter": {"team": {"id": {"eq": "t1"}}}}

    @pytest.mark.asyncio
    async def test_default_page_size_and_start_cursor(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(return_value=_ok({"documents": _page([])}))

        pages = await _collect(gateway.pages("documentIds", ["documents"], after="resume-here"))

        assert pages == [([], None, False)]
        datasource.execute_operation.assert_awaited_once_with(
            "documentIds", {"first": 50, "after": "resume-here"})

    @pytest.mark.asyncio
    async def test_nested_path(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(
            return_value=_ok({"issue": {"comments": _page([{"id": "c1"}])}}))

        pages = await _collect(gateway.pages("issueComments", ["issue", "comments"], {"id": "i1"}))

        assert pages == [([{"id": "c1"}], None, False)]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data", [
        {},
        {"issue": None},
        {"issue": {"comments": None}},
        {"issue": {"comments": {"nodes": None, "pageInfo": None}}},
    ])
    async def test_absent_connection_is_one_empty_page(self, data) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(return_value=_ok(data))

        pages = await _collect(gateway.pages("issueComments", ["issue", "comments"], {"id": "i1"}))

        assert pages == [([], None, False)]
        datasource.execute_operation.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("end_cursor", [None, "", "start"])
    async def test_more_pages_without_a_new_cursor_is_an_incomplete_listing(self, end_cursor) -> None:
        """Taken for the end, it would let the reconciler remove what the rest of the list holds."""
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(
            return_value=_ok({"issues": _page([{"id": "a"}], True, end_cursor)}))

        with pytest.raises(LinearPaginationError):
            await _collect(gateway.pages("issueIds", ["issues"], after="start"))

        datasource.execute_operation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_callable_is_used_instead_of_the_operation_name(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock()
        datasource.issues = AsyncMock(side_effect=[
            _ok({"issues": _page([{"id": "a"}], True, "c1")}),
            _ok({"issues": _page([{"id": "b"}])}),
        ])

        pages = await _collect(gateway.pages(
            "issues", ["issues"], page_size=25, after="start",
            fetch=lambda ds, first, after: ds.issues(first=first, after=after, filter={"x": 1}),
        ))

        assert [nodes for nodes, _, _ in pages] == [[{"id": "a"}], [{"id": "b"}]]
        assert [call.kwargs for call in datasource.issues.await_args_list] == [
            {"first": 25, "after": "start", "filter": {"x": 1}},
            {"first": 25, "after": "c1", "filter": {"x": 1}},
        ]
        datasource.execute_operation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_page_raises_it_is_not_the_end_of_the_list(self) -> None:
        gateway, datasource, _, sleep = _make_gateway()
        datasource.execute_operation = AsyncMock(side_effect=[
            _ok({"issues": _page([{"id": "a"}], True, "c1")}),
            _failed(message="Bad cursor", status=400),
        ])

        seen = []
        with pytest.raises(LinearApiError, match="Bad cursor"):
            async for nodes, cursor, has_next in gateway.pages("issueIds", ["issues"]):
                seen.append((nodes, cursor, has_next))

        # The first page was delivered and said there was more.
        assert seen == [([{"id": "a"}], "c1", True)]
        sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_first_page_raises_before_anything_is_yielded(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(return_value=_failed(status=401))

        with pytest.raises(LinearAuthError):
            await _collect(gateway.pages("issueIds", ["issues"]))

    @pytest.mark.asyncio
    async def test_a_page_is_retried_in_place(self) -> None:
        gateway, datasource, _, sleep = _make_gateway()
        datasource.execute_operation = AsyncMock(side_effect=[
            _ok({"issues": _page([{"id": "a"}], True, "c1")}),
            _failed(status=502),
            _failed(status=429),
            _ok({"issues": _page([{"id": "b"}])}),
        ])

        pages = await _collect(gateway.pages("issueIds", ["issues"]))

        assert [nodes for nodes, _, _ in pages] == [[{"id": "a"}], [{"id": "b"}]]
        assert _waits(sleep) == [1, 5]
        # The retries asked for the same page.
        assert [call.args[1].get("after") for call in datasource.execute_operation.await_args_list] == [
            None, "c1", "c1", "c1",
        ]

    @pytest.mark.asyncio
    async def test_transient_failure_that_outlives_the_retries_raises(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(side_effect=[
            _ok({"issues": _page([{"id": "a"}], True, "c1")}),
            *[_failed(status=503)] * 4,
        ])

        with pytest.raises(LinearTransientError):
            await _collect(gateway.pages("issueIds", ["issues"]))

        assert datasource.execute_operation.await_count == 5


class TestAllNodes:
    @pytest.mark.asyncio
    async def test_collects_every_page(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(side_effect=[
            _ok({"project": {"issues": _page([{"id": "a"}, {"id": "b"}], True, "c1")}}),
            _ok({"project": {"issues": _page([{"id": "c"}])}}),
        ])

        nodes = await gateway.all_nodes(
            "projectIssues", ["project", "issues"], {"id": "p1"}, page_size=2, after="c0")

        assert nodes == [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        assert [call.args[1] for call in datasource.execute_operation.await_args_list] == [
            {"id": "p1", "first": 2, "after": "c0"},
            {"id": "p1", "first": 2, "after": "c1"},
        ]

    @pytest.mark.asyncio
    async def test_partial_list_is_never_returned(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(side_effect=[
            _ok({"project": {"issues": _page([{"id": "a"}], True, "c1")}}),
            _failed(status=401),
        ])

        with pytest.raises(LinearAuthError):
            await gateway.all_nodes("projectIssues", ["project", "issues"], {"id": "p1"})


# ---------------------------------------------------------------------------
# LinearGateway.complete
# ---------------------------------------------------------------------------


class TestComplete:
    @pytest.mark.asyncio
    async def test_fetches_the_pages_the_first_response_left_out(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(side_effect=[
            _ok({"issue": {"comments": _page([{"id": "c2"}], True, "cursor-2")}}),
            _ok({"issue": {"comments": _page([{"id": "c3"}])}}),
        ])
        issue = {"id": "i1", "comments": _page([{"id": "c1"}], True, "cursor-1")}

        nodes = await gateway.complete(issue, "comments", "issueComments", ["issue", "comments"])

        assert nodes == [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]
        assert [call.args for call in datasource.execute_operation.await_args_list] == [
            ("issueComments", {"id": "i1", "first": 100, "after": "cursor-1"}),
            ("issueComments", {"id": "i1", "first": 100, "after": "cursor-2"}),
        ]
        # The parent now holds the whole list and says so.
        assert issue["comments"] == {
            "nodes": [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }

    @pytest.mark.asyncio
    async def test_page_size_is_passed_on(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(
            return_value=_ok({"project": {"projectUpdates": _page([])}}))
        project = {"id": "p1", "projectUpdates": _page([{"id": "u1"}], True, "cursor-1")}

        await gateway.complete(
            project, "projectUpdates", "projectUpdates", ["project", "projectUpdates"], page_size=50)

        datasource.execute_operation.assert_awaited_once_with(
            "projectUpdates", {"id": "p1", "first": 50, "after": "cursor-1"})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("connection", [
        _page([{"id": "c1"}]),
        _page([{"id": "c1"}], False, "cursor-1"),
        {"nodes": [{"id": "c1"}]},
    ])
    async def test_complete_list_needs_no_call(self, connection) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock()
        issue = {"id": "i1", "comments": connection}

        nodes = await gateway.complete(issue, "comments", "issueComments", ["issue", "comments"])

        assert nodes == [{"id": "c1"}]
        datasource.execute_operation.assert_not_awaited()
        assert issue["comments"]["pageInfo"] == {"hasNextPage": False, "endCursor": None}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("issue", [
        {"id": "i1", "comments": _page([{"id": "c1"}], True, None)},
        {"comments": _page([{"id": "c1"}], True, "cursor-1")},
    ])
    async def test_more_pages_without_a_way_to_read_them_is_an_error(self, issue) -> None:
        """Reporting such a list as complete would silently cut it short."""
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock()

        with pytest.raises(LinearApiError):
            await gateway.complete(issue, "comments", "issueComments", ["issue", "comments"])

        datasource.execute_operation.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("parent", [{"id": "i1"}, {"id": "i1", "comments": None}])
    async def test_missing_field_becomes_an_empty_list(self, parent) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock()

        assert await gateway.complete(parent, "comments", "issueComments", ["issue", "comments"]) == []
        assert parent["comments"]["nodes"] == []
        datasource.execute_operation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_follow_up_page_raises_and_leaves_the_parent_alone(self) -> None:
        gateway, datasource, _, _ = _make_gateway()
        datasource.execute_operation = AsyncMock(return_value=_failed(status=500))
        original = _page([{"id": "c1"}], True, "cursor-1")
        issue = {"id": "i1", "comments": original}

        with pytest.raises(LinearTransientError):
            await gateway.complete(issue, "comments", "issueComments", ["issue", "comments"])

        # Not rewritten as "complete": nobody can take the first page for the whole list.
        assert issue["comments"] is original
        assert issue["comments"]["pageInfo"]["hasNextPage"] is True

    @pytest.mark.asyncio
    async def test_first_page_nodes_are_copied_not_shared(self) -> None:
        gateway, _, _, _ = _make_gateway()
        first_page = [{"id": "c1"}]
        issue = {"id": "i1", "comments": _page(first_page)}

        nodes = await gateway.complete(issue, "comments", "issueComments", ["issue", "comments"])
        nodes.append({"id": "extra"})

        assert first_page == [{"id": "c1"}]
