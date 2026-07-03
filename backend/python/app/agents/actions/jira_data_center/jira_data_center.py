"""Jira Data Center / Server toolset (standalone, REST v2).

Independent of the Cloud ``Jira`` action — it neither imports nor subclasses it,
so the two toolsets cannot break each other (mirrors the connector split
``jira_cloud`` vs ``jira_data_center``). It wraps the shared ``JiraDataSource``
and calls only its ``*_v2`` methods (REST v2), applying the Data Center deltas:

- bodies are plain/wiki-markup strings, NOT ADF;
- users are identified by ``name``/``key``, NOT ``accountId``;
- JQL search is ``POST /rest/api/2/search`` with ``startAt``/``maxResults`` offset paging;
- projects come back as a single array (no pagination);
- browse URLs use the configured instance ``baseUrl`` directly (no Cloud cloud-id proxy).
"""

import difflib
import json
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from app.agents.actions.response_transformer import ResponseTransformer
from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.connectors.core.registry.types import AuthField, DocumentationLink
from app.sources.client.http.exception.exception import HttpStatusCode
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.jira.jira import JiraClient
from app.sources.external.jira.jira import JiraDataSource

logger = logging.getLogger(__name__)

_APP = "jiradatacenter"

# Jira serializes some fields (dev-status "development" summary, greenhopper sprints) as Java
# toString dumps like "SummaryBean@742a6b3[...]" — pure noise for an LLM. This matches that shape.
_JAVA_BEAN_RE = re.compile(r"@[0-9a-f]{4,}\[")
# LexoRank ordering token (the "rank" system field), e.g. "0|i0004v:".
_LEXORANK_RE = re.compile(r"^0\|[a-z0-9]+:?$")


# ---------------------------------------------------------------------------
# Pydantic input schemas (self-contained — no import from the Cloud module)
# ---------------------------------------------------------------------------
class CreateIssueInput(BaseModel):
    """Schema for creating Jira Data Center issues"""
    project_key: str = Field(description="Project key (e.g., 'PA')")
    summary: str = Field(description="Issue summary")
    issue_type_name: str = Field(description="Issue type (e.g., 'Task', 'Bug', 'Story')")
    description: Optional[str] = Field(default=None, description="Issue description (plain text / wiki markup)")
    assignee_name: Optional[str] = Field(default=None, description="Assignee username (Data Center 'name')")
    assignee_query: Optional[str] = Field(default=None, description="Name or email to resolve the assignee's username")
    priority_name: Optional[str] = Field(default=None, description="Priority")
    labels: list[str] | None = Field(default=None, description="List of labels")
    components: list[str] | None = Field(default=None, description="List of component names")
    parent_key: str | None = Field(default=None, description="Parent issue key")
    custom_fields: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Required or optional project-specific fields as {field_id: value}. "
            "Call get_create_issue_fields first to discover required field IDs and value formats. "
            "Example: {'customfield_10016': 5, 'duedate': '2026-06-30'}"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def extract_nested_values(cls, data: dict) -> dict:
        if isinstance(data, dict):
            normalized = dict(data)
            for key in ["project", "projectKey", "project_key"]:
                if key in normalized and "project_key" not in normalized:
                    normalized["project_key"] = normalized[key]
                    if key != "project_key":
                        normalized.pop(key, None)
            if "issuetype" in normalized and isinstance(normalized["issuetype"], dict):
                if "name" in normalized["issuetype"]:
                    normalized["issue_type_name"] = normalized["issuetype"]["name"]
                elif "issue_type_name" not in normalized:
                    normalized["issue_type_name"] = str(normalized["issuetype"])
                normalized.pop("issuetype", None)
            elif "issue_type" in normalized and "issue_type_name" not in normalized:
                normalized["issue_type_name"] = normalized["issue_type"]
                normalized.pop("issue_type", None)
            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = "ignore"


class GetIssuesInput(BaseModel):
    """Schema for getting issues from a project"""
    project_key: str = Field(description="Project key (e.g., 'PA')")
    days: Optional[int] = Field(default=None, description="Days to look back")
    max_results: Optional[int] = Field(default=None, description="Max results")

    @model_validator(mode="before")
    @classmethod
    def extract_project_key(cls, data: dict) -> dict:
        if isinstance(data, dict):
            normalized = dict(data)
            for key in ["project", "projectKey", "project_key"]:
                if key in normalized and "project_key" not in normalized:
                    normalized["project_key"] = normalized[key]
                    if key != "project_key":
                        normalized.pop(key, None)
            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = "ignore"


class GetIssueInput(BaseModel):
    """Schema for getting a specific issue"""
    issue_key: str = Field(description="Issue key (e.g., 'PA-123')")

    @model_validator(mode="before")
    @classmethod
    def extract_issue_key(cls, data: dict) -> dict:
        if isinstance(data, dict):
            normalized = dict(data)
            for key in ["issueId", "issueIdOrKey", "issue_id", "issue_key", "issueKey"]:
                if key in normalized and "issue_key" not in normalized:
                    normalized["issue_key"] = normalized[key]
                    if key != "issue_key":
                        normalized.pop(key, None)
            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = "ignore"


class SearchIssuesInput(BaseModel):
    """Schema for searching issues using JQL"""
    jql: str = Field(description="JQL query with time filter")
    maxResults: Optional[int] = Field(default=50, description="Max results")


class AddCommentInput(BaseModel):
    """Schema for adding a comment"""
    issue_key: str = Field(description="Issue key")
    comment: str = Field(description="Comment text (plain text / wiki markup)")

    @model_validator(mode="before")
    @classmethod
    def extract_issue_key(cls, data: dict) -> dict:
        if isinstance(data, dict):
            normalized = dict(data)
            for key in ["issueId", "issueIdOrKey", "issue_id", "issue_key", "issueKey"]:
                if key in normalized and "issue_key" not in normalized:
                    normalized["issue_key"] = normalized[key]
                    if key != "issue_key":
                        normalized.pop(key, None)
            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = "ignore"


class SearchUsersInput(BaseModel):
    """Schema for searching users"""
    query: str = Field(description="Search query (name or email)")
    max_results: Optional[int] = Field(default=50, description="Max results")


class UpdateIssueInput(BaseModel):
    """Schema for updating a Jira Data Center issue"""
    issue_key: str = Field(description="Issue key (e.g., 'PA-123')")
    summary: Optional[str] = Field(default=None, description="Issue summary")
    description: Optional[str] = Field(default=None, description="Issue description (plain text / wiki markup)")
    issue_type_name: Optional[str] = Field(default=None, description="New issue type — call get_create_issue_fields first to discover required fields for the target type")
    assignee_name: Optional[str] = Field(default=None, description="Assignee username (Data Center 'name')")
    assignee_query: Optional[str] = Field(default=None, description="Name or email to resolve the assignee's username")
    reporter_name: Optional[str] = Field(default=None, description="Reporter username (Data Center 'name')")
    reporter_query: Optional[str] = Field(default=None, description="Name or email to resolve the reporter's username")
    priority_name: Optional[str] = Field(default=None, description="Priority")
    labels: list[str] | None = Field(default=None, description="List of labels")
    components: list[str] | None = Field(default=None, description="List of component names")
    status: str | None = Field(default=None, description="Issue status to transition to (e.g., 'In Progress', 'Done')")
    custom_fields: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Required or optional fields as {field_id: value}. Needed when changing issue type — "
            "call get_create_issue_fields first. Example: {'customfield_10016': 5, 'duedate': '2026-06-30'}"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def extract_nested_values(cls, data: dict) -> dict:
        if isinstance(data, dict):
            normalized = dict(data)
            for key in ["issueId", "issueIdOrKey", "issue_id", "issue_key", "issueKey"]:
                if key in normalized and "issue_key" not in normalized:
                    normalized["issue_key"] = normalized[key]
                    if key != "issue_key":
                        normalized.pop(key, None)
            for key in ["issuetype", "issue_type", "issueType"]:
                if key in normalized:
                    if "issue_type_name" not in normalized:
                        val = normalized[key]
                        normalized["issue_type_name"] = val.get("name", str(val)) if isinstance(val, dict) else str(val)
                    normalized.pop(key, None)
            if "fields" in normalized and isinstance(normalized["fields"], dict):
                fields = normalized["fields"]
                for f in ["summary", "description", "assignee_name", "assignee_query",
                          "reporter_name", "reporter_query", "priority_name", "labels",
                          "components", "status", "issue_type_name", "custom_fields"]:
                    if f in fields:
                        normalized[f] = fields[f]
                normalized.pop("fields", None)
            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = "ignore"


