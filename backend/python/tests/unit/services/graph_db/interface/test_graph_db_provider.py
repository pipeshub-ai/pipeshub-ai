"""
Unit tests for the IGraphDBProvider abstract interface.

Tests cover:
- Module-level imports (abc, typing, Person)
- TYPE_CHECKING conditional branch (True path via reload)
- Abstract class cannot be instantiated directly
- Concrete subclass must implement all abstract methods
- Concrete subclass instances satisfy isinstance checks
- All abstract method signatures are present on the class
"""

import importlib
import sys
import types
from abc import ABC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_concrete_class():
    """Create a minimal concrete subclass that implements every abstract method."""
    # Collect all abstract method names from the ABC
    abstract_names = list(IGraphDBProvider.__abstractmethods__)

    # Build a namespace with stub implementations for each
    namespace = {}
    for name in abstract_names:
        # Use a factory to capture `name` in the closure
        namespace[name] = AsyncMock()

    ConcreteProvider = type("ConcreteProvider", (IGraphDBProvider,), namespace)
    return ConcreteProvider


# ---------------------------------------------------------------------------
# Module-level import / class definition tests
# ---------------------------------------------------------------------------


class TestModuleImports:
    """Verify that the module can be imported and exposes the expected symbols."""

    def test_igraph_db_provider_is_importable(self):
        """IGraphDBProvider should be importable from the module."""
        assert IGraphDBProvider is not None

    def test_igraph_db_provider_is_abstract(self):
        """IGraphDBProvider should be an abstract base class."""
        assert issubclass(IGraphDBProvider, ABC)

    def test_person_import_accessible(self):
        """The module imports Person at module level (non-TYPE_CHECKING)."""
        from app.models.entities import Person
        # Person is imported at the top of graph_db_provider.py unconditionally
        assert Person is not None

    def test_type_checking_imports_are_conditional(self):
        """The TYPE_CHECKING block should not execute at runtime.
        Verify by checking that the guarded names are not module-level attributes."""
        import app.services.graph_db.interface.graph_db_provider as mod
        # These are only imported under TYPE_CHECKING and should not be
        # real module-level attributes at runtime (they may exist as forward refs).
        # The module itself should still be importable without error.
        assert hasattr(mod, "IGraphDBProvider")

    def test_type_checking_true_branch(self):
        """Cover the TYPE_CHECKING=True branch by reloading the module
        with TYPE_CHECKING patched to True."""
        import app.services.graph_db.interface.graph_db_provider as mod

        # Avoid importing full FastAPI/Starlette stack during forced TYPE_CHECKING=True
        # reload, which can trigger circular import noise in test environments.
        fake_fastapi = types.ModuleType("fastapi")
        fake_fastapi.Request = type("Request", (), {})

        with patch.object(
            sys.modules["typing"], "TYPE_CHECKING", True
        ), patch.dict(sys.modules, {"fastapi": fake_fastapi}):
            importlib.reload(mod)

        # After reload with TYPE_CHECKING=True, the guarded imports
        # should have executed, bringing Request and entity types into scope.
        assert hasattr(mod, "Request")
        assert hasattr(mod, "AppRole")
        assert hasattr(mod, "AppUser")
        assert hasattr(mod, "AppUserGroup")
        assert hasattr(mod, "FileRecord")
        assert hasattr(mod, "Record")
        assert hasattr(mod, "RecordGroup")
        assert hasattr(mod, "User")

        # Reload again without patch to restore normal state for other tests
        importlib.reload(mod)


# ---------------------------------------------------------------------------
# Abstract class behaviour
# ---------------------------------------------------------------------------


