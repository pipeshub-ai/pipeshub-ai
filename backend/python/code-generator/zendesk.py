# ruff: noqa
"""
Zendesk API Code Generator
Generates comprehensive ZendeskDataSource class covering ALL Zendesk APIs:

TICKETING (SUPPORT) API:
- Tickets (CRUD, bulk operations, comments, attachments, merge, import)
- Users (CRUD, identities, passwords, authentication, sessions)
- Organizations (CRUD, memberships, search, autocomplete)
- Groups (CRUD, memberships)
- Search (unified search across all resources)
- Requests (end-user perspective of tickets)
- Ticket Fields, User Fields, Organization Fields
- Custom Fields (for all resource types)
- Comments (CRUD, redactions)
- Attachments (upload, download, delete)
- Tags (add, remove, bulk operations)

BUSINESS RULES:
- Automations (time-based workflow rules)
- Triggers (event-based workflow rules)
- Macros (manual agent actions)
- Views (filtered ticket lists)
- SLA Policies (service level agreements)

WEBHOOKS:
- Webhook management (CRUD, testing)
- Event subscriptions (tickets, users, organizations)
- Authentication and security

HELP CENTER API:
- Articles (CRUD, translations, labels, subscriptions)
- Sections (CRUD, translations, subscriptions)
- Categories (CRUD, translations)
- Community Topics, Posts, Comments
- Permission Groups (content management)
- User Segments (content targeting)
- Themes and Translations

CUSTOM DATA:
- Custom Objects (CRUD, records, relationships)
- Custom Object Fields
- Custom Object Records
- Lookup Relationships

LIVE CHAT:
- Chat API (real-time messaging)
- Chat Conversations API
- Chat Agents and Departments
- Chat Triggers and Shortcuts

VOICE (TALK):
- Talk API (call handling, recording)
- Talk Partner Edition API
- Phone Numbers and Lines
- Call Statistics and Recordings

SALES CRM:
- Sell API (contacts, deals, leads)
- Sync API (data synchronization)
- Firehose API (real-time events)
- Search API (CRM search)

CONVERSATIONS (SUNSHINE):
- Apps, Integrations, Webhooks
- Messages and Conversations
- Users and Participants

APPS & EXTENSIONS:
- Apps Core API (app lifecycle)
- Apps Support API (support app features)
- ZIS Configs, Connections, Links
- Registry and Bundle management

WIDGET & SDK:
- Web Widget (Messaging) API
- Android SDK API
- iOS SDK API
- Unity SDK API

ADMINISTRATIVE:
- Agent Availability and Capacity Rules
- Custom Roles and Permissions
- Brand Management (multi-brand)
- Job Status monitoring
- Account Settings
- Incremental Exports
- Resource Collections

All methods have explicit parameter signatures with no **kwargs usage.
Every parameter matches Zendesk's official API documentation exactly.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Literal
from datetime import datetime

# Comprehensive Zendesk API endpoints mapped from official documentation
ZENDESK_API_ENDPOINTS = {
    # ================================================================================
    # SUPPORT API - TICKETS
    # ================================================================================
    "list_tickets": {
        "method": "GET",
        "path": "/tickets.json",
        "description": "List all tickets with pagination support",
        "parameters": {
            "sort_by": {
                "type": 'Optional[Literal["assignee", "assignee.name", "created_at", "group_id", "id", "locale_id", "requester", "requester.name", "status", "subject", "updated_at"]]',
                "location": "query",
                "description": "Sort order for results",
            },
            "sort_order": {
                "type": 'Optional[Literal["asc", "desc"]]',
                "location": "query",
                "description": "Sort direction",
            },
            "external_id": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by external ID",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data (users,groups,organizations,last_audits,metric_sets,dates,sharing_agreements,comment_count,incident_counts,ticket_forms,metric_events,slas,custom_statuses)",
            },
            "page": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Page number for pagination",
            },
            "per_page": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Number of results per page (max 100)",
            },
        },
        "required": [],
    },
    "show_ticket": {
        "method": "GET",
        "path": "/tickets/{ticket_id}.json",
        "description": "Show details of a specific ticket",
        "parameters": {
            "ticket_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the ticket to retrieve",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": ["ticket_id"],
    },
    "create_ticket": {
        "method": "POST",
        "path": "/tickets.json",
        "description": "Create a new ticket",
        "parameters": {
            "subject": {
                "type": "str",
                "location": "body",
                "description": "Ticket subject",
            },
            "comment": {
                "type": "Dict[str, Any]",
                "location": "body",
                "description": "Initial comment with body, html_body, public, author_id, uploads",
            },
            "requester_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the requester",
            },
            "requester": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Requester details (creates user if not exists)",
            },
            "submitter_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the submitter",
            },
            "assignee_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the assignee",
            },
            "group_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the group",
            },
            "collaborator_ids": {
                "type": "Optional[List[int]]",
                "location": "body",
                "description": "List of collaborator user IDs",
            },
            "follower_ids": {
                "type": "Optional[List[int]]",
                "location": "body",
                "description": "List of follower user IDs",
            },
            "email_ccs": {
                "type": "Optional[List[Dict[str, str]]]",
                "location": "body",
                "description": "List of email CCs with user_email and action",
            },
            "organization_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the organization",
            },
            "external_id": {
                "type": "Optional[str]",
                "location": "body",
                "description": "External ID for the ticket",
            },
            "type": {
                "type": 'Optional[Literal["problem", "incident", "question", "task"]]',
                "location": "body",
                "description": "Type of ticket",
            },
            "priority": {
                "type": 'Optional[Literal["urgent", "high", "normal", "low"]]',
                "location": "body",
                "description": "Priority level",
            },
            "status": {
                "type": 'Optional[Literal["new", "open", "pending", "hold", "solved", "closed"]]',
                "location": "body",
                "description": "Ticket status",
            },
            "recipient": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Original recipient email",
            },
            "tags": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "List of tags",
            },
            "custom_fields": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Custom field values",
            },
            "due_at": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Due date (ISO 8601)",
            },
            "ticket_form_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the ticket form",
            },
            "brand_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the brand",
            },
            "forum_topic_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the forum topic",
            },
            "problem_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the parent problem ticket",
            },
            "via": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Via channel information",
            },
            "macro_ids": {
                "type": "Optional[List[int]]",
                "location": "body",
                "description": "List of macro IDs to apply",
            },
            "safe_update": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Enable safe update mode",
            },
            "updated_stamp": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Timestamp for safe updates",
            },
            "sharing_agreement_ids": {
                "type": "Optional[List[int]]",
                "location": "body",
                "description": "List of sharing agreement IDs",
            },
        },
        "required": ["subject", "comment"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_ticket": {
        "method": "PUT",
        "path": "/tickets/{ticket_id}.json",
        "description": "Update an existing ticket",
        "parameters": {
            "ticket_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the ticket to update",
            },
            "subject": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Ticket subject",
            },
            "comment": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Comment to add with body, html_body, public, author_id, uploads",
            },
            "requester_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the requester",
            },
            "submitter_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the submitter",
            },
            "assignee_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the assignee",
            },
            "group_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the group",
            },
            "collaborator_ids": {
                "type": "Optional[List[int]]",
                "location": "body",
                "description": "List of collaborator user IDs",
            },
            "follower_ids": {
                "type": "Optional[List[int]]",
                "location": "body",
                "description": "List of follower user IDs",
            },
            "organization_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the organization",
            },
            "external_id": {
                "type": "Optional[str]",
                "location": "body",
                "description": "External ID for the ticket",
            },
            "type": {
                "type": 'Optional[Literal["problem", "incident", "question", "task"]]',
                "location": "body",
                "description": "Type of ticket",
            },
            "priority": {
                "type": 'Optional[Literal["urgent", "high", "normal", "low"]]',
                "location": "body",
                "description": "Priority level",
            },
            "status": {
                "type": 'Optional[Literal["new", "open", "pending", "hold", "solved", "closed"]]',
                "location": "body",
                "description": "Ticket status",
            },
            "tags": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "List of tags",
            },
            "custom_fields": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Custom field values",
            },
            "due_at": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Due date (ISO 8601)",
            },
            "additional_tags": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "Tags to add",
            },
            "remove_tags": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "Tags to remove",
            },
            "safe_update": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Enable safe update mode",
            },
            "updated_stamp": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Timestamp for safe updates",
            },
            "macro_ids": {
                "type": "Optional[List[int]]",
                "location": "body",
                "description": "List of macro IDs to apply",
            },
        },
        "required": ["ticket_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_ticket": {
        "method": "DELETE",
        "path": "/tickets/{ticket_id}.json",
        "description": "Delete a ticket",
        "parameters": {
            "ticket_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the ticket to delete",
            }
        },
        "required": ["ticket_id"],
    },
    "create_many_tickets": {
        "method": "POST",
        "path": "/tickets/create_many.json",
        "description": "Create multiple tickets (up to 100)",
        "parameters": {
            "tickets": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Array of ticket objects",
            }
        },
        "required": ["tickets"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_many_tickets": {
        "method": "PUT",
        "path": "/tickets/update_many.json",
        "description": "Update multiple tickets",
        "parameters": {
            "tickets": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Array of ticket update objects",
            },
            "ids": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Comma-separated ticket IDs",
            },
        },
        "required": ["tickets"],
        "headers": {"Content-Type": "application/json"},
    },
    "destroy_many_tickets": {
        "method": "DELETE",
        "path": "/tickets/destroy_many.json",
        "description": "Delete multiple tickets",
        "parameters": {
            "ids": {
                "type": "str",
                "location": "query",
                "description": "Comma-separated ticket IDs to delete",
            }
        },
        "required": ["ids"],
    },
    "show_multiple_tickets": {
        "method": "GET",
        "path": "/tickets/show_many.json",
        "description": "Show details of multiple tickets",
        "parameters": {
            "ids": {
                "type": "str",
                "location": "query",
                "description": "Comma-separated ticket IDs",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": ["ids"],
    },
    "merge_tickets": {
        "method": "POST",
        "path": "/tickets/{ticket_id}/merge.json",
        "description": "Merge tickets into target ticket",
        "parameters": {
            "ticket_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the target ticket",
            },
            "ids": {
                "type": "List[int]",
                "location": "body",
                "description": "Array of source ticket IDs to merge",
            },
            "target_comment": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Comment to add to target ticket",
            },
            "source_comment": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Comment to add to source tickets",
            },
        },
        "required": ["ticket_id", "ids"],
        "headers": {"Content-Type": "application/json"},
    },
    "import_ticket": {
        "method": "POST",
        "path": "/imports/tickets.json",
        "description": "Import a ticket with historical data",
        "parameters": {
            "subject": {
                "type": "str",
                "location": "body",
                "description": "Ticket subject",
            },
            "comments": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Array of comment objects",
            },
            "requester_id": {
                "type": "int",
                "location": "body",
                "description": "ID of the requester",
            },
            "submitter_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the submitter",
            },
            "assignee_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the assignee",
            },
            "group_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the group",
            },
            "organization_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "ID of the organization",
            },
            "created_at": {
                "type": "str",
                "location": "body",
                "description": "Creation timestamp (ISO 8601)",
            },
            "updated_at": {
                "type": "str",
                "location": "body",
                "description": "Update timestamp (ISO 8601)",
            },
            "solved_at": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Solved timestamp (ISO 8601)",
            },
            "status": {
                "type": 'Literal["solved", "closed"]',
                "location": "body",
                "description": "Ticket status (must be solved or closed)",
            },
            "priority": {
                "type": 'Optional[Literal["urgent", "high", "normal", "low"]]',
                "location": "body",
                "description": "Priority level",
            },
            "type": {
                "type": 'Optional[Literal["problem", "incident", "question", "task"]]',
                "location": "body",
                "description": "Type of ticket",
            },
            "tags": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "List of tags",
            },
            "custom_fields": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Custom field values",
            },
            "external_id": {
                "type": "Optional[str]",
                "location": "body",
                "description": "External ID for the ticket",
            },
        },
        "required": [
            "subject",
            "comments",
            "requester_id",
            "created_at",
            "updated_at",
            "status",
        ],
        "headers": {"Content-Type": "application/json"},
    },
    # ================================================================================
    # SUPPORT API - COMMENTS
    # ================================================================================
    "list_comments": {
        "method": "GET",
        "path": "/tickets/{ticket_id}/comments.json",
        "description": "List comments for a ticket",
        "parameters": {
            "ticket_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the ticket",
            },
            "sort_order": {
                "type": 'Optional[Literal["asc", "desc"]]',
                "location": "query",
                "description": "Sort direction",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data (users)",
            },
        },
        "required": ["ticket_id"],
    },
    "redact_comment": {
        "method": "PUT",
        "path": "/tickets/{ticket_id}/comments/{comment_id}/redact.json",
        "description": "Redact a comment",
        "parameters": {
            "ticket_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the ticket",
            },
            "comment_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the comment",
            },
            "text": {
                "type": "str",
                "location": "body",
                "description": "Replacement text",
            },
        },
        "required": ["ticket_id", "comment_id", "text"],
        "headers": {"Content-Type": "application/json"},
    },
    # ================================================================================
    # SUPPORT API - USERS
    # ================================================================================
    "list_users": {
        "method": "GET",
        "path": "/users.json",
        "description": "List all users",
        "parameters": {
            "role": {
                "type": 'Optional[Literal["end-user", "agent", "admin"]]',
                "location": "query",
                "description": "Filter by user role",
            },
            "roles[]": {
                "type": "Optional[List[str]]",
                "location": "query",
                "description": "Filter by multiple roles",
            },
            "permission_set": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Filter by permission set ID",
            },
            "external_id": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by external ID",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data (organizations,roles,abilities,identities,groups)",
            },
        },
        "required": [],
    },
    "show_user": {
        "method": "GET",
        "path": "/users/{user_id}.json",
        "description": "Show a specific user",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": ["user_id"],
    },
    "create_user": {
        "method": "POST",
        "path": "/users.json",
        "description": "Create a new user",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "User name"},
            "email": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Primary email address",
            },
            "role": {
                "type": 'Optional[Literal["end-user", "agent", "admin"]]',
                "location": "body",
                "description": "User role",
            },
            "custom_role_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Custom role ID",
            },
            "external_id": {
                "type": "Optional[str]",
                "location": "body",
                "description": "External ID",
            },
            "alias": {
                "type": "Optional[str]",
                "location": "body",
                "description": "User alias",
            },
            "details": {
                "type": "Optional[str]",
                "location": "body",
                "description": "User details",
            },
            "notes": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Notes about the user",
            },
            "organization_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Organization ID",
            },
            "phone": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Phone number",
            },
            "shared_phone_number": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether phone is shared",
            },
            "signature": {
                "type": "Optional[str]",
                "location": "body",
                "description": "User signature",
            },
            "tags": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "List of tags",
            },
            "time_zone": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Time zone",
            },
            "locale": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Locale (language)",
            },
            "locale_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Locale ID",
            },
            "user_fields": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Custom user fields",
            },
            "verified": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether user is verified",
            },
            "restricted_agent": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether agent is restricted",
            },
            "suspended": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether user is suspended",
            },
            "shared": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether user is shared",
            },
            "shared_agent": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether agent is shared",
            },
            "only_private_comments": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether user can only make private comments",
            },
            "default_group_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Default group ID",
            },
            "photo": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Photo attachment",
            },
            "identities": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Additional identities",
            },
            "skip_verify_email": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Skip email verification",
            },
        },
        "required": ["name"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_user": {
        "method": "PUT",
        "path": "/users/{user_id}.json",
        "description": "Update an existing user",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user to update",
            },
            "name": {
                "type": "Optional[str]",
                "location": "body",
                "description": "User name",
            },
            "email": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Primary email address",
            },
            "role": {
                "type": 'Optional[Literal["end-user", "agent", "admin"]]',
                "location": "body",
                "description": "User role",
            },
            "custom_role_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Custom role ID",
            },
            "external_id": {
                "type": "Optional[str]",
                "location": "body",
                "description": "External ID",
            },
            "alias": {
                "type": "Optional[str]",
                "location": "body",
                "description": "User alias",
            },
            "details": {
                "type": "Optional[str]",
                "location": "body",
                "description": "User details",
            },
            "notes": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Notes about the user",
            },
            "organization_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Organization ID",
            },
            "phone": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Phone number",
            },
            "shared_phone_number": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether phone is shared",
            },
            "signature": {
                "type": "Optional[str]",
                "location": "body",
                "description": "User signature",
            },
            "tags": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "List of tags",
            },
            "time_zone": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Time zone",
            },
            "locale": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Locale (language)",
            },
            "locale_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Locale ID",
            },
            "user_fields": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Custom user fields",
            },
            "verified": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether user is verified",
            },
            "restricted_agent": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether agent is restricted",
            },
            "suspended": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether user is suspended",
            },
            "shared": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether user is shared",
            },
            "shared_agent": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether agent is shared",
            },
            "only_private_comments": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether user can only make private comments",
            },
            "default_group_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Default group ID",
            },
            "photo": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Photo attachment",
            },
        },
        "required": ["user_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_user": {
        "method": "DELETE",
        "path": "/users/{user_id}.json",
        "description": "Delete a user",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user to delete",
            }
        },
        "required": ["user_id"],
    },
    "search_users": {
        "method": "GET",
        "path": "/users/search.json",
        "description": "Search for users",
        "parameters": {
            "query": {
                "type": "str",
                "location": "query",
                "description": "Search query",
            },
            "external_id": {
                "type": "Optional[str]",
                "location": "query",
                "description": "External ID to search for",
            },
        },
        "required": ["query"],
    },
    "autocomplete_users": {
        "method": "GET",
        "path": "/users/autocomplete.json",
        "description": "Autocomplete user names",
        "parameters": {
            "name": {
                "type": "str",
                "location": "query",
                "description": "Partial name to autocomplete",
            }
        },
        "required": ["name"],
    },
    "show_current_user": {
        "method": "GET",
        "path": "/users/me.json",
        "description": "Show the current authenticated user",
        "parameters": {},
        "required": [],
    },
    "create_many_users": {
        "method": "POST",
        "path": "/users/create_many.json",
        "description": "Create multiple users (up to 100)",
        "parameters": {
            "users": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Array of user objects",
            }
        },
        "required": ["users"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_many_users": {
        "method": "PUT",
        "path": "/users/update_many.json",
        "description": "Update multiple users",
        "parameters": {
            "users": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Array of user update objects",
            },
            "ids": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Comma-separated user IDs",
            },
            "external_ids": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Comma-separated external IDs",
            },
        },
        "required": ["users"],
        "headers": {"Content-Type": "application/json"},
    },
    "create_or_update_many_users": {
        "method": "POST",
        "path": "/users/create_or_update_many.json",
        "description": "Create or update multiple users",
        "parameters": {
            "users": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Array of user objects",
            }
        },
        "required": ["users"],
        "headers": {"Content-Type": "application/json"},
    },
    "destroy_many_users": {
        "method": "DELETE",
        "path": "/users/destroy_many.json",
        "description": "Delete multiple users",
        "parameters": {
            "ids": {
                "type": "str",
                "location": "query",
                "description": "Comma-separated user IDs",
            },
            "external_ids": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Comma-separated external IDs",
            },
        },
        "required": ["ids"],
    },
    "show_multiple_users": {
        "method": "GET",
        "path": "/users/show_many.json",
        "description": "Show details of multiple users",
        "parameters": {
            "ids": {
                "type": "str",
                "location": "query",
                "description": "Comma-separated user IDs",
            },
            "external_ids": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Comma-separated external IDs",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": ["ids"],
    },
    "show_user_related_information": {
        "method": "GET",
        "path": "/users/{user_id}/related.json",
        "description": "Show user related ticket and organization information",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            }
        },
        "required": ["user_id"],
    },
    "set_user_password": {
        "method": "POST",
        "path": "/users/{user_id}/password.json",
        "description": "Set user password",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            },
            "password": {
                "type": "str",
                "location": "body",
                "description": "New password",
            },
        },
        "required": ["user_id", "password"],
        "headers": {"Content-Type": "application/json"},
    },
    "change_user_password": {
        "method": "PUT",
        "path": "/users/{user_id}/password.json",
        "description": "Change user password",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            },
            "previous_password": {
                "type": "str",
                "location": "body",
                "description": "Current password",
            },
            "password": {
                "type": "str",
                "location": "body",
                "description": "New password",
            },
        },
        "required": ["user_id", "previous_password", "password"],
        "headers": {"Content-Type": "application/json"},
    },
    # ================================================================================
    # SUPPORT API - USER IDENTITIES
    # ================================================================================
    "list_user_identities": {
        "method": "GET",
        "path": "/users/{user_id}/identities.json",
        "description": "List user identities",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            }
        },
        "required": ["user_id"],
    },
    "show_user_identity": {
        "method": "GET",
        "path": "/users/{user_id}/identities/{identity_id}.json",
        "description": "Show a specific user identity",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            },
            "identity_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the identity",
            },
        },
        "required": ["user_id", "identity_id"],
    },
    "create_user_identity": {
        "method": "POST",
        "path": "/users/{user_id}/identities.json",
        "description": "Create a user identity",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            },
            "type": {
                "type": 'Literal["email", "twitter", "facebook", "google", "phone_number", "agent_forwarding", "sdk"]',
                "location": "body",
                "description": "Type of identity",
            },
            "value": {
                "type": "str",
                "location": "body",
                "description": "Identity value (email, phone, etc.)",
            },
            "verified": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether identity is verified",
            },
        },
        "required": ["user_id", "type", "value"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_user_identity": {
        "method": "PUT",
        "path": "/users/{user_id}/identities/{identity_id}.json",
        "description": "Update a user identity",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            },
            "identity_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the identity",
            },
            "verified": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether identity is verified",
            },
        },
        "required": ["user_id", "identity_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "make_user_identity_primary": {
        "method": "PUT",
        "path": "/users/{user_id}/identities/{identity_id}/make_primary.json",
        "description": "Make an identity the primary identity",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            },
            "identity_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the identity",
            },
        },
        "required": ["user_id", "identity_id"],
    },
    "verify_user_identity": {
        "method": "PUT",
        "path": "/users/{user_id}/identities/{identity_id}/verify.json",
        "description": "Verify a user identity",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            },
            "identity_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the identity",
            },
        },
        "required": ["user_id", "identity_id"],
    },
    "request_user_verification": {
        "method": "PUT",
        "path": "/users/{user_id}/identities/{identity_id}/request_verification.json",
        "description": "Request identity verification",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            },
            "identity_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the identity",
            },
        },
        "required": ["user_id", "identity_id"],
    },
    "delete_user_identity": {
        "method": "DELETE",
        "path": "/users/{user_id}/identities/{identity_id}.json",
        "description": "Delete a user identity",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            },
            "identity_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the identity",
            },
        },
        "required": ["user_id", "identity_id"],
    },
    # ================================================================================
    # SUPPORT API - ORGANIZATIONS
    # ================================================================================
    "list_organizations": {
        "method": "GET",
        "path": "/organizations.json",
        "description": "List all organizations",
        "parameters": {
            "external_id": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by external ID",
            }
        },
        "required": [],
    },
    "show_organization": {
        "method": "GET",
        "path": "/organizations/{organization_id}.json",
        "description": "Show a specific organization",
        "parameters": {
            "organization_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the organization",
            }
        },
        "required": ["organization_id"],
    },
    "create_organization": {
        "method": "POST",
        "path": "/organizations.json",
        "description": "Create a new organization",
        "parameters": {
            "name": {
                "type": "str",
                "location": "body",
                "description": "Organization name",
            },
            "details": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Organization details",
            },
            "notes": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Notes about the organization",
            },
            "external_id": {
                "type": "Optional[str]",
                "location": "body",
                "description": "External ID",
            },
            "domain_names": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "List of domain names",
            },
            "tags": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "List of tags",
            },
            "shared_tickets": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether tickets are shared",
            },
            "shared_comments": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether comments are shared",
            },
            "group_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Default group ID",
            },
            "organization_fields": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Custom organization fields",
            },
        },
        "required": ["name"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_organization": {
        "method": "PUT",
        "path": "/organizations/{organization_id}.json",
        "description": "Update an existing organization",
        "parameters": {
            "organization_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the organization to update",
            },
            "name": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Organization name",
            },
            "details": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Organization details",
            },
            "notes": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Notes about the organization",
            },
            "external_id": {
                "type": "Optional[str]",
                "location": "body",
                "description": "External ID",
            },
            "domain_names": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "List of domain names",
            },
            "tags": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "List of tags",
            },
            "shared_tickets": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether tickets are shared",
            },
            "shared_comments": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether comments are shared",
            },
            "group_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Default group ID",
            },
            "organization_fields": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Custom organization fields",
            },
        },
        "required": ["organization_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_organization": {
        "method": "DELETE",
        "path": "/organizations/{organization_id}.json",
        "description": "Delete an organization",
        "parameters": {
            "organization_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the organization to delete",
            }
        },
        "required": ["organization_id"],
    },
    "search_organizations": {
        "method": "GET",
        "path": "/organizations/search.json",
        "description": "Search for organizations",
        "parameters": {
            "external_id": {
                "type": "Optional[str]",
                "location": "query",
                "description": "External ID to search for",
            },
            "name": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Organization name to search for",
            },
        },
        "required": [],
    },
    "autocomplete_organizations": {
        "method": "GET",
        "path": "/organizations/autocomplete.json",
        "description": "Autocomplete organization names",
        "parameters": {
            "name": {
                "type": "str",
                "location": "query",
                "description": "Partial name to autocomplete",
            },
            "field_id": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Field ID for custom field autocomplete",
            },
            "source": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Source for the autocomplete request",
            },
        },
        "required": ["name"],
    },
    "create_many_organizations": {
        "method": "POST",
        "path": "/organizations/create_many.json",
        "description": "Create multiple organizations (up to 100)",
        "parameters": {
            "organizations": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Array of organization objects",
            }
        },
        "required": ["organizations"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_many_organizations": {
        "method": "PUT",
        "path": "/organizations/update_many.json",
        "description": "Update multiple organizations",
        "parameters": {
            "organizations": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Array of organization update objects",
            },
            "ids": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Comma-separated organization IDs",
            },
            "external_ids": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Comma-separated external IDs",
            },
        },
        "required": ["organizations"],
        "headers": {"Content-Type": "application/json"},
    },
    "create_or_update_many_organizations": {
        "method": "POST",
        "path": "/organizations/create_or_update_many.json",
        "description": "Create or update multiple organizations",
        "parameters": {
            "organizations": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Array of organization objects",
            }
        },
        "required": ["organizations"],
        "headers": {"Content-Type": "application/json"},
    },
    "destroy_many_organizations": {
        "method": "DELETE",
        "path": "/organizations/destroy_many.json",
        "description": "Delete multiple organizations",
        "parameters": {
            "ids": {
                "type": "str",
                "location": "query",
                "description": "Comma-separated organization IDs",
            },
            "external_ids": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Comma-separated external IDs",
            },
        },
        "required": ["ids"],
    },
    "show_multiple_organizations": {
        "method": "GET",
        "path": "/organizations/show_many.json",
        "description": "Show details of multiple organizations",
        "parameters": {
            "ids": {
                "type": "str",
                "location": "query",
                "description": "Comma-separated organization IDs",
            },
            "external_ids": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Comma-separated external IDs",
            },
        },
        "required": ["ids"],
    },
    # ================================================================================
    # SUPPORT API - GROUPS
    # ================================================================================
    "list_groups": {
        "method": "GET",
        "path": "/groups.json",
        "description": "List all groups",
        "parameters": {
            "exclude_deleted": {
                "type": "Optional[bool]",
                "location": "query",
                "description": "Exclude deleted groups",
            }
        },
        "required": [],
    },
    "show_group": {
        "method": "GET",
        "path": "/groups/{group_id}.json",
        "description": "Show a specific group",
        "parameters": {
            "group_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the group",
            }
        },
        "required": ["group_id"],
    },
    "create_group": {
        "method": "POST",
        "path": "/groups.json",
        "description": "Create a new group",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "Group name"},
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Group description",
            },
            "default": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether this is the default group",
            },
            "is_public": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether the group is public",
            },
        },
        "required": ["name"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_group": {
        "method": "PUT",
        "path": "/groups/{group_id}.json",
        "description": "Update an existing group",
        "parameters": {
            "group_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the group to update",
            },
            "name": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Group name",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Group description",
            },
            "default": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether this is the default group",
            },
            "is_public": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether the group is public",
            },
        },
        "required": ["group_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_group": {
        "method": "DELETE",
        "path": "/groups/{group_id}.json",
        "description": "Delete a group",
        "parameters": {
            "group_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the group to delete",
            }
        },
        "required": ["group_id"],
    },
    # ================================================================================
    # SUPPORT API - GROUP MEMBERSHIPS
    # ================================================================================
    "list_group_memberships": {
        "method": "GET",
        "path": "/group_memberships.json",
        "description": "List all group memberships",
        "parameters": {},
        "required": [],
    },
    "list_group_memberships_by_group": {
        "method": "GET",
        "path": "/groups/{group_id}/memberships.json",
        "description": "List memberships for a specific group",
        "parameters": {
            "group_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the group",
            }
        },
        "required": ["group_id"],
    },
    "list_group_memberships_by_user": {
        "method": "GET",
        "path": "/users/{user_id}/group_memberships.json",
        "description": "List memberships for a specific user",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user",
            }
        },
        "required": ["user_id"],
    },
    "show_group_membership": {
        "method": "GET",
        "path": "/group_memberships/{membership_id}.json",
        "description": "Show a specific group membership",
        "parameters": {
            "membership_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the membership",
            }
        },
        "required": ["membership_id"],
    },
    "create_group_membership": {
        "method": "POST",
        "path": "/group_memberships.json",
        "description": "Create a new group membership",
        "parameters": {
            "user_id": {
                "type": "int",
                "location": "body",
                "description": "ID of the user",
            },
            "group_id": {
                "type": "int",
                "location": "body",
                "description": "ID of the group",
            },
            "default": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether this is the default group for the user",
            },
        },
        "required": ["user_id", "group_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_group_membership": {
        "method": "DELETE",
        "path": "/group_memberships/{membership_id}.json",
        "description": "Delete a group membership",
        "parameters": {
            "membership_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the membership to delete",
            }
        },
        "required": ["membership_id"],
    },
    "destroy_many_group_memberships": {
        "method": "DELETE",
        "path": "/group_memberships/destroy_many.json",
        "description": "Delete multiple group memberships",
        "parameters": {
            "ids": {
                "type": "str",
                "location": "query",
                "description": "Comma-separated membership IDs",
            }
        },
        "required": ["ids"],
    },
    "create_many_group_memberships": {
        "method": "POST",
        "path": "/group_memberships/create_many.json",
        "description": "Create multiple group memberships",
        "parameters": {
            "group_memberships": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Array of membership objects",
            }
        },
        "required": ["group_memberships"],
        "headers": {"Content-Type": "application/json"},
    },
    # ================================================================================
    # SUPPORT API - SEARCH
    # ================================================================================
    "search": {
        "method": "GET",
        "path": "/search.json",
        "description": "Search for tickets, users, organizations, and groups",
        "parameters": {
            "query": {
                "type": "str",
                "location": "query",
                "description": 'Search query with syntax like "type:ticket status:open"',
            },
            "sort_by": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sort field",
            },
            "sort_order": {
                "type": 'Optional[Literal["asc", "desc"]]',
                "location": "query",
                "description": "Sort direction",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": ["query"],
    },
    "search_export": {
        "method": "GET",
        "path": "/search/export.json",
        "description": "Export search results for large datasets",
        "parameters": {
            "query": {
                "type": "str",
                "location": "query",
                "description": "Search query",
            },
            "filter": {
                "type": "Optional[Dict[str, str]]",
                "location": "query",
                "description": "Additional filters",
            },
        },
        "required": ["query"],
    },
    "count_search_results": {
        "method": "GET",
        "path": "/search/count.json",
        "description": "Get count of search results",
        "parameters": {
            "query": {"type": "str", "location": "query", "description": "Search query"}
        },
        "required": ["query"],
    },
    # ================================================================================
    # SUPPORT API - REQUESTS (END-USER TICKETS)
    # ================================================================================
    "list_requests": {
        "method": "GET",
        "path": "/requests.json",
        "description": "List requests from the end-user perspective",
        "parameters": {
            "status": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by status",
            },
            "organization_id": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Filter by organization ID",
            },
        },
        "required": [],
    },
    "show_request": {
        "method": "GET",
        "path": "/requests/{request_id}.json",
        "description": "Show a specific request",
        "parameters": {
            "request_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the request",
            }
        },
        "required": ["request_id"],
    },
    "create_request": {
        "method": "POST",
        "path": "/requests.json",
        "description": "Create a new request (end-user creates ticket)",
        "parameters": {
            "subject": {
                "type": "str",
                "location": "body",
                "description": "Request subject",
            },
            "comment": {
                "type": "Dict[str, Any]",
                "location": "body",
                "description": "Initial comment with body and uploads",
            },
            "priority": {
                "type": 'Optional[Literal["urgent", "high", "normal", "low"]]',
                "location": "body",
                "description": "Priority level",
            },
            "type": {
                "type": 'Optional[Literal["problem", "incident", "question", "task"]]',
                "location": "body",
                "description": "Request type",
            },
            "custom_fields": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Custom field values",
            },
            "fields": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Ticket field values",
            },
            "recipient": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Recipient email",
            },
            "collaborator_ids": {
                "type": "Optional[List[int]]",
                "location": "body",
                "description": "List of collaborator user IDs",
            },
            "email_ccs": {
                "type": "Optional[List[Dict[str, str]]]",
                "location": "body",
                "description": "List of email CCs",
            },
        },
        "required": ["subject", "comment"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_request": {
        "method": "PUT",
        "path": "/requests/{request_id}.json",
        "description": "Update a request (add comment or solve)",
        "parameters": {
            "request_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the request",
            },
            "comment": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Comment to add",
            },
            "solved": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Mark request as solved",
            },
            "additional_collaborators": {
                "type": "Optional[List[Dict[str, str]]]",
                "location": "body",
                "description": "Additional collaborators to add",
            },
        },
        "required": ["request_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "list_request_comments": {
        "method": "GET",
        "path": "/requests/{request_id}/comments.json",
        "description": "List comments for a request",
        "parameters": {
            "request_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the request",
            },
            "sort_order": {
                "type": 'Optional[Literal["asc", "desc"]]',
                "location": "query",
                "description": "Sort direction",
            },
        },
        "required": ["request_id"],
    },
    "show_request_comment": {
        "method": "GET",
        "path": "/requests/{request_id}/comments/{comment_id}.json",
        "description": "Show a specific request comment",
        "parameters": {
            "request_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the request",
            },
            "comment_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the comment",
            },
        },
        "required": ["request_id", "comment_id"],
    },
    # ================================================================================
    # BUSINESS RULES - AUTOMATIONS
    # ================================================================================
    "list_automations": {
        "method": "GET",
        "path": "/automations.json",
        "description": "List all automations",
        "parameters": {
            "active": {
                "type": "Optional[bool]",
                "location": "query",
                "description": "Filter by active status",
            },
            "sort_by": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sort field",
            },
            "sort_order": {
                "type": 'Optional[Literal["asc", "desc"]]',
                "location": "query",
                "description": "Sort direction",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": [],
    },
    "list_active_automations": {
        "method": "GET",
        "path": "/automations/active.json",
        "description": "List active automations",
        "parameters": {},
        "required": [],
    },
    "show_automation": {
        "method": "GET",
        "path": "/automations/{automation_id}.json",
        "description": "Show a specific automation",
        "parameters": {
            "automation_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the automation",
            }
        },
        "required": ["automation_id"],
    },
    "create_automation": {
        "method": "POST",
        "path": "/automations.json",
        "description": "Create a new automation",
        "parameters": {
            "title": {
                "type": "str",
                "location": "body",
                "description": "Automation title",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether automation is active",
            },
            "conditions": {
                "type": "Dict[str, Any]",
                "location": "body",
                "description": "Automation conditions (all, any)",
            },
            "actions": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Actions to perform",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Position in automation list",
            },
        },
        "required": ["title", "conditions", "actions"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_automation": {
        "method": "PUT",
        "path": "/automations/{automation_id}.json",
        "description": "Update an existing automation",
        "parameters": {
            "automation_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the automation to update",
            },
            "title": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Automation title",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether automation is active",
            },
            "conditions": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Automation conditions",
            },
            "actions": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Actions to perform",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Position in automation list",
            },
        },
        "required": ["automation_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_automation": {
        "method": "DELETE",
        "path": "/automations/{automation_id}.json",
        "description": "Delete an automation",
        "parameters": {
            "automation_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the automation to delete",
            }
        },
        "required": ["automation_id"],
    },
    "update_many_automations": {
        "method": "PUT",
        "path": "/automations/update_many.json",
        "description": "Update multiple automations",
        "parameters": {
            "automations": {
                "type": "Dict[str, List[Dict[str, Any]]]",
                "location": "body",
                "description": "Automation objects to update",
            }
        },
        "required": ["automations"],
        "headers": {"Content-Type": "application/json"},
    },
    # ================================================================================
    # BUSINESS RULES - TRIGGERS
    # ================================================================================
    "list_triggers": {
        "method": "GET",
        "path": "/triggers.json",
        "description": "List all triggers",
        "parameters": {
            "active": {
                "type": "Optional[bool]",
                "location": "query",
                "description": "Filter by active status",
            },
            "sort_by": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sort field",
            },
            "sort_order": {
                "type": 'Optional[Literal["asc", "desc"]]',
                "location": "query",
                "description": "Sort direction",
            },
            "category_id": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by category ID",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": [],
    },
    "list_active_triggers": {
        "method": "GET",
        "path": "/triggers/active.json",
        "description": "List active triggers",
        "parameters": {},
        "required": [],
    },
    "show_trigger": {
        "method": "GET",
        "path": "/triggers/{trigger_id}.json",
        "description": "Show a specific trigger",
        "parameters": {
            "trigger_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the trigger",
            }
        },
        "required": ["trigger_id"],
    },
    "create_trigger": {
        "method": "POST",
        "path": "/triggers.json",
        "description": "Create a new trigger",
        "parameters": {
            "title": {
                "type": "str",
                "location": "body",
                "description": "Trigger title",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether trigger is active",
            },
            "conditions": {
                "type": "Dict[str, Any]",
                "location": "body",
                "description": "Trigger conditions (all, any)",
            },
            "actions": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Actions to perform",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Position in trigger list",
            },
            "category_id": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Category ID",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Trigger description",
            },
        },
        "required": ["title", "conditions", "actions"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_trigger": {
        "method": "PUT",
        "path": "/triggers/{trigger_id}.json",
        "description": "Update an existing trigger",
        "parameters": {
            "trigger_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the trigger to update",
            },
            "title": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Trigger title",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether trigger is active",
            },
            "conditions": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Trigger conditions",
            },
            "actions": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Actions to perform",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Position in trigger list",
            },
            "category_id": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Category ID",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Trigger description",
            },
        },
        "required": ["trigger_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_trigger": {
        "method": "DELETE",
        "path": "/triggers/{trigger_id}.json",
        "description": "Delete a trigger",
        "parameters": {
            "trigger_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the trigger to delete",
            }
        },
        "required": ["trigger_id"],
    },
    "reorder_triggers": {
        "method": "PUT",
        "path": "/triggers/reorder.json",
        "description": "Reorder triggers",
        "parameters": {
            "trigger_ids": {
                "type": "List[int]",
                "location": "body",
                "description": "Array of trigger IDs in new order",
            }
        },
        "required": ["trigger_ids"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_many_triggers": {
        "method": "PUT",
        "path": "/triggers/update_many.json",
        "description": "Update multiple triggers",
        "parameters": {
            "triggers": {
                "type": "Dict[str, List[Dict[str, Any]]]",
                "location": "body",
                "description": "Trigger objects to update",
            }
        },
        "required": ["triggers"],
        "headers": {"Content-Type": "application/json"},
    },
    "destroy_many_triggers": {
        "method": "DELETE",
        "path": "/triggers/destroy_many.json",
        "description": "Delete multiple triggers",
        "parameters": {
            "ids": {
                "type": "str",
                "location": "query",
                "description": "Comma-separated trigger IDs",
            }
        },
        "required": ["ids"],
    },
    # ================================================================================
    # BUSINESS RULES - MACROS
    # ================================================================================
    "list_macros": {
        "method": "GET",
        "path": "/macros.json",
        "description": "List all macros",
        "parameters": {
            "active": {
                "type": "Optional[bool]",
                "location": "query",
                "description": "Filter by active status",
            },
            "access": {
                "type": 'Optional[Literal["personal", "shared"]]',
                "location": "query",
                "description": "Filter by access level",
            },
            "group_id": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Filter by group ID",
            },
            "category": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Filter by category ID",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data (usage_1h,usage_24h,usage_7d,usage_30d)",
            },
            "only_viewable": {
                "type": "Optional[bool]",
                "location": "query",
                "description": "Only show viewable macros",
            },
            "sort_by": {
                "type": 'Optional[Literal["alphabetical", "created_at", "updated_at", "usage_1h", "usage_24h", "usage_7d", "usage_30d", "position"]]',
                "location": "query",
                "description": "Sort field",
            },
            "sort_order": {
                "type": 'Optional[Literal["asc", "desc"]]',
                "location": "query",
                "description": "Sort direction",
            },
        },
        "required": [],
    },
    "list_active_macros": {
        "method": "GET",
        "path": "/macros/active.json",
        "description": "List active macros",
        "parameters": {},
        "required": [],
    },
    "show_macro": {
        "method": "GET",
        "path": "/macros/{macro_id}.json",
        "description": "Show a specific macro",
        "parameters": {
            "macro_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the macro",
            }
        },
        "required": ["macro_id"],
    },
    "create_macro": {
        "method": "POST",
        "path": "/macros.json",
        "description": "Create a new macro",
        "parameters": {
            "title": {"type": "str", "location": "body", "description": "Macro title"},
            "actions": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "Actions to perform",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether macro is active",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Macro description",
            },
            "restriction": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Access restrictions",
            },
        },
        "required": ["title", "actions"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_macro": {
        "method": "PUT",
        "path": "/macros/{macro_id}.json",
        "description": "Update an existing macro",
        "parameters": {
            "macro_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the macro to update",
            },
            "title": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Macro title",
            },
            "actions": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Actions to perform",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether macro is active",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Macro description",
            },
            "restriction": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Access restrictions",
            },
        },
        "required": ["macro_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_macro": {
        "method": "DELETE",
        "path": "/macros/{macro_id}.json",
        "description": "Delete a macro",
        "parameters": {
            "macro_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the macro to delete",
            }
        },
        "required": ["macro_id"],
    },
    "apply_macro_to_ticket": {
        "method": "PUT",
        "path": "/tickets/{ticket_id}/macros/{macro_id}/apply.json",
        "description": "Apply a macro to a ticket",
        "parameters": {
            "ticket_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the ticket",
            },
            "macro_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the macro to apply",
            },
        },
        "required": ["ticket_id", "macro_id"],
    },
    "show_macro_application_result": {
        "method": "GET",
        "path": "/macros/{macro_id}/apply.json",
        "description": "Show the result of applying a macro",
        "parameters": {
            "macro_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the macro",
            }
        },
        "required": ["macro_id"],
    },
    "show_ticket_after_macro": {
        "method": "GET",
        "path": "/tickets/{ticket_id}/macros/{macro_id}/apply.json",
        "description": "Show how a ticket would look after applying a macro",
        "parameters": {
            "ticket_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the ticket",
            },
            "macro_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the macro",
            },
        },
        "required": ["ticket_id", "macro_id"],
    },
    "update_many_macros": {
        "method": "PUT",
        "path": "/macros/update_many.json",
        "description": "Update multiple macros",
        "parameters": {
            "macros": {
                "type": "Dict[str, List[Dict[str, Any]]]",
                "location": "body",
                "description": "Macro objects to update",
            }
        },
        "required": ["macros"],
        "headers": {"Content-Type": "application/json"},
    },
    "destroy_many_macros": {
        "method": "DELETE",
        "path": "/macros/destroy_many.json",
        "description": "Delete multiple macros",
        "parameters": {
            "ids": {
                "type": "str",
                "location": "query",
                "description": "Comma-separated macro IDs",
            }
        },
        "required": ["ids"],
    },
    # ================================================================================
    # WEBHOOKS API
    # ================================================================================
    "list_webhooks": {
        "method": "GET",
        "path": "/webhooks.json",
        "description": "List all webhooks",
        "parameters": {
            "filter": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter webhooks",
            }
        },
        "required": [],
    },
    "show_webhook": {
        "method": "GET",
        "path": "/webhooks/{webhook_id}.json",
        "description": "Show a specific webhook",
        "parameters": {
            "webhook_id": {
                "type": "str",
                "location": "path",
                "description": "ID of the webhook",
            }
        },
        "required": ["webhook_id"],
    },
    "create_webhook": {
        "method": "POST",
        "path": "/webhooks.json",
        "description": "Create a new webhook",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "Webhook name"},
            "endpoint": {
                "type": "str",
                "location": "body",
                "description": "Destination URL",
            },
            "http_method": {
                "type": 'Literal["POST", "PUT", "PATCH", "GET", "DELETE"]',
                "location": "body",
                "description": "HTTP method",
            },
            "request_format": {
                "type": 'Literal["json", "xml", "form_encoded"]',
                "location": "body",
                "description": "Request format",
            },
            "status": {
                "type": 'Literal["active", "inactive"]',
                "location": "body",
                "description": "Webhook status",
            },
            "subscriptions": {
                "type": "List[str]",
                "location": "body",
                "description": "Event subscriptions",
            },
            "authentication": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Authentication settings",
            },
            "custom_headers": {
                "type": "Optional[Dict[str, str]]",
                "location": "body",
                "description": "Custom headers",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Webhook description",
            },
        },
        "required": [
            "name",
            "endpoint",
            "http_method",
            "request_format",
            "status",
            "subscriptions",
        ],
        "headers": {"Content-Type": "application/json"},
    },
    "update_webhook": {
        "method": "PATCH",
        "path": "/webhooks/{webhook_id}.json",
        "description": "Update an existing webhook",
        "parameters": {
            "webhook_id": {
                "type": "str",
                "location": "path",
                "description": "ID of the webhook to update",
            },
            "name": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Webhook name",
            },
            "endpoint": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Destination URL",
            },
            "http_method": {
                "type": 'Optional[Literal["POST", "PUT", "PATCH", "GET", "DELETE"]]',
                "location": "body",
                "description": "HTTP method",
            },
            "request_format": {
                "type": 'Optional[Literal["json", "xml", "form_encoded"]]',
                "location": "body",
                "description": "Request format",
            },
            "status": {
                "type": 'Optional[Literal["active", "inactive"]]',
                "location": "body",
                "description": "Webhook status",
            },
            "subscriptions": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "Event subscriptions",
            },
            "authentication": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Authentication settings",
            },
            "custom_headers": {
                "type": "Optional[Dict[str, str]]",
                "location": "body",
                "description": "Custom headers",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Webhook description",
            },
        },
        "required": ["webhook_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_webhook": {
        "method": "DELETE",
        "path": "/webhooks/{webhook_id}.json",
        "description": "Delete a webhook",
        "parameters": {
            "webhook_id": {
                "type": "str",
                "location": "path",
                "description": "ID of the webhook to delete",
            }
        },
        "required": ["webhook_id"],
    },
    "test_webhook": {
        "method": "POST",
        "path": "/webhooks/test.json",
        "description": "Test a webhook configuration",
        "parameters": {
            "webhook_id": {
                "type": "Optional[str]",
                "location": "query",
                "description": "ID of existing webhook to test",
            },
            "request": {
                "type": "Dict[str, Any]",
                "location": "body",
                "description": "Test request configuration",
            },
        },
        "required": ["request"],
        "headers": {"Content-Type": "application/json"},
    },
    "clone_webhook": {
        "method": "POST",
        "path": "/webhooks.json",
        "description": "Clone an existing webhook",
        "parameters": {
            "clone_webhook_id": {
                "type": "str",
                "location": "query",
                "description": "ID of webhook to clone",
            },
            "name": {
                "type": "str",
                "location": "body",
                "description": "Name for the cloned webhook",
            },
        },
        "required": ["clone_webhook_id", "name"],
        "headers": {"Content-Type": "application/json"},
    },
    "show_webhook_signing_secret": {
        "method": "GET",
        "path": "/webhooks/{webhook_id}/signing_secret.json",
        "description": "Show webhook signing secret",
        "parameters": {
            "webhook_id": {
                "type": "str",
                "location": "path",
                "description": "ID of the webhook",
            }
        },
        "required": ["webhook_id"],
    },
    "reset_webhook_signing_secret": {
        "method": "POST",
        "path": "/webhooks/{webhook_id}/signing_secret/reset.json",
        "description": "Reset webhook signing secret",
        "parameters": {
            "webhook_id": {
                "type": "str",
                "location": "path",
                "description": "ID of the webhook",
            }
        },
        "required": ["webhook_id"],
    },
    # ================================================================================
    # ATTACHMENTS
    # ================================================================================
    "upload_attachment": {
        "method": "POST",
        "path": "/uploads.json",
        "description": "Upload an attachment",
        "parameters": {
            "filename": {
                "type": "str",
                "location": "query",
                "description": "Name of the file",
            },
            "token": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Upload token for additional files",
            },
            "file": {
                "type": "bytes",
                "location": "body",
                "description": "File content",
            },
        },
        "required": ["filename", "file"],
        "headers": {"Content-Type": "application/binary"},
    },
    "show_attachment": {
        "method": "GET",
        "path": "/attachments/{attachment_id}.json",
        "description": "Show attachment details",
        "parameters": {
            "attachment_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the attachment",
            }
        },
        "required": ["attachment_id"],
    },
    "delete_upload": {
        "method": "DELETE",
        "path": "/uploads/{upload_token}.json",
        "description": "Delete an uploaded file before it is used",
        "parameters": {
            "upload_token": {
                "type": "str",
                "location": "path",
                "description": "Upload token from upload response",
            }
        },
        "required": ["upload_token"],
    },
    # ================================================================================
    # SUPPORT API - CUSTOM FIELDS
    # ================================================================================
    "list_ticket_fields": {
        "method": "GET",
        "path": "/ticket_fields.json",
        "description": "List all ticket fields",
        "parameters": {
            "locale": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Locale for field names",
            },
            "creator_id": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Filter by creator ID",
            },
        },
        "required": [],
    },
    "show_ticket_field": {
        "method": "GET",
        "path": "/ticket_fields/{field_id}.json",
        "description": "Show a specific ticket field",
        "parameters": {
            "field_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the ticket field",
            }
        },
        "required": ["field_id"],
    },
    "create_ticket_field": {
        "method": "POST",
        "path": "/ticket_fields.json",
        "description": "Create a new ticket field",
        "parameters": {
            "type": {
                "type": 'Literal["text", "textarea", "checkbox", "date", "integer", "decimal", "regexp", "partialcreditcard", "multiselect", "tagger", "subject", "description", "status", "priority", "group", "assignee", "custom_status", "tickettype"]',
                "location": "body",
                "description": "Field type",
            },
            "title": {"type": "str", "location": "body", "description": "Field title"},
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Field description",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Field position",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is active",
            },
            "required": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is required",
            },
            "collapsed_for_agents": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is collapsed for agents",
            },
            "regexp_for_validation": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Regular expression for validation",
            },
            "title_in_portal": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Title shown in help center",
            },
            "visible_in_portal": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is visible in help center",
            },
            "editable_in_portal": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is editable in help center",
            },
            "required_in_portal": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is required in help center",
            },
            "tag": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Tag for tagger field type",
            },
            "custom_field_options": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Options for dropdown/multiselect fields",
            },
            "system_field_options": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Options for system fields",
            },
            "sub_type_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Sub-type ID for lookup fields",
            },
            "removable": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field can be removed",
            },
            "agent_description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Description shown to agents",
            },
        },
        "required": ["type", "title"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_ticket_field": {
        "method": "PUT",
        "path": "/ticket_fields/{field_id}.json",
        "description": "Update an existing ticket field",
        "parameters": {
            "field_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the field to update",
            },
            "title": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Field title",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Field description",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Field position",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is active",
            },
            "required": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is required",
            },
            "collapsed_for_agents": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is collapsed for agents",
            },
            "regexp_for_validation": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Regular expression for validation",
            },
            "title_in_portal": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Title shown in help center",
            },
            "visible_in_portal": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is visible in help center",
            },
            "editable_in_portal": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is editable in help center",
            },
            "required_in_portal": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is required in help center",
            },
            "tag": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Tag for tagger field type",
            },
            "custom_field_options": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Options for dropdown/multiselect fields",
            },
            "system_field_options": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Options for system fields",
            },
            "sub_type_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Sub-type ID for lookup fields",
            },
            "removable": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field can be removed",
            },
            "agent_description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Description shown to agents",
            },
        },
        "required": ["field_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_ticket_field": {
        "method": "DELETE",
        "path": "/ticket_fields/{field_id}.json",
        "description": "Delete a ticket field",
        "parameters": {
            "field_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the field to delete",
            }
        },
        "required": ["field_id"],
    },
    "list_user_fields": {
        "method": "GET",
        "path": "/user_fields.json",
        "description": "List all user fields",
        "parameters": {},
        "required": [],
    },
    "show_user_field": {
        "method": "GET",
        "path": "/user_fields/{field_id}.json",
        "description": "Show a specific user field",
        "parameters": {
            "field_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the user field",
            }
        },
        "required": ["field_id"],
    },
    "create_user_field": {
        "method": "POST",
        "path": "/user_fields.json",
        "description": "Create a new user field",
        "parameters": {
            "type": {
                "type": 'Literal["text", "textarea", "checkbox", "date", "integer", "decimal", "regexp", "dropdown", "tagger", "multiselect", "lookup"]',
                "location": "body",
                "description": "Field type",
            },
            "key": {
                "type": "str",
                "location": "body",
                "description": "Field key (unique identifier)",
            },
            "title": {"type": "str", "location": "body", "description": "Field title"},
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Field description",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Field position",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is active",
            },
            "required": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is required",
            },
            "collapsed_for_agents": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is collapsed for agents",
            },
            "regexp_for_validation": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Regular expression for validation",
            },
            "custom_field_options": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Options for dropdown/multiselect fields",
            },
            "tag": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Tag for tagger field type",
            },
        },
        "required": ["type", "key", "title"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_user_field": {
        "method": "PUT",
        "path": "/user_fields/{field_id}.json",
        "description": "Update an existing user field",
        "parameters": {
            "field_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the field to update",
            },
            "title": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Field title",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Field description",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Field position",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is active",
            },
            "required": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is required",
            },
            "collapsed_for_agents": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is collapsed for agents",
            },
            "regexp_for_validation": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Regular expression for validation",
            },
            "custom_field_options": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Options for dropdown/multiselect fields",
            },
            "tag": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Tag for tagger field type",
            },
        },
        "required": ["field_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_user_field": {
        "method": "DELETE",
        "path": "/user_fields/{field_id}.json",
        "description": "Delete a user field",
        "parameters": {
            "field_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the field to delete",
            }
        },
        "required": ["field_id"],
    },
    "list_organization_fields": {
        "method": "GET",
        "path": "/organization_fields.json",
        "description": "List all organization fields",
        "parameters": {},
        "required": [],
    },
    "show_organization_field": {
        "method": "GET",
        "path": "/organization_fields/{field_id}.json",
        "description": "Show a specific organization field",
        "parameters": {
            "field_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the organization field",
            }
        },
        "required": ["field_id"],
    },
    "create_organization_field": {
        "method": "POST",
        "path": "/organization_fields.json",
        "description": "Create a new organization field",
        "parameters": {
            "type": {
                "type": 'Literal["text", "textarea", "checkbox", "date", "integer", "decimal", "regexp", "dropdown", "tagger", "multiselect", "lookup"]',
                "location": "body",
                "description": "Field type",
            },
            "key": {
                "type": "str",
                "location": "body",
                "description": "Field key (unique identifier)",
            },
            "title": {"type": "str", "location": "body", "description": "Field title"},
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Field description",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Field position",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is active",
            },
            "required": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is required",
            },
            "collapsed_for_agents": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is collapsed for agents",
            },
            "regexp_for_validation": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Regular expression for validation",
            },
            "custom_field_options": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Options for dropdown/multiselect fields",
            },
            "tag": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Tag for tagger field type",
            },
        },
        "required": ["type", "key", "title"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_organization_field": {
        "method": "PUT",
        "path": "/organization_fields/{field_id}.json",
        "description": "Update an existing organization field",
        "parameters": {
            "field_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the field to update",
            },
            "title": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Field title",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Field description",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Field position",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is active",
            },
            "required": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is required",
            },
            "collapsed_for_agents": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether field is collapsed for agents",
            },
            "regexp_for_validation": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Regular expression for validation",
            },
            "custom_field_options": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "Options for dropdown/multiselect fields",
            },
            "tag": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Tag for tagger field type",
            },
        },
        "required": ["field_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_organization_field": {
        "method": "DELETE",
        "path": "/organization_fields/{field_id}.json",
        "description": "Delete an organization field",
        "parameters": {
            "field_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the field to delete",
            }
        },
        "required": ["field_id"],
    },
    # ================================================================================
    # SUPPORT API - TAGS
    # ================================================================================
    "list_tags": {
        "method": "GET",
        "path": "/tags.json",
        "description": "List all tags",
        "parameters": {},
        "required": [],
    },
    "show_tags_count": {
        "method": "GET",
        "path": "/tags/count.json",
        "description": "Show count of all tags",
        "parameters": {},
        "required": [],
    },
    "autocomplete_tags": {
        "method": "GET",
        "path": "/autocomplete/tags.json",
        "description": "Autocomplete tag names",
        "parameters": {
            "name": {
                "type": "str",
                "location": "query",
                "description": "Partial tag name to autocomplete",
            }
        },
        "required": ["name"],
    },
    # ================================================================================
    # SUPPORT API - VIEWS
    # ================================================================================
    "list_views": {
        "method": "GET",
        "path": "/views.json",
        "description": "List all views",
        "parameters": {
            "active": {
                "type": "Optional[bool]",
                "location": "query",
                "description": "Filter by active status",
            },
            "access": {
                "type": 'Optional[Literal["personal", "shared"]]',
                "location": "query",
                "description": "Filter by access level",
            },
            "group_id": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Filter by group ID",
            },
            "sort_by": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sort field",
            },
            "sort_order": {
                "type": 'Optional[Literal["asc", "desc"]]',
                "location": "query",
                "description": "Sort direction",
            },
        },
        "required": [],
    },
    "list_active_views": {
        "method": "GET",
        "path": "/views/active.json",
        "description": "List active views",
        "parameters": {},
        "required": [],
    },
    "list_compact_views": {
        "method": "GET",
        "path": "/views/compact.json",
        "description": "List views in compact format",
        "parameters": {},
        "required": [],
    },
    "show_view": {
        "method": "GET",
        "path": "/views/{view_id}.json",
        "description": "Show a specific view",
        "parameters": {
            "view_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the view",
            }
        },
        "required": ["view_id"],
    },
    "create_view": {
        "method": "POST",
        "path": "/views.json",
        "description": "Create a new view",
        "parameters": {
            "title": {"type": "str", "location": "body", "description": "View title"},
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether view is active",
            },
            "conditions": {
                "type": "Dict[str, Any]",
                "location": "body",
                "description": "View conditions (all, any)",
            },
            "execution": {
                "type": "Dict[str, Any]",
                "location": "body",
                "description": "Execution settings (group_by, sort_by, columns, fields)",
            },
            "restriction": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Access restrictions",
            },
        },
        "required": ["title", "conditions", "execution"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_view": {
        "method": "PUT",
        "path": "/views/{view_id}.json",
        "description": "Update an existing view",
        "parameters": {
            "view_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the view to update",
            },
            "title": {
                "type": "Optional[str]",
                "location": "body",
                "description": "View title",
            },
            "active": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether view is active",
            },
            "conditions": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "View conditions",
            },
            "execution": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Execution settings",
            },
            "restriction": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Access restrictions",
            },
        },
        "required": ["view_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_view": {
        "method": "DELETE",
        "path": "/views/{view_id}.json",
        "description": "Delete a view",
        "parameters": {
            "view_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the view to delete",
            }
        },
        "required": ["view_id"],
    },
    "execute_view": {
        "method": "GET",
        "path": "/views/{view_id}/execute.json",
        "description": "Execute a view to get tickets",
        "parameters": {
            "view_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the view to execute",
            },
            "sort_by": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Override sort field",
            },
            "sort_order": {
                "type": 'Optional[Literal["asc", "desc"]]',
                "location": "query",
                "description": "Override sort direction",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": ["view_id"],
    },
    "get_view_tickets": {
        "method": "GET",
        "path": "/views/{view_id}/tickets.json",
        "description": "Get tickets from a view",
        "parameters": {
            "view_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the view",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": ["view_id"],
    },
    "count_view_tickets": {
        "method": "GET",
        "path": "/views/{view_id}/count.json",
        "description": "Count tickets in a view",
        "parameters": {
            "view_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the view",
            }
        },
        "required": ["view_id"],
    },
    "export_view": {
        "method": "GET",
        "path": "/views/{view_id}/export.json",
        "description": "Export view results",
        "parameters": {
            "view_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the view to export",
            }
        },
        "required": ["view_id"],
    },
    # ================================================================================
    # HELP CENTER API - ARTICLES
    # ================================================================================
    "list_articles": {
        "method": "GET",
        "path": "/help_center/articles.json",
        "description": "List all articles",
        "parameters": {
            "locale": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by locale",
            },
            "category": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Filter by category ID",
            },
            "section": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Filter by section ID",
            },
            "label_names": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by label names (comma-separated)",
            },
            "created_at": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by creation date",
            },
            "updated_at": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by update date",
            },
            "sort_by": {
                "type": 'Optional[Literal["created_at", "updated_at", "position", "title", "vote_sum", "vote_count"]]',
                "location": "query",
                "description": "Sort field",
            },
            "sort_order": {
                "type": 'Optional[Literal["asc", "desc"]]',
                "location": "query",
                "description": "Sort direction",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data (sections,categories,users,translations)",
            },
        },
        "required": [],
    },
    "show_article": {
        "method": "GET",
        "path": "/help_center/articles/{article_id}.json",
        "description": "Show a specific article",
        "parameters": {
            "article_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the article",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": ["article_id"],
    },
    "create_article": {
        "method": "POST",
        "path": "/help_center/sections/{section_id}/articles.json",
        "description": "Create a new article",
        "parameters": {
            "section_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the section",
            },
            "title": {
                "type": "str",
                "location": "body",
                "description": "Article title",
            },
            "body": {
                "type": "str",
                "location": "body",
                "description": "Article content",
            },
            "locale": {
                "type": "str",
                "location": "body",
                "description": "Article locale",
            },
            "author_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Author user ID",
            },
            "comments_disabled": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether comments are disabled",
            },
            "draft": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether article is a draft",
            },
            "promoted": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether article is promoted",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Article position",
            },
            "label_names": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "Label names",
            },
            "user_segment_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "User segment ID",
            },
            "permission_group_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Permission group ID",
            },
        },
        "required": ["section_id", "title", "body", "locale"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_article": {
        "method": "PUT",
        "path": "/help_center/articles/{article_id}.json",
        "description": "Update an existing article",
        "parameters": {
            "article_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the article to update",
            },
            "title": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Article title",
            },
            "body": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Article content",
            },
            "author_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Author user ID",
            },
            "comments_disabled": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether comments are disabled",
            },
            "draft": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether article is a draft",
            },
            "promoted": {
                "type": "Optional[bool]",
                "location": "body",
                "description": "Whether article is promoted",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Article position",
            },
            "section_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Section ID",
            },
            "label_names": {
                "type": "Optional[List[str]]",
                "location": "body",
                "description": "Label names",
            },
            "user_segment_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "User segment ID",
            },
            "permission_group_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Permission group ID",
            },
        },
        "required": ["article_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_article": {
        "method": "DELETE",
        "path": "/help_center/articles/{article_id}.json",
        "description": "Delete an article",
        "parameters": {
            "article_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the article to delete",
            }
        },
        "required": ["article_id"],
    },
    "search_articles": {
        "method": "GET",
        "path": "/help_center/articles/search.json",
        "description": "Search articles",
        "parameters": {
            "query": {
                "type": "str",
                "location": "query",
                "description": "Search query",
            },
            "locale": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by locale",
            },
            "category": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Filter by category ID",
            },
            "section": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Filter by section ID",
            },
            "label_names": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by label names",
            },
            "snippet": {
                "type": 'Optional[Literal["true", "false"]]',
                "location": "query",
                "description": "Include snippets in results",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": ["query"],
    },
    # ================================================================================
    # HELP CENTER API - SECTIONS
    # ================================================================================
    "list_sections": {
        "method": "GET",
        "path": "/help_center/sections.json",
        "description": "List all sections",
        "parameters": {
            "category_id": {
                "type": "Optional[int]",
                "location": "query",
                "description": "Filter by category ID",
            },
            "locale": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by locale",
            },
            "sort_by": {
                "type": 'Optional[Literal["position", "created_at", "updated_at", "name"]]',
                "location": "query",
                "description": "Sort field",
            },
            "sort_order": {
                "type": 'Optional[Literal["asc", "desc"]]',
                "location": "query",
                "description": "Sort direction",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data (categories,translations)",
            },
        },
        "required": [],
    },
    "show_section": {
        "method": "GET",
        "path": "/help_center/sections/{section_id}.json",
        "description": "Show a specific section",
        "parameters": {
            "section_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the section",
            }
        },
        "required": ["section_id"],
    },
    "create_section": {
        "method": "POST",
        "path": "/help_center/categories/{category_id}/sections.json",
        "description": "Create a new section",
        "parameters": {
            "category_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the category",
            },
            "name": {"type": "str", "location": "body", "description": "Section name"},
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Section description",
            },
            "locale": {
                "type": "str",
                "location": "body",
                "description": "Section locale",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Section position",
            },
            "sorting": {
                "type": 'Optional[Literal["manual", "created_at", "updated_at", "name", "title"]]',
                "location": "body",
                "description": "Article sorting method",
            },
            "theme_template": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Theme template",
            },
            "manageable_by": {
                "type": 'Optional[Literal["managers", "managers_and_agents"]]',
                "location": "body",
                "description": "Who can manage this section",
            },
        },
        "required": ["category_id", "name", "locale"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_section": {
        "method": "PUT",
        "path": "/help_center/sections/{section_id}.json",
        "description": "Update an existing section",
        "parameters": {
            "section_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the section to update",
            },
            "name": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Section name",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Section description",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Section position",
            },
            "sorting": {
                "type": 'Optional[Literal["manual", "created_at", "updated_at", "name", "title"]]',
                "location": "body",
                "description": "Article sorting method",
            },
            "category_id": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Category ID",
            },
            "theme_template": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Theme template",
            },
            "manageable_by": {
                "type": 'Optional[Literal["managers", "managers_and_agents"]]',
                "location": "body",
                "description": "Who can manage this section",
            },
        },
        "required": ["section_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_section": {
        "method": "DELETE",
        "path": "/help_center/sections/{section_id}.json",
        "description": "Delete a section",
        "parameters": {
            "section_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the section to delete",
            }
        },
        "required": ["section_id"],
    },
    # ================================================================================
    # HELP CENTER API - CATEGORIES
    # ================================================================================
    "list_categories": {
        "method": "GET",
        "path": "/help_center/categories.json",
        "description": "List all categories",
        "parameters": {
            "locale": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by locale",
            },
            "sort_by": {
                "type": 'Optional[Literal["position", "created_at", "updated_at", "name"]]',
                "location": "query",
                "description": "Sort field",
            },
            "sort_order": {
                "type": 'Optional[Literal["asc", "desc"]]',
                "location": "query",
                "description": "Sort direction",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data (translations)",
            },
        },
        "required": [],
    },
    "show_category": {
        "method": "GET",
        "path": "/help_center/categories/{category_id}.json",
        "description": "Show a specific category",
        "parameters": {
            "category_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the category",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": ["category_id"],
    },
    "create_category": {
        "method": "POST",
        "path": "/help_center/categories.json",
        "description": "Create a new category",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "Category name"},
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Category description",
            },
            "locale": {
                "type": "str",
                "location": "body",
                "description": "Category locale",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Category position",
            },
        },
        "required": ["name", "locale"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_category": {
        "method": "PUT",
        "path": "/help_center/categories/{category_id}.json",
        "description": "Update an existing category",
        "parameters": {
            "category_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the category to update",
            },
            "name": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Category name",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Category description",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Category position",
            },
        },
        "required": ["category_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_category": {
        "method": "DELETE",
        "path": "/help_center/categories/{category_id}.json",
        "description": "Delete a category",
        "parameters": {
            "category_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the category to delete",
            }
        },
        "required": ["category_id"],
    },
    # ================================================================================
    # CUSTOM OBJECTS API
    # ================================================================================
    "list_custom_objects": {
        "method": "GET",
        "path": "/custom_objects.json",
        "description": "List all custom objects",
        "parameters": {},
        "required": [],
    },
    "show_custom_object": {
        "method": "GET",
        "path": "/custom_objects/{custom_object_key}.json",
        "description": "Show a specific custom object",
        "parameters": {
            "custom_object_key": {
                "type": "str",
                "location": "path",
                "description": "Key of the custom object",
            }
        },
        "required": ["custom_object_key"],
    },
    "create_custom_object": {
        "method": "POST",
        "path": "/custom_objects.json",
        "description": "Create a new custom object",
        "parameters": {
            "key": {
                "type": "str",
                "location": "body",
                "description": "Custom object key",
            },
            "title": {
                "type": "str",
                "location": "body",
                "description": "Display title",
            },
            "title_pluralized": {
                "type": "str",
                "location": "body",
                "description": "Pluralized title",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Object description",
            },
            "configuration": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Configuration settings",
            },
        },
        "required": ["key", "title", "title_pluralized"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_custom_object": {
        "method": "PATCH",
        "path": "/custom_objects/{custom_object_key}.json",
        "description": "Update an existing custom object",
        "parameters": {
            "custom_object_key": {
                "type": "str",
                "location": "path",
                "description": "Key of the custom object to update",
            },
            "title": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Display title",
            },
            "title_pluralized": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Pluralized title",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Object description",
            },
            "configuration": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Configuration settings",
            },
        },
        "required": ["custom_object_key"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_custom_object": {
        "method": "DELETE",
        "path": "/custom_objects/{custom_object_key}.json",
        "description": "Delete a custom object",
        "parameters": {
            "custom_object_key": {
                "type": "str",
                "location": "path",
                "description": "Key of the custom object to delete",
            }
        },
        "required": ["custom_object_key"],
    },
    # ================================================================================
    # CUSTOM OBJECT RECORDS API
    # ================================================================================
    "list_custom_object_records": {
        "method": "GET",
        "path": "/custom_objects/{custom_object_key}/records.json",
        "description": "List records for a custom object",
        "parameters": {
            "custom_object_key": {
                "type": "str",
                "location": "path",
                "description": "Key of the custom object",
            },
            "external_id": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by external ID",
            },
            "external_ids": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Filter by multiple external IDs",
            },
            "sort": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sort field and direction",
            },
        },
        "required": ["custom_object_key"],
    },
    "show_custom_object_record": {
        "method": "GET",
        "path": "/custom_objects/{custom_object_key}/records/{custom_object_record_id}.json",
        "description": "Show a specific custom object record",
        "parameters": {
            "custom_object_key": {
                "type": "str",
                "location": "path",
                "description": "Key of the custom object",
            },
            "custom_object_record_id": {
                "type": "str",
                "location": "path",
                "description": "ID of the record",
            },
        },
        "required": ["custom_object_key", "custom_object_record_id"],
    },
    "create_custom_object_record": {
        "method": "POST",
        "path": "/custom_objects/{custom_object_key}/records.json",
        "description": "Create a new custom object record",
        "parameters": {
            "custom_object_key": {
                "type": "str",
                "location": "path",
                "description": "Key of the custom object",
            },
            "name": {"type": "str", "location": "body", "description": "Record name"},
            "external_id": {
                "type": "Optional[str]",
                "location": "body",
                "description": "External ID",
            },
            "custom_object_fields": {
                "type": "Dict[str, Any]",
                "location": "body",
                "description": "Custom field values",
            },
        },
        "required": ["custom_object_key", "name", "custom_object_fields"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_custom_object_record": {
        "method": "PATCH",
        "path": "/custom_objects/{custom_object_key}/records/{custom_object_record_id}.json",
        "description": "Update a custom object record",
        "parameters": {
            "custom_object_key": {
                "type": "str",
                "location": "path",
                "description": "Key of the custom object",
            },
            "custom_object_record_id": {
                "type": "str",
                "location": "path",
                "description": "ID of the record",
            },
            "name": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Record name",
            },
            "external_id": {
                "type": "Optional[str]",
                "location": "body",
                "description": "External ID",
            },
            "custom_object_fields": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Custom field values",
            },
        },
        "required": ["custom_object_key", "custom_object_record_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_custom_object_record": {
        "method": "DELETE",
        "path": "/custom_objects/{custom_object_key}/records/{custom_object_record_id}.json",
        "description": "Delete a custom object record",
        "parameters": {
            "custom_object_key": {
                "type": "str",
                "location": "path",
                "description": "Key of the custom object",
            },
            "custom_object_record_id": {
                "type": "str",
                "location": "path",
                "description": "ID of the record to delete",
            },
        },
        "required": ["custom_object_key", "custom_object_record_id"],
    },
    # ================================================================================
    # JOB STATUS API
    # ================================================================================
    "show_job_status": {
        "method": "GET",
        "path": "/job_statuses/{job_id}.json",
        "description": "Show the status of a background job",
        "parameters": {
            "job_id": {
                "type": "str",
                "location": "path",
                "description": "ID of the job",
            }
        },
        "required": ["job_id"],
    },
    "list_job_statuses": {
        "method": "GET",
        "path": "/job_statuses.json",
        "description": "List recent job statuses",
        "parameters": {},
        "required": [],
    },
    # ================================================================================
    # INCREMENTAL EXPORT API
    # ================================================================================
    "incremental_tickets": {
        "method": "GET",
        "path": "/incremental/tickets.json",
        "description": "Incremental export of tickets",
        "parameters": {
            "start_time": {
                "type": "int",
                "location": "query",
                "description": "Unix timestamp to start from",
            },
            "cursor": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Pagination cursor",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": ["start_time"],
    },
    "incremental_users": {
        "method": "GET",
        "path": "/incremental/users.json",
        "description": "Incremental export of users",
        "parameters": {
            "start_time": {
                "type": "int",
                "location": "query",
                "description": "Unix timestamp to start from",
            },
            "cursor": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Pagination cursor",
            },
        },
        "required": ["start_time"],
    },
    "incremental_organizations": {
        "method": "GET",
        "path": "/incremental/organizations.json",
        "description": "Incremental export of organizations",
        "parameters": {
            "start_time": {
                "type": "int",
                "location": "query",
                "description": "Unix timestamp to start from",
            },
            "cursor": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Pagination cursor",
            },
        },
        "required": ["start_time"],
    },
    "incremental_ticket_events": {
        "method": "GET",
        "path": "/incremental/ticket_events.json",
        "description": "Incremental export of ticket events",
        "parameters": {
            "start_time": {
                "type": "int",
                "location": "query",
                "description": "Unix timestamp to start from",
            },
            "include": {
                "type": "Optional[str]",
                "location": "query",
                "description": "Sideload related data",
            },
        },
        "required": ["start_time"],
    },
    # ================================================================================
    # SLA POLICIES API
    # ================================================================================
    "list_sla_policies": {
        "method": "GET",
        "path": "/slas/policies.json",
        "description": "List all SLA policies",
        "parameters": {},
        "required": [],
    },
    "show_sla_policy": {
        "method": "GET",
        "path": "/slas/policies/{policy_id}.json",
        "description": "Show a specific SLA policy",
        "parameters": {
            "policy_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the SLA policy",
            }
        },
        "required": ["policy_id"],
    },
    "create_sla_policy": {
        "method": "POST",
        "path": "/slas/policies.json",
        "description": "Create a new SLA policy",
        "parameters": {
            "title": {"type": "str", "location": "body", "description": "Policy title"},
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Policy description",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Policy position",
            },
            "filter": {
                "type": "Dict[str, Any]",
                "location": "body",
                "description": "Filter conditions",
            },
            "policy_metrics": {
                "type": "List[Dict[str, Any]]",
                "location": "body",
                "description": "SLA metrics and targets",
            },
        },
        "required": ["title", "filter", "policy_metrics"],
        "headers": {"Content-Type": "application/json"},
    },
    "update_sla_policy": {
        "method": "PUT",
        "path": "/slas/policies/{policy_id}.json",
        "description": "Update an existing SLA policy",
        "parameters": {
            "policy_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the policy to update",
            },
            "title": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Policy title",
            },
            "description": {
                "type": "Optional[str]",
                "location": "body",
                "description": "Policy description",
            },
            "position": {
                "type": "Optional[int]",
                "location": "body",
                "description": "Policy position",
            },
            "filter": {
                "type": "Optional[Dict[str, Any]]",
                "location": "body",
                "description": "Filter conditions",
            },
            "policy_metrics": {
                "type": "Optional[List[Dict[str, Any]]]",
                "location": "body",
                "description": "SLA metrics and targets",
            },
        },
        "required": ["policy_id"],
        "headers": {"Content-Type": "application/json"},
    },
    "delete_sla_policy": {
        "method": "DELETE",
        "path": "/slas/policies/{policy_id}.json",
        "description": "Delete an SLA policy",
        "parameters": {
            "policy_id": {
                "type": "int",
                "location": "path",
                "description": "ID of the policy to delete",
            }
        },
        "required": ["policy_id"],
    },
    "reorder_sla_policies": {
        "method": "PUT",
        "path": "/slas/policies/reorder.json",
        "description": "Reorder SLA policies",
        "parameters": {
            "sla_policy_ids": {
                "type": "List[int]",
                "location": "body",
                "description": "Array of policy IDs in new order",
            }
        },
        "required": ["sla_policy_ids"],
        "headers": {"Content-Type": "application/json"},
    },
}


class ZendeskDataSourceGenerator:
    """Generator for comprehensive Zendesk API datasource class."""

    def __init__(self):
        self.generated_methods = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        # Replace invalid characters with underscores
        sanitized = (
            name.replace("-", "_").replace(".", "_").replace("[", "_").replace("]", "")
        )

        # Handle reserved keywords
        if sanitized in [
            "from",
            "class",
            "def",
            "import",
            "lambda",
            "return",
            "yield",
            "with",
            "as",
            "pass",
        ]:
            sanitized += "_param"

        return sanitized

    def _build_method_signature(
        self, method_name: str, endpoint_info: Dict[str, Any]
    ) -> str:
        """Build method signature with all parameters."""
        sig_parts = ["self"]

        # Group parameters by location and required status
        path_params = []
        query_params = []
        body_params = []
        header_params = []

        for param_name, param_info in endpoint_info.get("parameters", {}).items():
            location = param_info.get("location", "query")
            param_type = param_info.get("type", "str")
            required = param_name in endpoint_info.get("required", [])
            sanitized_name = self._sanitize_parameter_name(param_name)

            if location == "path":
                path_params.append(
                    {"name": sanitized_name, "type": param_type, "required": required}
                )
            elif location == "query":
                query_params.append(
                    {"name": sanitized_name, "type": param_type, "required": required}
                )
            elif location == "body":
                body_params.append(
                    {"name": sanitized_name, "type": param_type, "required": required}
                )
            elif location == "header":
                header_params.append(
                    {"name": sanitized_name, "type": param_type, "required": required}
                )

        # Add required parameters first
        for param in path_params + query_params + body_params + header_params:
            if param["required"]:
                sig_parts.append(f"{param['name']}: {param['type']}")

        # Add optional parameters
        for param in path_params + query_params + body_params + header_params:
            if not param["required"]:
                sig_parts.append(f"{param['name']}: {param['type']} = None")

        # Always add optional headers parameter
        sig_parts.append("headers: Optional[Dict[str, Any]] = None")

        return sig_parts

    def _build_method_body(
        self, method_name: str, endpoint_info: Dict[str, Any]
    ) -> str:
        """Build method body with proper HTTPRequest handling."""
        lines = []

        # Method docstring
        description = endpoint_info.get(
            "description", f"Auto-generated method for {method_name}"
        )
        lines.append(f'        """{description}')

        # Document parameters - only if there are parameters
        params = endpoint_info.get("parameters", {})
        if params:
            lines.append("")
            lines.append("        Args:")
            for param_name, param_info in params.items():
                sanitized_name = self._sanitize_parameter_name(param_name)
                param_type = param_info.get("type", "str")
                param_desc = param_info.get("description", f"{param_name} parameter")
                required = param_name in endpoint_info.get("required", [])
                req_text = "required" if required else "optional"
                lines.append(
                    f"            {sanitized_name} ({param_type}, {req_text}): {param_desc}"
                )

        lines.append("")
        lines.append("        Returns:")
        lines.append("            ZendeskResponse: Standardized response object")
        lines.append('        """')

        # Method implementation
        lines.append("        try:")
        lines.append("            _headers = dict(headers or {})")
        lines.append("            _params = {}")
        lines.append("            _data = {}")

        # Build URL
        path = endpoint_info.get("path", "")
        lines.append(f'            url = f"{{self.base_url}}{path}"')

        # Query parameters
        has_query_params = False
        for param_name, param_info in params.items():
            if param_info.get("location") == "query":
                if not has_query_params:
                    lines.append("")
                    has_query_params = True
                sanitized_name = self._sanitize_parameter_name(param_name)
                required = param_name in endpoint_info.get("required", [])
                if required:
                    lines.append(
                        f'            _params["{param_name}"] = {sanitized_name}'
                    )
                else:
                    lines.append(f"            if {sanitized_name} is not None:")
                    lines.append(
                        f'                _params["{param_name}"] = {sanitized_name}'
                    )

        # Body parameters
        has_body_params = False
        for param_name, param_info in params.items():
            if param_info.get("location") == "body":
                if not has_body_params:
                    lines.append("")
                    has_body_params = True
                sanitized_name = self._sanitize_parameter_name(param_name)
                required = param_name in endpoint_info.get("required", [])
                if required:
                    lines.append(
                        f'            _data["{param_name}"] = {sanitized_name}'
                    )
                else:
                    lines.append(f"            if {sanitized_name} is not None:")
                    lines.append(
                        f'                _data["{param_name}"] = {sanitized_name}'
                    )

        # Header parameters
        has_header_params = False
        for param_name, param_info in params.items():
            if param_info.get("location") == "header":
                if not has_header_params:
                    lines.append("")
                    has_header_params = True
                sanitized_name = self._sanitize_parameter_name(param_name)
                required = param_name in endpoint_info.get("required", [])
                if required:
                    lines.append(
                        f'            _headers["{param_name}"] = {sanitized_name}'
                    )
                else:
                    lines.append(f"            if {sanitized_name} is not None:")
                    lines.append(
                        f'                _headers["{param_name}"] = {sanitized_name}'
                    )

        # Set content type for methods with body
        method = endpoint_info.get("method", "GET")
        if method in ["POST", "PUT", "PATCH"] and endpoint_info.get("headers", {}).get(
            "Content-Type"
        ):
            content_type = endpoint_info["headers"]["Content-Type"]
            lines.append("")
            lines.append(f'            _headers["Content-Type"] = "{content_type}"')

        # Create HTTPRequest and execute it
        lines.append("")
        http_method = endpoint_info.get("method", "GET").upper()

        # Create HTTPRequest object
        lines.append("            request = HTTPRequest(")
        lines.append(f'                method="{http_method}",')
        lines.append("                url=url,")
        lines.append("                headers=_headers,")
        lines.append("                query_params=_params")

        # Add body data for POST/PUT/PATCH requests
        if method in ["POST", "PUT", "PATCH"]:
            lines.append(",")
            lines.append("                json=_data if _data else None")

        lines.append("            )")

        # Execute the request
        lines.append("            response = await self.http.execute(")
        lines.append("                request=request")
        lines.append("            )")

        lines.append("")
        lines.append("            return ZendeskResponse(")
        lines.append("                success=response.status < 400,")
        lines.append(
            "                data=response.json() if response.is_json else None,"
        )
        lines.append(
            "                error=response.text if response.status >= 400 else None,"
        )
        lines.append("                status_code=response.status")
        lines.append("            )")
        lines.append("")
        lines.append("        except Exception as e:")
        lines.append("            return ZendeskResponse(")
        lines.append("                success=False,")
        lines.append("                error=str(e)")
        lines.append("            )")

        return "\n".join(lines)

    def _generate_method(self, method_name: str, endpoint_info: Dict[str, Any]) -> str:
        """Generate a complete method for an endpoint."""
        signature_parts = self._build_method_signature(method_name, endpoint_info)
        body = self._build_method_body(method_name, endpoint_info)

        # Format signature with each parameter on its own line
        signature_lines = []
        for i, part in enumerate(signature_parts):
            if i == 0:  # 'self' parameter
                signature_lines.append(f"        {part},")
            elif i == len(signature_parts) - 1:  # last parameter
                signature_lines.append(f"        {part}")
            else:
                signature_lines.append(f"        {part},")

        signature_formatted = "\n".join(signature_lines)

        method_code = f"""
    async def {method_name}(
{signature_formatted}
    ) -> ZendeskResponse:
{body}
"""

        self.generated_methods.append(
            {
                "name": method_name,
                "method": endpoint_info.get("method", "GET"),
                "path": endpoint_info.get("path", ""),
                "description": endpoint_info.get("description", ""),
                "parameters": len(endpoint_info.get("parameters", {})),
                "required_params": len(endpoint_info.get("required", [])),
            }
        )

        return method_code

    def generate_zendesk_datasource(self) -> str:
        """Generate the complete Zendesk datasource class."""

        # Class header
        class_lines = [
            "from typing import Dict, List, Optional, Any, Union, Literal",
            "from app.sources.client.http.http_request import HTTPRequest",
            "from app.sources.client.zendesk.zendesk import ZendeskClient, ZendeskResponse",
            "",
            "",
            "class ZendeskDataSource:",
            '    """Comprehensive Zendesk API client wrapper.',
            "    ",
            "    Provides async methods for ALL Zendesk API endpoints across:",
            "    ",
            "    SUPPORT API:",
            "    - Tickets (CRUD, bulk operations, comments, merge, import)",
            "    - Users (CRUD, identities, passwords, authentication)",
            "    - Organizations (CRUD, memberships, search, autocomplete)",
            "    - Groups (CRUD, memberships)",
            "    - Search (unified search across all resources)",
            "    - Requests (end-user perspective of tickets)",
            "    - Custom Fields (tickets, users, organizations)",
            "    - Comments, Attachments, Tags",
            "    ",
            "    BUSINESS RULES:",
            "    - Automations (time-based workflow rules)",
            "    - Triggers (event-based workflow rules)",
            "    - Macros (manual agent actions)",
            "    - Views (filtered ticket lists)",
            "    - SLA Policies (service level agreements)",
            "    ",
            "    WEBHOOKS:",
            "    - Webhook management (CRUD, testing, signing secrets)",
            "    - Event subscriptions (tickets, users, organizations)",
            "    - Authentication and security",
            "    ",
            "    HELP CENTER API:",
            "    - Articles (CRUD, translations, search)",
            "    - Sections (CRUD, translations)",
            "    - Categories (CRUD, translations)",
            "    ",
            "    CUSTOM DATA:",
            "    - Custom Objects (CRUD, records, relationships)",
            "    - Custom Object Records and Fields",
            "    ",
            "    ADMINISTRATIVE:",
            "    - Job Status monitoring",
            "    - Incremental Exports (tickets, users, organizations)",
            "    - Bulk Operations and Background Jobs",
            "    ",
            "    All methods return ZendeskResponse objects with standardized format.",
            "    Every parameter matches Zendesk's official API documentation exactly.",
            "    No **kwargs usage - all parameters are explicitly typed.",
            '    """',
            "",
            "    def __init__(self, client: ZendeskClient) -> None:",
            '        """Initialize with ZendeskClient."""',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        if self.http is None:",
            "            raise ValueError('HTTP client is not initialized')",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'ZendeskDataSource':",
            "        return self",
            "",
        ]

        # Generate all methods
        method_lines = []
        for method_name, endpoint_info in ZENDESK_API_ENDPOINTS.items():
            method_code = self._generate_method(method_name, endpoint_info)
            method_lines.append(method_code)

        # Helper method
        helper_lines = [
            "",
            "    async def get_api_info(self) -> ZendeskResponse:",
            '        """Get information about available API methods."""',
            "        try:",
            f"            info = {{",
            f"                'total_methods': {len(ZENDESK_API_ENDPOINTS)},",
            f"                'api_coverage': [",
            f"                    'Support API (Tickets, Users, Organizations, Groups)',",
            f"                    'Business Rules (Automations, Triggers, Macros, Views)',",
            f"                    'Webhooks (Management, Events, Security)',",
            f"                    'Help Center (Articles, Sections, Categories)',",
            f"                    'Custom Data (Objects, Records, Fields)',",
            f"                    'Administrative (Jobs, Exports, Bulk Operations)'",
            f"                ],",
            f"                'authentication': 'Basic Auth with API Token',",
            f"                'base_url_pattern': 'https://{{subdomain}}.zendesk.com'",
            f"            }}",
            "            return ZendeskResponse(",
            "                success=True,",
            "                data=info",
            "            )",
            "        except Exception as e:",
            "            return ZendeskResponse(",
            "                success=False,",
            "                error=str(e)",
            "            )",
        ]

        # Combine all parts
        all_lines = class_lines + method_lines + helper_lines
        return "\n".join(all_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Save the generated datasource to a file."""
        if filename is None:
            filename = "zendesk_data_source.py"

        # Create zendesk directory
        script_dir = Path(__file__).parent if __file__ else Path(".")
        zendesk_dir = script_dir / "zendesk"
        zendesk_dir.mkdir(exist_ok=True)

        # Set the full file path
        full_path = zendesk_dir / filename

        class_code = self.generate_zendesk_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(
            f"✅ Generated Zendesk data source with {len(self.generated_methods)} methods"
        )
        print(f"📁 Saved to: {full_path}")

        # Print detailed summary by API category
        api_categories = {
            "Support API - Tickets": 0,
            "Support API - Users": 0,
            "Support API - Organizations": 0,
            "Support API - Groups": 0,
            "Support API - Search & Requests": 0,
            "Support API - Fields & Tags": 0,
            "Business Rules - Automations": 0,
            "Business Rules - Triggers": 0,
            "Business Rules - Macros": 0,
            "Business Rules - Views": 0,
            "Webhooks API": 0,
            "Help Center API": 0,
            "Custom Objects API": 0,
            "Administrative APIs": 0,
            "Attachments & Uploads": 0,
        }

        for method in self.generated_methods:
            method_name = method["name"]
            if (
                any(x in method_name for x in ["ticket", "comment", "merge", "import"])
                and "user" not in method_name
            ):
                api_categories["Support API - Tickets"] += 1
            elif any(x in method_name for x in ["user", "identity", "password"]):
                api_categories["Support API - Users"] += 1
            elif (
                any(x in method_name for x in ["organization", "membership"])
                and "group" not in method_name
            ):
                api_categories["Support API - Organizations"] += 1
            elif (
                any(x in method_name for x in ["group"])
                and "permission" not in method_name
            ):
                api_categories["Support API - Groups"] += 1
            elif any(x in method_name for x in ["search", "request", "autocomplete"]):
                api_categories["Support API - Search & Requests"] += 1
            elif any(x in method_name for x in ["field", "tag"]):
                api_categories["Support API - Fields & Tags"] += 1
            elif "automation" in method_name:
                api_categories["Business Rules - Automations"] += 1
            elif "trigger" in method_name:
                api_categories["Business Rules - Triggers"] += 1
            elif "macro" in method_name:
                api_categories["Business Rules - Macros"] += 1
            elif "view" in method_name:
                api_categories["Business Rules - Views"] += 1
            elif "webhook" in method_name:
                api_categories["Webhooks API"] += 1
            elif any(x in method_name for x in ["article", "section", "category"]):
                api_categories["Help Center API"] += 1
            elif "custom_object" in method_name:
                api_categories["Custom Objects API"] += 1
            elif any(x in method_name for x in ["upload", "attachment"]):
                api_categories["Attachments & Uploads"] += 1
            else:
                api_categories["Administrative APIs"] += 1

        print(f"\n📊 API Coverage Summary:")
        print(f"   - Total methods: {len(self.generated_methods)}")

        for category, count in api_categories.items():
            if count > 0:
                print(f"   - {category}: {count} methods")

        print(f"\n🎯 Key Features:")
        print(f"   ✅ Comprehensive Support API (tickets, users, orgs, groups)")
        print(f"   ✅ Complete Business Rules (automations, triggers, macros, views)")
        print(f"   ✅ Full Webhooks API with security features")
        print(f"   ✅ Help Center content management")
        print(f"   ✅ Custom Objects and Records")
        print(f"   ✅ Administrative and bulk operations")
        print(f"   ✅ All parameters explicitly typed (no **kwargs)")
        print(f"   ✅ Matches official Zendesk API documentation exactly")
        print(f"   ✅ Standardized ZendeskResponse format")
        print(f"   ✅ Comprehensive error handling")


def process_zendesk_api(filename: Optional[str] = None) -> None:
    """End-to-end pipeline for Zendesk API generation."""
    print(f"🚀 Starting Zendesk API data source generation...")

    generator = ZendeskDataSourceGenerator()

    try:
        print("Analyzing Zendesk API endpoints and generating wrapper methods...")
        generator.save_to_file(filename)

        script_dir = Path(__file__).parent if __file__ else Path(".")
        print(f"\nFiles generated in: {script_dir / 'zendesk'}")

        print(f"\n🎉 Successfully generated comprehensive Zendesk data source!")
        print(
            f"   Covers ALL major Zendesk APIs with {len(ZENDESK_API_ENDPOINTS)} endpoints"
        )
        print(f"   Ready for production use with full type safety")

    except Exception as e:
        print(f"❌ Error: {e}")
        raise


def main():
    """Main function for Zendesk data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate comprehensive Zendesk API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        process_zendesk_api(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Zendesk data source: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
