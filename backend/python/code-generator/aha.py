# ruff: noqa
"""
Aha! REST API Code Generator

Generates AhaDataSource class covering Aha! API v1:
- User profile and management
- Product management
- Feature CRUD operations
- Idea management and portals
- Release management
- Goal and initiative operations
- Epic management
- Requirement management
- Task management
- Comment operations
- Page/note management
- Workflow management
- Integration listing
- Custom fields
- Team management
- Strategy operations
- Audit and deletion tracking

The generated DataSource accepts an AhaClient and uses the client's
configured subdomain-based base URL. Methods are generated for all
API endpoints.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Aha! API Endpoints - organized by resource
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which is https://{subdomain}.aha.io/api/v1)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
#   version: API version tag
# ================================================================================

AHA_API_ENDPOINTS = {
    # ================================================================================
    # USERS
    # ================================================================================
    "get_current_user": {
        "method": "GET",
        "path": "/me",
        "description": "Get the current authenticated user details",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "list_users": {
        "method": "GET",
        "path": "/users",
        "description": "List all users in the account",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": [],
        "version": "v1",
    },
    "get_user": {
        "method": "GET",
        "path": "/users/{user_id}",
        "description": "Get a specific user by ID",
        "parameters": {
            "user_id": {"type": "str", "location": "path", "description": "The user ID"},
        },
        "required": ["user_id"],
        "version": "v1",
    },

    # ================================================================================
    # PRODUCTS
    # ================================================================================
    "list_products": {
        "method": "GET",
        "path": "/products",
        "description": "List all products in the account",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": [],
        "version": "v1",
    },
    "get_product": {
        "method": "GET",
        "path": "/products/{product_id}",
        "description": "Get a specific product by ID",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "list_product_users": {
        "method": "GET",
        "path": "/products/{product_id}/users",
        "description": "List all users for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "list_product_workflows": {
        "method": "GET",
        "path": "/products/{product_id}/workflows",
        "description": "List all workflows for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "list_product_teams": {
        "method": "GET",
        "path": "/products/{product_id}/teams",
        "description": "List all teams for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },

    # ================================================================================
    # FEATURES
    # ================================================================================
    "list_features": {
        "method": "GET",
        "path": "/features",
        "description": "List all features across all products",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
            "q": {"type": "Optional[str]", "location": "query", "description": "Search query string"},
            "assigned_to_user": {"type": "Optional[str]", "location": "query", "description": "Filter by assigned user"},
        },
        "required": [],
        "version": "v1",
    },
    "list_product_features": {
        "method": "GET",
        "path": "/products/{product_id}/features",
        "description": "List all features for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
            "q": {"type": "Optional[str]", "location": "query", "description": "Search query string"},
            "assigned_to_user": {"type": "Optional[str]", "location": "query", "description": "Filter by assigned user"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "get_feature": {
        "method": "GET",
        "path": "/features/{feature_id}",
        "description": "Get a specific feature by ID",
        "parameters": {
            "feature_id": {"type": "str", "location": "path", "description": "The feature ID"},
        },
        "required": ["feature_id"],
        "version": "v1",
    },
    "create_feature": {
        "method": "POST",
        "path": "/products/{product_id}/features",
        "description": "Create a new feature in a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
            "name": {"type": "str", "location": "body", "description": "The name of the feature"},
            "description": {"type": "Optional[str]", "location": "body", "description": "The feature description"},
            "workflow_status": {"type": "Optional[str]", "location": "body", "description": "The workflow status"},
            "assigned_to_user": {"type": "Optional[str]", "location": "body", "description": "User to assign the feature to"},
            "due_date": {"type": "Optional[str]", "location": "body", "description": "Due date in YYYY-MM-DD format"},
            "start_date": {"type": "Optional[str]", "location": "body", "description": "Start date in YYYY-MM-DD format"},
            "release": {"type": "Optional[str]", "location": "body", "description": "Release to associate the feature with"},
            "tags": {"type": "Optional[str]", "location": "body", "description": "Comma-separated list of tags"},
        },
        "required": ["product_id", "name"],
        "version": "v1",
    },
    "update_feature": {
        "method": "PUT",
        "path": "/features/{feature_id}",
        "description": "Update an existing feature",
        "parameters": {
            "feature_id": {"type": "str", "location": "path", "description": "The feature ID"},
            "name": {"type": "Optional[str]", "location": "body", "description": "The name of the feature"},
            "description": {"type": "Optional[str]", "location": "body", "description": "The feature description"},
            "workflow_status": {"type": "Optional[str]", "location": "body", "description": "The workflow status"},
            "assigned_to_user": {"type": "Optional[str]", "location": "body", "description": "User to assign the feature to"},
            "due_date": {"type": "Optional[str]", "location": "body", "description": "Due date in YYYY-MM-DD format"},
            "start_date": {"type": "Optional[str]", "location": "body", "description": "Start date in YYYY-MM-DD format"},
            "release": {"type": "Optional[str]", "location": "body", "description": "Release to associate the feature with"},
            "tags": {"type": "Optional[str]", "location": "body", "description": "Comma-separated list of tags"},
        },
        "required": ["feature_id"],
        "version": "v1",
    },
    "list_feature_comments": {
        "method": "GET",
        "path": "/features/{feature_id}/comments",
        "description": "List comments on a feature",
        "parameters": {
            "feature_id": {"type": "str", "location": "path", "description": "The feature ID"},
        },
        "required": ["feature_id"],
        "version": "v1",
    },
    "list_feature_tasks": {
        "method": "GET",
        "path": "/features/{feature_id}/tasks",
        "description": "List tasks for a feature",
        "parameters": {
            "feature_id": {"type": "str", "location": "path", "description": "The feature ID"},
        },
        "required": ["feature_id"],
        "version": "v1",
    },
    "list_feature_requirements": {
        "method": "GET",
        "path": "/features/{feature_id}/requirements",
        "description": "List requirements for a feature",
        "parameters": {
            "feature_id": {"type": "str", "location": "path", "description": "The feature ID"},
        },
        "required": ["feature_id"],
        "version": "v1",
    },
    "convert_feature_to_epic": {
        "method": "POST",
        "path": "/features/{feature_id}/convert_to_epic",
        "description": "Convert a feature to an epic",
        "parameters": {
            "feature_id": {"type": "str", "location": "path", "description": "The feature ID to convert"},
        },
        "required": ["feature_id"],
        "version": "v1",
    },

    # ================================================================================
    # IDEAS
    # ================================================================================
    "list_ideas": {
        "method": "GET",
        "path": "/ideas",
        "description": "List all ideas across all products",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": [],
        "version": "v1",
    },
    "list_product_ideas": {
        "method": "GET",
        "path": "/products/{product_id}/ideas",
        "description": "List all ideas for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "get_idea": {
        "method": "GET",
        "path": "/ideas/{idea_id}",
        "description": "Get a specific idea by ID",
        "parameters": {
            "idea_id": {"type": "str", "location": "path", "description": "The idea ID"},
        },
        "required": ["idea_id"],
        "version": "v1",
    },
    "list_idea_comments": {
        "method": "GET",
        "path": "/ideas/{idea_id}/comments",
        "description": "List comments on an idea",
        "parameters": {
            "idea_id": {"type": "str", "location": "path", "description": "The idea ID"},
        },
        "required": ["idea_id"],
        "version": "v1",
    },
    "list_idea_endorsements": {
        "method": "GET",
        "path": "/ideas/{idea_id}/endorsements",
        "description": "List endorsements for an idea",
        "parameters": {
            "idea_id": {"type": "str", "location": "path", "description": "The idea ID"},
        },
        "required": ["idea_id"],
        "version": "v1",
    },
    "list_idea_tasks": {
        "method": "GET",
        "path": "/ideas/{idea_id}/tasks",
        "description": "List tasks for an idea",
        "parameters": {
            "idea_id": {"type": "str", "location": "path", "description": "The idea ID"},
        },
        "required": ["idea_id"],
        "version": "v1",
    },
    "list_idea_portals": {
        "method": "GET",
        "path": "/idea_portals",
        "description": "List all idea portals",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "list_product_idea_portals": {
        "method": "GET",
        "path": "/products/{product_id}/idea_portals",
        "description": "List idea portals for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "list_idea_categories": {
        "method": "GET",
        "path": "/products/{product_id}/idea_categories",
        "description": "List idea categories for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },

    # ================================================================================
    # RELEASES
    # ================================================================================
    "list_product_releases": {
        "method": "GET",
        "path": "/products/{product_id}/releases",
        "description": "List all releases for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "get_release": {
        "method": "GET",
        "path": "/releases/{release_id}",
        "description": "Get a specific release by ID",
        "parameters": {
            "release_id": {"type": "str", "location": "path", "description": "The release ID"},
        },
        "required": ["release_id"],
        "version": "v1",
    },
    "list_release_features": {
        "method": "GET",
        "path": "/releases/{release_id}/features",
        "description": "List features in a release",
        "parameters": {
            "release_id": {"type": "str", "location": "path", "description": "The release ID"},
        },
        "required": ["release_id"],
        "version": "v1",
    },
    "list_release_epics": {
        "method": "GET",
        "path": "/releases/{release_id}/epics",
        "description": "List epics in a release",
        "parameters": {
            "release_id": {"type": "str", "location": "path", "description": "The release ID"},
        },
        "required": ["release_id"],
        "version": "v1",
    },
    "list_release_comments": {
        "method": "GET",
        "path": "/releases/{release_id}/comments",
        "description": "List comments on a release",
        "parameters": {
            "release_id": {"type": "str", "location": "path", "description": "The release ID"},
        },
        "required": ["release_id"],
        "version": "v1",
    },
    "list_release_tasks": {
        "method": "GET",
        "path": "/releases/{release_id}/tasks",
        "description": "List tasks for a release",
        "parameters": {
            "release_id": {"type": "str", "location": "path", "description": "The release ID"},
        },
        "required": ["release_id"],
        "version": "v1",
    },
    "list_release_phases": {
        "method": "GET",
        "path": "/release_phases",
        "description": "List all release phases",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "get_release_phase": {
        "method": "GET",
        "path": "/release_phases/{release_phase_id}",
        "description": "Get a specific release phase by ID",
        "parameters": {
            "release_phase_id": {"type": "str", "location": "path", "description": "The release phase ID"},
        },
        "required": ["release_phase_id"],
        "version": "v1",
    },

    # ================================================================================
    # GOALS
    # ================================================================================
    "list_goals": {
        "method": "GET",
        "path": "/goals",
        "description": "List all goals across all products",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": [],
        "version": "v1",
    },
    "list_product_goals": {
        "method": "GET",
        "path": "/products/{product_id}/goals",
        "description": "List all goals for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "get_goal": {
        "method": "GET",
        "path": "/goals/{goal_id}",
        "description": "Get a specific goal by ID",
        "parameters": {
            "goal_id": {"type": "str", "location": "path", "description": "The goal ID"},
        },
        "required": ["goal_id"],
        "version": "v1",
    },
    "list_goal_features": {
        "method": "GET",
        "path": "/goals/{goal_id}/features",
        "description": "List features linked to a goal",
        "parameters": {
            "goal_id": {"type": "str", "location": "path", "description": "The goal ID"},
        },
        "required": ["goal_id"],
        "version": "v1",
    },
    "list_goal_epics": {
        "method": "GET",
        "path": "/goals/{goal_id}/epics",
        "description": "List epics linked to a goal",
        "parameters": {
            "goal_id": {"type": "str", "location": "path", "description": "The goal ID"},
        },
        "required": ["goal_id"],
        "version": "v1",
    },
    "list_goal_initiatives": {
        "method": "GET",
        "path": "/goals/{goal_id}/initiatives",
        "description": "List initiatives linked to a goal",
        "parameters": {
            "goal_id": {"type": "str", "location": "path", "description": "The goal ID"},
        },
        "required": ["goal_id"],
        "version": "v1",
    },
    "list_goal_releases": {
        "method": "GET",
        "path": "/goals/{goal_id}/releases",
        "description": "List releases linked to a goal",
        "parameters": {
            "goal_id": {"type": "str", "location": "path", "description": "The goal ID"},
        },
        "required": ["goal_id"],
        "version": "v1",
    },
    "list_goal_key_results": {
        "method": "GET",
        "path": "/goals/{goal_id}/key_results",
        "description": "List key results for a goal",
        "parameters": {
            "goal_id": {"type": "str", "location": "path", "description": "The goal ID"},
        },
        "required": ["goal_id"],
        "version": "v1",
    },
    "list_goal_comments": {
        "method": "GET",
        "path": "/goals/{goal_id}/comments",
        "description": "List comments on a goal",
        "parameters": {
            "goal_id": {"type": "str", "location": "path", "description": "The goal ID"},
        },
        "required": ["goal_id"],
        "version": "v1",
    },

    # ================================================================================
    # INITIATIVES
    # ================================================================================
    "list_initiatives": {
        "method": "GET",
        "path": "/initiatives",
        "description": "List all initiatives across all products",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": [],
        "version": "v1",
    },
    "list_product_initiatives": {
        "method": "GET",
        "path": "/products/{product_id}/initiatives",
        "description": "List all initiatives for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "get_initiative": {
        "method": "GET",
        "path": "/initiatives/{initiative_id}",
        "description": "Get a specific initiative by ID",
        "parameters": {
            "initiative_id": {"type": "str", "location": "path", "description": "The initiative ID"},
        },
        "required": ["initiative_id"],
        "version": "v1",
    },

    # ================================================================================
    # EPICS
    # ================================================================================
    "list_epics": {
        "method": "GET",
        "path": "/epics",
        "description": "List all epics across all products",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": [],
        "version": "v1",
    },
    "list_product_epics": {
        "method": "GET",
        "path": "/products/{product_id}/epics",
        "description": "List all epics for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "get_epic": {
        "method": "GET",
        "path": "/epics/{epic_id}",
        "description": "Get a specific epic by ID",
        "parameters": {
            "epic_id": {"type": "str", "location": "path", "description": "The epic ID"},
        },
        "required": ["epic_id"],
        "version": "v1",
    },
    "list_epic_features": {
        "method": "GET",
        "path": "/epics/{epic_id}/features",
        "description": "List features in an epic",
        "parameters": {
            "epic_id": {"type": "str", "location": "path", "description": "The epic ID"},
        },
        "required": ["epic_id"],
        "version": "v1",
    },
    "list_epic_comments": {
        "method": "GET",
        "path": "/epics/{epic_id}/comments",
        "description": "List comments on an epic",
        "parameters": {
            "epic_id": {"type": "str", "location": "path", "description": "The epic ID"},
        },
        "required": ["epic_id"],
        "version": "v1",
    },
    "list_epic_tasks": {
        "method": "GET",
        "path": "/epics/{epic_id}/tasks",
        "description": "List tasks for an epic",
        "parameters": {
            "epic_id": {"type": "str", "location": "path", "description": "The epic ID"},
        },
        "required": ["epic_id"],
        "version": "v1",
    },

    # ================================================================================
    # REQUIREMENTS
    # ================================================================================
    "get_requirement": {
        "method": "GET",
        "path": "/requirements/{requirement_id}",
        "description": "Get a specific requirement by ID",
        "parameters": {
            "requirement_id": {"type": "str", "location": "path", "description": "The requirement ID"},
        },
        "required": ["requirement_id"],
        "version": "v1",
    },
    "list_requirement_comments": {
        "method": "GET",
        "path": "/requirements/{requirement_id}/comments",
        "description": "List comments on a requirement",
        "parameters": {
            "requirement_id": {"type": "str", "location": "path", "description": "The requirement ID"},
        },
        "required": ["requirement_id"],
        "version": "v1",
    },
    "list_requirement_tasks": {
        "method": "GET",
        "path": "/requirements/{requirement_id}/tasks",
        "description": "List tasks for a requirement",
        "parameters": {
            "requirement_id": {"type": "str", "location": "path", "description": "The requirement ID"},
        },
        "required": ["requirement_id"],
        "version": "v1",
    },

    # ================================================================================
    # TASKS
    # ================================================================================
    "list_tasks": {
        "method": "GET",
        "path": "/tasks",
        "description": "List all tasks",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": [],
        "version": "v1",
    },
    "get_task": {
        "method": "GET",
        "path": "/tasks/{task_id}",
        "description": "Get a specific task by ID",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The task ID"},
        },
        "required": ["task_id"],
        "version": "v1",
    },
    "list_product_tasks": {
        "method": "GET",
        "path": "/products/{product_id}/tasks",
        "description": "List all tasks for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "list_user_tasks": {
        "method": "GET",
        "path": "/users/{user_id}/tasks",
        "description": "List tasks assigned to a user",
        "parameters": {
            "user_id": {"type": "str", "location": "path", "description": "The user ID"},
        },
        "required": ["user_id"],
        "version": "v1",
    },

    # ================================================================================
    # COMMENTS
    # ================================================================================
    "get_comment": {
        "method": "GET",
        "path": "/comments/{comment_id}",
        "description": "Get a specific comment by ID",
        "parameters": {
            "comment_id": {"type": "str", "location": "path", "description": "The comment ID"},
        },
        "required": ["comment_id"],
        "version": "v1",
    },
    "list_product_comments": {
        "method": "GET",
        "path": "/products/{project_id}/comments",
        "description": "List comments for a product",
        "parameters": {
            "project_id": {"type": "str", "location": "path", "description": "The product/project ID"},
        },
        "required": ["project_id"],
        "version": "v1",
    },

    # ================================================================================
    # PAGES / NOTES
    # ================================================================================
    "list_product_pages": {
        "method": "GET",
        "path": "/products/{product_id}/pages",
        "description": "List all pages/notes for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },

    # ================================================================================
    # INTEGRATIONS
    # ================================================================================
    "list_product_integrations": {
        "method": "GET",
        "path": "/products/{product_id}/integrations",
        "description": "List all integrations for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },

    # ================================================================================
    # CUSTOM FIELDS
    # ================================================================================
    "list_custom_field_definitions": {
        "method": "GET",
        "path": "/custom_field_definitions",
        "description": "List all custom field definitions",
        "parameters": {},
        "required": [],
        "version": "v1",
    },

    # ================================================================================
    # TEAMS
    # ================================================================================
    "list_teams": {
        "method": "GET",
        "path": "/teams",
        "description": "List all teams",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "get_team": {
        "method": "GET",
        "path": "/teams/{team_id}",
        "description": "Get a specific team by ID",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The team ID"},
        },
        "required": ["team_id"],
        "version": "v1",
    },
    "list_team_members": {
        "method": "GET",
        "path": "/team_members",
        "description": "List all team members",
        "parameters": {},
        "required": [],
        "version": "v1",
    },

    # ================================================================================
    # WORKFLOWS
    # ================================================================================
    "get_workflow": {
        "method": "GET",
        "path": "/workflows/{workflow_id}",
        "description": "Get a specific workflow by ID",
        "parameters": {
            "workflow_id": {"type": "str", "location": "path", "description": "The workflow ID"},
        },
        "required": ["workflow_id"],
        "version": "v1",
    },

    # ================================================================================
    # AUDIT / DELETION TRACKING
    # ================================================================================
    "list_audits": {
        "method": "GET",
        "path": "/audits",
        "description": "List audit events",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": [],
        "version": "v1",
    },
    "list_deletions": {
        "method": "GET",
        "path": "/deletions",
        "description": "List recently deleted records",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": [],
        "version": "v1",
    },

    # ================================================================================
    # STRATEGY
    # ================================================================================
    "list_strategy_models": {
        "method": "GET",
        "path": "/strategy_models",
        "description": "List all strategy models",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "get_strategy_model": {
        "method": "GET",
        "path": "/strategy_models/{strategy_model_id}",
        "description": "Get a specific strategy model by ID",
        "parameters": {
            "strategy_model_id": {"type": "str", "location": "path", "description": "The strategy model ID"},
        },
        "required": ["strategy_model_id"],
        "version": "v1",
    },
    "list_strategy_visions": {
        "method": "GET",
        "path": "/strategy_visions",
        "description": "List all strategy visions",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "get_strategy_vision": {
        "method": "GET",
        "path": "/strategy_visions/{strategy_vision_id}",
        "description": "Get a specific strategy vision by ID",
        "parameters": {
            "strategy_vision_id": {"type": "str", "location": "path", "description": "The strategy vision ID"},
        },
        "required": ["strategy_vision_id"],
        "version": "v1",
    },
    "list_strategy_positions": {
        "method": "GET",
        "path": "/strategy_positions",
        "description": "List all strategy positions",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "get_strategy_position": {
        "method": "GET",
        "path": "/strategy_positions/{strategy_position_id}",
        "description": "Get a specific strategy position by ID",
        "parameters": {
            "strategy_position_id": {"type": "str", "location": "path", "description": "The strategy position ID"},
        },
        "required": ["strategy_position_id"],
        "version": "v1",
    },

    # ================================================================================
    # CREATIVE BRIEFS
    # ================================================================================
    "list_product_creative_briefs": {
        "method": "GET",
        "path": "/products/{product_id}/creative_briefs",
        "description": "List creative briefs for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "get_creative_brief": {
        "method": "GET",
        "path": "/creative_briefs/{creative_brief_id}",
        "description": "Get a specific creative brief by ID",
        "parameters": {
            "creative_brief_id": {"type": "str", "location": "path", "description": "The creative brief ID"},
        },
        "required": ["creative_brief_id"],
        "version": "v1",
    },

    # ================================================================================
    # PERSONAS
    # ================================================================================
    "list_product_personas": {
        "method": "GET",
        "path": "/products/{product_id}/personas",
        "description": "List personas for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },

    # ================================================================================
    # COMPETITORS
    # ================================================================================
    "get_competitor": {
        "method": "GET",
        "path": "/competitors/{competitor_id}",
        "description": "Get a specific competitor by ID",
        "parameters": {
            "competitor_id": {"type": "str", "location": "path", "description": "The competitor ID"},
        },
        "required": ["competitor_id"],
        "version": "v1",
    },

    # ================================================================================
    # SCHEDULES
    # ================================================================================
    "list_schedules": {
        "method": "GET",
        "path": "/schedules",
        "description": "List all schedules",
        "parameters": {},
        "required": [],
        "version": "v1",
    },

    # ================================================================================
    # SCREEN DEFINITIONS
    # ================================================================================
    "list_screen_definitions": {
        "method": "GET",
        "path": "/screen_definitions",
        "description": "List all screen definitions",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "get_screen_definition": {
        "method": "GET",
        "path": "/screen_definitions/{screen_definition_id}",
        "description": "Get a specific screen definition by ID",
        "parameters": {
            "screen_definition_id": {"type": "str", "location": "path", "description": "The screen definition ID"},
        },
        "required": ["screen_definition_id"],
        "version": "v1",
    },
}


class AhaDataSourceGenerator:
    """Generator for comprehensive Aha! REST API datasource class.

    Generates methods for Aha! API v1 endpoints.
    The generated DataSource class accepts an AhaClient whose base URL
    is https://{subdomain}.aha.io/api/v1.
    """

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        return sanitized

    def _build_query_params(self, endpoint_info: Dict) -> List[str]:
        """Build query parameter handling code."""
        lines = ["        query_params: dict[str, Any] = {}"]
        required_set = set(endpoint_info.get("required", []))

        for param_name, param_info in endpoint_info["parameters"].items():
            if param_info["location"] == "query":
                sanitized_name = self._sanitize_parameter_name(param_name)
                api_name = param_info.get("api_name", param_name)
                is_required = param_name in required_set

                if is_required:
                    if "bool" in param_info["type"]:
                        lines.append(
                            f"        query_params['{api_name}'] = str({sanitized_name}).lower()"
                        )
                    else:
                        lines.append(
                            f"        query_params['{api_name}'] = {sanitized_name}"
                        )
                elif "Optional[bool]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = str({sanitized_name}).lower()",
                    ])
                elif "Optional[int]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = str({sanitized_name})",
                    ])
                elif "List[" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}[]'] = {sanitized_name}",
                    ])
                else:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = {sanitized_name}",
                    ])

        return lines

    def _build_path_formatting(self, path: str, endpoint_info: Dict) -> str:
        """Build URL path with parameter substitution."""
        path_params = [
            name
            for name, info in endpoint_info["parameters"].items()
            if info["location"] == "path"
        ]

        if path_params:
            format_dict = ", ".join(
                f"{param}={self._sanitize_parameter_name(param)}"
                for param in path_params
            )
            return f'        url = self.base_url + "{path}".format({format_dict})'
        else:
            return f'        url = self.base_url + "{path}"'

    def _build_request_body(self, endpoint_info: Dict) -> List[str]:
        """Build request body handling."""
        body_params = {
            name: info
            for name, info in endpoint_info["parameters"].items()
            if info["location"] == "body"
        }

        if not body_params:
            return []

        lines = ["        body: dict[str, Any] = {}"]

        for param_name, param_info in body_params.items():
            sanitized_name = self._sanitize_parameter_name(param_name)

            if param_name in endpoint_info["required"]:
                lines.append(f"        body['{param_name}'] = {sanitized_name}")
            else:
                lines.extend([
                    f"        if {sanitized_name} is not None:",
                    f"            body['{param_name}'] = {sanitized_name}",
                ])

        return lines

    @staticmethod
    def _modernize_type(type_str: str) -> str:
        """Convert typing-style annotations to modern Python 3.10+ syntax."""
        if type_str.startswith("Optional[") and type_str.endswith("]"):
            inner = type_str[len("Optional["):-1]
            inner = AhaDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = AhaDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                AhaDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = AhaDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                AhaDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"list[{modernized}]"
        if type_str == "List":
            return "list"
        return type_str

    @staticmethod
    def _split_type_args(s: str) -> List[str]:
        """Split type arguments respecting nested brackets."""
        parts = []
        depth = 0
        current = ""
        for ch in s:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current.strip())
        return parts

    def _generate_method_signature(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate method signature with explicit parameters."""
        params = ["self"]
        has_any_bool = False

        required_non_bool: List[str] = []
        required_bool: List[str] = []
        for param_name in endpoint_info["required"]:
            if param_name in endpoint_info["parameters"]:
                param_info = endpoint_info["parameters"][param_name]
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                param_str = f"{sanitized_name}: {modern_type}"
                if "bool" in param_info.get("type", ""):
                    required_bool.append(param_str)
                    has_any_bool = True
                else:
                    required_non_bool.append(param_str)

        optional_params: List[str] = []
        for param_name, param_info in endpoint_info["parameters"].items():
            if param_name not in endpoint_info["required"]:
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                if "| None" not in modern_type:
                    modern_type = f"{modern_type} | None"
                optional_params.append(f"{sanitized_name}: {modern_type} = None")
                if "bool" in param_info.get("type", ""):
                    has_any_bool = True

        params.extend(required_non_bool)
        if has_any_bool and (required_bool or optional_params):
            params.append("*")
        params.extend(required_bool)
        params.extend(optional_params)

        signature_params = ",\n        ".join(params)
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> AhaResponse:"

    def _generate_method_docstring(self, endpoint_info: Dict) -> List[str]:
        """Generate method docstring."""
        version = endpoint_info.get("version", "v1")
        lines = [f'        """{endpoint_info["description"]} (API {version})', ""]

        if endpoint_info["parameters"]:
            lines.append("        Args:")
            for param_name, param_info in endpoint_info["parameters"].items():
                sanitized_name = self._sanitize_parameter_name(param_name)
                lines.append(
                    f"            {sanitized_name}: {param_info['description']}"
                )
            lines.append("")

        lines.extend([
            "        Returns:",
            "            AhaResponse with operation result",
            '        """',
        ])

        return lines

    def _generate_method(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate a complete method for an API endpoint."""
        lines = []

        lines.append(self._generate_method_signature(method_name, endpoint_info))
        lines.extend(self._generate_method_docstring(endpoint_info))

        has_query = any(
            info["location"] == "query"
            for info in endpoint_info["parameters"].values()
        )
        if has_query:
            query_lines = self._build_query_params(endpoint_info)
            lines.extend(query_lines)
            lines.append("")

        lines.append(self._build_path_formatting(endpoint_info["path"], endpoint_info))

        body_lines = self._build_request_body(endpoint_info)
        if body_lines:
            lines.append("")
            lines.extend(body_lines)

        lines.append("")
        lines.append("        try:")
        lines.append("            request = HTTPRequest(")
        lines.append(f'                method="{endpoint_info["method"]}",')
        lines.append("                url=url,")
        lines.append('                headers={"Content-Type": "application/json"},')
        if has_query:
            lines.append("                query=query_params,")
        if body_lines:
            lines.append("                body=body,")
        lines.append("            )")
        lines.extend([
            "            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]",
            "            response_data = response.json() if response.text() else None",
            "            return AhaResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return AhaResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
            "version": endpoint_info.get("version", "v1"),
        })

        return "\n".join(lines)

    def generate_aha_datasource(self) -> str:
        """Generate the complete Aha! datasource class."""

        class_lines = [
            '"""',
            "Aha! REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Aha! REST API v1 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.aha.aha import AhaClient, AhaResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class AhaDataSource:",
            '    """Aha! REST API DataSource',
            "",
            "    Provides async wrapper methods for Aha! REST API operations:",
            "    - User profile and management",
            "    - Product management",
            "    - Feature CRUD operations",
            "    - Idea management and portals",
            "    - Release management",
            "    - Goal and initiative operations",
            "    - Epic management",
            "    - Requirement management",
            "    - Task management",
            "    - Comment operations",
            "    - Page/note management",
            "    - Workflow management",
            "    - Integration listing",
            "    - Custom fields",
            "    - Team management",
            "    - Strategy operations",
            "    - Audit and deletion tracking",
            "",
            "    The base URL is https://{subdomain}.aha.io/api/v1.",
            "",
            "    All methods return AhaResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: AhaClient) -> None:",
            '        """Initialize with AhaClient.',
            "",
            "        Args:",
            "            client: AhaClient instance with configured authentication and subdomain",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'AhaDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> AhaClient:",
            '        """Return the underlying AhaClient."""',
            "        return self._client",
            "",
        ]

        for method_name, endpoint_info in AHA_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Aha! datasource to a file."""
        if filename is None:
            filename = "aha.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        aha_dir = script_dir.parent / "app" / "sources" / "external" / "aha"
        aha_dir.mkdir(parents=True, exist_ok=True)

        full_path = aha_dir / filename

        class_code = self.generate_aha_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Aha! data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")


def main():
    """Main function for Aha! data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Aha! REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = AhaDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Aha! data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
