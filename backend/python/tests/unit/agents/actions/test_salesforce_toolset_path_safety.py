"""The Salesforce toolset builds REST paths from the agent's arguments: an object name or a
record id must not steer a request to another endpoint.

These run the real SalesforceDataSource and HTTP client against a fake transport, so the
URL asserted is the one that would go to Salesforce.
"""

import json

import httpx
import pytest

from app.agents.actions.salesforce.salesforce import Salesforce
from app.sources.client.salesforce.salesforce import SalesforceConfig, SalesforceClient
from app.sources.external.salesforce.salesforce_data_source import SalesforceDataSource

VALID_ID = "001000000000001AAA"


def _toolset(sent: list) -> Salesforce:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.method, request.url.path))
        return httpx.Response(200, json={"id": VALID_ID, "success": True, "records": [], "done": True})

    client = SalesforceClient.build_with_config(SalesforceConfig(
        instance_url="https://acme.my.salesforce.com", access_token="token", api_version="59.0",
    ))
    rest = client.get_client()
    rest.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=rest.headers)
    sf = Salesforce.__new__(Salesforce)
    sf.client = SalesforceDataSource(client)
    sf.api_version = "59.0"
    sf.instance_url = "https://acme.my.salesforce.com"
    sf.chat_state = {}
    return sf


@pytest.mark.asyncio
@pytest.mark.parametrize("sobject", ["../composite", "../../../apexrest/X", "Account/External_Id__c", "Account?x=1"])
async def test_create_record_rejects_an_object_name_that_is_a_path(sobject):
    sent: list = []
    ok, body = await _toolset(sent).create_record(sobject=sobject, data={"Name": "x"})
    assert ok is False
    assert sent == []
    assert "Invalid Salesforce API name" in json.loads(body)["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("record_id", ["x/../../../composite/sobjects", "001000000000001AAA/..", "{x}", "001"])
async def test_update_record_rejects_a_record_id_that_is_a_path(record_id):
    sent: list = []
    ok, _ = await _toolset(sent).update_record(sobject="Account", record_id=record_id, data={"Name": "x"})
    assert ok is False
    assert sent == []


@pytest.mark.asyncio
async def test_valid_names_and_ids_still_reach_their_endpoint():
    sent: list = []
    sf = _toolset(sent)
    ok, _ = await sf.update_record(sobject="Account", record_id=VALID_ID, data={"Name": "x"})
    assert ok is True
    assert sent == [("PATCH", f"/services/data/v59.0/sobjects/Account/{VALID_ID}")]


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [
    lambda sf: sf.get_record(sobject="../limits", record_id=VALID_ID),
    lambda sf: sf.get_record(sobject="Account", record_id="../../limits"),
    lambda sf: sf.describe_object(sobject="../../limits"),
    lambda sf: sf.get_record_chatter(record_id="../../limits"),
    lambda sf: sf.post_chatter_comment(feed_element_id="../../limits", text="hi"),
])
async def test_every_path_argument_is_checked(call):
    sent: list = []
    ok, _ = await call(_toolset(sent))
    assert ok is False
    assert sent == []


def test_search_limit_is_clamped():
    assert Salesforce._search_limit(500) == 50
    assert Salesforce._search_limit("10 OFFSET 2000") == 10
    assert Salesforce._search_limit(0) == 1
    assert Salesforce._search_limit(7) == 7


def test_like_wildcards_match_literally():
    assert Salesforce._like_value("100%") == "100\\%"
    assert Salesforce._like_value("a_c") == "a\\_c"
    assert Salesforce._like_value("O'Brien\\") == "O\\'Brien\\\\"


@pytest.mark.asyncio
async def test_upload_whose_document_id_cannot_be_read_is_not_reported_as_attached():
    """The file was created but could not be linked to the record the user asked for."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agents.actions.salesforce.attachments import SalesforceAttachmentUploader
    from app.sources.client.salesforce.salesforce import SalesforceResponse

    client = SimpleNamespace(
        sobject_create=AsyncMock(return_value=SalesforceResponse(success=True, data={"id": "068000000000001AAA"})),
        soql_query=AsyncMock(return_value=SalesforceResponse(success=False, error="HTTP 503")),
    )
    attachment = SimpleNamespace(filename="quote.pdf", content=b"%PDF", record_id="r1")
    result = await SalesforceAttachmentUploader(client)._upload_one(
        attachment, link_to_id="006000000000001AAA", share_type="V", visibility=None,
    )
    assert result.success is False
    assert "not linked" in (result.error or "")
