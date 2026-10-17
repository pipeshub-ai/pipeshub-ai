"""Per-connector permission model: declared in the decorator, denormalized onto
the App instance doc so the query service can route without importing connectors.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, PermissionModel
from app.connectors.core.registry.connector_builder import (
    ConnectorBuilder,
    ConnectorConfigBuilder,
)
from app.connectors.core.registry.connector_registry import ConnectorRegistry

# Connectors whose source has no per-record ACLs, so one connector-wide query
# answers for every user. gitlab and rss stay out: gitlab syncs per-project
# member ACLs, and rss still writes creator-only permissions the APP_LEVEL scan
# does not re-check. The personal GitHub and GitLab connectors qualify because
# the only USER_APP_RELATION either ever writes is the creator's, so reaching
# the app already implies being the one principal the per-user ACL would match.
# Adding a connector here is a permissions decision, not a performance one.
APP_LEVEL_CONNECTORS = [
    "app.connectors.sources.s3.connector",
    "app.connectors.sources.minio.connector",
    "app.connectors.sources.google_cloud_storage.connector",
    "app.connectors.sources.azure_blob.connector",
    "app.connectors.sources.azure_files.connector",
    "app.connectors.sources.web.connector",
    "app.connectors.sources.postgres.connector",
    "app.connectors.sources.mariadb.connector",
    "app.connectors.sources.snowflake.connector",
    "app.connectors.sources.local_fs.connector",
    "app.connectors.sources.github.connector",
    "app.connectors.sources.gitlab_personal.connector",
    # Personal-scope connectors: one credential, one principal, so reaching the
    # instance is reaching its records.
    "app.connectors.sources.atlassian.confluence_datacenter_personal.connector",
    "app.connectors.sources.atlassian.jira_cloud_personal.connector",
    "app.connectors.sources.atlassian.jira_data_center_personal.connector",
    "app.connectors.sources.dropbox_individual.connector",
    "app.connectors.sources.google.drive.individual.connector",
    "app.connectors.sources.google.gmail.individual.connector",
    "app.connectors.sources.microsoft.outlook_individual.connector",
    "app.connectors.sources.nextcloud.connector",
    "app.connectors.sources.notion_personal.connector",
    "app.connectors.sources.slack.individual.connector",
    "app.connectors.sources.rss.connector",
]

# (module, RecordGroup count) for connectors whose groups search may trust.
# Only groups records attach to directly matter: a flag on a container no
# record carries is never read, because recordGroupIds holds direct groups only.
RECORD_GROUP_LEVEL_SITES = [
    ("app/connectors/sources/github_teams/projects.py", 3),
    ("app/connectors/sources/gitlab/projects.py", 3),
    ("app/connectors/sources/google/gmail/team/connector.py", 1),
    ("app/connectors/sources/microsoft/outlook/connector.py", 2),
    ("app/connectors/sources/notion/connector.py", 2),
    ("app/connectors/sources/slack/team/connector.py", 2),
    ("app/connectors/sources/slack/individual/connector.py", 2),
]


class TestConfigBuilder:
    def test_defaults_to_record_level(self) -> None:
        config = ConnectorConfigBuilder().build()
        assert config["permissionModel"] == PermissionModel.RECORD_LEVEL.value

    def test_with_permission_model_sets_app_level(self) -> None:
        config = ConnectorConfigBuilder().with_permission_model(PermissionModel.APP_LEVEL).build()
        assert config["permissionModel"] == PermissionModel.APP_LEVEL.value

    def test_rejects_raw_strings(self) -> None:
        with pytest.raises(ValueError, match="PermissionModel"):
            ConnectorConfigBuilder().with_permission_model("APP_LEVEL")

    def test_rejects_group_level(self) -> None:
        """RECORD_GROUP_LEVEL describes a RecordGroup. On an app doc the query path
        reads it as "not APP_LEVEL" and silently means RECORD_LEVEL."""
        with pytest.raises(ValueError, match="RecordGroup"):
            ConnectorConfigBuilder().with_permission_model(PermissionModel.RECORD_GROUP_LEVEL)


class TestConnectorBuilderPassthrough:
    def test_rejects_group_level(self) -> None:
        with pytest.raises(ValueError, match="RecordGroup"):
            ConnectorBuilder("Example").with_permission_model(PermissionModel.RECORD_GROUP_LEVEL)

    def test_survives_a_later_configure_call(self) -> None:
        """`configure()` swaps the config builder, so the flag is applied at build time."""
        decorator = (
            ConnectorBuilder("Example")
            .with_supported_auth_types("NONE")
            .with_permission_model(PermissionModel.APP_LEVEL)
            .configure(lambda c: c.with_icon("/icons/x.svg"))
            .build_decorator()
        )

        @decorator
        class _Example:
            pass

        config = _Example._connector_metadata["config"]
        assert config["permissionModel"] == PermissionModel.APP_LEVEL.value
        assert config["iconPath"] == "/icons/x.svg"

    def test_undeclared_connector_is_record_level(self) -> None:
        decorator = (
            ConnectorBuilder("Example2").with_supported_auth_types("NONE").build_decorator()
        )

        @decorator
        class _Example2:
            pass

        assert (
            _Example2._connector_metadata["config"]["permissionModel"]
            == PermissionModel.RECORD_LEVEL.value
        )

    def test_rejects_raw_strings(self) -> None:
        with pytest.raises(ValueError, match="PermissionModel"):
            ConnectorBuilder("Example3").with_permission_model("APP_LEVEL")


class TestDeclaredConnectors:
    @pytest.mark.parametrize("module_path", APP_LEVEL_CONNECTORS)
    def test_declares_app_level(self, module_path) -> None:
        import importlib

        module = importlib.import_module(module_path)
        models = {
            getattr(obj, "_connector_metadata")["config"].get("permissionModel")
            for obj in vars(module).values()
            if isinstance(getattr(obj, "_connector_metadata", None), dict)
        }
        assert PermissionModel.APP_LEVEL.value in models, f"{module_path} must declare APP_LEVEL"

    @pytest.mark.parametrize(
        "module_path",
        [
            "app.connectors.sources.google.drive.team.connector",
            "app.connectors.sources.microsoft.sharepoint_online.connector",
        ],
    )
    def test_acl_connectors_stay_record_level(self, module_path) -> None:
        """Sources with real per-record ACLs must not be cached user-independently."""
        import importlib

        module = importlib.import_module(module_path)
        models = {
            getattr(obj, "_connector_metadata")["config"].get("permissionModel")
            for obj in vars(module).values()
            if isinstance(getattr(obj, "_connector_metadata", None), dict)
        }
        assert models, f"no connector metadata found in {module_path}"
        assert models == {PermissionModel.RECORD_LEVEL.value}


def _registry() -> ConnectorRegistry:
    container = MagicMock()
    container.logger.return_value = MagicMock()
    return ConnectorRegistry(container)


class TestRecordGroupLevelSites:
    """A dropped declaration is silent — search keeps working, just slower, so
    nothing else would catch it."""

    @pytest.mark.parametrize("path,expected", RECORD_GROUP_LEVEL_SITES)
    def test_declared_on_every_expected_group(self, path, expected) -> None:
        import pathlib

        src = pathlib.Path(path).read_text(encoding="utf-8")
        found = src.count("permission_model=PermissionModel.RECORD_GROUP_LEVEL")
        assert found == expected, f"{path}: {found} sites, expected {expected}"

    def test_the_connector_builder_still_rejects_it(self) -> None:
        """RECORD_GROUP_LEVEL describes a RecordGroup. Letting it onto an app doc
        would make the query path read it as "not APP_LEVEL" and treat it as
        RECORD_LEVEL."""
        with pytest.raises(ValueError):
            ConnectorConfigBuilder().with_permission_model(
                PermissionModel.RECORD_GROUP_LEVEL
            )


class TestPersistAndBackfill:
    def test_permission_model_for_reads_config(self) -> None:
        assert ConnectorRegistry._permission_model_for(
            {"config": {"permissionModel": PermissionModel.APP_LEVEL.value}}
        ) == PermissionModel.APP_LEVEL.value

    def test_permission_model_for_defaults_safely(self) -> None:
        assert ConnectorRegistry._permission_model_for({}) == PermissionModel.RECORD_LEVEL.value
        assert ConnectorRegistry._permission_model_for({"config": {}}) == PermissionModel.RECORD_LEVEL.value

    @pytest.mark.asyncio
    async def test_new_instance_carries_the_flag(self) -> None:
        registry = _registry()
        graph = MagicMock()
        graph.get_document = AsyncMock(return_value={"_key": "org-1"})
        graph.batch_upsert_nodes = AsyncMock(return_value=True)
        graph.batch_create_edges = AsyncMock(return_value=True)
        registry._graph_provider = graph
        registry._check_name_uniqueness = AsyncMock(return_value=True)

        doc = await registry._create_connector_instance(
            connector_type="S3",
            instance_name="my-s3",
            metadata={
                "appGroup": "S3",
                "supportedAuthTypes": ["ACCESS_KEY"],
                "config": {"permissionModel": PermissionModel.APP_LEVEL.value},
            },
            scope="team",
            created_by="user-1",
            org_id="org-1",
            selected_auth_type="ACCESS_KEY",
        )

        assert doc["permissionModel"] == PermissionModel.APP_LEVEL.value
        upserted = graph.batch_upsert_nodes.await_args.args[0][0]
        assert upserted["permissionModel"] == PermissionModel.APP_LEVEL.value

    @pytest.mark.asyncio
    async def test_backfills_missing_and_stale_flags(self) -> None:
        registry = _registry()
        registry._connectors = {
            "S3": {"config": {"permissionModel": PermissionModel.APP_LEVEL.value}},
            "DRIVE": {"config": {"permissionModel": PermissionModel.RECORD_LEVEL.value}},
        }
        graph = MagicMock()
        graph.get_all_documents = AsyncMock(
            return_value=[
                {"_key": "a", "type": "S3", "isActive": True},  # missing flag
                {
                    "_key": "b",
                    "type": "S3",
                    "isActive": True,
                    "permissionModel": PermissionModel.RECORD_LEVEL.value,  # stale
                },
                {
                    "_key": "c",
                    "type": "DRIVE",
                    "isActive": True,
                    "permissionModel": PermissionModel.RECORD_LEVEL.value,  # current
                },
                {"_key": "kb", "type": Connectors.KNOWLEDGE_BASE.value, "isActive": True},
            ]
        )
        graph.update_node = AsyncMock(return_value=True)
        graph.batch_update_connector_status = AsyncMock(return_value=0)
        registry._graph_provider = graph

        assert await registry.sync_with_database() is True

        updated = {
            call.args[0]: call.args[2]["permissionModel"]
            for call in graph.update_node.await_args_list
        }
        assert updated == {"a": PermissionModel.APP_LEVEL.value, "b": PermissionModel.APP_LEVEL.value}

    @pytest.mark.asyncio
    async def test_requests_root_membership_once_for_root_scoped_connectors(self) -> None:
        """Slack search matches on a record's root group, and instances synced
        before that field existed have none. Marking them un-backfilled hands
        them to the membership backfill and keeps search on record ids until it
        finishes, instead of quietly dropping every threaded record."""
        registry = _registry()
        registry._connectors = {
            "SLACK": {"config": {"permissionModel": PermissionModel.RECORD_LEVEL.value}},
            "DRIVE": {"config": {"permissionModel": PermissionModel.RECORD_LEVEL.value}},
        }
        graph = MagicMock()
        graph.get_all_documents = AsyncMock(
            return_value=[
                {"_key": "slack-old", "type": "SLACK", "isActive": True,
                 "permissionModel": PermissionModel.RECORD_LEVEL.value},
                {"_key": "slack-done", "type": "SLACK", "isActive": True,
                 "permissionModel": PermissionModel.RECORD_LEVEL.value,
                 "rootMembershipRequested": True},
                {"_key": "drive", "type": "DRIVE", "isActive": True,
                 "permissionModel": PermissionModel.RECORD_LEVEL.value},
            ]
        )
        graph.update_node = AsyncMock(return_value=True)
        graph.batch_update_connector_status = AsyncMock(return_value=0)
        registry._graph_provider = graph

        assert await registry.sync_with_database() is True

        requested = {
            call.args[0]: call.args[2]
            for call in graph.update_node.await_args_list
            if "rootMembershipRequested" in call.args[2]
        }
        assert set(requested) == {"slack-old"}
        assert requested["slack-old"] == {
            "vectorMembershipBackfilled": False,
            "rootMembershipRequested": True,
        }

    @pytest.mark.asyncio
    async def test_a_new_instance_is_not_asked_to_rescan(self) -> None:
        """An instance created after this change syncs roots itself, so asking
        it to rescan would re-walk the whole connector for nothing."""
        registry = _registry()
        registry._connectors = {
            "SLACK": {"config": {"permissionModel": PermissionModel.RECORD_LEVEL.value}}
        }
        graph = MagicMock()
        graph.get_all_documents = AsyncMock(
            return_value=[{"_key": "slack-new", "type": "SLACK", "isActive": True,
                           "permissionModel": PermissionModel.RECORD_LEVEL.value,
                           "rootMembershipRequested": True}]
        )
        graph.update_node = AsyncMock(return_value=True)
        graph.batch_update_connector_status = AsyncMock(return_value=0)
        registry._graph_provider = graph

        assert await registry.sync_with_database() is True

        assert not [
            c for c in graph.update_node.await_args_list
            if "rootMembershipRequested" in c.args[2]
        ]

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_only_root_scoped_types_are_born_marked(self) -> None:
        """A new instance is marked only when its type is root-scoped today.
        Marking every connector would mean a type joining
        ROOT_SCOPED_CONNECTOR_TYPES later finds its existing instances already
        marked, so the reconciliation skips them while their points still carry
        no roots — and their records stop matching with nothing to notice it."""
        from app.connectors.core.constants import ConnectorStateKeys
        key = ConnectorStateKeys.ROOT_MEMBERSHIP_REQUESTED

        async def created_doc(connector_type: str) -> dict:
            registry = _registry()
            meta = {
                "appGroup": "g", "appGroupId": "gid", "authType": "NONE",
                "supportedAuthTypes": ["NONE"], "scope": ["personal"],
                "config": {"permissionModel": PermissionModel.RECORD_LEVEL.value},
            }
            registry._connectors = {connector_type: meta}
            graph = MagicMock()
            graph.batch_upsert_nodes = AsyncMock(return_value=[{"_key": "k"}])
            graph.batch_create_edges = AsyncMock(return_value=[{"_key": "e"}])
            graph.get_document = AsyncMock(return_value={"_key": "o"})
            registry._graph_provider = graph
            registry._check_name_uniqueness = AsyncMock(return_value=True)
            await registry._create_connector_instance(
                connector_type=connector_type, instance_name="i", metadata=meta,
                scope="personal", created_by="u", org_id="o",
                selected_auth_type="NONE",
            )
            assert graph.batch_upsert_nodes.await_args is not None, "instance was never written"
            return graph.batch_upsert_nodes.await_args.args[0][0]

        assert (await created_doc("Slack")).get(key) is True, (
            "a root-scoped type must be born marked, or it takes a pointless rescan"
        )
        assert key not in (await created_doc("Drive")), (
            "a non-root-scoped type must be born unmarked, so it stays eligible "
            "for the rescan if its type becomes root-scoped later"
        )

    async def test_root_membership_request_failure_does_not_fail_sync(self) -> None:
        registry = _registry()
        registry._connectors = {
            "SLACK": {"config": {"permissionModel": PermissionModel.RECORD_LEVEL.value}}
        }
        graph = MagicMock()
        graph.get_all_documents = AsyncMock(
            return_value=[{"_key": "slack-old", "type": "SLACK", "isActive": True,
                           "permissionModel": PermissionModel.RECORD_LEVEL.value}]
        )
        graph.update_node = AsyncMock(side_effect=RuntimeError("graph down"))
        graph.batch_update_connector_status = AsyncMock(return_value=0)
        registry._graph_provider = graph

        assert await registry.sync_with_database() is True

    @pytest.mark.asyncio
    async def test_backfill_failure_does_not_fail_sync(self) -> None:
        registry = _registry()
        registry._connectors = {"S3": {"config": {"permissionModel": PermissionModel.APP_LEVEL.value}}}
        graph = MagicMock()
        graph.get_all_documents = AsyncMock(
            return_value=[{"_key": "a", "type": "S3", "isActive": True}]
        )
        graph.update_node = AsyncMock(side_effect=RuntimeError("graph down"))
        graph.batch_update_connector_status = AsyncMock(return_value=0)
        registry._graph_provider = graph

        assert await registry.sync_with_database() is True

    @pytest.mark.asyncio
    async def test_unregistered_types_are_left_alone(self) -> None:
        registry = _registry()
        registry._connectors = {}
        graph = MagicMock()
        graph.get_all_documents = AsyncMock(
            return_value=[{"_key": "a", "type": "RETIRED", "isActive": False}]
        )
        graph.update_node = AsyncMock()
        graph.batch_update_connector_status = AsyncMock(return_value=0)
        registry._graph_provider = graph

        assert await registry.sync_with_database() is True
        graph.update_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_kb_documents_are_skipped(self) -> None:
        registry = _registry()
        registry._connectors = {
            Connectors.KNOWLEDGE_BASE.value: {"config": {}},
        }
        graph = MagicMock()
        graph.get_all_documents = AsyncMock(
            return_value=[{"_key": "kb", "type": Connectors.KNOWLEDGE_BASE.value, "isActive": True}]
        )
        graph.update_node = AsyncMock()
        graph.batch_update_connector_status = AsyncMock(return_value=0)
        registry._graph_provider = graph

        assert await registry.sync_with_database() is True
        graph.update_node.assert_not_called()
        assert registry._collection_name == CollectionNames.APPS.value
