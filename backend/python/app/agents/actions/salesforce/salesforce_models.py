"""Pydantic models and shared constants for the Salesforce agent toolset."""
from enum import Enum
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


# Default API version used across all tools
DEFAULT_API_VERSION = "59.0"

# SOQL templates: use .format(sobject=..., where=..., limit=..., etc.)
SOQL_LIST_RECENT_RECORDS = (
    "SELECT Id, Name, LastModifiedDate FROM {sobject} "
    "ORDER BY LastModifiedDate DESC LIMIT {limit}"
)
SOQL_SEARCH_ACCOUNTS = (
    "SELECT Id, Name, Industry, Phone, Website, Type, BillingCity, BillingState, BillingCountry "
    "FROM Account{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
)
SOQL_SEARCH_CONTACTS = (
    "SELECT Id, FirstName, LastName, Name, Email, Phone, Title, Department, Account.Name "
    "FROM Contact{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
)
SOQL_SEARCH_LEADS = (
    "SELECT Id, FirstName, LastName, Name, Company, Email, Phone, Status, LeadSource, Title "
    "FROM Lead{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
)
SOQL_SEARCH_OPPORTUNITIES = (
    "SELECT Id, Name, StageName, Amount, CloseDate, Probability, Account.Name, Type "
    "FROM Opportunity{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
)
SOQL_SEARCH_CASES = (
    "SELECT Id, CaseNumber, Subject, Status, Priority, Origin, Description, Account.Name, Contact.Name "
    "FROM Case{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
)
SOQL_SEARCH_PRODUCTS = (
    "SELECT Id, Name, ProductCode, Description, Family, IsActive, QuantityUnitOfMeasure "
    "FROM Product2{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
)
SOQL_LIST_PRICEBOOKS = (
    "SELECT Id, Name, Description, IsActive, IsStandard "
    "FROM Pricebook2{where} ORDER BY IsStandard DESC, Name ASC LIMIT {limit}"
)
SOQL_OPPORTUNITY_PRICEBOOK2_BY_ID = (
    "SELECT Pricebook2Id FROM Opportunity WHERE Id = '{opportunity_id}' LIMIT 1"
)
SOQL_PRICEBOOK_ENTRY_BY_PRODUCT_AND_BOOK = (
    "SELECT Id, UnitPrice FROM PricebookEntry "
    "WHERE Product2Id = '{product_id}' AND Pricebook2Id = '{pricebook_id}' "
    "AND IsActive = true LIMIT 1"
)
SOQL_SEARCH_TASKS = (
    "SELECT Id, Subject, Status, Priority, ActivityDate, Description, "
    "OwnerId, Owner.Name, WhatId, What.Name, WhoId, Who.Name "
    "FROM Task{where} ORDER BY LastModifiedDate DESC LIMIT {limit}"
)

SF_JSON_MESSAGE_KEY = "message"
SF_JSON_DATA_KEY = "data"
SF_JSON_ERROR_KEY = "error"

MSG_UNKNOWN_ERROR = "Unknown error"

SF_SOBJECT_ACCOUNT = "Account"
SF_SOBJECT_CONTACT = "Contact"
SF_SOBJECT_LEAD = "Lead"
SF_SOBJECT_OPPORTUNITY = "Opportunity"
SF_SOBJECT_CASE = "Case"
SF_SOBJECT_PRODUCT2 = "Product2"
SF_SOBJECT_OPPORTUNITY_LINE_ITEM = "OpportunityLineItem"
SF_SOBJECT_TASK = "Task"

MSG_SUCCESS = "Operation completed successfully"

ERR_NEED_PBE_OR_PRODUCT = "Either pricebook_entry_id or product_id must be provided"
ERR_OPP_NO_PRICEBOOK = (
    "Opportunity has no Pricebook2Id set; provide pricebook_id or set the opportunity's pricebook first"
)
ERR_NO_ACTIVE_PBE = "No active PricebookEntry found for the given product and pricebook"

