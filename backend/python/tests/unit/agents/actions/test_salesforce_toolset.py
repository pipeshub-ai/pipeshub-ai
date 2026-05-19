"""
Unit tests for app.agents.actions.salesforce.salesforce

Tests all Salesforce toolset methods. The SalesforceDataSource (self.client)
is mocked so no real HTTP calls are made.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


def _valid_registry_entry(
    *,
    storage_type: str = "external",
    download_url: str = "https://cm.local/d/x",
    filename: str = "a.pdf",
    mime_type: str = "application/pdf",
    size_bytes: int = 1,
) -> dict[str, object]:
    """Build a complete ``StagedDocumentEntry``-shaped dict for registry seeds.

    The salesforce consumer now coerces each registry entry through
    ``StagedDocumentEntry.model_validate`` before fetching, so seeds with
    missing required fields are rejected as ``corrupt_registry_entry``
    instead of reaching the fetch path. Tests that exercise the fetch
    branches must therefore supply a complete entry shape.
    """
    return {
        "storage_type": storage_type,
        "download_url": download_url,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
    }


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


class TestHandleResponse:
    def test_success_returns_true_and_json(self):
        sf = _build_salesforce()
        resp = _success_response({"totalSize": 0, "records": []})
        ok, body = sf._handle_response(resp, "Query OK")
        assert ok is True
        parsed = json.loads(body)
        assert parsed["message"] == "Query OK"
        assert parsed["success"] is True

    def test_error_returns_false_and_json(self):
        sf = _build_salesforce()
        resp = _error_response("Bad request")
        ok, body = sf._handle_response(resp, "Query OK")
        assert ok is False
        parsed = json.loads(body)
        assert parsed["error"] == "Bad request"
        assert parsed["success"] is False

    def test_error_with_none_error_field(self):
        sf = _build_salesforce()
        resp = SalesforceResponse(success=False, error=None)
        ok, body = sf._handle_response(resp, "msg")
        assert ok is False
        parsed = json.loads(body)
        assert "Unknown error" in parsed["error"]
        assert parsed["success"] is False


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


class TestGetRecentRecordDisplayField:
    def test_case_returns_case_number(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        assert Salesforce._get_recent_record_display_field("Case") == "CaseNumber"

    def test_task_returns_subject(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        assert Salesforce._get_recent_record_display_field("Task") == "Subject"

    def test_email_message_returns_subject(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        assert Salesforce._get_recent_record_display_field("EmailMessage") == "Subject"

    def test_event_returns_subject(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        assert Salesforce._get_recent_record_display_field("Event") == "Subject"

    def test_order_returns_order_number(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        assert Salesforce._get_recent_record_display_field("Order") == "OrderNumber"

    def test_contract_returns_contract_number(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        assert Salesforce._get_recent_record_display_field("Contract") == "ContractNumber"

    def test_content_document_returns_title(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        assert Salesforce._get_recent_record_display_field("ContentDocument") == "Title"

    def test_note_returns_title(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        assert Salesforce._get_recent_record_display_field("Note") == "Title"

    def test_unknown_sobject_returns_name(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        assert Salesforce._get_recent_record_display_field("Account") == "Name"
        assert Salesforce._get_recent_record_display_field("Opportunity") == "Name"
        assert Salesforce._get_recent_record_display_field("CustomObject__c") == "Name"


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
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "ORDER BY LastModifiedDate DESC" in q
        assert "LIMIT 5" in q
        # Generic sobjects use "Name" as the display field
        assert "Name" in q

    @pytest.mark.asyncio
    async def test_case_uses_case_number_field(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(
            return_value=_success_response({"totalSize": 1, "records": [{"Id": "500ABC"}]})
        )
        ok, _ = await sf.list_recent_records(sobject="Case", limit=10)
        assert ok is True
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "CaseNumber" in q
        assert "Name" not in q.replace("LastModifiedDate", "")

    @pytest.mark.asyncio
    async def test_task_uses_subject_field(self):
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(
            return_value=_success_response({"totalSize": 1, "records": [{"Id": "00TABC"}]})
        )
        ok, _ = await sf.list_recent_records(sobject="Task", limit=10)
        assert ok is True
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "Subject" in q
        assert "Name" not in q.replace("LastModifiedDate", "")

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
    async def test_no_filter_by_default(self):
        """Default active_only=False means no IsActive filter in the query."""
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.list_pricebooks()
        q = sf.client.soql_query.call_args.kwargs["q"]
        assert "IsActive = true" not in q

    @pytest.mark.asyncio
    async def test_active_only_explicit(self):
        """When active_only=True is explicitly passed, the IsActive filter is applied."""
        sf = _build_salesforce()
        sf.client.soql_query = AsyncMock(return_value=_success_response({"totalSize": 0, "records": []}))
        await sf.list_pricebooks(active_only=True)
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
# Create Pricebook Entry
# ============================================================================


class TestCreatePricebookEntry:
    @pytest.mark.asyncio
    async def test_minimal(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_success_response({"id": "01uPBE", "success": True})
        )
        ok, body = await sf.create_pricebook_entry(
            product_id="01tPROD",
            pricebook_id="01sPB",
            unit_price=99.0,
        )
        assert ok is True
        parsed = json.loads(body)
        assert parsed["success"] is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["Product2Id"] == "01tPROD"
        assert data["Pricebook2Id"] == "01sPB"
        assert data["UnitPrice"] == 99.0
        assert data["IsActive"] is True

    @pytest.mark.asyncio
    async def test_inactive_entry(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_success_response({"id": "01uPBE", "success": True})
        )
        ok, _ = await sf.create_pricebook_entry(
            product_id="01tPROD",
            pricebook_id="01sPB",
            unit_price=50.0,
            is_active=False,
        )
        assert ok is True
        data = sf.client.sobject_create.call_args.kwargs["data"]
        assert data["IsActive"] is False

    @pytest.mark.asyncio
    async def test_correct_sobject_is_used(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_success_response({"id": "01uPBE", "success": True})
        )
        await sf.create_pricebook_entry(
            product_id="01tPROD", pricebook_id="01sPB", unit_price=10.0
        )
        assert sf.client.sobject_create.call_args.kwargs["sobject"] == "PricebookEntry"

    @pytest.mark.asyncio
    async def test_error_response(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_error_response("DUPLICATE_VALUE")
        )
        ok, body = await sf.create_pricebook_entry(
            product_id="01tPROD", pricebook_id="01sPB", unit_price=10.0
        )
        assert ok is False
        parsed = json.loads(body)
        assert parsed["success"] is False
        assert "DUPLICATE_VALUE" in parsed["error"]

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(side_effect=Exception("network error"))
        ok, body = await sf.create_pricebook_entry(
            product_id="01tPROD", pricebook_id="01sPB", unit_price=10.0
        )
        assert ok is False
        assert "network error" in body


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


# ============================================================================
# File transfer tools: ``upload_file_to_salesforce`` + ``attach_file_to_record``
# ============================================================================
#
# These tests cover the cross-toolset "stage in blob, upload into SF" flow
# added in this branch. Mocking layer is deliberately split:
#
# - ``TestUploadFileToSalesforce`` mocks ``_upload_one_staged_document_to_salesforce``
#   to isolate the orchestration contract: registry validation, asyncio.gather
#   with ``return_exceptions=True``, the ``Semaphore(_SF_UPLOAD_CONCURRENCY)``
#   cap, and the shared ``aiohttp.ClientSession`` injection (regression for the
#   connection-pooling change).
#
# - ``TestUploadOneStagedDocument`` mocks ``fetch_staged_document_bytes`` and
#   the SF client to cover the per-doc helper's branches (size cap, blob
#   staging errors, CV create failure).
#
# - ``TestAttachFileToRecord`` covers the standalone link-creation tool.


def _build_salesforce_with_state(state=None):
    """Build a Salesforce instance with a usable ``state`` attribute."""
    sf = _build_salesforce()
    sf.state = state
    return sf


def _ok_doc_row(doc_id: str, **extra) -> dict:
    """Build a successful per-doc result row matching the helper's shape."""
    base = {
        "document_id": doc_id,
        "ok": True,
        "content_version_id": f"068xx{doc_id}",
        "content_document_id": f"069xx{doc_id}",
        "filename": "f.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 10,
        "weburl_content_document_id": (
            f"https://mycompany.my.salesforce.com/lightning/r/069xx{doc_id}/view"
        ),
    }
    base.update(extra)
    return base


