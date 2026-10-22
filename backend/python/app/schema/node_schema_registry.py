"""
Node Schema Registry

This module adapts the existing ArangoDB JSON Schema definitions from arango/documents.py
for use with the jsonschema Python library in the Neo4j provider.

The adaptation process:
1. Extracts the 'rule' key (the actual JSON Schema) from the ArangoDB schema wrapper
2. Remaps '_key' to 'id' in properties (Neo4j uses 'id', ArangoDB uses '_key')
3. Strips ArangoDB-specific keys ('level', 'message')
4. Returns None for collections without schemas
"""

import copy

from app.config.constants.arangodb import CollectionNames
from app.schema.arango.documents import (
    agent_schema,
    agent_skill_candidates_schema,
    agent_skill_versions_schema,
    agent_skills_schema,
    agent_template_schema,
    app_role_schema,
    app_schema,
    artifact_record_schema,
    block_schema,
    comment_record_schema,
    department_schema,
    named_entity_schema,
    named_entity_persist_retry_schema,
    value_mention_schema,
    file_record_schema,
    link_record_schema,
    mail_record_schema,
    meeting_record_schema,
    message_record_schema,
    orgs_schema,
    people_schema,
    project_record_schema,
    record_group_schema,
    record_schema,
    stage_state_schema,
    sql_table_record_schema,
    team_schema,
    ticket_record_schema,
    user_schema,
    webpage_record_schema,
)


def adapt_schema(arango_schema: dict | None) -> dict | None:
    """
    Adapt an ArangoDB schema for use with jsonschema library.

    Args:
        arango_schema: Schema dict from arango/documents.py (may be None)

    Returns:
        Adapted JSON Schema dict, or None if input is None
    """
    if arango_schema is None:
        return None

    # Extract the 'rule' key (the actual JSON Schema)
    if "rule" not in arango_schema:
        # Some schemas might not have the wrapper - return as-is
        return arango_schema

    schema = copy.deepcopy(arango_schema["rule"])

    # Remap '_key' to 'id' in properties
    if "properties" in schema and "_key" in schema["properties"]:
        schema["properties"]["id"] = schema["properties"].pop("_key")
    elif "properties" in schema:
        # Add 'id' property if not present (for schemas with additionalProperties: false)
        # This ensures the 'id' field doesn't get rejected
        schema["properties"]["id"] = {"type": "string"}

    return schema


