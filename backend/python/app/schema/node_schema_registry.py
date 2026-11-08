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
    code_file_record_schema,
    comment_record_schema,
    deal_record_schema,
    department_schema,
    knowledge_schema,
    mcp_server_schema,
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
    product_record_schema,
    project_record_schema,
    pull_request_record_schema,
    record_group_schema,
    record_schema,
    stage_state_schema,
    sql_column_record_schema,
    sql_table_record_schema,
    sql_view_record_schema,
    team_schema,
    ticket_record_schema,
    tool_schema,
    toolset_schema,
    user_schema,
    webpage_record_schema,
)
from app.schema.arango.edges import (
    agent_has_knowledge_schema,
    agent_has_mcp_server_schema,
    agent_has_skill_schema,
    agent_has_toolset_schema,
    agent_skill_relation_schema,
    authenticated_as_schema,
    basic_edge_schema,
    belongs_to_schema,
    contact_schema,
    customer_schema,
    deal_info_schema,
    deal_of_schema,
    entity_relations_schema,
    inherit_permissions_schema,
    is_of_type_schema,
    lead_schema,
    mcp_server_has_tool_schema,
    member_of_schema,
    mentions_entity_schema,
    node_relations_schema,
    permissions_schema,
    prospect_schema,
    record_links_schema,
    sold_in_schema,
    taxonomy_edge_schema,
    toolset_has_tool_schema,
    user_app_relation_schema,
    user_drive_relation_schema,
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


#: A projected property value on a Neo4j record: a flat ``p_<name>`` key holding a scalar or a list of
#: scalars; null is allowed because an update removes a key by setting it to null. (ArangoDB keeps the
#: values in the ``recordProps`` sub-document, which the shared record schema declares.)
RECORD_PROPERTY_KEY_PATTERN = r"^p_[a-zA-Z0-9_]{1,64}$"
_RECORD_PROPERTY_VALUE = {
    "type": ["string", "number", "boolean", "null"],
}
_RECORD_PROPERTY_LIST = {"type": "array", "items": {"type": "string"}}


def _with_record_property_keys(schema: dict | None) -> dict | None:
    if schema is None:
        return None
    schema["patternProperties"] = {
        RECORD_PROPERTY_KEY_PATTERN: {"anyOf": [_RECORD_PROPERTY_VALUE, _RECORD_PROPERTY_LIST]},
    }
    return schema


# Build the node schema registry mapping collection names to adapted schemas
# This mirrors the NODE_COLLECTIONS list defined by CollectionNames in app/config/constants/arangodb.py
NODE_SCHEMA_REGISTRY: dict[str, dict | None] = {
    CollectionNames.RECORDS.value: _with_record_property_keys(adapt_schema(record_schema)),
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
    CollectionNames.BLOCKS.value: adapt_schema(block_schema),
    CollectionNames.AGENT_KNOWLEDGE.value: adapt_schema(knowledge_schema),
    CollectionNames.AGENT_TOOLSETS.value: adapt_schema(toolset_schema),
    CollectionNames.AGENT_TOOLS.value: adapt_schema(tool_schema),
    CollectionNames.AGENT_MCP_SERVERS.value: adapt_schema(mcp_server_schema),
    CollectionNames.CODE_FILES.value: adapt_schema(code_file_record_schema),
    CollectionNames.DEALS.value: adapt_schema(deal_record_schema),
    CollectionNames.PRODUCTS.value: adapt_schema(product_record_schema),
    CollectionNames.PULLREQUESTS.value: adapt_schema(pull_request_record_schema),
    CollectionNames.SQL_TABLES.value: adapt_schema(sql_table_record_schema),
    CollectionNames.SQL_COLUMNS.value: adapt_schema(sql_column_record_schema),
    CollectionNames.SQL_VIEWS.value: adapt_schema(sql_view_record_schema),
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


# Edge collections, built the same way: the Arango rule, `_from`/`_to` kept as declared (the validator
# strips both endpoint formats before checking). Mirrors EDGE_COLLECTIONS in arango_http_provider.
EDGE_SCHEMA_REGISTRY: dict[str, dict | None] = {
    CollectionNames.IS_OF_TYPE.value: adapt_schema(is_of_type_schema),
    CollectionNames.NODE_RELATIONS.value: adapt_schema(node_relations_schema),
    CollectionNames.RECORD_LINKS.value: adapt_schema(record_links_schema),
    CollectionNames.ENTITY_RELATIONS.value: adapt_schema(entity_relations_schema),
    CollectionNames.USER_DRIVE_RELATION.value: adapt_schema(user_drive_relation_schema),
    CollectionNames.BELONGS_TO_DEPARTMENT.value: adapt_schema(basic_edge_schema),
    CollectionNames.ORG_DEPARTMENT_RELATION.value: adapt_schema(basic_edge_schema),
    CollectionNames.BELONGS_TO.value: adapt_schema(belongs_to_schema),
    CollectionNames.INHERIT_PERMISSIONS.value: adapt_schema(inherit_permissions_schema),
    CollectionNames.ORG_APP_RELATION.value: adapt_schema(basic_edge_schema),
    CollectionNames.USER_APP_RELATION.value: adapt_schema(user_app_relation_schema),
    CollectionNames.AUTHENTICATED_AS.value: adapt_schema(authenticated_as_schema),
    CollectionNames.BELONGS_TO_CATEGORY.value: adapt_schema(taxonomy_edge_schema),
    CollectionNames.BELONGS_TO_LANGUAGE.value: adapt_schema(taxonomy_edge_schema),
    CollectionNames.BELONGS_TO_TOPIC.value: adapt_schema(taxonomy_edge_schema),
    CollectionNames.MENTIONS_ENTITY.value: adapt_schema(mentions_entity_schema),
    CollectionNames.BELONGS_TO_RECORD_GROUP.value: adapt_schema(basic_edge_schema),
    CollectionNames.INTER_CATEGORY_RELATIONS.value: adapt_schema(basic_edge_schema),
    CollectionNames.PERMISSION.value: adapt_schema(permissions_schema),
    CollectionNames.AGENT_HAS_KNOWLEDGE.value: adapt_schema(agent_has_knowledge_schema),
    CollectionNames.AGENT_HAS_TOOLSET.value: adapt_schema(agent_has_toolset_schema),
    CollectionNames.TOOLSET_HAS_TOOL.value: adapt_schema(toolset_has_tool_schema),
    CollectionNames.AGENT_HAS_MCP_SERVER.value: adapt_schema(agent_has_mcp_server_schema),
    CollectionNames.MCP_SERVER_HAS_TOOL.value: adapt_schema(mcp_server_has_tool_schema),
    CollectionNames.AGENT_SKILL_RELATION.value: adapt_schema(agent_skill_relation_schema),
    CollectionNames.AGENT_HAS_SKILL.value: adapt_schema(agent_has_skill_schema),
    CollectionNames.PROSPECT.value: adapt_schema(prospect_schema),
    CollectionNames.CUSTOMER.value: adapt_schema(customer_schema),
    CollectionNames.LEAD.value: adapt_schema(lead_schema),
    CollectionNames.CONTACT.value: adapt_schema(contact_schema),
    CollectionNames.DEAL_INFO.value: adapt_schema(deal_info_schema),
    CollectionNames.DEAL_OF.value: adapt_schema(deal_of_schema),
    CollectionNames.SOLD_IN.value: adapt_schema(sold_in_schema),
    CollectionNames.MEMBER_OF.value: adapt_schema(member_of_schema),
}


def get_edge_schema(collection: str) -> dict | None:
    """Adapted JSON Schema for an edge collection, or None if it has none."""
    return EDGE_SCHEMA_REGISTRY.get(collection)


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
# must decode on read, from the same schemas the writes follow (SC-8).
JSON_ENCODED_FIELDS: dict[str, frozenset[str]] = {
    collection: fields
    for collection, fields in {
        **{c: _json_object_fields(s) for c, s in NODE_SCHEMA_REGISTRY.items()},
        # Has no schema. ``retired`` is a list of maps, so Neo4j stores it as a JSON
        # string; no compare-and-set ever compares it (``switchSeq`` is the guard).
        CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value: frozenset({"retired"}),
    }.items()
    if fields
}
