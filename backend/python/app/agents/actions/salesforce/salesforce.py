import json
import logging
import re
from typing import Any

from app.agents.actions.salesforce.salesforce_models import (
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
    CreateProductInput,
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
    SF_JSON_DATA_KEY,
    SF_JSON_ERROR_KEY,
    SF_JSON_MESSAGE_KEY,
    SF_SOBJECT_ACCOUNT,
    SF_SOBJECT_CASE,
    SF_SOBJECT_CONTACT,
    SF_SOBJECT_LEAD,
    SF_SOBJECT_OPPORTUNITY,
    SF_SOBJECT_OPPORTUNITY_LINE_ITEM,
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
)
from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import AuthField, CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.sources.client.salesforce.salesforce import SalesforceClient, SalesforceResponse
from app.sources.external.salesforce.salesforce_data_source import SalesforceDataSource

_SF_KEY_ID_SET = frozenset(SF_KEY_ID_LIST)
logger = logging.getLogger(__name__)

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
            icon_path="/assets/icons/connectors/salesforce.svg",
            app_group="CRM",
            app_description="Salesforce OAuth application for agent integration",
        ),
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/salesforce.svg"))\
    .build_decorator()
class Salesforce:
    """Salesforce CRM tools exposed to agents using SalesforceDataSource."""

    def __init__(self, client: SalesforceClient) -> None:
        self.client = SalesforceDataSource(client)
        self.api_version = DEFAULT_API_VERSION
        self.instance_url = (client.get_base_url() or "").rstrip("/")

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
                {SF_JSON_MESSAGE_KEY: success_message, SF_JSON_DATA_KEY: data},
                default=str,
            )
        error = response.error or MSG_UNKNOWN_ERROR
        return False, json.dumps({SF_JSON_ERROR_KEY: error})

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
        app_name="salesforce",
        tool_name="soql_query",
        description="Execute a SOQL query against Salesforce",
        llm_description=(
            "Executes a Salesforce Object Query Language (SOQL) query. Use this for structured queries against "
            "specific objects and fields. Examples: 'SELECT Id, Name FROM Account WHERE Industry = \\'Technology\\' LIMIT 10', "
            "'SELECT Id, Subject, Status FROM Case WHERE Status != \\'Closed\\' ORDER BY CreatedDate DESC'. "
            "Always include LIMIT to avoid returning too many records."
        ),
        args_schema=SOQLQueryInput,
        returns="JSON with query results including records array and totalSize",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to query Salesforce data with specific filters or conditions",
            "User needs a custom or complex query that specialized search tools cannot handle",
            "User asks for aggregate data (COUNT, SUM, AVG) from Salesforce",
            "User wants to query a custom object or non-standard fields",
        ],
        when_not_to_use=[
            "User wants a simple text search across multiple objects (use sosl_search)",
            "User wants to search accounts by name (use search_accounts for simpler queries)",
        ],
        typical_queries=[
            "Run a SOQL query to find all accounts in Technology industry",
            "Query all open opportunities with amount greater than 50000",
            "Get all contacts for account X",
            "Count the number of open cases",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="sosl_search",
        description="Execute a SOSL search across Salesforce objects",
        llm_description=(
            "Executes a Salesforce Object Search Language (SOSL) search. Use this for full-text search across "
            "multiple objects. Example: 'FIND {Acme} IN ALL FIELDS RETURNING Account(Id, Name), Contact(Id, Name, Email)'. "
            "SOSL is best for keyword search; for structured queries use soql_query."
        ),
        args_schema=SOSLSearchInput,
        returns="JSON with search results grouped by object type",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to search across multiple Salesforce objects at once",
            "User wants a full-text keyword search in Salesforce",
            "User asks to find all records related to a term or company name",
        ],
        when_not_to_use=[
            "User wants a structured query with specific filters (use soql_query)",
            "User wants to search only one specific object type (use the dedicated search tool for that object)",
        ],
        typical_queries=[
            "Search for 'Acme' across all Salesforce objects",
            "Find all records mentioning 'server outage'",
            "Search for contact or account named John",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # Generic Record CRUD Tools
    # ------------------------------------------------------------------

    @tool(
        app_name="salesforce",
        tool_name="get_record",
        description="Retrieve a Salesforce record by ID",
        llm_description=(
            "Retrieves a single record from any Salesforce object by its record ID. "
            "Provide the object API name (e.g., 'Account', 'Contact', 'Lead', 'Opportunity', 'Case', or any custom object like 'Custom__c') "
            "and the 15/18-character record ID. Optionally specify fields to return."
        ),
        args_schema=GetRecordInput,
        returns="JSON with the record data",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to view a specific Salesforce record by ID",
            "User asks for details of a record given its ID",
            "User needs to look up a record from any standard or custom object",
        ],
        when_not_to_use=[
            "User wants to search for records without knowing the ID (use search or SOQL tools)",
            "User wants to list recent records (use list_recent_records)",
        ],
        typical_queries=[
            "Get the account with ID 001XXXXXXXXXXXX",
            "Show me the details of contact 003XXXXXXXXXXXX",
            "Fetch opportunity record 006XXXXXXXXXXXX",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="create_record",
        description="Create a new Salesforce record",
        llm_description=(
            "Creates a new record for any Salesforce object. Provide the object API name and a dictionary of "
            "field-value pairs. For standard objects use standard field API names (e.g., 'Name', 'Email', 'Phone'). "
            "For custom objects, field names typically end with '__c'. Prefer using the specialized create tools "
            "(create_account, create_contact, etc.) for standard objects when possible."
        ),
        args_schema=CreateRecordInput,
        returns="JSON with the created record ID",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to create a record for a custom object or non-standard object",
            "User asks to create a record and the specialized tool is not available",
        ],
        when_not_to_use=[
            "User wants to create an Account (use create_account)",
            "User wants to create a Contact (use create_contact)",
            "User wants to create a Lead (use create_lead)",
            "User wants to create an Opportunity (use create_opportunity)",
            "User wants to create a Case (use create_case)",
        ],
        typical_queries=[
            "Create a custom object record in Salesforce",
            "Add a new record to the Custom_Object__c",
        ],
    )
    async def create_record(
        self, sobject: str, data: dict[str, Any]
    ) -> tuple[bool, str]:
        """Create a new Salesforce record."""
        try:
            logger.info("salesforce.create_record called: sobject=%s", sobject)
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="update_record",
        description="Update an existing Salesforce record",
        llm_description=(
            "Updates an existing record by ID. Provide the object API name, record ID, and a dictionary "
            "of field-value pairs to update. Only include fields that should change."
        ),
        args_schema=UpdateRecordInput,
        returns="JSON confirming the update",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to update or modify a Salesforce record",
            "User asks to change fields on an existing record",
        ],
        when_not_to_use=[
            "User wants to create a new record (use create_record or specialized create tools)",
            "User wants to delete a record (use delete_record)",
        ],
        typical_queries=[
            "Update the account name to 'New Acme Corp'",
            "Change the opportunity stage to 'Closed Won'",
            "Update the contact's email address",
        ],
    )
    async def update_record(
        self, sobject: str, record_id: str, data: dict[str, Any]
    ) -> tuple[bool, str]:
        """Update a Salesforce record."""
        try:
            logger.info(
                "salesforce.update_record called: sobject=%s, record_id=%s", sobject, record_id
            )
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # Object Metadata
    # ------------------------------------------------------------------

    @tool(
        app_name="salesforce",
        tool_name="describe_object",
        description="Get metadata and field information for a Salesforce object",
        llm_description=(
            "Returns metadata about a Salesforce object including all fields, their types, picklist values, "
            "relationships, and validation rules. Use this to discover what fields exist on an object "
            "before creating or querying records."
        ),
        args_schema=DescribeObjectInput,
        returns="JSON with object metadata including fields, relationships, and picklist values",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to know what fields are available on a Salesforce object",
            "User needs to discover field names before running a query",
            "User asks about the schema or structure of a Salesforce object",
            "User wants to see picklist values for a field",
        ],
        when_not_to_use=[
            "User wants to query actual data (use soql_query or search tools)",
            "User wants to create or update a record (use create/update tools)",
        ],
        typical_queries=[
            "What fields does the Account object have?",
            "Describe the Opportunity object in Salesforce",
            "What are the picklist values for Case Status?",
            "Show me the schema for the Lead object",
        ],
    )
    async def describe_object(self, sobject: str) -> tuple[bool, str]:
        """Describe a Salesforce object's metadata."""
        try:
            logger.info("salesforce.describe_object called: sobject=%s", sobject)
            response = await self.client.s_object_describe(
                sobject_api_name=sobject, version=self.api_version
            )
            if response.success:
                data = response.data
                # Filter picklist values to only active entries
                for field in data.get("fields", []):
                    pv = field.get("picklistValues")
                    if isinstance(pv, list):
                        field["picklistValues"] = [
                            v for v in pv if v.get("active", False)
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
                return False, json.dumps(
                    {SF_JSON_ERROR_KEY: "Unexpected describe response format"}
                )
            return self._handle_response(
                response, MSG_SUCCESS
            )
        except Exception as e:
            logger.error(ERR_LOG, "describe_object", e)
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # Recent Records
    # ------------------------------------------------------------------

    @tool(
        app_name="salesforce",
        tool_name="list_recent_records",
        description="List recently viewed records of a Salesforce object",
        llm_description=(
            "Lists the most recently viewed or modified records for a given Salesforce object type. "
            "Useful for getting a quick overview without constructing a full SOQL query."
        ),
        args_schema=ListRecentRecordsInput,
        returns="JSON with a list of recent records",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to see recent accounts, contacts, or other records",
            "User asks for the latest or most recent records of a type",
            "User wants a quick overview of records without specific filters",
        ],
        when_not_to_use=[
            "User wants specific records matching criteria (use search tools or soql_query)",
            "User knows the exact record ID (use get_record)",
        ],
        typical_queries=[
            "Show me recent accounts",
            "List my latest opportunities",
            "What are the most recent cases?",
        ],
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
            query = SOQL_LIST_RECENT_RECORDS.format(sobject=sobject, limit=limit)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(
                response, MSG_SUCCESS
            )
        except Exception as e:
            logger.error(ERR_LOG, "list_recent_records", e)
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # Account Tools
    # ------------------------------------------------------------------

    @tool(
        app_name="salesforce",
        tool_name="search_accounts",
        description="Search for Salesforce accounts",
        llm_description=(
            "Searches Salesforce Account records by name and/or industry. "
            "Returns key fields: Id, Name, Industry, Phone, Website, Type, BillingCity, BillingState. "
            "For more complex account queries, use soql_query."
        ),
        args_schema=SearchAccountsInput,
        returns="JSON with matching account records",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to find accounts in Salesforce",
            "User asks to list or search for companies/organizations in Salesforce",
        ],
        when_not_to_use=[
            "User wants a complex query with many conditions (use soql_query)",
            "User wants to search across multiple object types (use sosl_search)",
        ],
        typical_queries=[
            "Search for accounts named Acme",
            "Find all Technology industry accounts",
            "list accounts matching 'Global'",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="create_account",
        description="Create a new Salesforce account",
        llm_description=(
            "Creates a new Account in Salesforce. Only the Name field is required. "
            "Optionally provide Industry, Phone, Website, Description, and billing address fields. "
            "Do not ask the user for optional fields they did not provide."
        ),
        args_schema=CreateAccountInput,
        returns="JSON with the created account ID",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to create a new account/company in Salesforce",
            "User asks to add a new organization to the CRM",
        ],
        when_not_to_use=[
            "User wants to update an existing account (use update_record)",
            "User wants to create a Contact, Lead, or Opportunity (use their dedicated tools)",
        ],
        typical_queries=[
            "Create a new account named Acme Corp",
            "Add a new company called TechStart to Salesforce",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # Contact Tools
    # ------------------------------------------------------------------

    @tool(
        app_name="salesforce",
        tool_name="search_contacts",
        description="Search for Salesforce contacts",
        llm_description=(
            "Searches Salesforce Contact records by name, email, or parent account. "
            "Returns key fields: Id, Name, Email, Phone, Title, Account.Name. "
            "For more complex queries, use soql_query."
        ),
        args_schema=SearchContactsInput,
        returns="JSON with matching contact records",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to find contacts in Salesforce",
            "User asks to search for a person's contact details in the CRM",
        ],
        when_not_to_use=[
            "User wants a complex query (use soql_query)",
            "User is looking for Leads, not Contacts (use search_leads)",
        ],
        typical_queries=[
            "Search for contacts named John",
            "Find contacts with email @acme.com",
            "List contacts for account 001XXXXXXXXXXXX",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="create_contact",
        description="Create a new Salesforce contact",
        llm_description=(
            "Creates a new Contact in Salesforce. LastName is required. "
            "Optionally provide FirstName, Email, Phone, Title, AccountId, and Department. "
            "Do not ask the user for optional fields they did not provide."
        ),
        args_schema=CreateContactInput,
        returns="JSON with the created contact ID",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to create a new contact in Salesforce",
            "User asks to add a person to the CRM",
        ],
        when_not_to_use=[
            "User wants to create a Lead (use create_lead)",
            "User wants to update an existing contact (use update_record)",
        ],
        typical_queries=[
            "Create a contact for John Doe at Acme",
            "Add a new contact with email john@example.com",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # Lead Tools
    # ------------------------------------------------------------------

    @tool(
        app_name="salesforce",
        tool_name="search_leads",
        description="Search for Salesforce leads",
        llm_description=(
            "Searches Salesforce Lead records by name, company, or status. "
            "Returns key fields: Id, Name, Company, Email, Phone, Status, LeadSource. "
            "For more complex queries, use soql_query."
        ),
        args_schema=SearchLeadsInput,
        returns="JSON with matching lead records",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to find leads in Salesforce",
            "User asks to search for prospects or potential customers",
        ],
        when_not_to_use=[
            "User is looking for existing Contacts (use search_contacts)",
            "User wants a complex query (use soql_query)",
        ],
        typical_queries=[
            "Search for leads from Acme company",
            "Find all open leads",
            "List leads with status 'Working - Contacted'",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="create_lead",
        description="Create a new Salesforce lead",
        llm_description=(
            "Creates a new Lead in Salesforce. LastName and Company are required. "
            "Optionally provide FirstName, Email, Phone, Title, Status, and Industry. "
            "Do not ask the user for optional fields they did not provide."
        ),
        args_schema=CreateLeadInput,
        returns="JSON with the created lead ID",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to create a new lead in Salesforce",
            "User asks to add a prospect or potential customer",
        ],
        when_not_to_use=[
            "User wants to create a Contact (use create_contact)",
            "User wants to convert a lead (use update_record to change status)",
        ],
        typical_queries=[
            "Create a lead for Jane Doe from TechCorp",
            "Add a new lead with email jane@techcorp.com",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # Opportunity Tools
    # ------------------------------------------------------------------

    @tool(
        app_name="salesforce",
        tool_name="search_opportunities",
        description="Search for Salesforce opportunities",
        llm_description=(
            "Searches Salesforce Opportunity records by name, stage, or parent account. "
            "Returns key fields: Id, Name, StageName, Amount, CloseDate, Probability, Account.Name. "
            "For more complex queries, use soql_query."
        ),
        args_schema=SearchOpportunitiesInput,
        returns="JSON with matching opportunity records",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to find opportunities or deals in Salesforce",
            "User asks about the sales pipeline",
        ],
        when_not_to_use=[
            "User wants a complex query with many conditions (use soql_query)",
            "User wants aggregate pipeline data (use soql_query with SUM/COUNT)",
        ],
        typical_queries=[
            "Search for opportunities in the Qualification stage",
            "Find all open deals for account Acme",
            "List opportunities closing this month",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="create_opportunity",
        description="Create a new Salesforce opportunity",
        llm_description=(
            "Creates a new Opportunity in Salesforce. Name, StageName, and CloseDate are required. "
            "Optionally provide AccountId, Amount, Description, and Probability. "
            "CloseDate must be in YYYY-MM-DD format. Do not ask the user for optional fields they did not provide."
        ),
        args_schema=CreateOpportunityInput,
        returns="JSON with the created opportunity ID",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to create a new opportunity or deal in Salesforce",
            "User asks to add a new sales opportunity",
        ],
        when_not_to_use=[
            "User wants to update an existing opportunity (use update_record)",
            "User wants to create an Account or Contact (use their dedicated tools)",
        ],
        typical_queries=[
            "Create an opportunity named 'Enterprise Deal' at Qualification stage closing 2025-12-31",
            "Add a new $50,000 deal for Acme Corp",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # Case Tools
    # ------------------------------------------------------------------

    @tool(
        app_name="salesforce",
        tool_name="search_cases",
        description="Search for Salesforce cases",
        llm_description=(
            "Searches Salesforce Case records by subject, status, priority, or parent account. "
            "Returns key fields: Id, CaseNumber, Subject, Status, Priority, Origin, Account.Name. "
            "For more complex queries, use soql_query."
        ),
        args_schema=SearchCasesInput,
        returns="JSON with matching case records",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to find support cases in Salesforce",
            "User asks about open or escalated cases",
        ],
        when_not_to_use=[
            "User wants a complex query with many conditions (use soql_query)",
            "User wants to search across multiple objects (use sosl_search)",
        ],
        typical_queries=[
            "Search for high priority cases",
            "Find all open cases",
            "List escalated cases for account Acme",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="create_case",
        description="Create a new Salesforce case",
        llm_description=(
            "Creates a new Case (support ticket) in Salesforce. Subject is required. "
            "Optionally provide Status, Priority, Origin, Description, AccountId, and ContactId. "
            "Do not ask the user for optional fields they did not provide."
        ),
        args_schema=CreateCaseInput,
        returns="JSON with the created case ID",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to create a new support case in Salesforce",
            "User asks to open a ticket or report an issue",
        ],
        when_not_to_use=[
            "User wants to update an existing case (use update_record)",
            "User wants to close a case (use update_record to change Status to 'Closed')",
        ],
        typical_queries=[
            "Create a case for 'Login issue'",
            "Open a high priority support ticket",
            "Create a new case for account Acme about billing",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # Product Tools
    # ------------------------------------------------------------------

    @tool(
        app_name="salesforce",
        tool_name="search_products",
        description="Search for Salesforce products",
        llm_description=(
            "Searches Salesforce Product2 records by name, product code, or family. "
            "Returns key fields: Id, Name, ProductCode, Description, Family, IsActive, QuantityUnitOfMeasure. "
            "By default only active products are returned. For complex queries, use soql_query."
        ),
        args_schema=SearchProductsInput,
        returns="JSON with matching product records",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to find products in Salesforce",
            "User asks to search the product catalog",
            "User wants to look up a SKU or product code",
        ],
        when_not_to_use=[
            "User wants line items on an opportunity (use soql_query on OpportunityLineItem)",
            "User wants a complex query (use soql_query)",
        ],
        typical_queries=[
            "Search for products named 'Pro Plan'",
            "Find products in the Software family",
            "Look up product SKU ABC-123",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="create_product",
        description="Create a new Salesforce product",
        llm_description=(
            "Creates a new Product2 record in Salesforce. Only Name is required. "
            "Optionally provide ProductCode, Description, Family, IsActive, and QuantityUnitOfMeasure. "
            "Note: To make the product sellable on opportunities, you must also create a PricebookEntry "
            "in the standard pricebook (not handled by this tool). Do not ask the user for optional fields they did not provide."
        ),
        args_schema=CreateProductInput,
        returns="JSON with the created product ID",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to add a new product to the Salesforce product catalog",
            "User asks to create a SKU or product entry",
        ],
        when_not_to_use=[
            "User wants to add an existing product to an opportunity (use add_product_to_opportunity)",
            "User wants to update an existing product (use update_record)",
        ],
        typical_queries=[
            "Create a product named 'Premium License'",
            "Add a new product with code SKU-001",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="list_pricebooks",
        description="List Salesforce price books (Pricebook2)",
        llm_description=(
            "Lists Salesforce Pricebook2 records. Returns key fields: Id, Name, Description, "
            "IsActive, IsStandard. By default only active price books are returned. "
            "Use this to find a Pricebook Id needed for adding products to opportunities."
        ),
        args_schema=ListPricebooksInput,
        returns="JSON with matching price book records",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to list available price books",
            "User asks which pricebooks exist",
            "User needs a Pricebook Id to add products to an opportunity",
        ],
        when_not_to_use=[
            "User wants product entries within a pricebook (use soql_query on PricebookEntry)",
        ],
        typical_queries=[
            "List all price books",
            "Show active pricebooks",
            "Find the standard pricebook",
        ],
    )
    async def list_pricebooks(
        self,
        name: str | None = None,
        active_only: bool = True,
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="add_product_to_opportunity",
        description="Add a product line item to a Salesforce opportunity",
        llm_description=(
            "Adds a product to a Salesforce Opportunity by creating an OpportunityLineItem. "
            "You must provide an opportunity_id and either a pricebook_entry_id directly, or a "
            "product_id (the tool will look up the PricebookEntry on the opportunity's pricebook, "
            "or on pricebook_id if supplied). Quantity defaults to 1. If unit_price is omitted, "
            "the PricebookEntry's UnitPrice is used. Note: the opportunity must already have a "
            "Pricebook2Id set for line items to be added."
        ),
        args_schema=AddProductToOpportunityInput,
        returns="JSON with the created OpportunityLineItem ID",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to add a product to an opportunity",
            "User wants to add a line item to a deal",
            "User wants to attach a SKU to an opportunity",
        ],
        when_not_to_use=[
            "User wants to create a new product in the catalog (use create_product)",
            "User wants to update an existing line item (use update_record on OpportunityLineItem)",
        ],
        typical_queries=[
            "Add product 01t... to opportunity 006...",
            "Add 5 units of the Pro Plan product to this opportunity",
        ],
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
                return False, json.dumps({SF_JSON_ERROR_KEY: ERR_NEED_PBE_OR_PRODUCT})

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
                        return False, json.dumps({SF_JSON_ERROR_KEY: ERR_OPP_NO_PRICEBOOK})

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
                    return False, json.dumps({SF_JSON_ERROR_KEY: ERR_NO_ACTIVE_PBE})
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # Task Tools
    # ------------------------------------------------------------------

    @tool(
        app_name="salesforce",
        tool_name="search_tasks",
        description="Search for Salesforce tasks",
        llm_description=(
            "Searches Salesforce Task records by subject, status, priority, owner, or related record. "
            "Returns key fields: Id, Subject, Status, Priority, ActivityDate, Owner.Name, What.Name, Who.Name. "
            "WhatId is the related record (Account, Opportunity, Case, etc.); WhoId is the related Contact or Lead. "
            "For complex queries, use soql_query."
        ),
        args_schema=SearchTasksInput,
        returns="JSON with matching task records",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to find tasks or activities in Salesforce",
            "User asks about their open or overdue tasks",
            "User wants to see tasks for a specific account, opportunity, or contact",
        ],
        when_not_to_use=[
            "User wants events / calendar items (use soql_query on Event)",
            "User wants a complex query (use soql_query)",
        ],
        typical_queries=[
            "Show my open tasks",
            "Find tasks for opportunity 006XXXXXXXXXXXX",
            "List high priority tasks",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="create_task",
        description="Create a new Salesforce task",
        llm_description=(
            "Creates a new Task in Salesforce. Subject is required. "
            "Use what_id to relate the task to an Account/Opportunity/Case (etc.) and who_id to relate to a Contact/Lead. "
            "activity_date must be in YYYY-MM-DD format. owner_id defaults to the authenticated user when omitted. "
            "Do not ask the user for optional fields they did not provide."
        ),
        args_schema=CreateTaskInput,
        returns="JSON with the created task ID",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to create a new task or to-do in Salesforce",
            "User asks to add a follow-up activity for an account, contact, or opportunity",
            "User wants to schedule a call/email/meeting as a task",
        ],
        when_not_to_use=[
            "User wants a calendar event (use soql_query / create_record on Event instead)",
            "User wants to update an existing task (use update_record)",
        ],
        typical_queries=[
            "Create a task to follow up with John tomorrow",
            "Add a high priority task for opportunity X",
            "Create a 'Call' task for contact 003XXXXXXXXXXXX",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # Chatter
    # ------------------------------------------------------------------

    @tool(
        app_name="salesforce",
        tool_name="get_record_chatter",
        description="Get the Chatter feed for a Salesforce record",
        llm_description=(
            "Returns the Chatter feed elements (posts, comments, updates) for any Salesforce record by ID — "
            "Account, Opportunity, Case, Contact, Lead, or any other sObject. "
            "Use this to summarize discussions, recent activity, or comments on a record. "
            "If you only have a name, search for the record first (e.g., search_opportunities, search_accounts) "
            "to get the ID, then call this tool."
        ),
        args_schema=GetRecordChatterInput,
        returns="JSON with Chatter feed elements including posts, comments, authors, and timestamps",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User asks what is being discussed on a record's Chatter",
            "User wants the latest Chatter posts/comments/activity for an opportunity, account, case, etc.",
            "User asks to summarize discussion or collaboration on a Salesforce record",
        ],
        when_not_to_use=[
            "User wants the record's field data (use get_record or search_*)",
            "User wants their own news feed across all records (use soql_query on FeedItem)",
        ],
        typical_queries=[
            "What is being discussed in the Acme opportunity's Chatter?",
            "Show me the latest Chatter posts on account 001XXXXXXXXXXXX",
            "Summarize the discussion on this case's feed",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="post_chatter_comment",
        description="Post a comment / reply on a Salesforce Chatter feed item",
        llm_description=(
            "Adds a comment (reply) to an existing Chatter FeedElement. Provide the feed element ID "
            "(starts with '0D5', obtained from get_record_chatter) and the comment text. "
            "Use this when the user wants to reply to a specific Chatter post."
        ),
        args_schema=PostChatterCommentInput,
        returns="JSON with the created comment details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to reply to a Chatter post on a record",
            "User asks to comment on a specific feed item",
            "User wants to add a comment/reply to a discussion thread",
        ],
        when_not_to_use=[
            "User wants to create a new top-level post on a record (use post_chatter_to_record)",
            "User wants to read the feed (use get_record_chatter)",
        ],
        typical_queries=[
            "Reply 'thanks!' to that Chatter post",
            "Add a comment to feed item 0D5XXXXXXXXXXXX",
            "Comment on the latest Chatter post on this opportunity",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    @tool(
        app_name="salesforce",
        tool_name="post_chatter_to_record",
        description="Create a new Chatter post on a Salesforce record",
        llm_description=(
            "Creates a new top-level Chatter FeedItem on any record (Account, Opportunity, Case, etc.). "
            "Provide the record ID and the post text. Use this when the user wants to start a new "
            "Chatter discussion or post an update on a record. To reply to an existing post, use post_chatter_comment instead."
        ),
        args_schema=PostChatterToRecordInput,
        returns="JSON with the created feed item details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to post a new Chatter update on a record",
            "User asks to share an update on an opportunity, account, or case",
            "User wants to start a new Chatter discussion on a record",
        ],
        when_not_to_use=[
            "User wants to reply to an existing post (use post_chatter_comment)",
            "User wants to read existing posts (use get_record_chatter)",
        ],
        typical_queries=[
            "Post 'Closed won!' on the Acme opportunity Chatter",
            "Add a Chatter update to account 001XXXXXXXXXXXX",
            "Share a status update on this case",
        ],
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
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})

    # ------------------------------------------------------------------
    # User Info
    # ------------------------------------------------------------------

    @tool(
        app_name="salesforce",
        tool_name="get_current_user",
        description="Get the current authenticated Salesforce user's info",
        llm_description=(
            "Returns information about the currently authenticated Salesforce user, including "
            "their name, email, profile, and organization details."
        ),
        args_schema=GetUserInfoInput,
        returns="JSON with user profile information",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.SEARCH,
        is_essential=False,
        requires_auth=True,
        when_to_use=[
            "User wants to know who is currently logged in to Salesforce",
            "User asks about their Salesforce profile or organization",
            "User needs their Salesforce user ID for other operations",
        ],
        when_not_to_use=[
            "User is asking about a different Salesforce user (use soql_query on User object)",
        ],
        typical_queries=[
            "Who am I in Salesforce?",
            "Show my Salesforce user info",
            "What is my Salesforce user ID?",
        ],
    )
    async def get_current_user(self) -> tuple[bool, str]:
        """Get the current authenticated user's info."""
        try:
            logger.info("salesforce.get_current_user called")
            response = await self.client.get_user_info()
            return self._handle_response(response, MSG_SUCCESS)
        except Exception as e:
            logger.error(ERR_LOG, "get_current_user", e)
            return False, json.dumps({SF_JSON_ERROR_KEY: str(e)})