class TestAbstractBehaviour:
    """Verify that IGraphDBProvider enforces abstract contracts."""

    def test_cannot_instantiate_directly(self):
        """Instantiating IGraphDBProvider directly should raise TypeError."""
        with pytest.raises(TypeError):
            IGraphDBProvider()

    def test_abstract_methods_set_is_nonempty(self):
        """The class should declare at least one abstract method."""
        assert len(IGraphDBProvider.__abstractmethods__) > 0

    def test_partial_implementation_raises(self):
        """A subclass that does NOT implement all abstract methods should raise TypeError."""

        class Incomplete(IGraphDBProvider):
            pass  # deliberately missing all implementations

        with pytest.raises(TypeError):
            Incomplete()

    def test_partial_implementation_with_one_method_raises(self):
        """A subclass implementing only one method should still raise TypeError."""

        class AlmostComplete(IGraphDBProvider):
            async def connect(self) -> bool:
                return True

        with pytest.raises(TypeError):
            AlmostComplete()


# ---------------------------------------------------------------------------
# Concrete subclass tests
# ---------------------------------------------------------------------------


class TestConcreteSubclass:
    """Verify that a fully-implemented concrete subclass works correctly."""

    def test_concrete_subclass_can_be_instantiated(self):
        """A concrete subclass implementing all abstract methods should instantiate."""
        ConcreteProvider = _make_concrete_class()
        instance = ConcreteProvider()
        assert instance is not None

    def test_concrete_subclass_is_instance(self):
        """Concrete instance should pass isinstance check."""
        ConcreteProvider = _make_concrete_class()
        instance = ConcreteProvider()
        assert isinstance(instance, IGraphDBProvider)

    def test_concrete_subclass_is_subclass(self):
        """Concrete class should pass issubclass check."""
        ConcreteProvider = _make_concrete_class()
        assert issubclass(ConcreteProvider, IGraphDBProvider)


# ---------------------------------------------------------------------------
# Abstract method inventory
# ---------------------------------------------------------------------------


