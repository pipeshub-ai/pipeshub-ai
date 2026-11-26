"""SR-8: a lost acknowledgement from each write client reaches the durable kernel as an unknown effect.

Each client runs, against its loopback fake, inside a non-idempotent write tool on the production durable port and
executor. The fake loses the answer to the write; the run must park for a person (``ix.unknown.t1.0``) after one
execution, with one effect at the fake and nothing sent again.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from google.oauth2.credentials import Credentials  # type: ignore
from googleapiclient.discovery import build  # type: ignore
from msgraph.generated.users.item.send_mail.send_mail_post_request_body import (  # type: ignore
    SendMailPostRequestBody,
)

from app.sources.client.github.github import GitHubClientViaToken
from app.sources.client.google.google import GoogleClient
from app.sources.client.mariadb.mariadb import MariaDBClient
from app.sources.client.microsoft.microsoft import MSGraphClientWithDelegatedAuth
from app.sources.client.redshift.redshift import RedshiftClient
from app.sources.client.slack.slack import SlackClient, SlackRESTClientViaToken
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource
from app.sources.external.slack.slack import SlackDataSource
from harness_core.tools.base import ToolOutput
from tests.unit.sources.client.saferetry_fakes import (
    DROP,
    UNKNOWN,
    FakeSQL,
    as_tool_output,
    http_fake,
    mariadb_handler,
    real_mariadb,
    redshift_handler,
    run_durable_write,
    serve,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

Write = tuple["Callable[[], Awaitable[ToolOutput]]", "Callable[[], int]"]


@contextmanager
def slack() -> Iterator[Write]:
    with http_fake(ok=True, channel="C1", ts="1.0") as fake:
        fake.plan = [DROP]
        rest = SlackRESTClientViaToken("xoxb-saferetry")
        rest.get_web_client().base_url = f"http://{fake.address}/api/"
        source = SlackDataSource(SlackClient(rest))

        async def post() -> ToolOutput:
            response = await source.chat_post_message(channel="C1", text="hi")  # pyright: ignore[reportUnknownMemberType]
            return ToolOutput(success=response.success, error=response.error)

        yield post, lambda: len(fake.writes())


@contextmanager
def gmail() -> Iterator[Write]:
    with http_fake(id="m1") as fake:
        fake.plan = [DROP]
        service = build(
            "gmail",
            "v1",
            credentials=Credentials(token="qa-token"),
            client_options={"api_endpoint": f"http://{fake.address}/"},
            static_discovery=True,
        )
        source = GoogleGmailDataSource(GoogleClient(service).get_client())  # type: ignore[arg-type]
        yield (lambda: as_tool_output(source.users_messages_send(userId="me", body={"raw": "aGk="}))), lambda: len(
            fake.writes()
        )


@contextmanager
def outlook() -> Iterator[Write]:
    with http_fake(id="m1") as fake:
        fake.plan = [DROP]
        client = MSGraphClientWithDelegatedAuth("h.e.s", "tenant", logging.getLogger("tests.saferetry")).client
        client.request_adapter.base_url = client.path_parameters["baseurl"] = f"http://{fake.address}/v1.0"
        send = client.users.by_user_id("u1").send_mail
        yield (lambda: as_tool_output(send.post(SendMailPostRequestBody()))), lambda: len(fake.writes())


@contextmanager
def github() -> Iterator[Write]:
    with http_fake(id=1, number=1) as fake:
        fake.plan = [DROP]
        client = GitHubClientViaToken("qa-token", base_url=f"http://{fake.address}")
        client.create_client()
        repo = client.get_sdk().get_repo("o/r", lazy=True)
        yield (lambda: as_tool_output(asyncio.to_thread(repo.create_issue, title="t"))), lambda: len(fake.writes())


@contextmanager
def mariadb() -> Iterator[Write]:
    fake = FakeSQL(drop_on="INSERT")
    with serve(mariadb_handler(fake), http=False) as address:
        client = MariaDBClient(host="127.0.0.1", user="qa", password="qa", port=int(address.rsplit(":", 1)[1]))

        async def insert() -> ToolOutput:
            try:
                return await as_tool_output(client.execute_query("INSERT INTO notes VALUES (1)"))
            finally:
                await client.close()

        yield insert, lambda: len(fake.received)


@contextmanager
def redshift() -> Iterator[Write]:
    fake = FakeSQL(drop_on="INSERT")
    with serve(redshift_handler(fake), http=False) as address:
        port = int(address.rsplit(":", 1)[1])
        client = RedshiftClient(host="127.0.0.1", database="qa", user="qa", password="qa", port=port, ssl=False)
        yield (
            lambda: as_tool_output(asyncio.to_thread(client.execute_query, "INSERT INTO notes VALUES (1)"))
        ), lambda: len(fake.received)
        client.close()


@pytest.mark.usefixtures(real_mariadb.__name__)
@pytest.mark.parametrize("client", [slack, gmail, outlook, github, mariadb, redshift])
async def test_sr_8_a_lost_acknowledgement_parks_for_a_person_with_one_effect(
    client: Callable[[], contextmanager[Write]],  # type: ignore[type-arg]
) -> None:
    with client() as (write, effects):
        status, executions = await run_durable_write(write)
        assert (status, executions, effects()) == (f"suspended:{UNKNOWN}", 1, 1)
