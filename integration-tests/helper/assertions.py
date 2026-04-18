"""
Generic Assertions Framework

Reusable assertion utilities for all connector integration tests.
Tests import RecordType, RecordGroupType, etc. from backend directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from app.models.entities import AppUserGroup, Record, RecordType, User

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol

logger = logging.getLogger("test-assertions")


@dataclass
class RecordAssertion:
    """
    Expected properties for a record.
    
    All fields are optional - only specified fields will be validated.
    Tests pass RecordType enum values directly (e.g., RecordType.CONFLUENCE_PAGE.value).
    
    Example:
        from app.models.entities import RecordType
        
        expected = RecordAssertion(
            external_record_id="12345",
            record_type=RecordType.CONFLUENCE_PAGE.value,
            mime_type="text/html",
            record_name="My Page",
        )
    """
    external_record_id: Optional[str] = None
    record_name: Optional[str] = None
    record_type: Optional[str] = None
    mime_type: Optional[str] = None
    parent_external_record_id: Optional[str] = None
    external_record_group_id: Optional[str] = None
    external_revision_id: Optional[str] = None
    is_dependent_node: Optional[bool] = None
    custom_matchers: Optional[Dict[str, Callable[[Any], bool]]] = None


# RecordAssertion dataclass field names matching Record model attributes (snake_case)
_RECORD_ASSERTION_ATTRS: tuple[str, ...] = (
    "external_record_id",
    "record_name",
    "record_type",
    "mime_type",
    "parent_external_record_id",
    "external_record_group_id",
    "external_revision_id",
    "is_dependent_node",
)


def _assert_record_field_equals(record: Record, attr: str, expected_val: Any) -> None:
    raw = getattr(record, attr)
    if attr == "record_type" and isinstance(raw, RecordType):
        actual = raw.value
    else:
        actual = raw
    assert actual == expected_val, (
        f"Expected {attr}={expected_val!r}, got {actual!r}"
    )


def _record_value_for_custom_field(record: Record, field: str) -> Any:
    """Resolve custom matcher field by Arango alias or Python attribute name."""
    by_alias = record.model_dump(by_alias=True)
    if field in by_alias:
        return by_alias[field]
    return getattr(record, field, None)


class ConnectorAssertions:
    """
    Generic assertion utilities for ALL connectors.
    
    This class works with any connector type (Confluence, S3, GCS, Jira, etc.).
    Tests import RecordType, RecordGroupType, etc. from backend to avoid duplication.
    
    Usage:
        from helper.assertions import ConnectorAssertions, RecordAssertion
        from app.models.entities import RecordType
        
        assertions = ConnectorAssertions(graph_provider)
        
        # Validate page properties
        expected = RecordAssertion(
            external_record_id=page_id,
            record_type=RecordType.CONFLUENCE_PAGE.value,
            record_name="My Page",
        )
        record = await assertions.assert_record_exists(connector_id, page_id, expected)
    """
    
    def __init__(self, graph_provider: "GraphProviderProtocol"):
        """
        Initialize assertion helper.
        
        Args:
            graph_provider: Graph provider instance (TestNeo4jProvider or TestArangoHTTPProvider)
        """
        self.graph = graph_provider
    
    # =========================================================================
    # Record Assertions
    # =========================================================================
    
    async def assert_record_exists(
        self,
        connector_id: str,
        external_record_id: str,
        expected: RecordAssertion | None = None,
    ) -> Record:
        """
        Assert record exists and optionally validate properties.
        
        Args:
            connector_id: Connector ID
            external_record_id: External record ID from source system
            expected: Optional RecordAssertion with expected property values
        
        Returns:
            Record from graph
        
        Raises:
            AssertionError: If record not found or properties don't match
        
        Example:
            from app.models.entities import RecordType
            
            expected = RecordAssertion(
                external_record_id="123",
                record_type=RecordType.CONFLUENCE_PAGE.value,
                mime_type="text/html",
            )
            record = await assertions.assert_record_exists(connector_id, "123", expected)
        """
        record = await self.graph.get_record_by_external_id(connector_id, external_record_id)
        assert record is not None, (
            f"Record with externalRecordId='{external_record_id}' not found "
            f"for connector {connector_id}"
        )
        
        if expected:
            self._validate_record_properties(record, expected)
        
        return record
    
    def _validate_record_properties(self, record: Record, expected: RecordAssertion) -> None:
        """Validate record properties against expected values."""
        for attr in _RECORD_ASSERTION_ATTRS:
            expected_val = getattr(expected, attr)
            if expected_val is not None:
                _assert_record_field_equals(record, attr, expected_val)

        if expected.custom_matchers:
            for field, matcher in expected.custom_matchers.items():
                value = _record_value_for_custom_field(record, field)
                assert matcher(value), (
                    f"Custom matcher failed for field '{field}' with value '{value}'"
                )
    
    async def assert_record_not_exists(
        self, connector_id: str, external_record_id: str
    ) -> None:
        """
        Assert record does NOT exist.
        
        Args:
            connector_id: Connector ID
            external_record_id: External record ID from source system
        
        Raises:
            AssertionError: If record exists
        """
        record = await self.graph.get_record_by_external_id(connector_id, external_record_id)
        assert record is None, (
            f"Record with externalRecordId='{external_record_id}' should not exist "
            f"for connector {connector_id}, but was found"
        )
    
    async def assert_record_updated(
        self,
        connector_id: str,
        external_record_id: str,
        previous_revision: str | int,
    ) -> Record:
        """
        Assert record version/revision has changed.
        
        Args:
            connector_id: Connector ID
            external_record_id: External record ID
            previous_revision: Previous externalRevisionId or version
        
        Returns:
            Updated Record
        
        Raises:
            AssertionError: If version hasn't changed
        """
        record = await self.graph.get_record_by_external_id(connector_id, external_record_id)
        assert record is not None, f"Record {external_record_id} not found"
        
        current_revision = record.external_revision_id or record.version
        assert str(current_revision) != str(previous_revision), (
            f"Expected version change from {previous_revision}, "
            f"but version is still {current_revision}"
        )
        
        return record
    
    # =========================================================================
    # User Assertions
    # =========================================================================
    
    async def assert_user_exists(
        self,
        connector_id: str,
        source_user_id: str,
        email: str | None = None,
    ) -> User:
        """
        Assert user synced with correct properties.
        
        Args:
            connector_id: Connector ID
            source_user_id: User ID from source system (e.g., Confluence accountId)
            email: Expected email address (optional)
        
        Returns:
            ``User`` from graph (Neo4j and Arango providers both return this model).
        
        Raises:
            AssertionError: If user not found or properties don't match
        """
        user = await self.graph.get_user_by_source_id(source_user_id, connector_id)
        assert user is not None, (
            f"User with sourceUserId='{source_user_id}' not found for connector {connector_id}"
        )
        
        if email is not None:
            assert user.email == email, (
                f"Expected email='{email}', got '{user.email}'"
            )
        
        return user
    
    async def assert_user_not_exists(
        self, connector_id: str, source_user_id: str
    ) -> None:
        """
        Assert user does NOT exist.
        
        Args:
            connector_id: Connector ID
            source_user_id: User ID from source system
        
        Raises:
            AssertionError: If user exists
        """
        user = await self.graph.get_user_by_source_id(source_user_id, connector_id)
        assert user is None, (
            f"User with sourceUserId='{source_user_id}' should not exist "
            f"for connector {connector_id}, but was found"
        )
    
    # =========================================================================
    # Group Assertions
    # =========================================================================
    
    async def assert_group_exists(
        self,
        connector_id: str,
        external_group_id: str,
        name: str | None = None,
        min_members: int | None = None,
    ) -> AppUserGroup:
        """
        Assert group synced with correct properties.
        
        Args:
            connector_id: Connector ID
            external_group_id: Group ID from source system
            name: Expected group name (optional)
            min_members: Minimum expected member count (optional)
        
        Returns:
            ``AppUserGroup`` from graph (Neo4j and Arango providers both return this model).
        
        Raises:
            AssertionError: If group not found, name doesn't match, or member count too low
        """
        group = await self.graph.get_user_group_by_external_id(connector_id, external_group_id)
        assert group is not None, (
            f"Group with externalGroupId='{external_group_id}' not found "
            f"for connector {connector_id}"
        )
        
        if name is not None:
            assert group.name == name, (
                f"Expected group name='{name}', got '{group.name}'"
            )
        
        if min_members is not None:
            member_count = await self.graph.count_group_members(connector_id, external_group_id)
            assert member_count >= min_members, (
                f"Expected at least {min_members} group members, got {member_count}"
            )
        
        return group
    
    async def assert_group_not_exists(
        self, connector_id: str, external_group_id: str
    ) -> None:
        """
        Assert group does NOT exist.
        
        Args:
            connector_id: Connector ID
            external_group_id: Group ID from source system
        
        Raises:
            AssertionError: If group exists
        """
        group = await self.graph.get_user_group_by_external_id(connector_id, external_group_id)
        assert group is None, (
            f"Group with externalGroupId='{external_group_id}' should not exist "
            f"for connector {connector_id}, but was found"
        )
    
    # =========================================================================
    # Permission Assertions
    # =========================================================================
    
    async def assert_inherits_permissions(
        self,
        connector_id: str,
        external_record_id: str,
        inherits: bool = True,
    ) -> None:
        """
        Assert record permission inheritance state.
        
        Args:
            connector_id: Connector ID
            external_record_id: External record ID
            inherits: Expected inheritance state (True = has INHERIT_PERMISSIONS edge)
        
        Raises:
            AssertionError: If inheritance state doesn't match expected
        """
        actual = await self.graph.record_inherits_permissions(connector_id, external_record_id)
        assert actual == inherits, (
            f"Expected record {external_record_id} inherit_permissions={inherits}, "
            f"got {actual}"
        )
    
    # =========================================================================
    # Batch Assertions
    # =========================================================================
    
    async def assert_all_records_have_property(
        self,
        connector_id: str,
        property_name: str,
        expected_value: Any = None,
        predicate: Callable[[Any], bool] | None = None,
    ) -> int:
        """
        Assert all records have a property (optionally matching value/predicate).
        
        Args:
            connector_id: Connector ID
            property_name: Property name to check (e.g., "mimeType", "connectorId")
            expected_value: Expected property value (optional)
            predicate: Custom validation function (optional)
        
        Returns:
            Number of records validated
        
        Raises:
            AssertionError: If any record missing property or validation fails
        
        Example:
            # Assert all records have connectorId
            count = await assertions.assert_all_records_have_property(
                connector_id, "connectorId", expected_value=connector_id
            )
            
            # Assert all records have non-null webUrl
            count = await assertions.assert_all_records_have_property(
                connector_id, "webUrl", predicate=lambda v: v is not None
            )
        """
        records = await self.graph.fetch_records_by_type(connector_id, "")
        
        if not records:
            logger.info(
                "assert_all_records_have_property: no records for connector %s",
                connector_id,
            )
            return 0
        
        validated_count = 0
        for record in records:
            record_id = record.get("_key") or record.get("externalRecordId", "unknown")
            value = record.get(property_name)
            
            if expected_value is not None:
                assert value == expected_value, (
                    f"Record {record_id}: Expected {property_name}='{expected_value}', "
                    f"got '{value}'"
                )
            elif predicate is not None:
                assert predicate(value), (
                    f"Record {record_id}: Predicate failed for {property_name}='{value}'"
                )
            else:
                assert value is not None, (
                    f"Record {record_id}: Missing required property '{property_name}'"
                )
            
            validated_count += 1
        
        logger.info(
            "✅ Validated property '%s' for %d records (connector %s)",
            property_name, validated_count, connector_id
        )
        return validated_count