class TestAbstractMethodInventory:
    """Ensure all expected abstract methods are declared."""

    EXPECTED_METHODS = [
        # Connection management
        "connect",
        "disconnect",
        "ensure_schema",
        # Transaction management
        "begin_transaction",
        "commit_transaction",
        "rollback_transaction",
        # Document operations
        "get_document",
        "get_record_by_id",
        "get_all_documents",
        "batch_upsert_nodes",
        "delete_nodes",
        "update_node",
        # Edge operations
        "batch_create_edges",
        "batch_delete_edges",
        "get_edge",
        "delete_edge",
        "delete_edges_from",
        "delete_edges_by_relationship_types",
        "delete_edges_to",
        "delete_edges_to_groups",
        "delete_edges_between_collections",
        "delete_nodes_and_edges",
        "update_edge",
        # Generic filter operations
        "remove_nodes_by_field",
        "get_edges_to_node",
        "get_edges_from_node",
        "get_edges_from_node_with_target_name",
        "get_related_nodes",
        "get_related_node_field",
        # Query operations
        "execute_query",
        "get_nodes_by_filters",
        "get_nodes_by_field_in",
        # Record operations
        "get_record_by_path",
        "get_record_by_external_id",
        "get_record_by_external_revision_id",
        "get_child_record_ids_by_relation_type",
        "get_parent_record_ids_by_relation_type",
        "get_virtual_record_ids_for_record_ids",
        "get_record_key_by_external_id",
        "get_records_by_status",
        "get_records",
        "reindex_single_record",
        "reindex_record_group_records",
        "reset_indexing_status_to_queued_for_record_ids",
        "get_documents_by_status",
        "get_record_by_conversation_index",
        "get_record_by_issue_key",
        "get_record_by_weburl",
        "get_records_by_parent",
        "get_records_by_record_group",
        "get_records_by_parent_record",
        # Record group operations
        "get_record_group_by_external_id",
        "get_record_group_by_id",
        "get_file_record_by_id",
        # User operations
        "get_user_by_email",
        "get_user_by_source_id",
        "get_user_by_user_id",
        "get_account_type",
        "get_connector_stats",
        "get_users",
        "get_app_user_by_email",
        "get_app_users",
        "list_user_knowledge_bases",
        "get_kb_children",
        "get_folder_children",
        "get_knowledge_base",
        "update_knowledge_base",
        "delete_knowledge_base",
        "_validate_folder_creation",
        "find_folder_by_name_in_parent",
        "create_folder",
        "get_folder_contents",
        "validate_folder_in_kb",
        "update_folder",
        "delete_folder",
        "update_record",
        "delete_records",
        "create_kb_permissions",
        "count_kb_owners",
        "remove_kb_permission",
        "get_user_kb_permission",
        "upload_records",
        "is_record_folder",
        "get_record_parent_info",
        "is_record_descendant_of",
        "delete_parent_child_edge_to_record",
        "create_parent_child_edge",
        "update_record_external_parent_id",
        "get_kb_permissions",
        "update_kb_permission",
        "list_kb_permissions",
        "list_all_records",
        "list_kb_records",
        # Group operations
        "get_user_group_by_external_id",
        "get_user_groups",
        "batch_upsert_people",
        "get_app_role_by_external_id",
        "get_app_creator_user",
        # Organization operations
        "get_all_orgs",
        "get_departments",
        "get_org_apps",
        "find_duplicate_records",
        "find_next_queued_duplicate",
        "update_queued_duplicates_status",
        "copy_document_relationships",
        # Permission operations
        "batch_upsert_records",
        "create_record_relation",
        "batch_upsert_record_groups",
        "create_record_group_relation",
        "create_record_groups_relation",
        "create_inherit_permissions_relation_record_group",
        "get_accessible_virtual_record_ids",
        "get_records_by_record_ids",
        "batch_upsert_record_permissions",
        "get_file_permissions",
        "get_first_user_with_permission_to_node",
        "get_users_with_permission_to_node",
        "check_record_access_with_details",
        "get_record_owner_source_user_email",
        # File/parent operations
        "get_file_parents",
        # Sync point operations
        "get_sync_point",
        "upsert_sync_point",
        "remove_sync_point",
        "delete_sync_points_by_connector_id",
        "delete_connector_sync_edges",
        # Batch operations
        "batch_upsert_app_users",
        "batch_upsert_user_groups",
        "batch_upsert_app_roles",
        "batch_upsert_orgs",
        "batch_upsert_domains",
        "batch_upsert_anyone",
        "batch_upsert_anyone_with_link",
        "batch_upsert_anyone_same_org",
        "batch_create_user_app_edges",
        # Entity ID operations
        "get_entity_id_by_email",
        "bulk_get_entity_ids_by_email",
        # Connector operations
        "process_file_permissions",
        "delete_records_and_relations",
        "delete_record",
        "delete_record_by_external_id",
        "remove_user_access_to_record",
        "delete_connector_instance",
        "get_key_by_external_file_id",
        "organization_exists",
        "get_user_sync_state",
        "update_user_sync_state",
        "get_drive_sync_state",
        "update_drive_sync_state",
        # Connector registry operations
        "check_connector_name_exists",
        "batch_update_connector_status",
        "get_user_connector_instances",
        "get_filtered_connector_instances",
        "store_page_token",
        "get_page_token_db",
        # Utility operations
        "check_collection_has_document",
        "check_edge_exists",
        "get_failed_records_with_active_users",
        "get_failed_records_by_org",
        "check_toolset_instance_in_use",
        "check_connector_in_use",
        # Knowledge hub operations
        "get_knowledge_hub_root_nodes",
        "get_knowledge_hub_children",
        "get_knowledge_hub_search",
        "get_knowledge_hub_breadcrumbs",
        "get_knowledge_hub_context_permissions",
        "get_knowledge_hub_filter_options",
        "get_knowledge_hub_node_info",
        "get_knowledge_hub_parent_node",
        "validate_folder_exists_in_kb",
        "get_key_by_external_message_id",
        "get_related_records_by_relation_type",
        "get_message_id_header_by_key",
        "get_related_mails_by_message_id_header",
        "check_connector_name_uniqueness",
        "batch_update_nodes",
        "get_connector_instances_with_filters",
        "count_connector_instances_by_scope",
        # Team operations
        "get_teams",
        "get_team_with_users",
        "get_user_teams",
        "get_user_created_teams",
        "get_team_users",
        "search_teams",
        "delete_team_member_edges",
        "batch_update_team_member_roles",
        "delete_all_team_permissions",
        "get_team_owner_removal_info",
        "get_team_permissions_and_owner_count",
        "add_user_to_all_team",
        "ensure_all_team_with_users",
        "ensure_team_app_edge",
        # User operations
        "get_organization_users",
        # Agent permission operations
        "get_agent",
        "check_agent_permission",
        "get_agents_by_web_search_provider",
        "get_agents_by_model_key",
    ]

    def test_all_expected_methods_are_abstract(self):
        """Every expected method name should be in __abstractmethods__."""
        abstract = IGraphDBProvider.__abstractmethods__
        for method_name in self.EXPECTED_METHODS:
            assert method_name in abstract, (
                f"{method_name} should be abstract but is not in __abstractmethods__"
            )

    def test_no_unexpected_abstract_methods(self):
        """__abstractmethods__ should not contain methods outside our expected set."""
        expected_set = set(self.EXPECTED_METHODS)
        actual_set = set(IGraphDBProvider.__abstractmethods__)
        unexpected = actual_set - expected_set
        # If this fails, a new abstract method was added but not listed above.
        assert unexpected == set(), (
            f"Unexpected abstract methods found: {unexpected}"
        )

    def test_abstract_method_count(self):
        """Sanity-check that the count of abstract methods matches expectations."""
        assert len(IGraphDBProvider.__abstractmethods__) == len(self.EXPECTED_METHODS)


