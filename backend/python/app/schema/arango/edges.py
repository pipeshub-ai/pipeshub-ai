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

belongs_to_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "entityType": {
                "type": "string",
                "enum": ["GROUP", "DOMAIN", "ORGANIZATION", "KB"],
            },
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "required": ["entityType"],
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the belongsTo schema.",
}

permissions_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "externalPermissionId": {"type": ["string", "null"]},
            "type": {"type": "string", "enum": ["USER", "GROUP", "DOMAIN"]},
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
        },
        "required": ["syncState", "lastSyncUpdate"],
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the user app relation schema.",
}

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
