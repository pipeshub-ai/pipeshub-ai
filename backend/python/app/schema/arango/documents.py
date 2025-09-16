from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import RecordGroupType, RecordType

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
    "message": "Document does not match the user schema.",
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
            "connectorName": {
                "type": "string",
                "enum": [connector.value for connector in Connectors],
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
            "appDescription": {"type": "string"},
            "appCategories": {"type": "array", "items": {"type": "string"}},
            "authType": {"type": "string"},
            "config": {
                "type": "object",
                "properties": {
                    "auth": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["OAUTH", "OAUTH_ADMIN_CONSENT", "API_TOKEN", "USERNAME_PASSWORD", "BEARER_TOKEN", "CUSTOM"]
                            },
                            "displayRedirectUri": {"type": "boolean", "default": True},
                            "redirectUri": {"type": "string"},
                            "authorizeUrl": {"type": "string"},
                            "tokenUrl": {"type": "string"},
                            "scopes": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "fields": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string"},
                                                "displayName": {"type": "string"},
                                                "placeholder": {"type": "string"},
                                                "description": {"type": "string"},
                                                "fieldType": {
                                                    "type": "string",
                                                    "enum": ["TEXT", "PASSWORD", "EMAIL", "URL", "TEXTAREA", "SELECT", "MULTISELECT", "CHECKBOX", "NUMBER", "FILE"]
                                                },
                                                "required": {"type": "boolean", "default": False},
                                                "defaultValue": {},
                                                "options": {
                                                    "type": "array",
                                                    "items": {"type": "string"}
                                                },
                                                "validation": {
                                                    "type": "object",
                                                    "properties": {
                                                        "minLength": {"type": "number"},
                                                        "maxLength": {"type": "number"},
                                                        "pattern": {"type": "string"},
                                                        "format": {"type": "string"}
                                                    }
                                                },
                                                "isSecret": {"type": "boolean", "default": False}
                                            },
                                            "required": ["name", "displayName", "fieldType"]
                                        }
                                    }
                                },
                                "required": ["fields"]
                            },
                            "values": {
                                "type": "object",
                                "additionalProperties": True
                            },
                            "customFields": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "displayName": {"type": "string"},
                                        "description": {"type": "string"},
                                        "fieldType": {
                                            "type": "string",
                                            "enum": ["TEXT", "PASSWORD", "EMAIL", "URL", "TEXTAREA", "SELECT", "MULTISELECT", "CHECKBOX", "NUMBER", "FILE", "JSON"]
                                        },
                                        "required": {"type": "boolean", "default": False},
                                        "defaultValue": {},
                                        "options": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        },
                                        "validation": {
                                            "type": "object",
                                            "properties": {
                                                "minLength": {"type": "number"},
                                                "maxLength": {"type": "number"},
                                                "pattern": {"type": "string"},
                                                "format": {"type": "string"}
                                            }
                                        },
                                        "isSecret": {"type": "boolean", "default": False}
                                    },
                                    "required": ["name", "displayName", "fieldType"]
                                }
                            },
                            "customValues": {
                                "type": "object",
                                "additionalProperties": True
                            },
                            "conditionalDisplay": {
                                "type": "object",
                                "properties": {
                                    "redirectUri": {
                                        "type": "object",
                                        "properties": {
                                            "showWhen": {
                                                "type": "object",
                                                "properties": {
                                                    "field": {"type": "string"},
                                                    "operator": {
                                                        "type": "string",
                                                        "enum": ["equals", "not_equals", "contains", "not_contains", "greater_than", "less_than", "is_empty", "is_not_empty"]
                                                    },
                                                    "value": {}
                                                },
                                                "required": ["field", "operator"]
                                            }
                                        },
                                        "required": ["showWhen"]
                                    }
                                },
                                "additionalProperties": {
                                    "type": "object",
                                    "properties": {
                                        "showWhen": {
                                            "type": "object",
                                            "properties": {
                                                "field": {"type": "string"},
                                                "operator": {
                                                    "type": "string",
                                                    "enum": ["equals", "not_equals", "contains", "not_contains", "greater_than", "less_than", "is_empty", "is_not_empty"]
                                                },
                                                "value": {}
                                            },
                                            "required": ["field", "operator"]
                                        }
                                    },
                                    "required": ["showWhen"]
                                }
                            }
                        },
                        "required": ["type", "schema"]
                    },
                    "sync": {
                        "type": "object",
                        "properties": {
                            "supportedStrategies": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": ["WEBHOOK", "SCHEDULED", "MANUAL", "REALTIME"]
                                }
                            },
                            "selectedStrategy": {
                                "type": "string",
                                "enum": ["WEBHOOK", "SCHEDULED", "MANUAL", "REALTIME"]
                            },
                            "webhookConfig": {
                                "type": "object",
                                "properties": {
                                    "supported": {"type": "boolean", "default": False},
                                    "webhookUrl": {"type": "string"},
                                    "events": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    },
                                    "verificationToken": {"type": "string"},
                                    "secretKey": {"type": "string"}
                                }
                            },
                            "scheduledConfig": {
                                "type": "object",
                                "properties": {
                                    "intervalMinutes": {"type": "number", "default": 60},
                                    "cronExpression": {"type": "string"},
                                    "timezone": {"type": "string", "default": "UTC"},
                                    "startTime": {"type": "number"},
                                    "nextTime": {"type": "number"},
                                    "endTime": {"type": "number"},
                                    "maxRepetitions": {"type": "number", "default": 0},
                                    "repetitionCount": {"type": "number", "default": 0}
                                }
                            },
                            "realtimeConfig": {
                                "type": "object",
                                "properties": {
                                    "supported": {"type": "boolean", "default": False},
                                    "connectionType": {
                                        "type": "string",
                                        "enum": ["WEBSOCKET", "SSE", "POLLING"]
                                    }
                                }
                            },
                            "customFields": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "displayName": {"type": "string"},
                                        "description": {"type": "string"},
                                        "fieldType": {
                                            "type": "string",
                                            "enum": ["TEXT", "PASSWORD", "EMAIL", "URL", "TEXTAREA", "SELECT", "MULTISELECT", "CHECKBOX", "NUMBER", "FILE", "JSON"]
                                        },
                                        "required": {"type": "boolean", "default": False},
                                        "defaultValue": {},
                                        "options": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        },
                                        "validation": {
                                            "type": "object",
                                            "properties": {
                                                "minLength": {"type": "number"},
                                                "maxLength": {"type": "number"},
                                                "pattern": {"type": "string"},
                                                "format": {"type": "string"}
                                            }
                                        },
                                        "isSecret": {"type": "boolean", "default": False}
                                    },
                                    "required": ["name", "displayName", "fieldType"]
                                }
                            },
                            "customValues": {
                                "type": "object",
                                "additionalProperties": True
                            },
                            "values": {
                                "type": "object",
                                "additionalProperties": True
                            }
                        },
                        "required": ["supportedStrategies"]
                    },
                    "filters": {
                        "type": "object",
                        "properties": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "fields": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string"},
                                                "displayName": {"type": "string"},
                                                "description": {"type": "string"},
                                                "fieldType": {
                                                    "type": "string",
                                                    "enum": ["TEXT", "SELECT", "MULTISELECT", "DATE", "DATERANGE", "NUMBER", "BOOLEAN", "TAGS"]
                                                },
                                                "required": {"type": "boolean", "default": False},
                                                "defaultValue": {},
                                                "options": {
                                                    "type": "array",
                                                    "items": {"type": "string"}
                                                },
                                                "operators": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "string",
                                                        "enum": ["EQUALS", "NOT_EQUALS", "CONTAINS", "NOT_CONTAINS", "STARTS_WITH", "ENDS_WITH", "GREATER_THAN", "LESS_THAN", "IN", "NOT_IN"]
                                                    }
                                                }
                                            },
                                            "required": ["name", "displayName", "fieldType"]
                                        }
                                    }
                                }
                            },
                            "values": {
                                "type": "object",
                                "additionalProperties": True
                            },
                            "endpoints": {
                                "type": "object",
                                "additionalProperties": True
                            },
                            "customFields": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "displayName": {"type": "string"},
                                        "description": {"type": "string"},
                                        "fieldType": {
                                            "type": "string",
                                            "enum": ["TEXT", "SELECT", "MULTISELECT", "DATE", "DATERANGE", "NUMBER", "BOOLEAN", "TAGS", "TEXTAREA", "JSON"]
                                        },
                                        "required": {"type": "boolean", "default": False},
                                        "defaultValue": {},
                                        "options": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        }
                                    },
                                    "required": ["name", "displayName", "fieldType"]
                                }
                            },
                            "customValues": {
                                "type": "object",
                                "additionalProperties": True
                            }
                        }
                    },
                    "credentials": {
                        "type": "object",
                        "properties": {
                            "access_token": {"type": ["string", "null"]},
                            "refresh_token": {"type": ["string", "null"]},
                            "token_type": {"type": ["string", "null"]},
                            "expires_in": {"type": ["number", "null"]},
                            "scope": {"type": ["string", "null"]},
                            "created_at": {"type": ["string", "null"]}
                        },
                        "additionalProperties": True
                    },
                    "oauth": {
                        "type": "object",
                        "properties": {
                            "state": {"type": ["string", "null"]}
                        },
                        "additionalProperties": True
                    },
                    "iconPath": {"type": "string"},
                    "supportsRealtime": {"type": "boolean", "default": False},
                    "supportsSync": {"type": "boolean", "default": False},
                    "documentationLinks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "url": {"type": "string"},
                                "type": {"type": "string", "enum": ["setup", "api", "connector"]}
                            },
                            "required": ["title", "url", "type"]
                        }
                    }
                },
                "required": ["auth", "sync"],
                "additionalProperties": False
            },
            "isActive": {"type": "boolean", "default": True},
            "isConfigured": {"type": "boolean", "default": False},
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
            "externalGroupId": {"type": ["string", "null"]},
            "externalParentId": {"type": ["string", "null"]},
            "externalRevisionId": {"type": ["string", "null"], "default": None},
            "externalRootGroupId": {"type": ["string", "null"]},
            "recordType": {
                "type": "string",
                "enum": [record_type.value for record_type in RecordType],
            },
            "version": {"type": "number", "default": 0},
            "origin": {"type": "string", "enum": [origin.value for origin in OriginTypes]},
            "connectorName": {
                "type": "string",
                "enum": [connector.value for connector in Connectors],
            },
            "mimeType": {"type": ["string", "null"], "default": None},
            "webUrl": {"type": ["string", "null"]},
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
            "recordGroupId": {"type":"string"},  # kb id
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