class GetProjectInput(BaseModel):
    """Schema for getting a specific project"""
    project_key: str = Field(description="Project key (e.g., 'PA')")


class GetProjectMetadataInput(BaseModel):
    """Schema for getting project metadata"""
    project_key: str = Field(description="Project key (e.g., 'PA')")

    @model_validator(mode="before")
    @classmethod
    def extract_project_key(cls, data: dict) -> dict:
        if isinstance(data, dict):
            normalized = dict(data)
            for key in ["project", "projectKey", "project_key"]:
                if key in normalized and "project_key" not in normalized:
                    normalized["project_key"] = normalized[key]
                    if key != "project_key":
                        normalized.pop(key, None)
            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = "ignore"


class GetCommentsInput(BaseModel):
    """Schema for getting comments on an issue"""
    issue_key: str = Field(description="Issue key (e.g., 'PA-123')")


class GetCreateIssueFieldsInput(BaseModel):
    """Schema for fetching create-issue field metadata for a project and issue type"""
    project_key: str = Field(description="Project key (e.g., 'PA')")
    issue_type_name: str = Field(description="Issue type name (e.g., 'Task', 'Bug', 'Story')")

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, data: dict) -> dict:
        if isinstance(data, dict):
            normalized = dict(data)
            for key in ["project", "projectKey", "project_key"]:
                if key in normalized:
                    if "project_key" not in normalized:
                        normalized["project_key"] = normalized[key]
                    if key != "project_key":
                        normalized.pop(key, None)
            for key in ["issueType", "issue_type", "issuetype"]:
                if key in normalized:
                    if "issue_type_name" not in normalized:
                        normalized["issue_type_name"] = normalized[key]
                    if key != "issue_type_name":
                        normalized.pop(key, None)
            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = "ignore"


@ToolsetBuilder("Jira Data Center")\
    .in_group("Atlassian")\
    .with_description(
        "Jira Data Center / Server integration for issue tracking and project "
    )\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        # Personal Access Token — apiToken only (no email) -> Bearer against baseUrl.
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="baseUrl",
                display_name="Base URL",
                placeholder="https://jira.yourcompany.com",
                description="The base URL of your Jira Data Center / Server instance",
                field_type="URL",
                required=True,
                usage="CONFIGURE",
                max_length=2000,
                is_secret=False,
            ),
            AuthField(
                name="apiToken",
                display_name="Personal Access Token",
                placeholder="your-personal-access-token",
                description="Personal Access Token from your Jira profile settings",
                field_type="PASSWORD",
                required=True,
                usage="AUTHENTICATE",
                max_length=2000,
                is_secret=True,
            ),
        ]),
        # Username + password -> HTTP Basic against baseUrl (older DC instances).
        AuthBuilder.type(AuthType.BASIC_AUTH).fields([
            AuthField(
                name="baseUrl",
                display_name="Base URL",
                placeholder="https://jira.yourcompany.com",
                description="The base URL of your Jira Data Center / Server instance",
                field_type="URL",
                required=True,
                usage="CONFIGURE",
                max_length=2000,
                is_secret=False,
            ),
            AuthField(
                name="username",
                display_name="Username",
                placeholder="your-username",
                description="Your Jira Data Center account username",
                field_type="TEXT",
                required=True,
                usage="AUTHENTICATE",
                max_length=500,
                is_secret=False,
            ),
            AuthField(
                name="password",
                display_name="Password",
                placeholder="your-password",
                description="Your Jira Data Center account password",
                field_type="PASSWORD",
                required=True,
                usage="AUTHENTICATE",
                max_length=2000,
                is_secret=True,
            ),
        ]),
    ])\
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("jira"))
        .add_documentation_link(DocumentationLink(
            "Jira Data Center REST API",
            "https://docs.atlassian.com/software/jira/docs/api/REST/latest/",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/jira/jira",
            "pipeshub",
        )))\
    .build_decorator()
