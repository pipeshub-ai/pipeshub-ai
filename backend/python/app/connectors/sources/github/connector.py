import base64
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import Logger, exception
from tokenize import String
from typing import Dict, List, Optional, Tuple

import requests
from fastapi.responses import StreamingResponse
from github.Issue import Issue

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
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.sources.github.common.apps import GithubApp
from app.models.entities import (
    AppUser,
    Record,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.github.github import (
    GitHubClient,
    GitHubConfig,
    GitHubResponse,
)
from app.sources.external.github.github_ import GitHubDataSource

# below to be removed
TOKEN = os.getenv("GITHUB_PAT")
OAUTH_GITHUB_CONFIG_PATH = "/services/connectors/github/config"

@dataclass
class IssueUpdate:
    """Tracks updates to a Ticket"""
    record:Optional[TicketRecord]
    is_new: bool
    is_updated: bool
    is_deleted: bool
    metadata_changed: bool
    content_changed: bool
    permissions_changed: bool
    old_permissions: Optional[List[Permission]] = None
    new_permissions: Optional[List[Permission]] = None
    external_record_id: Optional[str] = None

@ConnectorBuilder("Github")\
    .in_group("Github")\
    .with_auth_type("API_TOKEN")\
    .with_description("Sync content from your Github instance")\
    .with_categories(["Knowledge Management"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/bookstack.svg")\
        .add_documentation_link(DocumentationLink(
            "Github API Docs",
            "https://docs.github.com/en",
            "docs"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/bookstack/bookstack',
            'pipeshub'
        ))
        .with_redirect_uri("", False)
        .add_auth_field(AuthField(
            name="token_id",
            display_name="Token ID",
            placeholder="YourTokenID",
            description="The Token ID generated from your Github profile",
            field_type="TEXT",
            max_length=100
        ))
        .with_sync_support(True)
        .with_agent_support(False)
    )\
    .build_decorator()
