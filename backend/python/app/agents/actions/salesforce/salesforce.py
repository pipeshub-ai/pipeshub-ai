import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

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
from app.modules.agents.qna.chat_state import ChatState
logger = logging.getLogger(__name__)

# Default API version used across all tools
DEFAULT_API_VERSION = "59.0"


# ---------------------------------------------------------------------------
# Pydantic input schemas
# ---------------------------------------------------------------------------

class SOQLQueryInput(BaseModel):
    """Schema for executing a SOQL query"""
    query: str = Field(
        description="The SOQL query string to execute (e.g., \"SELECT Id, Name FROM Account LIMIT 10\")"
    )


class SOSLSearchInput(BaseModel):
    """Schema for executing a SOSL search"""
    search: str = Field(
        description="The SOSL search string (e.g., \"FIND {Acme} IN ALL FIELDS RETURNING Account(Id, Name), Contact(Id, Name)\")"
    )


class GetRecordInput(BaseModel):
    """Schema for retrieving a Salesforce record by ID"""
    sobject: str = Field(
        description="The Salesforce object API name (e.g., 'Account', 'Contact', 'Lead', 'Opportunity', 'Case')"
    )
    record_id: str = Field(description="The 15 or 18-character Salesforce record ID")
    fields: Optional[str] = Field(
        default=None,
        description="Comma-separated list of fields to return (e.g., 'Id,Name,Email'). Omit to return all accessible fields.",
    )


class CreateRecordInput(BaseModel):
    """Schema for creating a Salesforce record"""
    sobject: str = Field(
        description="The Salesforce object API name (e.g., 'Account', 'Contact', 'Lead', 'Opportunity', 'Case')"
    )
    data: Dict[str, Any] = Field(
        description="Field-value pairs for the new record (e.g., {\"Name\": \"Acme Corp\", \"Industry\": \"Technology\"})"
    )


class UpdateRecordInput(BaseModel):
    """Schema for updating a Salesforce record"""
    sobject: str = Field(
        description="The Salesforce object API name (e.g., 'Account', 'Contact', 'Lead', 'Opportunity', 'Case')"
    )
    record_id: str = Field(description="The 15 or 18-character Salesforce record ID")
    data: Dict[str, Any] = Field(
        description="Field-value pairs to update (e.g., {\"Name\": \"New Name\", \"Phone\": \"555-1234\"})"
    )


class DescribeObjectInput(BaseModel):
    """Schema for describing a Salesforce object's metadata"""
    sobject: str = Field(
        description="The Salesforce object API name to describe (e.g., 'Account', 'Contact', 'Lead', 'Opportunity', 'Case')"
    )


class ListRecentRecordsInput(BaseModel):
    """Schema for listing recent records of a Salesforce object"""
    sobject: str = Field(
        description="The Salesforce object API name (e.g., 'Account', 'Contact', 'Lead', 'Opportunity', 'Case')"
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of recent records to return (default 10, max 50)",
    )


