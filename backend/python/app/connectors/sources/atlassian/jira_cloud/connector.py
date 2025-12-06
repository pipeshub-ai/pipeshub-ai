"""Jira Cloud Connector Implementation"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterField,
    FilterType,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.atlassian.core.apps import JiraApp
from app.connectors.sources.atlassian.core.oauth import (
    OAUTH_JIRA_CONFIG_PATH,
    AtlassianScope,
)
from app.models.entities import (
    AppUser,
    CommentRecord,
    IndexingStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.jira.jira import JiraClient
from app.sources.external.jira.jira import JiraDataSource
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# API URLs
RESOURCE_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
BASE_URL = "https://api.atlassian.com/ex/jira"
AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"

# Pagination constants
DEFAULT_MAX_RESULTS: int = 100
THRESHOLD_PAGINATION_LIMIT: int = 100
MAX_PAGES_PER_PROJECT: int = 50
BATCH_PROCESSING_SIZE: int = 200

# JQL query constants
JQL_TIME_BUFFER_MINUTES: int = 5  # Buffer time for incremental sync to catch edge cases
ISSUE_SEARCH_FIELDS: List[str] = [
    "summary", "description", "status", "priority",
    "creator", "assignee", "created", "updated",
    "issuetype", "project"
]

# HTTP status codes
HTTP_STATUS_OK: int = 200
HTTP_STATUS_BAD_REQUEST: int = 400
HTTP_STATUS_UNAUTHORIZED: int = 401
HTTP_STATUS_GONE: int = 410

@dataclass
class AtlassianCloudResource:
    """Represents an Atlassian Cloud resource (site)."""
    id: str
    name: str
    url: str
    scopes: List[str]
    avatar_url: Optional[str] = None

def adf_to_text(adf_content: Dict[str, Any]) -> str:
    """
    Convert Atlassian Document Format (ADF) to plain text.
    """
    if not adf_content or not isinstance(adf_content, dict):
        return ""

    text_parts: List[str] = []

    def extract_text(node: Dict[str, Any]) -> str:
        """Recursively extract text from ADF nodes."""
        if not isinstance(node, dict):
            return ""

        node_type = node.get("type", "")
        text = ""

        if node_type == "text":
            text = node.get("text", "")
            marks = node.get("marks", [])
            for mark in marks:
                mark_type = mark.get("type", "")
                if mark_type == "link":
                    href = mark.get("attrs", {}).get("href", "")
                    text = f"{text} ({href})"

        elif node_type in ["paragraph", "heading", "blockquote", "listItem"]:
            content = node.get("content", [])
            text = " ".join(extract_text(child) for child in content)

            if node_type == "paragraph":
                text = text + "\n"
            elif node_type == "heading":
                level = node.get("attrs", {}).get("level", 1)
                text = f"{'#' * level} {text}\n"
            elif node_type == "blockquote":
                text = f"> {text}\n"
            elif node_type == "listItem":
                text = f"• {text}\n"

        elif node_type in ["bulletList", "orderedList"]:
            content = node.get("content", [])
            items: List[str] = []
            for i, child in enumerate(content):
                child_text = extract_text(child).strip()
                if node_type == "orderedList":
                    items.append(f"{i + 1}. {child_text}")
                else:
                    items.append(f"• {child_text}")
            text = "\n".join(items) + "\n"

        elif node_type == "codeBlock":
            content = node.get("content", [])
            code_text = " ".join(extract_text(child) for child in content)
            language = node.get("attrs", {}).get("language", "")
            text = f"```{language}\n{code_text}\n```\n"

        elif node_type == "inlineCode":
            text = f"`{node.get('text', '')}`"

        elif node_type == "hardBreak":
            text = "\n"

        elif node_type == "rule":
            text = "---\n"

        elif node_type == "media":
            attrs = node.get("attrs", {})
            alt = attrs.get("alt", "")
            title = attrs.get("title", "")
            text = f"[Media: {alt or title or 'attachment'}]\n"

        elif node_type == "mention":
            attrs = node.get("attrs", {})
            text = f"@{attrs.get('text', attrs.get('id', 'mention'))}"

        elif node_type == "emoji":
            attrs = node.get("attrs", {})
            text = attrs.get("shortName", attrs.get("text", ""))

        elif node_type == "table":
            content = node.get("content", [])
            rows: List[str] = []
            for row in content:
                if row.get("type") == "tableRow":
                    cells: List[str] = []
                    for cell in row.get("content", []):
                        cell_text = extract_text(cell).strip()
                        cells.append(cell_text)
                    rows.append(" | ".join(cells))
            text = "\n".join(rows) + "\n"

        elif node_type in ["tableCell", "tableHeader"]:
            content = node.get("content", [])
            text = " ".join(extract_text(child) for child in content)

        elif node_type == "panel":
            attrs = node.get("attrs", {})
            panel_type = attrs.get("panelType", "info")
            content = node.get("content", [])
            panel_text = " ".join(extract_text(child) for child in content)
            text = f"[{panel_type.upper()}] {panel_text}\n"

        elif "content" in node:
            content = node.get("content", [])
            text = " ".join(extract_text(child) for child in content)

        return text

    if "content" in adf_content:
        for node in adf_content.get("content", []):
            text = extract_text(node)
            if text:
                text_parts.append(text)
    else:
        text = extract_text(adf_content)
        if text:
            text_parts.append(text)

    result = "".join(text_parts)
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result.strip()

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
        .with_redirect_uri("connectors/oauth/callback/Jira", True)
        .add_auth_field(AuthField(
            name="clientId",
            display_name="Application (Client) ID",
            placeholder="Enter your Atlassian Cloud Application ID",
            description="The Application (Client) ID from Atlassian Developer Console"
        ))
        .add_auth_field(AuthField(
            name="clientSecret",
            display_name="Client Secret",
            placeholder="Enter your Atlassian Cloud Client Secret",
            description="The Client Secret from Atlassian Developer Console",
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
        .add_filter_field(FilterField(
            name="project_keys",
            display_name="Project Keys",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            description="Filter issues by project keys (e.g., PROJ1, PROJ2)"
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter issues by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter issues by creation date."))
        # Indexing filters - Issues
        .add_filter_field(FilterField(
            name="issues",
            display_name="Index Issues",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issues",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="issue_comments",
            display_name="Index Issue Comments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issue comments",
            default_value=True
        ))
    )\
    .build_decorator()
class JiraConnector(BaseConnector):
    """Jira connector for syncing projects, issues, and users from Jira Cloud"""

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService
    ) -> None:
        super().__init__(
            JiraApp(),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service
        )
        self.data_source: Optional[JiraDataSource] = None
        self.cloud_id: Optional[str] = None
        self.site_url: Optional[str] = None
        self._sync_in_progress: bool = False

        # Initialize sync points
        org_id = self.data_entities_processor.org_id

        self.issues_sync_point = SyncPoint(
            connector_name=Connectors.JIRA,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        # Filters will be loaded in init()
        self.sync_filters = None
        self.indexing_filters = None

    async def init(self) -> None:
        """Initialize Jira client using proper Client + DataSource architecture"""
        try:
            # Load filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service,
                "jira",
                self.logger
            )

            self.logger.info(f"Sync filters: {self.sync_filters}")
            self.logger.info(f"Indexing filters: {self.indexing_filters}")

            # Use JiraClient.build_from_services() to create client with proper auth
            client = await JiraClient.build_from_services(
                self.logger,
                self.config_service
            )

            # Create DataSource from client
            self.data_source = JiraDataSource(client)

            # Get cloud ID and site URL from accessible resources
            access_token = await self._get_access_token()
            resources = await JiraClient.get_accessible_resources(access_token)
            if not resources:
                raise Exception("No accessible Jira resources found")

            self.cloud_id = resources[0].id
            self.site_url = resources[0].url

            self.logger.info("Jira client initialized successfully using Client + DataSource architecture")
        except Exception as e:
            self.logger.error(f"Failed to initialize Jira client: {e}")
            raise

    async def _get_access_token(self) -> str:
        """Get access token from config"""
        config = await self.config_service.get_config(f"{OAUTH_JIRA_CONFIG_PATH}")
        access_token = config.get("credentials", {}).get("access_token") if config else None
        if not access_token:
            raise ValueError("Jira access token not found in configuration")
        return access_token

    async def _get_fresh_datasource(self) -> JiraDataSource:
        """
        Get JiraDataSource with ALWAYS-FRESH access token.

        This method:
        1. Fetches current OAuth token from config
        2. Rebuilds client with fresh token if needed
        3. Returns datasource with current token

        Returns:
            JiraDataSource with current valid token
        """
        # Fetch fresh access token from config
        fresh_token = await self._get_access_token()

        # Rebuild client with fresh token
        client = await JiraClient.build_from_services(
            self.logger,
            self.config_service
        )

        # Return new datasource with fresh client
        return JiraDataSource(client)

    def _parse_jira_timestamp(self, timestamp_str: Optional[str]) -> int:
        """Parse Jira timestamp to epoch milliseconds"""
        if not timestamp_str:
            return 0
        try:
            dt = datetime.strptime(
                timestamp_str.replace("+0000", "+00:00"),
                "%Y-%m-%dT%H:%M:%S.%f%z"
            )
            return int(dt.timestamp() * 1000)
        except Exception as e:
            self.logger.warning(f"Failed to parse timestamp '{timestamp_str}': {e}")
            return 0


    async def run_sync(self) -> None:
        """Run sync of Jira projects and issues - only new/updated tickets"""
        # Check if sync is already in progress
        if self._sync_in_progress:
            self.logger.warning("Sync already in progress, skipping this run")
            return

        self._sync_in_progress = True

        try:
            if not self.cloud_id:
                await self.init()

            org_id = self.data_entities_processor.org_id
            users = await self.data_entities_processor.get_all_active_users()

            if not users:
                self.logger.info("No users found")
                return

            # Get last sync time
            try:
                last_sync_data = await self.issues_sync_point.read_sync_point("issues")
                last_sync_time = last_sync_data.get("last_sync_time") if last_sync_data else None
            except Exception:
                last_sync_time = None

            if last_sync_time:
                self.logger.info(f"Starting incremental Jira sync for org: {org_id} from timestamp: {last_sync_time}")
                
            else:
                self.logger.info(f"Starting full Jira sync for org: {org_id} (first sync)")

            # Fetch and sync users
            self.logger.info("Fetching Jira users...")
            jira_users = await self._fetch_users(org_id)
            if jira_users:
                await self.data_entities_processor.on_new_app_users(jira_users)
                self.logger.info(f"Synced {len(jira_users)} Jira users")

            # Fetch projects
            self.logger.info("Fetching Jira projects...")
            projects = await self._fetch_projects()

            # Apply project_keys filter if configured
            if self.sync_filters:
                project_keys_filter = self.sync_filters.get(SyncFilterKey.PROJECT_KEYS)
                if project_keys_filter:
                    allowed_keys = project_keys_filter.get_value(default=[])
                    if allowed_keys:
                        projects = [
                            (proj, perms) for proj, perms in projects
                            if proj.short_name in allowed_keys
                        ]
                        self.logger.info(f"Filtered to {len(projects)} projects based on project_keys filter: {allowed_keys}")

            self.logger.info(f"Found {len(projects)} projects")

            # Sync projects as RecordGroups
            await self.data_entities_processor.on_new_record_groups(projects)
            self.logger.info("Synced projects as RecordGroups")

            # Fetch and sync issues for each project (only new/updated)
            total_issues_synced = 0
            total_issues_fetched = 0
            total_new_issues = 0
            total_updated_issues = 0
            batch_size = BATCH_PROCESSING_SIZE

            for project, project_permissions in projects:
                try:
                    self.logger.info(f"Fetching issues for project: {project.name} ({project.short_name})")

                    issues_with_permissions = await self._fetch_issues(
                        project.short_name,
                        project.external_group_id,
                        jira_users,
                        last_sync_time,
                        org_id
                    )
                    total_issues_fetched += len(issues_with_permissions)

                    if not issues_with_permissions:
                        self.logger.info(f"No new/updated issues for project {project.short_name}")
                        continue

                    self.logger.info(f"Found {len(issues_with_permissions)} new/updated issues for project {project.short_name}")

                    # Separate new records from updated records (including both issues and comments)
                    new_records_with_permissions = [
                        (record, perms) for record, perms in issues_with_permissions
                        if record.version == 0
                    ]

                    updated_records = [
                        record for record, perms in issues_with_permissions
                        if record.version > 0
                    ]

                    # Process new records
                    if new_records_with_permissions:
                        for i in range(0, len(new_records_with_permissions), batch_size):
                            batch = new_records_with_permissions[i:i + batch_size]
                            await self.data_entities_processor.on_new_records(batch)
                            total_new_issues += len(batch)
                            new_issues_count = sum(1 for r, _ in batch if isinstance(r, TicketRecord))
                            new_comments_count = sum(1 for r, _ in batch if isinstance(r, CommentRecord))
                            self.logger.info(f"Synced batch {i//batch_size + 1}: {new_issues_count} NEW issues, {new_comments_count} NEW comments for project {project.short_name}")

                    if updated_records:
                        # Batch upsert updated records using transaction
                        async with self.data_store_provider.transaction() as tx_store:
                            ticket_records_to_update = [record for record in updated_records if isinstance(record, TicketRecord)]
                            comment_records_to_update = [record for record in updated_records if isinstance(record, CommentRecord)]

                            if ticket_records_to_update:
                                await tx_store.batch_upsert_records(ticket_records_to_update)
                            if comment_records_to_update:
                                await tx_store.batch_upsert_records(comment_records_to_update)

                        # Notify about content updates
                        for record in updated_records:
                            await self.data_entities_processor.on_record_content_update(record)

                        total_updated_issues += len(updated_records)
                        updated_issues_count = sum(1 for r in updated_records if isinstance(r, TicketRecord))
                        updated_comments_count = sum(1 for r in updated_records if isinstance(r, CommentRecord))
                        self.logger.info(f"Updated {updated_issues_count} existing issues, {updated_comments_count} existing comments for project {project.short_name}")

                    total_issues_synced += len(issues_with_permissions)
                    self.logger.info(f"Completed syncing {len(issues_with_permissions)} issues for project {project.short_name} "
                                   f"(New: {len(new_records_with_permissions)}, Updated: {len(updated_records)})")

                except Exception as e:
                    self.logger.error(f"Error processing issues for project {project.short_name}: {e}")
                    continue

            # Update sync point only if we fetched issues
            if total_issues_fetched > 0:
                current_time = get_epoch_timestamp_in_ms()
                await self.issues_sync_point.update_sync_point("issues", {"last_sync_time": current_time})
                self.logger.info(f"Jira sync completed. Total: {total_issues_synced} issues "
                               f"(New: {total_new_issues}, Updated: {total_updated_issues})")
            else:
                self.logger.info("Jira sync completed - no new/updated issues found")

        except Exception as e:
            self.logger.error(f"Error during Jira sync: {e}", exc_info=True)
            raise
        finally:
            self._sync_in_progress = False
            # Session cleanup now handled by Client layer


    async def _fetch_users(self, org_id: str) -> List[AppUser]:
        """Fetch all active Jira users using DataSource"""
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        users: List[Dict[str, Any]] = []
        start_at = 0

        while True:
            # Use get_all_users which doesn't require query parameter
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_all_users(
                maxResults=DEFAULT_MAX_RESULTS,
                startAt=start_at
            )

            if response.status != HTTP_STATUS_OK:
                raise Exception(f"Failed to fetch users: {response.text()}")

            users_batch = response.json()

            if isinstance(users_batch, list):
                batch_users = users_batch
            else:
                batch_users = users_batch.get("values", [])

            if not batch_users:
                break

            users.extend(batch_users)

            if len(batch_users) < DEFAULT_MAX_RESULTS:
                break

            start_at += len(batch_users)

        app_users: List[AppUser] = []
        users_without_email = 0
        inactive_users = 0

        for user in users:
            account_id = user.get("accountId")

            # Only include active users
            if not user.get("active", True):
                inactive_users += 1
                continue

            # Skip users without email address
            email = user.get("emailAddress")
            if not email:
                users_without_email += 1
                self.logger.debug(
                    f"Skipping user {account_id} ({user.get('displayName')}) - no email address"
                )
                continue

            app_user = AppUser(
                app_name=Connectors.JIRA,
                source_user_id=account_id,
                org_id=org_id,
                email=email,
                full_name=user.get("displayName", email),
                is_active=user.get("active", True)
            )
            app_users.append(app_user)

        self.logger.info(
            f"Fetched {len(app_users)} users total, "
            f"skipped {users_without_email} users without email, "
            f"skipped {inactive_users} inactive users"
        )
        return app_users


    async def _fetch_projects(self) -> List[Tuple[RecordGroup, List[Permission]]]:
        """Fetch all projects with pagination using DataSource"""
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        projects: List[Dict[str, Any]] = []
        start_at = 0

        while True:
            # Use DataSource instead of manual HTTP call
            datasource = await self._get_fresh_datasource()
            response = await datasource.search_projects(
                maxResults=DEFAULT_MAX_RESULTS,
                startAt=start_at,
                expand=["description", "url", "permissions", "issueTypes"]
            )

            if response.status != HTTP_STATUS_OK:
                raise Exception(f"Failed to fetch projects: {response.text()}")

            projects_batch = response.json()
            batch_projects = projects_batch.get("values", [])
            projects.extend(batch_projects)

            start_at = projects_batch.get("startAt", 0) + len(batch_projects)
            total = projects_batch.get("total", 0)

            if start_at >= total or len(batch_projects) == 0:
                break

        record_groups: List[Tuple[RecordGroup, List[Permission]]] = []
        for project in projects:
            project_id = project.get("id")
            project_name = project.get("name")
            project_key = project.get("key")

            description = project.get("description")
            if description and isinstance(description, dict):
                description = adf_to_text(description)
            elif not description:
                description = None

            record_group = RecordGroup(
                id=str(uuid4()),
                external_group_id=project_id,
                connector_name=Connectors.JIRA,
                name=project_name,
                short_name=project_key,
                group_type=RecordGroupType.JIRA_PROJECT,
                origin=OriginTypes.CONNECTOR,
                description=description,
                web_url=project.get("url"),
            )
            record_groups.append((record_group, []))

        return record_groups


    async def _fetch_issues(
        self,
        project_key: str,
        project_id: str,
        users: List[AppUser],
        last_sync_time: Optional[int] = None,
        org_id: Optional[str] = None
    ) -> List[Tuple[Record, List[Permission]]]:
        """Fetch issues for a project using JQL search - only new/updated if last_sync_time provided"""
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        if not self.cloud_id:
            self.logger.error("cloud_id is not set. Cannot fetch issues.")
            return []

        issues: List[Dict[str, Any]] = []

        # Build JQL query starting with project filter
        jql_conditions = [f'project = "{project_key}"']

        # Build date filter parameters from sync filters (matching Confluence pattern)
        # Get modified filter
        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED) if self.sync_filters else None
        modified_after = None
        modified_before = None

        if modified_filter:
            modified_after, modified_before = modified_filter.get_value(default=(None, None))

        # Get created filter
        created_filter = self.sync_filters.get(SyncFilterKey.CREATED) if self.sync_filters else None
        created_after = None
        created_before = None

        if created_filter:
            created_after, created_before = created_filter.get_value(default=(None, None))

        # Merge modified_after with last_sync_time (use the latest to avoid re-syncing old data)
        if modified_after and last_sync_time:
            modified_after = max(modified_after, last_sync_time)
            self.logger.info(f"Using latest modified_after: {modified_after} (filter + checkpoint merged)")
        elif modified_after:
            self.logger.info(f"Using filter: fetching issues modified after {modified_after}")
        elif last_sync_time:
            # Apply time buffer for incremental sync to catch updates that might have been missed
            buffer_minutes = JQL_TIME_BUFFER_MINUTES
            modified_after = last_sync_time - (buffer_minutes * 60 * 1000)
            self.logger.info(f"Incremental sync: fetching issues modified after checkpoint with {buffer_minutes}min buffer")
        else:
            self.logger.info("Full sync: fetching all issues (first time)")

        # Build JQL conditions from date filters
        if modified_after:
            modified_dt = datetime.fromtimestamp(modified_after / 1000, tz=timezone.utc)
            jql_conditions.append(f'updated >= "{modified_dt.strftime("%Y-%m-%d %H:%M")}"')

        if modified_before:
            modified_dt = datetime.fromtimestamp(modified_before / 1000, tz=timezone.utc)
            jql_conditions.append(f'updated <= "{modified_dt.strftime("%Y-%m-%d %H:%M")}"')

        if created_after:
            created_dt = datetime.fromtimestamp(created_after / 1000, tz=timezone.utc)
            jql_conditions.append(f'created >= "{created_dt.strftime("%Y-%m-%d %H:%M")}"')

        if created_before:
            created_dt = datetime.fromtimestamp(created_before / 1000, tz=timezone.utc)
            jql_conditions.append(f'created <= "{created_dt.strftime("%Y-%m-%d %H:%M")}"')

        # Build final JQL with ORDER BY clause (required to avoid unbounded query error)
        jql = " AND ".join(jql_conditions) + " ORDER BY updated ASC"
        self.logger.info(f"JQL Query: {jql}")

        next_page_token: Optional[str] = None
        page_count = 0

        while page_count < MAX_PAGES_PER_PROJECT:
            page_count += 1

            try:
                # Use POST version of enhanced search API to avoid unbounded query restrictions
                datasource = await self._get_fresh_datasource()
                response = await datasource.search_and_reconsile_issues_using_jql_post(
                    jql=jql,
                    maxResults=DEFAULT_MAX_RESULTS,
                    nextPageToken=next_page_token,
                    fields=ISSUE_SEARCH_FIELDS,
                    expand="renderedFields"
                )

                if response.status != HTTP_STATUS_OK:
                    raise Exception(f"Failed to fetch issues: {response.text()}")

                issues_batch = response.json()

            except Exception as e:
                if "400" in str(e):
                    self.logger.warning(f"Got 400 with project key, trying with project ID {project_id}")
                    jql = f'project = {project_id}'
                    try:
                        datasource = await self._get_fresh_datasource()
                        response = await datasource.search_and_reconsile_issues_using_jql_post(
                            jql=jql,
                            maxResults=DEFAULT_MAX_RESULTS,
                            nextPageToken=next_page_token,
                            fields=ISSUE_SEARCH_FIELDS
                        )
                        if response.status == HTTP_STATUS_OK:
                            issues_batch = response.json()
                            self.logger.info("Successfully fetched issues using project ID")
                        else:
                            self.logger.error(f"Failed to fetch issues with project ID {project_id}: {response.text()}")
                            break
                    except Exception as id_e:
                        self.logger.error(f"Failed to fetch issues with project ID {project_id}: {id_e}")
                        break
                else:
                    raise

            batch_issues = issues_batch.get("issues", [])
            if not batch_issues:
                break

            issues.extend(batch_issues)

            # Get next page token from enhanced search API
            new_token = issues_batch.get("nextPageToken")

            # Break if no token or same token (prevents infinite loop)
            if not new_token or new_token == next_page_token:
                break

            next_page_token = new_token

        if page_count >= MAX_PAGES_PER_PROJECT:
            self.logger.warning(f"Reached max page limit ({MAX_PAGES_PER_PROJECT}) for project {project_key}")

        # Use transaction for efficient database lookups
        async with self.data_store_provider.transaction() as tx_store:
            return await self._build_issue_records(issues, project_id, users, tx_store, org_id)

    async def _build_issue_records(
        self,
        issues: List[Dict[str, Any]],
        project_id: str,
        users: List[AppUser],
        tx_store,
        org_id: str
    ) -> List[Tuple[Record, List[Permission]]]:
        """Build issue records with permissions from raw issue data"""
        all_records: List[Tuple[Record, List[Permission]]] = []
        # Use the user-facing site URL for weburl construction
        atlassian_domain = self.site_url if self.site_url else ""

        # Create accountId -> AppUser lookup for matching issue creators/assignees
        user_by_account_id = {user.source_user_id: user for user in users if user.source_user_id}

        for issue in issues:
            issue_id = issue.get("id")
            issue_key = issue.get("key")
            fields = issue.get("fields", {})
            issue_summary = fields.get("summary") or f"Issue {issue_key}"

            # Extract description (ADF to text conversion)
            description_adf = fields.get("description")
            description_text = adf_to_text(description_adf) if description_adf else None

            # Extract issue type
            issue_type_obj = fields.get("issuetype", {})
            issue_type = issue_type_obj.get("name") if issue_type_obj else None

            # Build record name with issue type for better searchability
            # Format: "[Bug] Issue summary" or "[Story] Issue summary"
            if issue_type:
                issue_name = f"[{issue_type}] {issue_summary}"
            else:
                issue_name = issue_summary

            # Add issue type to description for full searchability
            if issue_type and description_text:
                description = f"Issue Type: {issue_type}\n\n{description_text}"
            elif issue_type:
                description = f"Issue Type: {issue_type}"
            else:
                description = description_text

            status_obj = fields.get("status", {})
            status = status_obj.get("name") if status_obj else None

            priority_obj = fields.get("priority", {})
            priority = priority_obj.get("name") if priority_obj else None

            # Extract user information by accountId (email not available in issue fields)
            creator = fields.get("creator")
            creator_account_id = creator.get("accountId") if creator else None
            creator_name = creator.get("displayName") if creator else None
            creator_email = None
            if creator_account_id and creator_account_id in user_by_account_id:
                creator_email = user_by_account_id[creator_account_id].email

            assignee = fields.get("assignee")
            assignee_account_id = assignee.get("accountId") if assignee else None
            assignee_name = assignee.get("displayName") if assignee else None
            assignee_email = None
            if assignee_account_id and assignee_account_id in user_by_account_id:
                assignee_email = user_by_account_id[assignee_account_id].email

            # Simple permissions: creator and assignee only
            permissions = self._build_permissions(creator_email, assignee_email)

            created_at = self._parse_jira_timestamp(fields.get("created"))
            updated_at = self._parse_jira_timestamp(fields.get("updated"))

            # Check for existing record to handle updates properly
            existing_record = await tx_store.get_record_by_external_id(
                connector_name=Connectors.JIRA,
                external_id=issue_id
            )

            record_id = existing_record.id if existing_record else str(uuid4())
            is_new = existing_record is None

            # Only increment version if issue content actually changed
            # Check if source_updated_at (issue's updated timestamp) has changed
            if is_new:
                version = 0
            elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
                version = existing_record.version + 1  # Issue content changed
            else:
                version = existing_record.version if existing_record else 0  # Issue unchanged

            issue_record = TicketRecord(
                id=record_id,
                org_id=org_id,
                priority=priority,
                status=status,
                summary=issue_name,
                description=description,
                creator_email=creator_email,
                creator_name=creator_name,
                assignee=assignee_name,
                assignee_email=assignee_email,
                external_record_id=issue_id,
                record_name=issue_name,
                record_type=RecordType.TICKET,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.JIRA,
                record_group_type=RecordGroupType.JIRA_PROJECT,
                external_record_group_id=project_id,
                version=version,
                mime_type=MimeTypes.PLAIN_TEXT.value,
                weburl=f"{atlassian_domain}/browse/{issue_key}" if atlassian_domain else None,
                source_created_at=created_at,
                source_modified_at=updated_at,
                created_at=created_at,
                updated_at=updated_at
            )

            # Set indexing status based on filter
            if self.indexing_filters:
                issues_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.ISSUES)
                if not issues_indexing_enabled:
                    issue_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            all_records.append((issue_record, permissions))

            # Fetch comments for this issue (always fetch, not just for updated issues)
            # This ensures we capture new comments even when the parent issue hasn't changed
            should_fetch_comments = True
            if self.indexing_filters:
                should_fetch_comments = self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_COMMENTS)
            
            self.logger.debug(f"Should fetch comments for {issue_key}: {should_fetch_comments}")

            if should_fetch_comments:
                try:
                    comment_records = await self._fetch_issue_comments(
                        issue_id,
                        issue_key,
                        permissions,
                        project_id,
                        org_id,
                        user_by_account_id,
                        tx_store
                    )
                    if comment_records:
                        all_records.extend(comment_records)
                        self.logger.debug(f"Added {len(comment_records)} comments for issue {issue_key}")
                except Exception as e:
                    self.logger.error(f"Failed to fetch comments for issue {issue_key}: {e}")

        return all_records

    async def _fetch_issue_comments(
        self,
        issue_id: str,
        issue_key: str,
        parent_permissions: List[Permission],
        project_id: str,
        org_id: str,
        user_by_account_id: Dict[str, AppUser],
        tx_store
    ) -> List[Tuple[CommentRecord, List[Permission]]]:
        """
        Fetch comments for an issue.

        Args:
            issue_id: Jira issue ID
            issue_key: Jira issue key (e.g., PIPBACKEND-123)
            parent_permissions: Permissions inherited from parent issue
            project_id: Project ID for the issue
            org_id: Organization ID
            user_by_account_id: Lookup dict for matching accountId to AppUser
            tx_store: Transaction store for checking existing records

        Returns:
            List of tuples (CommentRecord, permissions)
        """
        comment_records: List[Tuple[CommentRecord, List[Permission]]] = []

        try:
            if not self.data_source:
                raise ValueError("DataSource not initialized")

            # Use DataSource to fetch comments
            start_at = 0
            all_comments = []

            while True:
                response = await self.data_source.get_comments(
                    issueIdOrKey=issue_id,
                    maxResults=DEFAULT_MAX_RESULTS,
                    startAt=start_at
                )

                if response.status != HTTP_STATUS_OK:
                    raise Exception(f"Failed to fetch comments: {response.text()}")

                comment_data = response.json()
                comments = comment_data.get("comments", [])

                if not comments:
                    break

                all_comments.extend(comments)
                self.logger.debug(f"Fetched {len(comments)} comments for issue {issue_key}, total so far: {len(all_comments)}")

                # Check if there are more comments
                total = comment_data.get("total", 0)
                current_start = comment_data.get("startAt", 0)
                max_results = comment_data.get("maxResults", DEFAULT_MAX_RESULTS)

                if current_start + max_results >= total:
                    break

                start_at = current_start + max_results

            if not all_comments:
                self.logger.debug(f"No comments found for issue {issue_key}")
                return []

            self.logger.info(f"Processing {len(all_comments)} comments for issue {issue_key}")

            # Process each comment
            for comment in all_comments:
                comment_id = comment.get("id")

                # Check for existing comment record
                existing_record = await tx_store.get_record_by_external_id(
                    connector_name=Connectors.JIRA,
                    external_id=f"comment_{comment_id}"
                )

                # Parse timestamps first (needed for version check)
                created_at = self._parse_jira_timestamp(comment.get("created"))
                updated_at = self._parse_jira_timestamp(comment.get("updated"))

                record_id = existing_record.id if existing_record else str(uuid4())
                is_new = existing_record is None

                # Check if source_updated_at (comment's updated timestamp) has changed
                if is_new:
                    version = 0
                elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
                    version = existing_record.version + 1  # Comment content changed
                else:
                    version = existing_record.version if existing_record else 0  # Comment unchanged
                    version = existing_record.version  # Comment unchanged

                # Extract comment content (ADF to text)
                body_adf = comment.get("body")
                content = adf_to_text(body_adf) if body_adf else ""

                # Extract author info using accountId lookup
                author = comment.get("author", {})
                author_account_id = author.get("accountId")
                author_name = author.get("displayName", "Unknown")
                author_email = None
                if author_account_id and author_account_id in user_by_account_id:
                    author_email = user_by_account_id[author_account_id].email

                # Comment name format: "Comment by Author on Issue KEY"
                comment_name = f"Comment by {author_name} on {issue_key}"

                # Create CommentRecord
                comment_record = CommentRecord(
                    id=record_id,
                    org_id=org_id,
                    record_name=comment_name,
                    record_type=RecordType.COMMENT,
                    external_record_id=f"comment_{comment_id}",
                    parent_external_record_id=issue_id,
                    parent_record_type=RecordType.TICKET,
                    external_record_group_id=project_id,
                    connector_name=Connectors.JIRA,
                    origin=OriginTypes.CONNECTOR,
                    version=version,
                    mime_type=MimeTypes.PLAIN_TEXT.value,
                    record_group_type=RecordGroupType.JIRA_PROJECT,
                    created_at=created_at,
                    updated_at=updated_at,
                    source_created_at=created_at,
                    source_updated_at=updated_at,
                    author_source_id=author_account_id,
                )

                # Set indexing status based on filter
                if self.indexing_filters:
                    comments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_COMMENTS)
                    if not comments_indexing_enabled:
                        comment_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                # Comments inherit permissions from parent issue
                comment_permissions = parent_permissions.copy()

                # Add author to permissions if not already there
                if author_email:
                    author_has_permission = any(
                        p.email == author_email for p in comment_permissions
                    )
                    if not author_has_permission:
                        comment_permissions.append(Permission(
                            entity_type=EntityType.USER,
                            email=author_email,
                            type=PermissionType.READ,
                        ))

                comment_records.append((comment_record, comment_permissions))

            self.logger.info(f"Returning {len(comment_records)} comment records for issue {issue_key}")
            return comment_records

        except Exception as e:
            self.logger.error(f"Failed to fetch comments for issue {issue_key}: {e}", exc_info=True)
            return []

    def _build_permissions(
        self,
        creator_email: Optional[str],
        assignee_email: Optional[str]
    ) -> List[Permission]:
        """
        Build simple permissions list for an issue.
        Creator and assignee get OWNER permission.

        Args:
            creator_email: Issue creator email
            assignee_email: Issue assignee email

        Returns:
            List of Permission objects
        """
        permissions: List[Permission] = []
        processed_emails: set = set()

        # Add creator permission
        if creator_email and creator_email not in processed_emails:
            permissions.append(Permission(
                entity_type=EntityType.USER,
                email=creator_email,
                type=PermissionType.OWNER,
            ))
            processed_emails.add(creator_email)

        # Add assignee permission (if different from creator)
        if assignee_email and assignee_email not in processed_emails:
            permissions.append(Permission(
                entity_type=EntityType.USER,
                email=assignee_email,
                type=PermissionType.OWNER,
            ))
            processed_emails.add(assignee_email)

        return permissions

    async def _fetch_issue_content(self, issue_id: str) -> str:
        """Fetch full issue content for streaming using DataSource"""
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        # Use DataSource to get issue details
        datasource = await self._get_fresh_datasource()
        response = await datasource.get_issue(
            issueIdOrKey=issue_id,
            expand=["renderedFields"]
        )

        if response.status != HTTP_STATUS_OK:
            raise Exception(f"Failed to fetch issue content: {response.text()}")

        issue_details = response.json()
        fields = issue_details.get("fields", {})
        description = fields.get("description", "")
        summary = fields.get("summary", "")

        summary_text = f"Title: {summary}" if summary else ""
        description_text = f"Description: {adf_to_text(description)}" if description else ""
        combined_text = f"# {summary_text}\n\n{description_text}"

        return combined_text

    async def get_signed_url(self, record: Record) -> str:
        """Create a signed URL for a specific record"""
        return ""

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Jira using DataSource"""
        try:
            if not self.data_source:
                await self.init()

            # Test by fetching user info (simple API call)
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_current_user()
            return response.status == HTTP_STATUS_OK
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return False

    async def run_incremental_sync(self) -> None:
        """Run incremental sync - calls run_sync which handles incremental logic"""
        await self.run_sync()

    async def cleanup(self) -> None:
        """Cleanup resources - now handled by Client layer"""
        # Client layer handles session cleanup automatically
        pass

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex records - not implemented for Jira yet."""
        self.logger.warning("Reindex not implemented for Jira connector")
        pass

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications"""
        pass

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream issue content"""
        try:
            if not self.cloud_id:
                await self.init()

            issue_id = record.external_record_id
            issue_content = await self._fetch_issue_content(issue_id)

            return StreamingResponse(
                iter([issue_content.encode('utf-8')]),
                media_type=MimeTypes.PLAIN_TEXT.value,
                headers={}
            )
        except Exception as e:
            self.logger.error(f"Error streaming issue {record.external_record_id}: {e}")
            raise

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService
    ) -> "BaseConnector":
        """Factory method to create JiraConnector instance"""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )
        await data_entities_processor.initialize()

        return JiraConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service
        )