class GithubConnector(BaseConnector):
    """
    Connector for synching data from a Github instance.
    """
    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id:str
    ) -> None:
        super().__init__(
            GithubApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
        self.connector_name=Connectors.GITHUB
        self.connector_id=connector_id
        self._create_sync_points()
        self.data_source: Optional[GitHubDataSource] = None
        self.batch_size = 100
        self.max_concurrent_batches = 5

    def _create_sync_points(self) -> None:
        """Initialize sync points for different data types."""
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )

        self.record_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.app_role_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

    async def init(self) -> bool:
        """_summary_

        Returns:
            bool: _description_
        """
        try:
            # Initialize client and datasource
            config =await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            self.logger.info(config)
            if not config:
                self.logger.error("Github configuration not found.")
                return False

            credentials_config = config.get("auth", {})
            token_id = credentials_config.get("token_id")

            if not all([token_id]):
                self.logger.error(
                    "Github token not found in configuration."
                )
                return False
            # Initialize Github client with the correct config fields
            # edit this for page limiting
            token_config=GitHubConfig(
                token=token_id,
                per_page=2
            )
            try:
                client = GitHubClient.build_with_config(token_config)
            except ValueError as e:
                self.logger.error(f"Failed to initialize Github client: {e}", exc_info=True)
                return False
            self.data_source = GitHubDataSource(client)
            self.logger.info("Github client initialized successfully.")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize Github client: {e}", exc_info=True)
            return False

    async def test_connection_and_access(self) -> bool:
        """_summary_

        Returns:
            bool: _description_
        """
        if not self.data_source:
            self.logger.error("Github data source not initialized")
            return False
        try:
            response:GitHubResponse =self.data_source.get_authenticated()
            if response.success:
                self.logger.info("Github connection test successful.")
                return True
            else:
                self.logger.error(f"Github connection test failed: {response.error}")
                return False
        except exception as e:
            self.logger.error(f"Github connection test failed: {e}", exc_info=True)
            return False

    async def stream_record(self,record:Record)->StreamingResponse:
            self.logger.info("🟣🟣🟣 STREAM_RECORD_MARKER 🟣🟣🟣")
            start_time = time.perf_counter()
            raw_url=record.weburl.split('/')
            self.logger.info(raw_url)
            repo_name=raw_url[4]
            username=raw_url[3]
            issue_number=int(raw_url[6])
            issue =  self.data_source.get_issue(owner=username,repo=repo_name,number=issue_number)
            # self.logger.info(type(issue))
            # self.logger.info(type(issue[0]))
            # self.logger.info(issue.data)
            # self.logger.info(issue.body)
            markdown_content:String =issue.data.body
            self.logger.info(markdown_content)
            IMG_SRC_REGEX = r'<img[^>]+src=[\'"]([^\'"]+)[\'"]'
            # img_url_list = re.findall(IMG_SRC_REGEX, markdown_content)
            matches: List[Tuple[re.Match, str]] = []
            for match in re.finditer(IMG_SRC_REGEX, markdown_content):
                self.logger.info(match)
                img_url = match.group(1)
                matches.append((match, img_url))
            self.logger.info(matches)
            if matches:
                url_to_base64: Dict[str, Optional[str]] = {}

                for _, url in matches:
                    if url in url_to_base64:
                        continue  # cache

                    try:
                        result = await self.get_img_bytes(img_url)
                    except Exception as exc:
                        self.logger.warning(f"Failed to fetch image {url}: {exc}")
                        url_to_base64[url] = None
                        continue

                    # If your fetch returns an Exception instead of raising:
                    if isinstance(result, Exception):
                        self.logger.warning(f"Failed to fetch image {url}: {result}")
                        url_to_base64[url] = None
                        continue
                    # below mime types donot work as <img .... is only for png types for jpg, svg ![Image](https://github.c is the format
                    # images are rendered on preview, not for docs,pdf only links

                    # ---- detect MIME type ----
                    lower = url.lower()
                    if lower.endswith(".gif"):
                        mime = "image/gif"
                    elif lower.endswith(".jpg") or lower.endswith(".jpeg"):
                        mime = "image/jpeg"
                    elif lower.endswith(".webp"):
                        mime = "image/webp"
                    elif lower.endswith(".svg"):
                        mime = "image/svg+xml"
                    else:
                        mime = "image/png"

                    base64_data = base64.b64encode(result).decode("utf-8")
                    url_to_base64[url] = f"![Image](data:{mime};base64,{base64_data})"
                    #  markdown_image = f"![Image](data:image/png;base64,{base64_image})"
                # ---- 3) Rebuild once (O(n + k)) ----
                result_parts: List[str] = []
                cursor = 0

                for match, url in matches:
                    start, end = match.span()

                    # copy text before this tag
                    result_parts.append(markdown_content[cursor:start])

                    # extract alt text
                    # alt_match = re.search(ALT_REGEX, match.group(0))
                    # alt_text = alt_match.group(1) if alt_match else "Image"

                    base64_url = url_to_base64.get(url)

                    if base64_url:
                        result_parts.append(f"!{base64_url}")
                    else:
                        # keep original tag if we failed
                        result_parts.append(match.group(0))

                    cursor = end

                # add tail
                result_parts.append(markdown_content[cursor:])
                markdown_content = "".join(result_parts)
            # return "".join(result_parts)
            # for img_url in img_url_list:
            #     img_bytes= await self.get_img_bytes(img_url)
            #     base64_image = base64.b64encode(img_bytes).decode("utf-8")
            #     markdown_image = f"![Image](data:image/png;base64,{base64_image})"
            #     markdown_content=markdown_content + markdown_image
            #     self.logger.info(img_url)
            # break
            self.logger.info(markdown_content)
            end_time = time.perf_counter()
            self.logger.info(f"Markdown size (chars): {len(markdown_content)}")
            elapsed_time = end_time - start_time
            self.logger.info(f"⏱️ Time taken for URL parsing and get_issue call: {elapsed_time:.4f} seconds")

            # DISCUSS ON WHAT SIZE OF CHUNKS TO BE SENT as of now NOT USED
            def stream_markdown(markdown_content, chunk_size=16000):  # 8KB chunks
                """Stream markdown content in optimal chunks"""
                for i in range(0, len(markdown_content), chunk_size):
                    yield markdown_content[i:i+chunk_size]

            return StreamingResponse(
                content = stream_markdown(markdown_content),
                # content= [markdown_content],
                media_type=record.mime_type if record.mime_type else "application/octet-stream",
                headers={
                    "Content-Disposition": f"attachment; filename={record.record_name}"
                }
            )

    async def run_sync(self):
            try:
                # syncing all issues-> subissues-> comments
                await self._sync_issues()
            except Exception as ex:
                self.logger.error(f"Error in Github connector run: {ex}", exc_info=True)
                raise
            # await self._get_issues()

    #---------------------------Issues Sync-----------------------------------#
    async def _sync_issues(self,full_sync:bool = False) ->None:

        current_timestamp = self._get_iso_time()
        github_record_sync_key = generate_record_sync_point_key('github','records','global')
        github_record_sync_point = await self.record_sync_point.read_sync_point(github_record_sync_key)

        # as of now no info. on roles
        # using below one as default

        users = await self.data_entities_processor.get_all_active_users() # to be changed
        if not users:
            self.logger.info("No users found")
            return
        if full_sync or not github_record_sync_point.get('timestamp'):
            await self._sync_issues_full(users,for_sub_issues=False)
            await self._sync_issues_full(users,for_sub_issues=True)
            #TODO: remove users from here : ask real role of full sync issue!!
            await self.record_sync_point.update_sync_point(
                github_record_sync_key,
                {"timestamp" : current_timestamp}
            )
        else:
            last_sync_timestamp = github_record_sync_point.get("timestamp")
            await self._sync_records_incremental(last_sync_timestamp,users)
            await self.record_sync_point.update_sync_point(
                github_record_sync_key,
                {"timestamp" : current_timestamp}
            )

    async def _sync_issues_full(self,users:List[AppUser],for_sub_issues:bool = False)->None:
        """_summary_

        Args:
            users (List[AppUser]): _description_
        """
        self.logger.info("Starting sync for issues as records.")
        # old way
        # await self._get_issues()
        # TODO: ask to improve this as api call getting wasted just to get username on github
        # TODO: no need of processing issues, subissues by parts REDUCE code length !!
        auth_res = self.data_source.get_authenticated()
        user_login = auth_res.data.login
        owner = user_login  # Use the same user as owner
        repo = os.getenv("GITHUB_REPO")
        issues_res = self.data_source.list_issues(owner, repo,state='all')
        batch_tickets:List[Tuple[TicketRecord,List[Permission]]]=[]

        if issues_res.success and issues_res.data:
            for issue in issues_res.data:
                parent_issue_ul:Dict = getattr(issue, "raw_data", None)
                parent_issue_url=parent_issue_ul.get("parent_issue_url",None)
                pull_request = getattr(issue,"pull_request",None)
                if pull_request is None:
                    if parent_issue_url is None and not for_sub_issues:
                        issue_update:IssueUpdate = await self._process_issues_to_tickets(issue=issue,for_sub_issue=for_sub_issues)
                        if issue_update:
                            if issue_update.is_updated:
                                await self._handle_record_updates(issue_update)
                                continue
                            if issue_update.record:
                                batch_tickets.append((issue_update.record,issue_update.new_permissions or []))
                                if len(batch_tickets) >= self.batch_size:
                                    self.logger.info(f"Processing batch of {len(batch_tickets)} tickets.")
                                    await self.data_entities_processor.on_new_records(batch_tickets)
                                    batch_tickets = []
                    elif parent_issue_url is not None and for_sub_issues:
                        issue_update:IssueUpdate = await self._process_issues_to_tickets(issue=issue,for_sub_issue=for_sub_issues)
                        if issue_update:
                            if issue_update.is_updated:
                                await self._handle_record_updates(issue_update)
                                continue
                            if issue_update.record:
                                batch_tickets.append((issue_update.record,issue_update.new_permissions or []))
                                if len(batch_tickets) >= self.batch_size:
                                    self.logger.info(f"Processing batch of {len(batch_tickets)} tickets.")
                                    await self.data_entities_processor.on_new_records(batch_tickets)
                                    batch_tickets = []
            if batch_tickets:
                self.logger.info(f"Processing last batch of {len(batch_tickets)} tickets.")
                await self.data_entities_processor.on_new_records(batch_tickets)
            self.logger.info("✅ Finished syncing issues as records.")
        else:
            self.logger.error(f"Failed to get issues: {issues_res.error}")

    async def _get_issues(self):
        # response = await self.data_source.get_repo()
        auth_res = self.data_source.get_authenticated()
        # print("Authenticated User", auth_res)
        user_login = auth_res.data.login
        owner = user_login  # Use the same user as owner
        # Mention your repo name for testing
        # repo = "pipeshub-ai"  # Use this repository for testing, fork this repository to your account and give a star :D
        repo = "ayush_ign"
        # read from .env
        # repo_res = await self.data_source.get_repo(owner, repo)
        issues_res = self.data_source.list_issues(owner, repo,state='all')
        # print(issues_res.data)
        # only_issues=[]
        # one_issue = None

        for issue in issues_res.data:
            parent_issue_url = getattr(issue, "parent_issue_url", None)
            pull_request = getattr(issue,"pull_request",None)
            if pull_request is None:
                if parent_issue_url is None:
                    await self._process_issues_to_tickets(issue=issue)
                    # break
                # print(issue.number)
                # only_issues.append(issue)
                # one_issue = issue
                # break

    async def _process_issues_to_tickets(self,issue:Issue,for_sub_issue:bool = False)-> Optional[IssueUpdate]:
        """_summary_

        Args:
            issue (Issue): _description_
        """
        try:
            # TODO: remove below things once permissions users discussed

            users = await self.data_entities_processor.get_all_active_users()
            if not users:
                self.logger.info("No users found")
                return None
            user=users[0]
            permissions=[Permission(
                entity_type=EntityType.USER,
                email=user.email,
                type=PermissionType.OWNER
            )]
            # check if record already exists
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=f"{issue.url}"
                )
            # detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False
            if existing_record:
                # TODO: add more changes especially body ones as of now default fallback to full body reindexing
                # check if title changed
                if existing_record.record_name != issue.title:
                    metadata_changed = True
                    is_updated = True
                # check if status changed
                # if existing_record.status != issue.state:
                #     metadata_changed = True
                #     is_updated = True
                # TODO: body changes check as of now True default
                content_changed = True
                is_updated = True
            # NOTE: using url as external record id as it is unique and can be used to fetch the issue, used as sub_issue parent
            parent_external_id = None
            parent_record_type = None
            if for_sub_issue:
                parent_issue_ul:Dict = getattr(issue, 'raw_data', None)
                parent_issue_url=parent_issue_ul.get('parent_issue_url',None)
                parent_external_id = parent_issue_url
                parent_record_type = RecordType.TICKET
            # else:
                # parent_external_id = issue.repository_url
                # parent_record_type = RecordType.REPOSITORY

            ticket_record = TicketRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=issue.title,
                external_record_id=str(issue.url),
                record_type = RecordType.TICKET.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=str(self.datetime_to_epoch_ms(issue.updated_at)),
                source_created_at= str(self.datetime_to_epoch_ms(issue.created_at)),
                version = 0,# not used further so 0
                external_record_group_id=issue.repository_url,
                org_id=self.data_entities_processor.org_id,
                # parent_record_type=RecordType.TICKET, #only for sub issues
                # TODO: if this is a sub issue then pass external issue id as parent
                record_group_type=RecordGroupType.REPOSITORY,
                parent_external_record_id=parent_external_id,
                parent_record_type=parent_record_type,
                mime_type=MimeTypes.MARKDOWN,
                weburl=issue.html_url,
                status=issue.state,
                # TODO: lookup db for user mail, assignee ... ask srikant
                creator_email=user.email,
                assignee=user.email,
                creator_name=issue.user.login,
                external_revision_id=str(self.datetime_to_epoch_ms(issue.updated_at)),
                preview_renderable=False
            )
            #TODO: work on permissioning then update parts here
            return IssueUpdate(
                record=ticket_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                old_permissions=permissions,
                new_permissions=permissions,
                external_record_id=str(issue.url)
            )
        except Exception as e:
            self.logger.error(f"Error in processing issues to tickets: {e}", exc_info=True)
            return None

    async def _process_sub_issues_to_tickets(self,issue:Issue)-> Optional[IssueUpdate]:
        """_summary_
            as of now included in issues itself with for_sub)issue flag.
        Args:
            issue (Issue): _description_

        Returns:
            Optional[IssueUpdate]: _description_
        """
        try:
            # TODO: remove below things once permissions users discussed
            users = await self.data_entities_processor.get_all_active_users()
            if not users:
                self.logger.info("No users found")
                return None
            user=users[0]
            permissions=[Permission(
                entity_type=EntityType.USER,
                email=user.email,
                type=PermissionType.OWNER
            )]
            # check if record already exists
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=f"{issue.url}"
                )
            # detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False
            if existing_record:
                # TODO: add more changes especially body ones as of now default fallback to full body reindexing
                # check if title changed
                if existing_record.record_name != issue.title:
                    metadata_changed = True
                    is_updated = True
                # check if status changed
                if existing_record.status != issue.state:
                    metadata_changed = True
                    is_updated = True
                # TODO: body changes check as of now True default
                content_changed = True
                is_updated = True
            # NOTE: using url as external record id as it is unique and can be used to fetch the issue, used as sub_issue parent

            ticket_record = TicketRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=issue.title,
                external_record_id=str(issue.url),
                record_type = RecordType.TICKET.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=str(self.datetime_to_epoch_ms(issue.updated_at)),
                source_created_at= str(self.datetime_to_epoch_ms(issue.created_at)),
                version = 0,# not used further so 0
                external_record_group_id=issue.repository_url,
                org_id=self.data_entities_processor.org_id,
                # parent_record_type=RecordType.TICKET, #only for sub issues
                # TODO: if this is a sub issue then pass external issue id
                record_group_type=RecordGroupType.REPOSITORY,
                mime_type=MimeTypes.MARKDOWN,
                weburl=issue.html_url,
                status=issue.state,
                # TODO: lookup db for user mail, assignee ... ask srikant
                creator_email=user.email,
                assignee=user.email,
                creator_name=issue.user.login,
                external_revision_id=str(self.datetime_to_epoch_ms(issue.updated_at)),
                preview_renderable=False
            )
            #TODO: work on permissioning then update parts here
            return IssueUpdate(
                record=ticket_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                old_permissions=permissions,
                new_permissions=permissions,
                external_record_id=str(issue.url)
            )
        except Exception as e:
            self.logger.error(f"Error in processing issues to tickets: {e}", exc_info=True)
            return None

    async def _sync_records_incremental(self,last_sync_timestamp:str,users:List[AppUser]) -> None:
        """_summary_
        when syncing so to avoid previosly synced files
        Args:
            last_sync_timestamp (str): _description_
            users (List[AppUser]): _description_
        """
        self.logger.info(f"Starting incremental record (page) sync from: {last_sync_timestamp}")
        # rerpo wise called or how will need to get only repos issues
        auth_res = self.data_source.get_authenticated()
        user_login = auth_res.data.login
        owner = user_login  # Use the same user as owner
        repo = os.getenv("GITHUB_REPO")
        # datetime_from_epoch =self.ms_to_datetime(int(last_sync_timestamp)) no moire needed as already in datetime format  datetime(2025, 12, 27, 0, 0, 0, tzinfo=timezone.utc)
        # getting all issues and sub issues after last sync point
        issues_res = self.data_source.list_issues(owner, repo,state='all',since=datetime.strptime(last_sync_timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))
        #  can reuse logic of _sync_issues_full
        if not issues_res.success:
            self.logger.info("Unable to fetch changes from last sync point.")
            # return
        else:
            batch_tickets:List[Tuple[TicketRecord,List[Permission]]]=[]
            # process issues first
            for issue in issues_res.data:
                parent_issue_ul:Dict = getattr(issue, "raw_data", None)
                parent_issue_url=parent_issue_ul.get("parent_issue_url",None)
                pull_request = getattr(issue,"pull_request",None)
                if pull_request is None:
                    if parent_issue_url is None :
                        issue_update:IssueUpdate = await self._process_issues_to_tickets(issue=issue,for_sub_issue=False)
                        if issue_update:
                            if issue_update.is_updated:
                                await self._handle_record_updates(issue_update)
                                continue
                            if issue_update.record:
                                batch_tickets.append((issue_update.record,issue_update.new_permissions or []))
                                if len(batch_tickets) >= self.batch_size:
                                    self.logger.info(f"Processing batch of {len(batch_tickets)} tickets.")
                                    await self.data_entities_processor.on_new_records(batch_tickets)
                                    batch_tickets = []

            if batch_tickets:
                self.logger.info(f"Processing last batch of {len(batch_tickets)} tickets.")
                await self.data_entities_processor.on_new_records(batch_tickets)
            batch_tickets=[]
            for issue in issues_res.data:
                parent_issue_ul:Dict = getattr(issue, "raw_data", None)
                parent_issue_url=parent_issue_ul.get("parent_issue_url",None)
                pull_request = getattr(issue,"pull_request",None)
                if pull_request is None:
                    if parent_issue_url is not None :
                        issue_update:IssueUpdate = await self._process_issues_to_tickets(issue=issue,for_sub_issue=True)
                        if issue_update:
                            if issue_update.is_updated:
                                await self._handle_record_updates(issue_update)
                                continue
                            if issue_update.record:
                                batch_tickets.append((issue_update.record,issue_update.new_permissions or []))
                                if len(batch_tickets) >= self.batch_size:
                                    self.logger.info(f"Processing batch of {len(batch_tickets)} tickets.")
                                    await self.data_entities_processor.on_new_records(batch_tickets)
                                    batch_tickets = []
            if batch_tickets:
                self.logger.info(f"Processing last batch of {len(batch_tickets)} tickets.")
                await self.data_entities_processor.on_new_records(batch_tickets)
            batch_tickets=[]
        self.logger.info("✅ Finished syncing all issue records.")

    async def _handle_page_upsert_event_issue(self,issue:Issue,users:List[AppUser])-> None:
        """_summary_

        Args:
            issue (Issue): _description_
            users (List[AppUser]): _description_
        """
        # existing_record = None
        # below code to code fetch from arango db by connector name and external record id but HOW ??
        # async with self.data_store_provider.transaction() as tx_store:
        #     existing_record = await tx_store.get_record_by_external_id(
        #         connector_id=self.connector_id,
        #         external_id=f"{issue.id}"
        #     )
        # self.logger.info(type(existing_record))
        # self.logger.info(existing_record)
        # user = users[0]
        # permissions=[Permission(
        #     entity_type=EntityType.USER,
        #     email=user.email,
        #     type=PermissionType.OWNER
        # )]
        # // add source details of time as in ui this is only seen
        # new_issue = TicketRecord(
        #     id=existing_record.id if existing_record else str(uuid.uuid4()),
        #     record_name=issue.title,
        #     record_type = RecordType.TICKET.value,
        #     external_record_id=str(issue.id),
        #     connector_id=self.connector_id,
        #     origin=OriginTypes.CONNECTOR.value,
        #     updated_at=str(self.datetime_to_epoch_ms(issue.updated_at)),
        #     created_at= str(self.datetime_to_epoch_ms(issue.created_at)),
        #     version = 0,
        #     external_record_group_id="694ac840980fc35be585c035",
        #     org_id="694ac7f8980fc35be585bfe8",
        #     parent_record_type="FILE",
        #     record_group_type="KB",
        #     mime_type=MimeTypes.MARKDOWN,
        #     weburl=issue.html_url,
        #     status=issue.state,
        #     creator_email=user.email,
        #     assignee=user.email,
        #     creator_name=issue.user.login,
        #     external_revision_id=str(self.datetime_to_epoch_ms(issue.updated_at)),
        #     preview_renderable=False # update this
        # )
        # if existing_record is None:
        #     # a new record incoming
        #     await self.data_entities_processor.on_new_records([(new_issue,permissions)])
        #     return
        # else:
        #     await self._handle_record_updates(new_issue)


    async def _handle_record_updates(self,issue_update:IssueUpdate) -> None:
        """_summary_

        Args:
            issue_update (IssueUpdate): _description_
        """
        try:
            if issue_update.is_deleted:
                # await self.data_entities_processor.on_record_deleted(
                    # record_id=issu_update.external_record_id
                # )
                self.logger.info("need to implement")
            elif issue_update.is_updated:
                if issue_update.metadata_changed:
                    self.logger.info(f"Metadata changed for record: {issue_update.record.record_name}")
                    await self.data_entities_processor.on_record_metadata_update(issue_update.record)
                # if issue_update.permissions_changed:
                #     self.logger.info(f"Permissions changed for record: {issue_update.record.record_name}")
                #     await self.data_entities_processor.on_updated_record_permissions(
                #         issue_update.record,
                #         issue_update.new_permissions
                #     )
                if issue_update.content_changed:
                    self.logger.info(f"Content changed for record: {issue_update.record.record_name}")
                    await self.data_entities_processor.on_record_content_update(issue_update.record)
        except Exception as e:
            self.logger.error(f"Error handling record updates: {e}", exc_info=True)

    async def reindex_records(self):
        return

    async def run_incremental_sync(self):
        return

    #---------------------------insitu functions-----------------------------------#
    def datetime_to_epoch_ms(self,dt) -> int:
        # make sure it's timezone-aware (assume UTC if missing)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    async def get_img_bytes(self,image_url:String):
        # _inline_images_as_base64 of pr https://github.com/pipeshub-ai/pipeshub-ai/pull/952/files connector.py zammad code
        # for proper handling of all img. formats and errors raisen
        config =await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
        if not config:
                self.logger.error("❌Github configuration not found.")
                raise ValueError("Github credentials not found")

        credentials_config = config.get("auth", {})
        token_id = credentials_config.get("token_id")
        # token_secret = credentials_config.get("token_secret")

        if not all([token_id]):
            self.logger.error("❌Github configuration not found.")
            raise ValueError("Github credentials not found")

        GITHUB_TOKEN=token_id
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }

        resp = requests.get(image_url, headers=headers,allow_redirects=True)
        # self.logger.info(type(resp))
        image_bytes = resp.content
        return image_bytes

    def _get_iso_time(self) -> str:
        # Get the current time in UTC
        utc_now = datetime.now(timezone.utc)
        # Format the time into the ISO 8601 string format with 'Z'
        iso_format_string = utc_now.strftime('%Y-%m-%dT%H:%M:%SZ')
        return iso_format_string

    def ms_to_datetime(ms: int) -> datetime:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """Get signed URL for record access (optional - if API supports it)."""

        return None

    async def handle_webhook_notification(self, org_id: str, notification: Dict) -> bool:
        """Handle webhook notifications (optional - for real-time sync)."""
        try:
            return True
        except Exception as e:
            self.logger.error(f"Error handling webhook: {e}")
            return False

    def get_filter_options(self):
        return

    async def cleanup(self) -> None:
        """
        Cleanup resources used by the connector.
        """
        self.logger.info("Cleaning up BookStack connector resources.")
        self.data_source = None

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id:str
    ) -> "BaseConnector":
        """
        Factory method to create a Github connector instance.

        Args:
            logger: Logger instance
            data_store_provider: Data store provider for database operations
            config_service: Configuration service for accessing credentials

        Returns:
            Initialized GithubConnector instance
        """
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()

        return GithubConnector (
            logger, data_entities_processor, data_store_provider, config_service,connector_id
        )