webpage_record_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "orgId": {"type": "string"},
            "domain": {"type": ["string", "null"]},
        },
        "additionalProperties": False,
    },
    "level": "strict",
    "message": "Document does not match the webpage record schema.",
}

ticket_record_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "orgId": {"type": "string"},
            "summary": {"type": ["string", "null"]},
            "description": {"type": ["string", "null"]},
            "status": {"type": ["string", "null"]},
            "priority": {"type": ["string", "null"]},
            "assignee": {"type": ["string", "null"]},
            "reporterEmail": {"type": ["string", "null"]},
            "assigneeEmail": {"type": ["string", "null"]},
            "creatorEmail": {"type": ["string", "null"]},
            "creatorName": {"type": ["string", "null"]},
        },
    },
}

record_group_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "orgId": {"type": "string"},
            "groupName": {"type": "string", "minLength": 1},
            "shortName": {"type": ["string", "null"]},
            "description": {"type": ["string", "null"]},
            # should be a uuid
            "externalGroupId": {"type": "string", "minLength": 1},
            "externalRevisionId": {"type": ["string", "null"], "default": None},
            "groupType": {
                "type": "string",
                "enum": [group_type.value for group_type in RecordGroupType],
            },
            "connectorName": {
                "type": "string",
                "enum": [connector.value for connector in Connectors],
            },
            "parentExternalGroupId": {"type": ["string", "null"]},
            "webUrl": {"type": ["string", "null"]},
            "createdBy":{"type": ["string", "null"]},
            "deletedByUserId":{"type": ["string", "null"]},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
            "lastSyncTimestamp": {"type": "number"},
            "isDeletedAtSource": {"type": "boolean", "default": False},
            "deletedAtSourceTimestamp": {"type": ["number", "null"]},
            "sourceCreatedAtTimestamp": {"type": ["number", "null"]},
            "sourceLastModifiedTimestamp": {"type": ["number", "null"]},
        },
        "required": [
            "groupName",
            # "externalGroupId",
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


agent_template_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string", "minLength": 1},
            "startMessage": {"type": "string", "minLength": 1},
            "systemPrompt": {"type": "string", "minLength": 1},
            "tools": {
                "type": "array",
                "items" :{
                    "type": "object",
                    "properties": {
                        #scoped name for tool wild card (google.* (all tools for google)) (e.g. app_name.tool_name, google.search)
                        "name": {"type": "string", "minLength": 1},
                        "description": {"type": "string", "minLength": 1},
                    },
                    "nullable": True,
                    "required": ["name"],
                    "additionalProperties": True,
                },
                "default": [],
            },
            "models": {
                "type": "array",
                "items" :{
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "role": {"type": "string", "minLength": 1},
                        "provider": {"type": "string", "minLength": 1},
                        "config": {"type": "object"},
                    },
                    "nullable": True,
                    "required": ["name", "role", "provider"],
                    "additionalProperties": True,
                },
                "default": [],
            },
            "memory": {
                "type": "object",
                "properties": {
                    "type": {"type": "array", "items": {"type": "string", "enum": ["CONVERSATIONS", "KNOWLEDGE_BASE", "APPS", "ACTIVITIES", "VECTOR_DB"]}},
                },
                "nullable": True,
                "required": ["type"],
                "additionalProperties": True,
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "orgId": {"type": ["string", "null"]},
            "isActive": {"type": "boolean", "default": True},
            "createdBy": {"type": ["string", "null"]},
            "updatedByUserId": {"type": ["string", "null"]},
            "deletedByUserId": {"type": ["string", "null"]},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": ["number", "null"]},
            "deletedAtTimestamp": {"type": ["number", "null"]},
            "isDeleted": {"type": "boolean", "default": False},
        },
        "required": ["name", "description", "startMessage", "systemPrompt"],
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the agent template schema.",
}

