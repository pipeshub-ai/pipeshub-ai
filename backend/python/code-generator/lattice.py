# ruff: noqa
"""
Lattice API Code Generator
Generates comprehensive LatticeDataSource class covering ALL Lattice APIs:

TALENT API (v1):
- Competencies (get by ID)
- Custom Attributes (get by ID, values)
- Departments (get by ID, list all)
- Feedback (get by ID, list all)
- Goals (CRUD, updates, progress tracking)
- Me (current user info)
- Questions (get by ID, revisions)
- Review Cycles (get by ID, list all, reviewees, reviews)
- Reviewees (get by ID, reviews)
- Tags (get by ID, list all)
- Tasks (get by ID)
- Updates (get by ID, list all)
- Users (get by ID, list all, custom attributes, direct reports, goals, tasks)

HRIS API (v2):
- Employees (CRUD, list all, get fields)
- Time Off Requests (list all, get by ID)
- Time Off Policies (list all)
- Time Off Reports (list all with filtering)

Pagination:
- v1 uses cursor-based pagination (limit, startingAfter, endingCursor, hasMore)
- v2 uses offset-based pagination (limit, offset, total)

Rate Limits:
- v1: 240 requests per minute
- v2: 200 requests per minute / 3000 requests per hour

All methods have explicit parameter signatures with no **kwargs usage.
Every parameter matches Lattice's official API documentation exactly.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# ================================================================================
# LATTICE TALENT API v1 ENDPOINTS
# ================================================================================

LATTICE_TALENT_API_ENDPOINTS = {

    # --- Competencies ---
    'get_competency': {
        'method': 'GET',
        'path': '/v1/competency/{competency_id}',
        'description': 'Returns a competency with the given id. Returns 404 if not found',
        'parameters': {
            'competency_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the competency'},
        },
        'required': ['competency_id'],
    },

    # --- Custom Attributes ---
    'get_custom_attribute': {
        'method': 'GET',
        'path': '/v1/customAttribute/{custom_attribute_id}',
        'description': 'Returns the custom attribute with the given id. Returns 404 if not found',
        'parameters': {
            'custom_attribute_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the custom attribute'},
        },
        'required': ['custom_attribute_id'],
    },

    'get_custom_attribute_value': {
        'method': 'GET',
        'path': '/v1/customAttributeValue/{custom_attribute_value_id}',
        'description': 'Returns the custom attribute value with the given id. Returns 404 if not found',
        'parameters': {
            'custom_attribute_value_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the custom attribute value'},
        },
        'required': ['custom_attribute_value_id'],
    },

    # --- Departments ---
    'get_department': {
        'method': 'GET',
        'path': '/v1/department/{department_id}',
        'description': 'Returns a department with the given id. Returns 404 if not found',
        'parameters': {
            'department_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the department'},
        },
        'required': ['department_id'],
    },

    'list_departments': {
        'method': 'GET',
        'path': '/v1/departments',
        'description': 'Returns a paginated list of all departments in Lattice',
        'parameters': {
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
        },
        'required': [],
        'query_key_map': {'starting_after': 'startingAfter'},
    },

    # --- Feedback ---
    'get_feedback': {
        'method': 'GET',
        'path': '/v1/feedback/{feedback_id}',
        'description': 'Returns a feedback with the given id. Returns 404 if not found',
        'parameters': {
            'feedback_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the feedback'},
        },
        'required': ['feedback_id'],
    },

    'list_feedbacks': {
        'method': 'GET',
        'path': '/v1/feedbacks',
        'description': 'Returns a paginated list of all continuous feedback in Lattice. Newest feedback returned first',
        'parameters': {
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
            'only_public': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Filter to only return public feedback'},
        },
        'required': [],
        'query_key_map': {'starting_after': 'startingAfter', 'only_public': 'onlyPublic'},
    },

    # --- Goals ---
    'get_goal': {
        'method': 'GET',
        'path': '/v1/goal/{goal_id}',
        'description': 'Returns a goal with the given id. Returns 404 if not found',
        'parameters': {
            'goal_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the goal'},
        },
        'required': ['goal_id'],
    },

    'list_goals': {
        'method': 'GET',
        'path': '/v1/goals',
        'description': 'Returns a paginated list of goals in Lattice',
        'parameters': {
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
            'state': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter goals by state (e.g. active, draft, completed, archived)'},
        },
        'required': [],
        'query_key_map': {'starting_after': 'startingAfter'},
    },

    'create_goal': {
        'method': 'POST',
        'path': '/v1/goals',
        'description': 'Creates a new goal with the provided information',
        'parameters': {
            'name': {'type': 'str', 'location': 'body', 'description': 'The name/objective of the goal'},
            'description': {'type': 'Optional[str]', 'location': 'body', 'description': 'The markdown text description of the goal'},
            'start_date': {'type': 'Optional[str]', 'location': 'body', 'description': 'The start date of the goal (YYYY-MM-DD)'},
            'due_date': {'type': 'Optional[str]', 'location': 'body', 'description': 'The due date of the goal (YYYY-MM-DD)'},
            'priority': {'type': 'Optional[int]', 'location': 'body', 'description': 'The priority of the goal'},
            'private': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Whether the goal is private'},
            'owner_ids': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'List of user IDs who own this goal'},
            'okr_type': {'type': 'Optional[str]', 'location': 'body', 'description': 'The OKR type of the goal'},
            'amount_type': {'type': 'Optional[str]', 'location': 'body', 'description': 'The type of amount being tracked'},
            'starting_amount': {'type': 'Optional[float]', 'location': 'body', 'description': 'The starting amount when the goal was set'},
            'goal_amount': {'type': 'Optional[float]', 'location': 'body', 'description': 'The target amount to reach'},
            'company_goal': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Whether this is a company-wide goal'},
            'department_id': {'type': 'Optional[str]', 'location': 'body', 'description': 'The department ID if this is a department goal'},
            'departments_visible_to_ids': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'List of department IDs that can view this goal'},
            'goal_cycle_id': {'type': 'Optional[str]', 'location': 'body', 'description': 'The goal cycle ID to associate with'},
            'tag_names': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'List of tag names to associate with the goal'},
            'is_draft': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Whether this goal is a draft'},
            'parent_id': {'type': 'Optional[str]', 'location': 'body', 'description': 'The parent goal ID in the goal tree'},
        },
        'required': ['name'],
        'headers': {'Content-Type': 'application/json'},
        'body_key_map': {
            'start_date': 'startDate',
            'due_date': 'dueDate',
            'owner_ids': 'ownerIds',
            'okr_type': 'okrType',
            'amount_type': 'amountType',
            'starting_amount': 'startingAmount',
            'goal_amount': 'goalAmount',
            'company_goal': 'companyGoal',
            'department_id': 'departmentId',
            'departments_visible_to_ids': 'departmentsVisibleToIds',
            'goal_cycle_id': 'goalCycleId',
            'tag_names': 'tagNames',
            'is_draft': 'isDraft',
            'parent_id': 'parentId',
        },
    },

    'update_goal': {
        'method': 'PUT',
        'path': '/v1/goals/{goal_id}',
        'description': 'Updates an existing goal with the provided information',
        'parameters': {
            'goal_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the goal to update'},
            'name': {'type': 'Optional[str]', 'location': 'body', 'description': 'The name/objective of the goal'},
            'description': {'type': 'Optional[str]', 'location': 'body', 'description': 'The markdown text description of the goal'},
            'start_date': {'type': 'Optional[str]', 'location': 'body', 'description': 'The start date of the goal (YYYY-MM-DD)'},
            'due_date': {'type': 'Optional[str]', 'location': 'body', 'description': 'The due date of the goal (YYYY-MM-DD)'},
            'priority': {'type': 'Optional[int]', 'location': 'body', 'description': 'The priority of the goal'},
            'private': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Whether the goal is private'},
            'owner_ids': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'List of user IDs who own this goal'},
            'okr_type': {'type': 'Optional[str]', 'location': 'body', 'description': 'The OKR type of the goal'},
            'amount_type': {'type': 'Optional[str]', 'location': 'body', 'description': 'The type of amount being tracked'},
            'starting_amount': {'type': 'Optional[float]', 'location': 'body', 'description': 'The starting amount when the goal was set'},
            'goal_amount': {'type': 'Optional[float]', 'location': 'body', 'description': 'The target amount to reach'},
            'company_goal': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Whether this is a company-wide goal'},
            'department_id': {'type': 'Optional[str]', 'location': 'body', 'description': 'The department ID if this is a department goal'},
            'departments_visible_to_ids': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'List of department IDs that can view this goal'},
            'goal_cycle_id': {'type': 'Optional[str]', 'location': 'body', 'description': 'The goal cycle ID to associate with'},
            'tag_names': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'List of tag names to associate with the goal'},
            'is_draft': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Whether this goal is a draft'},
            'parent_id': {'type': 'Optional[str]', 'location': 'body', 'description': 'The parent goal ID in the goal tree'},
        },
        'required': ['goal_id'],
        'headers': {'Content-Type': 'application/json'},
        'body_key_map': {
            'start_date': 'startDate',
            'due_date': 'dueDate',
            'owner_ids': 'ownerIds',
            'okr_type': 'okrType',
            'amount_type': 'amountType',
            'starting_amount': 'startingAmount',
            'goal_amount': 'goalAmount',
            'company_goal': 'companyGoal',
            'department_id': 'departmentId',
            'departments_visible_to_ids': 'departmentsVisibleToIds',
            'goal_cycle_id': 'goalCycleId',
            'tag_names': 'tagNames',
            'is_draft': 'isDraft',
            'parent_id': 'parentId',
        },
    },

    'list_all_goal_updates': {
        'method': 'GET',
        'path': '/v1/goals/updates',
        'description': 'Retrieves all goal updates across all goals in the company with pagination support',
        'parameters': {
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
        },
        'required': [],
        'query_key_map': {'starting_after': 'startingAfter'},
    },

    'create_goal_update': {
        'method': 'POST',
        'path': '/v1/goals/{goal_id}/update',
        'description': 'Creates a progress update for an existing goal',
        'parameters': {
            'goal_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the goal to update'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'The comment for the progress update'},
            'status': {'type': 'Optional[str]', 'location': 'body', 'description': 'The status of the goal update'},
            'increment': {'type': 'Optional[float]', 'location': 'body', 'description': 'The amount to increment the goal progress by'},
            'complete': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Mark the goal as complete'},
            'incomplete': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Mark the goal as incomplete'},
        },
        'required': ['goal_id'],
        'headers': {'Content-Type': 'application/json'},
    },

    'list_goal_updates': {
        'method': 'GET',
        'path': '/v1/goals/{goal_id}/updates',
        'description': 'Retrieves all progress updates for a specific goal with pagination support',
        'parameters': {
            'goal_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the goal'},
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
        },
        'required': ['goal_id'],
        'query_key_map': {'starting_after': 'startingAfter'},
    },

    # --- Me ---
    'get_me': {
        'method': 'GET',
        'path': '/v1/me',
        'description': 'Returns the current user the API token is associated with',
        'parameters': {},
        'required': [],
    },

    # --- Questions ---
    'get_question': {
        'method': 'GET',
        'path': '/v1/question/{question_id}',
        'description': 'Returns a review question with the given id. Returns 404 if not found',
        'parameters': {
            'question_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the question'},
        },
        'required': ['question_id'],
    },

    'get_question_revision': {
        'method': 'GET',
        'path': '/v1/questionRevision/{question_revision_id}',
        'description': 'Returns a question revision with the given id. Returns 404 if not found',
        'parameters': {
            'question_revision_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the question revision'},
        },
        'required': ['question_revision_id'],
    },

    # --- Review Cycles ---
    'get_review_cycle': {
        'method': 'GET',
        'path': '/v1/reviewCycle/{review_cycle_id}',
        'description': 'Returns a review cycle with the given id. Returns 404 if not found',
        'parameters': {
            'review_cycle_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the review cycle'},
        },
        'required': ['review_cycle_id'],
    },

    'list_review_cycle_reviewees': {
        'method': 'GET',
        'path': '/v1/reviewCycle/{review_cycle_id}/reviewees',
        'description': 'Returns a paginated list of all reviewees in a review cycle',
        'parameters': {
            'review_cycle_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the review cycle'},
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
        },
        'required': ['review_cycle_id'],
        'query_key_map': {'starting_after': 'startingAfter'},
    },

    'list_review_cycle_reviews': {
        'method': 'GET',
        'path': '/v1/reviewCycle/{review_cycle_id}/reviews',
        'description': 'Returns a paginated list of all reviews for a review cycle',
        'parameters': {
            'review_cycle_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the review cycle'},
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
            'order_direction': {'type': 'Optional[str]', 'location': 'query', 'description': 'Sort direction for results (asc or desc)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Offset for pagination'},
        },
        'required': ['review_cycle_id'],
        'query_key_map': {'starting_after': 'startingAfter', 'order_direction': 'orderDirection'},
    },

    'list_review_cycles': {
        'method': 'GET',
        'path': '/v1/reviewCycles',
        'description': 'Returns a paginated list of all review cycles in Lattice',
        'parameters': {
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
        },
        'required': [],
        'query_key_map': {'starting_after': 'startingAfter'},
    },

    # --- Reviewees ---
    'get_reviewee': {
        'method': 'GET',
        'path': '/v1/reviewee/{reviewee_id}',
        'description': 'Returns a reviewee with the given id. Returns 404 if not found',
        'parameters': {
            'reviewee_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the reviewee'},
        },
        'required': ['reviewee_id'],
    },

    'list_reviewee_reviews': {
        'method': 'GET',
        'path': '/v1/reviewee/{reviewee_id}/reviews',
        'description': 'Returns a paginated list of all reviews for a reviewee',
        'parameters': {
            'reviewee_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the reviewee'},
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
        },
        'required': ['reviewee_id'],
        'query_key_map': {'starting_after': 'startingAfter'},
    },

    # --- Tags ---
    'get_tag': {
        'method': 'GET',
        'path': '/v1/tag/{tag_id}',
        'description': 'Returns a tag with the given id. Returns 404 if not found',
        'parameters': {
            'tag_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the tag'},
        },
        'required': ['tag_id'],
    },

    'list_tags': {
        'method': 'GET',
        'path': '/v1/tags',
        'description': 'Returns a paginated list of all tags in Lattice',
        'parameters': {
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
        },
        'required': [],
        'query_key_map': {'starting_after': 'startingAfter'},
    },

    # --- Tasks ---
    'get_task': {
        'method': 'GET',
        'path': '/v1/task/{task_id}',
        'description': 'Returns a task with the given id. Returns 404 if not found',
        'parameters': {
            'task_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the task'},
        },
        'required': ['task_id'],
    },

    # --- Updates ---
    'get_update': {
        'method': 'GET',
        'path': '/v1/update/{update_id}',
        'description': 'Returns an Update with the given id. Returns 404 if not found',
        'parameters': {
            'update_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the update'},
        },
        'required': ['update_id'],
    },

    'list_updates': {
        'method': 'GET',
        'path': '/v1/updates',
        'description': 'Returns a paginated list of all Updates in Lattice',
        'parameters': {
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
        },
        'required': [],
        'query_key_map': {'starting_after': 'startingAfter'},
    },

    # --- Users ---
    'get_user': {
        'method': 'GET',
        'path': '/v1/user/{user_id}',
        'description': 'Returns a user with the given id. Returns 404 if not found',
        'parameters': {
            'user_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the user'},
        },
        'required': ['user_id'],
    },

    'list_user_custom_attributes': {
        'method': 'GET',
        'path': '/v1/user/{user_id}/customAttributes',
        'description': "Returns a list of the user's custom attributes",
        'parameters': {
            'user_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the user'},
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
        },
        'required': ['user_id'],
        'query_key_map': {'starting_after': 'startingAfter'},
    },

    'list_user_direct_reports': {
        'method': 'GET',
        'path': '/v1/user/{user_id}/directReports',
        'description': 'Returns a list of users that report to a user',
        'parameters': {
            'user_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the user'},
        },
        'required': ['user_id'],
    },

    'list_user_goals': {
        'method': 'GET',
        'path': '/v1/user/{user_id}/goals',
        'description': 'Returns a paginated list of all goals owned by a user',
        'parameters': {
            'user_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the user'},
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
            'state': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter goals by state (e.g. active, draft, completed, archived)'},
        },
        'required': ['user_id'],
        'query_key_map': {'starting_after': 'startingAfter'},
    },

    'list_user_tasks': {
        'method': 'GET',
        'path': '/v1/user/{user_id}/tasks',
        'description': 'Returns a paginated list of tasks for a user',
        'parameters': {
            'user_id': {'type': 'str', 'location': 'path', 'description': 'The ID of the user'},
        },
        'required': ['user_id'],
    },

    'list_users': {
        'method': 'GET',
        'path': '/v1/users',
        'description': "Returns a paginated list of users in Lattice. By default returns active users. A status of null retrieves all users regardless of status",
        'parameters': {
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of objects to return (1-100, default 10)'},
            'starting_after': {'type': 'Optional[str]', 'location': 'query', 'description': 'Cursor for pagination from previous response endingCursor'},
            'status': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter users by status (active, inactive, null for all)'},
        },
        'required': [],
        'query_key_map': {'starting_after': 'startingAfter'},
    },
}


# ================================================================================
# LATTICE HRIS API v2 ENDPOINTS
# ================================================================================

LATTICE_HRIS_API_ENDPOINTS = {

    # --- Employees ---
    'list_employees': {
        'method': 'GET',
        'path': '/v2/employees',
        'description': 'Returns a paginated list of all employees. Uses offset-based pagination',
        'parameters': {
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Maximum records per response (1-100, default 25)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Pagination offset (default 0)'},
        },
        'required': [],
    },

    'create_employee': {
        'method': 'POST',
        'path': '/v2/employees',
        'description': 'Creates a new employee with the provided information. Returns the new employee UUID',
        'parameters': {
            'personal': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Personal info: birthdate, company_employee_id, legal_first_name, legal_middle_name, legal_last_name, preferred_first_name, preferred_last_name'},
            'contact_information': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Contact info: personal_email, phone_number, work_email, address_line_1, address_line_2, address_city, address_state, address_country, address_postal_code'},
            'employment_details': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Employment details: employment_status, employment_type, start_date, termination_date, termination_reason, termination_type'},
            'role_details': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Role details: department, manager, effective_at, job_title'},
            'pay_types': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Pay info: base_pay_amount, base_pay_currency, base_pay_effective_at, base_pay_schedule, base_pay_payment_type'},
            'demographic': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Demographic info: gender_identity, binary_sex'},
            'sensitive_data': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Sensitive data: ssn'},
            'custom_categories': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Custom category fields defined by the organization'},
        },
        'required': [],
        'headers': {'Content-Type': 'application/json'},
    },

    'get_employee': {
        'method': 'GET',
        'path': '/v2/employees/{employee_id}',
        'description': 'Returns a single employee by UUID',
        'parameters': {
            'employee_id': {'type': 'str', 'location': 'path', 'description': 'The UUID of the employee'},
        },
        'required': ['employee_id'],
    },

    'update_employee': {
        'method': 'PATCH',
        'path': '/v2/employees/{employee_id}',
        'description': 'Updates an employee by UUID. Only include fields to update. Returns 204 No Content on success',
        'parameters': {
            'employee_id': {'type': 'str', 'location': 'path', 'description': 'The UUID of the employee to update'},
            'personal': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Personal info to update'},
            'contact_information': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Contact info to update'},
            'employment_details': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Employment details to update'},
            'role_details': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Role details to update'},
            'pay_types': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Pay info to update'},
            'demographic': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Demographic info to update'},
            'sensitive_data': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Sensitive data to update'},
            'custom_categories': {'type': 'Optional[Dict[str, Any]]', 'location': 'body', 'description': 'Custom category fields to update'},
        },
        'required': ['employee_id'],
        'headers': {'Content-Type': 'application/json'},
    },

    'get_employee_fields': {
        'method': 'GET',
        'path': '/v2/employee-fields',
        'description': 'Returns all employee field definitions including custom fields, types, and options',
        'parameters': {},
        'required': [],
    },

    # --- Time Off ---
    'list_time_off_requests': {
        'method': 'GET',
        'path': '/v2/time-off/requests',
        'description': 'Returns a paginated list of time off requests with optional filtering',
        'parameters': {
            'employee_ids': {'type': 'Optional[List[str]]', 'location': 'query', 'description': 'Filter by employee IDs'},
            'start_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by start date (YYYY-MM-DD)'},
            'end_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by end date (YYYY-MM-DD)'},
            'statuses': {'type': 'Optional[List[str]]', 'location': 'query', 'description': 'Filter by statuses: PENDING, APPROVED, REJECTED, CANCELLED, PENDING_CANCELLATION'},
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Maximum records per response (1-100, default 25)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Pagination offset (default 0)'},
        },
        'required': [],
    },

    'get_time_off_request': {
        'method': 'GET',
        'path': '/v2/time-off/requests/{time_off_request_id}',
        'description': 'Returns a single time off request by UUID',
        'parameters': {
            'time_off_request_id': {'type': 'str', 'location': 'path', 'description': 'The UUID of the time off request'},
        },
        'required': ['time_off_request_id'],
    },

    'list_time_off_policies': {
        'method': 'GET',
        'path': '/v2/time-off/policies',
        'description': 'Returns all time off policies including policy type and accrual type',
        'parameters': {},
        'required': [],
    },

    'list_time_off_reports': {
        'method': 'GET',
        'path': '/v2/time-off/reports',
        'description': 'Returns time off balance reports with optional filtering',
        'parameters': {
            'employee_ids': {'type': 'Optional[List[str]]', 'location': 'query', 'description': 'Filter by specific employee IDs'},
            'policy_ids': {'type': 'Optional[List[str]]', 'location': 'query', 'description': 'Filter by specific policy IDs'},
            'range_start_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'Start date for report range (YYYY-MM-DD)'},
            'range_end_date': {'type': 'Optional[str]', 'location': 'query', 'description': 'End date for report range (YYYY-MM-DD)'},
            'limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Maximum records per response (1-100, default 25)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Pagination offset (default 0)'},
        },
        'required': [],
    },
}


# Merge all endpoints for complete coverage
LATTICE_API_ENDPOINTS = {**LATTICE_TALENT_API_ENDPOINTS, **LATTICE_HRIS_API_ENDPOINTS}


# ================================================================================
# API PATH TO METHOD MAPPING (for validation)
# ================================================================================

EXPECTED_API_PATHS = {
    # Talent API v1
    'GET /v1/competency/{id}': 'get_competency',
    'GET /v1/customAttribute/{id}': 'get_custom_attribute',
    'GET /v1/customAttributeValue/{id}': 'get_custom_attribute_value',
    'GET /v1/department/{id}': 'get_department',
    'GET /v1/departments': 'list_departments',
    'GET /v1/feedback/{id}': 'get_feedback',
    'GET /v1/feedbacks': 'list_feedbacks',
    'GET /v1/goal/{id}': 'get_goal',
    'GET /v1/goals': 'list_goals',
    'POST /v1/goals': 'create_goal',
    'GET /v1/goals/updates': 'list_all_goal_updates',
    'PUT /v1/goals/{id}': 'update_goal',
    'POST /v1/goals/{id}/update': 'create_goal_update',
    'GET /v1/goals/{id}/updates': 'list_goal_updates',
    'GET /v1/me': 'get_me',
    'GET /v1/question/{id}': 'get_question',
    'GET /v1/questionRevision/{id}': 'get_question_revision',
    'GET /v1/reviewCycle/{id}': 'get_review_cycle',
    'GET /v1/reviewCycle/{id}/reviewees': 'list_review_cycle_reviewees',
    'GET /v1/reviewCycle/{id}/reviews': 'list_review_cycle_reviews',
    'GET /v1/reviewCycles': 'list_review_cycles',
    'GET /v1/reviewee/{id}': 'get_reviewee',
    'GET /v1/reviewee/{id}/reviews': 'list_reviewee_reviews',
    'GET /v1/tag/{id}': 'get_tag',
    'GET /v1/tags': 'list_tags',
    'GET /v1/task/{id}': 'get_task',
    'GET /v1/update/{id}': 'get_update',
    'GET /v1/updates': 'list_updates',
    'GET /v1/user/{id}': 'get_user',
    'GET /v1/user/{id}/customAttributes': 'list_user_custom_attributes',
    'GET /v1/user/{id}/directReports': 'list_user_direct_reports',
    'GET /v1/user/{id}/goals': 'list_user_goals',
    'GET /v1/user/{id}/tasks': 'list_user_tasks',
    'GET /v1/users': 'list_users',
    # HRIS API v2
    'GET /v2/employees': 'list_employees',
    'POST /v2/employees': 'create_employee',
    'GET /v2/employees/{id}': 'get_employee',
    'PATCH /v2/employees/{id}': 'update_employee',
    'GET /v2/employee-fields': 'get_employee_fields',
    'GET /v2/time-off/requests': 'list_time_off_requests',
    'GET /v2/time-off/requests/{id}': 'get_time_off_request',
    'GET /v2/time-off/policies': 'list_time_off_policies',
    'GET /v2/time-off/reports': 'list_time_off_reports',
}


class LatticeDataSourceGenerator:
    """Generates the LatticeDataSource class with all API methods."""

    def __init__(self):
        self.generated_methods: List[Dict[str, Any]] = []

    def _build_method_signature(self, method_name: str, endpoint_info: Dict[str, Any]) -> List[str]:
        """Build method signature parts."""
        params = endpoint_info.get('parameters', {})
        required = endpoint_info.get('required', [])

        signature_parts = ['self']

        # Required parameters first
        for param_name, param_info in params.items():
            if param_name in required:
                signature_parts.append(f'{param_name}: {param_info["type"]}')

        # Optional parameters after
        for param_name, param_info in params.items():
            if param_name not in required:
                signature_parts.append(f'{param_name}: {param_info["type"]} = None')

        return signature_parts

    def _build_method_body(self, method_name: str, endpoint_info: Dict[str, Any]) -> str:
        """Build method body with request construction and execution."""
        params = endpoint_info.get('parameters', {})
        description = endpoint_info.get('description', '')
        query_key_map = endpoint_info.get('query_key_map', {})
        body_key_map = endpoint_info.get('body_key_map', {})

        lines = []

        # Docstring
        lines.append(f'        """{description}')
        lines.append('')
        lines.append('        Args:')
        for param_name, param_info in params.items():
            lines.append(f'            {param_name}: {param_info["description"]}')
        lines.append('')
        lines.append('        Returns:')
        lines.append('            LatticeResponse with operation result')
        lines.append('        """')
        lines.append('        try:')
        lines.append('            _params = {}')
        lines.append('            _data = {}')
        lines.append('            _headers = {}')

        # Build URL
        path = endpoint_info.get('path', '')
        lines.append(f'            url = f"{{self.base_url}}{path}"')

        # Query parameters
        has_query_params = False
        for param_name, param_info in params.items():
            if param_info.get('location') == 'query':
                if not has_query_params:
                    lines.append('')
                    has_query_params = True
                api_key = query_key_map.get(param_name, param_name)
                required = param_name in endpoint_info.get('required', [])
                if required:
                    lines.append(f'            _params["{api_key}"] = {param_name}')
                else:
                    lines.append(f'            if {param_name} is not None:')
                    lines.append(f'                _params["{api_key}"] = {param_name}')

        # Body parameters
        has_body_params = False
        for param_name, param_info in params.items():
            if param_info.get('location') == 'body':
                if not has_body_params:
                    lines.append('')
                    has_body_params = True
                api_key = body_key_map.get(param_name, param_name)
                required = param_name in endpoint_info.get('required', [])
                if required:
                    lines.append(f'            _data["{api_key}"] = {param_name}')
                else:
                    lines.append(f'            if {param_name} is not None:')
                    lines.append(f'                _data["{api_key}"] = {param_name}')

        # Set content type for methods with body
        method = endpoint_info.get('method', 'GET')
        if method in ['POST', 'PUT', 'PATCH'] and endpoint_info.get('headers', {}).get('Content-Type'):
            content_type = endpoint_info['headers']['Content-Type']
            lines.append('')
            lines.append(f'            _headers["Content-Type"] = "{content_type}"')

        # Create HTTPRequest and execute
        lines.append('')
        http_method = endpoint_info.get('method', 'GET').upper()

        lines.append('            request = HTTPRequest(')
        lines.append(f'                method="{http_method}",')
        lines.append('                url=url,')
        lines.append('                headers=_headers,')

        if method in ['POST', 'PUT', 'PATCH']:
            lines.append('                body=_data if _data else None,')

        lines.append('                query_params=_params')
        lines.append('            )')

        # Execute the request
        lines.append('            response = await self.http.execute(')
        lines.append('                request=request')
        lines.append('            )')

        lines.append('')
        lines.append('            return LatticeResponse(')
        lines.append('                success=response.status < 400,')
        lines.append('                data=response.json() if response.is_json and response.status < 400 else None,')
        lines.append('                error=response.text() if response.status >= 400 else None,')
        lines.append('                status_code=response.status')
        lines.append('            )')
        lines.append('')
        lines.append('        except Exception as e:')
        lines.append('            return LatticeResponse(')
        lines.append('                success=False,')
        lines.append('                error=str(e)')
        lines.append('            )')

        return '\n'.join(lines)

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

        signature_formatted = '\n'.join(signature_lines)

        method_code = f"""
    async def {method_name}(
{signature_formatted}
    ) -> LatticeResponse:
{body}
"""

        self.generated_methods.append({
            'name': method_name,
            'method': endpoint_info.get('method', 'GET'),
            'path': endpoint_info.get('path', ''),
            'description': endpoint_info.get('description', ''),
            'parameters': len(endpoint_info.get('parameters', {})),
            'required_params': len(endpoint_info.get('required', []))
        })

        return method_code

    def generate_lattice_datasource(self) -> str:
        """Generate the complete Lattice datasource class."""

        # Class header
        class_lines = [
            "from typing import Any, Dict, List, Optional",
            "",
            "from app.sources.client.http.http_request import HTTPRequest",
            "from app.sources.client.lattice.lattice import LatticeClient, LatticeResponse",
            "",
            "",
            "class LatticeDataSource:",
            '    """Comprehensive Lattice API client wrapper.',
            '',
            '    Provides async methods for ALL Lattice API endpoints across:',
            '',
            '    TALENT API (v1):',
            '    - Competencies (get by ID)',
            '    - Custom Attributes (get by ID, values)',
            '    - Departments (get by ID, list all)',
            '    - Feedback (get by ID, list all with filters)',
            '    - Goals (CRUD, updates, progress tracking)',
            '    - Me (current authenticated user)',
            '    - Questions (get by ID, revisions)',
            '    - Review Cycles (get by ID, list all, reviewees, reviews)',
            '    - Reviewees (get by ID, reviews)',
            '    - Tags (get by ID, list all)',
            '    - Tasks (get by ID)',
            '    - Updates (get by ID, list all)',
            '    - Users (get by ID, list all, custom attributes, direct reports, goals, tasks)',
            '',
            '    HRIS API (v2):',
            '    - Employees (CRUD, list all, get fields)',
            '    - Time Off Requests (list all, get by ID)',
            '    - Time Off Policies (list all)',
            '    - Time Off Reports (list all with filtering)',
            '',
            '    Pagination:',
            '    - v1: cursor-based (limit, startingAfter, endingCursor, hasMore)',
            '    - v2: offset-based (limit, offset, total)',
            '',
            '    Rate Limits:',
            '    - v1: 240 requests/minute',
            '    - v2: 200 requests/minute, 3000 requests/hour',
            '',
            '    All methods return LatticeResponse objects with standardized format.',
            '    Every parameter matches Lattice official API documentation exactly.',
            '    No **kwargs usage - all parameters are explicitly typed.',
            '    """',
            "",
            "    def __init__(self, client: LatticeClient) -> None:",
            '        """Initialize with LatticeClient."""',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        if self.http is None:",
            "            raise ValueError('HTTP client is not initialized')",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'LatticeDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
        ]

        # Generate all methods
        method_lines = []
        for method_name, endpoint_info in LATTICE_API_ENDPOINTS.items():
            method_code = self._generate_method(method_name, endpoint_info)
            method_lines.append(method_code)

        # Helper method
        helper_lines = [
            "",
            "    async def get_api_info(self) -> LatticeResponse:",
            '        """Get information about available API methods."""',
            "        try:",
            f"            info = {{",
            f"                'total_methods': {len(LATTICE_API_ENDPOINTS)},",
            f"                'talent_api_v1_methods': {len(LATTICE_TALENT_API_ENDPOINTS)},",
            f"                'hris_api_v2_methods': {len(LATTICE_HRIS_API_ENDPOINTS)},",
            f"                'api_coverage': [",
            f"                    'Talent API v1: Competencies, Custom Attributes, Departments',",
            f"                    'Talent API v1: Feedback, Goals (CRUD + Updates)',",
            f"                    'Talent API v1: Me, Questions, Question Revisions',",
            f"                    'Talent API v1: Review Cycles, Reviewees, Reviews',",
            f"                    'Talent API v1: Tags, Tasks, Updates, Users',",
            f"                    'HRIS API v2: Employees (CRUD + Fields)',",
            f"                    'HRIS API v2: Time Off (Requests, Policies, Reports)',",
            f"                ],",
            f"                'authentication': 'Bearer Token (API Key)',",
            f"                'base_url_us': 'https://api.latticehq.com',",
            f"                'base_url_emea': 'https://api.emea.latticehq.com',",
            f"            }}",
            "            return LatticeResponse(",
            "                success=True,",
            "                data=info",
            "            )",
            "        except Exception as e:",
            "            return LatticeResponse(",
            "                success=False,",
            "                error=str(e)",
            "            )",
        ]

        # Combine all parts
        all_lines = class_lines + method_lines + helper_lines
        return '\n'.join(all_lines)

    def save_to_file(self, filename: Optional[str] = None) -> Path:
        """Save the generated datasource to a file."""
        if filename is None:
            filename = "lattice.py"

        # Create lattice directory under external sources
        script_dir = Path(__file__).parent if '__file__' in dir() else Path('.')
        lattice_dir = script_dir.parent / 'app' / 'sources' / 'external' / 'lattice'
        lattice_dir.mkdir(parents=True, exist_ok=True)

        # Also create __init__.py
        init_file = lattice_dir / '__init__.py'
        if not init_file.exists():
            init_file.write_text('', encoding='utf-8')

        full_path = lattice_dir / filename

        class_code = self.generate_lattice_datasource()
        full_path.write_text(class_code + '\n', encoding='utf-8')

        print(f"Generated Lattice data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by API category
        talent_methods = [m for m in self.generated_methods if '/v1/' in m['path']]
        hris_methods = [m for m in self.generated_methods if '/v2/' in m['path']]

        print(f"\nAPI Coverage Summary:")
        print(f"  Total methods: {len(self.generated_methods)}")
        print(f"  Talent API v1: {len(talent_methods)} methods")
        print(f"  HRIS API v2: {len(hris_methods)} methods")

        # Detailed breakdown
        categories = {
            'Competencies': [],
            'Custom Attributes': [],
            'Departments': [],
            'Feedback': [],
            'Goals': [],
            'Me': [],
            'Questions': [],
            'Review Cycles': [],
            'Reviewees': [],
            'Tags': [],
            'Tasks (Talent)': [],
            'Updates': [],
            'Users': [],
            'Employees (HRIS)': [],
            'Time Off': [],
            'Employee Fields': [],
        }

        for m in self.generated_methods:
            name = m['name']
            if 'competency' in name:
                categories['Competencies'].append(name)
            elif 'custom_attribute' in name:
                categories['Custom Attributes'].append(name)
            elif 'department' in name and 'role' not in name:
                categories['Departments'].append(name)
            elif 'feedback' in name:
                categories['Feedback'].append(name)
            elif 'goal' in name:
                categories['Goals'].append(name)
            elif name == 'get_me':
                categories['Me'].append(name)
            elif 'question' in name:
                categories['Questions'].append(name)
            elif 'review_cycle' in name:
                categories['Review Cycles'].append(name)
            elif 'reviewee' in name:
                categories['Reviewees'].append(name)
            elif 'tag' in name:
                categories['Tags'].append(name)
            elif 'task' in name and '/v1/' in m['path']:
                categories['Tasks (Talent)'].append(name)
            elif 'update' in name and 'goal' not in name and 'employee' not in name:
                categories['Updates'].append(name)
            elif 'user' in name:
                categories['Users'].append(name)
            elif 'employee' in name and 'field' not in name:
                categories['Employees (HRIS)'].append(name)
            elif 'time_off' in name:
                categories['Time Off'].append(name)
            elif 'employee_fields' in name:
                categories['Employee Fields'].append(name)

        print(f"\n  Detailed breakdown:")
        for category, methods in categories.items():
            if methods:
                print(f"    {category}: {len(methods)} ({', '.join(methods)})")

        return full_path


def validate_api_coverage() -> bool:
    """Validate that all expected API paths are covered by endpoint definitions."""
    print("\nValidating API coverage...")

    all_covered = True
    covered_paths = set()

    for method_name, endpoint_info in LATTICE_API_ENDPOINTS.items():
        http_method = endpoint_info.get('method', 'GET').upper()
        path = endpoint_info.get('path', '')
        # Normalize path: replace specific param names with {id}
        normalized = path
        for param_name in endpoint_info.get('parameters', {}):
            param_info = endpoint_info['parameters'][param_name]
            if param_info.get('location') == 'path':
                normalized = normalized.replace('{' + param_name + '}', '{id}')
        covered_paths.add(f"{http_method} {normalized}")

    missing = []
    for expected_path, expected_method in EXPECTED_API_PATHS.items():
        if expected_path not in covered_paths:
            missing.append((expected_path, expected_method))
            all_covered = False

    if missing:
        print(f"\n  MISSING APIs ({len(missing)}):")
        for path, method in missing:
            print(f"    {path} -> expected method: {method}")
    else:
        print(f"  All {len(EXPECTED_API_PATHS)} expected API paths are covered!")

    # Check for any extra endpoints not in expected list
    extra = covered_paths - set(EXPECTED_API_PATHS.keys())
    if extra:
        print(f"\n  Extra endpoints ({len(extra)}):")
        for path in sorted(extra):
            print(f"    {path}")

    return all_covered


def process_lattice_api(filename: Optional[str] = None) -> None:
    """End-to-end pipeline for Lattice API generation."""
    print("Starting Lattice API data source generation...")

    generator = LatticeDataSourceGenerator()

    try:
        print("Analyzing Lattice API endpoints and generating wrapper methods...")
        output_path = generator.save_to_file(filename)

        # Validate coverage
        is_valid = validate_api_coverage()

        if is_valid:
            print(f"\nSuccessfully generated comprehensive Lattice data source!")
            print(f"  Covers ALL {len(LATTICE_API_ENDPOINTS)} Lattice API endpoints")
            print(f"  Talent API v1: {len(LATTICE_TALENT_API_ENDPOINTS)} endpoints")
            print(f"  HRIS API v2: {len(LATTICE_HRIS_API_ENDPOINTS)} endpoints")
            print(f"  Output: {output_path}")
        else:
            print(f"\nWARNING: Some API paths are not covered!")
            sys.exit(1)

    except Exception as e:
        print(f"Error: {e}")
        raise


def main():
    """Main function for Lattice data source generator."""
    import argparse

    parser = argparse.ArgumentParser(description='Generate comprehensive Lattice API data source')
    parser.add_argument('--filename', '-f', help='Output filename (optional)')

    args = parser.parse_args()

    try:
        process_lattice_api(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Lattice data source: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main() or 0)
