# User schema for ArangoDB
orgs_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_key": {"type": "string"},  # orgId
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
    "message": "Document does not match the organization schema.",
}

user_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_key": {"type": "string"},  # uuid
            "userId": {"type": "string"},
            "orgId": {"type": "string"},
            "firstName": {"type": "string"},
            "middleName": {"type": "string"},
            "lastName": {"type": "string"},
            "fullName": {"type": "string"},
            "email": {"type": "string", "format": "email"},
            "designation": {"type": "string"},
            "businessPhones": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 0,
            },
            "isActive": {"type": "boolean", "default": False},
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
            "groupType": {
                "type": "string",
                "enum": ["MS_USER_GROUPS", "GOOGLE_USER_GROUPS"],
            },
            "connectorName": {
                "type": "string",
                "enum": ["ONEDRIVE", "DRIVE", "GMAIL", "CONFLUENCE", "SLACK"],
            },
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
        "required": [
            "groupName",
            "externalGroupId",
            "recordType",
            "groupType",
            "connectorName",
            "createdAtTimestamp",
        ],
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
        "required": [
            "name",
            "type",
            "appGroup",
            "appGroupId",
            "isActive",
            "createdAtTimestamp",
            "updatedAtTimestamp",
        ],
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
            "recordType": {
                "type": "string",
                "enum": ["FILE", "DRIVE", "WEBPAGE", "MESSAGE", "MAIL", "OTHERS", "DATABASE", "PAGE_COMMENTS"],
            },
            "version": {"type": "number", "default": 0},
            "origin": {"type": "string", "enum": ["UPLOAD", "CONNECTOR"]},
            "connectorName": {
                "type": "string",
                "enum": ["ONEDRIVE", "DRIVE", "CONFLUENCE", "GMAIL", "SLACK","NOTION"],
            },
            # Arango collection entry
            "createdAtTimestamp": {"type": "number"},
            # Arango collection entry
            "updatedAtTimestamp": {"type": "number"},
            "lastSyncTimestamp": {"type": ["number", "null"]},
            "sourceCreatedAtTimestamp": {"type": ["number", "null"]},
            "sourceLastModifiedTimestamp": {"type": ["number", "null"]},
            "isDeleted": {"type": "boolean", "default": False},
            "isArchived": {"type": "boolean", "default": False},
            "deletedByUserId": {"type": ["string", "null"]},
            "indexingStatus": {
                "type": "string",
                "enum": [
                    "NOT_STARTED",
                    "IN_PROGRESS",
                    "PAUSED",
                    "FAILED",
                    "COMPLETED",
                    "FILE_TYPE_NOT_SUPPORTED",
                    "AUTO_INDEX_OFF"
                ],
            },
            "extractionStatus": {
                "type": "string",
                "enum": [
                    "NOT_STARTED",
                    "IN_PROGRESS",
                    "PAUSED",
                    "FAILED",
                    "COMPLETED",
                    "FILE_TYPE_NOT_SUPPORTED",
                    "AUTO_INDEX_OFF"
                ],
            },
            "isLatestVersion": {"type": "boolean", "default": True},
            "isDirty": {"type": "boolean", "default": False},  # needs re indexing
            "reason": {"type": ["string", "null"]},  # fail reason, didn't index reason
            "lastIndexTimestamp": {"type": ["number", "null"]},
            "lastExtractionTimestamp": {"type": ["number", "null"]},
            "summaryDocumentId": {"type": ["string", "null"]},
            "virtualRecordId": {"type": ["string", "null"], "default": None},
        },
        "required": [
            "recordName",
            "externalRecordId",
            "recordType",
            "origin",
            "createdAtTimestamp"
        ],
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
            "path": {"type": ["string", "null"]},
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
                "default": [],
            },
            "cc": {
                "type": "array",
                "items": {"type": "string", "minLength": 0},
                "default": [],
            },
            "bcc": {
                "type": "array",
                "items": {"type": "string", "minLength": 0},
                "default": [],
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
            "groupType": {
                "type": "string",
                "enum": ["SLACK_CHANNEL", "CONFLUENCE_SPACES","NOTION_WORKSPACE"],
            },
            "connectorName": {
                "type": "string",
                "enum": ["ONEDRIVE", "DRIVE", "CONFLUENCE", "SLACK","NOTION"],
            },
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
            "lastSyncTimestamp": {"type": "number"},
            "isDeletedAtSource": {"type": "boolean", "default": False},
            "deletedAtSourceTimestamp": {"type": "number"},
            "sourceCreatedAtTimestamp": {"type": "number"},
            "sourceLastModifiedTimestamp": {"type": "number"},
        },
        "required": [
            "groupName",
            "externalGroupId",
            "groupType",
            "connectorName",
            "createdAtTimestamp",
        ],
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
    "message": "Document does not match the department schema.",
}