# Salesforce API response keys
SF_KEY_RECORDS = "records"
SF_KEY_SEARCH_RECORDS = "searchRecords"
SF_KEY_ATTRIBUTES = "attributes"
SF_KEY_ID = "Id"
SF_KEY_ID_LOWER = "id"
SF_KEY_SUCCESS = "success"

# Salesforce field names used in lookups
SF_FIELD_PRICEBOOK2_ID = "Pricebook2Id"
SF_FIELD_UNIT_PRICE = "UnitPrice"

# Chatter constants
SF_CHATTER_FEED_ELEMENT_TYPE = "FeedItem"

# Error log message template
ERR_LOG = "Error in %s: %s"


# ---------------------------------------------------------------------------
# Chatter message-segment models
# ---------------------------------------------------------------------------


class ChatterMarkupType(str, Enum):
    """Supported Chatter markup types."""
    BOLD = "Bold"
    ITALIC = "Italic"
    PARAGRAPH = "Paragraph"
    UNORDERED_LIST = "UnorderedList"
    ORDERED_LIST = "OrderedList"
    LIST_ITEM = "ListItem"


class TextSegment(BaseModel):
    """A plain-text Chatter segment."""
    model_config = ConfigDict(extra="ignore")

    type: Literal["Text"] = "Text"
    text: str


class MarkupBeginSegment(BaseModel):
    """Opens a markup span (Bold, Italic, Paragraph)."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: Literal["MarkupBegin"] = "MarkupBegin"
    markupType: ChatterMarkupType = Field(alias="markupType")


class MarkupEndSegment(BaseModel):
    """Closes a markup span (Bold, Italic, Paragraph)."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: Literal["MarkupEnd"] = "MarkupEnd"
    markupType: ChatterMarkupType = Field(alias="markupType")


class HyperlinkSegment(BaseModel):
    """A hyperlink Chatter segment."""
    model_config = ConfigDict(extra="ignore")

    type: Literal["Hyperlink"] = "Hyperlink"
    url: str
    text: str


ChatterSegment = Union[
    TextSegment, MarkupBeginSegment, MarkupEndSegment, HyperlinkSegment
]

# ---------------------------------------------------------------------------
# Pydantic input schemas
# ---------------------------------------------------------------------------

class SOQLQueryInput(BaseModel):
    """Schema for executing a SOQL query"""
    model_config = ConfigDict(extra="ignore")

    query: str = Field(
        description="The SOQL query string to execute (e.g., \"SELECT Id, Name FROM Account LIMIT 10\")"
    )


class SOSLSearchInput(BaseModel):
    """Schema for executing a SOSL search"""
    model_config = ConfigDict(extra="ignore")

    search: str = Field(
        description="The SOSL search string (e.g., \"FIND {Acme} IN ALL FIELDS RETURNING Account(Id, Name), Contact(Id, Name)\")"
    )


class GetRecordInput(BaseModel):
    """Schema for retrieving a Salesforce record by ID"""
    model_config = ConfigDict(extra="ignore")

    sobject: str = Field(
        description="The Salesforce object API name (e.g., 'Account', 'Contact', 'Lead', 'Opportunity', 'Case')"
    )
    record_id: str = Field(description="The 15 or 18-character Salesforce record ID")
    fields: str | None = Field(
        default=None,
        description="Comma-separated list of fields to return (e.g., 'Id,Name,Email'). Omit to return all accessible fields.",
    )


class CreateRecordInput(BaseModel):
    """Schema for creating a Salesforce record"""
    model_config = ConfigDict(extra="ignore")

    sobject: str = Field(
        description="The Salesforce object API name (e.g., 'Account', 'Contact', 'Lead', 'Opportunity', 'Case')"
    )
    data: dict[str, Any] = Field( # Does not have fixed schema
        description="Field-value pairs for the new record (e.g., {\"Name\": \"Acme Corp\", \"Industry\": \"Technology\"})"
    )