def _err_doc_row(doc_id: str, error: str, **extra) -> dict:
    base = {"document_id": doc_id, "ok": False, "error": error}
    base.update(extra)
    return base


class TestInitWithState:
    def test_state_kwarg_set_on_instance(self):
        """The new ``state`` ctor kwarg lands on ``self.state``."""
        from app.agents.actions.salesforce.salesforce import Salesforce
        client = _mock_sf_client()
        state = {"org_id": "org-1"}
        with patch(
            "app.agents.actions.salesforce.salesforce.SalesforceDataSource"
        ):
            sf = Salesforce(client, state=state)
        assert sf.state is state

    def test_state_defaults_to_none(self):
        """No ``state`` arg keeps backward compatibility (positional client)."""
        from app.agents.actions.salesforce.salesforce import Salesforce
        client = _mock_sf_client()
        with patch(
            "app.agents.actions.salesforce.salesforce.SalesforceDataSource"
        ):
            sf = Salesforce(client)
        assert sf.state is None

    def test_state_via_kwargs_bag(self):
        """``state`` can also arrive via ``**kwargs`` (factory-style construction)."""
        from app.agents.actions.salesforce.salesforce import Salesforce
        client = _mock_sf_client()
        state = {"org_id": "org-2"}
        with patch(
            "app.agents.actions.salesforce.salesforce.SalesforceDataSource"
        ):
            sf = Salesforce(client, **{"state": state})
        assert sf.state is state