agent_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string", "minLength": 1},
            "startMessage": {"type": "string", "minLength": 1},
            "systemPrompt": {"type": "string", "minLength": 1},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "models": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "provider": {"type": "string"},
                    "modelName": {"type": "string"},
                },
                "required": ["provider", "modelName"],
                "additionalProperties": True,
                },
                "default": [],
            },
            "apps": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "kb": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "vectorDBs": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    },
                    "required": ["id", "name"],
                    "additionalProperties": True,
                },
                "default": [],
            },
            "isActive": {"type": "boolean", "default": True},
            "createdBy": {"type": ["string", "null"]},
            "updatedByUserId": {"type": ["string", "null"]},
            "deletedByUserId": {"type": ["string", "null"]},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
            "deletedAtTimestamp": {"type": "number"},
            "isDeleted": {"type": "boolean", "default": False},
        },
        "required": ["name", "description", "startMessage", "systemPrompt", "tools", "models"],
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the agent schema.",
}

team_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string", "minLength": 1},
            "orgId": {"type": ["string", "null"]},
            "createdBy": {"type": ["string", "null"]},
            "updatedByUserId": {"type": ["string", "null"]},
            "deletedByUserId": {"type": ["string", "null"]},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
            "deletedAtTimestamp": {"type": "number"},
            "isDeleted": {"type": "boolean", "default": False},
        },
        "required": ["name", "description"],
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the team schema.",
}