class UpdateRecordInput(BaseModel):
    """Schema for updating a Salesforce record"""
    model_config = ConfigDict(extra="ignore")

    sobject: str = Field(
        description="The Salesforce object API name (e.g., 'Account', 'Contact', 'Lead', 'Opportunity', 'Case')"
    )
    record_id: str = Field(description="The 15 or 18-character Salesforce record ID")
    data: dict[str, Any] = Field( # Does not have fixed schema
        description="Field-value pairs to update (e.g., {\"Name\": \"New Name\", \"Phone\": \"555-1234\"})"
    )


class DescribeObjectInput(BaseModel):
    """Schema for describing a Salesforce object's metadata"""
    model_config = ConfigDict(extra="ignore")

    sobject: str = Field(
        description="The Salesforce object API name to describe (e.g., 'Account', 'Contact', 'Lead', 'Opportunity', 'Case')"
    )


class ListRecentRecordsInput(BaseModel):
    """Schema for listing recent records of a Salesforce object"""
    model_config = ConfigDict(extra="ignore")

    sobject: str = Field(
        description="The Salesforce object API name (e.g., 'Account', 'Contact', 'Lead', 'Opportunity', 'Case')"
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of recent records to return (default 10, max 50)",
    )


class SearchAccountsInput(BaseModel):
    """Schema for searching Salesforce accounts"""
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(
        default=None,
        description="Account name to search for (partial match supported)",
    )
    industry: str | None = Field(
        default=None,
        description="Filter by industry",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class SearchContactsInput(BaseModel):
    """Schema for searching Salesforce contacts"""
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(
        default=None,
        description="Contact name to search for (partial match supported)",
    )
    email: str | None = Field(
        default=None,
        description="Filter by email address",
    )
    account_id: str | None = Field(
        default=None,
        description="Filter by parent Account ID",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class SearchLeadsInput(BaseModel):
    """Schema for searching Salesforce leads"""
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(
        default=None,
        description="Lead name to search for (partial match supported)",
    )
    company: str | None = Field(
        default=None,
        description="Filter by company name",
    )
    status: str | None = Field(
        default=None,
        description="Filter by lead status (e.g., 'Open - Not Contacted', 'Working - Contacted', 'Closed - Converted')",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class SearchOpportunitiesInput(BaseModel):
    """Schema for searching Salesforce opportunities"""
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(
        default=None,
        description="Opportunity name to search for (partial match supported)",
    )
    stage: str | None = Field(
        default=None,
        description="Filter by stage name (e.g., 'Prospecting', 'Qualification', 'Closed Won')",
    )
    account_id: str | None = Field(
        default=None,
        description="Filter by parent Account ID",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class SearchCasesInput(BaseModel):
    """Schema for searching Salesforce cases"""
    model_config = ConfigDict(extra="ignore")

    subject: str | None = Field(
        default=None,
        description="Case subject to search for (partial match supported)",
    )
    status: str | None = Field(
        default=None,
        description="Filter by case status (e.g., 'New', 'Working', 'Escalated', 'Closed')",
    )
    priority: str | None = Field(
        default=None,
        description="Filter by priority (e.g., 'High', 'Medium', 'Low')",
    )
    account_id: str | None = Field(
        default=None,
        description="Filter by parent Account ID",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class CreateAccountInput(BaseModel):
    """Schema for creating a Salesforce account"""
    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="Account name (required)")
    industry: str | None = Field(default=None, description="Industry")
    phone: str | None = Field(default=None, description="Phone number")
    website: str | None = Field(default=None, description="Website URL")
    description: str | None = Field(default=None, description="Account description")
    billing_city: str | None = Field(default=None, description="Billing city")
    billing_state: str | None = Field(default=None, description="Billing state/province")
    billing_country: str | None = Field(default=None, description="Billing country")


class CreateContactInput(BaseModel):
    """Schema for creating a Salesforce contact"""
    model_config = ConfigDict(extra="ignore")

    last_name: str = Field(description="Last name (required)")
    first_name: str | None = Field(default=None, description="First name")
    email: str | None = Field(default=None, description="Email address")
    phone: str | None = Field(default=None, description="Phone number")
    title: str | None = Field(default=None, description="Job title")
    account_id: str | None = Field(default=None, description="Parent Account ID to associate this contact with")
    department: str | None = Field(default=None, description="Department")


class CreateLeadInput(BaseModel):
    """Schema for creating a Salesforce lead"""
    model_config = ConfigDict(extra="ignore")

    last_name: str = Field(description="Last name (required)")
    company: str = Field(description="Company name (required)")
    first_name: str | None = Field(default=None, description="First name")
    email: str | None = Field(default=None, description="Email address")
    phone: str | None = Field(default=None, description="Phone number")
    title: str | None = Field(default=None, description="Job title")
    status: str | None = Field(default=None, description="Lead status (e.g., 'Open - Not Contacted')")
    industry: str | None = Field(default=None, description="Industry")


class CreateOpportunityInput(BaseModel):
    """Schema for creating a Salesforce opportunity"""
    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="Opportunity name (required)")
    stage_name: str = Field(description="Stage name (required, e.g., 'Prospecting', 'Qualification', 'Needs Analysis')")
    close_date: str = Field(description="Expected close date in YYYY-MM-DD format (required)")
    account_id: str | None = Field(default=None, description="Parent Account ID")
    amount: float | None = Field(default=None, description="Opportunity amount")
    description: str | None = Field(default=None, description="Opportunity description")
    probability: float | None = Field(default=None, ge=0, le=100, description="Probability percentage (0-100)")


class CreateCaseInput(BaseModel):
    """Schema for creating a Salesforce case"""
    model_config = ConfigDict(extra="ignore")

    subject: str = Field(description="Case subject (required)")
    status: str | None = Field(default=None, description="Case status (e.g., 'New', 'Working', 'Escalated')")
    priority: str | None = Field(default=None, description="Priority (e.g., 'High', 'Medium', 'Low')")
    origin: str | None = Field(default=None, description="Case origin (e.g., 'Phone', 'Email', 'Web')")
    description: str | None = Field(default=None, description="Case description")
    account_id: str | None = Field(default=None, description="Parent Account ID")
    contact_id: str | None = Field(default=None, description="Associated Contact ID")


class SearchProductsInput(BaseModel):
    """Schema for searching Salesforce products"""
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(
        default=None,
        description="Product name to search for (partial match supported)",
    )
    product_code: str | None = Field(
        default=None,
        description="Filter by product code (SKU)",
    )
    family: str | None = Field(
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
    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="Product name (required)")
    product_code: str | None = Field(default=None, description="Product code / SKU")
    description: str | None = Field(default=None, description="Product description")
    family: str | None = Field(default=None, description="Product family")
    is_active: bool = Field(default=True, description="Whether the product is active. Default True.")
    quantity_unit_of_measure: str | None = Field(
        default=None,
        description="Unit of measure (e.g., 'Each', 'Hours', 'Kg')",
    )


class ListPricebooksInput(BaseModel):
    """Schema for listing Salesforce price books"""
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(
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
    model_config = ConfigDict(extra="ignore")

    opportunity_id: str = Field(
        description="The 15 or 18-character Salesforce Opportunity Id"
    )
    pricebook_entry_id: str | None = Field(
        default=None,
        description="The PricebookEntry Id for the product. If omitted, provide product_id (and optionally pricebook_id) and the tool will look it up.",
    )
    product_id: str | None = Field(
        default=None,
        description="The Product2 Id. Used to look up the PricebookEntry if pricebook_entry_id is not provided.",
    )
    pricebook_id: str | None = Field(
        default=None,
        description="The Pricebook2 Id to use when looking up a PricebookEntry from product_id. If omitted, the opportunity's pricebook is used.",
    )
    quantity: float = Field(
        default=1, gt=0,
        description="Quantity of the product (default 1)",
    )
    unit_price: float | None = Field(
        default=None,
        description="Unit sales price. If omitted, the PricebookEntry UnitPrice is used.",
    )
    description: str | None = Field(
        default=None,
        description="Optional line item description",
    )


class SearchTasksInput(BaseModel):
    """Schema for searching Salesforce tasks"""
    model_config = ConfigDict(extra="ignore")

    subject: str | None = Field(
        default=None,
        description="Task subject to search for (partial match supported)",
    )
    status: str | None = Field(
        default=None,
        description="Filter by task status (e.g., 'Not Started', 'In Progress', 'Completed', 'Deferred')",
    )
    priority: str | None = Field(
        default=None,
        description="Filter by priority (e.g., 'High', 'Normal', 'Low')",
    )
    owner_id: str | None = Field(
        default=None,
        description="Filter by task owner User ID",
    )
    what_id: str | None = Field(
        default=None,
        description="Filter by related record ID (Account, Opportunity, Case, etc.)",
    )
    who_id: str | None = Field(
        default=None,
        description="Filter by related Contact or Lead ID",
    )
    limit: int = Field(
        default=10, ge=1, le=50,
        description="Maximum number of results (default 10, max 50)",
    )


class CreateTaskInput(BaseModel):
    """Schema for creating a Salesforce task"""
    model_config = ConfigDict(extra="ignore")

    subject: str = Field(description="Task subject (required)")
    status: str | None = Field(
        default=None,
        description="Task status (e.g., 'Not Started', 'In Progress', 'Completed')",
    )
    priority: str | None = Field(
        default=None,
        description="Priority (e.g., 'High', 'Normal', 'Low')",
    )
    activity_date: str | None = Field(
        default=None,
        description="Due date in YYYY-MM-DD format",
    )
    description: str | None = Field(default=None, description="Task description / comments")
    owner_id: str | None = Field(
        default=None,
        description="User ID of the task owner. Defaults to the authenticated user.",
    )
    what_id: str | None = Field(
        default=None,
        description="Related record ID (Account, Opportunity, Case, etc.) the task is about",
    )
    who_id: str | None = Field(
        default=None,
        description="Related Contact or Lead ID the task is associated with",
    )


class GetRecordChatterInput(BaseModel):
    """Schema for fetching a Salesforce record's Chatter feed"""
    model_config = ConfigDict(extra="ignore")

    record_id: str = Field(
        description="The Salesforce record ID whose Chatter feed should be fetched (Account, Opportunity, Case, Contact, Lead, etc.)",
    )


class PostChatterCommentInput(BaseModel):
    """Schema for posting a comment/reply on a Chatter feed item"""
    model_config = ConfigDict(extra="ignore")

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
    model_config = ConfigDict(extra="ignore")

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
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Salesforce sObject data models (for create payloads)
# ---------------------------------------------------------------------------


class SalesforceObjectData(BaseModel):
    """Base class for Salesforce sObject create/update payloads.

    Subclasses declare fields using Python names;
    serialises them using the ``alias`` (= Salesforce API field name),
    dropping ``None`` values.
    """
    model_config = ConfigDict(extra="ignore", populate_by_name=True)



class AccountData(SalesforceObjectData):
    """Payload for creating / updating an Account."""
    name: str = Field(alias="Name")
    industry: str | None = Field(default=None, alias="Industry")
    phone: str | None = Field(default=None, alias="Phone")
    website: str | None = Field(default=None, alias="Website")
    description: str | None = Field(default=None, alias="Description")
    billing_city: str | None = Field(default=None, alias="BillingCity")
    billing_state: str | None = Field(default=None, alias="BillingState")
    billing_country: str | None = Field(default=None, alias="BillingCountry")


class ContactData(SalesforceObjectData):
    """Payload for creating / updating a Contact."""
    last_name: str = Field(alias="LastName")
    first_name: str | None = Field(default=None, alias="FirstName")
    email: str | None = Field(default=None, alias="Email")
    phone: str | None = Field(default=None, alias="Phone")
    title: str | None = Field(default=None, alias="Title")
    account_id: str | None = Field(default=None, alias="AccountId")
    department: str | None = Field(default=None, alias="Department")


class LeadData(SalesforceObjectData):
    """Payload for creating / updating a Lead."""
    last_name: str = Field(alias="LastName")
    company: str = Field(alias="Company")
    first_name: str | None = Field(default=None, alias="FirstName")
    email: str | None = Field(default=None, alias="Email")
    phone: str | None = Field(default=None, alias="Phone")
    title: str | None = Field(default=None, alias="Title")
    status: str | None = Field(default=None, alias="Status")
    industry: str | None = Field(default=None, alias="Industry")


class OpportunityData(SalesforceObjectData):
    """Payload for creating / updating an Opportunity."""
    name: str = Field(alias="Name")
    stage_name: str = Field(alias="StageName")
    close_date: str = Field(alias="CloseDate")
    account_id: str | None = Field(default=None, alias="AccountId")
    amount: float | None = Field(default=None, alias="Amount")
    description: str | None = Field(default=None, alias="Description")
    probability: float | None = Field(default=None, alias="Probability")


class CaseData(SalesforceObjectData):
    """Payload for creating / updating a Case."""
    subject: str = Field(alias="Subject")
    status: str | None = Field(default=None, alias="Status")
    priority: str | None = Field(default=None, alias="Priority")
    origin: str | None = Field(default=None, alias="Origin")
    description: str | None = Field(default=None, alias="Description")
    account_id: str | None = Field(default=None, alias="AccountId")
    contact_id: str | None = Field(default=None, alias="ContactId")


class ProductData(SalesforceObjectData):
    """Payload for creating / updating a Product2."""
    name: str = Field(alias="Name")
    product_code: str | None = Field(default=None, alias="ProductCode")
    description: str | None = Field(default=None, alias="Description")
    family: str | None = Field(default=None, alias="Family")
    is_active: bool = Field(default=True, alias="IsActive")
    quantity_unit_of_measure: str | None = Field(default=None, alias="QuantityUnitOfMeasure")


class TaskData(SalesforceObjectData):
    """Payload for creating / updating a Task."""
    subject: str = Field(alias="Subject")
    status: str | None = Field(default=None, alias="Status")
    priority: str | None = Field(default=None, alias="Priority")
    activity_date: str | None = Field(default=None, alias="ActivityDate")
    description: str | None = Field(default=None, alias="Description")
    owner_id: str | None = Field(default=None, alias="OwnerId")
    what_id: str | None = Field(default=None, alias="WhatId")
    who_id: str | None = Field(default=None, alias="WhoId")
    task_type: str | None = Field(default=None, alias="Type")


class OpportunityLineItemData(SalesforceObjectData):
    """Payload for creating an OpportunityLineItem."""
    opportunity_id: str = Field(alias="OpportunityId")
    pricebook_entry_id: str = Field(alias="PricebookEntryId")
    quantity: float = Field(alias="Quantity")
    unit_price: float | None = Field(default=None, alias="UnitPrice")
    description: str | None = Field(default=None, alias="Description")


# ---------------------------------------------------------------------------
# Salesforce record model
# ---------------------------------------------------------------------------


class SalesforceRecordAttributes(BaseModel):
    """The 'attributes' block returned by the Salesforce REST API."""
    model_config = ConfigDict(extra="allow")

    type: str | None = Field(default=None, description="sObject type name")
    url: str | None = Field(default=None, description="REST API resource URL")


class SalesforceRecord(BaseModel):
    """A single Salesforce record with common fields and dynamic extras.

    Captures well-known fields (Id, Name, attributes, webUrl) while
    accepting any sObject-specific fields via ``extra = "allow"``.
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    Id: str | None = Field(default=None, description="18-character Salesforce record ID")
    id: str | None = Field(default=None, description="Record ID (lowercase, used in create responses)")
    Name: str | None = Field(default=None, description="Record name")
    attributes: SalesforceRecordAttributes | None = Field(default=None, description="Salesforce metadata attributes")
    webUrl: str | None = Field(default=None, description="Salesforce Lightning web URL")

    @property
    def record_id(self) -> str | None:
        """Return whichever ID field is set."""
        return self.Id or self.id
