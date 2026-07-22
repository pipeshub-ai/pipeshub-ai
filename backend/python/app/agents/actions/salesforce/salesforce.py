import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
from typing import Any, Optional

import aiohttp

from app.agents.actions.salesforce.models import (
    AccountData,
    AddProductToOpportunityInput,
    CaseData,
    ChatterMarkupType,
    ChatterSegment,
    CreateAccountInput,
    CreateCaseInput,
    CreateContactInput,
    CreateLeadInput,
    CreateOpportunityInput,
    CreatePricebookEntryInput,
    CreateProductInput,
    AttachFileToRecordData,
    ContentDocumentLinkCreatePayload,
    ContentVersionCreatePayload,
    ContentVersionUploadResult,
    CreateRecordInput,
    CreateTaskInput,
    ContactData,
    DEFAULT_API_VERSION,
    DescribeObjectInput,
    ERR_NEED_PBE_OR_PRODUCT,
    ERR_NO_ACTIVE_PBE,
    ERR_OPP_NO_PRICEBOOK,
    GetRecordChatterInput,
    GetRecordInput,
    GetUserInfoInput,
    HyperlinkSegment,
    LeadData,
    ListPricebooksInput,
    ListRecentRecordsInput,
    MarkupBeginSegment,
    MarkupEndSegment,
    MSG_SUCCESS,
    MSG_UNKNOWN_ERROR,
    ERR_LOG,
    SF_CHATTER_FEED_ELEMENT_TYPE,
    SF_FIELD_PRICEBOOK2_ID,
    SF_FIELD_UNIT_PRICE,
    SF_KEY_ID,
    SF_KEY_SUCCESS,
    SF_KEY_RECORDS,
    OpportunityData,
    OpportunityLineItemData,
    PostChatterCommentInput,
    PostChatterToRecordInput,
    ProductData,
    SearchAccountsInput,
    SearchCasesInput,
    SearchContactsInput,
    SearchLeadsInput,
    SearchOpportunitiesInput,
    SearchProductsInput,
    SearchTasksInput,
    SalesforceFieldMap,
    StagedDocumentUploadResult,
    UploadFileToSalesforceData,
    SF_JSON_DATA_KEY,
    SF_JSON_ERROR_KEY,
    SF_JSON_MESSAGE_KEY,
    SF_SOBJECT_ACCOUNT,
    SF_SOBJECT_CASE,
    SF_SOBJECT_CONTACT,
    SF_SOBJECT_CONTRACT,
    SF_SOBJECT_CONTENT_DOCUMENT,
    SF_SOBJECT_EMAIL_MESSAGE,
    SF_SOBJECT_EVENT,
    SF_SOBJECT_CONTENT_DOCUMENT_LINK,
    SF_SOBJECT_CONTENT_VERSION,
    SF_SOBJECT_LEAD,
    SF_SOBJECT_NOTE,
    SF_SOBJECT_OPPORTUNITY,
    SF_SOBJECT_OPPORTUNITY_LINE_ITEM,
    SF_SOBJECT_ORDER,
    SF_SOBJECT_PRICEBOOK_ENTRY,
    SF_SOBJECT_PRODUCT2,
    SF_SOBJECT_TASK,
    SOQL_LIST_PRICEBOOKS,
    SOQL_LIST_RECENT_RECORDS,
    SOQL_OPPORTUNITY_PRICEBOOK2_BY_ID,
    SOQL_PRICEBOOK_ENTRY_BY_PRODUCT_AND_BOOK,
    SOQL_SEARCH_ACCOUNTS,
    SOQL_SEARCH_CASES,
    SOQL_SEARCH_CONTACTS,
    SOQL_SEARCH_LEADS,
    SOQL_SEARCH_OPPORTUNITIES,
    SOQL_SEARCH_PRODUCTS,
    SOQL_SEARCH_TASKS,
    SF_KEY_ID_LIST,
    SOQLQueryInput,
    SOSLSearchInput,
    TaskData,
    TextSegment,
    UpdateRecordInput,
    UploadFileToSalesforceInput,
    AttachFileToRecordInput,
)
from app.agents.actions.util.blob_staging import (
    DEFAULT_MAX_STAGE_BYTES,
    BlobStagingError,
    StagedDocumentEntry,
    fetch_staged_document_bytes,
)
from app.config.configuration_service import ConfigurationService
from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.modules.agents.qna.chat_state import ChatState
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.connector_builder import AuthField, CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.connectors.core.registry.types import DocumentationLink
from app.sources.client.salesforce.salesforce import SalesforceClient, SalesforceResponse
from app.sources.external.salesforce.salesforce_data_source import SalesforceDataSource

_SF_KEY_ID_SET = frozenset(SF_KEY_ID_LIST)
logger = logging.getLogger(__name__)

# Caps in-flight ContentVersion uploads per ``upload_file_to_salesforce``
# call. Bounded by ``UploadFileToSalesforceInput.max_length=10`` (10 docs *
# 25 MiB ``DEFAULT_MAX_STAGE_BYTES`` = 250 MiB worst-case download buffer);
# 5 trades a bit of latency for a 125 MiB ceiling and headroom under
# Salesforce's 25 concurrent sync request soft limit when several agents
# upload in parallel from the same org.
_SF_UPLOAD_CONCURRENCY = 5

# ---------------------------------------------------------------------------
# Toolset registration
# ---------------------------------------------------------------------------

@ToolsetBuilder("Salesforce")\
    .in_group("CRM")\
    .with_description("Salesforce CRM integration for managing accounts, contacts, leads, opportunities, cases, and executing SOQL/SOSL queries")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Salesforce",
            authorize_url="https://login.salesforce.com/services/oauth2/authorize",
            token_url="https://login.salesforce.com/services/oauth2/token",
            redirect_uri="toolsets/oauth/callback/salesforce",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "api",
                    "refresh_token",
                    "id",
                ]
            ),
            additional_params={
                "prompt": "consent",
            },
            fields=[
                AuthField(
                    name="instance_url",
                    display_name="Salesforce Instance URL",
                    placeholder="https://yourcompany.my.salesforce.com",
                    description="The base URL of your Salesforce instance",
                    field_type="URL",
                    required=True,
                    usage="CONFIGURE",
                    max_length=2048,
                    is_secret=False,
                ),
                CommonFields.client_id("Salesforce Connected App"),
                CommonFields.client_secret("Salesforce Connected App"),
            ],
            icon_path=IconPaths.connector_icon("salesforce"),
            app_group="CRM",
            app_description="Salesforce OAuth application for agent integration",
        ),
    ])\
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("salesforce"))
        .add_documentation_link(DocumentationLink(
            "Salesforce OAuth Setup",
            "https://help.salesforce.com/s/articleView?id=xcloud.create_a_local_external_client_app.htm&type=5",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/salesforce/salesforce",
            "pipeshub",
        )))\
    .build_decorator()
