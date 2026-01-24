import base64
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from logging import Logger, exception
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from fastapi.responses import StreamingResponse
from github.Issue import Issue
from github.PullRequest import PullRequest
from PIL import Image

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
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.sources.github.common.apps import GithubApp
from app.models.blocks import (
    Block,
    BlockComment,
    BlockGroup,
    BlocksContainer,
    BlockType,
    ChildRecord,
    ChildType,
    CommentSubtype,
    DataFormat,
    GroupSubType,
    GroupType,
    TableRowMetadata,
    CommentAttachment,
)
from app.models.entities import (
    AppUser,
    CommentRecord,
    FileRecord,
    PullRequestRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.github.github import (
    GitHubClient,
    GitHubResponse,
)
from app.sources.external.github.github_ import GitHubDataSource

# below to be removed
OAUTH_GITHUB_CONFIG_PATH = "/services/connectors/github/config"
AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"

type_to_mime={
    "pdf":MimeTypes.PDF.value,
    "docx":MimeTypes.DOCX.value,
    "xlsx":MimeTypes.XLSX.value,
    "pptx":MimeTypes.PPTX.value,
}

@dataclass
class RecordUpdate:
    """Tracks updates to a Ticket"""
    record:Optional[Record]
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
    .with_description("Sync content from your Github instance")\
    .with_categories(["Knowledge Management"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Github",
            authorize_url=AUTHORIZE_URL,
            token_url=TOKEN_URL,
            redirect_uri="connectors/oauth/callback/Github",
            scopes=OAuthScopeConfig(
                personal_sync=["user", "repo"],
                team_sync=[],
                agent=[]
            ),
            fields=[
                AuthField(
                    name="clientId",
                    display_name="Application (Client) ID",
                    placeholder="Enter your Github Application ID",
                    description="The Application (Client) ID from Github OAuth Registration"
                ),
                AuthField(
                    name="clientSecret",
                    display_name="Client Secret",
                    placeholder="Enter your Github Client Secret",
                    description="The Client Secret from Github OAuth Registration",
                    field_type="PASSWORD",
                    is_secret=True
                )
            ],
            icon_path="/assets/icons/connectors/github.svg",
            app_description="OAuth application for accessing Github services",
            app_categories=["Knowledge Management"]
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/github.svg")\
        .with_realtime_support(False)\
        .add_documentation_link(DocumentationLink(
            "Github API Docs",
            "https://docs.github.com/en",
            "docs"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/github/github',
            'pipeshub'
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
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

        # TODO: check create check points are those 3 really req.
        self.data_source: Optional[GitHubDataSource] = None
        self.external_client:Optional[GitHubClient]=None
        self.batch_size = 5
        self.max_concurrent_batches = 5
        self._create_sync_points()

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

    async def init(self) -> bool:
        """_summary_

        Returns:
            bool: _description_
        """
        try:
            # Initialize client and datasource OLD METHOD
            # Initialize all NEW METHOD
            # for client
            self.external_client= await GitHubClient.build_from_services(
                logger=self.logger,
                config_service =self.config_service,
                connector_instance_id=self.connector_id
            )
            # for data source
            self.data_source=GitHubDataSource(self.external_client)
            self.logger.info("Github connector intialized successfully.")
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
        if record.record_type == RecordType.TICKET:
            self.logger.info("🟣🟣🟣 STREAM_TICKET_MARKER 🟣🟣🟣")
            blocks_container:BlocksContainer = await self._build_ticket_blocks(record)
            async def generate_blocks_json() -> AsyncGenerator[bytes, None]:
                json_str = blocks_container.model_dump_json(indent=2)
                chunk_size = 81920
                encoded = json_str.encode('utf-8')
                for i in range(0, len(encoded), chunk_size):
                    yield encoded[i:i + chunk_size]
            return StreamingResponse(
                # content = stream_markdown(markdown_content),
                content= generate_blocks_json(),
                media_type=MimeTypes.BLOCKS.value,
                headers={
                    "Content-Disposition": f"attachment; filename={record.record_name}"
                }
            )
        elif record.record_type == RecordType.COMMENT:
            # fetch api call to get comment body
            self.logger.info("🟣🟣🟣 STREAM_COMMENT_MARKER 🟣🟣🟣")
            start_time = time.perf_counter()
            raw_url=record.weburl.split('/')
            self.logger.info(f"raw_url : {raw_url}")
            repo_name=raw_url[5]
            comment_id = int(raw_url[8])
            owner = raw_url[4]
            issue_url = record.parent_external_record_id.split('/')
            self.logger.info(f"issue_url : {issue_url}")
            issue_number = int(issue_url[7])
            await self._log_rate_limit("UP")
            comment = self.data_source.get_issue_comment(owner,repo_name,issue_number,comment_id)
            await self._log_rate_limit("DOWN")
            # username=raw_url[3]
            # issue_number=int(raw_url[6])
            # issue =  self.data_source.get_issue(owner=username,repo=repo_name,number=issue_number)
            markdown_content:str =comment.data.body
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
                # content = stream_markdown(markdown_content),
                content= [markdown_content],
                media_type=record.mime_type if record.mime_type else "application/octet-stream",
                headers={
                    "Content-Disposition": f"attachment; filename={record.record_name}"
                }
            )
        elif record.record_type == RecordType.PULL_REQUEST:
            self.logger.info("🟣🟣🟣 STREAM_GITHUB_PULL_REQUEST_MARKER 🟣🟣🟣")
            block_container = await self._build_pull_request_blocks(record)

            async def generate_blocks_json() -> AsyncGenerator[bytes, None]:
                json_str = block_container.model_dump_json(indent=2)
                # Yield in chunks of 8KB for efficient streaming
                chunk_size = 81920
                encoded = json_str.encode('utf-8')
                for i in range(0, len(encoded), chunk_size):
                    yield encoded[i:i + chunk_size]

            return StreamingResponse(
                content= generate_blocks_json(),
                media_type=MimeTypes.BLOCKS.value,
                headers={
                    "Content-Disposition": f"attachment; filename={record.record_name}"
                }
            )
        elif record.record_type == RecordType.FILE:
            self.logger.info("🟣🟣🟣 STREAM-FILE-MARKER 🟣🟣🟣")
            record_url = record.weburl
            GITHUB_TOKEN = await self._get_api_token_()
            self.logger.info(f"Fetching file from URL: {record_url}")
            self.logger.info(f"Using token: {GITHUB_TOKEN} ")
            headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
            }
            file_data = b""
            try:
                async with httpx.AsyncClient(follow_redirects=True) as client:
                    resp = await client.get(record_url, headers=headers)
                    file_data = resp.content
                    self.logger.info(f"Fetched file of size: {len(file_data)} bytes")
            except httpx.HTTPStatusError as e:
                self.logger.error(f"HTTP {e.response.status_code} fetching image from {record_url}")
            except Exception as e:
                self.logger.error(f"Error fetching file from {record_url}: {e}")
            self.logger.info(f"Fetched file of size: {len(file_data)} bytes")
            # self.logger.info(f"data of file \n \n {file_data}")

            def stream_markdown(markdown_content, chunk_size=160000):
                """Stream markdown content in optimal chunks"""
                for i in range(0, len(markdown_content), chunk_size):
                    yield markdown_content[i:i+chunk_size]

            return StreamingResponse(
                content= stream_markdown(file_data),
                media_type=record.mime_type if record.mime_type else "application/octet-stream",
                headers={
                    "Content-Disposition": f"attachment; filename={record.record_name}"
                }
            )

    async def run_sync(self):
            try:
                # getting all users
                github_users =  await self._fetch_users()
                if not github_users:
                    raise ValueError("Failed to retrieve account information for user sync.")

                await self.data_entities_processor.on_new_app_users(github_users)
                self.logger.info("👥 Synced Individual Github user")
                # get all repos acc.
                await self._sync_all_repo_issue()
            except Exception as ex:
                self.logger.error(f"Error in Github Individual connector run: {ex}", exc_info=True)
                raise
            
    #---------------------------Users Sync-----------------------------------#
    async def _fetch_users(self) -> AppUser:
        """
        Fetch all active Github users using DataSource
        """
        if not self.data_source:
            raise ValueError("Data source not initialized")
        # get list of collab. from github api
        auth_res = self.data_source.get_authenticated()
        user_login = auth_res.data.login
        # get indi. users data
        user_res = self.data_source.get_user(user_login)
        if not user_res.success or not user_res.data:
            return  []
        user = user_res.data
        app_users: List[AppUser] = []
        #TODO: to get email fetch from login get emails and find, change it when
        ph_users = await self.data_entities_processor.get_all_active_users()
        if not ph_users:
            self.logger.info("No users found")
            return []
        ph_user=ph_users[0]
        email = ph_user.email

        app_user = AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id = user.login,
                is_active= True,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=user.login
            )
        app_users.append(app_user)
        self.logger.info(f"👥 Fetched {len(app_users)} active users ")
        return app_users

    #---------------------------Repo level Sync-----------------------------------#
    async def _sync_all_repo_issue(self,full_sync:bool = False)->None:
        # get repo names in a list form from run_sync
        # for each repo call sync_repo_issues
        # TODO: sync point repo level ask plan acc.
        current_timestamp = self._get_iso_time()
        github_record_sync_key = generate_record_sync_point_key('github','records','global')
        github_record_sync_point = await self.record_sync_point.read_sync_point(github_record_sync_key)
        users = await self.data_entities_processor.get_all_active_users() # to be changed
        # if not users:
        #     self.logger.info("No users found")
        #     return
        if full_sync or not github_record_sync_point.get('timestamp'):
            await self._sync_issues_full(users)
            await self.record_sync_point.update_sync_point(
                github_record_sync_key,
                {"timestamp" : current_timestamp}
            )
        else:
            last_sync_timestamp = github_record_sync_point.get("timestamp")
            await self._sync_issues_full(users,last_sync_timestamp)
            await self.record_sync_point.update_sync_point(
                github_record_sync_key,
                {"timestamp" : current_timestamp}
            )

    #---------------------------Issues Sync-----------------------------------#

    async def _sync_issues_full(self,users:List[AppUser],last_sync_time:Optional[str]=None)->None:
        """_summary_

        Args:
            users (List[AppUser]): _description_
        """
        self.logger.info("Starting sync for issues as records.")
        auth_res = self.data_source.get_authenticated()
        user_login = auth_res.data.login
        owner = user_login

        if last_sync_time:
            datetime.strptime(last_sync_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        else:
            pass

        ph_users = await self.data_entities_processor.get_all_active_users()
        if not ph_users:
            self.logger.info("No users found")
            return
        ph_user=ph_users[0]
        user_email = ph_user.email
        
        # NOTE: get repos returns all repos user has contributions to or is owner of
        repos_res  = self.data_source.list_user_repos(owner,type='all')
        if not repos_res.success or not repos_res.data:
            self.logger.error(f"Failed to get repositories: {repos_res.error}")
            return
        repos = repos_res.data
        for repo in repos:
            # TODO: Place for code indexing records
            record_group = RecordGroup(
            id=str(uuid.uuid4()),
            org_id=self.data_entities_processor.org_id,
            name=repo.url,
            group_type=RecordGroupType.REPOSITORY.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            external_group_id=repo.url,
            )

            permissions = [Permission(email=user_email, type=PermissionType.OWNER, entity_type=EntityType.USER)]
            await self.data_entities_processor.on_new_record_groups([(record_group, permissions)])

            await self._fetch_issues_batched(repo_name=repo.name,last_sync_time=last_sync_time)

    async def _fetch_issues_batched(self,repo_name:str,last_sync_time:Optional[str]=None)->None:
        """
        recieved: batch of issues
        process: for each make TicketRecord or PullRequestRecord
        return: list of Records consisting of Tickets and PR
        Args:
            issue_batch (List[Issue]): _description_
            last_sync_time (str): _description_
        """
        # batch_records:List[RecordUpdate] = await self._build_issue_records(issue_batch,last_sync_time)
        # return batch_records
        auth_res = self.data_source.get_authenticated()
        user_login = auth_res.data.login
        owner = user_login

        if last_sync_time:
            since_dt = datetime.strptime(last_sync_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        else:
            since_dt = None
        self.logger.info(f"Fetching issues for repository: {repo_name}")
        issues_res = self.data_source.list_issues(owner, repo_name,state='all',since=since_dt)
        issues_batch:List[Issue]=[]
        if not issues_res.success or not issues_res.data:
            self.logger.error(f"Failed to get issues: {issues_res.error}")
            return []
        all_issues = issues_res.data
        total_issues = len(all_issues)
        self.logger.info(f"📦 Fetched {total_issues} issues, processing in batches...")
        # Process issues in batches
        batch_size = self.batch_size  # or define a constant like BATCH_PROCESSING_SIZE
        batch_number = 0
        for i in range(0, total_issues, batch_size):
            batch_number += 1
            issues_batch = all_issues[i:i + batch_size]
            batch_records: List[Tuple[Record, List[Permission]]] = []
            self.logger.info(f"📦 Processing batch {batch_number}: {len(issues_batch)} issues")
            # batch_records = await self._fetch_issues_batched(issues_batch,last_sync_time=last_sync_time)
            batch_records = await self._build_issue_records(issues_batch,last_sync_time)
            # send batch results to process
            await self._process_new_records(batch_records)

    async def _process_new_records(self, batch_records: List[RecordUpdate]) -> None:
        for i in range(0, len(batch_records), self.batch_size):
            batch = batch_records[i:i+self.batch_size]
            batch_sent: List[Tuple[Record, Permission]] = []
            for record_update in batch:
                batch_sent.append((record_update.record, record_update.new_permissions))
            await self.data_entities_processor.on_new_records(batch_sent)

    async def _build_issue_records(self,issue_batch:List[Issue],last_sync_time:Optional[str]=None)->List[RecordUpdate]:
        # NOTE:Github considers all Pull Requests as issues not True Vice-Versa
        record_updates_batch:List[RecordUpdate]=[]
        for issue in issue_batch:
            # check and send not to be pull request
            pull_request = getattr(issue,"pull_request",None)
            if pull_request is None:
                record_update = await self._process_issue_to_ticket(issue)
            else:
                # if pull_request
                record_update=await self._process_pr_to_pull_request(issue)
            if record_update:
                record_updates_batch.append(record_update)
                # issue_id =record_update.record.external_record_id
                # permissions=record_update.new_permissions
                # parent_node_id=record_update.record.id
                # external_record_group_id=record_update.record.external_record_group_id
                # get the file attachments from issue data / pr data
                # make file records for all except images
                markdown_content_raw:str = issue.body
                markdown_content, attachments = await self.clean_github_content(markdown_content_raw)
                self.logger.debug(f"Processed markdown content for issue {issue.id}")
                # self.logger.debug(f"Cleaned markdown content: {markdown_content}")
                if attachments:
                    file_record_updates = await self.make_file_records_from_list(attachments=attachments,record=record_update.record )
                    if file_record_updates:
                        record_updates_batch.extend(file_record_updates)
                        self.logger.info(f"Added {len(file_record_updates)} attachments for issue {issue.id}")
                # NOTE : Fetch comments removed for now as handeled in blockgroups
                # try:
                #     comment_record_updates = await self._fetch_issue_comments(
                #         issue_id=issue_id,
                #         permissions=permissions,
                #         parent_node_id=parent_node_id,
                #         last_sync_time=last_sync_time,
                #         external_record_group_id=external_record_group_id
                #     )
                #     if comment_record_updates:
                #         record_updates_batch.extend(comment_record_updates)
                #         self.logger.info(f"Added {len(comment_record_updates)} comments for issue {issue_id}")
                # except Exception as e:
                #     self.logger.error(f"❌ Failed to fetch comments for issue {issue_id}: {e}")
        return record_updates_batch

    async def _process_issue_to_ticket(self,issue:Issue)-> Optional[RecordUpdate]:
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
            issue_type = "issue"
            parent_issue_ul:Dict = getattr(issue, 'raw_data', None)
            parent_issue_url=parent_issue_ul.get('parent_issue_url',None)
            if parent_issue_url:
                parent_external_id = parent_issue_url
                parent_record_type = RecordType.TICKET
                issue_type="sub_issue"
            label_names:List[str]=[]
            for label in issue.labels:
                label_names.append(label.name)

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
                #TODO: is_email_hidden like above case
                record_group_type=RecordGroupType.REPOSITORY,
                parent_external_record_id=parent_external_id,
                parent_record_type=parent_record_type,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=issue.html_url,
                status=issue.state,
                external_revision_id=str(self.datetime_to_epoch_ms(issue.updated_at)),
                preview_renderable=False,
                type=issue_type,
                labels=label_names,
                inherit_permissions=True,
            )
            return RecordUpdate(
                record=ticket_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                old_permissions=[],
                new_permissions=[],
                external_record_id=str(issue.url)
            )
        except Exception as e:
            self.logger.error(f"Error in processing issues to tickets: {e}", exc_info=True)
            return None

    async def _build_ticket_blocks(self,record:Record) -> BlocksContainer:
        """_summary_

        Args:
            record (Record): _description_

        Returns:
            BlocksContainer: _description_
        """
        raw_url=record.weburl.split('/')
        self.logger.info(f"raw_url : {raw_url}")
        repo_name=raw_url[4]
        username=raw_url[3]
        issue_number=int(raw_url[6])
        issue_res = self.data_source.get_issue(owner=username,repo=repo_name,number=issue_number)
        if not issue_res.success or not issue_res.data:
            self.logger.error(f"Failed to fetch issue details for record {record.external_record_id}: {issue_res.error}")
            return BlocksContainer(blocks=[], block_groups=[])
        block_group_number = 0
        blocks:List[Block]=[]
        block_groups:List[BlockGroup]=[]
        issue=issue_res.data

        # getting modi. markdown  content with images as base64
        markdown_content_raw:str =issue.body
        markdown_content_with_images_base64 = await self.embed_images_as_base64(markdown_content_raw)
        self.logger.debug(f"Processed markdown content for issue {issue.url}")
        # get linked attachments to issue->ticket
        existing_attachs=None
        async with self.data_store_provider.transaction() as tx_store:
                existing_attachs = await tx_store.get_records_by_parent(
                    connector_id=self.connector_id,
                    parent_external_record_id=f"{issue.url}",
                    record_type=RecordType.FILE
                )
        self.logger.info(f"Found {len(existing_attachs)} attachments linked to issue {issue.url}")
        # self.logger.debug(f"Attachments: {existing_attachs}")
        table_row_metadata:TableRowMetadata = None
        list_child_records:List[ChildRecord] = []
        for attach_record in existing_attachs:
            child_record = ChildRecord(
                child_type=ChildType.RECORD,
                child_id = attach_record.id,
                child_name = attach_record.record_name,
                )
            list_child_records.append(child_record)
        if list_child_records:
            table_row_metadata = TableRowMetadata(
                children_records=list_child_records
            )
        # self.logger.debug(f"Table Row Metadata: {table_row_metadata}")

        # bg of title and desc./body
        bg_0 = BlockGroup(
            index=block_group_number,
            name=record.record_name,
            type=GroupType.TEXT_SECTION.value,
            format=DataFormat.MARKDOWN.value,
            group_subtype=GroupSubType.ISSUE_CONTENT.value,
            source_group_id=record.weburl,
            data=markdown_content_with_images_base64,
            source_modified_date=str(self.datetime_to_epoch_ms(issue.updated_at)),
            requires_processing=True,
            table_row_metadata=table_row_metadata,
        )
        block_groups.append(bg_0)
        # make blocks of issue comments
        comments_bg = await self._build_comment_blocks(issue_url=record.weburl,parent_index =block_group_number,record=record)
        block_groups.extend(comments_bg)
        block_group_number += len(comments_bg)
        blocks_container = BlocksContainer(
            blocks=blocks,
            block_groups=block_groups
        )
        return blocks_container

    async def _sync_records_incremental(self,last_sync_timestamp:str,users:List[AppUser]) -> None:
        """_summary_
        {DEPRECATED} use _sync_issues_full with last sync time
        when syncing so to avoid previosly synced files
        Args:
            last_sync_timestamp (str): _description_
            users (List[AppUser]): _description_
        """
        return

    async def _handle_page_upsert_event_issue(self,issue:Issue,users:List[AppUser])-> None:
        """_summary_

        Args:
            issue (Issue): _description_
            users (List[AppUser]): _description_
        """

    async def _handle_record_updates(self,issue_update:RecordUpdate) -> None:
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
    #---------------------------Comments sync-----------------------------------#

    async def _fetch_issue_comments(
        self,
        issue_id:str,
        permissions:List[str],
        parent_node_id:str,
        external_record_group_id:str,
        last_sync_time:Optional[str] = None )->List [RecordUpdate]:
        """ now depreciated !!!

        Args:
            users (List[AppUser]): _description_
        """
        # to get comments of an issue make request to each issue
        self.logger.info("🟣🟣🟣 Comments Records 🟣🟣🟣")
        users = await self.data_entities_processor.get_all_active_users()
        if not users:
            self.logger.info("No users found")
            return None
        user=users[0]
        auth_res = self.data_source.get_authenticated()
        user_login = auth_res.data.login
        owner = user_login  # Use the same user as owner
        parent_issue_url = issue_id.split("/")
        issue_number = parent_issue_url[7]
        repo_name = parent_issue_url[5]
        self.logger.info(f"{parent_issue_url}")
        if last_sync_time:
            since_dt = datetime.strptime(last_sync_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        else:
            since_dt = None
        # if since_dt is None:
            # comments_res = self.data_source.list_issue_comments(owner = owner,repo=repo_name,number=int(issue_number))
        # else:
        comments_res = self.data_source.list_issue_comments(owner = owner,repo=repo_name,number=int(issue_number),since=since_dt)
        comment_record_updates:List[RecordUpdate]=[]
        # if not comments_res or not comments_res.success:
        #     return []
        if not comments_res:
            self.logger.error(f"❌ API call returned None for issue {issue_id}")
            return []

        if not comments_res.success:
            error_msg = comments_res.error if hasattr(comments_res, 'error') else "Unknown error"
            self.logger.error(f"❌ Failed to fetch comments for issue {issue_id}: {error_msg}")
            return []

        for comment in comments_res.data:
            # Check for existing comment record
            # detect changes

            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=f"{comment._url}"
                )
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False
            record_name = f"comment by {user.email} on issue {issue_number}"
            if existing_record:
                # TODO: add more changes especially body ones as of now default fallback to full body reindexing
                if existing_record.record_name != comment.body:
                    metadata_changed = True
                    is_updated = True
                # TODO: body changes check as of now True default
                content_changed = True
                is_updated = True
            self.logger.info(f"🐍🐍{comment.body}")
            comment_record = CommentRecord(
                id =existing_record.id if existing_record else str(uuid.uuid4()),
                org_id=self.data_entities_processor.org_id,
                record_name=record_name,
                record_type=RecordType.COMMENT,
                external_record_id=str(comment.url),
                external_revision_id=str(self.datetime_to_epoch_ms(comment.updated_at)),
                parent_external_record_id=issue_id,
                parent_record_type=RecordType.TICKET,
                external_record_group_id=external_record_group_id,
                connector_name=Connectors.GITHUB,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR,
                mime_type=MimeTypes.MARKDOWN.value,
                record_group_type=RecordGroupType.REPOSITORY,  # Inherit from parent issue
                source_updated_at=str(self.datetime_to_epoch_ms(comment.updated_at)),
                source_created_at= str(self.datetime_to_epoch_ms(comment.created_at)),
                # author_source_id=author_account_id or "unknown",
                preview_renderable=False,
                is_dependent_node=True,  # Comments are dependent nodes
                parent_node_id=parent_node_id, # Internal record ID of parent ticket
                version=0,
                author_source_id=user.email,
                weburl=str(comment.html_url),
                )

            record_update =  RecordUpdate(
                record=comment_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                old_permissions=permissions,
                new_permissions=permissions,
                external_record_id=str(comment.url)
            )
            comment_record_updates.append(record_update)

        return comment_record_updates

    async def _process_comments_to_commentrecord(self)->CommentRecord:
        return

    async def _build_comment_blocks(self,issue_url:str,parent_index:int,record:Record)->List[BlockGroup]:
        """_summary_
        Args:
            issue_url (str): _description_
        Returns:
            List[BlockGroup]: _description_
        """
        self.logger.info(f"Building comment blocks for issue: {issue_url}")
        raw_url=issue_url.split('/')
        self.logger.info(f"raw_url : {raw_url}")
        repo_name=raw_url[4]
        username=raw_url[3]
        issue_number=int(raw_url[6])
        # fetching issue comments if present
        #TODO: will date wise filtering be needed here, as of now None
        since_dt = None
        comments_res = self.data_source.list_issue_comments(owner = username,repo=repo_name,number=int(issue_number),since=since_dt)
        if not comments_res.success or not comments_res.data:
            self.logger.error(f"Failed to fetch comments for issue {issue_url}: {comments_res.error}")
            return []
        block_groups:List[BlockGroup]=[]
        block_group_number = parent_index + 1
        comments = comments_res.data
        for comment in comments:
            raw_markdown_content:str = comment.body
            markdown_content_with_images_base64= await self.embed_images_as_base64(raw_markdown_content)
            # handle attachments if any in comment body
            # push attachments comment wise to on_new_records
            table_row_metadata:TableRowMetadata = None
            childrecords = await self.process_other_attachments_blocks(raw_markdown_content=raw_markdown_content,record=record)
            table_row_metadata = TableRowMetadata(
                children_records=childrecords
            )
            bg = BlockGroup(
                index=block_group_number,
                parent_index=parent_index,
                name=f"Comment by {comment.user.login} on issue {issue_number}",
                type=GroupType.TEXT_SECTION.value,
                format=DataFormat.MARKDOWN.value,
                group_subtype=GroupSubType.COMMENT.value,
                source_group_id=comment.url,
                data=markdown_content_with_images_base64,
                source_modified_date=str(self.datetime_to_epoch_ms(comment.updated_at)),
                requires_processing=True,
                weburl=comment.html_url,
                table_row_metadata=table_row_metadata,
            )
            block_group_number += 1
            block_groups.append(bg)
        return block_groups
    
    #---------------------------Pull Requests-----------------------------------#
    async def _process_pr_to_pull_request(self,issue:Issue)->Optional[RecordUpdate]:

        # make call to fetch a pull request details
        # getting issue number and details
        issue_url=issue.url.split('/')
        issue_number=int(issue_url[7])
        owner=issue_url[4]
        repo_name=issue_url[5]
        pull_request_raw = self.data_source.get_pull(owner,repo_name,issue_number)
        if not pull_request_raw.success:
            self.logger.error(f"Failed to fetch pull request details for issue {issue.url}: {pull_request_raw.error}")
            return None
        pull_request = pull_request_raw.data
        if not pull_request:
            self.logger.error(f"No pull request data found for issue {issue.url}")
            return None
        try:
            # check if record already exists
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=f"{pull_request.url}"
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
                if existing_record.record_name != pull_request.title:
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
            label_names:List[str]=[]
            for label in pull_request.labels:
                label_names.append(label.name)

            # making pull request record
            pr_record = PullRequestRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=pull_request.title,
                external_record_id=str(pull_request.url),
                record_type = RecordType.PULL_REQUEST.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=str(self.datetime_to_epoch_ms(pull_request.updated_at)),
                source_created_at= str(self.datetime_to_epoch_ms(pull_request.created_at)),
                version = 0,# not used further so 0
                external_record_group_id=issue.repository_url,
                org_id=self.data_entities_processor.org_id,
                record_group_type=RecordGroupType.REPOSITORY,
                parent_external_record_id=parent_external_id,
                parent_record_type=parent_record_type,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=pull_request.html_url,
                status=pull_request.state,
                external_revision_id=str(self.datetime_to_epoch_ms(pull_request.updated_at)),
                preview_renderable=False,
                labels=label_names,
                mergeable=str(pull_request.mergeable),
                inherit_permissions=True,
            )
            return RecordUpdate(
                record=pr_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                old_permissions=[],
                new_permissions=[],
                external_record_id=str(pull_request.url)
            )
        except Exception as e:
            self.logger.error(f"Error in processing issues to tickets: {e}", exc_info=True)
            return None

    async def _build_pull_request_blocks(self,record:Record)->BlocksContainer:
        # TODO: think for BG as code file updates how as in newer commit some files same as old
        # TODO: think of keys when PR gets updated like only when metadata is getting updated or say body and also consider it for file changes
        pr_url = record.weburl.split('/')
        self.logger.info(f"Building blocks for pull request: {record.weburl}")
        pr_number =  int(pr_url[6])
        owner = pr_url[3]
        repo_name = pr_url[4]
        pull_request_res = self.data_source.get_pull(owner,repo_name,pr_number)
        if not pull_request_res.success or not pull_request_res.data:
            self.logger.error(f"Failed to fetch pull request details for record {record.external_record_id}: {pull_request_res.error}")
            return BlocksContainer(blocks=[], block_groups=[])
        pull_request:PullRequest = pull_request_res.data
        block_group_number = 0
        block_number=0
        blocks:List[Block]=[]
        block_groups:List[BlockGroup]=[]
        markdown_content_raw:str = pull_request.body
        # getting modi. markdown  content with images as base64
        markdown_with_base64= await self.embed_images_as_base64(markdown_content_raw)
        self.logger.debug(f"Processed markdown content for issue {pull_request.url}")
        # get linked attachments to issue
        existing_attachs=None
        async with self.data_store_provider.transaction() as tx_store:
                existing_attachs = await tx_store.get_records_by_parent(
                    connector_id=self.connector_id,
                    parent_external_record_id=f"{pull_request.url}",
                    record_type=RecordType.FILE
                )
        self.logger.info(f"Found {len(existing_attachs)} attachments linked to issue {pull_request.url}")
        table_row_metadata:TableRowMetadata = None
        list_child_records:List[ChildRecord] = []
        for attach_record in existing_attachs:
            child_record = ChildRecord(
                child_type=ChildType.RECORD,
                child_id = attach_record.id,
                child_name = attach_record.record_name,
                )
            list_child_records.append(child_record)
        if list_child_records:
            table_row_metadata = TableRowMetadata(
                children_records=list_child_records
            )

        # bg of title and desc./body
        bg_0 = BlockGroup(
            index=block_group_number,
            name=pull_request.title,
            type=GroupType.TEXT_SECTION,
            data = markdown_with_base64,
            format=DataFormat.MARKDOWN,
            weburl=pull_request.html_url,
            group_subtype=GroupSubType.PR_CONTENT,
            requires_processing=True,
            table_row_metadata=table_row_metadata,
            source_modified_date=str(self.datetime_to_epoch_ms(pull_request.updated_at)),
        )
        self.logger.info(f"bg for title and desc created for pr{pr_number}")
        block_groups.append(bg_0)

        comment_block_groups = await self._build_comment_blocks(issue_url=record.weburl,parent_index =block_group_number,record=record)
        block_groups.extend(comment_block_groups)
        block_group_number += len(comment_block_groups)
        block_group_number +=1
        # blocks for commits of pr
        commits_res = self.data_source.get_pull_commits(owner,repo_name,pr_number)
        if commits_res.success and commits_res.data:
            commits = commits_res.data
            for commit in commits:
                block = Block(
                    index=block_number,
                    parent_index=block_group_number,
                    type=BlockType.COMMIT,
                    weburl=commit.html_url,
                    format=DataFormat.MARKDOWN,
                    data=commit.commit.message,
                    source_creation_date=str(self.datetime_to_epoch_ms(commit.commit.committer.date)),
                    source_id=commit.sha,
                )
                block_number +=1
                blocks.append(block)
        bg_1 = BlockGroup(
            index=block_group_number,
            name="block group for pull request commits",
            type=GroupType.COMMITS,
            description=f"List of commits for pull request #{pr_number}",
            weburl=record.weburl,
            # TODO: ask is group subtype needed here
        )
        block_groups.append(bg_1)
        block_group_number +=1
        self.logger.info(f"bg and blocks of length {block_number},  for commits created for pr{pr_number}")
        # block group for files changed in pr
        files_res = self.data_source.get_pull_file_changes(owner,repo_name,pr_number)
        if files_res.success and files_res.data:
            self.logger.info(f"Fetched raw data : {files_res.data}")
            files = files_res.data
            # in file data patch is present which is diff make another call for files content
            # do other way dict of filepath to list of comments
            review_comments_res = self.data_source.get_pull_review_comments(owner,repo_name,pr_number)
            review_comments_map:Dict[str,List[BlockComment]]={}
            if review_comments_res.success and review_comments_res.data:
                review_comments = review_comments_res.data
                for r_comment in review_comments:
                    raw_markdown_content:str = r_comment.body
                    markdown_content_with_images_base64= await self.embed_images_as_base64(raw_markdown_content)
                    attachments = await self.process_other_attachments_block_comment(raw_markdown_content,record)
                    block_comment = BlockComment(
                        text= markdown_content_with_images_base64,
                        format=DataFormat.MARKDOWN,
                        subtype=CommentSubtype.CODE_REVIEW,
                        weburl=r_comment.html_url,
                        updated_at=str(self.datetime_to_epoch_ms(r_comment.updated_at)),
                        created_at=str(self.datetime_to_epoch_ms(r_comment.created_at)),
                        attachments=attachments,
                    )
                    self.logger.info(f"Mapping review r_comment on file: {r_comment.path}")
                    # self.logger.info(f"r_comment body: {r_comment.body}")
                    if r_comment.path in review_comments_map:
                        review_comments_map[r_comment.path].append(block_comment)
                    else:
                        review_comments_map[r_comment.path]=[block_comment]
            
            for file in files:
                self.logger.info(f"Fetching content for file: {file.filename}")
                # file content details
                file_blob_url = getattr(file,"blob_url",None).split('/')
                file_ref = file_blob_url[6]
                file_content_res = self.data_source.get_file_contents(owner,repo_name,file.filename,file_ref)

                if file_content_res.success and file_content_res.data:
                    self.logger.info(f"Fetched content for file: {file.filename}")
                    # file content is base64 encoded
                    file_content = None
                    try:
                        # Decode base64 content from GitHub API
                        file_content = base64.b64decode(file_content_res.data.content).decode('utf-8')
                    except Exception as e:
                        self.logger.error(f"Failed to decode file content for {file.filename}: {e}")
                        # file_content = file_content_res.data.content
                        continue
                    bg_n= BlockGroup(
                        index=block_group_number,
                        name=f"block for file {file.filename}",
                        type=GroupType.FULL_CODE_PATCH,
                        format = DataFormat.MARKDOWN,
                        group_substype=GroupSubType.PR_FILE_CHANGE,
                        data=str(file.patch) + str("\n\nFull File Content:\n") + str(file_content),
                        comments=review_comments_map.get(file.filename,[]),
                        requires_processing=True,
                        # weburl=
                       )
                    block_groups.append(bg_n)
                    block_group_number +=1

        blocks_container = BlocksContainer(
            blocks=blocks,
            block_groups=block_groups
        )
        # pretty_json = blocks_container.model_dump_json(indent=2)
        # self.logger.info(f"Blocks Container for PR {pr_number}:\n{pretty_json}")
        self.logger.info(f"Blocks container created for pr{pr_number}")
        return blocks_container

    #---------------------------Attachment functions-----------------------------------#
    async def embed_images_as_base64(self,body_content:str)->str:
        """
            getting raw markdown content, then getting images as base64 and appending in markdown content
        """
        self.logger.debug("Embedding images as base64 in markdown content in embed_images_as_base64 function")
        markdown_content_clean, attachments = await self.clean_github_content(body_content)
        if not attachments:
            return markdown_content_clean

        for attach in attachments:
            if attach.get("type") != "image":
                continue
            attachment_url = attach.get("href")
            self.logger.debug(f"Fetching image from URL: {attachment_url}")
            try:
                image_bytes = await self.get_img_bytes(attachment_url)
                if image_bytes:
                    # to get image format as  in attachment data just an image
                    img =  Image.open(BytesIO(image_bytes))
                    fmt = img.format.lower() if img.format else "png"
                    base64_data =  base64.b64encode(image_bytes).decode("utf-8")
                    md_image_data = f"![Image](data:image/{fmt};base64,{base64_data})"
                    markdown_content_clean += f"{md_image_data}"
            except Exception as e:
                self.logger.error(f"Error embedding image from {attachment_url}: {e}")
                continue
        return markdown_content_clean

    async def process_other_attachments_blocks(self,raw_markdown_content:str,record:Record)->List[ChildRecord]:
        cleaned_content, attachments = await self.clean_github_content(raw_markdown_content)
        child_records:List[ChildRecord]=[]
        record_updates:List[RecordUpdate]=[]
        record_updates = await self.make_file_records_from_list(attachments,record)
        await self._process_new_records(record_updates)
        for record_update in record_updates:
            child_record = ChildRecord(
                child_id=record_update.record.id,
                child_type=ChildType.RECORD,
                child_name = record_update.record.record_name,
            )
            child_records.append(child_record)
        return child_records

    async def process_other_attachments_block_comment(self,raw_markdown_content:str,record:Record)->List[CommentAttachment]:
        cleaned_content, attachments = await self.clean_github_content(raw_markdown_content)
        comment_attachments:List[CommentAttachment]=[]
        record_updates:List[RecordUpdate]=[]
        record_updates = await self.make_file_records_from_list(attachments,record)
        await self._process_new_records(record_updates)
        for record_update in record_updates:
            comment_attachment = CommentAttachment(
                name=record_update.record.record_name,
                id=record_update.record.id,
            )
            comment_attachments.append(comment_attachment)
        return comment_attachments
    
    async def make_file_records_from_list(self,attachments:List[Dict[str,Any]],record:Record)->List[RecordUpdate]:
        list_records_new:List[RecordUpdate]=[]
        for attach in attachments:
            if attach.get("type") == "image":
                continue
            # creating file record for each attachment
            attachment_url = attach.get("href")
            attachment_name = attach.get("filename")
            attachment_type = attach.get("type")
            self.logger.info(f"Processing attachment: {attachment_name} of type {attachment_type} from URL: {attachment_url}")
            if not attachment_url or not attachment_name:
                self.logger.warning(f"Skipping attachment due to missing URL or name: {attach}")
                continue
            
            if attachment_type.lower() not in type_to_mime:
                self.logger.warning(f"Skipping attachment due to unsupported type: {attachment_type}")
                continue
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=f"{attachment_url}"
                )
            # detect changes
            record_id = str(uuid.uuid4())

            filerecord = FileRecord(
                id = existing_record.id if existing_record else record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=attachment_name,
                record_type=RecordType.FILE,
                external_record_id=str(attachment_url),
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR,
                weburl = str(attachment_url),
                record_group_type=RecordGroupType.REPOSITORY,
                parent_external_record_id=record.external_record_id,
                parent_record_type=record.record_type,
                external_record_group_id=record.external_record_group_id,
                mime_type=type_to_mime.get(attachment_type,MimeTypes.UNKNOWN.value),
                extension=attachment_type,
                is_file=True,
                inherit_permissions=True,
                preview_renderable=True,
                version=0,
                size_in_bytes=0, # unknown
            )

            record_update = RecordUpdate(
                record=filerecord,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
                old_permissions=[],
                new_permissions=[],
                external_record_id=attachment_url
            )
            list_records_new.append(record_update)

        return list_records_new
    
    #---------------------------insitu functions-----------------------------------#
    def datetime_to_epoch_ms(self,dt) -> int:
        # make sure it's timezone-aware (assume UTC if missing)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    async def _get_api_token_(self)->str:
        """getting bearer token for file data streaming

        Raises:
            ValueError: _description_
            ValueError: _description_

        Returns:
            str: _description_
        """
        config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
        if not config:
            self.logger.error("❌Github configuration not found.")
            raise Exception("Github configuration not found")

        credentials_config = config.get("credentials", {})
        access_token = credentials_config.get("access_token","")

        if not access_token:
            self.logger.error("❌Github configuration not found.")
            raise ValueError("Github credentials not found")

        GITHUB_TOKEN=access_token
        return GITHUB_TOKEN

    async def get_img_bytes(self,image_url:str):

        # GITHUB_TOKEN= await self._get_api_token_()
        # headers = {
        #     "Authorization": f"{GITHUB_TOKEN}",
        #     "Accept": "application/vnd.github+json"
        # }
        # try:
        #     resp = requests.get(image_url, headers=headers,allow_redirects=True)
        #     # self.logger.info(type(resp))
        #     image_bytes = resp.content
        #     return image_bytes
        # except Exception as e:
        #     self.logger.error(f"Error fetching image from {image_url}: {e}")
        #     return None
        GITHUB_TOKEN = await self._get_api_token_()
        self.logger.info(f"Fetching image from URL: {image_url}")
        self.logger.info(f"Using token: {GITHUB_TOKEN} ")
        headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(image_url, headers=headers)
                resp.raise_for_status()
                img_data = resp.content
                self.logger.info(f"Fetched image of size: {len(img_data)} bytes")
                return img_data
        except httpx.HTTPStatusError as e:
            self.logger.error(f"HTTP {e.response.status_code} fetching image from {image_url}")
            return None
        except Exception as e:
            self.logger.error(f"Error fetching image from {image_url}: {e}")
            return None

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

    async def _log_rate_limit(self, label: str = "") -> None:
        """Log GitHub rate limit: remaining, limit, and reset time."""
        try:
            res = self.data_source.get_rate_limit()
            if not res or not res.success or not res.data:
                self.logger.info(f"Rate Limit {label}: unavailable")
                return

            # res.data is RateLimitOverview; .rate has .remaining/.limit/.reset
            rate = getattr(res.data, "rate", res.data)
            remaining = getattr(rate, "remaining", None)
            limit = getattr(rate, "limit", None)
            reset = getattr(rate, "reset", None)

            reset_str = "unknown"
            extra = ""
            if isinstance(reset, datetime):
                reset_utc = reset if reset.tzinfo else reset.replace(tzinfo=timezone.utc)
                secs = max(int((reset_utc - datetime.now(timezone.utc)).total_seconds()), 0)
                reset_str = reset_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                extra = f" (in {secs}s)"
            elif reset is not None:
                reset_str = str(reset)

            self.logger.info(f"Rate Limit {label}: {remaining}/{limit} remaining, resets at {reset_str}{extra}")
        except Exception as e:
            self.logger.warning(f"Rate Limit {label}: failed to read ({e})")

    async def clean_github_content(self,text:str)->Tuple[str, List[Dict[str, Any]]]:
        """
        Removes all attachments (images, files) from GitHub markdown/HTML content
        and extracts their metadata.

        Returns:
            tuple: (cleaned_text, attachments_list)
        """
        attachments = []

        def get_file_type(url, filename=None):
            """Determine file type from URL or filename"""
            # Try to get extension from filename first (more reliable)
            if filename:
                ext = os.path.splitext(filename)[1].lower()
                if ext:
                    return ext.replace('.', '')

            # Parse URL path
            path = urlparse(url).path
            ext = os.path.splitext(path)[1].lower()

            if ext:
                return ext.replace('.', '')

            # GitHub-specific patterns
            if 'user-attachments/assets' in url:
                return 'image'  # GitHub assets are typically images
            elif 'user-attachments/files' in url:
                return 'file'

            return 'unknown'

        # --- 1. HTML IMG TAGS ---
        # More robust pattern that handles various attribute orders
        html_img_pattern = r'<img\s+[^>]*?src=["\'](.*?)["\'][^>]*?/?>'

        def html_img_handler(match):
            url = match.group(1)

            # Try to extract alt text if present
            alt_match = re.search(r'alt=["\'](.*?)["\']', match.group(0))
            alt_text = alt_match.group(1) if alt_match else None

            attachments.append({
                "type": get_file_type(url),
                "source": "html_img",
                "href": url,
                "alt": alt_text
            })
            return ""

        text = re.sub(html_img_pattern, html_img_handler, text, flags=re.IGNORECASE | re.DOTALL)

        # --- 2. MARKDOWN IMAGES: ![alt](url) ---
        md_image_pattern = r'!\[(.*?)\]\((.*?)\)'

        def md_image_handler(match):
            alt_text = match.group(1)
            url = match.group(2)

            attachments.append({
                "type": get_file_type(url, alt_text),
                "source": "markdown_image",
                "href": url,
                "alt": alt_text if alt_text else None
            })
            return ""

        text = re.sub(md_image_pattern, md_image_handler, text)

        # --- 3. MARKDOWN FILE LINKS: [filename.ext](url) ---
        # This pattern specifically looks for file attachments
        # (links with extensions or GitHub file paths)
        md_link_pattern = r'\[(.*?)\]\((.*?)\)'

        def md_link_handler(match):
            link_text = match.group(1)
            url = match.group(2)

            # Check if this is a file attachment
            is_github_file = 'user-attachments/files' in url or 'github.com' in url and '/files/' in url

            # Common file extensions
            file_extensions = {'.pdf', '.docx', '.xlsx', '.zip', '.pptx', '.txt',
                            '.csv', '.log', '.tiff', '.tif', '.json', '.xml'}
            has_file_ext = any(link_text.lower().endswith(ext) or url.lower().endswith(ext)
                            for ext in file_extensions)

            # If it's a file attachment, extract it
            if is_github_file or has_file_ext:
                attachments.append({
                    "type": get_file_type(url, link_text),
                    "source": "file_attachment",
                    "href": url,
                    "filename": link_text
                })
                return ""

            # Otherwise, keep the link (it's a regular hyperlink)
            return match.group(0)

        text = re.sub(md_link_pattern, md_link_handler, text)

        # --- 4. CLEANUP ---
        # Remove excessive blank lines created by deletions
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        return text, attachments

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