class SearchAccountsInput(BaseModel):
    """Schema for searching Salesforce accounts"""
    name: Optional[str] = Field(
        default=None,
        description="Account name to search for (partial match supported)",
    )
    industry: Optional[str] = Field(
        default=None,
        description="Filter by industry",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class SearchContactsInput(BaseModel):
    """Schema for searching Salesforce contacts"""
    name: Optional[str] = Field(
        default=None,
        description="Contact name to search for (partial match supported)",
    )
    email: Optional[str] = Field(
        default=None,
        description="Filter by email address",
    )
    account_id: Optional[str] = Field(
        default=None,
        description="Filter by parent Account ID",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class SearchLeadsInput(BaseModel):
    """Schema for searching Salesforce leads"""
    name: Optional[str] = Field(
        default=None,
        description="Lead name to search for (partial match supported)",
    )
    company: Optional[str] = Field(
        default=None,
        description="Filter by company name",
    )
    status: Optional[str] = Field(
        default=None,
        description="Filter by lead status (e.g., 'Open - Not Contacted', 'Working - Contacted', 'Closed - Converted')",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class SearchOpportunitiesInput(BaseModel):
    """Schema for searching Salesforce opportunities"""
    name: Optional[str] = Field(
        default=None,
        description="Opportunity name to search for (partial match supported)",
    )
    stage: Optional[str] = Field(
        default=None,
        description="Filter by stage name (e.g., 'Prospecting', 'Qualification', 'Closed Won')",
    )
    account_id: Optional[str] = Field(
        default=None,
        description="Filter by parent Account ID",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class SearchCasesInput(BaseModel):
    """Schema for searching Salesforce cases"""
    subject: Optional[str] = Field(
        default=None,
        description="Case subject to search for (partial match supported)",
    )
    status: Optional[str] = Field(
        default=None,
        description="Filter by case status (e.g., 'New', 'Working', 'Escalated', 'Closed')",
    )
    priority: Optional[str] = Field(
        default=None,
        description="Filter by priority (e.g., 'High', 'Medium', 'Low')",
    )
    account_id: Optional[str] = Field(
        default=None,
        description="Filter by parent Account ID",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class CreateAccountInput(BaseModel):
    """Schema for creating a Salesforce account"""
    name: str = Field(description="Account name (required)")
    industry: Optional[str] = Field(default=None, description="Industry")
    phone: Optional[str] = Field(default=None, description="Phone number")
    website: Optional[str] = Field(default=None, description="Website URL")
    description: Optional[str] = Field(default=None, description="Account description")
    billing_city: Optional[str] = Field(default=None, description="Billing city")
    billing_state: Optional[str] = Field(default=None, description="Billing state/province")
    billing_country: Optional[str] = Field(default=None, description="Billing country")


class CreateContactInput(BaseModel):
    """Schema for creating a Salesforce contact"""
    last_name: str = Field(description="Last name (required)")
    first_name: Optional[str] = Field(default=None, description="First name")
    email: Optional[str] = Field(default=None, description="Email address")
    phone: Optional[str] = Field(default=None, description="Phone number")
    title: Optional[str] = Field(default=None, description="Job title")
    account_id: Optional[str] = Field(default=None, description="Parent Account ID to associate this contact with")
    department: Optional[str] = Field(default=None, description="Department")


class CreateLeadInput(BaseModel):
    """Schema for creating a Salesforce lead"""
    last_name: str = Field(description="Last name (required)")
    company: str = Field(description="Company name (required)")
    first_name: Optional[str] = Field(default=None, description="First name")
    email: Optional[str] = Field(default=None, description="Email address")
    phone: Optional[str] = Field(default=None, description="Phone number")
    title: Optional[str] = Field(default=None, description="Job title")
    status: Optional[str] = Field(default=None, description="Lead status (e.g., 'Open - Not Contacted')")
    industry: Optional[str] = Field(default=None, description="Industry")


class CreateOpportunityInput(BaseModel):
    """Schema for creating a Salesforce opportunity"""
    name: str = Field(description="Opportunity name (required)")
    stage_name: str = Field(description="Stage name (required, e.g., 'Prospecting', 'Qualification', 'Needs Analysis')")
    close_date: str = Field(description="Expected close date in YYYY-MM-DD format (required)")
    account_id: Optional[str] = Field(default=None, description="Parent Account ID")
    amount: Optional[float] = Field(default=None, description="Opportunity amount")
    description: Optional[str] = Field(default=None, description="Opportunity description")
    probability: Optional[float] = Field(default=None, ge=0, le=100, description="Probability percentage (0-100)")


class CreateCaseInput(BaseModel):
    """Schema for creating a Salesforce case"""
    subject: str = Field(description="Case subject (required)")
    status: Optional[str] = Field(default=None, description="Case status (e.g., 'New', 'Working', 'Escalated')")
    priority: Optional[str] = Field(default=None, description="Priority (e.g., 'High', 'Medium', 'Low')")
    origin: Optional[str] = Field(default=None, description="Case origin (e.g., 'Phone', 'Email', 'Web')")
    description: Optional[str] = Field(default=None, description="Case description")
    account_id: Optional[str] = Field(default=None, description="Parent Account ID")
    contact_id: Optional[str] = Field(default=None, description="Associated Contact ID")


class SearchProductsInput(BaseModel):
    """Schema for searching Salesforce products"""
    name: Optional[str] = Field(
        default=None,
        description="Product name to search for (partial match supported)",
    )
    product_code: Optional[str] = Field(
        default=None,
        description="Filter by product code (SKU)",
    )
    family: Optional[str] = Field(
        default=None,
        description="Filter by product family",
    )
    active_only: bool = Field(
        default=True,
        description="Only return active products. Default True.",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class CreateProductInput(BaseModel):
    """Schema for creating a Salesforce product"""
    name: str = Field(description="Product name (required)")
    product_code: Optional[str] = Field(default=None, description="Product code / SKU")
    description: Optional[str] = Field(default=None, description="Product description")
    family: Optional[str] = Field(default=None, description="Product family")
    is_active: bool = Field(default=True, description="Whether the product is active. Default True.")
    quantity_unit_of_measure: Optional[str] = Field(
        default=None,
        description="Unit of measure (e.g., 'Each', 'Hours', 'Kg')",
    )


class ListPricebooksInput(BaseModel):
    """Schema for listing Salesforce price books"""
    name: Optional[str] = Field(
        default=None,
        description="Price book name to search for (partial match supported)",
    )
    active_only: bool = Field(
        default=True,
        description="Only return active price books. Default True.",
    )
    limit: int = Field(
        default=20, ge=1, le=200,
        description="Maximum number of results (default 20, max 200)",
    )


class AddProductToOpportunityInput(BaseModel):
    """Schema for adding a product line item to a Salesforce opportunity"""
    opportunity_id: str = Field(
        description="The 15 or 18-character Salesforce Opportunity Id"
    )
    pricebook_entry_id: Optional[str] = Field(
        default=None,
        description="The PricebookEntry Id for the product. If omitted, provide product_id (and optionally pricebook_id) and the tool will look it up.",
    )
    product_id: Optional[str] = Field(
        default=None,
        description="The Product2 Id. Used to look up the PricebookEntry if pricebook_entry_id is not provided.",
    )
    pricebook_id: Optional[str] = Field(
        default=None,
        description="The Pricebook2 Id to use when looking up a PricebookEntry from product_id. If omitted, the opportunity's pricebook is used.",
    )
    quantity: float = Field(
        default=1, gt=0,
        description="Quantity of the product (default 1)",
    )
    unit_price: Optional[float] = Field(
        default=None,
        description="Unit sales price. If omitted, the PricebookEntry UnitPrice is used.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional line item description",
    )


class SearchTasksInput(BaseModel):
    """Schema for searching Salesforce tasks"""
    subject: Optional[str] = Field(
        default=None,
        description="Task subject to search for (partial match supported)",
    )
    status: Optional[str] = Field(
        default=None,
        description="Filter by task status (e.g., 'Not Started', 'In Progress', 'Completed', 'Deferred')",
    )
    priority: Optional[str] = Field(
        default=None,
        description="Filter by priority (e.g., 'High', 'Normal', 'Low')",
    )
    owner_id: Optional[str] = Field(
        default=None,
        description="Filter by task owner User ID",
    )
    what_id: Optional[str] = Field(
        default=None,
        description="Filter by related record ID (Account, Opportunity, Case, etc.)",
    )
    who_id: Optional[str] = Field(
        default=None,
        description="Filter by related Contact or Lead ID",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class CreateTaskInput(BaseModel):
    """Schema for creating a Salesforce task"""
    subject: str = Field(description="Task subject (required)")
    status: Optional[str] = Field(
        default=None,
        description="Task status (e.g., 'Not Started', 'In Progress', 'Completed')",
    )
    priority: Optional[str] = Field(
        default=None,
        description="Priority (e.g., 'High', 'Normal', 'Low')",
    )
    activity_date: Optional[str] = Field(
        default=None,
        description="Due date in YYYY-MM-DD format",
    )
    description: Optional[str] = Field(default=None, description="Task description / comments")
    owner_id: Optional[str] = Field(
        default=None,
        description="User ID of the task owner. Defaults to the authenticated user.",
    )
    what_id: Optional[str] = Field(
        default=None,
        description="Related record ID (Account, Opportunity, Case, etc.) the task is about",
    )
    who_id: Optional[str] = Field(
        default=None,
        description="Related Contact or Lead ID the task is associated with",
    )
    type: Optional[str] = Field(
        default=None,
        description="Task type (e.g., 'Call', 'Email', 'Meeting')",
    )


class GetRecordChatterInput(BaseModel):
    """Schema for fetching a Salesforce record's Chatter feed"""
    record_id: str = Field(
        description="The Salesforce record ID whose Chatter feed should be fetched (Account, Opportunity, Case, Contact, Lead, etc.)",
    )


class PostChatterCommentInput(BaseModel):
    """Schema for posting a comment/reply on a Chatter feed item"""
    feed_element_id: str = Field(
        description="The Chatter FeedElement (FeedItem) ID to reply to (starts with '0D5')",
    )
    text: str = Field(
        description=(
            "The comment text to post (max 10,000 chars). When is_rich_text=True, supports "
            "markdown: **bold**, *italic*, [label](url), and blank lines for paragraph breaks."
        )
    )
    is_rich_text: bool = Field(
        default=False,
        description=(
            "If True, the `text` is parsed as markdown and posted as Chatter rich text "
            "(bold, italic, hyperlinks, paragraphs). Default False (plain text)."
        ),
    )


class PostChatterToRecordInput(BaseModel):
    """Schema for creating a new Chatter post on a Salesforce record"""
    record_id: str = Field(
        description="The Salesforce record ID to post to (Account, Opportunity, Case, Contact, Lead, User, etc.)",
    )
    text: str = Field(
        description=(
            "The post text (max 10,000 chars). When is_rich_text=True, supports markdown: "
            "**bold**, *italic*, [label](url), and blank lines for paragraph breaks."
        )
    )
    is_rich_text: bool = Field(
        default=False,
        description=(
            "If True, the `text` is parsed as markdown and posted as Chatter rich text "
            "(bold, italic, hyperlinks, paragraphs). Default False (plain text)."
        ),
    )


class GetUserInfoInput(BaseModel):
    """Schema for getting the current user's info"""
    pass


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

    def _build_web_url(self, sobject: Optional[str], record_id: Optional[str]) -> Optional[str]:
        """Build a Salesforce Lightning web URL for a record."""
        if not self.instance_url or not record_id:
            return None
        if sobject:
            return f"{self.instance_url}/lightning/r/{sobject}/{record_id}/view"
        # Fallback: classic record URL (Salesforce will redirect to Lightning)
        return f"{self.instance_url}/{record_id}"

    def _enrich_with_web_urls(
        self, data: Any, default_sobject: Optional[str] = None
    ) -> Any:
        """Inject a webUrl field into records by inspecting attributes.type and Id.

        Handles:
        - SOQL/SOSL responses: {"records": [...], "totalSize": N}
        - SOSL searchRecords responses: {"searchRecords": [...]}
        - Single record dicts (e.g., from sobject_get): {"Id": "...", "attributes": {...}}
        - Create responses: {"id": "...", "success": true}
        """
        if not isinstance(data, dict):
            return data

        def _enrich_record(record: Dict[str, Any]) -> Dict[str, Any]:
            if not isinstance(record, dict):
                return record
            record_id = record.get("Id") or record.get("id")
            sobject = default_sobject
            attrs = record.get("attributes")
            if isinstance(attrs, dict) and attrs.get("type"):
                sobject = attrs["type"]
            web_url = self._build_web_url(sobject, record_id)
            if web_url:
                record["webUrl"] = web_url
            return record

        # SOQL/SOSL query response
        records = data.get("records")
        if isinstance(records, list):
            data["records"] = [_enrich_record(r) for r in records]

        # SOSL searchRecords response
        search_records = data.get("searchRecords")
        if isinstance(search_records, list):
            data["searchRecords"] = [_enrich_record(r) for r in search_records]

        # Single record (from sobject_get) — has "attributes" or matches default_sobject
        if "attributes" in data or (default_sobject and ("Id" in data or "id" in data)):
            _enrich_record(data)

        # Create response: {"id": "...", "success": true}
        if "id" in data and "success" in data and default_sobject:
            web_url = self._build_web_url(default_sobject, data.get("id"))
            if web_url:
                data["webUrl"] = web_url

        return data

    def _handle_response(
        self,
        response: SalesforceResponse,
        success_message: str,
        sobject: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Return a standardised (success, json_string) tuple."""
        if response.success:
            data = self._enrich_with_web_urls(response.data, default_sobject=sobject)
            return True, json.dumps(
                {"message": success_message, "data": data}, default=str
            )
        error = response.error or "Unknown error"
        return False, json.dumps({"error": error})

    def _build_soql_conditions(self, conditions: List[str]) -> str:
        """Build a WHERE clause from a list of conditions."""
        if not conditions:
            return ""
        return " WHERE " + " AND ".join(conditions)

    def _markdown_to_chatter_segments(self, text: str) -> List[Dict[str, Any]]:
        """Convert a small subset of markdown into Salesforce Chatter messageSegments.

        Supports:
        - **bold** / __bold__   → MarkupBegin/MarkupEnd type=Bold
        - *italic* / _italic_   → MarkupBegin/MarkupEnd type=Italic
        - [label](url)          → Hyperlink segment
        - blank line            → Paragraph break
        - newline               → preserved as text

        Anything that doesn't match falls through as plain Text.
        """
        if not text:
            return [{"type": "Text", "text": ""}]

        segments: List[Dict[str, Any]] = []
        # Split into paragraphs on blank lines so we can emit Paragraph markup between them.
        paragraphs = re.split(r"\n\s*\n", text)

        # Inline pattern: bold (**x** or __x__), italic (*x* or _x_), link [x](y)
        inline_re = re.compile(
            r"(\*\*([^*\n]+)\*\*|__([^_\n]+)__|\*([^*\n]+)\*|_([^_\n]+)_|\[([^\]]+)\]\(([^)]+)\))"
        )

        def _emit_text(s: str) -> None:
            if s:
                segments.append({"type": "Text", "text": s})

        for p_idx, paragraph in enumerate(paragraphs):
            if p_idx > 0:
                # Paragraph break between paragraphs
                segments.append({"type": "MarkupBegin", "markupType": "Paragraph"})
                segments.append({"type": "MarkupEnd", "markupType": "Paragraph"})

            pos = 0
            for m in inline_re.finditer(paragraph):
                _emit_text(paragraph[pos:m.start()])
                bold = m.group(2) or m.group(3)
                italic = m.group(4) or m.group(5)
                link_label = m.group(6)
                link_url = m.group(7)
                if bold:
                    segments.append({"type": "MarkupBegin", "markupType": "Bold"})
                    _emit_text(bold)
                    segments.append({"type": "MarkupEnd", "markupType": "Bold"})
                elif italic:
                    segments.append({"type": "MarkupBegin", "markupType": "Italic"})
                    _emit_text(italic)
                    segments.append({"type": "MarkupEnd", "markupType": "Italic"})
                elif link_label and link_url:
                    segments.append({
                        "type": "Hyperlink",
                        "url": link_url,
                        "text": link_label,
                    })
                pos = m.end()
            _emit_text(paragraph[pos:])

        if not segments:
            segments.append({"type": "Text", "text": text})
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
    async def soql_query(self, query: str) -> Tuple[bool, str]:
        """Execute a SOQL query."""
        try:
            logger.info("salesforce.soql_query called with query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, "SOQL query executed successfully")
        except Exception as e:
            logger.error(f"Error executing SOQL query: {e}")
            return False, json.dumps({"error": str(e)})

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
    async def sosl_search(self, search: str) -> Tuple[bool, str]:
        """Execute a SOSL search."""
        try:
            logger.info("salesforce.sosl_search called with search: %s", search)
            response = await self.client.sosl_search(
                api_version=self.api_version, q=search
            )
            return self._handle_response(response, "SOSL search executed successfully")
        except Exception as e:
            logger.error(f"Error executing SOSL search: {e}")
            return False, json.dumps({"error": str(e)})

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
        self, sobject: str, record_id: str, fields: Optional[str] = None
    ) -> Tuple[bool, str]:
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
            return self._handle_response(response, f"{sobject} record retrieved successfully", sobject=sobject)
        except Exception as e:
            logger.error(f"Error getting record: {e}")
            return False, json.dumps({"error": str(e)})

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
        self, sobject: str, data: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Create a new Salesforce record."""
        try:
            logger.info("salesforce.create_record called: sobject=%s", sobject)
            response = await self.client.sobject_create(
                api_version=self.api_version, sobject=sobject, data=data
            )
            return self._handle_response(response, f"{sobject} record created successfully", sobject=sobject)
        except Exception as e:
            logger.error(f"Error creating record: {e}")
            return False, json.dumps({"error": str(e)})

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
        self, sobject: str, record_id: str, data: Dict[str, Any]
    ) -> Tuple[bool, str]:
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
            return self._handle_response(response, f"{sobject} record updated successfully", sobject=sobject)
        except Exception as e:
            logger.error(f"Error updating record: {e}")
            return False, json.dumps({"error": str(e)})

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
    async def describe_object(self, sobject: str) -> Tuple[bool, str]:
        """Describe a Salesforce object's metadata."""
        try:
            logger.info("salesforce.describe_object called: sobject=%s", sobject)
            response = await self.client.s_object_describe(
                sobject_api_name=sobject, version=self.api_version
            )
            if response.success and response.data:
                # Trim to most useful fields to avoid huge payloads
                data = response.data
                if isinstance(data, dict):
                    trimmed = {
                        "name": data.get("name"),
                        "label": data.get("label"),
                        "labelPlural": data.get("labelPlural"),
                        "keyPrefix": data.get("keyPrefix"),
                        "createable": data.get("createable"),
                        "updateable": data.get("updateable"),
                        "deletable": data.get("deletable"),
                        "queryable": data.get("queryable"),
                        "searchable": data.get("searchable"),
                        "fields": [
                            {
                                "name": f.get("name"),
                                "label": f.get("label"),
                                "type": f.get("type"),
                                "required": not f.get("nillable", True) and f.get("createable", False),
                                "createable": f.get("createable"),
                                "updateable": f.get("updateable"),
                                "picklistValues": [
                                    {"value": pv.get("value"), "label": pv.get("label")}
                                    for pv in (f.get("picklistValues") or [])
                                    if pv.get("active")
                                ] if f.get("picklistValues") else None,
                            }
                            for f in (data.get("fields") or [])
                        ],
                    }
                    return True, json.dumps(
                        {"message": f"{sobject} described successfully", "data": trimmed},
                        default=str,
                    )
            return self._handle_response(response, f"{sobject} described successfully")
        except Exception as e:
            logger.error(f"Error describing object: {e}")
            return False, json.dumps({"error": str(e)})

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
    ) -> Tuple[bool, str]:
        """List recent records of a Salesforce object."""
        try:
            logger.info(
                "salesforce.list_recent_records called: sobject=%s, limit=%d", sobject, limit
            )
            query = f"SELECT Id, Name, LastModifiedDate FROM {sobject} ORDER BY LastModifiedDate DESC LIMIT {limit}"
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, f"Recent {sobject} records retrieved")
        except Exception as e:
            logger.error(f"Error listing recent records: {e}")
            return False, json.dumps({"error": str(e)})

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
            "List accounts matching 'Global'",
        ],
    )
    async def search_accounts(
        self,
        name: Optional[str] = None,
        industry: Optional[str] = None,
        limit: int = 10,
    ) -> Tuple[bool, str]:
        """Search for Salesforce accounts."""
        try:
            conditions: List[str] = []
            if name:
                conditions.append(f"Name LIKE '%{name}%'")
            if industry:
                conditions.append(f"Industry = '{industry}'")
            where = self._build_soql_conditions(conditions)
            query = f"SELECT Id, Name, Industry, Phone, Website, Type, BillingCity, BillingState, BillingCountry FROM Account{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
            logger.info("salesforce.search_accounts query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, "Accounts retrieved successfully")
        except Exception as e:
            logger.error(f"Error searching accounts: {e}")
            return False, json.dumps({"error": str(e)})

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
        industry: Optional[str] = None,
        phone: Optional[str] = None,
        website: Optional[str] = None,
        description: Optional[str] = None,
        billing_city: Optional[str] = None,
        billing_state: Optional[str] = None,
        billing_country: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Create a new Salesforce account."""
        try:
            data: Dict[str, Any] = {"Name": name}
            if industry:
                data["Industry"] = industry
            if phone:
                data["Phone"] = phone
            if website:
                data["Website"] = website
            if description:
                data["Description"] = description
            if billing_city:
                data["BillingCity"] = billing_city
            if billing_state:
                data["BillingState"] = billing_state
            if billing_country:
                data["BillingCountry"] = billing_country

            logger.info("salesforce.create_account called: name=%s", name)
            response = await self.client.sobject_create(
                api_version=self.api_version, sobject="Account", data=data
            )
            return self._handle_response(response, "Account created successfully", sobject="Account")
        except Exception as e:
            logger.error(f"Error creating account: {e}")
            return False, json.dumps({"error": str(e)})

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
        name: Optional[str] = None,
        email: Optional[str] = None,
        account_id: Optional[str] = None,
        limit: int = 10,
    ) -> Tuple[bool, str]:
        """Search for Salesforce contacts."""
        try:
            conditions: List[str] = []
            if name:
                conditions.append(f"Name LIKE '%{name}%'")
            if email:
                conditions.append(f"Email LIKE '%{email}%'")
            if account_id:
                conditions.append(f"AccountId = '{account_id}'")
            where = self._build_soql_conditions(conditions)
            query = f"SELECT Id, FirstName, LastName, Name, Email, Phone, Title, Department, Account.Name FROM Contact{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
            logger.info("salesforce.search_contacts query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, "Contacts retrieved successfully")
        except Exception as e:
            logger.error(f"Error searching contacts: {e}")
            return False, json.dumps({"error": str(e)})

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
        first_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        title: Optional[str] = None,
        account_id: Optional[str] = None,
        department: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Create a new Salesforce contact."""
        try:
            data: Dict[str, Any] = {"LastName": last_name}
            if first_name:
                data["FirstName"] = first_name
            if email:
                data["Email"] = email
            if phone:
                data["Phone"] = phone
            if title:
                data["Title"] = title
            if account_id:
                data["AccountId"] = account_id
            if department:
                data["Department"] = department

            logger.info("salesforce.create_contact called: last_name=%s", last_name)
            response = await self.client.sobject_create(
                api_version=self.api_version, sobject="Contact", data=data
            )
            return self._handle_response(response, "Contact created successfully", sobject="Contact")
        except Exception as e:
            logger.error(f"Error creating contact: {e}")
            return False, json.dumps({"error": str(e)})

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
        name: Optional[str] = None,
        company: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 10,
    ) -> Tuple[bool, str]:
        """Search for Salesforce leads."""
        try:
            conditions: List[str] = []
            if name:
                conditions.append(f"Name LIKE '%{name}%'")
            if company:
                conditions.append(f"Company LIKE '%{company}%'")
            if status:
                conditions.append(f"Status = '{status}'")
            where = self._build_soql_conditions(conditions)
            query = f"SELECT Id, FirstName, LastName, Name, Company, Email, Phone, Status, LeadSource, Title FROM Lead{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
            logger.info("salesforce.search_leads query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, "Leads retrieved successfully")
        except Exception as e:
            logger.error(f"Error searching leads: {e}")
            return False, json.dumps({"error": str(e)})

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
        first_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        title: Optional[str] = None,
        status: Optional[str] = None,
        industry: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Create a new Salesforce lead."""
        try:
            data: Dict[str, Any] = {"LastName": last_name, "Company": company}
            if first_name:
                data["FirstName"] = first_name
            if email:
                data["Email"] = email
            if phone:
                data["Phone"] = phone
            if title:
                data["Title"] = title
            if status:
                data["Status"] = status
            if industry:
                data["Industry"] = industry

            logger.info("salesforce.create_lead called: last_name=%s, company=%s", last_name, company)
            response = await self.client.sobject_create(
                api_version=self.api_version, sobject="Lead", data=data
            )
            return self._handle_response(response, "Lead created successfully", sobject="Lead")
        except Exception as e:
            logger.error(f"Error creating lead: {e}")
            return False, json.dumps({"error": str(e)})

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
        name: Optional[str] = None,
        stage: Optional[str] = None,
        account_id: Optional[str] = None,
        limit: int = 10,
    ) -> Tuple[bool, str]:
        """Search for Salesforce opportunities."""
        try:
            conditions: List[str] = []
            if name:
                conditions.append(f"Name LIKE '%{name}%'")
            if stage:
                conditions.append(f"StageName = '{stage}'")
            if account_id:
                conditions.append(f"AccountId = '{account_id}'")
            where = self._build_soql_conditions(conditions)
            query = f"SELECT Id, Name, StageName, Amount, CloseDate, Probability, Account.Name, Type FROM Opportunity{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
            logger.info("salesforce.search_opportunities query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, "Opportunities retrieved successfully")
        except Exception as e:
            logger.error(f"Error searching opportunities: {e}")
            return False, json.dumps({"error": str(e)})

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
        account_id: Optional[str] = None,
        amount: Optional[float] = None,
        description: Optional[str] = None,
        probability: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Create a new Salesforce opportunity."""
        try:
            data: Dict[str, Any] = {
                "Name": name,
                "StageName": stage_name,
                "CloseDate": close_date,
            }
            if account_id:
                data["AccountId"] = account_id
            if amount is not None:
                data["Amount"] = amount
            if description:
                data["Description"] = description
            if probability is not None:
                data["Probability"] = probability

            logger.info("salesforce.create_opportunity called: name=%s", name)
            response = await self.client.sobject_create(
                api_version=self.api_version, sobject="Opportunity", data=data
            )
            return self._handle_response(response, "Opportunity created successfully", sobject="Opportunity")
        except Exception as e:
            logger.error(f"Error creating opportunity: {e}")
            return False, json.dumps({"error": str(e)})

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
        subject: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        account_id: Optional[str] = None,
        limit: int = 10,
    ) -> Tuple[bool, str]:
        """Search for Salesforce cases."""
        try:
            conditions: List[str] = []
            if subject:
                conditions.append(f"Subject LIKE '%{subject}%'")
            if status:
                conditions.append(f"Status = '{status}'")
            if priority:
                conditions.append(f"Priority = '{priority}'")
            if account_id:
                conditions.append(f"AccountId = '{account_id}'")
            where = self._build_soql_conditions(conditions)
            query = f"SELECT Id, CaseNumber, Subject, Status, Priority, Origin, Description, Account.Name, Contact.Name FROM Case{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
            logger.info("salesforce.search_cases query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, "Cases retrieved successfully")
        except Exception as e:
            logger.error(f"Error searching cases: {e}")
            return False, json.dumps({"error": str(e)})

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
        status: Optional[str] = None,
        priority: Optional[str] = None,
        origin: Optional[str] = None,
        description: Optional[str] = None,
        account_id: Optional[str] = None,
        contact_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Create a new Salesforce case."""
        try:
            data: Dict[str, Any] = {"Subject": subject}
            if status:
                data["Status"] = status
            if priority:
                data["Priority"] = priority
            if origin:
                data["Origin"] = origin
            if description:
                data["Description"] = description
            if account_id:
                data["AccountId"] = account_id
            if contact_id:
                data["ContactId"] = contact_id

            logger.info("salesforce.create_case called: subject=%s", subject)
            response = await self.client.sobject_create(
                api_version=self.api_version, sobject="Case", data=data
            )
            return self._handle_response(response, "Case created successfully", sobject="Case")
        except Exception as e:
            logger.error(f"Error creating case: {e}")
            return False, json.dumps({"error": str(e)})

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
        name: Optional[str] = None,
        product_code: Optional[str] = None,
        family: Optional[str] = None,
        active_only: bool = True,
        limit: int = 10,
    ) -> Tuple[bool, str]:
        """Search for Salesforce products."""
        try:
            conditions: List[str] = []
            if name:
                conditions.append(f"Name LIKE '%{name}%'")
            if product_code:
                conditions.append(f"ProductCode LIKE '%{product_code}%'")
            if family:
                conditions.append(f"Family = '{family}'")
            if active_only:
                conditions.append("IsActive = true")
            where = self._build_soql_conditions(conditions)
            query = f"SELECT Id, Name, ProductCode, Description, Family, IsActive, QuantityUnitOfMeasure FROM Product2{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
            logger.info("salesforce.search_products query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, "Products retrieved successfully")
        except Exception as e:
            logger.error(f"Error searching products: {e}")
            return False, json.dumps({"error": str(e)})

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
        product_code: Optional[str] = None,
        description: Optional[str] = None,
        family: Optional[str] = None,
        is_active: bool = True,
        quantity_unit_of_measure: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Create a new Salesforce product."""
        try:
            data: Dict[str, Any] = {"Name": name, "IsActive": is_active}
            if product_code:
                data["ProductCode"] = product_code
            if description:
                data["Description"] = description
            if family:
                data["Family"] = family
            if quantity_unit_of_measure:
                data["QuantityUnitOfMeasure"] = quantity_unit_of_measure

            logger.info("salesforce.create_product called: name=%s", name)
            response = await self.client.sobject_create(
                api_version=self.api_version, sobject="Product2", data=data
            )
            return self._handle_response(response, "Product created successfully", sobject="Product2")
        except Exception as e:
            logger.error(f"Error creating product: {e}")
            return False, json.dumps({"error": str(e)})

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
        name: Optional[str] = None,
        active_only: bool = True,
        limit: int = 20,
    ) -> Tuple[bool, str]:
        """List Salesforce price books."""
        try:
            conditions: List[str] = []
            if name:
                conditions.append(f"Name LIKE '%{name}%'")
            if active_only:
                conditions.append("IsActive = true")
            where = self._build_soql_conditions(conditions)
            query = (
                f"SELECT Id, Name, Description, IsActive, IsStandard "
                f"FROM Pricebook2{where} ORDER BY IsStandard DESC, Name ASC LIMIT {limit}"
            )
            logger.info("salesforce.list_pricebooks query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, "Price books retrieved successfully")
        except Exception as e:
            logger.error(f"Error listing price books: {e}")
            return False, json.dumps({"error": str(e)})

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
        pricebook_entry_id: Optional[str] = None,
        product_id: Optional[str] = None,
        pricebook_id: Optional[str] = None,
        quantity: float = 1,
        unit_price: Optional[float] = None,
        description: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Add a product line item to a Salesforce opportunity."""
        try:
            if not pricebook_entry_id and not product_id:
                return False, json.dumps({
                    "error": "Either pricebook_entry_id or product_id must be provided",
                })

            resolved_unit_price = unit_price

            # Look up PricebookEntry if not directly provided
            if not pricebook_entry_id:
                effective_pricebook_id = pricebook_id
                if not effective_pricebook_id:
                    opp_query = (
                        f"SELECT Pricebook2Id FROM Opportunity WHERE Id = '{opportunity_id}' LIMIT 1"
                    )
                    opp_resp = await self.client.soql_query(
                        api_version=self.api_version, q=opp_query
                    )
                    try:
                        opp_data = opp_resp.data if hasattr(opp_resp, "data") else opp_resp
                        records = (opp_data or {}).get("records", []) if isinstance(opp_data, dict) else []
                        if records:
                            effective_pricebook_id = records[0].get("Pricebook2Id")
                    except Exception:
                        effective_pricebook_id = None
                    if not effective_pricebook_id:
                        return False, json.dumps({
                            "error": "Opportunity has no Pricebook2Id set; provide pricebook_id or set the opportunity's pricebook first",
                        })

                pbe_query = (
                    f"SELECT Id, UnitPrice FROM PricebookEntry "
                    f"WHERE Product2Id = '{product_id}' AND Pricebook2Id = '{effective_pricebook_id}' "
                    f"AND IsActive = true LIMIT 1"
                )
                logger.info("salesforce.add_product_to_opportunity pbe lookup: %s", pbe_query)
                pbe_resp = await self.client.soql_query(
                    api_version=self.api_version, q=pbe_query
                )
                try:
                    pbe_data = pbe_resp.data if hasattr(pbe_resp, "data") else pbe_resp
                    pbe_records = (pbe_data or {}).get("records", []) if isinstance(pbe_data, dict) else []
                except Exception:
                    pbe_records = []
                if not pbe_records:
                    return False, json.dumps({
                        "error": "No active PricebookEntry found for the given product and pricebook",
                    })
                pricebook_entry_id = pbe_records[0].get("Id")
                if resolved_unit_price is None:
                    resolved_unit_price = pbe_records[0].get("UnitPrice")

            data: Dict[str, Any] = {
                "OpportunityId": opportunity_id,
                "PricebookEntryId": pricebook_entry_id,
                "Quantity": quantity,
            }
            if resolved_unit_price is not None:
                data["UnitPrice"] = resolved_unit_price
            if description:
                data["Description"] = description

            logger.info(
                "salesforce.add_product_to_opportunity called: opp=%s pbe=%s qty=%s",
                opportunity_id, pricebook_entry_id, quantity,
            )
            response = await self.client.sobject_create(
                api_version=self.api_version, sobject="OpportunityLineItem", data=data
            )
            return self._handle_response(
                response, "Product added to opportunity successfully", sobject="OpportunityLineItem"
            )
        except Exception as e:
            logger.error(f"Error adding product to opportunity: {e}")
            return False, json.dumps({"error": str(e)})

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
        subject: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        owner_id: Optional[str] = None,
        what_id: Optional[str] = None,
        who_id: Optional[str] = None,
        limit: int = 10,
    ) -> Tuple[bool, str]:
        """Search for Salesforce tasks."""
        try:
            conditions: List[str] = []
            if subject:
                conditions.append(f"Subject LIKE '%{subject}%'")
            if status:
                conditions.append(f"Status = '{status}'")
            if priority:
                conditions.append(f"Priority = '{priority}'")
            if owner_id:
                conditions.append(f"OwnerId = '{owner_id}'")
            if what_id:
                conditions.append(f"WhatId = '{what_id}'")
            if who_id:
                conditions.append(f"WhoId = '{who_id}'")
            where = self._build_soql_conditions(conditions)
            query = (
                f"SELECT Id, Subject, Status, Priority, ActivityDate, Description, Type, "
                f"OwnerId, Owner.Name, WhatId, What.Name, WhoId, Who.Name "
                f"FROM Task{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
            )
            logger.info("salesforce.search_tasks query: %s", query)
            response = await self.client.soql_query(
                api_version=self.api_version, q=query
            )
            return self._handle_response(response, "Tasks retrieved successfully")
        except Exception as e:
            logger.error(f"Error searching tasks: {e}")
            return False, json.dumps({"error": str(e)})

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
        status: Optional[str] = None,
        priority: Optional[str] = None,
        activity_date: Optional[str] = None,
        description: Optional[str] = None,
        owner_id: Optional[str] = None,
        what_id: Optional[str] = None,
        who_id: Optional[str] = None,
        type: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Create a new Salesforce task."""
        try:
            data: Dict[str, Any] = {"Subject": subject}
            if status:
                data["Status"] = status
            if priority:
                data["Priority"] = priority
            if activity_date:
                data["ActivityDate"] = activity_date
            if description:
                data["Description"] = description
            if owner_id:
                data["OwnerId"] = owner_id
            if what_id:
                data["WhatId"] = what_id
            if who_id:
                data["WhoId"] = who_id
            if type:
                data["Type"] = type

            logger.info("salesforce.create_task called: subject=%s", subject)
            response = await self.client.sobject_create(
                api_version=self.api_version, sobject="Task", data=data
            )
            return self._handle_response(response, "Task created successfully", sobject="Task")
        except Exception as e:
            logger.error(f"Error creating task: {e}")
            return False, json.dumps({"error": str(e)})

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
    async def get_record_chatter(self, record_id: str) -> Tuple[bool, str]:
        """Fetch the Chatter feed for a Salesforce record."""
        try:
            logger.info("salesforce.get_record_chatter called: record_id=%s", record_id)
            response = await self.client.record_feed_elements(
                record_group_id=record_id, version=self.api_version
            )
            return self._handle_response(response, "Chatter feed retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting record chatter: {e}")
            return False, json.dumps({"error": str(e)})

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
    ) -> Tuple[bool, str]:
        """Post a comment on a Chatter feed item."""
        try:
            logger.info(
                "salesforce.post_chatter_comment called: feed_element_id=%s, rich=%s",
                feed_element_id, is_rich_text,
            )
            segments = (
                self._markdown_to_chatter_segments(text) if is_rich_text else None
            )
            response = await self.client.feed_elements_capability_comments_items(
                feed_element_id=feed_element_id,
                version=self.api_version,
                text=text,
                message_segments=segments,
            )
            return self._handle_response(response, "Chatter comment posted successfully")
        except Exception as e:
            logger.error(f"Error posting chatter comment: {e}")
            return False, json.dumps({"error": str(e)})

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
    ) -> Tuple[bool, str]:
        """Create a new Chatter post on a Salesforce record."""
        try:
            logger.info(
                "salesforce.post_chatter_to_record called: record_id=%s, rich=%s",
                record_id, is_rich_text,
            )
            segments = (
                self._markdown_to_chatter_segments(text) if is_rich_text else None
            )
            response = await self.client.feed_elements_post_and_search(
                version=self.api_version,
                feedelementtype="FeedItem",
                subjectid=record_id,
                text=text,
                message_segments=segments,
            )
            return self._handle_response(response, "Chatter post created successfully")
        except Exception as e:
            logger.error(f"Error posting chatter to record: {e}")
            return False, json.dumps({"error": str(e)})

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
    async def get_current_user(self) -> Tuple[bool, str]:
        """Get the current authenticated user's info."""
        try:
            logger.info("salesforce.get_current_user called")
            response = await self.client.get_user_info()
            return self._handle_response(response, "User info retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
            return False, json.dumps({"error": str(e)})
