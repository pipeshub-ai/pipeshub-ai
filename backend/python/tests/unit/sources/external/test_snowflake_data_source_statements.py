import json
from typing import Never
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.snowflake.snowflake_ import SnowflakeDataSource


class FakeHTTP:
    def __init__(self, status: int = 200, body: dict | None = None) -> None:
        self.status = status
        self.body = body if body is not None else {}
        self.headers = {"Authorization": "Bearer x"}
        self.requests = []

    def get_base_url(self) -> str:
        return "https://acct.snowflakecomputing.com/api/v2"

    async def execute(self, request) -> object:
        self.requests.append(request)
        response = MagicMock()
        response.status = self.status
        response.text.return_value = json.dumps(self.body)
        response.json.return_value = self.body
        return response


def data_source(http: FakeHTTP) -> SnowflakeDataSource:
    client = MagicMock()
    client.get_client.return_value = http
    return SnowflakeDataSource(client)


async def test_a_failed_status_poll_carries_status_sqlstate_and_message() -> None:
    http = FakeHTTP(422, {"message": "SQL compilation error", "sqlState": "42000"})
    response = await data_source(http).get_statement_status("h1", partition=2)
    assert response.success is False
    assert (response.status_code, response.sql_state, response.error) == (422, "42000", "SQL compilation error")
    assert http.requests[0].url.endswith("/statements/h1?partition=2")


async def test_a_running_statement_poll_keeps_its_status() -> None:
    response = await data_source(FakeHTTP(202, {"statementHandle": "h1"})).get_statement_status("h1")
    assert response.success is True
    assert response.status_code == 202


async def test_a_resubmitted_statement_carries_its_request_id() -> None:
    http = FakeHTTP(200, {"data": []})
    await data_source(http).execute_sql("SELECT 1", request_id="r-1", retry=True)
    assert http.requests[0].url.endswith("/statements?requestId=r-1&retry=true")


async def test_a_first_submission_has_no_retry_flag() -> None:
    http = FakeHTTP(200, {"data": []})
    await data_source(http).execute_sql("SELECT 1", request_id="r-1")
    assert http.requests[0].url.endswith("/statements?requestId=r-1")


async def test_presigned_url_quotes_the_stage_and_escapes_the_path() -> None:
    http = FakeHTTP(200, {"data": []})
    await data_source(http).generate_presigned_url("My DB", "s", "st", "dir/it's.csv")
    statement = http.requests[0].body["statement"]
    assert statement == (
        "SELECT GET_PRESIGNED_URL(@\"My DB\".\"s\".\"st\", 'dir/it''s.csv', 3600) AS presigned_url"
    )


async def test_stage_listing_quotes_the_stage_and_escapes_the_pattern() -> None:
    http = FakeHTTP(200, {"data": []})
    await data_source(http).list_stage_files("DB", "S", "st", pattern="%o'k%")
    statement = http.requests[0].body["statement"]
    assert statement == "SELECT * FROM DIRECTORY(@\"DB\".\"S\".\"st\") WHERE RELATIVE_PATH LIKE '%o''k%'"


class HTMLHTTP(FakeHTTP):
    async def execute(self, request) -> object:
        self.requests.append(request)
        response = MagicMock()
        response.status = 404
        response.text.return_value = "<html>Not found</html>"
        response.json.side_effect = ValueError("not JSON")
        return response


async def test_an_html_404_keeps_its_status() -> None:
    # An unknown account resolves (wildcard DNS) and answers with a page, not JSON.
    response = await data_source(HTMLHTTP()).execute_sql("SELECT 1")
    assert (response.success, response.status_code, response.data) == (False, 404, None)


async def test_an_html_404_is_not_retried() -> None:
    from app.sources.external.snowflake.statements import (
        SnowflakeFetchError,
        SnowflakeStatementRunner,
    )

    http = HTMLHTTP()
    runner = SnowflakeStatementRunner(data_source(http), warehouse="WH", sleep=AsyncMock())
    with pytest.raises(SnowflakeFetchError) as error:
        await runner.run("SELECT 1")
    assert error.value.status_code == 404
    assert len(http.requests) == 1


async def test_a_rejected_token_keeps_snowflakes_code_and_message() -> None:
    from app.sources.external.snowflake.statements import SnowflakeFetchError

    body = {"code": "390144", "message": "JWT token is invalid. [abc-123]"}
    response = await data_source(FakeHTTP(401, body)).execute_sql("SELECT 1")
    assert (response.status_code, response.code, response.error) == (401, "390144", body["message"])
    error = SnowflakeFetchError.from_response(response, "Snowflake refused the statement")
    assert error.code == "390144" and "JWT token is invalid" in str(error)


async def test_no_answer_names_the_exception() -> None:
    import httpx

    class TimeoutHTTP(FakeHTTP):
        async def execute(self, request) -> Never:
            raise httpx.ConnectTimeout("")

    response = await data_source(TimeoutHTTP()).execute_sql("SELECT 1")
    assert response.status_code is None and response.exception_type == "ConnectTimeout"
    assert response.error == "ConnectTimeout"


class HTML200HTTP(FakeHTTP):
    async def execute(self, request) -> object:
        self.requests.append(request)
        response = MagicMock()
        response.status = 200
        response.text.return_value = "<html>Sign in to the proxy</html>"
        response.json.side_effect = ValueError("not JSON")
        return response


async def test_a_success_page_that_is_not_json_is_a_failure_not_empty_rows() -> None:
    import pytest

    from app.sources.external.snowflake.statements import (
        SnowflakeFetchError,
        SnowflakeStatementRunner,
    )

    response = await data_source(HTML200HTTP()).execute_sql("SELECT 1")
    assert (response.success, response.status_code) == (False, 200)
    runner = SnowflakeStatementRunner(data_source(HTML200HTTP()), warehouse="WH", sleep=AsyncMock())
    with pytest.raises(SnowflakeFetchError):
        await runner.run("SELECT 1")


async def test_an_empty_success_body_is_still_a_success() -> None:
    class EmptyHTTP(FakeHTTP):
        async def execute(self, request) -> object:
            response = MagicMock()
            response.status = 200
            response.text.return_value = ""
            return response

    response = await data_source(EmptyHTTP()).execute_sql("SELECT 1")
    assert response.success and response.data is None


@pytest.mark.parametrize("body", [{}, {"data": []}], ids=["empty", "rows-without-metadata"])
async def test_a_finished_reply_without_result_metadata_is_an_error_not_no_rows(body) -> None:
    # Every finished statement carries resultSetMetaData, an empty result too:
    # without it, an empty listing would make a schema look emptied.
    from app.sources.external.snowflake.statements import (
        SnowflakeFetchError,
        SnowflakeStatementRunner,
    )

    runner = SnowflakeStatementRunner(data_source(FakeHTTP(200, body)), warehouse="WH", sleep=AsyncMock())
    with pytest.raises(SnowflakeFetchError, match="without its result metadata"):
        await runner.run("SELECT 1")