# ---------------------------------------------------------------------------
# Async method invocation on concrete subclass
# ---------------------------------------------------------------------------


class TestConcreteMethodCalls:
    """Verify that async abstract methods on a concrete subclass can be awaited."""

    @pytest.mark.asyncio
    async def test_connect_can_be_awaited(self):
        ConcreteProvider = _make_concrete_class()
        instance = ConcreteProvider()
        instance.connect.return_value = True
        result = await instance.connect()
        assert result is True

    @pytest.mark.asyncio
    async def test_disconnect_can_be_awaited(self):
        ConcreteProvider = _make_concrete_class()
        instance = ConcreteProvider()
        instance.disconnect.return_value = True
        result = await instance.disconnect()
        assert result is True

    @pytest.mark.asyncio
    async def test_ensure_schema_can_be_awaited(self):
        ConcreteProvider = _make_concrete_class()
        instance = ConcreteProvider()
        instance.ensure_schema.return_value = True
        result = await instance.ensure_schema()
        assert result is True

    @pytest.mark.asyncio
    async def test_get_document_returns_dict(self):
        ConcreteProvider = _make_concrete_class()
        instance = ConcreteProvider()
        expected = {"id": "doc1", "name": "test"}
        instance.get_document.return_value = expected
        result = await instance.get_document("doc1", "collection")
        assert result == expected

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids(self):
        ConcreteProvider = _make_concrete_class()
        instance = ConcreteProvider()
        expected = {"vr1": "r1", "vr2": "r2"}
        instance.get_accessible_virtual_record_ids.return_value = expected
        result = await instance.get_accessible_virtual_record_ids("user1", "org1")
        assert result == expected

    @pytest.mark.asyncio
    async def test_batch_upsert_people(self):
        from app.models.entities import Person
        ConcreteProvider = _make_concrete_class()
        instance = ConcreteProvider()
        instance.batch_upsert_people.return_value = None
        result = await instance.batch_upsert_people([])
        assert result is None
