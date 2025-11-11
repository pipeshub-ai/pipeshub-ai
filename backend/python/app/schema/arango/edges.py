record_relations_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "relationshipType": {
                "type": "string",
                "enum": [
                    "PARENT_CHILD",
                    "DUPLICATE",
                    "ATTACHMENT",
                    "SIBLING",
                    "OTHERS",
                ],
            },
            "customRelationshipTag": {"type": "string"},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the file relations schema.",
}

is_of_type_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
    },
    "level": "strict",
    "message": "Document does not match the relations schema.",
}

user_drive_relation_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "access_level": {"type": "string"},
        },
        "required": ["access_level"],
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the user drive relation schema.",
}

# Tasks belongs to Workflow
# User belongs to *
# Record belongs to *
belongs_to_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "entityType": {
                "type": ["string", "null"],
                "enum": ["GROUP", "DOMAIN", "ORGANIZATION", "KB", "WORKFLOW", "USER"],
            },
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the belongsTo schema.",
}

# This is when records/record groups inherit permissions from parent record groups
inherit_permissions_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the inheritPermissions schema.",
}

permissions_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "externalPermissionId": {"type": ["string", "null"]},
            "type": {"type": ["string", "null"], "enum": ["USER", "GROUP", "DOMAIN","TEAM", "ORG", "ROLE"]},
            "role": {
                "type": "string",
                "enum": [
                    "OWNER",
                    "ORGANIZER",
                    "FILEORGANIZER",
                    "WRITER",
                    "COMMENTER",
                    "READER",
                    "OTHERS",
                ],
            },
            "sourceRoleType": {"type": ["string", "null"]},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
            "lastUpdatedTimestampAtSource": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the permissions schema.",
}

user_app_relation_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "syncState": {
                "type": "string",
                "enum": ["NOT_STARTED", "IN_PROGRESS", "PAUSED", "COMPLETED", "FAILED"],
            },
            "lastSyncUpdate": {"type": "number"},
            "sourceUserId": {"type": "string"},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "required": ["syncState", "lastSyncUpdate"],
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the user app relation schema.",
}

# Agent -> Tool, Model, Workflow
# Task -> agent
basic_edge_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "createdAtTimestamp": {"type": "number"},
        },
        "required": ["createdAtTimestamp"],
        "additionalProperties": False,
    },
    "level": "strict",
    "message": "Document does not match the basic edge schema.",
}

# User -> Agent
# User -> Agent Template
role_based_edge_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "createdAtTimestamp": {"type": "number"},
            "role": {"type": "string", "enum": ["OWNER", "MEMBER"]},
        },
        "required": ["role", "createdAtTimestamp"],
        "additionalProperties": True,
    }
}

# Agent -> Memory
source_edge_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "createdAtTimestamp": {"type": "number"},
            "source": {"type": "string", "enum": ["CONVERSATION", "KNOWLEDGE_BASE", "APPS"]},
        },
        "required": ["createdAtTimestamp"],
        "additionalProperties": True,
    }
}


# future schema

# task_action_edge_schema = {
#     "rule": {
#         "type": "object",
#         "properties": {
#             "_from": {"type": "string", "minLength": 1},
#             "_to": {"type": "string", "minLength": 1},
#             "createdAtTimestamp": {"type": "number"},
#             "approvers": {
#                 "type": "array", "items":{
#                 "type": "object",
#                     "properties": {
#                         "userId": {"type": "array", "items": {"type": "string"}},
#                         "userGroupsIds": {"type": "array", "items": {"type": "string"}},
#                         "order": {"type": "number"},
#                     },
#                 "required": ["userId", "order"],
#                 "additionalProperties": True,
#             }},
#             "reviewers": {
#                 "type": "array", "items":{
#                 "type": "object",
#                 "properties": {
#                     "userId": {"type": "array", "items": {"type": "string"}},
#                     "userGroupsIds": {"type": "array", "items": {"type": "string"}},
#                     "order": {"type": "number"},
#                 },
#                 "required": ["userId", "order"],
#                 "additionalProperties": True,
#             }},
#         },
#     },
#     "level": "strict",
#     "message": "Document does not match the agent action edge schema.",
# }
