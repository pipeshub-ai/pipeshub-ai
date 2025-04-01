# User schema for ArangoDB
orgs_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_key": {"type": "string"}, # orgId
            "accountType": {"type": "string", "enum": ["individual", "enterprise"]},
            "name": {"type": "string"},
            "isActive": {"type": "boolean", "default": False},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "required": ["accountType", "isActive"],
        "additionalProperties": False,
    },
    "level": "strict",
    "message": "Document does not match the organization schema."
}

user_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_key": {"type": "string"}, #uuid
            "userId" :{"type" : "string"}, 
            "orgId": {"type": "string"},
            "firstName": {"type": "string"},
            "middleName": {"type": "string"},
            "lastName": {"type": "string"},
            "fullName": {"type": "string"},

            "email": {"type": "string", "format": "email"},
            "designation": {"type": "string"},
            "businessPhones": {
                "type": "array",
                "items": {
                    "type": "string"
                },
                "minItems": 0
            },
            "isActive":  {"type": "boolean", "default": False},

            # Arango collection entry
            "createdAtTimestamp": {"type": "number"},
            # Arango collection entry
            "updatedAtTimestamp": {"type": "number"},
        },
        "required": ["email"],  # Required fields
        "additionalProperties": False,  # disallow extra fields
    },
    "level": "strict",  # Strict validation (reject invalid documents)
    "message": "Document does not match the record schema.",
}

user_group_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "orgId": {"type": "string"},
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            # should be a uuid
            "externalGroupId": {"type": "string", "minLength": 1},
            "groupType": {"type": "string", "enum": ["MS_USER_GROUPS", "GOOGLE_USER_GROUPS"]},
            "connectorName": {"type": "string", "enum": ["ONEDRIVE", "DRIVE", "GMAIL", "CONFLUENCE", "SLACK"]},
            "mail": {"type": "string"},
            "mailEnabled": {"type": "boolean", "default": False},

            # Arango collection entry
            "createdAtTimestamp": {"type": "number"},
            # Arango collection entry
            "updatedAtTimestamp": {"type": "number"},
            "lastSyncTimestampstamp": {"type": "number"},  # Arango collection entry

            "isDeletedAtSource": {"type": "boolean", "default": False},
            "deletedAtSourceTimestamp": {"type": "number"},
            "sourceCreatedAtTimestamp": {"type": "number"},
            "sourceLastModifiedTimestamp": {"type": "number"},
        },
        "required": ["groupName", "externalGroupId", "recordType", "groupType", "connectorName", "createdAtTimestamp"],
        "additionalProperties": False,
    },
    "level": "strict",
    "message": "Document does not match the record group schema.",
}

app_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_key": {"type": "string"},
            "name": {"type": "string"},
            "type": {"type": "string"},
            "appGroup": {"type": "string"},
            "appGroupId": {"type": "string"},
            "isActive": {"type": "boolean", "default": True},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "required": ["name", "type", "appGroup", "appGroupId", "isActive", "createdAtTimestamp", "updatedAtTimestamp"],
        "additionalProperties": False,
    },
    "level": "strict",
    "message": "Document does not match the app schema.",
}

# Record schema for ArangoDB
record_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "orgId": {"type": "string"},
            "recordName": {"type": "string", "minLength": 1},
            # should be a uuid
            "externalRecordId": {"type": "string", "minLength": 1},
            "externalRevisionId": {"type": ["string", "null"]},
            "recordType": {"type": "string", "enum": ["FILE", "DRIVE", "WEBPAGE", "MESSAGE", "MAIL", "OTHERS"]},
            "version": {"type": "number", "default": 0},
            "origin": {"type": "string", "enum": ["UPLOAD", "CONNECTOR"]},
            "connectorName": {"type": "string", "enum": ["ONEDRIVE", "DRIVE", "CONFLUENCE", "GMAIL", "SLACK"]},

            # Arango collection entry
            "createdAtTimestamp": {"type": "number"},
            # Arango collection entry
            "updatedAtTimestamp": {"type": "number"},
            "lastSyncTimestamp": {"type": "number"},

            "sourceCreatedAtTimestamp": {"type": ["number", "null"]},
            "sourceLastModifiedTimestamp": {"type": ["number", "null"]},

            "isDeleted": {"type": "boolean", "default": False},
            "isArchived": {"type": "boolean", "default": False},
            "deletedByUserId" :{"type":"string"},

            "lastIndexTimestamp": {"type": ["number", "null"]},
            "lastExtractionTimestamp": {"type": ["number", "null"]},
            "indexingStatus": {"type": "string", "enum": ["NOT_STARTED", "IN_PROGRESS", "FAILED", "COMPLETED", "FILE_TYPE_NOT_SUPPORTED"]},
            "extractionStatus": {"type": "string", "enum": ["NOT_STARTED", "IN_PROGRESS", "FAILED", "COMPLETED"]},
            "isLatestVersion": {"type": "boolean", "default": True},
            "isDirty": {"type": "boolean", "default": False}, # needs re indexing
            "reason": {"type": ["string", "null"]}  # fail reason, didn't index reason
        },
        "required": ["recordName", "externalRecordId", "recordType", "origin", "createdAtTimestamp"],
        "additionalProperties": False,
    },
    "level": "strict",
    "message": "Document does not match the record schema.",
}

