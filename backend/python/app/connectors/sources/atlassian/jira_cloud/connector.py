import re
from dataclasses import dataclass
from logging import Logger
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.registry.connector_builder import (
    AuthField,
    ConnectorBuilder,
    DocumentationLink,
)
from app.connectors.sources.atlassian.core.apps import JiraApp
from app.connectors.sources.atlassian.core.oauth import (
    OAUTH_JIRA_CONFIG_PATH,
    AtlassianScope,
)
from app.models.entities import (
    AppUser,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType

RESOURCE_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
BASE_URL = "https://api.atlassian.com/ex/jira"
AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"

def adf_to_text(adf_content: Dict[str, Any]) -> str:
    """
    Convert Atlassian Document Format (ADF) to plain text.
    Args:
        adf_content: ADF content (dict or None)
    Returns:
        Plain text representation of the ADF content
    """
    if not adf_content or not isinstance(adf_content, dict):
        return ""

    text_parts = []

    def extract_text(node: Dict[str, Any]) -> str:
        """Recursively extract text from ADF nodes."""
        if not isinstance(node, dict):
            return ""

        node_type = node.get("type", "")
        text = ""

        # Handle text nodes
        if node_type == "text":
            text = node.get("text", "")

            # Apply marks (formatting) if present
            marks = node.get("marks", [])
            for mark in marks:
                mark_type = mark.get("type", "")
                if mark_type == "link":
                    href = mark.get("attrs", {}).get("href", "")
                    text = f"{text} ({href})"

        # Handle paragraph, heading, and other block nodes
        elif node_type in ["paragraph", "heading", "blockquote", "listItem"]:
            content = node.get("content", [])
            text = " ".join(extract_text(child) for child in content)

            # Add appropriate formatting
            if node_type == "paragraph":
                text = text + "\n"
            elif node_type == "heading":
                level = node.get("attrs", {}).get("level", 1)
                text = f"{'#' * level} {text}\n"
            elif node_type == "blockquote":
                text = f"> {text}\n"
            elif node_type == "listItem":
                text = f"• {text}\n"

        # Handle list nodes
        elif node_type in ["bulletList", "orderedList"]:
            content = node.get("content", [])
            items = []
            for i, child in enumerate(content):
                child_text = extract_text(child).strip()
                if node_type == "orderedList":
                    items.append(f"{i + 1}. {child_text}")
                else:
                    items.append(f"• {child_text}")
            text = "\n".join(items) + "\n"

        # Handle code blocks
        elif node_type == "codeBlock":
            content = node.get("content", [])
            code_text = " ".join(extract_text(child) for child in content)
            language = node.get("attrs", {}).get("language", "")
            text = f"```{language}\n{code_text}\n```\n"

        # Handle inline code
        elif node_type == "inlineCode":
            text = f"`{node.get('text', '')}`"

        # Handle hardBreak
        elif node_type == "hardBreak":
            text = "\n"

        # Handle rule (horizontal line)
        elif node_type == "rule":
            text = "---\n"

        # Handle media (images, files)
        elif node_type == "media":
            attrs = node.get("attrs", {})
            alt = attrs.get("alt", "")
            title = attrs.get("title", "")
            text = f"[Media: {alt or title or 'attachment'}]\n"

        # Handle mention
        elif node_type == "mention":
            attrs = node.get("attrs", {})
            text = f"@{attrs.get('text', attrs.get('id', 'mention'))}"

        # Handle emoji
        elif node_type == "emoji":
            attrs = node.get("attrs", {})
            text = attrs.get("shortName", attrs.get("text", ""))

        # Handle table
        elif node_type == "table":
            content = node.get("content", [])
            rows = []
            for row in content:
                if row.get("type") == "tableRow":
                    cells = []
                    for cell in row.get("content", []):
                        cell_text = extract_text(cell).strip()
                        cells.append(cell_text)
                    rows.append(" | ".join(cells))
            text = "\n".join(rows) + "\n"

        # Handle table cells
        elif node_type in ["tableCell", "tableHeader"]:
            content = node.get("content", [])
            text = " ".join(extract_text(child) for child in content)

        # Handle panel
        elif node_type == "panel":
            attrs = node.get("attrs", {})
            panel_type = attrs.get("panelType", "info")
            content = node.get("content", [])
            panel_text = " ".join(extract_text(child) for child in content)
            text = f"[{panel_type.upper()}] {panel_text}\n"

        # Handle any other node with content
        elif "content" in node:
            content = node.get("content", [])
            text = " ".join(extract_text(child) for child in content)

        return text

    # Start processing from the root
    if "content" in adf_content:
        for node in adf_content.get("content", []):
            text = extract_text(node)
            if text:
                text_parts.append(text)
    else:
        # Sometimes ADF might be a single node
        text = extract_text(adf_content)
        if text:
            text_parts.append(text)

    # Join all parts and clean up extra whitespace
    result = "".join(text_parts)

    # Clean up multiple consecutive newlines
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result.strip()


@dataclass
class AtlassianCloudResource:
    """Represents an Atlassian Cloud resource (site)"""
    id: str
    name: str
    url: str
    scopes: List[str]
    avatar_url: Optional[str] = None

class JiraClient:
    def __init__(self, logger: Logger, config_service: ConfigurationService) -> None:
        self.logger = logger
        self.config_service = config_service
        self.base_url = BASE_URL
        self.session = None
        self.accessible_resources = None
        self.cloud_id = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure session is created and available"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self) -> None:
        """Close the session"""
        if self.session:
            await self.session.close()
            self.session = None

    async def __aenter__(self) -> "JiraClient":
        """Async context manager entry"""
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit"""
        await self.close()

    async def initialize(self) -> None:
        await self._ensure_session()
        self.accessible_resources = await self.get_accessible_resources()
        if self.accessible_resources:
            self.cloud_id = self.accessible_resources[0].id
        else:
            raise Exception("No accessible resources found")

    async def make_authenticated_json_request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Make authenticated API request and return JSON response"""
        config = await self.config_service.get_config(f"{OAUTH_JIRA_CONFIG_PATH}")
        token = None
        if not config:
            self.logger.error("❌ Jira credentials not found")
            raise ValueError("Jira credentials not found")

        credentials_config = config.get("credentials", {})

        if not credentials_config:
            self.logger.error("❌ Jira credentials not found")
            raise ValueError("Jira credentials not found")

        token = {
            "token_type": credentials_config.get("token_type"),
            "access_token": credentials_config.get("access_token")
        }

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"{token.get('token_type')} {token.get('access_token')}"

        session = await self._ensure_session()
        async with session.request(method, url, headers=headers, **kwargs) as response:
            response.raise_for_status()
            return await response.json()


    async def get_accessible_resources(self) -> List[AtlassianCloudResource]:
        """
        Get list of Atlassian sites (Confluence/Jira instances) accessible to the user
        Args:
            None
        Returns:
            List of accessible Atlassian Cloud resources
        """

        response = await self.make_authenticated_json_request(
            "GET",
            RESOURCE_URL
        )

        return [
            AtlassianCloudResource(
                id=resource["id"],
                name=resource.get("name", ""),
                url=resource["url"],
                scopes=resource.get("scopes", []),
                avatar_url=resource.get("avatarUrl")
            )
            for resource in response
        ]

    async def fetch_issues_with_permissions(self, project_key: str, project_id: str, user: AppUser) -> List[Tuple[Record, List[Permission]]]:
        url = f"{BASE_URL}/{self.cloud_id}/rest/api/3/search"
        issues = []

        while True:
            issues_batch = await self.make_authenticated_json_request("GET", url, params={"projectKey": project_key, "maxResults": 25})
            issues = issues + issues_batch.get("issues", [])
            next_url = issues_batch.get("_links", {}).get("next", None)
            if not next_url:
                break
            url = next_url

        issue_records = []
        for issue in issues:
            issue_id = f"project-{project_key}/issue-{issue.get('id')}"
            fields = issue.get("fields", {})
            issue_name = fields.get("summary")
            status = fields.get("status", {}).get("name")
            priority = fields.get("priority", {}).get("name")
            creator = fields.get("creator") or {}
            creator_email = creator.get("emailAddress")
            creator_name = creator.get("displayName")

            if creator_email is None:
                creator_email = user.email
            permissions = [Permission(
                entity_type=EntityType.USER,
                email=creator_email,
                type=PermissionType.OWNER,
            )]
            atlassian_domain = self.accessible_resources[0].url

            issue_record = TicketRecord(
                priority=priority,
                status=status,
                summary=issue_name,
                creator_email=creator_email,
                creator_name=creator_name,
                external_record_id=issue_id,
                record_name=issue_name,
                record_type=RecordType.TICKET,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.JIRA,
                record_group_type=RecordGroupType.JIRA_PROJECT,
                external_record_group_id=project_id,
                version=0,
                mime_type=MimeTypes.PLAIN_TEXT.value,
                weburl=f"{atlassian_domain}/browse/{issue.get('key')}"
            )
            issue_records.append((issue_record, permissions))

        return issue_records

    async def fetch_projects_with_permissions(self) -> List[Tuple[RecordGroup, List[Permission]]]:
        url = f"{BASE_URL}/{self.cloud_id}/rest/api/3/project/search"

        projects = []
        while True:
            projects_batch = await self.make_authenticated_json_request("GET", url, params={"maxResults": 25, "expand": "description,url,permissions,issueTypes"})
            projects = projects + projects_batch.get("values", [])
            next_url = projects_batch.get("nextPage", None)
            if not next_url:
                break
            url = next_url

        record_groups = []
        for project in projects:
            project_id = project.get("id")
            project_name = project.get("name")
            project_key = project.get("key")

            record_group = RecordGroup(
                external_group_id=project_id,
                connector_name=Connectors.JIRA.value,
                name=project_name,
                short_name=project_key,
                group_type=RecordGroupType.JIRA_PROJECT,
                origin=OriginTypes.CONNECTOR,
                description=project.get("description", None),
            )
            record_groups.append((record_group, []))

        return record_groups

    async def fetch_users(self) -> List[AppUser]:
        url = f"{BASE_URL}/{self.cloud_id}/rest/api/3/users/search"
        users = []
        base_url = f"{BASE_URL}/{self.cloud_id}"
        while True:
            users_batch = await self.make_authenticated_json_request("GET", url)
            users = users + users_batch.get("results", [])
            next_url = users_batch.get("_links", {}).get("next", None)
            if not next_url:
                break
            url = f"{base_url}/{next_url}"
        return [AppUser(email=user["emailAddress"], org_id=self.org_id, source_user_id=user["accountId"]) for user in users]

    async def fetch_issue_content(
        self,
        issue_id: str,
    ) -> str:
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/issue/{issue_id}"
        print(url, "jira request url")
        issue_details = await self.make_authenticated_json_request("GET", url)
        description = issue_details.get("fields", {}).get("description", "")
        summary = issue_details.get("fields", {}).get("summary", "")

        # convert description ADF(Atlassian Document Format) to text
        summary_text = f"Title: {summary}" if summary else ""
        description_text = f"Description: {adf_to_text(description)}" if description else ""
        combined_text = f"# {summary_text}\n\n{description_text}"

        return combined_text

@ConnectorBuilder("Jira")\
    .in_group("Atlassian")\
    .with_auth_type("OAUTH")\
    .with_description("Sync issues from Jira Cloud")\
    .with_categories(["Storage"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/jira.svg")
        .add_documentation_link(DocumentationLink(
            "Jira Cloud API Setup",
            "https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/jira/jira',
            'pipeshub'
        ))
        .with_redirect_uri("connectors/oauth/callback/Jira", False)
        .add_auth_field(AuthField(
            name="clientId",
            display_name="Application (Client) ID",
            placeholder="Enter your Atlassian Cloud Application ID",
            description="The Application (Client) ID from Azure AD App Registration"
        ))
        .add_auth_field(AuthField(
            name="clientSecret",
            display_name="Client Secret",
            placeholder="Enter your Atlassian Cloud Client Secret",
            description="The Client Secret from Azure AD App Registration",
            field_type="PASSWORD",
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="domain",
            display_name="Atlassian Domain",
            description="https://your-domain.atlassian.net"
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_oauth_urls(AUTHORIZE_URL, TOKEN_URL, AtlassianScope.get_full_access())

    )\
    .build_decorator()
class JiraConnector(BaseConnector):
    def __init__(self, logger: Logger, data_entities_processor: DataSourceEntitiesProcessor,
                 data_store_provider: DataStoreProvider, config_service: ConfigurationService) -> None:
        super().__init__(JiraApp(), logger, data_entities_processor, data_store_provider, config_service)
        self.provider = None

    async def init(self) -> None:
        pass

    async def run_sync(self) -> None:
        users = await self.data_entities_processor.get_all_active_users()
        if not users:
            self.logger.info("No users found")
            return

        jira_client = await self.get_jira_client()
        user = users[0]
        try:
            projects = await jira_client.fetch_projects_with_permissions()

            await self.data_entities_processor.on_new_record_groups(projects)

            for project, permissions in projects:
                issues = await jira_client.fetch_issues_with_permissions(project.short_name, project.external_group_id, user)
                await self.data_entities_processor.on_new_records(issues)

        except Exception as e:
            self.logger.error(f"Error processing user {user.email}: {e}")


    async def get_jira_client(self) -> JiraClient:
        jira_client = JiraClient(self.logger, self.config_service)
        await jira_client.initialize()

        return jira_client

    async def get_signed_url(self, record: Record) -> str:
        """
        Create a signed URL for a specific record.
        """
        pass

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Jira."""
        pass

    async def run_incremental_sync(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    async def handle_webhook_notification(self, notification: Dict) -> None:
        pass

    async def stream_record(self, record: Record) -> StreamingResponse:
        jira_client = await self.get_jira_client()
        issue_content = await jira_client.fetch_issue_content(record.external_record_id)
        return StreamingResponse(
            iter([issue_content]), media_type=MimeTypes.PLAIN_TEXT.value, headers={}
        )

    @classmethod
    async def create_connector(cls, logger, data_store_provider: DataStoreProvider, config_service: ConfigurationService) -> "BaseConnector":
        data_entities_processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await data_entities_processor.initialize()

        return JiraConnector(logger, data_entities_processor, data_store_provider, config_service)