# Build the node schema registry mapping collection names to adapted schemas
# This mirrors the NODE_COLLECTIONS list defined by CollectionNames in app/config/constants/arangodb.py
NODE_SCHEMA_REGISTRY: dict[str, dict | None] = {
    CollectionNames.RECORDS.value: adapt_schema(record_schema),
    CollectionNames.STAGE_STATES.value: adapt_schema(stage_state_schema),
    CollectionNames.DRIVES.value: None,  # No schema
    CollectionNames.FILES.value: adapt_schema(file_record_schema),
    CollectionNames.LINKS.value: adapt_schema(link_record_schema),
    CollectionNames.MAILS.value: adapt_schema(mail_record_schema),
    CollectionNames.MESSAGES.value: adapt_schema(message_record_schema),
    CollectionNames.WEBPAGES.value: adapt_schema(webpage_record_schema),
    CollectionNames.COMMENTS.value: adapt_schema(comment_record_schema),
    CollectionNames.PEOPLE.value: adapt_schema(people_schema),
    CollectionNames.USERS.value: adapt_schema(user_schema),
    CollectionNames.GROUPS.value: None,  # No schema
    CollectionNames.ROLES.value: adapt_schema(app_role_schema),
    CollectionNames.ORGS.value: adapt_schema(orgs_schema),
    CollectionNames.ANYONE.value: None,  # No schema
    CollectionNames.CHANNEL_HISTORY.value: None,  # No schema
    CollectionNames.PAGE_TOKENS.value: None,  # No schema
    CollectionNames.APPS.value: adapt_schema(app_schema),
    CollectionNames.DEPARTMENTS.value: adapt_schema(department_schema),
    CollectionNames.NAMED_ENTITIES.value: adapt_schema(named_entity_schema),
    CollectionNames.VALUE_MENTIONS.value: adapt_schema(value_mention_schema),
    CollectionNames.NAMED_ENTITY_PERSIST_RETRIES.value: adapt_schema(named_entity_persist_retry_schema),
    CollectionNames.CATEGORIES.value: None,  # No schema
    CollectionNames.LANGUAGES.value: None,  # No schema
    CollectionNames.TOPICS.value: None,  # No schema
    CollectionNames.SUBCATEGORIES1.value: None,  # No schema
    CollectionNames.SUBCATEGORIES2.value: None,  # No schema
    CollectionNames.SUBCATEGORIES3.value: None,  # No schema
    CollectionNames.BLOCKS.value: None,  # No schema
    CollectionNames.RECORD_GROUPS.value: adapt_schema(record_group_schema),
    CollectionNames.AGENT_INSTANCES.value: adapt_schema(agent_schema),
    CollectionNames.AGENT_TEMPLATES.value: adapt_schema(agent_template_schema),
    CollectionNames.AGENT_SKILLS.value: adapt_schema(agent_skills_schema),
    CollectionNames.AGENT_SKILL_VERSIONS.value: adapt_schema(agent_skill_versions_schema),
    CollectionNames.AGENT_SKILL_CANDIDATES.value: adapt_schema(agent_skill_candidates_schema),
    CollectionNames.TICKETS.value: adapt_schema(ticket_record_schema),
    CollectionNames.MEETINGS.value: adapt_schema(meeting_record_schema),
    CollectionNames.PROJECTS.value: adapt_schema(project_record_schema),
    CollectionNames.SYNC_POINTS.value: None,  # No schema
    CollectionNames.TEAMS.value: adapt_schema(team_schema),
    CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value: None,  # No schema
    CollectionNames.ARTIFACTS.value: adapt_schema(artifact_record_schema),
}


def get_node_schema(collection: str) -> dict | None:
    """
    Get the adapted JSON Schema for a collection.

    Args:
        collection: Collection name

    Returns:
        Adapted JSON Schema dict, or None if no schema exists for this collection
    """
    return NODE_SCHEMA_REGISTRY.get(collection)


def get_required_fields(collection: str) -> list:
    """
    Get the list of required fields for a collection from its schema.

    Args:
        collection: Collection name

    Returns:
        List of required field names, or empty list if no schema or no required fields
    """
    schema = get_node_schema(collection)
    if schema is None:
        return []

    return schema.get("required", [])


def _types(prop: dict) -> set:
    declared = prop.get("type")
    return set(declared) if isinstance(declared, list) else {declared}


def _json_object_fields(schema: dict | None) -> frozenset[str]:
    """Properties a schema declares as an object or an array of objects/arrays."""
    if not schema:
        return frozenset()
    rule = schema.get("rule", schema)
    return frozenset(
        name for name, prop in (rule.get("properties") or {}).items()
        if "object" in _types(prop)
        or ("array" in _types(prop) and _types(prop.get("items") or {}) & {"object", "array"})
    )


# Fields Neo4j stores as JSON strings (it has no map or list-of-map property) and
# must decode on read, from the same schemas the writes follow (SC-8). Blocks and
# SQL tables are not validated through NODE_SCHEMA_REGISTRY, so their schemas are
# read here directly.
JSON_ENCODED_FIELDS: dict[str, frozenset[str]] = {
    collection: fields
    for collection, fields in {
        **{c: _json_object_fields(s) for c, s in NODE_SCHEMA_REGISTRY.items()},
        CollectionNames.BLOCKS.value: _json_object_fields(block_schema),
        CollectionNames.SQL_TABLES.value: _json_object_fields(sql_table_record_schema),
    }.items()
    if fields
}
