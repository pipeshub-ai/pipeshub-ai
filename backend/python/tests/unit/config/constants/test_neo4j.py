"""
Comprehensive tests for app.config.constants.neo4j

Covers:
- Neo4jLabel enum (all members and values)
- Neo4jRelationshipType enum (all members and values)
- COLLECTION_TO_LABEL mapping (all entries, types)
- EDGE_COLLECTION_TO_RELATIONSHIP mapping (all entries, types)
- collection_to_label() function (known keys + fallback)
- edge_collection_to_relationship() function (known keys + fallback)
- parse_node_id() function (with slash, without slash)
- build_node_id() function
"""

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import (
    COLLECTION_TO_LABEL,
    EDGE_COLLECTION_TO_RELATIONSHIP,
    Neo4jLabel,
    Neo4jRelationshipType,
    build_node_id,
    collection_to_label,
    edge_collection_to_relationship,
    parse_node_id,
)


# ---------------------------------------------------------------------------
# Neo4jLabel enum
# ---------------------------------------------------------------------------

class TestNeo4jLabel:
    def test_records_label(self):
        assert Neo4jLabel.RECORDS.value == "Record"

    def test_record_groups_label(self):
        assert Neo4jLabel.RECORD_GROUPS.value == "RecordGroup"

    def test_sync_points_label(self):
        assert Neo4jLabel.SYNC_POINTS.value == "SyncPoint"

    def test_record_type_labels(self):
        assert Neo4jLabel.FILES.value == "File"
        assert Neo4jLabel.MAILS.value == "Mail"
        assert Neo4jLabel.WEBPAGES.value == "Webpage"
        assert Neo4jLabel.COMMENTS.value == "Comment"
        assert Neo4jLabel.TICKETS.value == "Ticket"
        assert Neo4jLabel.LINKS.value == "Link"
        assert Neo4jLabel.PROJECTS.value == "Project"

    def test_user_labels(self):
        assert Neo4jLabel.USERS.value == "User"
        assert Neo4jLabel.GROUPS.value == "Group"
        assert Neo4jLabel.PEOPLE.value == "Person"
        assert Neo4jLabel.ROLES.value == "Role"
        assert Neo4jLabel.ORGS.value == "Organization"
        assert Neo4jLabel.ANYONE.value == "Anyone"
        assert Neo4jLabel.ANYONE_WITH_LINK.value == "AnyoneWithLink"
        assert Neo4jLabel.ANYONE_SAME_ORG.value == "AnyoneSameOrg"

    def test_app_labels(self):
        assert Neo4jLabel.APPS.value == "App"
        assert Neo4jLabel.DRIVES.value == "Drive"

    def test_other_labels(self):
        assert Neo4jLabel.PAGE_TOKENS.value == "PageToken"
        assert Neo4jLabel.BLOCKS.value == "Block"

    def test_tools_labels(self):
        assert Neo4jLabel.TOOLS.value == "Tool"
        assert Neo4jLabel.TOOLS_CTAGS.value == "ToolCtag"

    def test_metadata_labels(self):
        assert Neo4jLabel.DEPARTMENTS.value == "Departments"
        assert Neo4jLabel.CATEGORIES.value == "Categories"
        assert Neo4jLabel.SUBCATEGORIES1.value == "Subcategories1"
        assert Neo4jLabel.SUBCATEGORIES2.value == "Subcategories2"
        assert Neo4jLabel.SUBCATEGORIES3.value == "Subcategories3"
        assert Neo4jLabel.LANGUAGES.value == "Languages"
        assert Neo4jLabel.TOPICS.value == "Topics"

    def test_teams_label(self):
        assert Neo4jLabel.TEAMS.value == "Teams"

    def test_agent_builder_labels(self):
        assert Neo4jLabel.AGENT_TEMPLATES.value == "AgentTemplate"
        assert Neo4jLabel.AGENT_INSTANCES.value == "AgentInstance"
        assert Neo4jLabel.AGENT_KNOWLEDGE.value == "AgentKnowledge"
        assert Neo4jLabel.AGENT_TOOLSETS.value == "AgentToolset"
        assert Neo4jLabel.AGENT_TOOLS.value == "AgentTool"

    def test_total_member_count(self):
        assert len(Neo4jLabel) == 37


