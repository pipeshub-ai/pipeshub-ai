"""
Unit tests for app.agents.actions.salesforce.salesforce

Tests all Salesforce toolset methods. The SalesforceDataSource (self.client)
is mocked so no real HTTP calls are made.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.sources.client.salesforce.salesforce import SalesforceResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_sf_client():
    """Return a mock SalesforceClient with get_base_url."""
    client = MagicMock()
    client.get_base_url.return_value = "https://mycompany.my.salesforce.com"
    return client


def _success_response(data=None):
    return SalesforceResponse(success=True, data=data or {})


def _error_response(error="Something went wrong"):
    return SalesforceResponse(success=False, error=error)


def _build_salesforce(mock_client=None):
    """Instantiate the Salesforce class bypassing the ToolsetBuilder decorator."""
    from app.agents.actions.salesforce.salesforce import Salesforce

    client = mock_client or _mock_sf_client()
    # Patch SalesforceDataSource so it doesn't try to call client.get_client()
    with patch(
        "app.agents.actions.salesforce.salesforce.SalesforceDataSource"
    ) as MockDS:
        mock_ds = AsyncMock()
        MockDS.return_value = mock_ds
        sf = Salesforce.__new__(Salesforce)
        sf.client = mock_ds
        sf.api_version = "59.0"
        sf.instance_url = "https://mycompany.my.salesforce.com"
    return sf


def _build_salesforce_via_init():
    """Instantiate Salesforce through real __init__ to cover lines 559-561."""
    from app.agents.actions.salesforce.salesforce import Salesforce

    client = _mock_sf_client()
    with patch(
        "app.agents.actions.salesforce.salesforce.SalesforceDataSource"
    ) as MockDS:
        mock_ds = AsyncMock()
        MockDS.return_value = mock_ds
        sf = Salesforce(client)
    return sf


# ============================================================================
# Constructor
# ============================================================================


class TestInit:
    def test_init_sets_attributes(self):
        sf = _build_salesforce_via_init()
        assert sf.api_version == "59.0"
        assert sf.instance_url == "https://mycompany.my.salesforce.com"

    def test_init_strips_trailing_slash(self):
        client = MagicMock()
        client.get_base_url.return_value = "https://mycompany.my.salesforce.com/"
        from app.agents.actions.salesforce.salesforce import Salesforce
        with patch("app.agents.actions.salesforce.salesforce.SalesforceDataSource"):
            sf = Salesforce(client)
        assert sf.instance_url == "https://mycompany.my.salesforce.com"

    def test_init_handles_none_base_url(self):
        client = MagicMock()
        client.get_base_url.return_value = None
        from app.agents.actions.salesforce.salesforce import Salesforce
        with patch("app.agents.actions.salesforce.salesforce.SalesforceDataSource"):
            sf = Salesforce(client)
        assert sf.instance_url == ""


# ============================================================================
# Helper method tests
# ============================================================================


class TestBuildWebUrl:
    def test_returns_url_when_record_id_present(self):
        sf = _build_salesforce()
        url = sf._build_web_url("001ABC")
        assert url == "https://mycompany.my.salesforce.com/001ABC"

    def test_returns_none_when_no_record_id(self):
        sf = _build_salesforce()
        assert sf._build_web_url(None) is None

    def test_returns_none_when_no_instance_url(self):
        sf = _build_salesforce()
        sf.instance_url = ""
        assert sf._build_web_url("001ABC") is None


class TestEnrichWithWebUrls:
    def test_enriches_soql_records(self):
        sf = _build_salesforce()
        data = {
            "totalSize": 1,
            "records": [
                {"Id": "001ABC", "attributes": {"type": "Account"}, "Name": "Acme"}
            ],
        }
        result = sf._enrich_with_web_urls(data)
        assert result["records"][0]["webUrl"] == "https://mycompany.my.salesforce.com/001ABC"

    def test_enriches_sosl_search_records(self):
        sf = _build_salesforce()
        data = {
            "searchRecords": [
                {"Id": "003XYZ", "attributes": {"type": "Contact"}, "Name": "John"}
            ]
        }
        result = sf._enrich_with_web_urls(data)
        assert result["searchRecords"][0]["webUrl"] == "https://mycompany.my.salesforce.com/003XYZ"

    def test_enriches_single_record(self):
        sf = _build_salesforce()
        data = {"Id": "001ABC", "attributes": {"type": "Account"}, "Name": "Acme"}
        result = sf._enrich_with_web_urls(data)
        assert result["webUrl"] == "https://mycompany.my.salesforce.com/001ABC"

    def test_enriches_create_response(self):
        sf = _build_salesforce()
        data = {"id": "001NEW", "success": True}
        result = sf._enrich_with_web_urls(data)
        assert result["webUrl"] == "https://mycompany.my.salesforce.com/001NEW"

    def test_non_dict_passthrough(self):
        sf = _build_salesforce()
        assert sf._enrich_with_web_urls("not a dict") == "not a dict"

    def test_non_dict_record_in_records_list_raises(self):
        sf = _build_salesforce()
        data = {"totalSize": 1, "records": ["not a dict"]}
        with pytest.raises(ValidationError):
            sf._enrich_with_web_urls(data)

    def test_record_without_id_no_weburl(self):
        sf = _build_salesforce()
        sf.instance_url = ""
        data = {"totalSize": 1, "records": [{"Name": "Acme", "attributes": {"type": "Account"}}]}
        result = sf._enrich_with_web_urls(data)
        assert "webUrl" not in result["records"][0]

    def test_create_response_gets_web_url_from_id(self):
        """Create payloads only expose lowercase id; enrichment uses SalesforceRecord.record_id."""
        sf = _build_salesforce()
        data = {"id": "001NEW", "success": True}
        result = sf._enrich_with_web_urls(data)
        assert result["webUrl"] == "https://mycompany.my.salesforce.com/001NEW"

    def test_dict_without_any_matching_pattern(self):
        sf = _build_salesforce()
        data = {"foo": "bar"}
        result = sf._enrich_with_web_urls(data)
        assert result == {"foo": "bar"}


class TestHandleResponse:
    def test_success_returns_true_and_json(self):
        sf = _build_salesforce()
        resp = _success_response({"totalSize": 0, "records": []})
        ok, body = sf._handle_response(resp, "Query OK")
        assert ok is True
        parsed = json.loads(body)
        assert parsed["message"] == "Query OK"

    def test_error_returns_false_and_json(self):
        sf = _build_salesforce()
        resp = _error_response("Bad request")
        ok, body = sf._handle_response(resp, "Query OK")
        assert ok is False
        parsed = json.loads(body)
        assert parsed["error"] == "Bad request"

    def test_error_with_none_error_field(self):
        sf = _build_salesforce()
        resp = SalesforceResponse(success=False, error=None)
        ok, body = sf._handle_response(resp, "msg")
        assert ok is False
        assert "Unknown error" in json.loads(body)["error"]


class TestBuildSoqlConditions:
    def test_empty_conditions(self):
        sf = _build_salesforce()
        assert sf._build_soql_conditions([]) == ""

    def test_single_condition(self):
        sf = _build_salesforce()
        result = sf._build_soql_conditions(["Name = 'Acme'"])
        assert result == " WHERE Name = 'Acme'"

    def test_multiple_conditions(self):
        sf = _build_salesforce()
        result = sf._build_soql_conditions(["A = '1'", "B = '2'"])
        assert result == " WHERE A = '1' AND B = '2'"


# ============================================================================
# Markdown to Chatter segments
# ============================================================================


class TestMarkdownToChatterSegments:
    def test_plain_text(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("Hello world")
        assert len(segs) == 1
        assert segs[0].type == "Text"
        assert segs[0].text == "Hello world"

    def test_empty_text(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("")
        assert len(segs) == 1
        assert segs[0].text == ""

    def test_bold_text(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("**bold**")
        types = [s.type for s in segs]
        assert "MarkupBegin" in types
        assert "MarkupEnd" in types
        # The bold text should be in a Text segment
        text_segs = [s for s in segs if s.type == "Text"]
        assert any(s.text == "bold" for s in text_segs)

    def test_italic_text(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("*italic*")
        markup_begins = [s for s in segs if s.type == "MarkupBegin"]
        assert any(s.markupType.value == "Italic" for s in markup_begins)

    def test_hyperlink(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("[Google](https://google.com)")
        hyperlinks = [s for s in segs if s.type == "Hyperlink"]
        assert len(hyperlinks) == 1
        assert hyperlinks[0].url == "https://google.com"
        assert hyperlinks[0].text == "Google"

    def test_heading(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("# My Heading")
        # Heading becomes bold
        markup_begins = [s for s in segs if s.type == "MarkupBegin"]
        assert any(s.markupType.value == "Bold" for s in markup_begins)

    def test_unordered_list(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("- item1\n- item2")
        markup_types = [
            s.markupType.value for s in segs if hasattr(s, "markupType")
        ]
        assert "UnorderedList" in markup_types
        assert "ListItem" in markup_types

    def test_ordered_list(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("1. first\n2. second")
        markup_types = [
            s.markupType.value for s in segs if hasattr(s, "markupType")
        ]
        assert "OrderedList" in markup_types
        assert "ListItem" in markup_types

    def test_paragraph_break_on_blank_line(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("line1\n\nline2")
        para_begins = [
            s for s in segs
            if s.type == "MarkupBegin" and s.markupType.value == "Paragraph"
        ]
        assert len(para_begins) >= 1

    def test_horizontal_rule(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("text above\n---\ntext below")
        # HR after first block emits paragraph break
        para_begins = [
            s for s in segs
            if s.type == "MarkupBegin" and s.markupType.value == "Paragraph"
        ]
        assert len(para_begins) >= 1

    def test_horizontal_rule_as_first_line(self):
        sf = _build_salesforce()
        # HR at start (first_block=True) should NOT emit paragraph break
        segs = sf._markdown_to_chatter_segments("---\ntext")
        text_segs = [s for s in segs if s.type == "Text"]
        assert any("text" in s.text for s in text_segs)

    def test_blank_line_at_start_no_paragraph_break(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("\ntext")
        # No paragraph break since it's the first block
        para_begins = [
            s for s in segs
            if s.type == "MarkupBegin" and s.markupType.value == "Paragraph"
        ]
        assert len(para_begins) == 0

    def test_heading_not_first_block(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("intro\n# Heading")
        para_begins = [
            s for s in segs
            if s.type == "MarkupBegin" and s.markupType.value == "Paragraph"
        ]
        assert len(para_begins) >= 1

    def test_unordered_list_not_first_block(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("intro\n- item1\n- item2")
        para_begins = [
            s for s in segs
            if s.type == "MarkupBegin" and s.markupType.value == "Paragraph"
        ]
        assert len(para_begins) >= 1

    def test_ordered_list_not_first_block(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("intro\n1. first\n2. second")
        para_begins = [
            s for s in segs
            if s.type == "MarkupBegin" and s.markupType.value == "Paragraph"
        ]
        assert len(para_begins) >= 1

    def test_list_followed_by_non_list(self):
        sf = _build_salesforce()
        # Unordered list then non-matching line triggers the break in while loop
        segs = sf._markdown_to_chatter_segments("- item\nnot a list item")
        text_segs = [s for s in segs if s.type == "Text"]
        assert any("not a list item" in s.text for s in text_segs)

    def test_ordered_list_followed_by_non_list(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("1. item\nnot ordered")
        text_segs = [s for s in segs if s.type == "Text"]
        assert any("not ordered" in s.text for s in text_segs)


# ============================================================================
# SOQL / SOSL tools
# ============================================================================


class TestSoqlQuery:
    @pytest.mark.asyncio
    async def test_success(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(
            return_value=_success_response({"totalSize": 1, "records": [{"Id": "001"}]})
        )
        ok, body = await sf.soql_query(query="SELECT Id FROM Account LIMIT 1")
        assert ok is True
        sf.client.soql_query.assert_awaited_once_with(api_version="59.0", q="SELECT Id FROM Account LIMIT 1")

    @pytest.mark.asyncio
    async def test_error_response(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_error_response("INVALID_QUERY"))
        ok, body = await sf.soql_query(query="BAD")
        assert ok is False
        assert "INVALID_QUERY" in body

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(side_effect=RuntimeError("timeout"))
        ok, body = await sf.soql_query(query="SELECT Id FROM Account")
        assert ok is False
        assert "timeout" in body


class TestSoslSearch:
    @pytest.mark.asyncio
    async def test_success(self):
        sf = _build_salesforce()
        sf.client.sosl_search = AsyncMock(
            return_value=_success_response({"searchRecords": []})
        )
        ok, body = await sf.sosl_search(search="FIND {Acme}")
        assert ok is True
        sf.client.sosl_search.assert_awaited_once_with(
            api_version="59.0", q="FIND {Acme}"
        )

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sosl_search = AsyncMock(side_effect=ValueError("bad"))
        ok, body = await sf.sosl_search(search="FIND {x}")
        assert ok is False


# ============================================================================
# Generic Record CRUD
# ============================================================================


class TestGetRecord:
    @pytest.mark.asyncio
    async def test_success(self):
        sf = _build_salesforce()
        sf.client.sobject_get = AsyncMock(
            return_value=_success_response({"Id": "001ABC", "Name": "Acme", "attributes": {"type": "Account"}})
        )
        ok, body = await sf.get_record(sobject="Account", record_id="001ABC")
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["Id"] == "001ABC"

    @pytest.mark.asyncio
    async def test_with_fields(self):
        sf = _build_salesforce()
        sf.client.sobject_get = AsyncMock(return_value=_success_response({"Id": "001ABC"}))
        await sf.get_record(sobject="Account", record_id="001ABC", fields="Id,Name")
        sf.client.sobject_get.assert_awaited_once_with(
            api_version="59.0", sobject="Account", record_id="001ABC", fields="Id,Name"
        )

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_get = AsyncMock(side_effect=Exception("oops"))
        ok, _ = await sf.get_record(sobject="Account", record_id="001")
        assert ok is False


class TestCreateRecord:
    @pytest.mark.asyncio
    async def test_success(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_success_response({"id": "001NEW", "success": True})
        )
        ok, body = await sf.create_record(sobject="Account", data={"Name": "New"})
        assert ok is True
        sf.client.sobject_create.assert_awaited_once_with(
            api_version="59.0", sobject="Account", data={"Name": "New"}
        )

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.create_record(sobject="Custom__c", data={})
        assert ok is False


class TestUpdateRecord:
    @pytest.mark.asyncio
    async def test_success(self):
        sf = _build_salesforce()
        sf.client.sobject_update = AsyncMock(return_value=_success_response({}))
        ok, _ = await sf.update_record(sobject="Account", record_id="001", data={"Name": "Updated"})
        assert ok is True
        sf.client.sobject_update.assert_awaited_once_with(
            api_version="59.0", sobject="Account", record_id="001", data={"Name": "Updated"}
        )

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_update = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.update_record(sobject="Account", record_id="001", data={})
        assert ok is False


# ============================================================================
# Describe Object
# ============================================================================


class TestDescribeObject:
    @pytest.mark.asyncio
    async def test_success_trims_fields(self):
        sf = _build_salesforce()
        describe_data = {
            "name": "Account",
            "label": "Account",
            "labelPlural": "Accounts",
            "keyPrefix": "001",
            "createable": True,
            "updateable": True,
            "deletable": True,
            "queryable": True,
            "searchable": True,
            "fields": [
                {
                    "name": "Name",
                    "label": "Account Name",
                    "type": "string",
                    "nillable": False,
                    "createable": True,
                    "updateable": True,
                    "picklistValues": None,
                }
            ],
        }
        sf.client.s_object_describe = AsyncMock(
            return_value=_success_response(describe_data)
        )
        ok, body = await sf.describe_object(sobject="Account")
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["name"] == "Account"
        assert len(parsed["data"]["fields"]) == 1
        assert parsed["data"]["fields"][0]["name"] == "Name"
        sf.client.s_object_describe.assert_awaited_once_with(
            sobject_api_name="Account", version="59.0"
        )

    @pytest.mark.asyncio
    async def test_success_with_picklist(self):
        sf = _build_salesforce()
        describe_data = {
            "name": "Case",
            "label": "Case",
            "labelPlural": "Cases",
            "keyPrefix": "500",
            "createable": True,
            "updateable": True,
            "deletable": True,
            "queryable": True,
            "searchable": True,
            "fields": [
                {
                    "name": "Status",
                    "label": "Status",
                    "type": "picklist",
                    "nillable": False,
                    "createable": True,
                    "updateable": True,
                    "picklistValues": [
                        {"value": "New", "label": "New", "active": True},
                        {"value": "Old", "label": "Old", "active": False},
                    ],
                }
            ],
        }
        sf.client.s_object_describe = AsyncMock(
            return_value=_success_response(describe_data)
        )
        ok, body = await sf.describe_object(sobject="Case")
        parsed = json.loads(body)
        # Only active picklist values
        pv = parsed["data"]["fields"][0]["picklistValues"]
        assert len(pv) == 1
        assert pv[0]["value"] == "New"

    @pytest.mark.asyncio
    async def test_fallback_when_data_not_dict(self):
        sf = _build_salesforce()
        sf.client.s_object_describe = AsyncMock(
            return_value=_success_response(["not", "a", "dict"])
        )
        ok, body = await sf.describe_object(sobject="Account")
        assert ok is False
        assert "error" in json.loads(body)

    @pytest.mark.asyncio
    async def test_error_response(self):
        sf = _build_salesforce()
        sf.client.s_object_describe = AsyncMock(
            return_value=_error_response("NOT_FOUND")
        )
        ok, body = await sf.describe_object(sobject="FakeObject")
        assert ok is False
        assert "NOT_FOUND" in body

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.s_object_describe = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.describe_object(sobject="Account")
        assert ok is False


# ============================================================================
# List Recent Records
# ============================================================================


class TestListRecentRecords:
    @pytest.mark.asyncio
    async def test_success(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(
            return_value=_success_response({"totalSize": 2, "records": [{"Id": "001"}, {"Id": "002"}]})
        )
        ok, body = await sf.list_recent_records(sobject="Account", limit=5)
        assert ok is True
        # Verify the query includes ORDER BY and LIMIT
        call_args = sf.client.soql_query.call_args
        assert "ORDER BY LastModifiedDate DESC" in call_args.kwargs["q"]
        assert "LIMIT 5" in call_args.kwargs["q"]

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.list_recent_records(sobject="Account")
        assert ok is False


# ============================================================================
# Search Tools
# ============================================================================


class TestSearchAccounts:
    @pytest.mark.asyncio
    async def test_no_filters(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        ok, _ = await sf.search_accounts()
        assert ok is True
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "WHERE" not in q

    @pytest.mark.asyncio
    async def test_with_name_and_industry(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.search_accounts(name="Acme", industry="Technology", limit=5)
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "Name LIKE '%Acme%'" in q
        assert "Industry = 'Technology'" in q
        assert "LIMIT 5" in q

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.search_accounts(name="X")
        assert ok is False


class TestSearchContacts:
    @pytest.mark.asyncio
    async def test_with_filters(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.search_contacts(name="John", email="john@acme.com", account_id="001ABC")
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "Name LIKE '%John%'" in q
        assert "Email LIKE '%john@acme.com%'" in q
        assert "AccountId = '001ABC'" in q

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(side_effect=Exception("err"))
        ok, _ = await sf.search_contacts()
        assert ok is False


class TestSearchLeads:
    @pytest.mark.asyncio
    async def test_with_filters(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.search_leads(name="Jane", company="TechCorp", status="Open - Not Contacted")
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "Name LIKE '%Jane%'" in q
        assert "Company LIKE '%TechCorp%'" in q
        assert "Status = 'Open - Not Contacted'" in q

    @pytest.mark.asyncio
    async def test_partial_filters(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.search_leads(name="Jane")
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "Name LIKE '%Jane%'" in q
        assert "Company LIKE" not in q
        assert "Status =" not in q

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.search_leads()
        assert ok is False


class TestSearchOpportunities:
    @pytest.mark.asyncio
    async def test_with_filters(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.search_opportunities(name="Deal", stage="Qualification", account_id="001")
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "Name LIKE '%Deal%'" in q
        assert "StageName = 'Qualification'" in q
        assert "AccountId = '001'" in q

    @pytest.mark.asyncio
    async def test_partial_filters(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.search_opportunities(stage="Closed Won")
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "StageName = 'Closed Won'" in q
        assert "Name LIKE" not in q
        assert "AccountId" not in q

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.search_opportunities()
        assert ok is False


class TestSearchCases:
    @pytest.mark.asyncio
    async def test_with_filters(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.search_cases(subject="Login", status="New", priority="High", account_id="001")
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "Subject LIKE '%Login%'" in q
        assert "Status = 'New'" in q
        assert "Priority = 'High'" in q
        assert "AccountId = '001'" in q

    @pytest.mark.asyncio
    async def test_partial_filters(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.search_cases(status="Escalated")
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "Status = 'Escalated'" in q
        assert "Subject LIKE" not in q
        assert "Priority =" not in q
        assert "AccountId =" not in q

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.search_cases()
        assert ok is False


class TestSearchProducts:
    @pytest.mark.asyncio
    async def test_active_only_default(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.search_products()
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "IsActive = true" in q

    @pytest.mark.asyncio
    async def test_all_filters(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.search_products(name="Pro", product_code="SKU", family="Software", active_only=False)
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "Name LIKE '%Pro%'" in q
        assert "ProductCode LIKE '%SKU%'" in q
        assert "Family = 'Software'" in q
        assert "IsActive = true" not in q

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.search_products()
        assert ok is False


class TestSearchTasks:
    @pytest.mark.asyncio
    async def test_with_all_filters(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.search_tasks(
            subject="Follow up",
            status="Not Started",
            priority="High",
            owner_id="005OWNER",
            what_id="006OPP",
            who_id="003CONTACT",
            limit=5,
        )
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "Subject LIKE '%Follow up%'" in q
        assert "Status = 'Not Started'" in q
        assert "Priority = 'High'" in q
        assert "OwnerId = '005OWNER'" in q
        assert "WhatId = '006OPP'" in q
        assert "WhoId = '003CONTACT'" in q
        assert "LIMIT 5" in q

    @pytest.mark.asyncio
    async def test_partial_filters(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.search_tasks(subject="Call")
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "Subject LIKE '%Call%'" in q
        assert "OwnerId =" not in q
        assert "WhatId =" not in q
        assert "WhoId =" not in q

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.search_tasks()
        assert ok is False


class TestListPricebooks:
    @pytest.mark.asyncio
    async def test_active_only(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.list_pricebooks()
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "IsActive = true" in q

    @pytest.mark.asyncio
    async def test_with_name_filter(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.list_pricebooks(name="Standard", active_only=False, limit=5)
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "Name LIKE '%Standard%'" in q
        assert "IsActive = true" not in q
        assert "LIMIT 5" in q

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.list_pricebooks()
        assert ok is False


# ============================================================================
# Create Tools
# ============================================================================


class TestCreateAccount:
    @pytest.mark.asyncio
    async def test_minimal(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_success_response({"id": "001NEW", "success": True})
        )
        ok, _ = await sf.create_account(name="Acme")
        assert ok is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data == {"Name": "Acme"}

    @pytest.mark.asyncio
    async def test_all_fields(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "001", "success": True}))
        await sf.create_account(
            name="Acme",
            industry="Tech",
            phone="555-1234",
            website="https://acme.com",
            description="A company",
            billing_city="SF",
            billing_state="CA",
            billing_country="US",
        )
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["Name"] == "Acme"
        assert data["Industry"] == "Tech"
        assert data["Phone"] == "555-1234"
        assert data["Website"] == "https://acme.com"
        assert data["Description"] == "A company"
        assert data["BillingCity"] == "SF"
        assert data["BillingState"] == "CA"
        assert data["BillingCountry"] == "US"

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.create_account(name="X")
        assert ok is False


class TestCreateContact:
    @pytest.mark.asyncio
    async def test_minimal(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "003NEW", "success": True}))
        ok, _ = await sf.create_contact(last_name="Doe")
        assert ok is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data == {"LastName": "Doe"}

    @pytest.mark.asyncio
    async def test_all_fields(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "003", "success": True}))
        await sf.create_contact(
            last_name="Doe", first_name="John", email="j@x.com",
            phone="555", title="CTO", account_id="001", department="Eng",
        )
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["LastName"] == "Doe"
        assert data["FirstName"] == "John"
        assert data["Email"] == "j@x.com"
        assert data["AccountId"] == "001"
        assert data["Department"] == "Eng"

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.create_contact(last_name="X")
        assert ok is False


class TestCreateLead:
    @pytest.mark.asyncio
    async def test_minimal(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "00Q", "success": True}))
        ok, _ = await sf.create_lead(last_name="Doe", company="Acme")
        assert ok is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data == {"LastName": "Doe", "Company": "Acme"}

    @pytest.mark.asyncio
    async def test_all_fields(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "00Q", "success": True}))
        await sf.create_lead(
            last_name="Doe", company="Acme", first_name="Jane",
            email="j@a.com", phone="555", title="VP", status="Open", industry="Tech",
        )
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["FirstName"] == "Jane"
        assert data["Status"] == "Open"
        assert data["Industry"] == "Tech"

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.create_lead(last_name="X", company="Y")
        assert ok is False


class TestCreateOpportunity:
    @pytest.mark.asyncio
    async def test_minimal(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "006", "success": True}))
        ok, _ = await sf.create_opportunity(name="Deal", stage_name="Prospecting", close_date="2025-12-31")
        assert ok is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data == {"Name": "Deal", "StageName": "Prospecting", "CloseDate": "2025-12-31"}

    @pytest.mark.asyncio
    async def test_with_amount_and_probability(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "006", "success": True}))
        await sf.create_opportunity(
            name="Big Deal", stage_name="Closed Won", close_date="2025-06-30",
            account_id="001", amount=50000.0, description="Desc", probability=90.0,
        )
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["Amount"] == 50000.0
        assert data["Probability"] == 90.0
        assert data["AccountId"] == "001"

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.create_opportunity(name="X", stage_name="Y", close_date="2025-01-01")
        assert ok is False


class TestCreateCase:
    @pytest.mark.asyncio
    async def test_minimal(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "500", "success": True}))
        ok, _ = await sf.create_case(subject="Login issue")
        assert ok is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data == {"Subject": "Login issue"}

    @pytest.mark.asyncio
    async def test_all_fields(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "500", "success": True}))
        await sf.create_case(
            subject="Bug", status="New", priority="High", origin="Web",
            description="Desc", account_id="001", contact_id="003",
        )
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["Status"] == "New"
        assert data["Priority"] == "High"
        assert data["Origin"] == "Web"
        assert data["AccountId"] == "001"
        assert data["ContactId"] == "003"

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.create_case(subject="X")
        assert ok is False


class TestCreateProduct:
    @pytest.mark.asyncio
    async def test_minimal(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "01t", "success": True}))
        ok, _ = await sf.create_product(name="Widget")
        assert ok is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data == {"Name": "Widget", "IsActive": True}

    @pytest.mark.asyncio
    async def test_all_fields(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "01t", "success": True}))
        await sf.create_product(
            name="Widget", product_code="W-001", description="A widget",
            family="Hardware", is_active=False, quantity_unit_of_measure="Each",
        )
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["ProductCode"] == "W-001"
        assert data["Family"] == "Hardware"
        assert data["IsActive"] is False
        assert data["QuantityUnitOfMeasure"] == "Each"

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.create_product(name="X")
        assert ok is False


class TestCreateTask:
    @pytest.mark.asyncio
    async def test_minimal(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "00T", "success": True}))
        ok, _ = await sf.create_task(subject="Call John")
        assert ok is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data == {"Subject": "Call John"}

    @pytest.mark.asyncio
    async def test_all_fields(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "00T", "success": True}))
        await sf.create_task(
            subject="Follow up", status="Not Started", priority="High",
            activity_date="2025-06-15", description="Call back",
            owner_id="005OWNER", what_id="006OPP", who_id="003CONTACT",
        )
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["Subject"] == "Follow up"
        assert data["Status"] == "Not Started"
        assert data["ActivityDate"] == "2025-06-15"
        assert data["OwnerId"] == "005OWNER"
        assert data["WhatId"] == "006OPP"
        assert data["WhoId"] == "003CONTACT"

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.create_task(subject="X")
        assert ok is False


# ============================================================================
# Add Product to Opportunity
# ============================================================================


class TestAddProductToOpportunity:
    @pytest.mark.asyncio
    async def test_with_pricebook_entry_id(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "00k", "success": True}))
        ok, _ = await sf.add_product_to_opportunity(
            opportunity_id="006OPP",
            pricebook_entry_id="01uPBE",
            quantity=3,
            unit_price=100.0,
        )
        assert ok is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["OpportunityId"] == "006OPP"
        assert data["PricebookEntryId"] == "01uPBE"
        assert data["Quantity"] == 3
        assert data["UnitPrice"] == 100.0

    @pytest.mark.asyncio
    async def test_missing_both_ids(self):
        sf = _build_salesforce()
        ok, body = await sf.add_product_to_opportunity(opportunity_id="006OPP")
        assert ok is False
        err = json.loads(body)["error"]
        assert "pricebook_entry_id" in err and "product_id" in err

    @pytest.mark.asyncio
    async def test_lookup_pbe_from_product_id(self):
        sf = _build_salesforce()
        # First query: get opportunity's Pricebook2Id
        opp_response = _success_response({"records": [{"Pricebook2Id": "01sPB"}]})
        # Second query: lookup PricebookEntry
        pbe_response = _success_response({"records": [{"Id": "01uPBE", "UnitPrice": 50.0}]})
        # Third call: create the line item
        create_response = _success_response({"id": "00k", "success": True})

        sf.client.soql_query = AsyncMock(side_effect=[opp_response, pbe_response])
        sf.client.sobject_create = AsyncMock(return_value=create_response)

        ok, _ = await sf.add_product_to_opportunity(
            opportunity_id="006OPP", product_id="01tPROD", quantity=2,
        )
        assert ok is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["PricebookEntryId"] == "01uPBE"
        assert data["UnitPrice"] == 50.0
        assert data["Quantity"] == 2

    @pytest.mark.asyncio
    async def test_no_pricebook_on_opportunity(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(
            return_value=_success_response({"records": [{}]})
        )
        ok, body = await sf.add_product_to_opportunity(
            opportunity_id="006", product_id="01t",
        )
        assert ok is False
        assert "Pricebook2Id" in json.loads(body)["error"]

    @pytest.mark.asyncio
    async def test_no_pbe_found(self):
        sf = _build_salesforce()
        opp_resp = _success_response({"records": [{"Pricebook2Id": "01s"}]})
        pbe_resp = _success_response({"records": []})
        sf.client.soql_query = AsyncMock(side_effect=[opp_resp, pbe_resp])
        ok, body = await sf.add_product_to_opportunity(
            opportunity_id="006", product_id="01t",
        )
        assert ok is False
        assert "PricebookEntry" in json.loads(body)["error"]

    @pytest.mark.asyncio
    async def test_with_explicit_pricebook_id(self):
        sf = _build_salesforce()
        pbe_resp = _success_response({"records": [{"Id": "01uPBE", "UnitPrice": 25.0}]})
        create_resp = _success_response({"id": "00k", "success": True})
        sf.client.soql_query = AsyncMock(return_value=pbe_resp)
        sf.client.sobject_create = AsyncMock(return_value=create_resp)

        ok, _ = await sf.add_product_to_opportunity(
            opportunity_id="006", product_id="01t", pricebook_id="01sPB",
        )
        assert ok is True
        # Should only have called soql_query once (PBE lookup, not opp lookup)
        assert sf.client.soql_query.await_count == 1

    @pytest.mark.asyncio
    async def test_with_description(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({"id": "00k", "success": True}))
        ok, _ = await sf.add_product_to_opportunity(
            opportunity_id="006",
            pricebook_entry_id="01u",
            quantity=1,
            unit_price=50.0,
            description="Line item description",
        )
        assert ok is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["Description"] == "Line item description"

    @pytest.mark.asyncio
    async def test_unit_price_overrides_pbe_price(self):
        """When user provides unit_price, it should be used even when PBE has UnitPrice."""
        sf = _build_salesforce()
        pbe_resp = _success_response({"records": [{"Id": "01uPBE", "UnitPrice": 25.0}]})
        create_resp = _success_response({"id": "00k", "success": True})
        sf.client.soql_query = AsyncMock(return_value=pbe_resp)
        sf.client.sobject_create = AsyncMock(return_value=create_resp)

        ok, _ = await sf.add_product_to_opportunity(
            opportunity_id="006", product_id="01t", pricebook_id="01s",
            unit_price=99.99,
        )
        assert ok is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["UnitPrice"] == 99.99  # User's price, not PBE's 25.0

    @pytest.mark.asyncio
    async def test_opp_query_exception_fallback(self):
        """When parsing opp response raises, effective_pricebook_id stays None."""
        sf = _build_salesforce()
        # Return a response whose .data access will cause the try block to fail
        bad_resp = MagicMock()
        bad_resp.data = None  # (None or {}).get("records", []) => AttributeError avoided, but records=[]
        sf.client.soql_query = AsyncMock(return_value=bad_resp)

        ok, body = await sf.add_product_to_opportunity(
            opportunity_id="006", product_id="01t",
        )
        assert ok is False
        assert "Pricebook2Id" in json.loads(body)["error"]

    @pytest.mark.asyncio
    async def test_pbe_query_exception_fallback(self):
        """When parsing PBE response raises, pbe_records defaults to []."""
        sf = _build_salesforce()
        opp_resp = _success_response({"records": [{"Pricebook2Id": "01s"}]})
        # PBE response whose .data triggers the except branch
        bad_pbe = MagicMock()
        type(bad_pbe).data = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        sf.client.soql_query = AsyncMock(side_effect=[opp_resp, bad_pbe])

        ok, body = await sf.add_product_to_opportunity(
            opportunity_id="006", product_id="01t",
        )
        assert ok is False
        assert "PricebookEntry" in json.loads(body)["error"]

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(side_effect=Exception("fail"))
        ok, _ = await sf.add_product_to_opportunity(
            opportunity_id="006", pricebook_entry_id="01u",
        )
        assert ok is False


# ============================================================================
# Chatter Tools
# ============================================================================


class TestGetRecordChatter:
    @pytest.mark.asyncio
    async def test_success(self):
        sf = _build_salesforce()
        sf.client.record_feed_elements = AsyncMock(
            return_value=_success_response({"elements": []})
        )
        ok, _ = await sf.get_record_chatter(record_id="001ABC")
        assert ok is True
        sf.client.record_feed_elements.assert_awaited_once_with(
            record_group_id="001ABC", version=sf.api_version
        )

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.record_feed_elements = AsyncMock(side_effect=Exception("err"))
        ok, _ = await sf.get_record_chatter(record_id="001")
        assert ok is False


class TestPostChatterComment:
    @pytest.mark.asyncio
    async def test_plain_text(self):
        sf = _build_salesforce()
        sf.client.feed_elements_capability_comments_items = AsyncMock(
            return_value=_success_response({"id": "0D7"})
        )
        ok, _ = await sf.post_chatter_comment(
            feed_element_id="0D5ABC", text="Nice work!"
        )
        assert ok is True
        call_kwargs = sf.client.feed_elements_capability_comments_items.call_args.kwargs
        assert call_kwargs["text"] == "Nice work!"
        assert call_kwargs["message_segments"] is None

    @pytest.mark.asyncio
    async def test_rich_text(self):
        sf = _build_salesforce()
        sf.client.feed_elements_capability_comments_items = AsyncMock(
            return_value=_success_response({"id": "0D7"})
        )
        ok, _ = await sf.post_chatter_comment(
            feed_element_id="0D5ABC", text="**bold** text", is_rich_text=True
        )
        assert ok is True
        call_kwargs = sf.client.feed_elements_capability_comments_items.call_args.kwargs
        assert call_kwargs["message_segments"] is not None
        assert len(call_kwargs["message_segments"]) > 0

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.feed_elements_capability_comments_items = AsyncMock(
            side_effect=Exception("err")
        )
        ok, _ = await sf.post_chatter_comment(feed_element_id="0D5", text="x")
        assert ok is False


class TestPostChatterToRecord:
    @pytest.mark.asyncio
    async def test_plain_text(self):
        sf = _build_salesforce()
        sf.client.feed_elements_post_and_search = AsyncMock(
            return_value=_success_response({"id": "0D5NEW"})
        )
        ok, _ = await sf.post_chatter_to_record(
            record_id="001ABC", text="Status update"
        )
        assert ok is True
        call_kwargs = sf.client.feed_elements_post_and_search.call_args.kwargs
        assert call_kwargs["subjectid"] == "001ABC"
        assert call_kwargs["feedelementtype"] == "FeedItem"
        assert call_kwargs["message_segments"] is None

    @pytest.mark.asyncio
    async def test_rich_text(self):
        sf = _build_salesforce()
        sf.client.feed_elements_post_and_search = AsyncMock(
            return_value=_success_response({"id": "0D5NEW"})
        )
        ok, _ = await sf.post_chatter_to_record(
            record_id="001ABC", text="*italic* and [link](https://x.com)", is_rich_text=True
        )
        assert ok is True
        segs = sf.client.feed_elements_post_and_search.call_args.kwargs["message_segments"]
        assert segs is not None

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.feed_elements_post_and_search = AsyncMock(side_effect=Exception("err"))
        ok, _ = await sf.post_chatter_to_record(record_id="001", text="x")
        assert ok is False


# ============================================================================
# User Info
# ============================================================================


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_success(self):
        sf = _build_salesforce()
        sf.client.get_user_info = AsyncMock(
            return_value=_success_response({"user_id": "005ABC", "name": "Test User"})
        )
        ok, body = await sf.get_current_user()
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["user_id"] == "005ABC"

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.get_user_info = AsyncMock(side_effect=Exception("auth error"))
        ok, body = await sf.get_current_user()
        assert ok is False
        assert "auth error" in body