kb_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "userId":{"type" : "string"},
            "orgId": {"type": "string"},
            "name": {"type": "string", "default": "Default"},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
            "deletedAtTimestamp": {"type": "number"},
            "isDeleted": {"type": "boolean", "default": False},
            "isArchived": {"type": "boolean", "default": False},
        },
        "required": ["orgId"],
        "additionalProperties": False,
    },
    "level": "strict",
    "message": "Document does not match the department schema.",
}

notion_page_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "page_id": {"type": "string", "minLength": 1},  # Notion's UUID for the page
            "title": {"type": "string"},  # Extracted page title
            "url": {"type": "string", "format": "uri"},  # Notion page URL
            "parent": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["database_id", "page_id", "workspace", "block_id"]
                    },
                    "database_id": {"type": "string"},
                    "page_id": {"type": "string"},
                    "workspace": {"type": "boolean"},
                    "block_id": {"type": "string"}
                },
                "additionalProperties": False
            },

            "isArchived": {"type": "boolean", "default": False},  # Whether the page is archived in Notion
            "isDeleted": {"type": "boolean", "default": False},  # Whether the page is deleted in your system
            "createdAtTimestamp":{"type":"number"},
            "deletedAtTimestamp": {"type": "number"},  # When the page was deleted in your system
            "hasChildren": {"type": "boolean"},  # Whether the page has child bloc
            "propertyValues": {
                "type": "object",
                "additionalProperties": True  # Allow any database properties
            },
            "rawData": {
                "type": "object",
                "additionalProperties": True  # Store the complete raw JSON from Notion API
            }
        },
        "required": [
            "page_id",
            "title",
        ],
        "additionalProperties": False
    },
    "level": "strict",
    "message": "Document does not match the Notion page schema."
}

notion_database_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "database_id": {"type": "string", "minLength": 1},  # Notion's UUID for the database
            "title": {"type": "string"},  # Extracted database title
            "url": {"type": "string", "format": "uri"},  # Notion database URL
            "parent": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["page_id", "workspace", "block_id"]
                    },
                    "page_id": {"type": "string"},
                    "workspace": {"type": "boolean"},
                    "block_id": {"type": "string"}
                },
                "additionalProperties": False
            },
            "isArchived": {"type": "boolean", "default": False},  # Whether the database is archived in Notion
            "isDeleted": {"type": "boolean", "default": False},  # Whether the database is deleted in your system
            "createdAtTimestamp": {"type": "number"},
            "deletedAtTimestamp": {"type": "number"},  # When the database was deleted in your system
            "properties": {
                "type": "object",
                "additionalProperties": True  # Database schema/properties definition
            },
            "rawData": {
                "type": "object",
                "additionalProperties": True  # Store the complete raw JSON from Notion API
            }
        },
        "required": [
            "database_id",
            "title",
        ],
        "additionalProperties": False
    },
    "level": "strict",
    "message": "Document does not match the Notion database schema."
}

notion_comments_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "page_id": {"type": "string", "minLength": 1},  # The page these comments belong to
            "comments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "comment_id": {"type": "string", "minLength": 1},  # Notion's UUID for the comment
                        "text": {"type": "string"},  # Comment text content
                        "createdBy": {"type": "string"},  # Who created the comment
                        "createdAtTimestamp": {"type": "number"},  # When comment was created
                        "deletedAtTimestamp": {"type": "number"}  # When comment was deleted (optional)
                    },
                    "required": [
                        "comment_id",
                    ],
                    "additionalProperties": False
                },
                "minItems": 0
            }
        },
        "required": [
            "page_id",
            "comments"
        ],
        "additionalProperties": False
    },
    "level": "strict",
    "message": "Document does not match the Notion comments schema."
}