# ---------------------------------------------------------------------------
# Neo4jRelationshipType enum
# ---------------------------------------------------------------------------

class TestNeo4jRelationshipType:
    def test_core_relationships(self):
        assert Neo4jRelationshipType.RECORD_RELATIONS.value == "RECORD_RELATION"
        assert Neo4jRelationshipType.BELONGS_TO.value == "BELONGS_TO"
        assert Neo4jRelationshipType.IS_OF_TYPE.value == "IS_OF_TYPE"
        assert Neo4jRelationshipType.PERMISSION.value == "PERMISSION"
        assert Neo4jRelationshipType.INHERIT_PERMISSIONS.value == "INHERIT_PERMISSIONS"
        assert Neo4jRelationshipType.USER_APP_RELATION.value == "USER_APP_RELATION"
        assert Neo4jRelationshipType.ORG_APP_RELATION.value == "ORG_APP_RELATION"
        assert Neo4jRelationshipType.USER_DRIVE_RELATION.value == "USER_DRIVE_RELATION"

    def test_metadata_relationships(self):
        assert Neo4jRelationshipType.BELONGS_TO_DEPARTMENT.value == "BELONGS_TO_DEPARTMENT"
        assert Neo4jRelationshipType.BELONGS_TO_CATEGORY.value == "BELONGS_TO_CATEGORY"
        assert Neo4jRelationshipType.BELONGS_TO_LANGUAGE.value == "BELONGS_TO_LANGUAGE"
        assert Neo4jRelationshipType.BELONGS_TO_TOPIC.value == "BELONGS_TO_TOPIC"

    def test_agent_relationships(self):
        assert Neo4jRelationshipType.AGENT_HAS_KNOWLEDGE.value == "AGENT_HAS_KNOWLEDGE"
        assert Neo4jRelationshipType.AGENT_HAS_TOOLSET.value == "AGENT_HAS_TOOLSET"
        assert Neo4jRelationshipType.TOOLSET_HAS_TOOL.value == "TOOLSET_HAS_TOOL"

    def test_total_member_count(self):
        assert len(Neo4jRelationshipType) == 15


# ---------------------------------------------------------------------------
# COLLECTION_TO_LABEL mapping
# ---------------------------------------------------------------------------

