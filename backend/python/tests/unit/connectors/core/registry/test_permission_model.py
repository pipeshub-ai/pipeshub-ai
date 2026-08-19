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
# answers for every user. gitlab, gitlab_personal and rss were removed after
# review: gitlab syncs per-project member ACLs, and the two personal-scope
# connectors write creator-only permissions that the APP_LEVEL scan does not
# re-check. Adding a connector here is a permissions decision, not a
# performance one.
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


class TestConnectorBuilderPassthrough:
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