class TestNormalizePathOnClient:
    def test_extension_preserved_when_present(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        assert Salesforce._normalize_path_on_client(
            "Invoice.pdf", "application/pdf",
        ) == "Invoice.pdf"

    def test_missing_extension_derived_from_mime(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        # ``mimetypes.guess_extension`` returns ``.pdf`` for application/pdf,
        # so the filename gains the right extension instead of becoming
        # FileType=UNKNOWN in Salesforce.
        result = Salesforce._normalize_path_on_client(
            "Invoice", "application/pdf",
        )
        assert result.startswith("Invoice.")
        assert result.endswith(".pdf")

    def test_missing_extension_unknown_mime_falls_back_to_bin(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        assert Salesforce._normalize_path_on_client(
            "blob", "application/x-not-a-real-type",
        ) == "blob.bin"

    def test_empty_filename_normalized(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        assert Salesforce._normalize_path_on_client(
            "", "application/octet-stream",
        ) == "attachment.bin"

    def test_mime_with_charset_handled(self):
        from app.agents.actions.salesforce.salesforce import Salesforce
        result = Salesforce._normalize_path_on_client(
            "note", "text/plain; charset=utf-8",
        )
        # Just assert an extension was added; the exact value depends on
        # the local mimetypes DB but it should never be ``.bin`` for
        # ``text/plain``.
        assert result.startswith("note.")
        assert result != "note.bin"


class TestUploadFileToSalesforce:
    @pytest.mark.asyncio
    async def test_state_none_falls_through_to_missing_context(self):
        """``state=None`` is coerced to ``{}`` which lacks org_id; the call
        bails on the missing-context check, not the no-.get check.
        """
        sf = _build_salesforce_with_state(state=None)
        ok, body = await sf.upload_file_to_salesforce(document_ids=["d1"])
        assert ok is False
        assert "org_id and config_service" in body

    @pytest.mark.asyncio
    async def test_non_mapping_state_returns_container_error(self):
        """A state without ``.get`` (e.g. a list) trips the explicit guard."""
        sf = _build_salesforce_with_state(state=[1, 2, 3])
        ok, body = await sf.upload_file_to_salesforce(document_ids=["d1"])
        assert ok is False
        assert "chat state container" in body

    @pytest.mark.asyncio
    async def test_missing_org_id_returns_error(self):
        sf = _build_salesforce_with_state(state={
            "config_service": MagicMock(),
            "document_id_to_url": {"d1": {}},
        })
        ok, body = await sf.upload_file_to_salesforce(document_ids=["d1"])
        assert ok is False
        assert "org_id and config_service" in body

    @pytest.mark.asyncio
    async def test_missing_config_service_returns_error(self):
        sf = _build_salesforce_with_state(state={
            "org_id": "org-1",
            "document_id_to_url": {"d1": {}},
        })
        ok, body = await sf.upload_file_to_salesforce(document_ids=["d1"])
        assert ok is False
        assert "org_id and config_service" in body

    @pytest.mark.asyncio
    async def test_empty_registry_returns_no_staged_documents(self):
        sf = _build_salesforce_with_state(state={
            "org_id": "org-1",
            "config_service": MagicMock(),
            "document_id_to_url": {},
        })
        ok, body = await sf.upload_file_to_salesforce(document_ids=["d1"])
        assert ok is False
        parsed = json.loads(body)
        assert "no_staged_documents" in parsed["error"]
        assert parsed["requested_document_ids"] == ["d1"]
        assert parsed["registered_document_ids"] == []

    @pytest.mark.asyncio
    async def test_single_doc_success(self):
        sf = _build_salesforce_with_state(state={
            "org_id": "org-1",
            "config_service": MagicMock(),
            "document_id_to_url": {"d1": {"storage_type": "s3"}},
        })
        sf._upload_one_staged_document_to_salesforce = AsyncMock(
            return_value=_ok_doc_row("d1"),
        )
        with patch(
            "app.agents.actions.salesforce.salesforce.aiohttp.ClientSession"
        ):
            ok, body = await sf.upload_file_to_salesforce(document_ids=["d1"])
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["succeeded"] == 1
        assert parsed["data"]["failed"] == 0
        assert parsed["data"]["results"][0]["document_id"] == "d1"

    @pytest.mark.asyncio
    async def test_results_preserve_input_order(self):
        """``asyncio.gather`` keeps results in input order; the synth loop must too."""
        sf = _build_salesforce_with_state(state={
            "org_id": "org-1",
            "config_service": MagicMock(),
            "document_id_to_url": {
                "d1": {}, "d2": {}, "d3": {},
            },
        })
        sf._upload_one_staged_document_to_salesforce = AsyncMock(
            side_effect=lambda *, doc_id, **_: _ok_doc_row(doc_id),
        )
        with patch(
            "app.agents.actions.salesforce.salesforce.aiohttp.ClientSession"
        ):
            ok, body = await sf.upload_file_to_salesforce(
                document_ids=["d3", "d1", "d2"],
            )
        assert ok is True
        order = [r["document_id"] for r in json.loads(body)["data"]["results"]]
        assert order == ["d3", "d1", "d2"]

    @pytest.mark.asyncio
    async def test_mixed_success_and_handled_error(self):
        """Per-doc handled errors do not poison sibling successes."""
        sf = _build_salesforce_with_state(state={
            "org_id": "org-1",
            "config_service": MagicMock(),
            "document_id_to_url": {"d1": {}, "d2": {}, "d3": {}},
        })

        async def fake(*, doc_id, **_):
            if doc_id == "d2":
                return _err_doc_row(doc_id, "Blob fetch failed: 404")
            return _ok_doc_row(doc_id)

        sf._upload_one_staged_document_to_salesforce = AsyncMock(side_effect=fake)
        with patch(
            "app.agents.actions.salesforce.salesforce.aiohttp.ClientSession"
        ):
            ok, body = await sf.upload_file_to_salesforce(
                document_ids=["d1", "d2", "d3"],
            )
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["succeeded"] == 2
        assert parsed["data"]["failed"] == 1
        d2 = next(
            r for r in parsed["data"]["results"] if r["document_id"] == "d2"
        )
        assert "Blob fetch failed" in d2["error"]

    @pytest.mark.asyncio
    async def test_unexpected_exception_isolated_to_one_row(self):
        """Regression: ``return_exceptions=True`` prevents one bad task from
        cancelling the rest of the batch, and the synth loop converts the
        raw exception into the standard per-doc error row shape so the
        public JSON contract still holds.
        """
        sf = _build_salesforce_with_state(state={
            "org_id": "org-1",
            "config_service": MagicMock(),
            "document_id_to_url": {"d1": {}, "d2": {}, "d3": {}},
        })

        async def fake(*, doc_id, **_):
            if doc_id == "d2":
                raise RuntimeError("kaboom")
            return _ok_doc_row(doc_id)

        sf._upload_one_staged_document_to_salesforce = AsyncMock(side_effect=fake)
        with patch(
            "app.agents.actions.salesforce.salesforce.aiohttp.ClientSession"
        ):
            ok, body = await sf.upload_file_to_salesforce(
                document_ids=["d1", "d2", "d3"],
            )
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["succeeded"] == 2
        assert parsed["data"]["failed"] == 1
        d2 = next(
            r for r in parsed["data"]["results"] if r["document_id"] == "d2"
        )
        assert d2["ok"] is False
        assert "Unexpected upload failure" in d2["error"]
        assert "kaboom" in d2["error"]

    @pytest.mark.asyncio
    async def test_all_failed_returns_ok_false(self):
        """``ok`` is True iff at least one upload succeeded."""
        sf = _build_salesforce_with_state(state={
            "org_id": "org-1",
            "config_service": MagicMock(),
            "document_id_to_url": {"d1": {}, "d2": {}},
        })
        sf._upload_one_staged_document_to_salesforce = AsyncMock(
            side_effect=lambda *, doc_id, **_: _err_doc_row(doc_id, "nope"),
        )
        with patch(
            "app.agents.actions.salesforce.salesforce.aiohttp.ClientSession"
        ):
            ok, body = await sf.upload_file_to_salesforce(
                document_ids=["d1", "d2"],
            )
        assert ok is False
        assert json.loads(body)["message"] == "All uploads failed"

    @pytest.mark.asyncio
    async def test_semaphore_caps_concurrency_at_configured_limit(self):
        """At most ``_SF_UPLOAD_CONCURRENCY`` helpers run at any one time."""
        import asyncio as _asyncio

        from app.agents.actions.salesforce.salesforce import (
            _SF_UPLOAD_CONCURRENCY,
        )

        sf = _build_salesforce_with_state(state={
            "org_id": "org-1",
            "config_service": MagicMock(),
            "document_id_to_url": {f"d{i}": {} for i in range(10)},
        })

        in_flight = 0
        peak = 0
        lock = _asyncio.Lock()

        async def fake(*, doc_id, **_):
            nonlocal in_flight, peak
            async with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            # Yield long enough for sibling tasks to ramp up before we
            # release the semaphore slot.
            await _asyncio.sleep(0.02)
            async with lock:
                in_flight -= 1
            return _ok_doc_row(doc_id)

        sf._upload_one_staged_document_to_salesforce = AsyncMock(side_effect=fake)
        with patch(
            "app.agents.actions.salesforce.salesforce.aiohttp.ClientSession"
        ):
            await sf.upload_file_to_salesforce(
                document_ids=[f"d{i}" for i in range(10)],
            )

        assert peak <= _SF_UPLOAD_CONCURRENCY, (
            f"semaphore cap violated: peak in-flight = {peak} "
            f"(limit = {_SF_UPLOAD_CONCURRENCY})"
        )
        # Sanity: we should actually be running concurrently, not
        # serially — otherwise the cap test is vacuously true.
        assert peak > 1

    @pytest.mark.asyncio
    async def test_shared_session_passed_to_every_helper_call(self):
        """Regression: every parallel fetch borrows the same ClientSession.

        If a future refactor accidentally passes ``session=None`` (e.g.
        moves the gather outside the ``async with``), each fetch would
        spin up its own session and we'd lose the intra-batch keep-alive
        + pooling we just added.
        """
        sf = _build_salesforce_with_state(state={
            "org_id": "org-1",
            "config_service": MagicMock(),
            "document_id_to_url": {f"d{i}": {} for i in range(4)},
        })
        sf._upload_one_staged_document_to_salesforce = AsyncMock(
            side_effect=lambda *, doc_id, **_: _ok_doc_row(doc_id),
        )

        # ``aiohttp.ClientSession()`` returns an async-context-manager whose
        # __aenter__ yields the session instance — we make that instance
        # identifiable so we can assert every helper call received it.
        sentinel_session = MagicMock(name="shared-session")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=sentinel_session)
        cm.__aexit__ = AsyncMock(return_value=False)
        with patch(
            "app.agents.actions.salesforce.salesforce.aiohttp.ClientSession",
            return_value=cm,
        ):
            await sf.upload_file_to_salesforce(
                document_ids=[f"d{i}" for i in range(4)],
            )

        seen_sessions = {
            call.kwargs.get("session")
            for call in sf._upload_one_staged_document_to_salesforce.await_args_list
        }
        assert seen_sessions == {sentinel_session}, (
            f"helper received mixed/unexpected session ids: {seen_sessions}"
        )

    @pytest.mark.asyncio
    async def test_outer_exception_returns_error(self):
        """An exception raised before the gather (e.g. async with setup)
        is trapped by the outer try/except and returns the error envelope.
        """
        sf = _build_salesforce_with_state(state={
            "org_id": "org-1",
            "config_service": MagicMock(),
            "document_id_to_url": {"d1": {}},
        })
        with patch(
            "app.agents.actions.salesforce.salesforce.aiohttp.ClientSession",
            side_effect=RuntimeError("ctor blew up"),
        ):
            ok, body = await sf.upload_file_to_salesforce(document_ids=["d1"])
        assert ok is False
        assert "ctor blew up" in body


class TestUploadOneStagedDocument:
    """Cover the per-doc helper ``_upload_one_staged_document_to_salesforce``."""

    @pytest.mark.asyncio
    async def test_not_found_in_chat_state(self):
        sf = _build_salesforce_with_state(state={})
        result = await sf._upload_one_staged_document_to_salesforce(
            doc_id="missing",
            registry={"other": {}},
            org_id="org-1",
            config_service=MagicMock(),
        )
        assert result["ok"] is False
        assert "not_found_in_chat_state" in result["error"]
        assert result["registered_document_ids"] == ["other"]

    @pytest.mark.asyncio
    async def test_blob_staging_error_returns_handled_row(self):
        from app.agents.actions.util.blob_staging import BlobStagingError
        sf = _build_salesforce_with_state(state={})
        with patch(
            "app.agents.actions.salesforce.salesforce.fetch_staged_document_bytes",
            new=AsyncMock(side_effect=BlobStagingError("missing endpoint")),
        ):
            result = await sf._upload_one_staged_document_to_salesforce(
                doc_id="d1",
                registry={"d1": _valid_registry_entry(storage_type="external")},
                org_id="org-1",
                config_service=MagicMock(),
            )
        assert result == {
            "document_id": "d1",
            "ok": False,
            "error": "Blob fetch failed: missing endpoint",
        }

    @pytest.mark.asyncio
    async def test_corrupt_entry_rejected_before_fetch(self):
        # New behavior: the helper coerces each registry entry through
        # StagedDocumentEntry.model_validate up-front, so a malformed
        # entry (e.g. missing download_url/filename) is rejected as
        # ``corrupt_registry_entry`` without ever calling the fetcher.
        sf = _build_salesforce_with_state(state={})
        with patch(
            "app.agents.actions.salesforce.salesforce.fetch_staged_document_bytes",
            new=AsyncMock(return_value=b"never-called"),
        ) as mock_fetch:
            result = await sf._upload_one_staged_document_to_salesforce(
                doc_id="d1",
                registry={"d1": {"storage_type": "external"}},
                org_id="org-1",
                config_service=MagicMock(),
            )
        assert result["ok"] is False
        assert "corrupt_registry_entry" in result["error"]
        mock_fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_aiohttp_client_error_returns_handled_row(self):
        import aiohttp as _aiohttp
        sf = _build_salesforce_with_state(state={})
        with patch(
            "app.agents.actions.salesforce.salesforce.fetch_staged_document_bytes",
            new=AsyncMock(side_effect=_aiohttp.ClientError("dns fail")),
        ):
            result = await sf._upload_one_staged_document_to_salesforce(
                doc_id="d1",
                registry={"d1": _valid_registry_entry(storage_type="s3")},
                org_id="org-1",
                config_service=MagicMock(),
            )
        assert result["ok"] is False
        assert "Download failed" in result["error"]

    @pytest.mark.asyncio
    async def test_zero_bytes_refused(self):
        sf = _build_salesforce_with_state(state={})
        with patch(
            "app.agents.actions.salesforce.salesforce.fetch_staged_document_bytes",
            new=AsyncMock(return_value=b""),
        ):
            result = await sf._upload_one_staged_document_to_salesforce(
                doc_id="d1",
                registry={"d1": _valid_registry_entry(storage_type="external")},
                org_id="org-1",
                config_service=MagicMock(),
            )
        assert result["ok"] is False
        assert "zero bytes" in result["error"]

    @pytest.mark.asyncio
    async def test_oversize_payload_refused(self):
        from app.agents.actions.util.blob_staging import DEFAULT_MAX_STAGE_BYTES
        sf = _build_salesforce_with_state(state={})
        # Use bytes() with size but no real payload to keep memory low.
        oversize = bytes(DEFAULT_MAX_STAGE_BYTES + 1)
        with patch(
            "app.agents.actions.salesforce.salesforce.fetch_staged_document_bytes",
            new=AsyncMock(return_value=oversize),
        ):
            result = await sf._upload_one_staged_document_to_salesforce(
                doc_id="d1",
                registry={"d1": _valid_registry_entry(storage_type="external")},
                org_id="org-1",
                config_service=MagicMock(),
            )
        assert result["ok"] is False
        assert "size_limit_exceeded" in result["error"]
        assert result["limit_bytes"] == DEFAULT_MAX_STAGE_BYTES

    @pytest.mark.asyncio
    async def test_session_forwarded_to_fetcher(self):
        """Regression: helper forwards ``session`` kwarg verbatim."""
        sf = _build_salesforce_with_state(state={})
        injected = MagicMock(name="injected-session")
        with patch(
            "app.agents.actions.salesforce.salesforce.fetch_staged_document_bytes",
            new=AsyncMock(return_value=b""),
        ) as mock_fetch:
            await sf._upload_one_staged_document_to_salesforce(
                doc_id="d1",
                registry={"d1": _valid_registry_entry(storage_type="external")},
                org_id="org-1",
                config_service=MagicMock(),
                session=injected,
            )
        assert mock_fetch.await_args.kwargs["session"] is injected


class TestAttachFileToRecord:
    @pytest.mark.asyncio
    async def test_success_with_defaults(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_success_response({"id": "06Axx0000001"}),
        )
        ok, body = await sf.attach_file_to_record(
            content_document_id="069xx0000001",
            record_id="006xx0000001",
        )
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["content_document_link_id"] == "06Axx0000001"
        assert parsed["data"]["content_document_id"] == "069xx0000001"
        assert parsed["data"]["linked_record_id"] == "006xx0000001"
        assert parsed["data"]["share_type"] == "V"
        assert parsed["data"]["visibility"] == "AllUsers"
        # Verify the SF call payload is the expected ContentDocumentLink shape.
        kwargs = sf.client.sobject_create.call_args.kwargs
        assert kwargs["sobject"] == "ContentDocumentLink"
        assert kwargs["data"] == {
            "ContentDocumentId": "069xx0000001",
            "LinkedEntityId": "006xx0000001",
            "ShareType": "V",
            "Visibility": "AllUsers",
        }

    @pytest.mark.asyncio
    async def test_custom_share_type_and_visibility(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_success_response({"id": "06Axx0000002"}),
        )
        ok, _ = await sf.attach_file_to_record(
            content_document_id="069xx0000002",
            record_id="006xx0000002",
            share_type="C",
            visibility="InternalUsers",
        )
        assert ok is True
        kwargs = sf.client.sobject_create.call_args.kwargs
        assert kwargs["data"]["ShareType"] == "C"
        assert kwargs["data"]["Visibility"] == "InternalUsers"

    @pytest.mark.asyncio
    async def test_link_create_failure(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_error_response("DUPLICATE_VALUE"),
        )
        ok, body = await sf.attach_file_to_record(
            content_document_id="069x", record_id="006x",
        )
        assert ok is False
        parsed = json.loads(body)
        assert parsed["error"] == "DUPLICATE_VALUE"
        assert parsed["content_document_id"] == "069x"
        assert parsed["record_id"] == "006x"

    @pytest.mark.asyncio
    async def test_exception_returns_error(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(side_effect=Exception("network"))
        ok, body = await sf.attach_file_to_record(
            content_document_id="069x", record_id="006x",
        )
        assert ok is False
        assert "network" in body


# ============================================================================
# Additional coverage: helpers, upload pipeline, validation branches
# ============================================================================


class TestInjectWebUrl:
    def test_enriches_id_fields_on_dict(self):
        sf = _build_salesforce()
        data = {"Id": "001ABC", "Name": "Acme"}
        result = sf._inject_web_url(data)
        assert result["weburl_id"] == "https://mycompany.my.salesforce.com/001ABC"

    def test_recurses_into_lists_and_nested_dicts(self):
        sf = _build_salesforce()
        data = {
            "records": [
                {"AccountId": "001PARENT", "nested": {"OwnerId": "005OWNER"}},
            ],
        }
        result = sf._inject_web_url(data)
        row = result["records"][0]
        assert row["weburl_accountid"] == "https://mycompany.my.salesforce.com/001PARENT"
        assert row["nested"]["weburl_ownerid"] == (
            "https://mycompany.my.salesforce.com/005OWNER"
        )

    def test_skips_weburl_when_instance_url_missing(self):
        sf = _build_salesforce()
        sf.instance_url = ""
        result = sf._inject_web_url({"Id": "001ABC"})
        assert "weburl_id" not in result

    def test_non_dict_passthrough(self):
        sf = _build_salesforce()
        assert sf._inject_web_url("plain") == "plain"
        assert sf._inject_web_url(42) == 42


class TestHandleResponseWebUrlInjection:
    def test_success_injects_web_urls_into_payload(self):
        sf = _build_salesforce()
        resp = _success_response({"Id": "001XYZ", "Name": "Test"})
        ok, body = sf._handle_response(resp, "OK")
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["weburl_id"] == (
            "https://mycompany.my.salesforce.com/001XYZ"
        )


class TestSanitizeSoqlValue:
    def test_escapes_quotes_and_backslashes(self):
        from app.agents.actions.salesforce.salesforce import Salesforce

        assert Salesforce._sanitize_soql_value("O'Brien") == "O\\'Brien"
        assert Salesforce._sanitize_soql_value("a\\b") == "a\\\\b"


class TestValidateApiName:
    def test_valid_names(self):
        from app.agents.actions.salesforce.salesforce import Salesforce

        assert Salesforce._validate_api_name("Account") == "Account"
        assert Salesforce._validate_api_name("Custom__c") == "Custom__c"

    def test_invalid_name_raises(self):
        from app.agents.actions.salesforce.salesforce import Salesforce

        with pytest.raises(ValueError, match="Invalid Salesforce API name"):
            Salesforce._validate_api_name("'; DROP TABLE--")


class TestMarkdownEdgeCases:
    def test_underline_bold_syntax(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("__bold__")
        markup_begins = [s for s in segs if s.type == "MarkupBegin"]
        assert any(s.markupType.value == "Bold" for s in markup_begins)

    def test_regular_lines_separated_by_newline_emitter(self):
        sf = _build_salesforce()
        segs = sf._markdown_to_chatter_segments("line one\nline two")
        text_segs = [s for s in segs if s.type == "Text"]
        assert any("\n" in s.text for s in text_segs)


class TestCreateRecordGeneric:
    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(side_effect=RuntimeError("boom"))
        ok, body = await sf.create_record(sobject="Account", data={"Name": "X"})
        assert ok is False
        assert "boom" in body


class TestUpdateRecordValidation:
    @pytest.mark.asyncio
    async def test_empty_data_returns_validation_error(self):
        sf = _build_salesforce()
        ok, body = await sf.update_record(
            sobject="Account", record_id="001", data=None,
        )
        assert ok is False
        parsed = json.loads(body)
        assert "`data` is required" in parsed["error"]
        assert parsed["next_action"]["tool"] == "salesforce.describe_object"

    @pytest.mark.asyncio
    async def test_exception(self):
        sf = _build_salesforce()
        sf.client.sobject_update = AsyncMock(side_effect=RuntimeError("fail"))
        ok, body = await sf.update_record(
            sobject="Account", record_id="001", data={"Name": "X"},
        )
        assert ok is False
        assert "fail" in body


class TestDescribeObjectFieldFiltering:
    @pytest.mark.asyncio
    async def test_skips_non_dict_field_entries(self):
        sf = _build_salesforce()
        describe_data = {
            "name": "Account",
            "fields": [
                "not-a-field-dict",
                {
                    "name": "Status",
                    "picklistValues": [
                        {"value": "New", "active": True},
                        {"value": "Old", "active": False},
                    ],
                },
            ],
        }
        sf.client.s_object_describe = AsyncMock(
            return_value=_success_response(describe_data),
        )
        ok, body = await sf.describe_object(sobject="Account")
        assert ok is True
        fields = json.loads(body)["data"]["fields"]
        assert len(fields) == 2
        assert fields[1]["picklistValues"] == [{"value": "New", "active": True}]


class TestUploadBytesAsContentVersion:
    @pytest.mark.asyncio
    async def test_success_with_lookup(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_success_response({"id": "068CV"}),
        )
        sf.client.soql_query = AsyncMock(
            return_value=_success_response({
                "records": [{
                    "ContentDocumentId": "069CD",
                    "ContentSize": 4,
                    "FileType": "PDF",
                    "FileExtension": "pdf",
                }],
            }),
        )
        result = await sf._upload_bytes_as_content_version(
            raw=b"test",
            filename="doc.pdf",
            mime_type="application/pdf",
            document_id="d1",
        )
        assert result["ok"] is True
        assert result["content_version_id"] == "068CV"
        assert result["content_document_id"] == "069CD"
        assert result["weburl_content_document_id"].endswith("/069CD")

    @pytest.mark.asyncio
    async def test_cv_create_failure(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_error_response("FIELD_REQUIRED"),
        )
        result = await sf._upload_bytes_as_content_version(
            raw=b"x", filename="f.pdf", mime_type="application/pdf",
        )
        assert result["ok"] is False
        assert "FIELD_REQUIRED" in result["error"]

    @pytest.mark.asyncio
    async def test_cv_success_without_id(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(return_value=_success_response({}))
        result = await sf._upload_bytes_as_content_version(
            raw=b"x", filename="f.pdf", mime_type="application/pdf",
        )
        assert result["ok"] is False
        assert "no id" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_lookup_missing_content_document_id(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_success_response({"id": "068CV"}),
        )
        sf.client.soql_query = AsyncMock(
            return_value=_success_response({"records": []}),
        )
        result = await sf._upload_bytes_as_content_version(
            raw=b"x", filename="f.pdf", mime_type="application/pdf",
        )
        assert result["ok"] is False
        assert "ContentDocumentId lookup" in result["error"]
        assert result["content_version_id"] == "068CV"

    @pytest.mark.asyncio
    async def test_size_mismatch_logged_but_still_succeeds(self):
        sf = _build_salesforce()
        sf.client.sobject_create = AsyncMock(
            return_value=_success_response({"id": "068CV"}),
        )
        sf.client.soql_query = AsyncMock(
            return_value=_success_response({
                "records": [{"ContentDocumentId": "069CD", "ContentSize": 999}],
            }),
        )
        result = await sf._upload_bytes_as_content_version(
            raw=b"four", filename="f.pdf", mime_type="application/pdf",
        )
        assert result["ok"] is True
        assert result["sf_content_size"] == 999


class TestUploadOneStagedDocumentSuccess:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        sf = _build_salesforce_with_state(state={})
        entry = _valid_registry_entry()
        sf._upload_bytes_as_content_version = AsyncMock(
            return_value={
                "ok": True,
                "content_version_id": "068",
                "content_document_id": "069",
                "filename": entry["filename"],
                "mime_type": entry["mime_type"],
                "size_bytes": 4,
                "weburl_content_document_id": "https://example/069",
            },
        )
        with patch(
            "app.agents.actions.salesforce.salesforce.fetch_staged_document_bytes",
            new=AsyncMock(return_value=b"data"),
        ):
            result = await sf._upload_one_staged_document_to_salesforce(
                doc_id="d1",
                registry={"d1": entry},
                org_id="org-1",
                config_service=MagicMock(),
            )
        assert result["ok"] is True
        assert result["document_id"] == "d1"
        assert result["content_document_id"] == "069"

    @pytest.mark.asyncio
    async def test_upload_helper_failure_preserves_content_version_id(self):
        sf = _build_salesforce_with_state(state={})
        entry = _valid_registry_entry()
        sf._upload_bytes_as_content_version = AsyncMock(
            return_value={
                "ok": False,
                "error": "SF rejected",
                "content_version_id": "068PARTIAL",
            },
        )
        with patch(
            "app.agents.actions.salesforce.salesforce.fetch_staged_document_bytes",
            new=AsyncMock(return_value=b"data"),
        ):
            result = await sf._upload_one_staged_document_to_salesforce(
                doc_id="d1",
                registry={"d1": entry},
                org_id="org-1",
                config_service=MagicMock(),
            )
        assert result["ok"] is False
        assert result["content_version_id"] == "068PARTIAL"


class TestAddProductOppLookupException:
    @pytest.mark.asyncio
    async def test_opp_parse_exception_falls_back_to_no_pricebook(self):
        sf = _build_salesforce()
        bad_opp = MagicMock()
        type(bad_opp).data = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("parse fail")),
        )
        sf.client.soql_query = AsyncMock(return_value=bad_opp)
        ok, body = await sf.add_product_to_opportunity(
            opportunity_id="006", product_id="01t",
        )
        assert ok is False
        assert "Pricebook2Id" in json.loads(body)["error"]