# future schema

# agent_template_schema = {
#     "rule": {
#         "type": "object",
#         "properties": {
#             "name": {"type": "string", "minLength": 1},
#             "description": {"type": "string", "minLength": 1},
#             "startMessage": {"type": "string", "minLength": 1},
#             "systemPrompt": {"type": "string", "minLength": 1},
#             "tools": {
#                 "type": "array",
#                 "items" :{
#                     "type": "object",
#                     "properties": {
#                         #scoped name for tool wild card (google.* (all tools for google)) (e.g. app_name.tool_name, google.search)
#                         "name": {"type": "string", "minLength": 1},
#                         "description": {"type": "string", "minLength": 1},
#                         "config": {"type": "object"},
#                     },
#                     "required": ["name"],
#                     "additionalProperties": True,
#                 }
#             },
#             "models": {
#                 "type": "array",
#                 "items" :{
#                     "type": "object",
#                     "properties": {
#                         "name": {"type": "string", "minLength": 1},
#                         "role": {"type": "string", "minLength": 1},
#                         "provider": {"type": "string", "minLength": 1},
#                         "config": {"type": "object"},
#                     },
#                     "required": ["name", "role", "provider"],
#                     "additionalProperties": True,
#                 }
#             },
#             "actions": {
#                 "type": "array",
#                 # scoped action name (e.g. app_name.action_name, google.search)
#                 "items": {
#                     "type": "object",
#                     "properties": {
#                         "name": {"type": "string", "minLength": 1},
#                         # add self approval option and add user id in approvers
#                         "approvers": {"type": "array", "items":{
#                             "type": "object",
#                             "properties": {
#                                 "userId": {"type": "array", "items": {"type": "string"}},
#                                 "userGroupsIds": {"type": "array", "items": {"type": "string"}},
#                                 "order": {"type": "number"},
#                             },
#                             "required": ["userId", "order"],
#                             "additionalProperties": True,
#                         }},
#                         "reviewers": {"type": "array", "items":{
#                             "type": "object",
#                             "properties": {
#                                 "userId": {"type": "array", "items": {"type": "string"}},
#                                 "userGroupsIds": {"type": "array", "items": {"type": "string"}},
#                                 "order": {"type": "number"},
#                             },
#                             "required": ["userId", "order"],
#                             "additionalProperties": True,
#                         }},
#                     },
#                     "required": ["name", "approvers", "reviewers"],
#                     "additionalProperties": True,
#                 },
#                 "default": [],
#             },
#             "memory": {
#                 "type": "object",
#                 "properties": {
#                     "type": {"type": "array", "items": {"type": "string", "enum": ["CONVERSATIONS", "KNOWLEDGE_BASE", "APPS", "ACTIVITIES", "VECTOR_DB"]}},
#                 },
#                 "required": ["type"],
#                 "additionalProperties": True,
#             },
#             "tags": {
#                 "type": "array",
#                 "items": {"type": "string"},
#                 "default": [],
#             },
#             "orgId": {"type": ["string", "null"]},
#             "isActive": {"type": "boolean", "default": True},
#             "createdBy": {"type": ["string", "null"]},
#             "updatedByUserId": {"type": ["string", "null"]},
#             "deletedByUserId": {"type": ["string", "null"]},
#             "createdAtTimestamp": {"type": "number"},
#             "updatedAtTimestamp": {"type": "number"},
#             "deletedAtTimestamp": {"type": "number"},
#             "isDeleted": {"type": "boolean", "default": False},
#         },
#         "required": ["name", "description", "startMessage", "systemPrompt", "tools", "models", "apps", "knowledgeBases"],
#         "additionalProperties": True,
#     },
#     "level": "strict",
#     "message": "Document does not match the agent template schema.",
# }