class Salesforce:
    """Salesforce CRM tools exposed to agents using SalesforceDataSource."""

    _RECENT_RECORD_DISPLAY_FIELD_BY_SOBJECT: dict[str, str] = {
        SF_SOBJECT_CASE: "CaseNumber",
        SF_SOBJECT_TASK: "Subject",
        SF_SOBJECT_EMAIL_MESSAGE: "Subject",
        SF_SOBJECT_EVENT: "Subject",
        SF_SOBJECT_ORDER: "OrderNumber",
        SF_SOBJECT_CONTRACT: "ContractNumber",
        SF_SOBJECT_CONTENT_DOCUMENT: "Title",
        SF_SOBJECT_NOTE: "Title",
    }
    def __init__(self, client: SalesforceClient, state: ChatState) -> None:
        self.client = SalesforceDataSource(client)
        self.api_version = DEFAULT_API_VERSION
        self.instance_url = (client.get_base_url() or "").rstrip("/")
        self.chat_state = state

    def _build_web_url(self, record_id: str | None) -> str | None:
        """Build a Salesforce Lightning web URL for a record."""
        if not self.instance_url or not record_id:
            return None
        return f"{self.instance_url}/{record_id}"

    def _inject_web_url(self, obj: object) -> object:
        """Recursively add web URLs for every SF_KEY_ID_LIST field on each dict.

        For each key that matches SF_KEY_ID_LIST (case-insensitive) with a non-null
        value, sets ``weburl_<lowercase_key>`` to the instance URL for that id.
        Walks dicts and lists so nested SOQL rows are enriched.
        """
        if isinstance(obj, list):
            return [self._inject_web_url(item) for item in obj]

        if not isinstance(obj, dict):
            return obj

        # Recurse into all values first so nested records are enriched
        for key, value in obj.items():
            obj[key] = self._inject_web_url(value)

        id_fields = [
            (key, val)
            for key, val in obj.items()
            if val is not None and str(key).lower() in _SF_KEY_ID_SET
        ]
        for key, val in id_fields:
            web_url = self._build_web_url(str(val))
            if web_url:
                obj[f"weburl_{str(key).lower()}"] = web_url

        return obj

    def _handle_response(
        self,
        response: SalesforceResponse,
        success_message: str,
        **_unused: object,
    ) -> tuple[bool, str]:
        """Return a standardised (success, json_string) tuple."""
        if response.success:
            data = self._inject_web_url(response.data)
            return True, json.dumps(
                {
                    SF_KEY_SUCCESS: True,
                    SF_JSON_MESSAGE_KEY: success_message,
                    SF_JSON_DATA_KEY: data,
                },
                default=str,
            )
        error = response.error or MSG_UNKNOWN_ERROR
        return self._error_response(error)

    @staticmethod
    def _error_response(msg: str) -> tuple[bool, str]:
        """Return a standardised (False, json_string) error tuple."""
        return False, json.dumps({SF_KEY_SUCCESS: False, SF_JSON_ERROR_KEY: msg})

    @staticmethod
    def _sanitize_soql_value(value: str) -> str:
        """Escape characters that are special in SOQL string literals."""
        value = value.replace("\\", "\\\\")
        value = value.replace("'", "\\'")
        return value

    # Regex for valid Salesforce API names (sObject names, field names, etc.)
    _VALID_API_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(__[a-zA-Z0-9]+)?$")

    @classmethod
    def _validate_api_name(cls, name: str) -> str:
        """Validate that a string is a legitimate Salesforce API name.

        Raises ValueError if the name contains characters that could be used
        for SOQL injection.
        """
        if not cls._VALID_API_NAME_RE.match(name):
            raise ValueError(f"Invalid Salesforce API name: {name!r}")
        return name

    def _build_soql_conditions(self, conditions: list[str]) -> str:
        """Build a WHERE clause from a list of conditions."""
        if not conditions:
            return ""
        return " WHERE " + " AND ".join(conditions)

    @classmethod
    def _get_recent_record_display_field(cls, sobject: str) -> str:
        """Return a display field for recent-record queries by sObject type."""
        return cls._RECENT_RECORD_DISPLAY_FIELD_BY_SOBJECT.get(sobject, "Name")

    def _markdown_to_chatter_segments(self, text: str) -> list[ChatterSegment]:
        """Convert markdown into Salesforce Chatter messageSegments.

        Supports:
        - # / ## / ### headings  → Bold text + paragraph break
        - **bold** / __bold__    → MarkupBegin/MarkupEnd type=Bold
        - *italic* / _italic_    → MarkupBegin/MarkupEnd type=Italic
        - [label](url)           → Hyperlink segment
        - - / * / + bullet lists → UnorderedList + ListItem
        - 1. / 2. numbered lists → OrderedList + ListItem
        - --- / *** / ___ hr     → Paragraph break (newline separator)
        - blank line             → Paragraph break
        - newline                → preserved as text
        """
        if not text:
            return [TextSegment(text="")]

        segments: list[ChatterSegment] = []

        # Inline pattern: bold (**x** or __x__), italic (*x* or _x_), link [x](y)
        inline_re = re.compile(
            r"(\*\*([^*\n]+)\*\*|__([^_\n]+)__|\*([^*\n]+)\*|_([^_\n]+)_|\[([^\]]+)\]\(([^)]+)\))"
        )

        def _emit_text(s: str) -> None:
            if s:
                segments.append(TextSegment(text=s))

        def _emit_inline(line: str) -> None:
            """Parse inline markdown (bold, italic, links) within a line."""
            pos = 0
            for m in inline_re.finditer(line):
                _emit_text(line[pos:m.start()])
                bold = m.group(2) or m.group(3)
                italic = m.group(4) or m.group(5)
                link_label = m.group(6)
                link_url = m.group(7)
                if bold:
                    segments.append(MarkupBeginSegment(markupType=ChatterMarkupType.BOLD))
                    _emit_text(bold)
                    segments.append(MarkupEndSegment(markupType=ChatterMarkupType.BOLD))
                elif italic:
                    segments.append(MarkupBeginSegment(markupType=ChatterMarkupType.ITALIC))
                    _emit_text(italic)
                    segments.append(MarkupEndSegment(markupType=ChatterMarkupType.ITALIC))
                elif link_label and link_url:
                    segments.append(HyperlinkSegment(url=link_url, text=link_label))
                pos = m.end()
            _emit_text(line[pos:])

        def _emit_paragraph_break() -> None:
            segments.append(MarkupBeginSegment(markupType=ChatterMarkupType.PARAGRAPH))
            segments.append(MarkupEndSegment(markupType=ChatterMarkupType.PARAGRAPH))

        # Regexes for block-level elements
        heading_re = re.compile(r"^(#{1,6})\s+(.*)")
        hr_re = re.compile(r"^[-*_]{3,}\s*$")
        ul_re = re.compile(r"^[-*+]\s+(.*)")
        ol_re = re.compile(r"^\d+[.)]\s+(.*)")

        lines = text.split("\n")
        i = 0
        first_block = True

        while i < len(lines):
            line = lines[i]

            # Blank line → paragraph break
            if not line.strip():
                if not first_block:
                    _emit_paragraph_break()
                i += 1
                continue

            # Horizontal rule → paragraph break (visual separator)
            if hr_re.match(line):
                if not first_block:
                    _emit_paragraph_break()
                i += 1
                continue

            # Heading → bold text
            hm = heading_re.match(line)
            if hm:
                if not first_block:
                    _emit_paragraph_break()
                first_block = False
                heading_text = hm.group(2).strip()
                segments.append(MarkupBeginSegment(markupType=ChatterMarkupType.BOLD))
                _emit_inline(heading_text)
                segments.append(MarkupEndSegment(markupType=ChatterMarkupType.BOLD))
                i += 1
                continue

            # Unordered list block
            um = ul_re.match(line)
            if um:
                if not first_block:
                    _emit_paragraph_break()
                first_block = False
                segments.append(MarkupBeginSegment(markupType=ChatterMarkupType.UNORDERED_LIST))
                while i < len(lines):
                    um = ul_re.match(lines[i])
                    if not um:
                        break
                    segments.append(MarkupBeginSegment(markupType=ChatterMarkupType.LIST_ITEM))
                    _emit_inline(um.group(1))
                    segments.append(MarkupEndSegment(markupType=ChatterMarkupType.LIST_ITEM))
                    i += 1
                segments.append(MarkupEndSegment(markupType=ChatterMarkupType.UNORDERED_LIST))
                continue

            # Ordered list block
            om = ol_re.match(line)
            if om:
                if not first_block:
                    _emit_paragraph_break()
                first_block = False
                segments.append(MarkupBeginSegment(markupType=ChatterMarkupType.ORDERED_LIST))
                while i < len(lines):
                    om = ol_re.match(lines[i])
                    if not om:
                        break
                    segments.append(MarkupBeginSegment(markupType=ChatterMarkupType.LIST_ITEM))
                    _emit_inline(om.group(1))
                    segments.append(MarkupEndSegment(markupType=ChatterMarkupType.LIST_ITEM))
                    i += 1
                segments.append(MarkupEndSegment(markupType=ChatterMarkupType.ORDERED_LIST))
                continue

            # Regular text line
            if not first_block:
                _emit_text("\n")
            first_block = False
            _emit_inline(line)
            i += 1

        if not segments:
            segments.append(TextSegment(text=text))
        return segments

    # ------------------------------------------------------------------
    # SOQL / SOSL Query Tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/soql_query",
        short_description="Execute a SOQL query against Salesforce",
        description=(
            "Executes a Salesforce Object Query Language (SOQL) query for structured queries against "
            "specific objects and fields. Examples: 'SELECT Id, Name FROM Account WHERE Industry = 'Technology' LIMIT 10'. "
            "Always include LIMIT to avoid returning too many records. Use for queries with specific filters, "
            "aggregate data (COUNT, SUM, AVG), custom objects, or non-standard fields. "
            "For simple text search across multiple objects use sosl_search; for simple name-based lookups "
            "use the dedicated search tools (search_accounts, search_contacts, etc.)."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="The SOQL query string to execute", required=True),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="search")],
    )
    async def soql_query(self, query: str) -> tuple[bool, str]:
        """Execute a SOQL query."""
        try:
            logger.info("salesforce.soql_query called with query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "soql_query", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/sosl_search",
        short_description="Execute a SOSL search across Salesforce objects",
        description=(
            "Executes a Salesforce Object Search Language (SOSL) full-text search across multiple objects. "
            "Example: 'FIND {Acme} IN ALL FIELDS RETURNING Account(Id, Name), Contact(Id, Name, Email)'. "
            "Best for keyword search across multiple objects or finding all records related to a term. "
            "For structured queries with specific filters use soql_query; for single-object searches "
            "use the dedicated search tools."
        ),
        parameters=[
            ToolParameter(name="search", type=ParameterType.STRING, description="The SOSL search string to execute", required=True),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="search")],
    )
    async def sosl_search(self, search: str) -> tuple[bool, str]:
        """Execute a SOSL search."""
        try:
            logger.info("salesforce.sosl_search called with search: %s", search)
            response = await self.client.sosl_search(
                api_version=self.api_version, q=search
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "sosl_search", e)
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # Generic Record CRUD Tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/get_record",
        short_description="Retrieve a Salesforce record by ID",
        description=(
            "Retrieves a single record from any Salesforce object by its record ID. "
            "Provide the object API name (e.g., 'Account', 'Contact', 'Lead', 'Opportunity', 'Case', or any custom object like 'Custom__c') "
            "and the 15/18-character record ID. Optionally specify fields to return. "
            "Use when you have a specific record ID. For searching without an ID use search or SOQL tools; "
            "for browsing recent records use list_recent_records."
        ),
        parameters=[
            ToolParameter(name="sobject", type=ParameterType.STRING, description="The Salesforce object API name (e.g., 'Account', 'Contact', 'Lead')", required=True),
            ToolParameter(name="record_id", type=ParameterType.STRING, description="The 15 or 18-character Salesforce record ID", required=True),
            ToolParameter(name="fields", type=ParameterType.STRING, description="Comma-separated list of fields to return (e.g., 'Id,Name,Email'). Omit to return all accessible fields.", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="read")],
    )
    async def get_record(
        self, sobject: str, record_id: str, fields: str | None = None
    ) -> tuple[bool, str]:
        """Retrieve a Salesforce record by ID."""
        try:
            logger.info(
                "salesforce.get_record called: sobject=%s, record_id=%s", sobject, record_id
            )
            response = await self.client.sobject_get(
                api_version=self.api_version,
                sobject=sobject,
                record_id=record_id,
                fields=fields,
            )
            return self._handle_response(
                response,
                MSG_SUCCESS,
                sobject=sobject,
            )
        except Exception as e:
            logger.error(ERR_LOG, "get_record", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/create_record",
        short_description="Create a new Salesforce record",
        description=(
            "Creates a new record for any Salesforce object. Supply `sobject` (API name) and `data` "
            "(non-empty object of field API names to values). If you do not know the createable/required "
            "fields, call describe_object first. Standard fields use their API names (e.g. 'Name', 'Email'); "
            "custom fields end with '__c'. Prefer specialized create tools (create_account, create_contact, "
            "create_lead, create_opportunity, create_case) when they apply."
        ),
        parameters=[
            ToolParameter(name="sobject", type=ParameterType.STRING, description="The Salesforce object API name (e.g. 'Account', 'Custom_Object__c')", required=True),
            ToolParameter(name="data", type=ParameterType.OBJECT, description="Non-empty map of field API names to values for the new record", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def create_record(
        self, sobject: str, data: SalesforceFieldMap | None = None
    ) -> tuple[bool, str]:
        """Create a new Salesforce record."""
        try:
            logger.info("salesforce.create_record called: sobject=%s", sobject)
            if not isinstance(data, dict) or not data:
                return False, json.dumps({
                    SF_JSON_ERROR_KEY: (
                        f"`data` is required and must be a non-empty object of "
                        f"field API names to values for {sobject}. "
                        f"If you do not know the fields, call describe_object "
                        f"with sobject='{sobject}' first, then retry create_record "
                        f"with the createable fields populated under `data` "
                        f"(do not flatten field names to top-level arguments)."
                    ),
                    "next_action": {
                        "tool": "salesforce.describe_object",
                        "args": {"sobject": sobject},
                    },
                })
            response = await self.client.sobject_create(
                api_version=self.api_version, sobject=sobject, data=data
            )
            return self._handle_response(
                response,
                MSG_SUCCESS,
                sobject=sobject,
            )
        except Exception as e:
            logger.error(ERR_LOG, "create_record", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/update_record",
        short_description="Update an existing Salesforce record",
        description=(
            "Updates an existing Salesforce record by ID. Provide the object API name, record ID, and a "
            "dictionary of field-value pairs to update under the `data` key. Only include fields that "
            "should change. For creating new records use create_record or specialized create tools."
        ),
        parameters=[
            ToolParameter(name="sobject", type=ParameterType.STRING, description="The Salesforce object API name (e.g., 'Account', 'Contact')", required=True),
            ToolParameter(name="record_id", type=ParameterType.STRING, description="The 15 or 18-character Salesforce record ID", required=True),
            ToolParameter(name="data", type=ParameterType.OBJECT, description="Field-value pairs to update (e.g., {\"Name\": \"New Name\"})", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def update_record(
        self,
        sobject: str,
        record_id: str,
        data: SalesforceFieldMap | None = None,
    ) -> tuple[bool, str]:
        """Update a Salesforce record."""
        try:
            logger.info(
                "salesforce.update_record called: sobject=%s, record_id=%s", sobject, record_id
            )
            if not isinstance(data, dict) or not data:
                return False, json.dumps({
                    SF_JSON_ERROR_KEY: (
                        f"`data` is required and must be a non-empty object of "
                        f"field API names to values to update on {sobject} "
                        f"{record_id}. Pass the changed fields under the `data` "
                        f"key (do not flatten field names to top-level arguments). "
                        f"If you do not know the updatable fields, call "
                        f"describe_object with sobject='{sobject}' first."
                    ),
                    "next_action": {
                        "tool": "salesforce.describe_object",
                        "args": {"sobject": sobject},
                    },
                })
            response = await self.client.sobject_update(
                api_version=self.api_version,
                sobject=sobject,
                record_id=record_id,
                data=data,
            )
            return self._handle_response(
                response,
                MSG_SUCCESS,
                sobject=sobject,
            )
        except Exception as e:
            logger.error(ERR_LOG, "update_record", e)
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # File Upload + Attach (cross-toolset transfer landing points)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_path_on_client(filename: str, mime_type: str) -> str:
        """Return a PathOnClient string that carries a usable file extension.

        Salesforce derives ``FileType`` (and therefore preview rendering)
        from the extension on ``PathOnClient``. If the caller hands us a
        filename without an extension (e.g. Graph returned just "Invoice"),
        SF stores ``FileType = "UNKNOWN"`` and the UI reports "preview
        unavailable" — which end users describe as "the file is corrupt".
        Guarantee an extension by:

        1. Stripping/cleaning the supplied filename.
        2. If it already has an extension, keep it as-is.
        3. Otherwise, derive one from the MIME type via
           ``mimetypes.guess_extension`` (falls back to ``.bin``).
        """
        cleaned = (filename or "").strip()
        if not cleaned:
            cleaned = "attachment.bin"

        # ``splitext`` returns ('', '') for inputs like 'no-ext' or '.dotfile'
        # in the way we want (the dotfile case is rare enough that mapping it
        # through mimetype is acceptable).
        _, ext = os.path.splitext(cleaned)
        if ext and len(ext) > 1:
            return cleaned

        guessed = mimetypes.guess_extension(
            (mime_type or "application/octet-stream").split(";", 1)[0].strip()
        )
        if not guessed:
            guessed = ".bin"
        return f"{cleaned}{guessed}"

    async def _upload_bytes_as_content_version(
        self,
        *,
        raw: bytes,
        filename: str,
        mime_type: str,
        document_id: str | None = None,
    ) -> ContentVersionUploadResult:
        """Create a Salesforce ContentVersion from in-memory bytes."""
        effective_filename = (filename or "attachment.bin").strip() or "attachment.bin"
        # SF previews break when PathOnClient lacks an extension; derive
        # one from the MIME type so a Graph attachment named "Invoice"
        # uploads as "Invoice.pdf" rather than "Invoice" (FileType=UNKNOWN).
        path_on_client = self._normalize_path_on_client(
            effective_filename, mime_type,
        )

        content_version_payload = ContentVersionCreatePayload(
            Title=effective_filename,
            PathOnClient=path_on_client,
            VersionData=base64.b64encode(raw).decode("ascii"),
        )

        cv_response = await self.client.sobject_create(
            api_version=self.api_version,
            sobject=SF_SOBJECT_CONTENT_VERSION,
            data=content_version_payload.model_dump(),
        )
        if not cv_response.success:
            return ContentVersionUploadResult(
                ok=False,
                error=cv_response.error or "Failed to create ContentVersion",
            )

        cv_data = cv_response.data or {}
        content_version_id = cv_data.get("id") or cv_data.get("Id")
        if not content_version_id:
            return ContentVersionUploadResult(
                ok=False,
                error="ContentVersion create succeeded but returned no id",
            )
        cv_id_str = str(content_version_id)

        # Resolve ContentDocumentId AND read back the size/type SF computed
        # from the upload. If SF's ContentSize differs from our raw size, the
        # JSON body lost / mangled bytes in flight. If FileType is UNKNOWN
        # but we sent a real PDF, PathOnClient handling broke.
        content_document_id: str | None = None
        sf_content_size: int | None = None
        sf_file_type: str | None = None
        sf_file_extension: str | None = None
        soql = (
            "SELECT ContentDocumentId, ContentSize, FileType, FileExtension "
            "FROM ContentVersion "
            f"WHERE Id = '{self._sanitize_soql_value(cv_id_str)}'"
        )
        cv_lookup = await self.client.soql_query(
            api_version=self.api_version, q=soql
        )
        if cv_lookup.success and isinstance(cv_lookup.data, dict):
            records = cv_lookup.data.get(SF_KEY_RECORDS) or []
            if records:
                rec = records[0]
                content_document_id = rec.get("ContentDocumentId")
                sf_content_size = rec.get("ContentSize")
                sf_file_type = rec.get("FileType")
                sf_file_extension = rec.get("FileExtension")

        # If SF disagrees with us on size, that's a transport-layer bug we
        # want to know about; only logged on mismatch to avoid steady-state
        # noise.
        if (
            isinstance(sf_content_size, int)
            and sf_content_size != len(raw)
        ):
            logger.error(
                "salesforce._upload_bytes_as_content_version doc_id=%s "
                "content_version_id=%s | SIZE MISMATCH: uploaded=%d "
                "SF.ContentSize=%d file_type=%r file_extension=%r",
                document_id, cv_id_str, len(raw),
                sf_content_size, sf_file_type, sf_file_extension,
            )

        if not content_document_id:
            return ContentVersionUploadResult(
                ok=False,
                error=(
                    "ContentVersion created but ContentDocumentId lookup "
                    "failed; the file exists in Salesforce but cannot be "
                    "attached without it."
                ),
                content_version_id=cv_id_str,
            )

        return ContentVersionUploadResult(
            ok=True,
            content_version_id=cv_id_str,
            content_document_id=str(content_document_id),
            filename=effective_filename,
            path_on_client=path_on_client,
            mime_type=mime_type or "application/octet-stream",
            size_bytes=len(raw),
            sf_content_size=sf_content_size,
            sf_file_type=sf_file_type,
            sf_file_extension=sf_file_extension,
            weburl_content_document_id=self._build_web_url(
                str(content_document_id),
            ),
        )

    async def _upload_one_staged_document_to_salesforce(
        self,
        *,
        doc_id: str,
        registry: dict[str, StagedDocumentEntry | dict[str, object]],
        org_id: str,
        config_service: ConfigurationService,
        session: aiohttp.ClientSession | None = None,
    ) -> StagedDocumentUploadResult:
        """Fetch one staged blob and create a ContentVersion; return one results row.

        ``session`` is forwarded to ``fetch_staged_document_bytes`` so a
        batched caller (``upload_file_to_salesforce``) can share one
        ``aiohttp.ClientSession`` across all parallel fetches and keep TCP
        connections to the cm endpoint / S3 host alive across the batch.
        When ``None``, the helper falls back to per-call sessions.
        """
        raw_entry = registry.get(doc_id)
        if raw_entry is None:
            known_ids = list(registry.keys())
            return StagedDocumentUploadResult(
                document_id=doc_id,
                ok=False,
                error=(
                    "not_found_in_chat_state: this document_id "
                    "was not registered by any producer tool in "
                    "the current turn. Use one of the "
                    "registered_document_ids below, or re-run the "
                    "producer (e.g. outlook.stage_attachment_to_blob) "
                    "and use the document_id it returns."
                ),
                registered_document_ids=known_ids,
            )

        # Boundary coercion: in-process producers write StagedDocumentEntry
        # directly, but a LangGraph checkpointer can round-trip chat_state
        # through JSON and entries come back as plain dicts — model_validate
        # handles both shapes and fails loudly on a malformed entry rather
        # than silently uploading "attachment.bin"/octet-stream.
        try:
            entry = StagedDocumentEntry.model_validate(raw_entry)
        except ValueError as validation_err:
            return StagedDocumentUploadResult(
                document_id=doc_id,
                ok=False,
                error=f"corrupt_registry_entry: {validation_err}",
            )

        try:
            raw = await fetch_staged_document_bytes(
                document_id=doc_id,
                entry=entry,
                org_id=org_id,
                config_service=config_service,
                session=session,
            )
        except BlobStagingError as fetch_err:
            return StagedDocumentUploadResult(
                document_id=doc_id,
                ok=False,
                error=f"Blob fetch failed: {fetch_err}",
            )
        except (aiohttp.ClientError, RuntimeError) as fetch_err:
            return StagedDocumentUploadResult(
                document_id=doc_id,
                ok=False,
                error=f"Download failed: {fetch_err}",
            )

        if not raw:
            return StagedDocumentUploadResult(
                document_id=doc_id,
                ok=False,
                error="Fetched zero bytes; refusing empty upload.",
            )

        if len(raw) > DEFAULT_MAX_STAGE_BYTES:
            return StagedDocumentUploadResult(
                document_id=doc_id,
                ok=False,
                error=(
                    f"size_limit_exceeded: {len(raw)} bytes > "
                    f"{DEFAULT_MAX_STAGE_BYTES} byte cap"
                ),
                size_bytes=len(raw),
                limit_bytes=DEFAULT_MAX_STAGE_BYTES,
            )

        # Title / PathOnClient / MIME come from the staged registry row
        # (chat_state.document_id_to_url → StagedDocumentEntry), not tool args.
        staged_filename = entry.filename
        staged_mime_type = entry.mime_type
        cv_result = await self._upload_bytes_as_content_version(
            raw=raw,
            filename=staged_filename,
            mime_type=staged_mime_type,
            document_id=doc_id,
        )
        return StagedDocumentUploadResult.from_content_version(
            document_id=doc_id,
            cv=cv_result,
        )

    @tool(
        path="/tools/salesforce/upload_file_to_salesforce",
        short_description="Upload staged files to Salesforce Files storage",
        description=(
            "Upload up to 10 staged files into Salesforce Files by passing their `document_ids` "
            "(from a *_to_blob staging tool). Returns per-file results with content_document_id. "
            "Use the output content_document_id with attach_file_to_record to link uploaded files "
            "to Salesforce records. The file must already be staged in PipesHub blob storage; "
            "if the file is already in Salesforce, use attach_file_to_record directly."
        ),
        parameters=[
            ToolParameter(name="document_ids", type=ParameterType.ARRAY, description="List of documentIds registered by a producer tool (1-10 ids per call)", required=True),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def upload_file_to_salesforce(
        self,
        document_ids: list[str],
    ) -> tuple[bool, str]:
        """Create Salesforce ContentVersions from one or more registered docIds.

        For each id the cached entry in
        ``chat_state['document_id_to_url']`` is resolved to bytes (either
        via a direct GET on a pre-signed URL, or via the scoped-token
        internal download path when the URL requires auth — see
        ``fetch_staged_document_bytes``), then a
        ``ContentVersion`` is created in Salesforce. One bad id does not
        fail the rest — per-id outcomes are reported in the ``results``
        array.
        """
        try:
            state = self.chat_state
            if not hasattr(state, "get"):
                return False, json.dumps({SF_JSON_ERROR_KEY: (
                    "Blob fetch requires the chat state container; this "
                    "tool cannot be invoked outside the agent runtime."
                )})

            org_id = state.get("org_id")
            config_service = state.get("config_service")
            if not org_id or not config_service:
                return False, json.dumps({SF_JSON_ERROR_KEY: (
                    "Blob fetch requires an authenticated agent context "
                    "(org_id and config_service). This tool cannot be "
                    "invoked outside the agent runtime."
                )})

            # ``document_id_to_url``: dict[document_id, StagedDocumentEntry]
            # (see ChatState). Producers set filename + mime_type per blob;
            # upload reads those from the registry row, not from tool args.
            registry = state.get("document_id_to_url")
            if not isinstance(registry, dict) or not registry:
                return False, json.dumps({
                    SF_JSON_ERROR_KEY: (
                        "no_staged_documents: the document_id_to_url "
                        "registry is empty. Run a producer tool (e.g. "
                        "outlook.stage_attachment_to_blob) FIRST and wait "
                        "for its result, then retry this tool with the "
                        "document_id values it returned. Do NOT call the "
                        "producer and this tool in parallel."
                    ),
                    "requested_document_ids": document_ids,
                    "registered_document_ids": [],
                })

            if not document_ids:
                return False, json.dumps({SF_JSON_ERROR_KEY: (
                    "`document_ids` must contain at least one id."
                )})

            sem = asyncio.Semaphore(_SF_UPLOAD_CONCURRENCY)

            # One shared session for the whole batch so the up to
            # ``_SF_UPLOAD_CONCURRENCY`` parallel fetches reuse TCP+TLS to
            # the cm endpoint / S3 host instead of each one paying its own
            # handshake. Per-request 120s timeout is enforced inside
            # ``fetch_staged_document_bytes``.
            async with aiohttp.ClientSession() as fetch_session:
                async def _bounded_upload(
                    doc_id: str,
                ) -> StagedDocumentUploadResult:
                    async with sem:
                        return await self._upload_one_staged_document_to_salesforce(
                            doc_id=doc_id,
                            registry=registry,
                            org_id=org_id,
                            config_service=config_service,
                            session=fetch_session,
                        )

                gathered = await asyncio.gather(
                    *(_bounded_upload(d) for d in document_ids),
                    return_exceptions=True,
                )

            results: list[dict[str, str | int | bool | list[str]]] = []
            for doc_id, outcome in zip(document_ids, gathered):
                if isinstance(outcome, BaseException):
                    results.append(
                        StagedDocumentUploadResult(
                            document_id=doc_id,
                            ok=False,
                            error=f"Unexpected upload failure: {outcome}",
                        ).to_wire_dict(),
                    )
                else:
                    results.append(outcome.to_wire_dict())

            succeeded = sum(1 for r in results if r.get("ok"))
            failed = len(results) - succeeded
            payload = UploadFileToSalesforceData(
                results=results,
                succeeded=succeeded,
                failed=failed,
            )
            ok = succeeded > 0
            return ok, json.dumps(
                {
                    SF_JSON_MESSAGE_KEY: (
                        MSG_SUCCESS if ok else "All uploads failed"
                    ),
                    SF_JSON_DATA_KEY: payload.model_dump(),
                },
                default=str,
            )
        except Exception as e:
            logger.exception(ERR_LOG, "upload_file_to_salesforce", e)
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        path="/tools/salesforce/attach_file_to_record",
        short_description="Link a Salesforce file to a record",
        description=(
            "Creates a ContentDocumentLink between a content_document_id and a record_id, making the "
            "file visible on the record's Files / Notes & Attachments related list. Usable after "
            "upload_file_to_salesforce or on its own for existing Salesforce files. Call once per target "
            "record; the same content_document_id can be linked to multiple records without re-upload. "
            "The file must already exist in Salesforce — use upload_file_to_salesforce first if needed."
        ),
        parameters=[
            ToolParameter(name="content_document_id", type=ParameterType.STRING, description="ContentDocumentId of the file to attach (15/18-char ID starting with '069')", required=True),
            ToolParameter(name="record_id", type=ParameterType.STRING, description="Salesforce record ID to attach the file to (15 or 18 chars)", required=True),
            ToolParameter(name="share_type", type=ParameterType.STRING, description="ContentDocumentLink ShareType: 'V' (Viewer), 'C' (Collaborator), or 'I' (Inferred). Defaults to 'V'.", required=False),
            ToolParameter(name="visibility", type=ParameterType.STRING, description="ContentDocumentLink Visibility: 'AllUsers' or 'InternalUsers'. Defaults to 'AllUsers'.", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def attach_file_to_record(
        self,
        content_document_id: str,
        record_id: str,
        share_type: str = "V",
        visibility: str = "AllUsers",
    ) -> tuple[bool, str]:
        """Create a ContentDocumentLink between an existing file and a record."""
        try:
            link_payload = ContentDocumentLinkCreatePayload(
                ContentDocumentId=content_document_id,
                LinkedEntityId=record_id,
                ShareType=share_type,
                Visibility=visibility,
            )

            link_response = await self.client.sobject_create(
                api_version=self.api_version,
                sobject=SF_SOBJECT_CONTENT_DOCUMENT_LINK,
                data=link_payload.model_dump(),
            )
            if not link_response.success:
                return False, json.dumps({SF_JSON_ERROR_KEY: (
                    link_response.error
                    or "Failed to create ContentDocumentLink"
                ), "content_document_id": content_document_id,
                   "record_id": record_id})

            link_data = link_response.data or {}
            link_id = link_data.get("id") or link_data.get("Id")

            payload = AttachFileToRecordData(
                content_document_link_id=(
                    str(link_id) if link_id is not None else None
                ),
                content_document_id=content_document_id,
                linked_record_id=record_id,
                share_type=share_type,
                visibility=visibility,
                weburl_linked_record_id=self._build_web_url(record_id),
            )
            return True, json.dumps(
                {
                    SF_JSON_MESSAGE_KEY: MSG_SUCCESS,
                    SF_JSON_DATA_KEY: payload.model_dump(exclude_none=True),
                },
                default=str,
            )
        except Exception as e:
            logger.error(ERR_LOG, "attach_file_to_record", e)
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # Object Metadata
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/describe_object",
        short_description="Get Salesforce object metadata (fields, types, picklists)",
        description=(
            "Returns the full Salesforce describe payload for an sObject: fields, types, relationships, "
            "and picklist metadata. Inactive picklist entries are filtered out so only assignable values "
            "remain. Use before create_record or update_record to discover field API names, types, or "
            "valid picklist values. For querying actual data use soql_query or search tools."
        ),
        parameters=[
            ToolParameter(name="sobject", type=ParameterType.STRING, description="The Salesforce object API name to describe (e.g., 'Account', 'Opportunity')", required=True),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="read")],
    )
    async def describe_object(self, sobject: str) -> tuple[bool, str]:
        """Describe a Salesforce object's metadata."""
        try:
            logger.info(
                "salesforce.describe_object called: sobject=%s",
                sobject,
            )
            response = await self.client.s_object_describe(
                sobject_api_name=sobject, version=self.api_version
            )
            if response.success:
                data = response.data
                if not isinstance(data, dict):
                    return False, json.dumps(
                        {SF_JSON_ERROR_KEY: "Unexpected describe response format"}
                    )
                for field in data.get("fields") or []:
                    if not isinstance(field, dict):
                        continue
                    pv = field.get("picklistValues")
                    if isinstance(pv, list):
                        field["picklistValues"] = [
                            v
                            for v in pv
                            if isinstance(v, dict) and v.get("active", False)
                        ]
                return True, json.dumps(
                    {
                        SF_JSON_MESSAGE_KEY: MSG_SUCCESS,
                        SF_JSON_DATA_KEY: data,
                    },
                    default=str,
                )
            if response.success and response.data is not None:
                # Data is not a dict — unexpected shape
                return self._error_response("Unexpected describe response format")
            return self._handle_response(
                response, MSG_SUCCESS
            )
        except Exception as e:
            logger.error(ERR_LOG, "describe_object", e)
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # Recent Records
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/list_recent_records",
        short_description="List recently viewed records of a Salesforce object",
        description=(
            "Lists the most recently viewed or modified records for a given Salesforce object type. "
            "Useful for a quick overview without constructing a SOQL query. For records matching "
            "specific criteria use search tools or soql_query; for a known record ID use get_record."
        ),
        parameters=[
            ToolParameter(name="sobject", type=ParameterType.STRING, description="The Salesforce object API name (e.g., 'Account', 'Contact')", required=True),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Maximum number of recent records to return (default 10, max 50)", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="read")],
    )
    async def list_recent_records(
        self, sobject: str, limit: int = 10
    ) -> tuple[bool, str]:
        """List recent records of a Salesforce object."""
        try:
            logger.info(
                "salesforce.list_recent_records called: sobject=%s, limit=%d", sobject, limit
            )
            sobject = self._validate_api_name(sobject)
            limit = int(limit)
            display_field = self._get_recent_record_display_field(sobject)
            query = SOQL_LIST_RECENT_RECORDS.format(
                sobject=sobject, display_field=display_field, limit=limit
            )
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(
                response, MSG_SUCCESS
            )
        except Exception as e:
            logger.error(ERR_LOG, "list_recent_records", e)
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # Account Tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/search_accounts",
        short_description="Search for Salesforce accounts",
        description=(
            "Searches Salesforce Account records by name and/or industry. Returns key fields: "
            "Id, Name, Industry, Phone, Website, Type, BillingCity, BillingState. "
            "For complex queries with many conditions use soql_query; for cross-object search use sosl_search."
        ),
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Account name to search for (partial match supported)", required=False),
            ToolParameter(name="industry", type=ParameterType.STRING, description="Filter by industry", required=False),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Maximum number of results (default 10, max 50)", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="search")],
    )
    async def search_accounts(
        self,
        name: str | None = None,
        industry: str | None = None,
        limit: int = 10,
    ) -> tuple[bool, str]:
        """Search for Salesforce accounts."""
        try:
            conditions: list[str] = []
            if name:
                conditions.append(f"Name LIKE '%{self._sanitize_soql_value(name)}%'")
            if industry:
                conditions.append(f"Industry = '{self._sanitize_soql_value(industry)}'")
            where = self._build_soql_conditions(conditions)
            query = SOQL_SEARCH_ACCOUNTS.format(where=where, limit=limit)
            logger.info("salesforce.search_accounts query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "search_accounts", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/create_account",
        short_description="Create a new Salesforce account",
        description=(
            "Creates a new Account in Salesforce. Only Name is required. Optionally provide Industry, "
            "Phone, Website, Description, and billing address fields. Do not ask the user for optional "
            "fields they did not provide. To update an existing account use update_record; for Contacts, "
            "Leads, or Opportunities use their dedicated create tools."
        ),
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Account name (required)", required=True),
            ToolParameter(name="industry", type=ParameterType.STRING, description="Industry", required=False),
            ToolParameter(name="phone", type=ParameterType.STRING, description="Phone number", required=False),
            ToolParameter(name="website", type=ParameterType.STRING, description="Website URL", required=False),
            ToolParameter(name="description", type=ParameterType.STRING, description="Account description", required=False),
            ToolParameter(name="billing_city", type=ParameterType.STRING, description="Billing city", required=False),
            ToolParameter(name="billing_state", type=ParameterType.STRING, description="Billing state/province", required=False),
            ToolParameter(name="billing_country", type=ParameterType.STRING, description="Billing country", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def create_account(
        self,
        name: str,
        industry: str | None = None,
        phone: str | None = None,
        website: str | None = None,
        description: str | None = None,
        billing_city: str | None = None,
        billing_state: str | None = None,
        billing_country: str | None = None,
    ) -> tuple[bool, str]:
        """Create a new Salesforce account."""
        try:
            account = AccountData(
                name=name,
                industry=industry,
                phone=phone,
                website=website,
                description=description,
                billing_city=billing_city,
                billing_state=billing_state,
                billing_country=billing_country,
            )

            logger.info("salesforce.create_account called: name=%s", name)
            response = await self.client.sobject_create(
                api_version=self.api_version,
                sobject=SF_SOBJECT_ACCOUNT,
                data=account.model_dump(by_alias=True, exclude_none=True),
            )
            return self._handle_response(
                response, MSG_SUCCESS, sobject=SF_SOBJECT_ACCOUNT
            )
        except Exception as e:
            logger.error(ERR_LOG, "create_account", e)
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # Contact Tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/search_contacts",
        short_description="Search for Salesforce contacts",
        description=(
            "Searches Salesforce Contact records by name, email, or parent account. Returns key fields: "
            "Id, Name, Email, Phone, Title, Account.Name. For complex queries use soql_query; "
            "for Leads use search_leads instead."
        ),
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Contact name to search for (partial match supported)", required=False),
            ToolParameter(name="email", type=ParameterType.STRING, description="Filter by email address", required=False),
            ToolParameter(name="account_id", type=ParameterType.STRING, description="Filter by parent Account ID", required=False),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Maximum number of results (default 10, max 50)", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="search")],
    )
    async def search_contacts(
        self,
        name: str | None = None,
        email: str | None = None,
        account_id: str | None = None,
        limit: int = 10,
    ) -> tuple[bool, str]:
        """Search for Salesforce contacts."""
        try:
            conditions: list[str] = []
            if name:
                conditions.append(f"Name LIKE '%{self._sanitize_soql_value(name)}%'")
            if email:
                conditions.append(f"Email LIKE '%{self._sanitize_soql_value(email)}%'")
            if account_id:
                conditions.append(f"AccountId = '{self._sanitize_soql_value(account_id)}'")
            where = self._build_soql_conditions(conditions)
            query = SOQL_SEARCH_CONTACTS.format(where=where, limit=limit)
            logger.info("salesforce.search_contacts query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "search_contacts", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/create_contact",
        short_description="Create a new Salesforce contact",
        description=(
            "Creates a new Contact in Salesforce. LastName is required. Optionally provide FirstName, "
            "Email, Phone, Title, AccountId, and Department. Do not ask the user for optional fields "
            "they did not provide. For Leads use create_lead; to update an existing contact use update_record."
        ),
        parameters=[
            ToolParameter(name="last_name", type=ParameterType.STRING, description="Last name (required)", required=True),
            ToolParameter(name="first_name", type=ParameterType.STRING, description="First name", required=False),
            ToolParameter(name="email", type=ParameterType.STRING, description="Email address", required=False),
            ToolParameter(name="phone", type=ParameterType.STRING, description="Phone number", required=False),
            ToolParameter(name="title", type=ParameterType.STRING, description="Job title", required=False),
            ToolParameter(name="account_id", type=ParameterType.STRING, description="Parent Account ID to associate this contact with", required=False),
            ToolParameter(name="department", type=ParameterType.STRING, description="Department", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def create_contact(
        self,
        last_name: str,
        first_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        title: str | None = None,
        account_id: str | None = None,
        department: str | None = None,
    ) -> tuple[bool, str]:
        """Create a new Salesforce contact."""
        try:
            contact = ContactData(
                last_name=last_name,
                first_name=first_name,
                email=email,
                phone=phone,
                title=title,
                account_id=account_id,
                department=department,
            )

            logger.info("salesforce.create_contact called: last_name=%s", last_name)
            response = await self.client.sobject_create(
                api_version=self.api_version,
                sobject=SF_SOBJECT_CONTACT,
                data=contact.model_dump(by_alias=True, exclude_none=True),
            )
            return self._handle_response(
                response, MSG_SUCCESS, sobject=SF_SOBJECT_CONTACT
            )
        except Exception as e:
            logger.error(ERR_LOG, "create_contact", e)
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # Lead Tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/search_leads",
        short_description="Search for Salesforce leads",
        description=(
            "Searches Salesforce Lead records by name, company, or status. Returns key fields: "
            "Id, Name, Company, Email, Phone, Status, LeadSource. For existing Contacts use "
            "search_contacts; for complex queries use soql_query."
        ),
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Lead name to search for (partial match supported)", required=False),
            ToolParameter(name="company", type=ParameterType.STRING, description="Filter by company name", required=False),
            ToolParameter(name="status", type=ParameterType.STRING, description="Filter by lead status (e.g., 'Open - Not Contacted', 'Working - Contacted')", required=False),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Maximum number of results (default 10, max 50)", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="search")],
    )
    async def search_leads(
        self,
        name: str | None = None,
        company: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> tuple[bool, str]:
        """Search for Salesforce leads."""
        try:
            conditions: list[str] = []
            if name:
                conditions.append(f"Name LIKE '%{self._sanitize_soql_value(name)}%'")
            if company:
                conditions.append(f"Company LIKE '%{self._sanitize_soql_value(company)}%'")
            if status:
                conditions.append(f"Status = '{self._sanitize_soql_value(status)}'")
            where = self._build_soql_conditions(conditions)
            query = SOQL_SEARCH_LEADS.format(where=where, limit=limit)
            logger.info("salesforce.search_leads query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "search_leads", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/create_lead",
        short_description="Create a new Salesforce lead",
        description=(
            "Creates a new Lead in Salesforce. LastName and Company are required. Optionally provide "
            "FirstName, Email, Phone, Title, Status, and Industry. Do not ask the user for optional "
            "fields they did not provide. For Contacts use create_contact; to convert a lead use "
            "update_record to change its status."
        ),
        parameters=[
            ToolParameter(name="last_name", type=ParameterType.STRING, description="Last name (required)", required=True),
            ToolParameter(name="company", type=ParameterType.STRING, description="Company name (required)", required=True),
            ToolParameter(name="first_name", type=ParameterType.STRING, description="First name", required=False),
            ToolParameter(name="email", type=ParameterType.STRING, description="Email address", required=False),
            ToolParameter(name="phone", type=ParameterType.STRING, description="Phone number", required=False),
            ToolParameter(name="title", type=ParameterType.STRING, description="Job title", required=False),
            ToolParameter(name="status", type=ParameterType.STRING, description="Lead status (e.g., 'Open - Not Contacted')", required=False),
            ToolParameter(name="industry", type=ParameterType.STRING, description="Industry", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def create_lead(
        self,
        last_name: str,
        company: str,
        first_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        title: str | None = None,
        status: str | None = None,
        industry: str | None = None,
    ) -> tuple[bool, str]:
        """Create a new Salesforce lead."""
        try:
            lead = LeadData(
                last_name=last_name,
                company=company,
                first_name=first_name,
                email=email,
                phone=phone,
                title=title,
                status=status,
                industry=industry,
            )

            logger.info("salesforce.create_lead called: last_name=%s, company=%s", last_name, company)
            response = await self.client.sobject_create(
                api_version=self.api_version,
                sobject=SF_SOBJECT_LEAD,
                data=lead.model_dump(by_alias=True, exclude_none=True),
            )
            return self._handle_response(
                response, MSG_SUCCESS, sobject=SF_SOBJECT_LEAD
            )
        except Exception as e:
            logger.error(ERR_LOG, "create_lead", e)
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # Opportunity Tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/search_opportunities",
        short_description="Search for Salesforce opportunities",
        description=(
            "Searches Salesforce Opportunity records by name, stage, or parent account. Returns key fields: "
            "Id, Name, StageName, Amount, CloseDate, Probability, Account.Name. For complex queries with "
            "many conditions or aggregate pipeline data use soql_query."
        ),
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Opportunity name to search for (partial match supported)", required=False),
            ToolParameter(name="stage", type=ParameterType.STRING, description="Filter by stage name (e.g., 'Prospecting', 'Qualification', 'Closed Won')", required=False),
            ToolParameter(name="account_id", type=ParameterType.STRING, description="Filter by parent Account ID", required=False),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Maximum number of results (default 10, max 50)", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="search")],
    )
    async def search_opportunities(
        self,
        name: str | None = None,
        stage: str | None = None,
        account_id: str | None = None,
        limit: int = 10,
    ) -> tuple[bool, str]:
        """Search for Salesforce opportunities."""
        try:
            conditions: list[str] = []
            if name:
                conditions.append(f"Name LIKE '%{self._sanitize_soql_value(name)}%'")
            if stage:
                conditions.append(f"StageName = '{self._sanitize_soql_value(stage)}'")
            if account_id:
                conditions.append(f"AccountId = '{self._sanitize_soql_value(account_id)}'")
            where = self._build_soql_conditions(conditions)
            query = SOQL_SEARCH_OPPORTUNITIES.format(where=where, limit=limit)
            logger.info("salesforce.search_opportunities query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "search_opportunities", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/create_opportunity",
        short_description="Create a new Salesforce opportunity",
        description=(
            "Creates a new Opportunity in Salesforce. Name, StageName, and CloseDate are required. "
            "CloseDate must be in YYYY-MM-DD format. Optionally provide AccountId, Amount, Description, "
            "and Probability. Do not ask the user for optional fields they did not provide. "
            "To update an existing opportunity use update_record."
        ),
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Opportunity name (required)", required=True),
            ToolParameter(name="stage_name", type=ParameterType.STRING, description="Stage name (required, e.g., 'Prospecting', 'Qualification')", required=True),
            ToolParameter(name="close_date", type=ParameterType.STRING, description="Expected close date in YYYY-MM-DD format (required)", required=True),
            ToolParameter(name="account_id", type=ParameterType.STRING, description="Parent Account ID", required=False),
            ToolParameter(name="amount", type=ParameterType.FLOAT, description="Opportunity amount", required=False),
            ToolParameter(name="description", type=ParameterType.STRING, description="Opportunity description", required=False),
            ToolParameter(name="probability", type=ParameterType.FLOAT, description="Probability percentage (0-100)", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def create_opportunity(
        self,
        name: str,
        stage_name: str,
        close_date: str,
        account_id: str | None = None,
        amount: float | None = None,
        description: str | None = None,
        probability: float | None = None,
    ) -> tuple[bool, str]:
        """Create a new Salesforce opportunity."""
        try:
            opportunity = OpportunityData(
                name=name,
                stage_name=stage_name,
                close_date=close_date,
                account_id=account_id,
                amount=amount,
                description=description,
                probability=probability,
            )

            logger.info("salesforce.create_opportunity called: name=%s", name)
            response = await self.client.sobject_create(
                api_version=self.api_version,
                sobject=SF_SOBJECT_OPPORTUNITY,
                data=opportunity.model_dump(by_alias=True, exclude_none=True),
            )
            return self._handle_response(
                response,
                MSG_SUCCESS,
                sobject=SF_SOBJECT_OPPORTUNITY,
            )
        except Exception as e:
            logger.error(ERR_LOG, "create_opportunity", e)
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # Case Tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/search_cases",
        short_description="Search for Salesforce cases",
        description=(
            "Searches Salesforce Case records by subject, status, priority, or parent account. "
            "Returns key fields: Id, CaseNumber, Subject, Status, Priority, Origin, Account.Name. "
            "For complex queries with many conditions use soql_query; for cross-object search use sosl_search."
        ),
        parameters=[
            ToolParameter(name="subject", type=ParameterType.STRING, description="Case subject to search for (partial match supported)", required=False),
            ToolParameter(name="status", type=ParameterType.STRING, description="Filter by case status (e.g., 'New', 'Working', 'Escalated', 'Closed')", required=False),
            ToolParameter(name="priority", type=ParameterType.STRING, description="Filter by priority (e.g., 'High', 'Medium', 'Low')", required=False),
            ToolParameter(name="account_id", type=ParameterType.STRING, description="Filter by parent Account ID", required=False),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Maximum number of results (default 10, max 50)", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="search")],
    )
    async def search_cases(
        self,
        subject: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        account_id: str | None = None,
        limit: int = 10,
    ) -> tuple[bool, str]:
        """Search for Salesforce cases."""
        try:
            conditions: list[str] = []
            if subject:
                conditions.append(f"Subject LIKE '%{self._sanitize_soql_value(subject)}%'")
            if status:
                conditions.append(f"Status = '{self._sanitize_soql_value(status)}'")
            if priority:
                conditions.append(f"Priority = '{self._sanitize_soql_value(priority)}'")
            if account_id:
                conditions.append(f"AccountId = '{self._sanitize_soql_value(account_id)}'")
            where = self._build_soql_conditions(conditions)
            query = SOQL_SEARCH_CASES.format(where=where, limit=limit)
            logger.info("salesforce.search_cases query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "search_cases", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/create_case",
        short_description="Create a new Salesforce case",
        description=(
            "Creates a new Case (support ticket) in Salesforce. Subject is required. Optionally provide "
            "Status, Priority, Origin, Description, AccountId, and ContactId. Do not ask the user for "
            "optional fields they did not provide. To update or close an existing case use update_record."
        ),
        parameters=[
            ToolParameter(name="subject", type=ParameterType.STRING, description="Case subject (required)", required=True),
            ToolParameter(name="status", type=ParameterType.STRING, description="Case status (e.g., 'New', 'Working', 'Escalated')", required=False),
            ToolParameter(name="priority", type=ParameterType.STRING, description="Priority (e.g., 'High', 'Medium', 'Low')", required=False),
            ToolParameter(name="origin", type=ParameterType.STRING, description="Case origin (e.g., 'Phone', 'Email', 'Web')", required=False),
            ToolParameter(name="description", type=ParameterType.STRING, description="Case description", required=False),
            ToolParameter(name="account_id", type=ParameterType.STRING, description="Parent Account ID", required=False),
            ToolParameter(name="contact_id", type=ParameterType.STRING, description="Associated Contact ID", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def create_case(
        self,
        subject: str,
        status: str | None = None,
        priority: str | None = None,
        origin: str | None = None,
        description: str | None = None,
        account_id: str | None = None,
        contact_id: str | None = None,
    ) -> tuple[bool, str]:
        """Create a new Salesforce case."""
        try:
            case = CaseData(
                subject=subject,
                status=status,
                priority=priority,
                origin=origin,
                description=description,
                account_id=account_id,
                contact_id=contact_id,
            )

            logger.info("salesforce.create_case called: subject=%s", subject)
            response = await self.client.sobject_create(
                api_version=self.api_version,
                sobject=SF_SOBJECT_CASE,
                data=case.model_dump(by_alias=True, exclude_none=True),
            )
            return self._handle_response(
                response, MSG_SUCCESS, sobject=SF_SOBJECT_CASE
            )
        except Exception as e:
            logger.error(ERR_LOG, "create_case", e)
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # Product Tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/search_products",
        short_description="Search for Salesforce products",
        description=(
            "Searches Salesforce Product2 records by name, product code, or family. Returns key fields: "
            "Id, Name, ProductCode, Description, Family, IsActive, QuantityUnitOfMeasure. By default "
            "only active products are returned. For line items on an opportunity use soql_query on "
            "OpportunityLineItem; for complex queries use soql_query."
        ),
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Product name to search for (partial match supported)", required=False),
            ToolParameter(name="product_code", type=ParameterType.STRING, description="Filter by product code (SKU)", required=False),
            ToolParameter(name="family", type=ParameterType.STRING, description="Filter by product family", required=False),
            ToolParameter(name="active_only", type=ParameterType.BOOLEAN, description="Only return active products. Default True.", required=False),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Maximum number of results (default 10, max 50)", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="search")],
    )
    async def search_products(
        self,
        name: str | None = None,
        product_code: str | None = None,
        family: str | None = None,
        active_only: bool = True,
        limit: int = 10,
    ) -> tuple[bool, str]:
        """Search for Salesforce products."""
        try:
            conditions: list[str] = []
            if name:
                conditions.append(f"Name LIKE '%{self._sanitize_soql_value(name)}%'")
            if product_code:
                conditions.append(f"ProductCode LIKE '%{self._sanitize_soql_value(product_code)}%'")
            if family:
                conditions.append(f"Family = '{self._sanitize_soql_value(family)}'")
            if active_only:
                conditions.append("IsActive = true")
            where = self._build_soql_conditions(conditions)
            query = SOQL_SEARCH_PRODUCTS.format(where=where, limit=limit)
            logger.info("salesforce.search_products query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "search_products", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/create_product",
        short_description="Create a new Salesforce product",
        description=(
            "Creates a new Product2 record in Salesforce. Only Name is required. Optionally provide "
            "ProductCode, Description, Family, IsActive, and QuantityUnitOfMeasure. To make the product "
            "sellable on opportunities, also create a PricebookEntry. Do not ask the user for optional "
            "fields they did not provide. To add an existing product to an opportunity use "
            "add_product_to_opportunity; to update an existing product use update_record."
        ),
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Product name (required)", required=True),
            ToolParameter(name="product_code", type=ParameterType.STRING, description="Product code / SKU", required=False),
            ToolParameter(name="description", type=ParameterType.STRING, description="Product description", required=False),
            ToolParameter(name="family", type=ParameterType.STRING, description="Product family", required=False),
            ToolParameter(name="is_active", type=ParameterType.BOOLEAN, description="Whether the product is active. Default True.", required=False),
            ToolParameter(name="quantity_unit_of_measure", type=ParameterType.STRING, description="Unit of measure (e.g., 'Each', 'Hours', 'Kg')", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def create_product(
        self,
        name: str,
        product_code: str | None = None,
        description: str | None = None,
        family: str | None = None,
        is_active: bool = True,
        quantity_unit_of_measure: str | None = None,
    ) -> tuple[bool, str]:
        """Create a new Salesforce product."""
        try:
            product = ProductData(
                name=name,
                product_code=product_code,
                description=description,
                family=family,
                is_active=is_active,
                quantity_unit_of_measure=quantity_unit_of_measure,
            )

            logger.info("salesforce.create_product called: name=%s", name)
            response = await self.client.sobject_create(
                api_version=self.api_version,
                sobject=SF_SOBJECT_PRODUCT2,
                data=product.model_dump(by_alias=True, exclude_none=True),
            )
            return self._handle_response(
                response, MSG_SUCCESS, sobject=SF_SOBJECT_PRODUCT2
            )
        except Exception as e:
            logger.error(ERR_LOG, "create_product", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/list_pricebooks",
        short_description="List Salesforce price books (Pricebook2)",
        description=(
            "Lists Salesforce Pricebook2 records. Returns key fields: Id, Name, Description, IsActive, "
            "IsStandard. By default returns all price books (active and inactive). Use to find a "
            "Pricebook Id needed for adding products to opportunities. For product entries within a "
            "pricebook use soql_query on PricebookEntry."
        ),
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Price book name to search for (partial match supported)", required=False),
            ToolParameter(name="active_only", type=ParameterType.BOOLEAN, description="Only return active price books. Default False.", required=False),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Maximum number of results (default 20, max 200)", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="search")],
    )
    async def list_pricebooks(
        self,
        name: str | None = None,
        active_only: bool = False,
        limit: int = 20,
    ) -> tuple[bool, str]:
        """List Salesforce price books."""
        try:
            conditions: list[str] = []
            if name:
                conditions.append(f"Name LIKE '%{self._sanitize_soql_value(name)}%'")
            if active_only:
                conditions.append("IsActive = true")
            where = self._build_soql_conditions(conditions)
            query = SOQL_LIST_PRICEBOOKS.format(where=where, limit=limit)
            logger.info("salesforce.list_pricebooks query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "list_pricebooks", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/create_pricebook_entry",
        short_description="Create a Salesforce pricebook entry",
        description=(
            "Creates a PricebookEntry in Salesforce to set a product's unit price within a specific "
            "pricebook. Required before a product can be sold from that pricebook. Provide product_id, "
            "pricebook_id, and unit_price. To create a new product use create_product; to add a product "
            "directly to an opportunity use add_product_to_opportunity."
        ),
        parameters=[
            ToolParameter(name="product_id", type=ParameterType.STRING, description="The Product2 Id to add to the pricebook", required=True),
            ToolParameter(name="pricebook_id", type=ParameterType.STRING, description="The Pricebook2 Id where the product should be listed", required=True),
            ToolParameter(name="unit_price", type=ParameterType.FLOAT, description="Unit price for the product in this pricebook", required=True),
            ToolParameter(name="is_active", type=ParameterType.BOOLEAN, description="Whether the pricebook entry is active. Default True.", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def create_pricebook_entry(
        self,
        product_id: str,
        pricebook_id: str,
        unit_price: float,
        is_active: bool = True,
    ) -> tuple[bool, str]:
        """Create a Salesforce PricebookEntry."""
        try:
            response = await self.client.sobject_create(
                api_version=self.api_version,
                sobject=SF_SOBJECT_PRICEBOOK_ENTRY,
                data={
                    "Product2Id": product_id,
                    "Pricebook2Id": pricebook_id,
                    "UnitPrice": unit_price,
                    "IsActive": is_active,
                },
            )
            return self._handle_response(
                response, MSG_SUCCESS, sobject=SF_SOBJECT_PRICEBOOK_ENTRY
            )
        except Exception as e:
            logger.error(ERR_LOG, "create_pricebook_entry", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/add_product_to_opportunity",
        short_description="Add a product line item to a Salesforce opportunity",
        description=(
            "Adds a product to a Salesforce Opportunity by creating an OpportunityLineItem. Provide "
            "opportunity_id and either pricebook_entry_id directly, or product_id (the tool looks up "
            "the PricebookEntry on the opportunity's pricebook). Quantity defaults to 1; if unit_price "
            "is omitted the PricebookEntry's UnitPrice is used. The opportunity must already have a "
            "Pricebook2Id set. To create a new product use create_product; to update an existing line "
            "item use update_record on OpportunityLineItem."
        ),
        parameters=[
            ToolParameter(name="opportunity_id", type=ParameterType.STRING, description="The Salesforce Opportunity Id", required=True),
            ToolParameter(name="pricebook_entry_id", type=ParameterType.STRING, description="The PricebookEntry Id for the product. If omitted, provide product_id.", required=False),
            ToolParameter(name="product_id", type=ParameterType.STRING, description="The Product2 Id. Used to look up the PricebookEntry if pricebook_entry_id is not provided.", required=False),
            ToolParameter(name="pricebook_id", type=ParameterType.STRING, description="The Pricebook2 Id for PricebookEntry lookup. If omitted, the opportunity's pricebook is used.", required=False),
            ToolParameter(name="quantity", type=ParameterType.FLOAT, description="Quantity of the product (default 1)", required=False),
            ToolParameter(name="unit_price", type=ParameterType.FLOAT, description="Unit sales price. If omitted, the PricebookEntry UnitPrice is used.", required=False),
            ToolParameter(name="description", type=ParameterType.STRING, description="Optional line item description", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def add_product_to_opportunity(
        self,
        opportunity_id: str,
        pricebook_entry_id: str | None = None,
        product_id: str | None = None,
        pricebook_id: str | None = None,
        quantity: float = 1,
        unit_price: float | None = None,
        description: str | None = None,
    ) -> tuple[bool, str]:
        """Add a product line item to a Salesforce opportunity."""
        try:
            if not pricebook_entry_id and not product_id:
                return self._error_response(ERR_NEED_PBE_OR_PRODUCT)

            resolved_unit_price = unit_price

            # Look up PricebookEntry if not directly provided
            if not pricebook_entry_id:
                effective_pricebook_id = pricebook_id
                if not effective_pricebook_id:
                    opp_query = SOQL_OPPORTUNITY_PRICEBOOK2_BY_ID.format(
                        opportunity_id=self._sanitize_soql_value(opportunity_id),
                    )
                    opp_resp = await self.client.soql_query(
                        api_version=self.api_version, q=opp_query
                    )
                    try:
                        opp_data = opp_resp.data if hasattr(opp_resp, "data") else opp_resp
                        records = (opp_data or {}).get(SF_KEY_RECORDS, [])
                        if records:
                            effective_pricebook_id = records[0].get(SF_FIELD_PRICEBOOK2_ID)
                    except Exception as e:
                        logger.warning(ERR_LOG, "add_product_to_opportunity opp pricebook lookup", e)
                        effective_pricebook_id = None
                    if not effective_pricebook_id:
                        return self._error_response(ERR_OPP_NO_PRICEBOOK)

                pbe_query = SOQL_PRICEBOOK_ENTRY_BY_PRODUCT_AND_BOOK.format(
                    product_id=self._sanitize_soql_value(product_id),
                    pricebook_id=self._sanitize_soql_value(effective_pricebook_id),
                )
                logger.info("salesforce.add_product_to_opportunity pbe lookup: %s", pbe_query)
                pbe_resp = await self.client.soql_query(
                    api_version=self.api_version, q=pbe_query
                )
                try:
                    pbe_data = pbe_resp.data if hasattr(pbe_resp, "data") else pbe_resp
                    pbe_records = (pbe_data or {}).get(SF_KEY_RECORDS, [])
                except Exception as e:
                    logger.warning(ERR_LOG, "add_product_to_opportunity pricebook entry lookup", e)
                    pbe_records = []
                if not pbe_records:
                    return self._error_response(ERR_NO_ACTIVE_PBE)
                pricebook_entry_id = pbe_records[0].get(SF_KEY_ID)
                if resolved_unit_price is None:
                    resolved_unit_price = pbe_records[0].get(SF_FIELD_UNIT_PRICE)

            line_item = OpportunityLineItemData(
                opportunity_id=opportunity_id,
                pricebook_entry_id=pricebook_entry_id,
                quantity=quantity,
                unit_price=resolved_unit_price,
                description=description,
            )

            logger.info(
                "salesforce.add_product_to_opportunity called: opp=%s pbe=%s qty=%s",
                opportunity_id, pricebook_entry_id, quantity,
            )
            response = await self.client.sobject_create(
                api_version=self.api_version,
                sobject=SF_SOBJECT_OPPORTUNITY_LINE_ITEM,
                data=line_item.model_dump(by_alias=True, exclude_none=True),
            )
            return self._handle_response(
                response,
                MSG_SUCCESS,
                sobject=SF_SOBJECT_OPPORTUNITY_LINE_ITEM,
            )
        except Exception as e:
            logger.error(ERR_LOG, "add_product_to_opportunity", e)
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # Task Tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/search_tasks",
        short_description="Search for Salesforce tasks",
        description=(
            "Searches Salesforce Task records by subject, status, priority, owner, or related record. "
            "Returns key fields: Id, Subject, Status, Priority, ActivityDate, Owner.Name, What.Name, Who.Name. "
            "WhatId is the related record (Account, Opportunity, Case, etc.); WhoId is the related Contact or Lead. "
            "For events/calendar items or complex queries use soql_query."
        ),
        parameters=[
            ToolParameter(name="subject", type=ParameterType.STRING, description="Task subject to search for (partial match supported)", required=False),
            ToolParameter(name="status", type=ParameterType.STRING, description="Filter by task status (e.g., 'Not Started', 'In Progress', 'Completed')", required=False),
            ToolParameter(name="priority", type=ParameterType.STRING, description="Filter by priority (e.g., 'High', 'Normal', 'Low')", required=False),
            ToolParameter(name="owner_id", type=ParameterType.STRING, description="Filter by task owner User ID", required=False),
            ToolParameter(name="what_id", type=ParameterType.STRING, description="Filter by related record ID (Account, Opportunity, Case, etc.)", required=False),
            ToolParameter(name="who_id", type=ParameterType.STRING, description="Filter by related Contact or Lead ID", required=False),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Maximum number of results (default 10, max 50)", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="search")],
    )
    async def search_tasks(
        self,
        subject: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        owner_id: str | None = None,
        what_id: str | None = None,
        who_id: str | None = None,
        limit: int = 10,
    ) -> tuple[bool, str]:
        """Search for Salesforce tasks."""
        try:
            conditions: list[str] = []
            if subject:
                conditions.append(f"Subject LIKE '%{self._sanitize_soql_value(subject)}%'")
            if status:
                conditions.append(f"Status = '{self._sanitize_soql_value(status)}'")
            if priority:
                conditions.append(f"Priority = '{self._sanitize_soql_value(priority)}'")
            if owner_id:
                conditions.append(f"OwnerId = '{self._sanitize_soql_value(owner_id)}'")
            if what_id:
                conditions.append(f"WhatId = '{self._sanitize_soql_value(what_id)}'")
            if who_id:
                conditions.append(f"WhoId = '{self._sanitize_soql_value(who_id)}'")
            where = self._build_soql_conditions(conditions)
            query = SOQL_SEARCH_TASKS.format(where=where, limit=limit)
            logger.info("salesforce.search_tasks query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "search_tasks", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/create_task",
        short_description="Create a new Salesforce task",
        description=(
            "Creates a new Task in Salesforce. Subject is required. Use what_id to relate the task to "
            "an Account/Opportunity/Case and who_id to relate to a Contact/Lead. activity_date must be "
            "in YYYY-MM-DD format. owner_id defaults to the authenticated user when omitted. Do not ask "
            "the user for optional fields they did not provide. For calendar events use create_record on "
            "Event; to update an existing task use update_record."
        ),
        parameters=[
            ToolParameter(name="subject", type=ParameterType.STRING, description="Task subject (required)", required=True),
            ToolParameter(name="status", type=ParameterType.STRING, description="Task status (e.g., 'Not Started', 'In Progress', 'Completed')", required=False),
            ToolParameter(name="priority", type=ParameterType.STRING, description="Priority (e.g., 'High', 'Normal', 'Low')", required=False),
            ToolParameter(name="activity_date", type=ParameterType.STRING, description="Due date in YYYY-MM-DD format", required=False),
            ToolParameter(name="description", type=ParameterType.STRING, description="Task description / comments", required=False),
            ToolParameter(name="owner_id", type=ParameterType.STRING, description="User ID of the task owner. Defaults to the authenticated user.", required=False),
            ToolParameter(name="what_id", type=ParameterType.STRING, description="Related record ID (Account, Opportunity, Case, etc.)", required=False),
            ToolParameter(name="who_id", type=ParameterType.STRING, description="Related Contact or Lead ID", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def create_task(
        self,
        subject: str,
        status: str | None = None,
        priority: str | None = None,
        activity_date: str | None = None,
        description: str | None = None,
        owner_id: str | None = None,
        what_id: str | None = None,
        who_id: str | None = None,
    ) -> tuple[bool, str]:
        """Create a new Salesforce task."""
        try:
            task = TaskData(
                subject=subject,
                status=status,
                priority=priority,
                activity_date=activity_date,
                description=description,
                owner_id=owner_id,
                what_id=what_id,
                who_id=who_id,
            )

            logger.info("salesforce.create_task called: subject=%s", subject)
            response = await self.client.sobject_create(
                api_version=self.api_version,
                sobject=SF_SOBJECT_TASK,
                data=task.model_dump(by_alias=True, exclude_none=True),
            )
            return self._handle_response(
                response, MSG_SUCCESS, sobject=SF_SOBJECT_TASK
            )
        except Exception as e:
            logger.error(ERR_LOG, "create_task", e)
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # Chatter
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/get_record_chatter",
        short_description="Get the Chatter feed for a Salesforce record",
        description=(
            "Returns the Chatter feed elements (posts, comments, updates) for any Salesforce record by ID — "
            "Account, Opportunity, Case, Contact, Lead, or any sObject. Use to summarize discussions, "
            "recent activity, or comments on a record. If you only have a name, search for the record "
            "first to get the ID. For the record's field data use get_record or search tools; for a "
            "cross-record news feed use soql_query on FeedItem."
        ),
        parameters=[
            ToolParameter(name="record_id", type=ParameterType.STRING, description="The Salesforce record ID whose Chatter feed should be fetched", required=True),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="read")],
    )
    async def get_record_chatter(self, record_id: str) -> tuple[bool, str]:
        """Fetch the Chatter feed for a Salesforce record."""
        try:
            logger.info("salesforce.get_record_chatter called: record_id=%s", record_id)
            response = await self.client.record_feed_elements(
                record_group_id=record_id, version=self.api_version
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "get_record_chatter", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/post_chatter_comment",
        short_description="Post a comment/reply on a Chatter feed item",
        description=(
            "Adds a comment (reply) to an existing Chatter FeedElement. Provide the feed element ID "
            "(starts with '0D5', obtained from get_record_chatter) and the comment text. Use when "
            "replying to a specific Chatter post. For creating a new top-level post use "
            "post_chatter_to_record; for reading the feed use get_record_chatter."
        ),
        parameters=[
            ToolParameter(name="feed_element_id", type=ParameterType.STRING, description="The Chatter FeedElement ID to reply to (starts with '0D5')", required=True),
            ToolParameter(name="text", type=ParameterType.STRING, description="The comment text to post. When is_rich_text=True, supports markdown: **bold**, *italic*, [label](url).", required=True),
            ToolParameter(name="is_rich_text", type=ParameterType.BOOLEAN, description="If True, text is parsed as markdown and posted as rich text. Default False.", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def post_chatter_comment(
        self, feed_element_id: str, text: str, is_rich_text: bool = False
    ) -> tuple[bool, str]:
        """Post a comment on a Chatter feed item."""
        try:
            logger.info(
                "salesforce.post_chatter_comment called: feed_element_id=%s, rich=%s",
                feed_element_id, is_rich_text,
            )
            segments = (
                [s.model_dump(by_alias=True) for s in self._markdown_to_chatter_segments(text)]
                if is_rich_text else None
            )
            response = await self.client.feed_elements_capability_comments_items(
                feed_element_id=feed_element_id,
                version=self.api_version,
                text=text,
                message_segments=segments,
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "post_chatter_comment", e)
            return self._error_response(str(e))

    @tool(
        path="/tools/salesforce/post_chatter_to_record",
        short_description="Create a new Chatter post on a Salesforce record",
        description=(
            "Creates a new top-level Chatter FeedItem on any record (Account, Opportunity, Case, etc.). "
            "Provide the record ID and the post text. Use when starting a new Chatter discussion or "
            "posting an update. To reply to an existing post use post_chatter_comment; to read existing "
            "posts use get_record_chatter."
        ),
        parameters=[
            ToolParameter(name="record_id", type=ParameterType.STRING, description="The Salesforce record ID to post to", required=True),
            ToolParameter(name="text", type=ParameterType.STRING, description="The post text. When is_rich_text=True, supports markdown: **bold**, *italic*, [label](url).", required=True),
            ToolParameter(name="is_rich_text", type=ParameterType.BOOLEAN, description="If True, text is parsed as markdown and posted as rich text. Default False.", required=False),
        ],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="write")],
    )
    async def post_chatter_to_record(
        self, record_id: str, text: str, is_rich_text: bool = False
    ) -> tuple[bool, str]:
        """Create a new Chatter post on a Salesforce record."""
        try:
            logger.info(
                "salesforce.post_chatter_to_record called: record_id=%s, rich=%s",
                record_id, is_rich_text,
            )
            segments = (
                [s.model_dump(by_alias=True) for s in self._markdown_to_chatter_segments(text)]
                if is_rich_text else None
            )
            response = await self.client.feed_elements_post_and_search(
                version=self.api_version,
                feedelementtype=SF_CHATTER_FEED_ELEMENT_TYPE,
                subjectid=record_id,
                text=text,
                message_segments=segments,
            )
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "post_chatter_to_record", e)
            return self._error_response(str(e))

    # ------------------------------------------------------------------
    # User Info
    # ------------------------------------------------------------------

    @tool(
        path="/tools/salesforce/get_current_user",
        short_description="Get the current authenticated Salesforce user's info",
        description=(
            "Returns information about the currently authenticated Salesforce user, including their "
            "name, email, profile, and organization details. Use when the user wants to know who is "
            "logged in or needs their Salesforce user ID. For other Salesforce users query the User "
            "object with soql_query."
        ),
        parameters=[],
        tags=[Tag(key="category", value="crm"), Tag(key="type", value="read")],
    )
    async def get_current_user(self) -> tuple[bool, str]:
        """Get the current authenticated user's info."""
        try:
            logger.info("salesforce.get_current_user called")
            response = await self.client.get_user_info()
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "get_current_user", e)
            return self._error_response(str(e))
