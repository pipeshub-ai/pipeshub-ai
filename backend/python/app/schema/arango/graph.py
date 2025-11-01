from app.config.constants.arangodb import CollectionNames

# Define all edge definitions
EDGE_DEFINITIONS = [
    {
        "edge_collection": CollectionNames.PERMISSIONS.value,
        "from_vertex_collections": [CollectionNames.RECORDS.value],
        "to_vertex_collections": [
            CollectionNames.USERS.value,
            CollectionNames.GROUPS.value,
            CollectionNames.ORGS.value,
        ],
    },
    {
        "edge_collection": CollectionNames.BELONGS_TO.value,
        "from_vertex_collections": [CollectionNames.USERS.value,CollectionNames.RECORDS.value,CollectionNames.FILES.value],
        "to_vertex_collections": [
            CollectionNames.GROUPS.value,
            CollectionNames.ORGS.value,
            CollectionNames.RECORD_GROUPS.value
        ],
    },
    {
        "edge_collection": CollectionNames.INHERIT_PERMISSIONS.value,
        "from_vertex_collections": [CollectionNames.RECORD_GROUPS.value],
        "to_vertex_collections": [CollectionNames.RECORDS.value],
    },
    {
        "edge_collection": CollectionNames.ORG_DEPARTMENT_RELATION.value,
        "from_vertex_collections": [CollectionNames.ORGS.value],
        "to_vertex_collections": [CollectionNames.DEPARTMENTS.value],
    },
    {
        "edge_collection": CollectionNames.BELONGS_TO_DEPARTMENT.value,
        "from_vertex_collections": [CollectionNames.RECORDS.value],
        "to_vertex_collections": [CollectionNames.DEPARTMENTS.value],
    },
    {
        "edge_collection": CollectionNames.BELONGS_TO_CATEGORY.value,
        "from_vertex_collections": [CollectionNames.RECORDS.value],
        "to_vertex_collections": [
            CollectionNames.CATEGORIES.value,
            CollectionNames.SUBCATEGORIES1.value,
            CollectionNames.SUBCATEGORIES2.value,
            CollectionNames.SUBCATEGORIES3.value,
        ],
    },
    {
        "edge_collection": CollectionNames.BELONGS_TO_TOPIC.value,
        "from_vertex_collections": [CollectionNames.RECORDS.value],
        "to_vertex_collections": [CollectionNames.TOPICS.value],
    },
    {
        "edge_collection": CollectionNames.BELONGS_TO_LANGUAGE.value,
        "from_vertex_collections": [CollectionNames.RECORDS.value],
        "to_vertex_collections": [CollectionNames.LANGUAGES.value],
    },
    {
        "edge_collection": CollectionNames.INTER_CATEGORY_RELATIONS.value,
        "from_vertex_collections": [CollectionNames.CATEGORIES.value, CollectionNames.SUBCATEGORIES1.value, CollectionNames.SUBCATEGORIES2.value, CollectionNames.SUBCATEGORIES3.value],
        "to_vertex_collections": [CollectionNames.CATEGORIES.value, CollectionNames.SUBCATEGORIES1.value, CollectionNames.SUBCATEGORIES2.value, CollectionNames.SUBCATEGORIES3.value],
    },
    {
        "edge_collection": CollectionNames.PERMISSIONS_TO_KB.value,
        "from_vertex_collections": [CollectionNames.USERS.value, CollectionNames.TEAMS.value],
        "to_vertex_collections": [CollectionNames.RECORD_GROUPS.value],
    },
    {
        "edge_collection": CollectionNames.IS_OF_TYPE.value,
        "from_vertex_collections": [CollectionNames.RECORDS.value],
        "to_vertex_collections": [CollectionNames.FILES.value, CollectionNames.MAILS.value, CollectionNames.WEBPAGES.value, CollectionNames.TICKETS.value],
    },
    {
        "edge_collection": CollectionNames.RECORD_RELATIONS.value,
        "from_vertex_collections": [CollectionNames.RECORDS.value, CollectionNames.FILES.value,CollectionNames.RECORD_GROUPS.value],
        "to_vertex_collections": [CollectionNames.RECORDS.value, CollectionNames.FILES.value],
    },
    {
        "edge_collection": CollectionNames.USER_DRIVE_RELATION.value,
        "from_vertex_collections": [CollectionNames.USERS.value],
        "to_vertex_collections": [CollectionNames.DRIVES.value],
    },
    {
        "edge_collection": CollectionNames.USER_APP_RELATION.value,
        "from_vertex_collections": [CollectionNames.USERS.value],
        "to_vertex_collections": [CollectionNames.APPS.value],
    },
    {
        "edge_collection": CollectionNames.ORG_APP_RELATION.value,
        "from_vertex_collections": [CollectionNames.ORGS.value],
        "to_vertex_collections": [CollectionNames.APPS.value],
    },
    {
        "edge_collection": CollectionNames.PERMISSION.value,
        "from_vertex_collections": [CollectionNames.USERS.value, CollectionNames.TEAMS.value, CollectionNames.ROLES.value],
        "to_vertex_collections": [CollectionNames.AGENT_INSTANCES.value, CollectionNames.AGENT_TEMPLATES.value, CollectionNames.TEAMS.value, CollectionNames.ROLES.value],
    },
]