# agent_schema = {
#     "rule": {
#         "type": "object",
#         "properties": {
#             "name": {"type": "string", "minLength": 1},
#             "description": {"type": "string", "minLength": 1},
#             "templateKey": {"type": "string", "minLength": 1},
#             "systemPrompt": {"type": "string", "minLength": 1},
#             "startingMessage": {"type": "string", "minLength": 1},
#             "tags": {
#                 "type": "array",
#                 "items": {"type": "string"},
#                 "default": [],
#             },
#             "isActive": {"type": "boolean", "default": True},
#             "createdBy": {"type": ["string", "null"]},
#             "updatedByUserId": {"type": ["string", "null"]},
#             "deletedByUserId": {"type": ["string", "null"]},
#             "createdAtTimestamp": {"type": "number"},
#             "updatedAtTimestamp": {"type": "number"},
#             "deletedAtTimestamp": {"type": "number"},
#             "isDeleted": {"type": "boolean", "default": False},
#         },
#         "required": ["name", "description", "templateKey", "tools", "models", "apps", "knowledgeBases"],
#         "additionalProperties": True,
#     },
#     "level": "strict",
#     "message": "Document does not match the agent schema.",
# }

# tool_schema = {
#     "rule": {
#         "type": "object",
#         "properties": {
#             # scoped name for tool (e.g. app_name.tool_name, google.search)
#             "name": {"type": "string", "minLength": 1},
#             "vendorName": {"type": "string", "minLength": 1},
#             "description": {"type": "string", "minLength": 1},
#             "isActive": {"type": "boolean", "default": True},
#             "createdByUserId": {"type": ["string", "null"]},
#             "updatedByUserId": {"type": ["string", "null"]},
#             "deletedByUserId": {"type": ["string", "null"]},
#             "orgId": {"type": ["string", "null"]},
#             "createdAtTimestamp": {"type": "number"},
#             "updatedAtTimestamp": {"type": "number"},
#             "deletedAtTimestamp": {"type": "number"},
#             "isDeleted": {"type": "boolean", "default": False},
#         },
#         "required": ["name", "description", "vendorName"],
#         "additionalProperties": True,
#     },
#     "level": "strict",
#     "message": "Document does not match the tool schema.",
# }