class TestCollectionToLabelMapping:
    def test_all_arango_collection_entries(self):
        """Verify that every key in COLLECTION_TO_LABEL maps to the expected Neo4j label."""
        expected_pairs = [
            (CollectionNames.RECORDS.value, Neo4jLabel.RECORDS.value),
            (CollectionNames.RECORD_GROUPS.value, Neo4jLabel.RECORD_GROUPS.value),
            (CollectionNames.SYNC_POINTS.value, Neo4jLabel.SYNC_POINTS.value),
            (CollectionNames.FILES.value, Neo4jLabel.FILES.value),
            (CollectionNames.MAILS.value, Neo4jLabel.MAILS.value),
            (CollectionNames.WEBPAGES.value, Neo4jLabel.WEBPAGES.value),
            (CollectionNames.COMMENTS.value, Neo4jLabel.COMMENTS.value),
            (CollectionNames.TICKETS.value, Neo4jLabel.TICKETS.value),
            (CollectionNames.LINKS.value, Neo4jLabel.LINKS.value),
            (CollectionNames.PROJECTS.value, Neo4jLabel.PROJECTS.value),
            (CollectionNames.USERS.value, Neo4jLabel.USERS.value),
            (CollectionNames.GROUPS.value, Neo4jLabel.GROUPS.value),
            (CollectionNames.PEOPLE.value, Neo4jLabel.PEOPLE.value),
            (CollectionNames.ROLES.value, Neo4jLabel.ROLES.value),
            (CollectionNames.ORGS.value, Neo4jLabel.ORGS.value),
            (CollectionNames.ANYONE.value, Neo4jLabel.ANYONE.value),
            (CollectionNames.APPS.value, Neo4jLabel.APPS.value),
            (CollectionNames.DRIVES.value, Neo4jLabel.DRIVES.value),
            (CollectionNames.PAGE_TOKENS.value, Neo4jLabel.PAGE_TOKENS.value),
            (CollectionNames.BLOCKS.value, Neo4jLabel.BLOCKS.value),
            ("tools", Neo4jLabel.TOOLS.value),
            ("tools_ctags", Neo4jLabel.TOOLS_CTAGS.value),
            (CollectionNames.DEPARTMENTS.value, Neo4jLabel.DEPARTMENTS.value),
            (CollectionNames.CATEGORIES.value, Neo4jLabel.CATEGORIES.value),
            (CollectionNames.SUBCATEGORIES1.value, Neo4jLabel.SUBCATEGORIES1.value),
            (CollectionNames.SUBCATEGORIES2.value, Neo4jLabel.SUBCATEGORIES2.value),
            (CollectionNames.SUBCATEGORIES3.value, Neo4jLabel.SUBCATEGORIES3.value),
            (CollectionNames.LANGUAGES.value, Neo4jLabel.LANGUAGES.value),
            (CollectionNames.TOPICS.value, Neo4jLabel.TOPICS.value),
            (CollectionNames.TEAMS.value, Neo4jLabel.TEAMS.value),
            (CollectionNames.AGENT_TEMPLATES.value, Neo4jLabel.AGENT_TEMPLATES.value),
            (CollectionNames.AGENT_INSTANCES.value, Neo4jLabel.AGENT_INSTANCES.value),
            (CollectionNames.AGENT_KNOWLEDGE.value, Neo4jLabel.AGENT_KNOWLEDGE.value),
            (CollectionNames.AGENT_TOOLSETS.value, Neo4jLabel.AGENT_TOOLSETS.value),
            (CollectionNames.AGENT_TOOLS.value, Neo4jLabel.AGENT_TOOLS.value),
        ]
        for arango_key, neo4j_label in expected_pairs:
            assert COLLECTION_TO_LABEL[arango_key] == neo4j_label, (
                f"Mapping mismatch for {arango_key}: "
                f"expected {neo4j_label}, got {COLLECTION_TO_LABEL.get(arango_key)}"
            )

    def test_mapping_size(self):
        assert len(COLLECTION_TO_LABEL) == 35

    def test_all_values_are_strings(self):
        for k, v in COLLECTION_TO_LABEL.items():
            assert isinstance(k, str)
            assert isinstance(v, str)


# ---------------------------------------------------------------------------
# EDGE_COLLECTION_TO_RELATIONSHIP mapping
# ---------------------------------------------------------------------------

class TestEdgeCollectionToRelationshipMapping:
    def test_all_edge_entries(self):
        expected_pairs = [
            (CollectionNames.RECORD_RELATIONS.value, Neo4jRelationshipType.RECORD_RELATIONS.value),
            (CollectionNames.BELONGS_TO.value, Neo4jRelationshipType.BELONGS_TO.value),
            (CollectionNames.IS_OF_TYPE.value, Neo4jRelationshipType.IS_OF_TYPE.value),
            (CollectionNames.PERMISSION.value, Neo4jRelationshipType.PERMISSION.value),
            (CollectionNames.INHERIT_PERMISSIONS.value, Neo4jRelationshipType.INHERIT_PERMISSIONS.value),
            (CollectionNames.USER_APP_RELATION.value, Neo4jRelationshipType.USER_APP_RELATION.value),
            (CollectionNames.ORG_APP_RELATION.value, Neo4jRelationshipType.ORG_APP_RELATION.value),
            (CollectionNames.USER_DRIVE_RELATION.value, Neo4jRelationshipType.USER_DRIVE_RELATION.value),
            (CollectionNames.BELONGS_TO_DEPARTMENT.value, Neo4jRelationshipType.BELONGS_TO_DEPARTMENT.value),
            (CollectionNames.BELONGS_TO_CATEGORY.value, Neo4jRelationshipType.BELONGS_TO_CATEGORY.value),
            (CollectionNames.BELONGS_TO_LANGUAGE.value, Neo4jRelationshipType.BELONGS_TO_LANGUAGE.value),
            (CollectionNames.BELONGS_TO_TOPIC.value, Neo4jRelationshipType.BELONGS_TO_TOPIC.value),
            (CollectionNames.AGENT_HAS_KNOWLEDGE.value, Neo4jRelationshipType.AGENT_HAS_KNOWLEDGE.value),
            (CollectionNames.AGENT_HAS_TOOLSET.value, Neo4jRelationshipType.AGENT_HAS_TOOLSET.value),
            (CollectionNames.TOOLSET_HAS_TOOL.value, Neo4jRelationshipType.TOOLSET_HAS_TOOL.value),
        ]
        for arango_key, neo4j_rel in expected_pairs:
            assert EDGE_COLLECTION_TO_RELATIONSHIP[arango_key] == neo4j_rel

    def test_mapping_size(self):
        assert len(EDGE_COLLECTION_TO_RELATIONSHIP) == 15

    def test_all_values_are_strings(self):
        for k, v in EDGE_COLLECTION_TO_RELATIONSHIP.items():
            assert isinstance(k, str)
            assert isinstance(v, str)


