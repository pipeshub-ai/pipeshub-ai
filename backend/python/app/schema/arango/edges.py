from enum import Enum
from typing import List, Type

from app.config.constants.arangodb import (
    EntityRelations,
    RecordRelations,
)


def _get_enum_values(enum_class: Type[Enum]) -> List[str]:
    """Helper function to extract enum values from an enum class"""
    return [item.value for item in enum_class]


record_relations_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "relationshipType": {
                "type": "string",
                "enum": _get_enum_values(RecordRelations),
            },
            "customRelationshipTag": {
                "type": "string",
                "description": "Optional custom relationship tag (use relationshipType directly instead of this field)"
            },
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the file relations schema.",
}

entity_relations_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "edgeType": {
                "type": "string",
                "enum": _get_enum_values(EntityRelations),
            },
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
            "sourceTimestamp": {"type": "number"},
        },
        "required": ["edgeType", "createdAtTimestamp"],
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the entity relations schema.",
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

# Org -> Org: Salesforce prospect relationship (Account start to first won opportunity)
sales_prospect_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "rating": {"type": ["string", "null"]},
            "type": {"type": ["string", "null"]},
            "externalId": {"type": ["string", "null"]},
            "startTime": {"type": ["number", "null"]}, # When Account was made in Salesforce (epoch ms)
            "endTime": {"type": ["number", "null"]}, # When first opportunity was won (epoch ms)
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the sales prospect schema.",
}

# Org -> Org: Salesforce customer relationship
sales_customer_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "rating": {"type": ["string", "null"]},
            "type": {"type": ["string", "null"]},
            "activeCustomer": {"type": "boolean"},
            "externalId": {"type": ["string", "null"]},
            "since": {"type": ["number", "null"]}, # When customer relationship started (epoch ms)
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the sales customer schema.",
}

# Org -> Person: Salesforce lead (until converted to contact)
sales_lead_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "company": {"type": ["string", "null"]},
            "title": {"type": ["string", "null"]},
            "status": {"type": ["string", "null"]},
            "rating": {"type": ["string", "null"]},
            "industry": {"type": ["string", "null"]},
            "leadSource": {"type": ["string", "null"]},
            "annualRevenue": {"type": ["number", "null"]},
            "externalId": {"type": ["string", "null"]},
            "startTime": {"type": ["number", "null"]},  # When contact was made in Salesforce (epoch ms)
            "endTime": {"type": ["number", "null"]},   # When lead was converted to contact (epoch ms)
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the sales lead schema.",
}

# Org -> Person: Salesforce contact relationship
sales_contact_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "description": {"type": ["string", "null"]},
            "leadSource": {"type": ["string", "null"]},
            "since": {"type": ["number", "null"]},  # When contact was made in Salesforce (epoch ms)
            "externalId": {"type": ["string", "null"]},  # Salesforce Contact Id for mapping leads
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the sales contact schema.",
}

# Org -> Deal: Salesforce opportunity/deal relationship
sales_deal_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "stage": {"type": ["string", "null"]},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the sales deal schema.",
}

# Deal -> Org: deal belongs to organization
deal_of_schema = {
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
    "message": "Document does not match the dealOf schema.",
}

# Product -> Deal: product sold in deal (parallel arrays of primitives per OLI)
sold_in_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "quantities": {
                "type": "array",
                "items": {"type": ["number", "null"]},
            },
            "unitPrices": {
                "type": "array",
                "items": {"type": ["number", "null"]},
            },
            "totalPrices": {
                "type": "array",
                "items": {"type": ["number", "null"]},
            },
            "isDeletedFlags": {
                "type": "array",
                "items": {"type": "boolean"},
            },
            "sourceUpdatedAtTimestamp": {"type": ["number", "null"]},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the soldIn schema.",
}

# Person -> Org: membership (Title, Department)
member_of_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "title": {"type": ["string", "null"]},
            "department": {"type": ["string", "null"]},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the memberOf schema.",
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

# Agent -> Toolset
agent_has_toolset_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "required": ["createdAtTimestamp"],
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the agent has toolset schema.",
}

# Agent -> Knowledge
agent_has_knowledge_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "required": ["createdAtTimestamp"],
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the agent has knowledge schema.",
}

# Toolset -> Tool
toolset_has_tool_schema = {
    "rule": {
        "type": "object",
        "properties": {
            "_from": {"type": "string", "minLength": 1},
            "_to": {"type": "string", "minLength": 1},
            "createdAtTimestamp": {"type": "number"},
            "updatedAtTimestamp": {"type": "number"},
        },
        "required": ["createdAtTimestamp"],
        "additionalProperties": True,
    },
    "level": "strict",
    "message": "Document does not match the toolset has tool schema.",
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