# # AI Models Schema
# ai_model_schema = {
#     "rule": {
#         "type": "object",
#         "properties": {
#             "name": {"type": "string", "minLength": 1},
#             "description": {"type": "string", "minLength": 1},
#             "provider": {
#                 "type": "string",
#                 "enum": [
#                     "OPENAI", "AZURE_OPENAI", "ANTHROPIC", "GOOGLE", "COHERE",
#                     "MISTRAL", "OLLAMA", "BEDROCK", "GEMINI", "GROQ", "TOGETHER",
#                     "FIREWORKS", "XAI", "VERTEX_AI", "CUSTOM"
#                 ]
#             },
#             "modelType": {
#                 "type": "string",
#                 "enum": ["LLM", "EMBEDDING", "OCR", "SLM", "REASONING", "MULTIMODAL"]
#             },
#             "orgId": {"type": ["string", "null"]},
#             "createdAtTimestamp": {"type": "number"},
#             "updatedAtTimestamp": {"type": "number"},
#             "deletedAtTimestamp": {"type": "number"},
#             "isDeleted": {"type": "boolean", "default": False},
#         },
#         "required": ["name", "description", "modelKey", "provider", "modelType"],
#         "additionalProperties": True,
#     },
#     "level": "strict",
#     "message": "Document does not match the AI model schema.",
# }

# # App Actions Schema
# app_action_schema = {
#     "rule": {
#         "type": "object",
#         "properties": {
#             # scoped name for action (e.g. app_name.action_name, drive.upload, gmail.send, etc. )
#             "name": {"type": "string", "minLength": 1},
#             "description": {"type": "string", "minLength": 1},
#             "orgId": {"type": ["string", "null"]},
#             "createdAtTimestamp": {"type": "number"},
#             "updatedAtTimestamp": {"type": "number"},
#             "deletedAtTimestamp": {"type": "number"},
#             "isDeleted": {"type": "boolean", "default": False},
#         },
#         "required": ["name", "description"],
#         "additionalProperties": True,
#     },
#     "level": "strict",
#     "message": "Document does not match the app action schema.",
# }

# # Conversation Schema
# conversation_schema = {
#     "rule": {
#         "type": "object",
#         "properties": {
#             "conversationDocId": {"type": "string", "minLength": 1},
#             "orgId": {"type": ["string", "null"]},
#             "createdAtTimestamp": {"type": "number"},
#             "updatedAtTimestamp": {"type": "number"},
#             "deletedAtTimestamp": {"type": "number"},
#             "isDeleted": {"type": "boolean", "default": False},
#         },
#         "required": ["conversationDocId"],
#     },
#     "level": "strict",
#     "message": "Document does not match the conversation schema.",
# }

# # task schema
# task_schema = {
#     "rule": {
#         "type": "object",
#         "properties": {
#             "orgId": {"type": ["string", "null"]},
#             "name": {"type": "string", "minLength": 1},
#             "description": {"type": "string", "minLength": 1},
#             "priority": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
#             "createdAtTimestamp": {"type": "number"},
#             "updatedAtTimestamp": {"type": "number"},
#             "deletedAtTimestamp": {"type": "number"},
#             "isDeleted": {"type": "boolean", "default": False},
#         },
#     },
# }

# # workflow schema
# workflow_schema = {
#     "rule": {
#         "type": "object",
#         "properties": {
#             "orgId": {"type": ["string", "null"]},
#             "name": {"type": "string", "minLength": 1},
#             "description": {"type": "string", "minLength": 1},
#             "taskCounts": {"type": "number"},
#             "createdBy": {"type": ["string", "null"]},
#             "updatedByUserId": {"type": ["string", "null"]},
#             "deletedByUserId": {"type": ["string", "null"]},
#             "createdAtTimestamp": {"type": "number"},
#             "updatedAtTimestamp": {"type": "number"},
#             "deletedAtTimestamp": {"type": "number"},
#             "isDeleted": {"type": "boolean", "default": False},
#         },
#         "required": ["name", "description", "taskCounts"],
#         "additionalProperties": True,
#     },
#     "level": "strict",
#     "message": "Document does not match the workflow schema.",
# }