class JiraDataCenter:
    """Jira Data Center / Server tool exposed to agents using ``JiraDataSource`` (v2)."""

    def __init__(self, client: JiraClient) -> None:
        self.client = JiraDataSource(client)
        self._site_url: Optional[str] = None
        self._field_schema_cache: Optional[dict[str, dict[str, str]]] = None
        self._create_fields_cache: dict[str, list[dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Response / error helpers
    # ------------------------------------------------------------------
    def _handle_response(
        self,
        response: HTTPResponse,
        success_message: str,
        include_guidance: bool = False,
    ) -> tuple[bool, str]:
        if response.status in [HttpStatusCode.SUCCESS.value, HttpStatusCode.CREATED.value, HttpStatusCode.NO_CONTENT.value]:
            try:
                data = response.json() if response.status != HttpStatusCode.NO_CONTENT.value else {}
                return True, json.dumps({"message": success_message, "data": data})
            except Exception as e:
                logger.error(f"Error parsing response: {e}")
                return True, json.dumps({"message": success_message, "data": {}})

        error_text = ""
        error_message = None
        error_details = None
        try:
            if response.is_json:
                error_data = response.json()
                if isinstance(error_data, dict):
                    error_message = (
                        error_data.get("error")
                        or error_data.get("message")
                        or (error_data.get("errorMessages", [None])[0]
                            if isinstance(error_data.get("errorMessages"), list) else None)
                    )
                    error_details = (
                        error_data.get("errors")
                        or error_data.get("errorMessages")
                        or error_data.get("details")
                    )
                    if error_message:
                        error_text = str(error_message)
                        if error_details and error_details != error_message:
                            error_text += f" - {error_details}"
                    else:
                        error_text = json.dumps(error_data)
                else:
                    error_text = str(error_data)
            else:
                error_text = response.text() if hasattr(response, "text") else str(response)
        except Exception as e:
            logger.debug(f"Error parsing error response: {e}")
            error_text = response.text() if hasattr(response, "text") else str(response)

        error_response: dict[str, object] = {
            "error": error_message or f"HTTP {response.status}",
            "status_code": response.status,
            "details": error_text,
        }
        if include_guidance:
            guidance = self._get_error_guidance(response.status)
            if guidance:
                error_response["guidance"] = guidance

        logger.error(f"HTTP error {response.status}: {error_text}")
        return False, json.dumps(error_response)

    def _get_error_guidance(self, status_code: int) -> Optional[str]:
        guidance_map = {
            HttpStatusCode.UNAUTHORIZED.value: (
                "Authentication failed. Check that the Personal Access Token (or username/password) "
                "is valid and not expired, and that the base URL points at the Jira Data Center instance."
            ),
            HttpStatusCode.FORBIDDEN.value: (
                "Access forbidden. The account may lack permission for this project/operation "
                "(e.g. 'Browse users' is required for user search)."
            ),
            HttpStatusCode.NOT_FOUND.value: (
                "Resource not found. Check the project key / issue key exists and the base URL is correct."
            ),
            HttpStatusCode.BAD_REQUEST.value: (
                "Bad request. Common causes: invalid JQL syntax, invalid field values, "
                "or missing required fields. For empty fields use 'IS EMPTY' rather than '='."
            ),
        }
        return guidance_map.get(status_code)

    # ------------------------------------------------------------------
    # Field / user / site helpers (Data Center specifics)
    # ------------------------------------------------------------------
    def _normalize_description(self, description: str) -> str:
        """Strip Slack mention markup; DC bodies are plain/wiki strings (no ADF)."""
        try:
            return re.compile(r"<@([A-Z0-9]+)>").sub(r"@\1", description)
        except Exception:
            return description

    def _validate_issue_fields(self, fields: dict[str, object]) -> tuple[bool, str]:
        """Validate issue fields for Data Center (plain-text bodies, name-based users)."""
        try:
            if not fields.get("project", {}).get("key"):
                return False, "Project key is required"
            if not fields.get("summary"):
                return False, "Summary is required"
            if not fields.get("issuetype", {}).get("name"):
                return False, "Issue type name is required"

            # Description stays a plain/wiki string on DC — reject ADF dicts.
            if fields.get("description") is not None and not isinstance(fields["description"], str):
                return False, "Description must be a plain-text string on Jira Data Center"

            for user_field in ("assignee", "reporter"):
                if fields.get(user_field):
                    val = fields[user_field]
                    if not isinstance(val, dict) or not val.get("name"):
                        return False, f"{user_field} must be a dict with a 'name' (username) field"

            if fields.get("priority"):
                if not isinstance(fields["priority"], dict) or not fields["priority"].get("name"):
                    return False, "Priority must be a dict with a 'name' field"

            if fields.get("components"):
                if not isinstance(fields["components"], list):
                    return False, "Components must be a list"
                for comp in fields["components"]:
                    if not isinstance(comp, dict) or not comp.get("name"):
                        return False, "Each component must be a dict with a 'name' field"

            return True, "Fields validation passed"
        except Exception as e:
            return False, f"Validation error: {e}"

    async def _resolve_user_to_name(self, project_key: str, query: str) -> Optional[str]:
        """Resolve a user query (name/email) to a Data Center username ('name')."""
        try:
            response = await self.client.find_assignable_users_v2(
                username=query, project=project_key, maxResults=1
            )
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                if isinstance(data, list) and data:
                    return data[0].get("name") or data[0].get("key")

            response = await self.client.get_user_search_v2(username=query, maxResults=1)
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                if isinstance(data, list) and data:
                    return data[0].get("name") or data[0].get("key")
        except Exception as e:
            logger.warning(f"Error resolving user to name: {e}")
        return None

    async def _get_site_url(self) -> Optional[str]:
        """Data Center browse URLs use the configured instance base URL directly."""
        if self._site_url:
            return self._site_url
        try:
            client_obj = self.client._client
            if hasattr(client_obj, "get_base_url"):
                base_url = client_obj.get_base_url()
                if base_url:
                    self._site_url = base_url.rstrip("/")
                    return self._site_url
        except Exception as e:
            logger.warning("Could not get site URL: %s", e)
        return None

    def _normalize_field_name(self, field_name: str) -> str:
        if not field_name:
            return field_name
        normalized = re.sub(r"[^\w\s-]", "", field_name)
        normalized = re.sub(r"[\s-]+", "_", normalized)
        return normalized.lower().strip("_")

    async def _fetch_and_cache_field_schema(self) -> dict[str, dict[str, str]]:
        if self._field_schema_cache is not None:
            return self._field_schema_cache
        try:
            response = await self.client.get_fields_v2()
            if response.status == HttpStatusCode.SUCCESS.value:
                fields_data = response.json()
                if not isinstance(fields_data, list):
                    self._field_schema_cache = {}
                    return self._field_schema_cache
                field_map: dict[str, dict[str, str]] = {}
                for field in fields_data:
                    field_id = field.get("id")
                    field_name = field.get("name", "")
                    if field_id and field_name:
                        field_map[field_id] = {
                            "name": field_name,
                            "normalized": self._normalize_field_name(field_name),
                        }
                self._field_schema_cache = field_map
                logger.info(f"Cached {len(field_map)} Jira DC field mappings")
                return self._field_schema_cache
            logger.warning(f"Failed to fetch field schema: HTTP {response.status}")
        except Exception as e:
            logger.error(f"Error fetching field schema: {e}")
        self._field_schema_cache = {}
        return self._field_schema_cache

    def _parse_allowed_values(self, raw: list[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for av in raw[:20]:
            if not isinstance(av, dict):
                continue
            entry: dict[str, Any] = {}
            if av.get("id"):
                entry["id"] = str(av["id"])
            label = av.get("name") or av.get("value")
            if label:
                entry["name"] = str(label)
            if entry:
                result.append(entry)
        return result

    async def _fetch_issue_types(self, project_key: str) -> list[dict[str, Any]]:
        """Paginated create-meta issue types for a project.

        The DC createmeta issue-types endpoint is paged (startAt/maxResults/total);
        fetching only the first page would report a valid type as "not found" on
        projects with more issue types than the server page size.
        """
        out: list[dict[str, Any]] = []
        start_at = 0
        page_size = 50
        while True:
            try:
                r = await self.client.get_create_meta_issue_types_v2(
                    projectIdOrKey=project_key, startAt=start_at, maxResults=page_size,
                )
            except Exception as e:
                logger.warning(f"Error fetching issue types for {project_key}: {e}")
                break
            if r.status != HttpStatusCode.SUCCESS.value:
                logger.warning(f"createmeta issue types HTTP {r.status} for {project_key}")
                break
            try:
                data = r.json()
            except Exception:
                break
            page = data.get("issueTypes", data.get("values", []))
            if not isinstance(page, list):
                break
            out.extend(page)
            fetched = start_at + len(page)
            total = data.get("total", 0)
            if not page or fetched >= total:
                break
            start_at = fetched
        return out

    async def _fetch_create_fields(
        self, project_key: str, issue_type_name: str
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        """Fetch create-field metadata for a project + issue type via the v2 createmeta endpoints (DC 9.0+)."""
        cache_key = f"{project_key.upper()}:{issue_type_name.lower()}"
        if cache_key in self._create_fields_cache:
            return self._create_fields_cache[cache_key], None

        issue_type_id: Optional[str] = None
        available_types: list[str] = []
        issue_types_raw = await self._fetch_issue_types(project_key)
        for it in issue_types_raw:
            name = it.get("name", "")
            if name:
                available_types.append(name)
            if issue_type_id is None and name.lower() == issue_type_name.lower():
                issue_type_id = it.get("id")

        if not issue_type_id and available_types:
            fuzzy = difflib.get_close_matches(
                issue_type_name.lower(), [n.lower() for n in available_types], n=1, cutoff=0.6
            )
            if fuzzy:
                for it in issue_types_raw:
                    if it.get("name", "").lower() == fuzzy[0]:
                        issue_type_id = it.get("id")
                        break

        if not issue_type_id:
            msg = f"Issue type '{issue_type_name}' not found in project '{project_key}'."
            if available_types:
                msg += f" Available: {', '.join(available_types)}"
            return [], msg

        fields_by_id: dict[str, dict[str, Any]] = {}
        start_at = 0
        page_size = 50
        while True:
            try:
                response = await self.client.get_create_meta_field_options_v2(
                    projectIdOrKey=project_key,
                    issueTypeId=issue_type_id,
                    startAt=start_at,
                    maxResults=page_size,
                )
            except Exception as e:
                logger.error(f"createmeta fields error for {project_key}/{issue_type_name}: {e}")
                break
            if response.status != HttpStatusCode.SUCCESS.value:
                logger.warning(f"createmeta HTTP {response.status} for {project_key}/{issue_type_name}")
                break
            try:
                data = response.json()
            except Exception:
                break
            page_fields = data.get("values", data.get("fields", []))
            if not isinstance(page_fields, list):
                break
            for f in page_fields:
                field_id = f.get("fieldId") or f.get("key") or f.get("id")
                if not field_id:
                    continue
                meta_schema = f.get("schema") or {}
                fields_by_id[field_id] = {
                    "field_id": field_id,
                    "name": f.get("name", field_id),
                    "required": bool(f.get("required", False)),
                    "schema_type": meta_schema.get("type", "string"),
                    "allowed_values": self._parse_allowed_values(f.get("allowedValues") or []),
                    "has_default_value": bool(f.get("hasDefaultValue", False)),
                    "operations": f.get("operations") or [],
                }
            fetched = start_at + len(page_fields)
            total = data.get("total", 0)
            if not page_fields or fetched >= total:
                break
            start_at = fetched

        fields = list(fields_by_id.values())
        self._create_fields_cache[cache_key] = fields
        return fields, None

    @staticmethod
    def _parse_bean(value: str) -> Optional[dict[str, Any]]:
        """Pull the readable fields out of a Jira/greenhopper Java toString dump (e.g. a sprint)."""
        out: dict[str, Any] = {}
        for key in ("name", "state", "startDate", "endDate", "goal"):
            m = re.search(rf"\b{key}=([^,\]]*)", value)
            if m:
                v = m.group(1).strip()
                if v and v != "<null>":
                    out[key] = v
        return out or None

    def _clean_issue_fields(self, issue: dict[str, Any]) -> dict[str, Any]:
        """Trim system/empty bloat and simplify nested objects on an issue."""
        if not isinstance(issue, dict) or "fields" not in issue:
            return issue
        fields = issue["fields"]
        if not isinstance(fields, dict):
            return issue

        cleaned_issue = dict(issue)
        cleaned_fields = dict(fields)
        fields_to_remove: list[str] = []
        fields_to_simplify: dict[str, Any] = {}

        for field_key, field_value in cleaned_fields.items():
            if field_key.startswith("customfield_") and field_value is None:
                fields_to_remove.append(field_key)
            elif isinstance(field_value, list) and len(field_value) == 0:
                fields_to_remove.append(field_key)
            elif field_value == "":
                fields_to_remove.append(field_key)
            elif isinstance(field_value, str) and _JAVA_BEAN_RE.search(field_value):
                # Serialized Java bean dump (e.g. the dev-status "development" field) — pure noise.
                fields_to_remove.append(field_key)
            elif isinstance(field_value, str) and _LEXORANK_RE.match(field_value):
                # LexoRank ordering token ("rank") — system metadata.
                fields_to_remove.append(field_key)
            elif (isinstance(field_value, list) and field_value
                    and all(isinstance(x, str) and _JAVA_BEAN_RE.search(x) for x in field_value)):
                # List of serialized beans (e.g. sprint) — keep only the readable fields.
                parsed = [p for p in (self._parse_bean(s) for s in field_value) if p]
                if parsed:
                    fields_to_simplify[field_key] = parsed
                else:
                    fields_to_remove.append(field_key)
            elif field_key == "project" and isinstance(field_value, dict):
                fields_to_simplify[field_key] = {"key": field_value.get("key"), "name": field_value.get("name")}
            elif field_key in ("status", "priority", "issuetype") and isinstance(field_value, dict):
                fields_to_simplify[field_key] = {"name": field_value.get("name"), "id": field_value.get("id")}
            elif field_key in ("assignee", "reporter", "creator") and isinstance(field_value, dict):
                simplified = {}
                # DC users: name/key (no accountId)
                for k in ("name", "key", "displayName", "emailAddress"):
                    if field_value.get(k):
                        simplified[k] = field_value.get(k)
                if simplified:
                    fields_to_simplify[field_key] = simplified
                else:
                    fields_to_remove.append(field_key)
            elif field_key == "parent" and isinstance(field_value, dict):
                parent_fields = field_value.get("fields", {})
                fields_to_simplify[field_key] = {
                    "key": field_value.get("key"),
                    "id": field_value.get("id"),
                    "summary": parent_fields.get("summary") if isinstance(parent_fields, dict) else None,
                }
            elif field_key == "comment" and isinstance(field_value, dict):
                # Full comment threads bloat the payload; keep only the most recent few
                # (use get_comments for the complete thread) and drop pagination noise.
                comments = field_value.get("comments")
                if isinstance(comments, list) and comments:
                    trimmed = []
                    for c in comments[-3:]:
                        if not isinstance(c, dict):
                            continue
                        author = c.get("author") or {}
                        entry: dict[str, Any] = {"body": c.get("body")}
                        for k in ("id", "created"):
                            if c.get(k):
                                entry[k] = c.get(k)
                        who = {k: author.get(k) for k in ("name", "key", "displayName") if author.get(k)}
                        if who:
                            entry["author"] = who
                        trimmed.append(entry)
                    fields_to_simplify[field_key] = {
                        "comments": trimmed,
                        "total": field_value.get("total", len(comments)),
                    }
                else:
                    fields_to_remove.append(field_key)
            elif field_key == "attachment" and isinstance(field_value, list):
                fields_to_simplify[field_key] = [
                    {k: att.get(k) for k in ("id", "filename", "size", "mimeType", "created") if att.get(k) is not None}
                    for att in field_value if isinstance(att, dict)
                ]

        for field_key in fields_to_remove:
            cleaned_fields.pop(field_key, None)
        for field_key, simplified_value in fields_to_simplify.items():
            cleaned_fields[field_key] = simplified_value

        cleaned_issue["fields"] = cleaned_fields
        return cleaned_issue

    @staticmethod
    def _apply_field_aliases(fields: dict[str, Any], field_schema: dict[str, dict[str, str]]) -> None:
        """Rename each populated ``customfield_XXXXX`` to its readable name, replacing (not
        duplicating) the raw key so custom fields aren't carried twice in the payload."""
        for field_id, field_info in field_schema.items():
            if field_id in fields and fields[field_id] is not None:
                normalized = field_info.get("normalized")
                if normalized and normalized != field_id and normalized not in fields:
                    fields[normalized] = fields.pop(field_id)

    async def _normalize_issues_in_response(
        self, response_data: dict[str, Any], field_schema: dict[str, dict[str, str]]
    ) -> dict[str, Any]:
        if not isinstance(response_data, dict):
            return response_data
        normalized = dict(response_data)
        if "issues" in normalized and isinstance(normalized["issues"], list):
            for issue in normalized["issues"]:
                if isinstance(issue.get("fields"), dict):
                    self._apply_field_aliases(issue["fields"], field_schema)
        elif isinstance(normalized.get("fields"), dict):
            self._apply_field_aliases(normalized["fields"], field_schema)
        return normalized

    def _add_urls_to_issue_references(self, issue: dict[str, Any], site_url: Optional[str]) -> None:
        if not site_url or not isinstance(issue, dict):
            return
        fields = issue.get("fields", {})
        if not isinstance(fields, dict):
            return

        # Only genuine issue references (e.g. "PA-123") get a /browse URL. On Data Center a
        # user's key is its username ("jdoe") and a project's key is "PA" — those must NOT be
        # stamped with /browse links, which the old "any dict with a key" check did.
        issue_key_re = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")

        def add_url(issue_ref: object) -> None:
            if isinstance(issue_ref, dict):
                k = issue_ref.get("key")
                if isinstance(k, str) and issue_key_re.match(k):
                    issue_ref["url"] = f"{site_url}/browse/{k}"

        parent = fields.get("parent")
        if parent:
            add_url(parent)
        for field_key, field_value in fields.items():
            if field_key in ("parent", "key", "id", "self", "url"):
                continue
            if isinstance(field_value, dict) and isinstance(field_value.get("key"), str):
                add_url(field_value)
            elif isinstance(field_value, list):
                for item in field_value:
                    if isinstance(item, dict) and isinstance(item.get("key"), str):
                        add_url(item)

    def _validate_and_fix_jql(self, jql: str) -> tuple[str, str | None]:
        if not jql:
            return jql, None
        original_jql = jql
        fixed_jql = jql
        warnings: list[str] = []

        resolution_pattern = re.compile(r'\bresolution\s*=\s*["\']?unresolved["\']?', re.IGNORECASE)
        if resolution_pattern.search(fixed_jql):
            fixed_jql = resolution_pattern.sub("resolution IS EMPTY", fixed_jql)
            warnings.append("Fixed 'resolution = Unresolved' to 'resolution IS EMPTY'")

        current_user_pattern = re.compile(r"\bassignee\s*=\s*currentUser\b(?!\()", re.IGNORECASE)
        if current_user_pattern.search(fixed_jql):
            fixed_jql = current_user_pattern.sub("assignee = currentUser()", fixed_jql)
            warnings.append("Fixed 'currentUser' to 'currentUser()'")

        warning_msg = "; ".join(warnings) if warnings else None
        if fixed_jql != original_jql:
            logger.info(f"JQL auto-fixed: '{original_jql}' -> '{fixed_jql}'")
        return fixed_jql, warning_msg

    def _comment_url(self, site_url: str, issue_key: str, comment_id: str) -> str:
        """Deep-link/permalink to a specific comment (Jira DC/Server ``focusedCommentId`` format)."""
        return (
            f"{site_url}/browse/{issue_key}?focusedCommentId={comment_id}"
            "&page=com.atlassian.jira.plugin.system.issuetabpanels%3Acomment-tabpanel"
            f"#comment-{comment_id}"
        )

    async def _run_search(self, jql: str, max_results: Optional[int]) -> HTTPResponse:
        """POST /rest/api/2/search — offset paging, single page for interactive tool use."""
        return await self.client.search_issues_post_v2(
            jql=jql,
            startAt=0,
            maxResults=max_results or 50,
            fields=["*all"],
        )

    def _clean_search_response(self, data: dict[str, Any]) -> dict[str, Any]:
        cleaned_data = (
            ResponseTransformer(data)
            .remove("expand", "self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                    "*.subtask", "*.avatarId", "*.hierarchyLevel",
                    "*.statusCategory", "*.active", "*.timeZone", "*.locale", "*.accountType",
                    "*.properties", "*._links", "*.watches", "*.votes", "*.worklog",
                    "*.progress", "*.aggregateprogress", "*.aggregatetimeestimate",
                    "*.aggregatetimespent", "*.workratio", "*.lastViewed", "*.security",
                    "*.watchCount", "*.isWatching", "*.hasVoted", "*.startAt", "*.maxResults", "*.total",
                    "*.statuscategorychangedate", "*.status_category_changed",
                    "*.aggregatetimeoriginalestimate", "*.timeestimate", "*.timeoriginalestimate",
                    "*.timespent", "*.rank", "*.environment", "*.fixVersions", "*.versions",
                    "*.issuelinks", "*.subtasks", "*.organizations", "*.request_participants",
                    "*.responders", "*.projectTypeKey", "*.simplified", "*.description", "*.id")
            .clean()
        )
        if isinstance(cleaned_data, dict) and "issues" in cleaned_data:
            cleaned_data["issues"] = [self._clean_issue_fields(issue) for issue in cleaned_data["issues"]]
            for field in ["nextPageToken", "next_cursor", "isLast", "total", "startAt", "maxResults"]:
                cleaned_data.pop(field, None)
        return cleaned_data

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------
    @tool(
        app_name=_APP,
        tool_name="validate_connection",
        description="Validate the Jira Data Center connection and provide diagnostics",
        parameters=[],
        returns="Connection validation status with diagnostics",
        primary_intent=ToolIntent.UTILITY,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def validate_connection(self) -> tuple[bool, str]:
        try:
            response = await self.client.get_current_user_v2()
            if response.status == HttpStatusCode.SUCCESS.value:
                user = response.json()
                return True, json.dumps({
                    "message": "Jira Data Center connection is valid",
                    "user": {
                        "name": user.get("name"),
                        "key": user.get("key"),
                        "displayName": user.get("displayName"),
                        "emailAddress": user.get("emailAddress"),
                    },
                })
            return self._handle_response(response, "Connection validated", include_guidance=True)
        except Exception as e:
            logger.error(f"Error validating Jira DC connection: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_current_user",
        description="Get the current authenticated Jira Data Center user's details (name, key, displayName, email)",
        parameters=[],
        returns="Current user's account details",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def get_current_user(self) -> tuple[bool, str]:
        try:
            response = await self.client.get_current_user_v2()
            if response.status == HttpStatusCode.SUCCESS.value:
                user = response.json()
                return True, json.dumps({
                    "message": "Current user fetched successfully",
                    "data": {
                        "name": user.get("name"),
                        "key": user.get("key"),
                        "displayName": user.get("displayName"),
                        "emailAddress": user.get("emailAddress"),
                    },
                })
            return self._handle_response(response, "Current user fetched", include_guidance=True)
        except Exception as e:
            logger.error(f"Error getting current user: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_create_issue_fields",
        description="Get the fields required to create an issue for a project and issue type",
        args_schema=GetCreateIssueFieldsInput,
        returns="Required and optional fields with IDs, types, value formats, and allowed values",
        llm_description=(
            "Returns all fields (required and optional) for a specific issue type in a Jira Data Center "
            "project. Call this before create_issue, and before update_issue when changing the issue type."
        ),
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def get_create_issue_fields(self, project_key: str, issue_type_name: str) -> tuple[bool, str]:
        try:
            fields, error_msg = await self._fetch_create_fields(project_key, issue_type_name)
            if error_msg:
                return False, json.dumps({
                    "error": error_msg,
                    "guidance": (
                        "Verify the project key and issue type name. Use get_projects to list projects "
                        "and get_project_metadata to list issue types for a project."
                    ),
                })

            internally_managed = {"issuetype", "project"}
            standard_field_ids = {
                "summary", "description", "assignee", "reporter", "priority",
                "labels", "components", "issuetype", "project", "parent",
            }
            always_skip = {"attachment", "worklog", "issuelinks", "subtasks", "watches", "votes"}

            required_fields = [
                f for f in fields
                if f["required"] and f["field_id"] not in internally_managed
                and f["field_id"] not in always_skip
                and not (f["field_id"] == "reporter" and f.get("has_default_value"))
            ]
            optional_fields = [
                f for f in fields
                if not f["required"] and f["field_id"] not in internally_managed
                and f["field_id"] not in always_skip
            ]

            def format_hint(f: dict[str, Any]) -> str:
                t = f["schema_type"]
                avs = f.get("allowed_values") or []
                if avs:
                    first_id = avs[0].get("id", "") if avs else ""
                    return f"object with 'id' from allowed_values, e.g. {{'id': '{first_id}'}}"
                if t == "number":
                    return "numeric value, e.g. 5"
                if t == "date":
                    return "ISO date string, e.g. '2026-06-30'"
                if t == "datetime":
                    return "ISO datetime string, e.g. '2026-06-30T10:00:00.000+0000'"
                if t == "array":
                    return "list — e.g. [{'id': '...'}] for option arrays or ['tag1'] for string arrays"
                if t == "user":
                    return "use assignee_name named parameter; or {'name': '<username>'}"
                return "string value"

            def clean_field(f: dict[str, Any]) -> dict[str, Any]:
                out: dict[str, Any] = {
                    "field_id": f["field_id"],
                    "name": f["name"],
                    "schema_type": f["schema_type"],
                    "value_format": format_hint(f),
                    "pass_via": "named_parameter" if f["field_id"] in standard_field_ids else "custom_fields",
                }
                avs = f.get("allowed_values") or []
                if avs:
                    out["allowed_values"] = avs
                if f.get("has_default_value"):
                    out["has_default_value"] = True
                    out["note"] = "Jira will auto-fill this field if not provided"
                return out

            return True, json.dumps({
                "message": "Field metadata fetched successfully",
                "project_key": project_key,
                "issue_type_name": issue_type_name,
                "instructions": (
                    "Pass standard fields (summary, description, assignee_name, priority_name, labels, "
                    "components, parent_key) as named parameters to create_issue. Pass ALL other fields — "
                    "especially required ones — via the custom_fields dict keyed by field_id."
                ),
                "required_fields": [clean_field(f) for f in required_fields],
                "optional_fields": [clean_field(f) for f in optional_fields],
            })
        except Exception as e:
            logger.error(f"Error getting create issue fields: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="create_issue",
        description="Create a new Jira Data Center issue",
        args_schema=CreateIssueInput,
        returns="Created issue key and details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type_name: str,
        description: Optional[str] = None,
        assignee_name: Optional[str] = None,
        assignee_query: Optional[str] = None,
        priority_name: str | None = None,
        labels: list[str] | None = None,
        components: list[str] | None = None,
        parent_key: str | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        try:
            fields: dict[str, object] = {
                "project": {"key": project_key},
                "summary": summary,
                "issuetype": {"name": issue_type_name},
            }
            if assignee_query and not assignee_name:
                assignee_name = await self._resolve_user_to_name(project_key, assignee_query)
            if description:
                fields["description"] = self._normalize_description(description)
            if assignee_name:
                fields["assignee"] = {"name": assignee_name}
            if priority_name:
                fields["priority"] = {"name": priority_name}
            if labels:
                fields["labels"] = labels
            if components:
                fields["components"] = [{"name": comp} for comp in components]
            if parent_key:
                fields["parent"] = {"key": parent_key}
            if custom_fields:
                for field_id, field_value in custom_fields.items():
                    if field_id not in fields:
                        fields[field_id] = field_value

            is_valid, validation_msg = self._validate_issue_fields(fields)
            if not is_valid:
                return False, json.dumps({
                    "error": "Field validation failed",
                    "validation_error": validation_msg,
                    "fields": fields,
                })

            response = await self.client.create_issue_v2(fields=fields)

            assignee_dropped = False
            if response.status == HttpStatusCode.BAD_REQUEST.value:
                try:
                    errors = response.json().get("errors", {})
                    if "assignee" in errors and "assignee" in fields:
                        logger.info("Retrying create without assignee field")
                        del fields["assignee"]
                        assignee_dropped = True
                        response = await self.client.create_issue_v2(fields=fields)
                except Exception:
                    pass

            if response.status in (HttpStatusCode.SUCCESS.value, HttpStatusCode.CREATED.value):
                data = response.json()
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl", "*._links")
                    .clean()
                )
                issue_key = cleaned_data.get("key")
                site_url = await self._get_site_url()
                if issue_key and site_url:
                    cleaned_data["url"] = f"{site_url}/browse/{issue_key}"
                if site_url:
                    self._add_urls_to_issue_references(cleaned_data, site_url)
                result: dict[str, Any] = {"message": "Issue created successfully", "data": cleaned_data}
                if assignee_dropped:
                    # Issue was created, but the requested assignee was rejected and removed on retry.
                    result["warning"] = (
                        f"Issue created unassigned: the assignee '{assignee_name}' could not be set "
                        "(not assignable on this project, or unknown username)."
                    )
                return True, json.dumps(result)

            if response.status == HttpStatusCode.BAD_REQUEST.value:
                try:
                    field_errors = response.json().get("errors", {})
                    if field_errors:
                        field_schema = await self._fetch_and_cache_field_schema()
                        readable = {
                            f"{field_schema.get(fid, {}).get('name', fid)} ({fid})": emsg
                            for fid, emsg in field_errors.items()
                        }
                        return False, json.dumps({
                            "error": "Issue creation failed — required fields are missing or invalid",
                            "field_errors": readable,
                            "guidance": (
                                f"Call get_create_issue_fields(project_key='{project_key}', "
                                f"issue_type_name='{issue_type_name}') to see required fields, then retry "
                                "create_issue supplying them via custom_fields."
                            ),
                        })
                except Exception:
                    pass
            return self._handle_response(response, "Issue created successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error creating issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="update_issue",
        description="Update an existing Jira Data Center issue (fields, status, assignee, type)",
        args_schema=UpdateIssueInput,
        returns="Update result",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def update_issue(
        self,
        issue_key: str,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        issue_type_name: Optional[str] = None,
        assignee_name: Optional[str] = None,
        assignee_query: Optional[str] = None,
        reporter_name: Optional[str] = None,
        reporter_query: Optional[str] = None,
        priority_name: str | None = None,
        labels: list[str] | None = None,
        components: list[str] | None = None,
        status: str | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        try:
            project_key = ""
            try:
                existing = await self.client.get_issue_v2(issueIdOrKey=issue_key)
                if existing.status == HttpStatusCode.SUCCESS.value:
                    project_key = existing.json().get("fields", {}).get("project", {}).get("key", "")
            except Exception as e:
                logger.warning(f"Could not fetch issue {issue_key} for project key: {e}")

            fields: dict[str, object] = {}
            if summary:
                fields["summary"] = summary
            if description:
                # DC: plain/wiki string, no ADF
                fields["description"] = self._normalize_description(description)

            if issue_type_name:
                resolved_type = issue_type_name
                if project_key:
                    available = [it.get("name", "") for it in await self._fetch_issue_types(project_key) if it.get("name")]
                    exact = next((n for n in available if n.lower() == issue_type_name.lower()), None)
                    if exact:
                        resolved_type = exact
                    else:
                        fuzzy = difflib.get_close_matches(issue_type_name.lower(), [n.lower() for n in available], n=1, cutoff=0.6)
                        if fuzzy:
                            for n in available:
                                if n.lower() == fuzzy[0]:
                                    resolved_type = n
                                    break
                fields["issuetype"] = {"name": resolved_type}

            if assignee_query and not assignee_name and project_key:
                assignee_name = await self._resolve_user_to_name(project_key, assignee_query)
            if assignee_name:
                fields["assignee"] = {"name": assignee_name}

            if reporter_query and not reporter_name and project_key:
                reporter_name = await self._resolve_user_to_name(project_key, reporter_query)
            if reporter_name:
                fields["reporter"] = {"name": reporter_name}

            if priority_name:
                fields["priority"] = {"name": priority_name}
            if labels:
                fields["labels"] = labels
            if components:
                fields["components"] = [{"name": comp} for comp in components]
            if custom_fields:
                for field_id, field_value in custom_fields.items():
                    if field_id not in fields:
                        fields[field_id] = field_value

            transition = None
            reporter_dropped = False
            status_warning: Optional[str] = None
            if status:
                available_statuses: list[str] = []
                try:
                    tr = await self.client.get_transitions_v2(issueIdOrKey=issue_key)
                    if tr.status == HttpStatusCode.SUCCESS.value:
                        for t in tr.json().get("transitions", []):
                            to_name = t.get("to", {}).get("name", "")
                            if to_name:
                                available_statuses.append(to_name)
                            if to_name.lower() == status.lower():
                                transition = {"id": t.get("id")}
                                break
                    else:
                        status_warning = f"Could not fetch transitions (HTTP {tr.status}); status not changed."
                except Exception as e:
                    logger.warning(f"Could not get transitions for {issue_key}: {e}")
                    status_warning = f"Could not fetch transitions ({e}); status not changed."
                if not transition and status_warning is None:
                    status_warning = (
                        f"Status '{status}' is not reachable from the issue's current status; it was not changed."
                        + (f" Reachable next statuses: {', '.join(available_statuses)}." if available_statuses else "")
                    )

            if not fields and not transition:
                if status and status_warning:
                    return False, json.dumps({"error": status_warning})
                return False, json.dumps({
                    "error": "No updates provided",
                    "guidance": "Provide at least one field to update or a status to transition to",
                })

            if fields:
                # Change issue type first so custom fields validate against the target type.
                if "issuetype" in fields and len(fields) > 1:
                    type_resp = await self.client.edit_issue_v2(issueIdOrKey=issue_key, fields={"issuetype": fields.pop("issuetype")})
                    if type_resp.status not in (HttpStatusCode.SUCCESS.value, HttpStatusCode.NO_CONTENT.value):
                        return self._handle_response(type_resp, "Issue updated successfully", include_guidance=True)
                if fields:
                    response = await self.client.edit_issue_v2(issueIdOrKey=issue_key, fields=fields)
                    if response.status == HttpStatusCode.BAD_REQUEST.value and "reporter" in fields:
                        try:
                            if "reporter" in response.json().get("errors", {}):
                                fields_no_reporter = {k: v for k, v in fields.items() if k != "reporter"}
                                if fields_no_reporter:
                                    reporter_dropped = True
                                    response = await self.client.edit_issue_v2(issueIdOrKey=issue_key, fields=fields_no_reporter)
                        except Exception:
                            pass
                    if response.status == HttpStatusCode.BAD_REQUEST.value:
                        try:
                            field_errors = response.json().get("errors", {})
                            if field_errors:
                                field_schema = await self._fetch_and_cache_field_schema()
                                readable = {f"{field_schema.get(fid, {}).get('name', fid)} ({fid})": emsg for fid, emsg in field_errors.items()}
                                return False, json.dumps({
                                    "error": "Issue update failed — required fields are missing or invalid",
                                    "field_errors": readable,
                                    "guidance": (
                                        f"Call get_create_issue_fields(project_key='{project_key}', "
                                        f"issue_type_name='{issue_type_name}') and retry with custom_fields."
                                        if issue_type_name and project_key
                                        else "Check field_errors and correct the values."
                                    ),
                                })
                        except Exception:
                            pass
                    if response.status not in (HttpStatusCode.SUCCESS.value, HttpStatusCode.NO_CONTENT.value):
                        return self._handle_response(response, "Issue updated successfully", include_guidance=True)

            transition_success = True
            transition_error = None
            if transition:
                try:
                    tr = await self.client.do_transition_v2(issueIdOrKey=issue_key, transition=transition)
                    if tr.status not in (HttpStatusCode.SUCCESS.value, HttpStatusCode.NO_CONTENT.value):
                        transition_success = False
                        transition_error = f"HTTP {tr.status}"
                        try:
                            err = tr.json()
                            if isinstance(err, dict) and "errorMessages" in err:
                                transition_error = "; ".join(err.get("errorMessages", []))
                        except Exception:
                            pass
                except Exception as e:
                    transition_success = False
                    transition_error = str(e)

            issue_response = await self.client.get_issue_v2(issueIdOrKey=issue_key)
            message = "Issue updated successfully"
            warnings: list[str] = []
            if transition and not transition_success:
                warnings.append(f"Status transition failed: {transition_error}")
            if status_warning:
                warnings.append(status_warning)
            if reporter_dropped:
                warnings.append(
                    f"Reporter '{reporter_name}' could not be set (not permitted on this instance); it was left unchanged."
                )

            if issue_response.status != HttpStatusCode.SUCCESS.value:
                site_url = await self._get_site_url()
                url = f"{site_url}/browse/{issue_key}" if site_url else None
                result: dict[str, Any] = {"message": message, "data": {"key": issue_key, "url": url} if url else {"key": issue_key}}
                if warnings:
                    result["warnings"] = warnings
                return True, json.dumps(result)

            cleaned_data = (
                ResponseTransformer(issue_response.json())
                .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                        "*.subtask", "*.avatarId", "*.hierarchyLevel", "*._links")
                .clean()
            )
            cleaned_data = self._clean_issue_fields(cleaned_data)
            issue_key_out = cleaned_data.get("key") or issue_key
            site_url = await self._get_site_url()
            if issue_key_out and site_url:
                cleaned_data["url"] = f"{site_url}/browse/{issue_key_out}"
            if site_url:
                self._add_urls_to_issue_references(cleaned_data, site_url)
            result = {"message": message, "data": cleaned_data}
            if warnings:
                result["warnings"] = warnings
            return True, json.dumps(result)
        except Exception as e:
            logger.error(f"Error updating issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_projects",
        description="Get all Jira Data Center projects",
        parameters=[],
        returns="List of projects",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def get_projects(self) -> tuple[bool, str]:
        try:
            response = await self.client.list_projects_get_v2(expand=["lead", "description"])
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl", "*._links")
                    .clean()
                )
                site_url = await self._get_site_url()
                if site_url and isinstance(cleaned_data, list):
                    for project in cleaned_data:
                        if project.get("key"):
                            project["url"] = f"{site_url}/projects/{project['key']}"
                return True, json.dumps({"message": "Projects fetched successfully", "data": cleaned_data})
            return self._handle_response(response, "Projects fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error getting projects: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_project",
        description="Get a specific Jira Data Center project",
        args_schema=GetProjectInput,
        returns="Project details",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def get_project(self, project_key: str) -> tuple[bool, str]:
        try:
            response = await self.client.get_project_v2(
                projectIdOrKey=project_key, expand=["description", "lead", "issueTypes"]
            )
            if response.status == HttpStatusCode.SUCCESS.value:
                cleaned_data = (
                    ResponseTransformer(response.json())
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl", "*._links")
                    .clean()
                )
                key = cleaned_data.get("key")
                if key:
                    site_url = await self._get_site_url()
                    if site_url:
                        cleaned_data["url"] = f"{site_url}/projects/{key}"
                return True, json.dumps({"message": "Project fetched successfully", "data": cleaned_data})
            return self._handle_response(response, "Project fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error getting project: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_issues",
        description="Get issues from a Jira Data Center project. For specific queries, use search_issues with JQL.",
        args_schema=GetIssuesInput,
        returns="List of issues from the project",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def get_issues(self, project_key: str, days: Optional[int] = None, max_results: Optional[int] = None) -> tuple[bool, str]:
        try:
            escaped = project_key.replace('"', '\\"')
            time_filter = days or 30
            jql = f'project = "{escaped}" AND updated >= -{time_filter}d ORDER BY updated DESC'
            response = await self._run_search(jql, max_results)
            if response.status == HttpStatusCode.SUCCESS.value:
                cleaned_data = self._clean_search_response(response.json())
                field_schema = await self._fetch_and_cache_field_schema()
                cleaned_data = await self._normalize_issues_in_response(cleaned_data, field_schema)
                site_url = await self._get_site_url()
                if site_url and isinstance(cleaned_data, dict) and "issues" in cleaned_data:
                    for issue in cleaned_data["issues"]:
                        if issue.get("key"):
                            issue["url"] = f"{site_url}/browse/{issue['key']}"
                        self._add_urls_to_issue_references(issue, site_url)
                return True, json.dumps({"message": "Issues fetched successfully", "data": cleaned_data})
            return self._handle_response(response, "Issues fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error getting issues: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_issue",
        description="Get a specific Jira Data Center issue",
        args_schema=GetIssueInput,
        returns="Issue details",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def get_issue(self, issue_key: str) -> tuple[bool, str]:
        try:
            response = await self.client.get_issue_v2(issueIdOrKey=issue_key, expand=["renderedFields"])
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                raw_desc = data.get("fields", {}).get("description")
                rendered_desc = (data.get("renderedFields") or {}).get("description")
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("expand", "renderedFields", "self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.subtask", "*.avatarId", "*.hierarchyLevel",
                            "*.statusCategory", "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links", "*.watches", "*.votes", "*.worklog",
                            "*.progress", "*.aggregateprogress", "*.workratio", "*.lastViewed", "*.security",
                            "*.description", "*.id")
                    .clean()
                )
                cleaned_data = self._clean_issue_fields(cleaned_data)
                field_schema = await self._fetch_and_cache_field_schema()
                cleaned_data = await self._normalize_issues_in_response(cleaned_data, field_schema)
                # Restore a readable description (rendered HTML preferred, else raw wiki text)
                desc = rendered_desc or raw_desc
                if desc and isinstance(cleaned_data.get("fields"), dict):
                    cleaned_data["fields"]["description"] = desc
                key = cleaned_data.get("key")
                site_url = await self._get_site_url()
                if key and site_url:
                    cleaned_data["url"] = f"{site_url}/browse/{key}"
                if site_url:
                    self._add_urls_to_issue_references(cleaned_data, site_url)
                return True, json.dumps({"message": "Issue fetched successfully", "data": cleaned_data})
            return self._handle_response(response, "Issue fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error getting issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="search_issues",
        description="Search Jira Data Center issues using JQL",
        args_schema=SearchIssuesInput,
        returns="Matching issues",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def search_issues(self, jql: str, maxResults: Optional[int] = None) -> tuple[bool, str]:
        try:
            fixed_jql, jql_warning = self._validate_and_fix_jql(jql)
            response = await self._run_search(fixed_jql, maxResults)
            if response.status == HttpStatusCode.SUCCESS.value:
                raw = response.json()
                # Capture the match total before _clean_search_response strips it, so the
                # caller knows when the (single-page) result is truncated.
                total = raw.get("total") if isinstance(raw, dict) else None
                cleaned_data = self._clean_search_response(raw)
                field_schema = await self._fetch_and_cache_field_schema()
                cleaned_data = await self._normalize_issues_in_response(cleaned_data, field_schema)
                site_url = await self._get_site_url()
                if site_url and isinstance(cleaned_data, dict) and "issues" in cleaned_data:
                    for issue in cleaned_data["issues"]:
                        if issue.get("key"):
                            issue["url"] = f"{site_url}/browse/{issue['key']}"
                        self._add_urls_to_issue_references(issue, site_url)
                result: dict[str, Any] = {"message": "Issues fetched successfully", "data": cleaned_data}
                returned = len(cleaned_data.get("issues", [])) if isinstance(cleaned_data, dict) else 0
                if isinstance(total, int):
                    result["total"] = total
                    result["returned"] = returned
                    if returned < total:
                        result["note"] = (
                            f"Showing {returned} of {total} matching issues. Narrow the JQL "
                            "or raise maxResults to retrieve more."
                        )
                if jql_warning:
                    result["warning"] = jql_warning
                    result["original_jql"] = jql
                    result["fixed_jql"] = fixed_jql
                return True, json.dumps(result)
            error_result = self._handle_response(response, "Issues fetched successfully", include_guidance=True)
            try:
                error_data = json.loads(error_result[1])
                error_data["jql_query"] = fixed_jql
                return error_result[0], json.dumps(error_data)
            except Exception:
                return error_result
        except Exception as e:
            logger.error(f"Error searching issues - JQL: {jql}, error: {e}")
            return False, json.dumps({"error": str(e), "jql_query": jql})

    @tool(
        app_name=_APP,
        tool_name="add_comment",
        description="Add a comment to a Jira Data Center issue",
        args_schema=AddCommentInput,
        returns="Comment result",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def add_comment(self, issue_key: str, comment: str) -> tuple[bool, str]:
        try:
            if not comment or not str(comment).strip():
                return False, json.dumps({"error": "Comment text is required and cannot be empty"})
            # DC comment body is a plain/wiki string (no ADF)
            response = await self.client.add_comment_v2(issueIdOrKey=issue_key, body=str(comment))
            if response.status in (HttpStatusCode.SUCCESS.value, HttpStatusCode.CREATED.value):
                cleaned_data = (
                    ResponseTransformer(response.json())
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl", "*._links")
                    .clean()
                )
                # The comment response has no issue reference; add the ticket URL from issue_key,
                # plus a permalink to the new comment (focusedCommentId deep-link).
                result: dict[str, Any] = {"message": "Comment added successfully", "data": cleaned_data, "issue_key": issue_key}
                site_url = await self._get_site_url()
                if site_url:
                    result["issue_url"] = f"{site_url}/browse/{issue_key}"
                    comment_id = cleaned_data.get("id") if isinstance(cleaned_data, dict) else None
                    if comment_id:
                        result["comment_url"] = self._comment_url(site_url, issue_key, comment_id)
                return True, json.dumps(result)
            return self._handle_response(response, "Comment added successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error adding comment: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_comments",
        description="Get comments for a Jira Data Center issue",
        args_schema=GetCommentsInput,
        returns="List of comments",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def get_comments(self, issue_key: str) -> tuple[bool, str]:
        try:
            response = await self.client.get_issue_comments_v2(issueIdOrKey=issue_key, expand="renderedBody")
            if response.status == HttpStatusCode.SUCCESS.value:
                cleaned_data = (
                    ResponseTransformer(response.json())
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl", "*._links")
                    .clean()
                )
                result: dict[str, Any] = {"message": "Comments fetched successfully", "data": cleaned_data, "issue_key": issue_key}
                site_url = await self._get_site_url()
                if site_url:
                    result["issue_url"] = f"{site_url}/browse/{issue_key}"
                    if isinstance(cleaned_data, dict) and isinstance(cleaned_data.get("comments"), list):
                        for c in cleaned_data["comments"]:
                            cid = c.get("id") if isinstance(c, dict) else None
                            if cid:
                                c["url"] = self._comment_url(site_url, issue_key, cid)
                return True, json.dumps(result)
            return self._handle_response(response, "Comments fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error getting comments: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="search_users",
        description="Search Jira Data Center users by name or email",
        args_schema=SearchUsersInput,
        returns="List of users (name, key, displayName, emailAddress)",
        llm_description=(
            "Search Jira Data Center users by name or email. Returns the username ('name') needed to set "
            "an assignee. For issues assigned to the current user, use `assignee = currentUser()` in JQL instead."
        ),
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def search_users(self, query: str, max_results: Optional[int] = None) -> tuple[bool, str]:
        try:
            if not query or not query.strip():
                return False, json.dumps({
                    "error": "Query parameter is required and cannot be empty.",
                    "guidance": "Provide a username or email. For your own issues, use `assignee = currentUser()` in JQL.",
                })
            query = query.strip()
            response = await self.client.find_users_for_picker_v2(query=query, maxResults=max_results or 20)
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                users = data.get("users", []) if isinstance(data, dict) else data
                cleaned_users = []
                for user in users or []:
                    cleaned_user = {
                        "name": user.get("name"),
                        "key": user.get("key"),
                        "displayName": user.get("displayName"),
                    }
                    if user.get("emailAddress"):
                        cleaned_user["emailAddress"] = user.get("emailAddress")
                    else:
                        # The DC user picker has no emailAddress field — the email is embedded in
                        # `html` (e.g. "John Doe - jdoe@example.com (jdoe)"), often with highlight
                        # <b> tags. Strip tags, then take the first email-looking token. (The old
                        # "last parens" scrape grabbed the username, not the email.)
                        plain = re.sub(r"<[^>]+>", "", user.get("html", ""))
                        m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", plain)
                        if m:
                            cleaned_user["emailAddress"] = m.group(0)
                    if cleaned_user.get("name") or cleaned_user.get("key"):
                        cleaned_users.append(cleaned_user)
                return True, json.dumps({
                    "message": "Users fetched successfully",
                    "data": {"results": cleaned_users, "total": len(cleaned_users)},
                })
            return self._handle_response(response, "Users fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error searching users: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_project_metadata",
        description="Get Jira Data Center project metadata (issue types, components, lead)",
        args_schema=GetProjectMetadataInput,
        returns="Project metadata",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
    )
    async def get_project_metadata(self, project_key: str) -> tuple[bool, str]:
        try:
            response = await self.client.get_project_v2(
                projectIdOrKey=project_key, expand=["description", "lead", "issueTypes"]
            )
            if response.status != HttpStatusCode.SUCCESS.value:
                return self._handle_response(response, "Project metadata fetched", include_guidance=True)
            cleaned_project = (
                ResponseTransformer(response.json())
                # Do NOT strip "*.subtask": the issue-type `subtask` flag below is built from it,
                # and the transformer descends into list items — removing it would report every
                # issue type as subtask=False, including real sub-task types.
                .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                        "*.active", "*.timeZone", "*._links", "*.avatarId", "*.hierarchyLevel")
                .clean()
            )
            metadata = {
                "project_key": cleaned_project.get("key"),
                "project_name": cleaned_project.get("name"),
                "issue_types": [
                    {"id": it.get("id"), "name": it.get("name"),
                     "description": it.get("description"), "subtask": it.get("subtask", False)}
                    for it in cleaned_project.get("issueTypes", [])
                ],
                "components": [
                    {"id": comp.get("id"), "name": comp.get("name"), "description": comp.get("description")}
                    for comp in cleaned_project.get("components", [])
                ],
                "lead": cleaned_project.get("lead", {}).get("displayName") if isinstance(cleaned_project.get("lead"), dict) else None,
            }
            return True, json.dumps({"message": "Project metadata fetched successfully", "metadata": metadata})
        except Exception as e:
            logger.error(f"Error getting project metadata: {e}")
            return False, json.dumps({"error": str(e)})
