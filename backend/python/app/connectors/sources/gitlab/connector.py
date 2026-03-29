import base64
import json
import os
import re
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from logging import Logger
from typing import Any
from urllib.parse import unquote

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from gitlab.v4.objects import (
    GroupMember,
    Issue,
    Project,
    ProjectCommit,
    ProjectIssue,
    ProjectIssueNote,
    ProjectMergeRequest,
    ProjectMergeRequestNote,
)
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
    SyncStrategy,
)
from app.connectors.sources.gitlab.common.apps import GitLabApp
from app.models.blocks import (
    Block,
    BlockComment,
    BlockGroup,
    BlocksContainer,
    BlockSubType,
    BlockType,
    ChildRecord,
    ChildType,
    CommentAttachment,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    CodeFileRecord,
    FileRecord,
    ItemType,
    PullRequestRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.gitlab.gitlab import (
    GitLabClient,
    GitLabResponse,
)
from app.sources.external.gitlab.gitlab_ import GitLabDataSource
from app.utils.streaming import create_stream_record_response

AUTHORIZE_URL = "https://gitlab.com/oauth/authorize"
TOKEN_URL = "https://gitlab.com/oauth/token"

PSEUDO_USER_GROUP_PREFIX = "[Pseudo-User]"
TEST_GITLAB_PROJECT_ID = os.getenv("TEST_GITLAB_PROJECT_ID")

@dataclass
class RecordUpdate:
    """Tracks updates to a Record"""
    record: Record | None
    is_new: bool
    is_updated: bool
    is_deleted: bool
    metadata_changed: bool
    content_changed: bool
    permissions_changed: bool
    old_permissions: list[Permission] | None = None
    new_permissions: list[Permission] | None = None
    external_record_id: str | None = None

@ConnectorBuilder("GitLab").in_group("GitLab").with_description(
    "Sync content from your GitLab instance"
).with_categories(["Knowledge Management"]).with_scopes(
    [ConnectorScope.TEAM.value]
).with_auth(
    [
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="GitLab",
            authorize_url=AUTHORIZE_URL,
            token_url=TOKEN_URL,
            redirect_uri="connectors/oauth/callback/Gitlab",
            scopes=OAuthScopeConfig(
                team_sync=["api", "read_user", "read_repository","read_registry" ,"sudo" ,"admin_mode","profile","email","read_api","read_service_ping","openid","read_virtual_registry","read_observability" ],
                personal_sync=[],
                agent=[]
            ),
            fields=[
                AuthField(
                    name="clientId",
                    display_name="Application (Client) ID",
                    placeholder="Enter your Gitlab Application ID",
                    description="The Application (Client) ID from Gitlab OAuth Registration",
                ),
                AuthField(
                    name="clientSecret",
                    display_name="Client Secret",
                    placeholder="Enter your Gitlab Client Secret",
                    description="The Client Secret from Gitlab OAuth Registration",
                    field_type="PASSWORD",
                    is_secret=True,
                ),
            ],
            icon_path="/assets/icons/connectors/gitlab.svg",
            app_description="OAuth application for accessing Gitlab services",
            app_categories=["Knowledge Management"],
        )
    ]
).configure(
    lambda builder: builder.with_icon("/assets/icons/connectors/gitlab.svg")
    .with_realtime_support(False)
    .add_documentation_link(
        DocumentationLink("Gitlab API Docs", "https://docs.gitlab.com/api/rest/", "docs")
    )
    .add_documentation_link(
        DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/connectors/gitlab/gitlab",
            "pipeshub",
        )
    )
    .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
    .with_sync_support(True)
    .with_agent_support(False)
).build_decorator()
class GitLabConnector(BaseConnector):
    """
    Connector for syncing data from Gitlab instance.
    """
    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
    ) -> None:
        super().__init__(
            GitLabApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
        )
        self.connector_name = Connectors.GITLAB.value
        self.connector_id = connector_id
        self.data_source: GitLabDataSource | None = None
        self.external_client: GitLabClient | None = None
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
                data_store_provider=self.data_store_provider,
            )

        self.record_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

    async def init(self) -> bool:
        """_summary_

        Returns:
            bool: _description_
        """
        try:
            # for client
            self.external_client = await GitLabClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            # for data source
            self.data_source = GitLabDataSource(self.external_client)
            self.logger.info("Gitlab connector initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Gitlab client: {e}", exc_info=True)
            return False

    async def test_connection_and_access(self) -> bool:
        """_summary_

        Returns:
            bool: _description_
        """
        if not self.data_source:
            return False
        try:
            response:GitLabResponse = self.data_source.get_user()
            if response.success and response.data:
                self.logger.info("GitLab connection test successful.")
                return True
            else:
                self.logger.error(f"GitLab connection test failed: {response.error}")
                return False
        except Exception as e:
            self.logger.error(f"GitLab connection test failed: {e}", exc_info=True)
            return False

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Docstring for stream_record

        :param self: Description
        :param record: Description
        :type record: Record
        :return: Description
        :rtype: StreamingResponse
        """
        if record.record_type == RecordType.TICKET:
            self.logger.info("🟣🟣🟣 STREAM_TICKET_MARKER 🟣🟣🟣")
            blocks_container = await self._build_ticket_blocks(record)

            # async def generate_blocks_json() -> AsyncGenerator[bytes, None]:
            #     json_str = blocks_container.model_dump_json(indent=2)
            #     chunk_size = 81920
            #     encoded = json_str.encode("utf-8")
            #     for i in range(0, len(encoded), chunk_size):
            #         yield encoded[i : i + chunk_size]

            return StreamingResponse(
                content=iter([blocks_container]),
                media_type=MimeTypes.BLOCKS.value,
                headers={
                    "Content-Disposition": f"attachment; filename={record.record_name}"
                },
            )
        elif record.record_type == RecordType.PULL_REQUEST:
            self.logger.info("🟣🟣🟣 STREAM_GITHUB_PULL_REQUEST_MARKER 🟣🟣🟣")
            block_container = await self._build_pull_request_blocks(record)

            # async def generate_blocks_json() -> AsyncGenerator[bytes, None]:
            #     json_str = block_container.model_dump_json(indent=2)
            #     chunk_size = 81920
            #     encoded = json_str.encode("utf-8")
            #     for i in range(0, len(encoded), chunk_size):
            #         yield encoded[i : i + chunk_size]

            return StreamingResponse(
                content=iter([block_container]),
                media_type=MimeTypes.BLOCKS.value,
                headers={
                    "Content-Disposition": f"attachment; filename={record.record_name}"
                },
            )
        elif record.record_type == RecordType.FILE:
            self.logger.info("🟣🟣🟣 STREAM-FILE-MARKER 🟣🟣🟣")
            filename = record.record_name or f"{record.external_record_id}"
            return create_stream_record_response(
                    self._fetch_attachment_content(record),
                    filename=filename,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}"
                )
        elif record.record_type == RecordType.CODE_FILE:
            self.logger.info("🟣🟣🟣 STREAM-CODE-FILE-MARKER 🟣🟣🟣")
            filename = record.record_name or f"{record.external_record_id}"
            self.logger.info(f"record form stream : {record}")
            return create_stream_record_response(
                    self._fetch_code_file_content(record),
                    filename=filename,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}"
                )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported record type for streaming: {record.record_type}",
            )

    #------------------Sync Points-----------------------------------#
    async def _get_issues_sync_checkpoint(self,project_id:int) -> int | None:
        """
        Get project issues sync checkpoint.
        """
        try:
            sync_point_key = generate_record_sync_point_key("gitlab",project_id,"issues")
            sync_point_data = await self.record_sync_point.read_sync_point(sync_point_key)
            return sync_point_data.get("last_sync_time") if sync_point_data else None
        except Exception:
            return None

    async def _update_issues_sync_checkpoint(self, project_id:int, last_sync_time:str) -> None:
        """
        Update project issues sync checkpoint.
        """
        sync_point_key = generate_record_sync_point_key("gitlab",project_id,"issues")
        sync_point_data = {"last_sync_time": last_sync_time}
        await self.record_sync_point.update_sync_point(sync_point_key, sync_point_data)

    async def _get_mr_sync_checkpoint(self,project_id:int) -> int | None:
        """
        Get project merge requests sync checkpoint.
        """
        try:
            sync_point_key = generate_record_sync_point_key("gitlab",project_id,"merge_requests")
            sync_point_data = await self.record_sync_point.read_sync_point(sync_point_key)
            return sync_point_data.get("last_sync_time") if sync_point_data else None
        except Exception:
            return None

    async def _update_mrs_sync_checkpoint(self, project_id:int, last_sync_time:str) -> None:
        """
        Update project merge requests sync checkpoint.
        """
        sync_point_key = generate_record_sync_point_key("gitlab",project_id,"merge_requests")
        sync_point_data = {"last_sync_time": last_sync_time}
        await self.record_sync_point.update_sync_point(sync_point_key, sync_point_data)

    async def run_sync(self) -> None:
        """syncing various entities """
        self.logger.info("⚒️⚒️ Starting GitLab sync")
        self.logger.info("Syncing users")
        await self._sync_users()
        # TODO: sync members from user groups of gitlab if needed
        # TODO: projects belonging to a specific group same as projects belonging to a user group
        # TODO: what to consider these groups then link projects to these groups ?
        self.logger.info("🕛🕛 Sync projects")
        await self._sync_all_project()

    # ---------------------------Users Sync-----------------------------------#
    async def _sync_users(self) ->None:
        """   Fetch all active Gitlab users project wise   """
        """get all owned groups/or such
             ->find projects of each group
                 ->sync members of each owned proj.
                 ->if no proj. found sync members of group as such """
        self.logger.info("Syncing Gitlab AppMembers group wise then project wise")
        groups_res = self.data_source.list_groups(owned=True)
        # TODO: check in enterprise edition do gitlab accounts have members directly in it
        total_synced =0
        total_skipped =0
        dict_member:dict[int,GroupMember] = {}
        if groups_res.success and groups_res.data:
            groups = groups_res.data
            for group in groups:
                group_id = group.id
                group_name = group.name
                self.logger.info(f"syncing proj. wise users for group {group_name}")
                members_res = self.data_source.list_group_members_all(group_id)
                if not members_res.success or not members_res.data:
                    self.logger.info(f"No members found for group {group_id} or error in fetching members")
                    continue
                members = members_res.data
                for member in members:
                    dict_member[member.id] = member
        # syncing from all projects
        projects_res = self.data_source.list_projects(owned=True)
        if projects_res.success and projects_res.data:
            projects = projects_res.data
            for project in projects:
                project_id = project.id
                # if project_id != int(TEST_GITLAB_PROJECT_ID):
                #     continue
                project_name = project.name
                self.logger.info(f"syncing users for project {project_name}")
                members_res = self.data_source.list_project_members_all(project_id)
                if not members_res.success or not members_res.data:
                    self.logger.info(f"No members found for project {project_id} or error in fetching members")
                    continue
                members = members_res.data
                for member in members:
                    dict_member[member.id] = member
        # TODO: for user_groups of gitlab bringing them as groups on our platform
        app_users:list[AppUser] = []
        for member_id, member in dict_member.items():
            # print(f"Syncing user {member.username} with id {member_id}")
            user_email =  member.public_email
            if not user_email:
                total_skipped+=1
                self.logger.info(f"Email not found for user {member.username} with id {member_id}, skipping")
            else:
                app_user = AppUser(
                    app_name=self.connector_name,
                    org_id=self.data_entities_processor.org_id,
                    connector_id=self.connector_id,
                    source_user_id=str(member_id),
                    is_active=True,
                    email=user_email,
                    full_name=member.name,
                    )
                app_users.append(app_user)
        if app_users:
            await self.data_entities_processor.on_new_app_users(app_users)
            total_synced+=len(app_users)
            # for appuser migrate previously created pseudo group permissions to app users
            for user in app_users:
                try:
                    await self.data_entities_processor.migrate_group_to_user_by_external_id(
                        group_external_id=user.source_user_id,
                        user_email=user.email,
                        connector_id=self.connector_id
                    )
                except Exception as e:
                    # Log error but continue with other users
                    self.logger.warning(
                        f"Failed to migrate pseudo-group permissions for user {user.email}: {e}",
                        exc_info=True
                    )
                    continue
        self.logger.info(f"Total users synced: {total_synced}, Total users skipped: {total_skipped}")
        self.logger.info("Users sync and migration of pseudo groups complete")

    # ---------------------------Project level Sync-----------------------------------#
    async def _sync_all_project(self) -> None:
        """
        Docstring for _sync_all_repo_issue

        :param self: Description
        :param full_sync: Description
        :type full_sync: bool
        """
        # TODO: check api is since is supported modify code acc. as sync point depends
        current_timestamp = self._get_iso_time()
        gitlab_record_group_sync_key = generate_record_sync_point_key("gitlab","record_group","global")
        await self._sync_projects()
        await self.record_sync_point.update_sync_point(gitlab_record_group_sync_key, {"timestamp": current_timestamp})

    async def _sync_repo_main(self,project_id:int,project_path:str) -> None:
        """Syncs default branch files code.
        PROCESS: 1. Sync all folders level wise via paginated graphql api.
                 2. Sync all code repo. files via paginated graphql api.
        REASON:  both can  be in same api call but pagination to be seprate.
                 level wise files ordering not needed
        """
        # tree_res = self.data_source.list_repo_tree(project_id,recursive=True)
        # fetching file tree
        TOKEN = await self._get_api_token_()
        tree_list = []
        after_cursor = ""
        while True:
            try:
                tree_res = await self.data_source.get_repo_tree_g(token=TOKEN,project_id=project_path,ref="HEAD",after_cursor=after_cursor)
            except Exception as e:
                self.logger.error(f"Error in fetching tree skipping repo code files sync for {project_id}: {e}")
                return
            if not tree_res.data:
                self.logger.info(f"No tree found for project {project_id}")
                return
            data:dict[str,Any] = json.loads(tree_res.data)
            project_nodes = data.get("data",{}).get("project",{}).get("repository",{}).get("paginatedTree",{}).get("nodes",[])
            if not project_nodes:
                self.logger.info(f"No project nodes found for project {project_id}")
                return
            t_nodes:dict[str,Any] = project_nodes[0]
            file_path_nodes:list[dict[str,Any]] = t_nodes.get("trees",{}).get("nodes",[])
            tree_list.extend(file_path_nodes)
            self.logger.debug(f"❗❗appended {len(file_path_nodes)} file path nodes via GQL")
            if not t_nodes.get("trees",{}).get("pageInfo",{}).get("hasNextPage"):
                break
            after_cursor = t_nodes.get("trees",{}).get("pageInfo",{}).get("endCursor","")
            # self.logger.info(f"tree_res : {tree_res}")


        # self.logger.info(f"at tree level  : {tree_list}")
        list_records_new:list[RecordUpdate] = []
        path_to_parent_external_id_dict:dict[str,str]={}
        level_wise_files:dict[int,list[dict[str,Any]]] = {}
        for item in tree_list:
            file_path = item.get("path")
            parent_file_path  =  self.get_parent_path_from_path(file_path)
            level_file = len(parent_file_path)
            if level_file not in level_wise_files:
                level_wise_files[level_file] = []
            level_wise_files[level_file].append(item)
        # self.logger.info(f"level_wise_files : {level_wise_files}")

        for _level,files in sorted(level_wise_files.items()):
            for file in files:
                file_path = file.get("path")
                file_name = file.get("name")
                file_hash = file.get("sha")
                external_record_id = file.get("webPath")
                weburl = file.get("webUrl")
                if file.get("type") == "tree":
                    parent_path = self.get_parent_path_from_path(file_path)
                    parent_path = "/".join(parent_path)
                    # parent_path = f"{parent_path}" if parent_path else "/"
                    self.logger.info(f"parent_path : {parent_path} for file path {file_path}")
                    parent_external_record_id = None
                    if parent_path == "" or not parent_path:
                        parent_external_record_id = None
                    elif parent_path in path_to_parent_external_id_dict:
                        parent_external_record_id = path_to_parent_external_id_dict[parent_path]
                    else:
                        try:
                            tmp_parent_path = parent_path.split("/")
                            async with self.data_store_provider.transaction() as tx_store:
                                parent_record = await tx_store.get_record_by_path(
                                    connector_id=f"apps/{self.connector_id}",
                                    path=tmp_parent_path,
                                    record_group_name=str(project_path),
                                )
                            if parent_record:
                                self.logger.info(f"parent_record : {parent_record} for file path {file_path}")
                                parent_external_record_id = parent_record.get("externalRecordId")
                                path_to_parent_external_id_dict[parent_path] = parent_external_record_id
                            else:
                                # shd not be a case if then level ordering is wrong
                                self.logger.debug(f"Parent path {parent_path} not found in DB or Cache for {file_name}")
                        except Exception as e:
                            self.logger.error(f"Error in fetching parent record {parent_path}: {e}")
                    existing_record = None
                    async with self.data_store_provider.transaction() as tx_store:
                        existing_record = await tx_store.get_record_by_external_id(
                            connector_id=self.connector_id, external_id=f"{external_record_id}"
                        )
                    is_new = existing_record is None
                    record_id = str(uuid.uuid4())

                    tree_record = FileRecord(
                        id = existing_record.id if existing_record else record_id,
                        org_id = self.data_entities_processor.org_id,
                        record_name = str(file_name),
                        record_type = RecordType.FILE.value,
                        connector_name = self.connector_name,
                        connector_id = self.connector_id,
                        external_record_id = external_record_id,
                        version = 0,
                        origin = OriginTypes.CONNECTOR.value,
                        record_group_type = RecordGroupType.PROJECT.value,
                        external_record_group_id = str(project_id),
                        mime_type=MimeTypes.FOLDER.value,
                        external_revision_id=str(file_hash),
                        preview_renderable=False,
                        parent_external_record_id=parent_external_record_id,
                        is_file=False,
                        inherit_permissions=True,
                        weburl = weburl,
                    )
                    record_update = RecordUpdate(
                        record=tree_record,
                        is_new=is_new,
                        is_updated=False,
                        is_deleted=False,
                        metadata_changed=False,
                        content_changed=False,
                        permissions_changed=False,
                        external_record_id=str(external_record_id),
                    )
                    list_records_new.append(record_update)
            if list_records_new:
                await self._process_new_records(list_records_new)
                self.logger.debug(f"❗❗after processing new records {len(list_records_new)} records")
                list_records_new=[]

        # fetching code files
        after_cursor = ""
        # code_file_list = []
        while True:
            try:
                tree_res = await self.data_source.get_file_tree_g(token=TOKEN,project_id=project_path,ref="HEAD",after_cursor=after_cursor)
            except Exception as e:
                self.logger.error(f"Error in fetching file tree skipping repo code files sync for {project_id}: {e}")
                return
            if not tree_res.success:
                self.logger.error(f"❌❌ Error in fetching file tree skipping repo code files sync for {project_id}: {tree_res.error}")
                return
            if not tree_res.data:
                self.logger.info(f"❌❌ No file tree found for project {project_id}")
                return
            try:
                data: dict[str, Any] = json.loads(tree_res.data)
            except json.JSONDecodeError as e:
                self.logger.error(f"❌ Failed to parse file tree JSON for {project_id}: {e}")
                return
            project_nodes = data.get("data",{}).get("project",{}).get("repository",{}).get("paginatedTree",{}).get("nodes",[])
            if not project_nodes:
                self.logger.info(f"No project nodes found for project {project_id}")
                return
            t_nodes:dict[str,Any] = project_nodes[0]
            file_path_nodes:list[dict[str,Any]] = t_nodes.get("blobs",{}).get("nodes",[])
            if file_path_nodes:
                self.logger.debug(f"❗❗ files fetched via GQL: {len(file_path_nodes)} : {file_path_nodes}")
                await self.build_code_file_records(file_path_nodes,project_id,project_path)
            if not t_nodes.get("blobs",{}).get("pageInfo",{}).get("hasNextPage"):
                self.logger.info("✅✅ No more code file pages left exiting")
                break
            after_cursor = t_nodes.get("blobs",{}).get("pageInfo",{}).get("endCursor","")
            if not after_cursor:
                self.logger.warning(f"⚠️ hasNextPage but no endCursor for {project_id}, stopping")
                break

    async def build_code_file_records(self,code_file_list:list[dict[str,Any]],project_id:int,project_path:str)->None:
        """Process code file records and push to processing"""

        list_records_new:list[RecordUpdate] = []
        for file in code_file_list:
            file_path = file.get("path")
            file_name = file.get("name")
            file_hash = file.get("sha")
            external_record_id = file.get("webPath")
            weburl = file.get("webUrl")

            # getiing parent id code
            file_extension = file_name.split(".")[-1]
            # file_extension_type = getattr(ExtensionTypes, file_extension.upper(),None)
            file_mime = getattr(MimeTypes,file_extension.upper(),MimeTypes.TEXT).value
            parent_path = self.get_parent_path_from_path(file_path)
            parent_path = "/".join(parent_path)
            # parent_path = parent_path if parent_path else "/"
            parent_external_record_id = None
            if parent_path == "" or not parent_path:
                parent_external_record_id = None
            # elif parent_path in path_to_parent_external_id_dict:
            #     parent_external_record_id = path_to_parent_external_id_dict[parent_path]
            else:
                try:
                    tmp_parent_path = parent_path.split("/")
                    async with self.data_store_provider.transaction() as tx_store:
                        parent_record = await tx_store.get_record_by_path(
                            connector_id=f"apps/{self.connector_id}",
                            path=tmp_parent_path,
                            record_group_name=str(project_path),
                        )
                    if parent_record:
                        self.logger.debug(f"✅✅ Parent_record : {parent_record} for file path {file_path}")
                        parent_external_record_id = parent_record.get("externalRecordId")
                        # path_to_parent_external_id_dict[parent_path] = parent_external_record_id
                    else:
                        self.logger.debug(f"❗❗Parent path {parent_path} not found in DB or Cache for {file_name}")
                except Exception as e:
                    self.logger.error(f"Error in fetching parent record {parent_path}: {e}")
            # if parent_external_record_id:
            #     inherit_permissions=False
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id, external_id=f"{external_record_id}"
                )
            record_id = str(uuid.uuid4())
            code_file_record = CodeFileRecord(
                id = existing_record.id if existing_record else record_id,
                org_id = self.data_entities_processor.org_id,
                record_name = str(file_name),
                record_type = RecordType.CODE_FILE.value,
                connector_name = self.connector_name,
                connector_id = self.connector_id,
                external_record_id = external_record_id,
                version = 0,
                origin = OriginTypes.CONNECTOR.value,
                record_group_type = RecordGroupType.PROJECT.value,
                external_record_group_id = str(project_id),
                mime_type=file_mime,
                external_revision_id=str(file_hash),
                preview_renderable=False,
                file_path=file_path,
                file_hash=file_hash,
                inherit_permissions=True,
                parent_external_record_id=parent_external_record_id,
                weburl = weburl,
            )
            record_update = RecordUpdate(
                record=code_file_record,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
                external_record_id=external_record_id,
            )
            list_records_new.append(record_update)
        if list_records_new:
            await self._process_new_records(list_records_new)
            self.logger.info(f"after processing new records {len(list_records_new)} records")

    async def _fetch_code_file_content(self,record:Record)->AsyncGenerator[bytes,None]:
        """stream content of code file"""
        try:
            async with self.data_store_provider.transaction() as tx_store:
                file_path = await tx_store.get_record_path(record.id)

            self.logger.info(f"new_record from stream : {file_path}")

            project_id = int(record.external_record_group_id)
            file_res = self.data_source.get_file_content(project_id,file_path)
            if not file_res.success :
                self.logger.error(f"error in fetching file content {file_res.error}")
                raise
            if not file_res.data:
                self.logger.error(f"No file content found for file {file_path}" )
                return
            file_data = file_res.data
            file_content_coded = file_data.content
            decoded_bytes = base64.b64decode(file_content_coded)
            yield decoded_bytes
        except Exception as e:
            self.logger.error(f"Error fetching code content for record {record.id}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error fetching code content for record {record.id}: {e}"
            ) from e
    # ---------------------------Project Sync-----------------------------------#

    async def _sync_projects(self) -> None:
        """_summary_
        Args:
        """
        projects_res = self.data_source.list_projects(owned=True)
        if not projects_res.success:
            raise Exception("❌❌ Error in fetching projects")
        if not projects_res.data:
            self.logger.info("No owned projects found")
            return
        projects = projects_res.data
        for project in projects:
            # sync non email members as pseudo user groups
            await self._sync_project_members_as_pseudo(project)
            project_id:int = project.id
            project_path:str = project.path_with_namespace
            # if project_id == int(TEST_GITLAB_PROJECT_ID):
            #     await self._fetch_issues_batched(project_id)
            #     await self._fetch_prs_batched(project_id)
            #     await self._sync_repo_main(project_id,project_path)
            # else:
            #     self.logger.debug(f"⚠️ Project {project.name} has no ID, skipping syncing issues and repo code files")
            await self._fetch_issues_batched(project_id)
            await self._fetch_prs_batched(project_id)
            await self._sync_repo_main(project_id,project_path)

    async def _sync_project_members_as_pseudo(self, project:Project) -> None:
        """_summary_
        Args:
            project (Project): _description_
        """
        project_id = project.id
        project_name = project.name
        dict_member:dict[int,GroupMember] = {}
        self.logger.info(f"syncing users for project {project_name}")
        members_res = self.data_source.list_project_members_all(project_id)
        if not members_res.success :
            self.logger.error(f"❌❌Error in fetching members for project {project_id}")
            return
        if not members_res.data:
            self.logger.info(f"No members found for project {project_id} ")
            return
        members = members_res.data
        for member in members:
            # users_raw.append(member)
            dict_member[member.id] = member
        # make sudo groups of users with no email
        pseudo_groups_permissions = []
        for member in dict_member.values():
            member_email = member.public_email
            if not member_email :
                #TODO: giving default permissions to groups , make it acc. to gitlab access
                pseudo_group_permission = await self._transform_restrictions_to_permisions(member)
                if pseudo_group_permission:
                    pseudo_groups_permissions.append(pseudo_group_permission)
            else:
                # mail found usual app user create
                user_permission = Permission(
                    email=member.public_email,
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER,
                )
                if user_permission:
                    pseudo_groups_permissions.append(user_permission)

        project_record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=project.path_with_namespace,
                group_type=RecordGroupType.PROJECT.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                external_group_id=str(project.id),
        )
        self.logger.debug(f"permisions for project {project_name} : {pseudo_groups_permissions}")
        await self.data_entities_processor.on_new_record_groups([(project_record_group, pseudo_groups_permissions)])
        self.logger.info(f"Synced project {project_name} as record group ")

        # creating dummy record group for issues to inherit permissions
        # issues_record_group = RecordGroup(
        #     org_id=self.data_entities_processor.org_id,
        #     name = f"{project.path_with_namespace}-issues",
        #     group_type=RecordGroupType.ISSUE_GROUP.value,
        #     connector_name=self.connector_name,
        #     connector_id=self.connector_id,
        #     external_group_id=f"{project.id}-issues", # not a valid group id externally
        # )
        # self.logger.info("")
        # await self.data_entities_processor.on_new_record_groups([(issues_record_group, pseudo_groups_permissions)])
        # await self._fetch_issues_batched(project_id=project_id)

    async def _transform_restrictions_to_permisions(self,member:GroupMember)->Permission|None:
        """ Transform restrictions to permissions """
        principal_id = str(member.id)
        permission_type = PermissionType.OWNER.value
        if principal_id:
            permission = await self._create_permission_from_principal(
                "user",
                principal_id,
                permission_type,
                create_pseudo_group_if_missing=True  # Enable pseudo-group creation for record-level permissions
                )
        if permission:
            # permissions.append(permission)
            return permission
        return None

    async def _create_permission_from_principal(
        self,
        principal_type: str,
        principal_id: str,
        permission_type: PermissionType,
        *,
        create_pseudo_group_if_missing: bool = False
    ) -> Permission | None:
        """
        Create Permission object from principal data (user or group).

        This is a common function used by both space and page permission processing.

        Args:
            principal_type: "user" or "group"
            principal_id: accountId for users, groupId for groups
            permission_type: Mapped PermissionType enum
            create_pseudo_group_if_missing: If True and user not found, create a
                pseudo-group to preserve the permission. Used for record-level

        Returns:
            Permission object or None if principal not found in DB
        """
        try:
            if principal_type == "user":
                entity_type = EntityType.USER
                # Lookup user by source_user_id (accountId) using transaction store
                async with self.data_store_provider.transaction() as tx_store:
                    user = await tx_store.get_user_by_source_id(
                        source_user_id=principal_id,
                        connector_id=self.connector_id,
                    )
                    if user:
                        return Permission(
                            email=user.email,
                            type=permission_type,
                            entity_type=entity_type
                        )

                    # User not found - check if pseudo-group exists or should be created
                    if create_pseudo_group_if_missing:
                        # Check for existing pseudo-group
                        pseudo_group = await tx_store.get_user_group_by_external_id(
                            connector_id=self.connector_id,
                            external_id=principal_id,
                        )

                        if not pseudo_group:
                            # Create pseudo-group on-the-fly
                            pseudo_group = await self._create_pseudo_group(principal_id)

                        if pseudo_group:
                            self.logger.debug(
                                f"Using pseudo-group for user {principal_id} (no email available)"
                            )
                            return Permission(
                                external_id=pseudo_group.source_user_group_id,
                                type=permission_type,
                                entity_type=EntityType.GROUP
                            )

                    self.logger.debug(f"  ⚠️ User {principal_id} not found in DB, skipping permission")
                    return None
        except Exception as e:
            self.logger.error(f"❌ Failed to create permission from principal: {e}")
            return None

    async def _create_pseudo_group(self, account_id: str) -> AppUserGroup | None:
        """
        Create a pseudo-group for a user without email.

        This preserves permissions for users who don't have email addresses yet.
        The pseudo-group uses the user's accountId as source_user_group_id.

        Args:
            account_id: Gitlab user accountId

        Returns:
            Created AppUserGroup or None if creation fails
        """
        try:
            pseudo_group = AppUserGroup(
                app_name=Connectors.GITLAB,
                connector_id=self.connector_id,
                source_user_group_id=account_id,
                name=f"{PSEUDO_USER_GROUP_PREFIX} {account_id}",
                org_id=self.data_entities_processor.org_id,
            )

            # Save to database (empty members list)
            await self.data_entities_processor.on_new_user_groups([(pseudo_group, [])])
            self.logger.info(f"Created pseudo-group for user without email: {account_id}")

            return pseudo_group

        except Exception as e:
            self.logger.error(f"Failed to create pseudo-group for {account_id}: {e}")
            return None

    # ---------------------------Issues Sync-----------------------------------#

    async def _sync_issues_full(self, project:Project,last_sync_time: str | None = None) -> None:
        """_summary_

        Args:
            users (List[AppUser]): _description_
        """

    async def _fetch_issues_batched(
        self, project_id: int, last_sync_time: str | None = None
    ) -> None:
        """
        process: for each make TicketRecord
        return: list of Records consisting of Tickets
        Args:
            issue_batch (List[Issue]): _description_
            last_sync_time (str): _description_
        """
        # get issue permissions as of now inherit them from RECORD_GROUP PROJECT
        last_sync_time:int|None = await self._get_issues_sync_checkpoint(project_id)
        self.logger.debug(f"😂😂😂😂😂😂 LAST SYNC TIME  : {last_sync_time}")
        if last_sync_time is not None:
            since_dt = datetime.fromtimestamp(last_sync_time/1000, tz=timezone.utc)
        else:
            since_dt = None
        self.logger.debug(f"😂😂😂😂😂😂 SINCE TIME  : {since_dt}")
        issues_res = self.data_source.list_issues(project_id,updated_after=since_dt,order_by="updated_at",sort="asc")
        if not issues_res.success:
            raise Exception(f"❌❌ Error in fetching issues for project {project_id}")
        if not issues_res.data:
            self.logger.info(f"No issues found for project {project_id}")
            return
        all_issues:list[ProjectIssue] = issues_res.data
        total_issues = len(all_issues)
        self.logger.info(f"📦 Fetched {total_issues} issues, processing in batches...")
        # Process issues in batches
        batch_size = self.batch_size
        batch_number = 0
        for i in range(0, total_issues, batch_size):
            batch_number += 1
            issues_batch = all_issues[i : i + batch_size]
            batch_records: list[RecordUpdate] = []
            self.logger.info(
                f"📦 Processing batch {batch_number}: {len(issues_batch)} issues"
            )
            batch_records = await self._build_issue_records(issues_batch)
            # send batch results to process
            await self._process_new_records(batch_records)

    async def _process_new_records(self, batch_records: list[RecordUpdate]) -> None:
        """ Send new records in batches to process """
        # NOTE: all functions calling this ensures only tickets+files or pull_requests+files are sent here
        need_sync_update:bool=True
        for i in range(0, len(batch_records), self.batch_size):
            batch = batch_records[i : i + self.batch_size]
            batch_sent: list[tuple[Record, list[Permission]]] = [
                (record_update.record, record_update.new_permissions)
                for record_update in batch
            ]
            try:
                await self.data_entities_processor.on_new_records(batch_sent)
                if not need_sync_update:
                    continue
                last_sync_time = None
                project_id:int|None = None
                record_type:RecordType|None = None
                for record_update in batch:
                    if record_update.record.record_type == RecordType.TICKET :
                        record_type = RecordType.TICKET
                        last_sync_time=record_update.record.source_updated_at
                        project_id  = record_update.record.external_record_group_id
                    elif record_update.record.record_type == RecordType.PULL_REQUEST :
                        record_type = RecordType.PULL_REQUEST
                        last_sync_time=record_update.record.source_updated_at
                        project_id = record_update.record.external_record_group_id
                    else:
                        continue
                if project_id and last_sync_time:
                    if record_type == RecordType.TICKET:
                        await self._update_issues_sync_checkpoint(project_id,last_sync_time)
                    elif record_type == RecordType.PULL_REQUEST:
                        await self._update_mrs_sync_checkpoint(project_id,last_sync_time)
            except Exception as e:
                self.logger.error(f"❌❌Error in processing set of records: {e}")
                need_sync_update=False

        self.logger.info(f"✅✅ Processed {len(batch_records)} records")

    async def _build_issue_records(self, issue_batch: list[ProjectIssue]) -> list[RecordUpdate]:
        """        """
        record_updates_batch: list[RecordUpdate] = []
        for issue in issue_batch:
            # consider ticket types-> issue, incident, task
            issue_type = issue.type
            self.logger.info(f"Processing issue {issue.title} of type {issue_type}")
            record_update = await self._process_issue_incident_task_to_ticket(issue)
            if record_update:
                record_updates_batch.append(record_update)
                # get the file attachments from issue data
                # make file records for all except images
                markdown_content_raw: str = issue.description or ""
                attachments,markdown_content  = await self.parse_gitlab_uploads_clean_test(
                    markdown_content_raw
                )
                self.logger.debug(f"Processed markdown content for issue {issue.title}")
                if attachments:
                    file_record_updates = await self.make_file_records_from_list(
                        attachments=attachments, record=record_update.record
                    )
                    if file_record_updates:
                        record_updates_batch.extend(file_record_updates)
                        self.logger.info(
                            f"Added {len(file_record_updates)} attachments for issue {issue.title}"
                        )
            # adding notees attachments
            attachment_records  = await self.make_files_records_from_notes(issue,record_update.record)
            if attachment_records:
                record_updates_batch.extend(attachment_records)
                self.logger.debug(f"Added {len(attachment_records)} attachments for issue notes {issue.title}")
        return record_updates_batch

    async def _process_issue_incident_task_to_ticket(self, issue: ProjectIssue) -> RecordUpdate | None:
        """ _summary_
        Args:
            issue (Issue): _description_
        """
        try:
            # check if record already exists
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=f"{issue.id}"
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
                # TODO: body changes check as of now True default
                content_changed = True
                is_updated = True

            issue_type = ItemType.ISSUE.value
            if issue.issue_type=="incident":
                issue_type = ItemType.INCIDENT.value
            elif issue.issue_type=="task":
                issue_type = ItemType.TASK.value

            label_names: list[str] = []
            for label in issue.labels:
                label_names.append(label)
            # self.logger.debug(f"labels : {label_names}")
            # self.logger.debug(f"date format : {issue.created_at, issue.updated_at}")
            ticket_record = TicketRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=issue.title,
                external_record_id=str(issue.id),
                record_type=RecordType.TICKET.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=self.datetime_to_epoch_ms(issue.updated_at),
                source_created_at=self.datetime_to_epoch_ms(issue.created_at),
                version=0,  # not used further so 0
                external_record_group_id=str(issue.project_id),
                org_id=self.data_entities_processor.org_id,
                record_group_type=RecordGroupType.PROJECT.value,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=issue.web_url,
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
                external_record_id=str(issue.id),
            )
        except Exception as e:
            self.logger.error(
                f"Error in processing issue/task/incident to ticket: {e}", exc_info=True
            )
            return None

    async def _build_ticket_blocks(self, record: Record) -> BlocksContainer:
        """_summary_

        Args:
            record (Record): _description_

        Returns:
            BlocksContainer: _description_
        """
        raw_url = record.weburl.split("/")
        self.logger.info(f"raw_url : {raw_url}")
        issue_number = int(raw_url[7])
        project_id = record.external_record_group_id
        issue_res = self.data_source.get_issue(
            project_id=project_id, issue_iid=issue_number
        )
        if not issue_res.success :
            raise Exception(
                f"❌❌ Failed to fetch issue details for record {record.external_record_id}: {issue_res.error}"
            )
        if not issue_res.data:
            raise Exception(
                f"❌❌ No issue data found for record {record.external_record_id}"
            )
        base_project_url = f"https://gitlab.com/api/v4/projects/{record.external_record_group_id}"
        block_group_number = 0
        blocks: list[Block] = []
        block_groups: list[BlockGroup] = []
        issue = issue_res.data

        # getting modi. markdown  content with images as base64
        markdown_content_raw: str = getattr(issue,"description","") or ""
        markdown_content_with_images_base64 = await self.embed_images_as_base64(
            markdown_content_raw, base_project_url
        )
        self.logger.debug(f"Processed markdown content for issue {issue.title}")
        # NOTE: Adding record name into Content for record name search Permanently FIX todo
        markdown_content_with_images_base64 = f"# {issue.title}\n\n{markdown_content_with_images_base64}"
        list_remaining_records: list[RecordUpdate] = []
        child_records,remaining_records =await self.make_child_records_of_attachments(markdown_raw=markdown_content_raw,record=record)
        list_remaining_records.extend(remaining_records)
        # bg of title and desc./body
        bg_0 = BlockGroup(
            index=block_group_number,
            name=record.record_name,
            type=GroupType.TEXT_SECTION.value,
            format=DataFormat.MARKDOWN.value,
            sub_type=GroupSubType.CONTENT.value,
            source_group_id=record.weburl,
            data=markdown_content_with_images_base64,
            source_modified_date=str(self.string_to_datetime(issue.updated_at)),
            requires_processing=True,
            children_records=child_records,
        )
        block_groups.append(bg_0)
        # make blocks of issue comments
        comments_bg,remaining_records = await self._build_comment_blocks(
            issue_url=record.weburl, parent_index=block_group_number, record=record
        )
        block_groups.extend(comments_bg)
        block_group_number += len(comments_bg)
        list_remaining_records.extend(remaining_records)
        blocks_container = BlocksContainer(blocks=blocks, block_groups=block_groups)
        await self._process_new_records(list_remaining_records)

        blocks_json = blocks_container.model_dump_json(indent=2)
        return blocks_json.encode('utf-8')
        # return blocks_container

    async def _sync_records_incremental(self) -> None:
        """_summary_
        {NOT USED} use _fetch_issues_batched with last sync time
        when syncing so to avoid previosly synced files
        Args:
        """
        return

    async def _handle_page_upsert_event_issue(self) -> None:
        return

    async def _handle_record_updates(self, issue_update: RecordUpdate) -> None:
        """_summary_

        Args:
            issue_update (IssueUpdate): _description_
        """

    async def reindex_records(self) -> None:
        return

    async def run_incremental_sync(self) -> None:
        return

    # ---------------------------Comments sync-----------------------------------#

    async def _build_comment_blocks(
        self, issue_url: str, parent_index: int, record: Record
    ) -> tuple[list[BlockGroup],list[RecordUpdate]]:
        """             """
        self.logger.info(f"Building comment blocks for issue: {issue_url}")
        raw_url = issue_url.split("/")
        self.logger.info(f"raw_url : {raw_url}")
        issue_number = int(raw_url[7])
        # Fetching issue comments if present
        # TODO: will date wise filtering be needed here, as of now None
        comments_res = self.data_source.list_issue_notes(
            project_id=int(record.external_record_group_id), issue_iid=issue_number
        )
        if not comments_res.success :
            raise Exception(
                f"Failed to fetch comments for issue {issue_url}: {comments_res.error}"
            )
        if not comments_res.data:
            self.logger.info(
                f"No comments found for issue {issue_url}"
            )
            # return []
        block_groups: list[BlockGroup] = []
        list_remaining_records: list[RecordUpdate] = []
        block_group_number = parent_index + 1
        comments:list[ProjectIssueNote] = comments_res.data
        self.logger.info(f"Fetched {len(comments)} comments for issue {issue_url}, building blocks...")
        base_project_url = f"https://gitlab.com/api/v4/projects/{record.external_record_group_id}"
        for comment in comments:
            raw_markdown_content: str = getattr(comment,"body","") or ""
            child_records,remaining_records = await self.make_child_records_of_attachments(markdown_raw=raw_markdown_content,record=record)
            list_remaining_records.extend(remaining_records)
            markdown_content_with_images_base64 = await self.embed_images_as_base64(
                raw_markdown_content,base_project_url
            )
            # making comment name
            comment_name = ""
            comment_author = getattr(comment,"author",{}) or {}
            comment_username = comment_author.get("username")
            if comment_username:
                comment_name = f"Comment by {comment_username} on issue {issue_number}"
            else:
                self.logger.debug(f"author : {comment.author}")
                comment_name = f"Comment on issue {issue_number}"
            bg = BlockGroup(
                index=block_group_number,
                parent_index=parent_index,
                name=comment_name,
                type=GroupType.TEXT_SECTION.value,
                format=DataFormat.MARKDOWN.value,
                sub_type=GroupSubType.COMMENT.value,
                data=markdown_content_with_images_base64,
                weburl=issue_url,
                requires_processing=True,
                children_records=child_records,
            )
            block_group_number += 1
            block_groups.append(bg)
        return block_groups,list_remaining_records

    async def _build_merge_request_comment_blocks(self, mr_url: str, parent_index: int, record: Record) -> tuple[list[BlockGroup],list[RecordUpdate]]:
        """Build comment blocks for merge request"""
        self.logger.info(f"Building comment block groups for merge request {record.record_name}")
        raw_url = mr_url.split("/")
        self.logger.info(f"raw_url : {raw_url}")
        mr_number = int(raw_url[7])
        comments_res = self.data_source.list_merge_request_notes(project_id=int(record.external_record_group_id), mr_iid=mr_number)
        if not comments_res.success :
            raise Exception(
                f"❌❌ Failed to fetch comments for merge request {mr_url}: {comments_res.error}"
            )
        if not comments_res.data:
            self.logger.info(
                f"No comments found for merge request {mr_url}"
            )
        # handling usual comments and review comments together
        block_groups: list[BlockGroup] = []
        block_group_number = parent_index + 1
        comments:list[ProjectMergeRequestNote] = comments_res.data
        self.logger.debug(f"Fetched {len(comments)} comments for merge request {mr_url}, building blocks...")
        list_remaining_attachments:list[RecordUpdate] = []
        map_file_r_comments:dict[str,list[BlockComment]]={}
        base_project_url = f"https://gitlab.com/api/v4/projects/{record.external_record_group_id}"
        for comment in comments:
            # classify as system, usual or file based comment
            # make bg of usual comments at once, map r_comments with file
            is_system_comment = getattr(comment,"system",False)
            is_review_comment = getattr(comment,"position",None)
            if not is_system_comment:
                raw_markdown_content: str = getattr(comment,"body","") or ""

                markdown_content_with_images_base64 = await self.embed_images_as_base64(
                    raw_markdown_content,base_project_url
                )
                child_records,remaining_attachments = await self.make_child_records_of_attachments(
                    markdown_raw=raw_markdown_content, record=record
                )
                list_remaining_attachments.extend(remaining_attachments)
                # making comment name
                comment_name = ""
                comment_author = getattr(comment,"author",{})
                comment_username = comment_author.get("username")
                if comment_username:
                    comment_name = f"Comment by {comment_username} on merge request {mr_number}"
                else:
                    self.logger.debug(f"author : {comment_author}")
                    comment_name = f"Comment on merge request {mr_number}"
                comment_modified_date = getattr(comment,"updated_at","")
                source_modified_date = self.string_to_datetime(comment_modified_date)
                bg = BlockGroup(
                    index=block_group_number,
                    parent_index=parent_index,
                    name=comment_name,
                    type=GroupType.TEXT_SECTION.value,
                    format=DataFormat.MARKDOWN.value,
                    sub_type=GroupSubType.COMMENT.value,
                    # source_group_id=comment.url,
                    data=markdown_content_with_images_base64,
                    weburl=mr_url,
                    source_modified_date=source_modified_date,
                    requires_processing=True,
                    children_records=child_records,
                )
                block_group_number += 1
                block_groups.append(bg)
            elif is_review_comment:
                # will need to get file changes per file, new  file content, then attach mapped r_comments
                raw_markdown_content: str = getattr(comment,"body","") or ""
                markdown_content_with_images_base64 = (
                    await self.embed_images_as_base64(raw_markdown_content,base_project_url)
                )
                comment_attachments,remaining_attachments = await self.make_block_comment_of_attachments(
                    markdown_raw=raw_markdown_content, record=record
                )
                list_remaining_attachments.extend(remaining_attachments)
                position = getattr(comment,"position",{})
                file_path = position.get("new_path")
                comment_modified_date = getattr(comment,"updated_at","")
                comment_created_date = getattr(comment,"created_at","")
                source_modified_date = self.string_to_datetime(comment_modified_date)
                source_created_date = self.string_to_datetime(comment_created_date)
                block_comment = BlockComment(
                    text=markdown_content_with_images_base64,
                    format=DataFormat.MARKDOWN.value,
                    updated_at=source_modified_date,
                    created_at=source_created_date,
                    attachments=comment_attachments,
                )
                if file_path:
                    if file_path in map_file_r_comments:
                        map_file_r_comments[file_path].append(block_comment)
                    else:
                        map_file_r_comments[file_path] = [block_comment]

        # fetching file changes of mr
        # iterate through each file changes, append with new file content
        # to get file content use mr -> sha as ref with path pf file
        file_changes_res = self.data_source.list_merge_request_changes(
            project_id=int(record.external_record_group_id), mr_iid=mr_number
        )
        if not file_changes_res.success :
            self.logger.error(
                f"❌❌ Failed to fetch file changes for merge request {mr_url}: {file_changes_res.error}"
            )
            raise
        if not file_changes_res.data:
            self.logger.info(
                f"No file changes found for merge request {mr_url}"
            )
        file_changes = file_changes_res.data
        # TODO: below call Can be avoided once Base SHA and head sha
        # are included as fields in pull request record while streaming
        # Also the additional properties of pr record included while calling stream record
        tmp_mr_res = self.data_source.get_merge_request(project_id=int(record.external_record_group_id),mr_iid=mr_number)
        tmp_mr = tmp_mr_res.data
        tmp_mr_sha = getattr(tmp_mr,"sha","")
        self.logger.debug(f"tmp_mr_sha : {tmp_mr_sha}")
        changes = file_changes.get("changes", [])
        for file_change in changes:
            file_path = file_change.get("new_path","")
            diff_content = file_change.get("diff","")
            # fetching new file content
            new_file_content_res = self.data_source.get_file_content(project_id=int(record.external_record_group_id),file_path=file_path,ref=tmp_mr_sha)
            if not new_file_content_res.success :
                self.logger.error(f"❌❌ Failed to fetch new file content for file {file_path} in merge request {mr_url}: {new_file_content_res.error}")
                continue
            if not new_file_content_res.data:
                self.logger.info(f"No new file content found for file {file_path} in merge request {mr_url}")
                continue
            new_file = new_file_content_res.data
            new_file_content = getattr(new_file,'content',"")
            self.logger.debug(f"new file content found for file {file_path} in merge request : {new_file_content}")
            try:
                # Decode base64 content from Gitlab API else add encoded content
                file_content = base64.b64decode(
                    new_file_content
                ).decode("utf-8")
            except Exception as e:
                self.logger.error(
                    f"Failed to decode code file content for {file_path}: {e}"
                )
                file_content = new_file_content
            bg_n = BlockGroup(
                index=block_group_number,
                name=f"block for file {file_path}",
                type=GroupType.FULL_CODE_PATCH,
                format=DataFormat.MARKDOWN,
                sub_type=GroupSubType.PR_FILE_CHANGE,
                data=diff_content
                + str("\n\nFull File Content:\n")
                + str(file_content),
                comments=map_file_r_comments.get(file_path, []),
                requires_processing=True,
            )
            block_groups.append(bg_n)
            block_group_number += 1
        return block_groups,list_remaining_attachments

    async def make_files_records_from_notes(self, issue: ProjectIssue, record:Record) -> list[RecordUpdate]:
        """Make file records from notes"""
        notes_res = self.data_source.list_issue_notes(project_id=int(issue.project_id), issue_iid=issue.iid)
        if not notes_res.success :
            raise Exception(f"❌❌ Failed to fetch notes for issue {issue.title}: {notes_res.error}")
        if not notes_res.data:
            self.logger.info(f"No notes found for issue {issue.title}")
            return None
        notes = notes_res.data
        record_updates_batch: list[RecordUpdate] = []
        for note in notes:
            note_content = getattr(note,"body","") or ""
            attachments,_ = await self.parse_gitlab_uploads_clean_test(note_content)
            if attachments:
                file_record_updates = await self.make_file_records_from_list(
                    attachments=attachments, record=record
                )
                if file_record_updates:
                    record_updates_batch.extend(file_record_updates)
                    self.logger.info(f"Added {len(file_record_updates)} attachments for issue {issue.title}")
        return record_updates_batch

    async def make_files_records_from_notes_mr(self,mr:ProjectMergeRequest,record:Record) -> list[RecordUpdate]:
        """Make file records from notes"""
        notes_res = self.data_source.list_merge_request_notes(project_id=int(mr.project_id), mr_iid=mr.iid)
        if not notes_res.success :
            raise Exception(f"❌❌ Failed to fetch notes for merge request {mr.title}: {notes_res.error}")
        if not notes_res.data:
            self.logger.info(f"No notes found for merge request {mr.title}")
            return None
        notes = notes_res.data
        record_updates_batch: list[RecordUpdate] = []
        for note in notes:
            note_content = getattr(note,"body","") or ""
            attachments,_ = await self.parse_gitlab_uploads_clean_test(note_content)
            if attachments:
                file_record_updates = await self.make_file_records_from_list(
                    attachments=attachments, record=record
                )
                if file_record_updates:
                    record_updates_batch.extend(file_record_updates)
                    self.logger.info(f"Added {len(file_record_updates)} attachments for merge request {mr.title}")
        return record_updates_batch

    # ---------------------------Pull Requests-----------------------------------#

    async def _fetch_prs_batched(self,project_id:int) ->None:
        """ Syncing merge requests in batches """
        last_sync_time = await self._get_mr_sync_checkpoint(project_id)
        if last_sync_time is not None:
            since_dt = datetime.fromtimestamp(last_sync_time/1000, tz=timezone.utc)
        else:
            since_dt = None
        prs_res = self.data_source.list_merge_requests(project_id,updated_after=since_dt,order_by="updated_at",sort="asc")
        if not prs_res.success :
            self.logger.error(f"Error in fetching issues for projectId {project_id}")
            return
        if not prs_res.data :
            self.logger.info(f"No merge requests found for projectId {project_id}")

        all_prs:list[ProjectMergeRequest] = prs_res.data
        total_prs = len(all_prs)
        self.logger.info(f"📦 Fetched {total_prs} merge requests, processing in batches...")
        # Process issues in batches
        batch_size = self.batch_size
        batch_number = 0
        for i in range(0, total_prs, batch_size):
            batch_number += 1
            prs_batch = all_prs[i : i + batch_size]
            batch_records: list[RecordUpdate] = []
            self.logger.info(
                f"📦 Processing batch {batch_number}: {len(prs_batch)} merge requests"
            )
            batch_records = await self._build_pr_records(prs_batch)
            # send batch results to process
            await self._process_new_records(batch_records)

    async def _build_pr_records(self,prs_batch:list[ProjectMergeRequest])->list[RecordUpdate]:
        """Make merge requests of gitlab projects into PullRequestRecords"""
        record_updates_batch: list[RecordUpdate] = []
        for pr in prs_batch:
            self.logger.debug(f"⚒️⚒️ Processing merge request {pr.title}")
            record_update = await self._process_mr_to_pull_request(pr)
            if record_update:
                record_updates_batch.append(record_update)
                # get the file attachments from mr data
                # make file records for all except images
                markdown_content_raw: str = getattr(pr,"description","") or ""
                attachments,markdown_content  = await self.parse_gitlab_uploads_clean_test(markdown_content_raw)
                self.logger.debug(f"Processed markdown content for mr {pr.title}")
                if attachments:
                    file_record_updates = await self.make_file_records_from_list(
                        attachments=attachments, record=record_update.record
                    )
                    if file_record_updates:
                        record_updates_batch.extend(file_record_updates)
                        self.logger.info(f"Added {len(file_record_updates)} attachments for mr {pr.title}")
                # adding notes attachments
                attachment_records = await self.make_files_records_from_notes_mr(pr,record_update.record)
                if attachment_records:
                    record_updates_batch.extend(attachment_records)
                    self.logger.debug(f"Added {len(attachment_records)} attachments for mr notes {pr.title}")
        return record_updates_batch

    async def _process_mr_to_pull_request(self, pr: ProjectMergeRequest) -> RecordUpdate | None:
        """Process merge request to pull request"""
        try:
            # check if record already exists
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=f"{pr.id}"
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
                if existing_record.record_name != pr.title:
                    metadata_changed = True
                    is_updated = True
                # TODO: body changes check as of now True default
                content_changed = True
                is_updated = True

            label_names: list[str] = []
            for label in pr.labels:
                label_names.append(label)
            # assignee_list:list[str] = []
            # for assignees in pr.assignees:
            #     assignee_list.append(assignees.get("username"))
            assignee_list: list[str] = [assignees.get("username") for assignees in pr.assignees]
            # reviewer_names:list[str] = []
            # for reviewers in pr.reviewers:
            #     reviewer_names.append(reviewers.get("username"))
            reviewer_names: list[str] = [reviewers.get("username") for reviewers in pr.reviewers]
            merged_by:str = (pr.merged_by.get("username") if pr.merged_by else None )
            self.logger.debug(f"labels : {label_names}")
            self.logger.debug(f"date format : {pr.created_at, pr.updated_at}")
            merge_request_record = PullRequestRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=pr.title,
                external_record_id=str(pr.id),
                record_type=RecordType.PULL_REQUEST.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=self.datetime_to_epoch_ms(pr.updated_at),
                source_created_at=self.datetime_to_epoch_ms(pr.created_at),
                version=0,  # not used further so 0
                external_record_group_id=str(pr.project_id),
                org_id=self.data_entities_processor.org_id,
                record_group_type=RecordGroupType.PROJECT.value,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=pr.web_url,
                status=pr.state,
                external_revision_id=str(self.datetime_to_epoch_ms(pr.updated_at)),
                preview_renderable=False,
                mergeable = pr.merge_status,
                labels=label_names,
                inherit_permissions=True,
                assignee=assignee_list,
                merged_by = merged_by,
                review_name = reviewer_names,
            )
            return RecordUpdate(
                record=merge_request_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                old_permissions=[],
                new_permissions=[],
                external_record_id=str(pr.id),
            )
        except Exception as e:
            self.logger.error(
                f"❌❌ Error in processing merge request to pull request: {e}", exc_info=True
            )
            raise

    async def _build_pull_request_blocks(self, record: Record) -> BlocksContainer:
        # TODO: think for BG as code file updates how as in newer commit some files same as old
        # TODO: think of keys when PR gets updated like only when metadata is getting updated or say body and also consider it for file changes
        raw_url = record.weburl.split("/")
        self.logger.debug(f"weburl :  {raw_url}")
        mr_number = int(raw_url[7])
        project_id = record.external_record_group_id
        mr_res = self.data_source.get_merge_request(project_id=project_id, mr_iid=mr_number)
        if not mr_res.success:
            raise Exception(f"❌❌ Failed to fetch merge request details for record {record.external_record_id}: {mr_res.error}")

        if not mr_res.data:
            raise Exception(f"❌❌ No merge request data found for record {record.external_record_id}")
        # TODO: when personal hosting base urls might be different
        base_project_url = f"https://gitlab.com/api/v4/projects/{record.external_record_group_id}"
        block_group_number = 0
        block_number = 0
        blocks: list[Block] = []
        block_groups: list[BlockGroup] = []
        list_remaining_attachments: list[RecordUpdate] = []
        mr = mr_res.data
        markdown_content_raw: str = mr.description or ""
        markdown_with_images_base64 = await self.embed_images_as_base64(
            markdown_content_raw, base_project_url
        )
        self.logger.debug(f"Processed markdown content for mr {mr.title}")
        markdown_content_with_title = f"{mr.title}\n\n{markdown_with_images_base64}"
        list_child_records,remaining_attachments = await self.make_child_records_of_attachments(markdown_content_raw,record)
        list_remaining_attachments.extend(remaining_attachments)
        # bg of title and description of mr
        bg_0 = BlockGroup(
            index=block_group_number,
            name=record.record_name,
            type=GroupType.TEXT_SECTION.value,
            format=DataFormat.MARKDOWN.value,
            sub_type=GroupSubType.CONTENT.value,
            source_group_id=record.weburl,
            data=markdown_content_with_title,
            source_modified_date=str(self.string_to_datetime(mr.updated_at)),
            requires_processing=True,
            children_records=list_child_records,
        )
        self.logger.debug(f"block group for title and description created for merge request {mr_number}")
        block_groups.append(bg_0)
        # make blocks of merge request comments and file wise review comments
        comments_bg,remaining_attachments = await self._build_merge_request_comment_blocks(
            mr_url=record.weburl, parent_index=block_group_number, record=record
        )
        block_groups.extend(comments_bg)
        block_group_number += len(comments_bg)
        list_remaining_attachments.extend(remaining_attachments)
        # list commits of mr
        mr_commits_res = self.data_source.list_merge_requests_commits(project_id=project_id,mr_iid=mr_number)
        if not mr_commits_res.success :
            raise Exception(f"❌❌ Failed to fetch commits for merge request {mr_number}: {mr_commits_res.error}")
        if not mr_commits_res.data:
            self.logger.info(f"No commits found for merge request {mr_number}")
        mr_commits:list[ProjectCommit] = mr_commits_res.data
        for commit in mr_commits:
            commit_message = getattr(commit, "message","")
            commit_title = getattr(commit, "title","")
            commit_web_url = getattr(commit, "web_url","")
            commit_id = getattr(commit, "id","")
            commit_committed_date = getattr(commit, "committed_date","")
            block = Block(
                index=block_number,
                parent_index=block_group_number,
                type=BlockType.TEXT.value,
                sub_type=BlockSubType.COMMIT.value,
                weburl=commit_web_url,
                format=DataFormat.MARKDOWN,
                data=commit_message,
                source_id=commit_id,
                name = commit_title,
                source_creation_date = str(self.string_to_datetime(commit_committed_date)),
            )
            block_number += 1
            blocks.append(block)
        bg_new = BlockGroup(
            index=block_group_number,
            name="block group for commits",
            type=GroupType.COMMITS,
            description=f"List of commits for merge request : {mr_number}",
        )
        block_groups.append(bg_new)
        blocks_container = BlocksContainer(blocks=blocks, block_groups=block_groups)
        self.logger.debug(f"block and groups created for merge request {mr_number}")
        await self._process_new_records(list_remaining_attachments)
        blocks_json = blocks_container.model_dump_json(indent=2)
        return blocks_json.encode('utf-8')
        # return blocks_container

    # ---------------------------Attachment functions-----------------------------------#

    async def embed_images_as_base64(self, body_content: str,base_project_url:str) -> str:
        """
        getting raw markdown content, then getting images as base64 and appending in markdown content
        """
        self.logger.debug(
            "Embedding images as base64 in markdown content in embed_images_as_base64 function"
        )
        attachments,markdown_content_clean  = await self.parse_gitlab_uploads_clean_test(
            body_content
        )
        if not attachments:
            return markdown_content_clean
        self.logger.info(f"attachments found for embedding : {attachments}")
        for attach in attachments:
            self.logger.debug(f"Processing attachment for embedding: {attach}")
            if attach.get("category") != "image":
                continue
            attachment_url = attach.get("href")
            self.logger.debug(f"Fetching image from URL: {attachment_url}")
            full_attachment_url = f"{base_project_url}{attachment_url}"
            try:
                image_bytes = await self.get_img_bytes(full_attachment_url)
                if image_bytes:
                    # to get image format as in attachment data just an image
                    img = Image.open(BytesIO(image_bytes))
                    fmt = img.format.lower() if img.format else "png"
                    base64_data = base64.b64encode(image_bytes).decode("utf-8")
                    md_image_data = f"![Image](data:image/{fmt};base64,{base64_data})"
                    markdown_content_clean += f"{md_image_data}"
            except Exception as e:
                self.logger.error(f"Error embedding image from {attachment_url}: {e}")
                continue
        return markdown_content_clean

    async def make_file_records_from_list(
        self, attachments: list[dict[str, Any]], record: Record
    ) -> list[RecordUpdate]:
        """Building file records from list of attachment links."""
        base_url_for_attachments = f"https://gitlab.com/api/v4/projects/{record.external_record_group_id}"
        list_records_new: list[RecordUpdate] = []
        for attach in attachments:
            if attach.get("category") == "image":
                continue
            # creating file record for each attachment
            attachment_url = attach.get("href")
            full_attachment_url = f"{base_url_for_attachments}{attachment_url}"
            attachment_name = attach.get("filename")
            attachment_type = attach.get("filetype")
            self.logger.info(
                f"Processing attachment: {attachment_name} of type {attachment_type} from URL: {attachment_url}"
            )
            if not attachment_url or not attachment_name:
                self.logger.warning(
                    f"Skipping attachment due to missing URL or name: {attach}"
                )
                continue

            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=f"{full_attachment_url}"
                )
            # detect changes
            record_id = str(uuid.uuid4())

            filerecord = FileRecord(
                id=existing_record.id if existing_record else record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=attachment_name,
                record_type=RecordType.FILE.value,
                external_record_id=str(full_attachment_url),
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR,
                weburl=str(full_attachment_url),
                record_group_type=RecordGroupType.ISSUE_GROUP.value,
                parent_external_record_id=record.external_record_id,
                parent_record_type=record.record_type,
                external_record_group_id=record.external_record_group_id,
                mime_type=getattr(
                    MimeTypes, attachment_type.upper(), MimeTypes.UNKNOWN
                ).value,
                extension=attachment_type.lower(),
                is_file=True,
                inherit_permissions=True,
                preview_renderable=True,
                version=0,
                size_in_bytes=0,  # unknown
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
                external_record_id=full_attachment_url,
            )
            list_records_new.append(record_update)

        return list_records_new

    async def _fetch_attachment_content(self,record:Record)->AsyncGenerator[bytes,None]:
        """stream content from gitlab """
        try:
            attachment_id = record.external_record_id
            if not attachment_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"No attachment ID available for record {record.id}"
                )
            # make call to fetch attachment content
            record_url = record.weburl
            if not record_url:
                raise HTTPException(
                    status_code=400,
                    detail=f"No record URL available for record {record.id}"
                )
            GITLAB_TOKEN = await self._get_api_token_()
            self.logger.info(f"Fetching file from URL: {record_url}")
            headers = {
                "Authorization": f"Bearer {GITLAB_TOKEN}",
                "Accept": "application/octet-stream",
            }

            async with httpx.AsyncClient(follow_redirects=True) as client:
                async with client.stream(
                    'GET',
                    record_url,
                    headers = headers,
                    timeout=30.0
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(chunk_size=16000):
                        yield chunk
        except Exception as e:
            self.logger.error(f"Error fetching attachment content for record {record.id}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error fetching attachment content for record {record.id}: {e}"
            ) from e

    async def make_child_records_of_attachments(self,markdown_raw:str,record:Record)->tuple[list[ChildRecord],list[RecordUpdate]]:
        """make child records of attachments from markdown raw content"""
        attachments,markdown_content  = await self.parse_gitlab_uploads_clean_test(
            markdown_raw
        )
        child_records: list[ChildRecord] = []
        remaining_attachments:list[RecordUpdate] = []
        base_url_for_attachments = f"https://gitlab.com/api/v4/projects/{record.external_record_group_id}"
        for attach in attachments:
            if attach.get("category") == "image":
                continue
            attachment_url = attach.get("href")
            full_attachment_url = f"{base_url_for_attachments}{attachment_url}"
            attachment_name = attach.get("filename")
            if not attachment_url or not attachment_name:
                self.logger.warning(
                    f"Skipping attachment due to missing URL or name: {attach}"
                )
                continue
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=f"{full_attachment_url}"
                )
            if existing_record:
                child_record = ChildRecord(
                    child_id=existing_record.id,
                    child_type=ChildType.RECORD,
                    child_name=existing_record.record_name,
                )
                child_records.append(child_record)
            else:
                remaining_attachment = await self.make_file_records_from_list([attach],record)
                remaining_attachments.extend(remaining_attachment)
                if remaining_attachment:
                    child_record = ChildRecord(
                        child_id=remaining_attachment[0].record.id,
                        child_type=ChildType.RECORD,
                        child_name=remaining_attachment[0].record.record_name,
                    )
                    child_records.append(child_record)
        return child_records,remaining_attachments

    async def make_block_comment_of_attachments(self,markdown_raw:str,record:Record)->tuple[list[CommentAttachment],list[RecordUpdate]]:
        """ make comment attachments from markdown raw content"""
        attachments,markdown_content  = await self.parse_gitlab_uploads_clean_test(markdown_raw)
        comment_attachments: list[CommentAttachment] = []
        remaining_attachments: list[RecordUpdate] = []
        base_url_for_attachments = f"https://gitlab.com/api/v4/projects/{record.external_record_group_id}"
        for attach in attachments:
            if attach.get("category") == "image":
                continue
            attachment_url = attach.get("href")
            full_attachment_url = f"{base_url_for_attachments}{attachment_url}"
            attachment_name = attach.get("filename")
            if not attachment_url or not attachment_name:
                self.logger.warning(
                    f"Skipping attachment due to missing URL or name: {attach}"
                )
                continue
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=f"{full_attachment_url}"
                )
            if existing_record:
                comment_attachment = CommentAttachment(
                    name=existing_record.record_name,
                    id=existing_record.id,
                )
                comment_attachments.append(comment_attachment)
            else:
                remaining_attachment = await self.make_file_records_from_list([attach],record)
                remaining_attachments.extend(remaining_attachment)
                if remaining_attachment:
                    comment_attachment = CommentAttachment(
                        name=remaining_attachment[0].record.record_name,
                        id=remaining_attachment[0].record.id,
                    )
                    comment_attachments.append(comment_attachment)
        return comment_attachments,remaining_attachments

    # ---------------------------insitu functions-----------------------------------#

    def datetime_to_epoch_ms(self, dt: datetime|str) -> int:
        # make sure it's timezone-aware (assume UTC if missing)
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    def string_to_datetime(self,time_str:str) -> datetime|None:
        try:
            return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        except Exception:
            self.logger.debug("issue in conversion of date time")
            return None

    async def _get_api_token_(self) -> str:
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
        access_token = credentials_config.get("access_token", "")

        if not access_token:
            self.logger.error("❌Github configuration not found.")
            raise ValueError("Github credentials not found")

        return access_token
        # self.logger.debug(f"Successfully retrieved GitLab API token from configuration.{GITLAB_TOKEN}")

    async def get_img_bytes(self, image_url: str) -> bytes | None:
        GITLAB_TOKEN = await self._get_api_token_()
        self.logger.info(f"Fetching image from URL: {image_url}")
        headers = {
            "Authorization": f"Bearer {GITLAB_TOKEN}",
            # "Private-Token": f"{GITLAB_TOKEN}",
            "Accept": "*/*",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True,timeout=30.0) as client:
                resp = await client.get(image_url, headers=headers)
                resp.raise_for_status()
                img_data = resp.content
                self.logger.info(f"Fetched image of size: {len(img_data)} bytes")
                return img_data
        except httpx.HTTPStatusError as e:
            self.logger.error(
                f"HTTP {e.response.status_code} fetching image from {image_url}"
            )
            return None
        except Exception as e:
            self.logger.error(f"Error fetching image from {image_url}: {e}")
            return None

    def _get_iso_time(self) -> str:
        # Get the current time in UTC
        utc_now = datetime.now(timezone.utc)
        # Format the time into the ISO 8601 string format with 'Z'
        return utc_now.strftime("%Y-%m-%dT%H:%M:%SZ")

    async def get_signed_url(self, record: Record) -> str | None:
        """Get signed URL for record access (optional - if API supports it)."""

        return None

    async def _log_rate_limit(self, label: str = "") -> None:
        """Log GitLab rate limit: remaining, limit, and reset time."""
        return

    async def parse_gitlab_uploads_clean_test(self,text:str)-> tuple[list[dict[str, Any]], str]:
        """
        Returns:
            "files": [...],
            "cleaned_markdown": "..." ,
        """
        IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
        UPLOAD_PATTERN = re.compile(
                r"""
                (?P<full>
                    (?:!\[.*?\]|\[.*?\])      # Image or link markdown
                    \(
                    (?P<href>
                        /uploads/
                        [a-f0-9]{32}/         # 32-char GitLab hash
                        (?P<filename>[^)\s]+) # filename
                    )
                    \)
                )
                """,
                re.VERBOSE | re.IGNORECASE,
            )
        if not isinstance(text, str):
            return {"files": [], "cleaned_markdown": ""}

        files = []
        cleaned_text = text

        matches = list(UPLOAD_PATTERN.finditer(text))

        for match in matches:
            full_match = match.group("full")
            href = match.group("href")
            filename = unquote(match.group("filename"))

            # Safety check for malformed filename
            if "." not in filename or filename.endswith("."):
                continue

            extension = filename.rsplit(".", 1)[-1].lower()

            # Ignore SVG explicitly
            if extension == "svg":
                cleaned_text = cleaned_text.replace(full_match, "")
                continue

            category = "image" if extension in IMAGE_EXTENSIONS else "attachment"

            files.append({
                "href": href,
                "filename": filename,
                "filetype": extension,
                "category": category
            })

            # Remove from markdown
            cleaned_text = cleaned_text.replace(full_match, "")

        # Remove extra blank lines caused by removal
        cleaned_text = re.sub(r'\n\s*\n+', '\n\n', cleaned_text).strip()

        return files,cleaned_text

    def get_parent_path_from_path(self,file_path:str)->list[str] | None:
        """Cleans and Removes file name form path and returns it"""
        if not file_path :
            return None
        file_path_dict = file_path.split("/")
        file_path_dict.pop()
        return file_path_dict

    async def handle_webhook_notification(self) -> bool:
        """Handle webhook notifications (optional - for real-time sync)."""
        return True

    def get_filter_options(self) -> None:
        return

    async def cleanup(self) -> None:
        """
        Cleanup resources used by the connector.
        """
        self.logger.info("Cleaning up GitLab connector resources.")
        self.data_source = None

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
    ) -> "BaseConnector":
        """
        Factory method to create a Gitlab connector instance.

        Args:
            logger: Logger instance
            data_store_provider: Data store provider for database operations
            config_service: Configuration service for accessing credentials

        Returns:
            Initialized GitLabConnector instance
        """
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()

        return GitLabConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
        )
