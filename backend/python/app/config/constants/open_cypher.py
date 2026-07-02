from enum import Enum

from app.config.constants.arangodb import CollectionNames

class FalkorLabel(Enum):
    """Falkor node labels mapped from ArangoDB collections"""
    # Records and Record relations
    RECORDS = "Record"
    RECORD_GROUPS = "RecordGroup"
    SYNC_POINTS = "SyncPoint"

    # Record types
    FILES = "File"
    MAILS = "Mail"
    MESSAGES = "Message"
    WEBPAGES = "Webpage"
    COMMENTS = "Comment"
    TICKETS = "Ticket"
    LINKS = "Link"
    PROJECTS = "Project"
    MEETINGS = "Meeting"
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

    # Teams
    TEAMS = "Teams"

    # Agent Builder collections
    AGENT_TEMPLATES = "AgentTemplate"
    AGENT_INSTANCES = "AgentInstance"
    AGENT_KNOWLEDGE = "AgentKnowledge"
    AGENT_TOOLSETS = "AgentToolset"
    AGENT_TOOLS = "AgentTool"

    # Sales
    DEALS = "Deals"
    PRODUCTS = "Products"

    # Artifacts
    ARTIFACTS = "Artifact"


class FalkorRelationshipType(Enum):
    """Falkor relationship types mapped from ArangoDB edge collections"""
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

    # Agent Builder relationships
    AGENT_HAS_KNOWLEDGE = "AGENT_HAS_KNOWLEDGE"
    AGENT_HAS_TOOLSET = "AGENT_HAS_TOOLSET"
    TOOLSET_HAS_TOOL = "TOOLSET_HAS_TOOL"

    # Sales relationships
    SOLD_IN = "SOLD_IN"
    DEAL_OF = "DEAL_OF"
    MEMBER_OF = "MEMBER_OF"
    PROSPECT = "PROSPECT"
    CUSTOMER = "CUSTOMER"
    LEAD = "LEAD"
    CONTACT = "CONTACT"
    DEAL_INFO = "DEAL_INFO"


COLLECTION_TO_LABEL: dict[str, str] = {
    CollectionNames.RECORDS.value: FalkorLabel.RECORDS.value,
    CollectionNames.RECORD_GROUPS.value: FalkorLabel.RECORD_GROUPS.value,
    CollectionNames.SYNC_POINTS.value: FalkorLabel.SYNC_POINTS.value,
    CollectionNames.FILES.value: FalkorLabel.FILES.value,
    CollectionNames.MAILS.value: FalkorLabel.MAILS.value,
    CollectionNames.MESSAGES.value: FalkorLabel.MESSAGES.value,
    CollectionNames.WEBPAGES.value: FalkorLabel.WEBPAGES.value,
    CollectionNames.COMMENTS.value: FalkorLabel.COMMENTS.value,
    CollectionNames.TICKETS.value: FalkorLabel.TICKETS.value,
    CollectionNames.MEETINGS.value: FalkorLabel.MEETINGS.value,
    CollectionNames.LINKS.value: FalkorLabel.LINKS.value,
    CollectionNames.PROJECTS.value: FalkorLabel.PROJECTS.value,
    CollectionNames.SQL_TABLES.value: FalkorLabel.SQL_TABLES.value,
    CollectionNames.SQL_VIEWS.value: FalkorLabel.SQL_VIEWS.value,
    CollectionNames.USERS.value: FalkorLabel.USERS.value,
    CollectionNames.GROUPS.value: FalkorLabel.GROUPS.value,
    CollectionNames.PEOPLE.value: FalkorLabel.PEOPLE.value,
    CollectionNames.ROLES.value: FalkorLabel.ROLES.value,
    CollectionNames.ORGS.value: FalkorLabel.ORGS.value,
    CollectionNames.ANYONE.value: FalkorLabel.ANYONE.value,
    CollectionNames.APPS.value: FalkorLabel.APPS.value,
    CollectionNames.DRIVES.value: FalkorLabel.DRIVES.value,
    CollectionNames.PAGE_TOKENS.value: FalkorLabel.PAGE_TOKENS.value,
    CollectionNames.BLOCKS.value: FalkorLabel.BLOCKS.value,
    CollectionNames.DEALS.value: FalkorLabel.DEALS.value,
    CollectionNames.PRODUCTS.value: FalkorLabel.PRODUCTS.value,
    CollectionNames.ARTIFACTS.value: FalkorLabel.ARTIFACTS.value,

    # Tools collections (not in CollectionNames enum, using string names)
    "tools": FalkorLabel.TOOLS.value,
    "tools_ctags": FalkorLabel.TOOLS_CTAGS.value,
    # Metadata collections
    CollectionNames.DEPARTMENTS.value: FalkorLabel.DEPARTMENTS.value,
    CollectionNames.CATEGORIES.value: FalkorLabel.CATEGORIES.value,
    CollectionNames.SUBCATEGORIES1.value: FalkorLabel.SUBCATEGORIES1.value,
    CollectionNames.SUBCATEGORIES2.value: FalkorLabel.SUBCATEGORIES2.value,
    CollectionNames.SUBCATEGORIES3.value: FalkorLabel.SUBCATEGORIES3.value,
    CollectionNames.LANGUAGES.value: FalkorLabel.LANGUAGES.value,
    CollectionNames.TOPICS.value: FalkorLabel.TOPICS.value,
    # Teams
    CollectionNames.TEAMS.value: FalkorLabel.TEAMS.value,
    # Agent Builder collections
    CollectionNames.AGENT_TEMPLATES.value: FalkorLabel.AGENT_TEMPLATES.value,
    CollectionNames.AGENT_INSTANCES.value: FalkorLabel.AGENT_INSTANCES.value,
    CollectionNames.AGENT_KNOWLEDGE.value: FalkorLabel.AGENT_KNOWLEDGE.value,
    CollectionNames.AGENT_TOOLSETS.value: FalkorLabel.AGENT_TOOLSETS.value,
    CollectionNames.AGENT_TOOLS.value: FalkorLabel.AGENT_TOOLS.value,
}