# ---------------------------------------------------------------------------
# collection_to_label() function
# ---------------------------------------------------------------------------

class TestCollectionToLabelFunction:
    def test_known_collection(self):
        assert collection_to_label(CollectionNames.RECORDS.value) == "Record"

    def test_another_known_collection(self):
        assert collection_to_label(CollectionNames.USERS.value) == "User"

    def test_tools_string_key(self):
        assert collection_to_label("tools") == "Tool"

    def test_tools_ctags_string_key(self):
        assert collection_to_label("tools_ctags") == "ToolCtag"

    def test_fallback_capitalizes(self):
        assert collection_to_label("unknowncollection") == "Unknowncollection"

    def test_fallback_already_capitalized(self):
        assert collection_to_label("MyCollection") == "Mycollection"

    def test_fallback_empty_string(self):
        assert collection_to_label("") == ""


# ---------------------------------------------------------------------------
# edge_collection_to_relationship() function
# ---------------------------------------------------------------------------

class TestEdgeCollectionToRelationshipFunction:
    def test_known_edge(self):
        assert edge_collection_to_relationship(CollectionNames.RECORD_RELATIONS.value) == "RECORD_RELATION"

    def test_another_known_edge(self):
        assert edge_collection_to_relationship(CollectionNames.BELONGS_TO.value) == "BELONGS_TO"

    def test_agent_edge(self):
        assert edge_collection_to_relationship(CollectionNames.AGENT_HAS_KNOWLEDGE.value) == "AGENT_HAS_KNOWLEDGE"

    def test_fallback_uppercases(self):
        assert edge_collection_to_relationship("unknownEdge") == "UNKNOWNEDGE"

    def test_fallback_empty_string(self):
        assert edge_collection_to_relationship("") == ""


# ---------------------------------------------------------------------------
# parse_node_id() function
# ---------------------------------------------------------------------------

class TestParseNodeId:
    def test_standard_id(self):
        collection, key = parse_node_id("records/123")
        assert collection == "records"
        assert key == "123"

    def test_id_with_multiple_slashes(self):
        collection, key = parse_node_id("users/some/complex/key")
        assert collection == "users"
        assert key == "some/complex/key"

    def test_no_slash(self):
        collection, key = parse_node_id("justkey")
        assert collection == ""
        assert key == "justkey"

    def test_empty_string(self):
        collection, key = parse_node_id("")
        assert collection == ""
        assert key == ""

    def test_slash_at_start(self):
        collection, key = parse_node_id("/key")
        assert collection == ""
        assert key == "key"

    def test_slash_at_end(self):
        collection, key = parse_node_id("collection/")
        assert collection == "collection"
        assert key == ""


# ---------------------------------------------------------------------------
# build_node_id() function
# ---------------------------------------------------------------------------

class TestBuildNodeId:
    def test_standard_build(self):
        assert build_node_id("records", "123") == "records/123"

    def test_empty_collection(self):
        assert build_node_id("", "key") == "/key"

    def test_empty_key(self):
        assert build_node_id("collection", "") == "collection/"

    def test_both_empty(self):
        assert build_node_id("", "") == "/"

    def test_roundtrip_with_parse(self):
        """build then parse should give original parts back."""
        node_id = build_node_id("users", "abc-def")
        coll, key = parse_node_id(node_id)
        assert coll == "users"
        assert key == "abc-def"
