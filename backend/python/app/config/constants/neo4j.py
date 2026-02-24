"""
Neo4j Constants and Schema Mappings

This module provides constants and mapping utilities for Neo4j implementation,
translating ArangoDB concepts (collections, _key, edges) to Neo4j concepts (labels, properties, relationships).
"""

from enum import Enum
from typing import Dict

from app.config.constants.arangodb import CollectionNames


class Neo4jLabel(Enum):
    """Neo4j node labels mapped from ArangoDB collections"""
    # Records and Record relations
    RECORDS = "Record"
    RECORD_GROUPS = "RecordGroup"
    SYNC_POINTS = "SyncPoint"

    # Record types
    FILES = "File"
    MAILS = "Mail"
    WEBPAGES = "Webpage"
    COMMENTS = "Comment"
    TICKETS = "Ticket"
    LINKS = "Link"
    PROJECTS = "Project"
    SQL_TABLES = "SqlTable"
    SQL_VIEWS = "SqlView"

    # Users and groups
    USERS = "User"
    GROUPS = "Group"
    PEOPLE = "Person"
    ROLES = "Role"
    ORGS = "Organization"
    ANYONE = "Anyone"
    ANYONE_WITH_LINK = "AnyoneWithLink"
    ANYONE_SAME_ORG = "AnyoneSameOrg"

    # Apps and relations
    APPS = "App"
    DRIVES = "Drive"

    # Other
    PAGE_TOKENS = "PageToken"
    BLOCKS = "Block"

    # Tools
    TOOLS = "Tool"
    TOOLS_CTAGS = "ToolCtag"

    # Metadata (categories, departments, languages, topics)
    # Using capitalized collection names to match default fallback behavior
    DEPARTMENTS = "Departments"
    CATEGORIES = "Categories"
    SUBCATEGORIES1 = "Subcategories1"
    SUBCATEGORIES2 = "Subcategories2"
    SUBCATEGORIES3 = "Subcategories3"
    LANGUAGES = "Languages"
    TOPICS = "Topics"

    # Storage mappings
    VIRTUAL_RECORD_TO_DOC_ID_MAPPING = "VirtualRecordToDocIdMapping"

    # Teams
    TEAMS = "Teams"


class Neo4jRelationshipType(Enum):
    """Neo4j relationship types mapped from ArangoDB edge collections"""
    RECORD_RELATIONS = "RECORD_RELATION"
    BELONGS_TO = "BELONGS_TO"
    IS_OF_TYPE = "IS_OF_TYPE"
    PERMISSION = "PERMISSION"
    INHERIT_PERMISSIONS = "INHERIT_PERMISSIONS"
    USER_APP_RELATION = "USER_APP_RELATION"
    ORG_APP_RELATION = "ORG_APP_RELATION"
    USER_DRIVE_RELATION = "USER_DRIVE_RELATION"
    BELONGS_TO_DEPARTMENT = "BELONGS_TO_DEPARTMENT"
    BELONGS_TO_CATEGORY = "BELONGS_TO_CATEGORY"
    BELONGS_TO_LANGUAGE = "BELONGS_TO_LANGUAGE"
    BELONGS_TO_TOPIC = "BELONGS_TO_TOPIC"