# Mapping from ArangoDB edge collections to Falkor relationship types
EDGE_COLLECTION_TO_RELATIONSHIP: dict[str, str] = {
    CollectionNames.RECORD_RELATIONS.value: FalkorRelationshipType.RECORD_RELATIONS.value,
    CollectionNames.BELONGS_TO.value: FalkorRelationshipType.BELONGS_TO.value,
    CollectionNames.IS_OF_TYPE.value: FalkorRelationshipType.IS_OF_TYPE.value,
    CollectionNames.PERMISSION.value: FalkorRelationshipType.PERMISSION.value,
    CollectionNames.INHERIT_PERMISSIONS.value: FalkorRelationshipType.INHERIT_PERMISSIONS.value,
    CollectionNames.USER_APP_RELATION.value: FalkorRelationshipType.USER_APP_RELATION.value,
    CollectionNames.ORG_APP_RELATION.value: FalkorRelationshipType.ORG_APP_RELATION.value,
    CollectionNames.USER_DRIVE_RELATION.value: FalkorRelationshipType.USER_DRIVE_RELATION.value,
    CollectionNames.BELONGS_TO_DEPARTMENT.value: FalkorRelationshipType.BELONGS_TO_DEPARTMENT.value,
    CollectionNames.BELONGS_TO_CATEGORY.value: FalkorRelationshipType.BELONGS_TO_CATEGORY.value,
    CollectionNames.BELONGS_TO_LANGUAGE.value: FalkorRelationshipType.BELONGS_TO_LANGUAGE.value,
    CollectionNames.BELONGS_TO_TOPIC.value: FalkorRelationshipType.BELONGS_TO_TOPIC.value,
    # Agent Builder relationships
    CollectionNames.AGENT_HAS_KNOWLEDGE.value: FalkorRelationshipType.AGENT_HAS_KNOWLEDGE.value,
    CollectionNames.AGENT_HAS_TOOLSET.value: FalkorRelationshipType.AGENT_HAS_TOOLSET.value,
    CollectionNames.TOOLSET_HAS_TOOL.value: FalkorRelationshipType.TOOLSET_HAS_TOOL.value,
    CollectionNames.SOLD_IN.value: FalkorRelationshipType.SOLD_IN.value,
    CollectionNames.DEAL_OF.value: FalkorRelationshipType.DEAL_OF.value,
    CollectionNames.MEMBER_OF.value: FalkorRelationshipType.MEMBER_OF.value,
    CollectionNames.PROSPECT.value: FalkorRelationshipType.PROSPECT.value,
    CollectionNames.CUSTOMER.value: FalkorRelationshipType.CUSTOMER.value,
    CollectionNames.LEAD.value: FalkorRelationshipType.LEAD.value,
    CollectionNames.CONTACT.value: FalkorRelationshipType.CONTACT.value,
    CollectionNames.DEAL_INFO.value: FalkorRelationshipType.DEAL_INFO.value,
}


def collection_to_label(collection: str) -> str:
    """Convert ArangoDB collection name to Falkor label"""
    return COLLECTION_TO_LABEL.get(collection, collection.capitalize())


def edge_collection_to_relationship(edge_collection: str) -> str:
    """Convert ArangoDB edge collection name to Falkor relationship type"""
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