# File Record schema for ArangoDB
file_record_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "orgId": {"type": "string"},
            "name": {"type": "string", "minLength": 1},
            "isFile": {"type": "boolean"},
            "extension": {"type": ["string", "null"]},
            "mimeType": {"type": ["string", "null"]},
            "sizeInBytes": {"type": "number"},
            "webUrl": {"type": "string"},
            "etag": {"type": ["string", "null"]},
            "ctag": {"type": ["string", "null"]},
            "md5Checksum": {"type": ["string", "null"]},
            "quickXorHash": {"type": ["string", "null"]},
            "crc32Hash": {"type": ["string", "null"]},
            "sha1Hash": {"type": ["string", "null"]},
            "sha256Hash": {"type": ["string", "null"]},
            "path": {"type": ["string", "null"]}
        },
        "required": ["name"],
        "additionalProperties": False,
    },
    "level": "strict",
    "message": "Document does not match the file record schema.",
}

drive_record_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "isFile": {"type": "boolean"},
        },
    },
}

mail_record_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "threadId": {"type": "string"},
            "isParent": {"type": "boolean", "default": False},
            "internalDate": {"type": "string"},
            "subject": {"type": "string"},
            "date": {"type": "string"},
            "from": {"type": "string"},
            "to": {
                "type": "array", 
                "items": {"type": "string", "minLength": 0},
                "default": []
            },
            "cc": {
                "type": "array", 
                "items": {"type": "string", "minLength": 0},
                "default": []
            },
            "bcc": {
                "type": "array", 
                "items": {"type": "string", "minLength": 0},
                "default": []
            },
            "messageIdHeader": {"type": ["string", "null"]},
            "historyId": {"type": "string"},
            "webUrl": {"type": "string"},
            "labelIds": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["threadId", "isParent"],
        "additionalProperties": False,
    },
    "level": "strict",
    "message": "Document does not match the mail record schema.",
}

record_group_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "orgId": {"type": "string"},
            "groupName": {"type": "string", "minLength": 1},
            # should be a uuid
            "externalGroupId": {"type": "string", "minLength": 1},
            "groupType": {"type": "string", "enum": ["SLACK_CHANNEL", "CONFLUENCE_SPACES"]},
            "connectorName": {"type": "string", "enum": ["ONEDRIVE", "DRIVE", "CONFLUENCE", "SLACK"]},

            "createdAtTimestamp": {"type": "number"},  # Arango record entry
            "updatedAtTimestamp": {"type": "number"},  # Arango record entry
            "lastSyncTimestampstamp": {"type": "number"},  # Arango record entry

            "isDeletedAtSource": {"type": "boolean", "default": False},
            "deletedAtSourceTimestamp": {"type": "number"},
            "sourceCreatedAtTimestamp": {"type": "number"},
            "sourceLastModifiedTimestamp": {"type": "number"},
        },
        "required": ["groupName", "externalGroupId", "groupType", "connectorName", "createdAtTimestamp"],
        "additionalProperties": False,
    },
    "level": "strict",
    "message": "Document does not match the record group schema.",
}

department_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "departmentName": {"type": "string", "minLength": 1},
            "orgId": {"type": ["string", "null"]},
        },
        "required": ["departmentName"],
        "additionalProperties": False,
    },
    "level": "strict",
    "message": "Document does not match the department schema."
}

kb_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "orgId": {"type": "string"},
            "name": { "type": "string", "default": "Default" },
            "createdAtTimestamp": { "type": "number" },
            "updatedAtTimestamp": { "type": "number" },
            "deletedAtTimestamp": { "type": "number" },
            "isDeleted": { "type": "boolean", "default": False },
            "isArchived": { "type": "boolean", "default": False },
        },
        "required": ["orgId"],
        "additionalProperties": False,
    },
    "level": "strict",
    "message": "Document does not match the department schema."
}