# Mapping from ArangoDB CollectionNames to Neo4j Labels
COLLECTION_TO_LABEL: Dict[str, str] = {
    CollectionNames.RECORDS.value: Neo4jLabel.RECORDS.value,
    CollectionNames.RECORD_GROUPS.value: Neo4jLabel.RECORD_GROUPS.value,
    CollectionNames.SYNC_POINTS.value: Neo4jLabel.SYNC_POINTS.value,
    CollectionNames.FILES.value: Neo4jLabel.FILES.value,
    CollectionNames.MAILS.value: Neo4jLabel.MAILS.value,
    CollectionNames.WEBPAGES.value: Neo4jLabel.WEBPAGES.value,
    CollectionNames.COMMENTS.value: Neo4jLabel.COMMENTS.value,
    CollectionNames.TICKETS.value: Neo4jLabel.TICKETS.value,
    CollectionNames.LINKS.value: Neo4jLabel.LINKS.value,
    CollectionNames.PROJECTS.value: Neo4jLabel.PROJECTS.value,
    CollectionNames.SQL_TABLES.value: Neo4jLabel.SQL_TABLES.value,
    CollectionNames.SQL_VIEWS.value: Neo4jLabel.SQL_VIEWS.value,
    CollectionNames.USERS.value: Neo4jLabel.USERS.value,
    CollectionNames.GROUPS.value: Neo4jLabel.GROUPS.value,
    CollectionNames.PEOPLE.value: Neo4jLabel.PEOPLE.value,
    CollectionNames.ROLES.value: Neo4jLabel.ROLES.value,
    CollectionNames.ORGS.value: Neo4jLabel.ORGS.value,
    CollectionNames.ANYONE.value: Neo4jLabel.ANYONE.value,
    CollectionNames.APPS.value: Neo4jLabel.APPS.value,
    CollectionNames.DRIVES.value: Neo4jLabel.DRIVES.value,
    CollectionNames.PAGE_TOKENS.value: Neo4jLabel.PAGE_TOKENS.value,
    CollectionNames.BLOCKS.value: Neo4jLabel.BLOCKS.value,
    # Tools collections (not in CollectionNames enum, using string names)
    "tools": Neo4jLabel.TOOLS.value,
    "tools_ctags": Neo4jLabel.TOOLS_CTAGS.value,
    # Metadata collections
    CollectionNames.DEPARTMENTS.value: Neo4jLabel.DEPARTMENTS.value,
    CollectionNames.CATEGORIES.value: Neo4jLabel.CATEGORIES.value,
    CollectionNames.SUBCATEGORIES1.value: Neo4jLabel.SUBCATEGORIES1.value,
    CollectionNames.SUBCATEGORIES2.value: Neo4jLabel.SUBCATEGORIES2.value,
    CollectionNames.SUBCATEGORIES3.value: Neo4jLabel.SUBCATEGORIES3.value,
    CollectionNames.LANGUAGES.value: Neo4jLabel.LANGUAGES.value,
    CollectionNames.TOPICS.value: Neo4jLabel.TOPICS.value,
    # Storage mappings
    CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value: Neo4jLabel.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value,
    # Teams
    CollectionNames.TEAMS.value: Neo4jLabel.TEAMS.value,
}

# Mapping from ArangoDB edge collections to Neo4j relationship types
EDGE_COLLECTION_TO_RELATIONSHIP: Dict[str, str] = {
    CollectionNames.RECORD_RELATIONS.value: Neo4jRelationshipType.RECORD_RELATIONS.value,
    CollectionNames.BELONGS_TO.value: Neo4jRelationshipType.BELONGS_TO.value,
    CollectionNames.IS_OF_TYPE.value: Neo4jRelationshipType.IS_OF_TYPE.value,
    CollectionNames.PERMISSION.value: Neo4jRelationshipType.PERMISSION.value,
    CollectionNames.INHERIT_PERMISSIONS.value: Neo4jRelationshipType.INHERIT_PERMISSIONS.value,
    CollectionNames.USER_APP_RELATION.value: Neo4jRelationshipType.USER_APP_RELATION.value,
    CollectionNames.ORG_APP_RELATION.value: Neo4jRelationshipType.ORG_APP_RELATION.value,
    CollectionNames.USER_DRIVE_RELATION.value: Neo4jRelationshipType.USER_DRIVE_RELATION.value,
    CollectionNames.BELONGS_TO_DEPARTMENT.value: Neo4jRelationshipType.BELONGS_TO_DEPARTMENT.value,
    CollectionNames.BELONGS_TO_CATEGORY.value: Neo4jRelationshipType.BELONGS_TO_CATEGORY.value,
    CollectionNames.BELONGS_TO_LANGUAGE.value: Neo4jRelationshipType.BELONGS_TO_LANGUAGE.value,
    CollectionNames.BELONGS_TO_TOPIC.value: Neo4jRelationshipType.BELONGS_TO_TOPIC.value,
}


def collection_to_label(collection: str) -> str:
    """Convert ArangoDB collection name to Neo4j label"""
    return COLLECTION_TO_LABEL.get(collection, collection.capitalize())


def edge_collection_to_relationship(edge_collection: str) -> str:
    """Convert ArangoDB edge collection name to Neo4j relationship type"""
    return EDGE_COLLECTION_TO_RELATIONSHIP.get(edge_collection, edge_collection.upper())


def parse_node_id(node_id: str) -> tuple[str, str]:
    """
    Parse ArangoDB-style node ID (collection/key) to (collection, key).

    Args:
        node_id: ArangoDB node ID (e.g., "records/123" or "users/abc")

    Returns:
        Tuple of (collection, key)
    """
    if "/" in node_id:
        parts = node_id.split("/", 1)
        return (parts[0], parts[1])
    return ("", node_id)


def build_node_id(collection: str, key: str) -> str:
    """
    Build ArangoDB-style node ID from collection and key.

    Args:
        collection: Collection name
        key: Document key

    Returns:
        Node ID string (e.g., "records/123")
    """
    return f"{collection}/{key}"

