from typing import Any, Dict


class LinearGraphQLOperations:
    """Registry of Linear GraphQL operations and fragments."""

    # Common fragments
    FRAGMENTS = {
        "UserFields": """
            fragment UserFields on User {
                id
                name
                displayName
                email
                avatarUrl
                active
                createdAt
                updatedAt
            }
        """,

        "TeamFields": """
            fragment TeamFields on Team {
                id
                name
                key
                description
                private
                parent {
                    id
                    name
                    key
                }
                createdAt
                updatedAt
            }
        """,

        "IssueFields": """
            fragment IssueFields on Issue {
                id
                identifier
                number
                title
                description
                priority
                estimate
                url
                createdAt
                updatedAt
                completedAt
                state {
                    id
                    name
                    type
                }
                assignee {
                    ...UserFields
                }
                creator {
                    ...UserFields
                }
                team {
                    ...TeamFields
                }
                labels {
                    nodes {
                        id
                        name
                        color
                    }
                }
                parent {
                    id
                    identifier
                }
                children {
                    nodes {
                        id
                        identifier
                    }
                }
                comments {
                    nodes {
                        ...CommentFields
                    }
                }
                attachments {
                    nodes {
                        id
                        title
                        subtitle
                        url
                        createdAt
                        updatedAt
                    }
                }
            }
        """,

        "ProjectFields": """
            fragment ProjectFields on Project {
                id
                name
                description
                state
                progress
                url
                createdAt
                updatedAt
                completedAt
                targetDate
                lead {
                    ...UserFields
                }
                teams {
                    nodes {
                        ...TeamFields
                    }
                }
            }
        """,

        "CommentFields": """
            fragment CommentFields on Comment {
                id
                body
                url
                createdAt
                updatedAt
                user {
                    ...UserFields
                }
            }
        """
    }

    # Query operations
    QUERIES = {
        "viewer": {
            "query": """
                query viewer {
                    organization {
                        id
                        name
                        urlKey
                    }
                }
            """,
            "fragments": [],
            "description": "Get organization information"
        },

        "teams": {
            "query": """
                query teams($first: Int, $after: String, $filter: TeamFilter) {
                    teams(first: $first, after: $after, filter: $filter) {
                        nodes {
                            ...TeamFields
                            members {
                                nodes {
                                    ...UserFields
                                }
                            }
                        }
                        pageInfo {
                            hasNextPage
                            hasPreviousPage
                            startCursor
                            endCursor
                        }
                    }
                }
            """,
            "fragments": ["TeamFields", "UserFields"],
            "description": "Get teams with optional filtering and cursor-based pagination"
        },

        "users": {
            "query": """
                query users($first: Int, $after: String, $filter: UserFilter, $orderBy: PaginationOrderBy) {
                    users(first: $first, after: $after, filter: $filter, orderBy: $orderBy) {
                        nodes {
                            ...UserFields
                        }
                        pageInfo {
                            hasNextPage
                            hasPreviousPage
                            startCursor
                            endCursor
                        }
                    }
                }
            """,
            "fragments": ["UserFields"],
            "description": "Get users with filtering and cursor-based pagination"
        },

        "issues": {
            "query": """
                query issues($first: Int, $after: String, $filter: IssueFilter) {
                    issues(first: $first, after: $after, filter: $filter) {
                        nodes {
                            ...IssueFields
                        }
                        pageInfo {
                            hasNextPage
                            hasPreviousPage
                            startCursor
                            endCursor
                        }
                    }
                }
            """,
            "fragments": ["IssueFields", "CommentFields", "UserFields", "TeamFields"],
            "description": "Get issues with filtering and cursor-based pagination"
        },

        "issue": {
            "query": """
                query issue($id: String!) {
                    issue(id: $id) {
                        ...IssueFields
                        comments {
                            nodes {
                                ...CommentFields
                            }
                        }
                        attachments {
                            nodes {
                                id
                                title
                                subtitle
                                url
                                createdAt
                                updatedAt
                            }
                        }
                        documents {
                            nodes {
                                id
                                title
                                url
                                slugId
                                content
                                createdAt
                                updatedAt
                                creator {
                                    id
                                    name
                                    email
                                }
                            }
                        }
                    }
                }
            """,
            "fragments": ["IssueFields", "CommentFields", "UserFields", "TeamFields"],
            "description": "Get single issue with comments, attachments, and documents"
        },

        "projects": {
            "query": """
                query projects($first: Int, $filter: ProjectFilter) {
                    projects(first: $first, filter: $filter) {
                        nodes {
                            ...ProjectFields
                            issues {
                                nodes {
                                    ...IssueFields
                                }
                            }
                        }
                        pageInfo {
                            hasNextPage
                            hasPreviousPage
                            startCursor
                            endCursor
                        }
                    }
                }
            """,
            "fragments": ["ProjectFields", "IssueFields", "UserFields", "TeamFields"],
            "description": "Get projects with issues"
        },

        "issueSearch": {
            "query": """
                query issueSearch($query: String!, $first: Int, $after: String, $filter: IssueFilter) {
                    issueSearch(query: $query, first: $first, after: $after, filter: $filter) {
                        nodes {
                            ...IssueFields
                        }
                        pageInfo {
                            hasNextPage
                            hasPreviousPage
                            startCursor
                            endCursor
                        }
                    }
                }
            """,
            "fragments": ["IssueFields", "UserFields", "TeamFields"],
            "description": "Search issues by query string"
        },

        "organization": {
            "query": """
                query organization {
                    organization {
                        id
                        name
                        urlKey
                        createdAt
                        updatedAt
                    }
                }
            """,
            "fragments": [],
            "description": "Get organization information"
        },

        "comment": {
            "query": """
                query comment($id: String!) {
                    comment(id: $id) {
                        ...CommentFields
                    }
                }
            """,
            "fragments": ["CommentFields", "UserFields"],
            "description": "Get single comment by ID"
        },

        "attachment": {
            "query": """
                query attachment($id: String!) {
                    attachment(id: $id) {
                        id
                        title
                        subtitle
                        url
                        createdAt
                        updatedAt
                    }
                }
            """,
            "fragments": [],
            "description": "Get single attachment by ID"
        },

        "attachments": {
            "query": """
                query Attachments($first: Int, $after: String, $filter: AttachmentFilter) {
                    attachments(first: $first, after: $after, filter: $filter) {
                        nodes {
                            id
                            title
                            subtitle
                            url
                            createdAt
                            updatedAt
                            issue {
                                id
                                identifier
                                team {
                                    id
                                    key
                                }
                            }
                        }
                        pageInfo {
                            hasNextPage
                            hasPreviousPage
                            startCursor
                            endCursor
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "List attachments with optional filtering and pagination"
        },

        "documents": {
            "query": """
                query Documents($first: Int, $after: String, $filter: DocumentFilter) {
                    documents(first: $first, after: $after, filter: $filter) {
                        nodes {
                            id
                            title
                            url
                            slugId
                            content
                            createdAt
                            updatedAt
                            creator {
                                id
                                name
                                email
                            }
                            issue {
                                id
                                identifier
                                team {
                                    id
                                    key
                                }
                            }
                        }
                        pageInfo {
                            hasNextPage
                            hasPreviousPage
                            startCursor
                            endCursor
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "List documents with optional filtering and pagination"
        }
    }

    # Mutation operations
    MUTATIONS = {
        "issueCreate": {
            "query": """
                mutation IssueCreate($input: IssueCreateInput!) {
                    issueCreate(input: $input) {
                        success
                        issue {
                            ...IssueFields
                        }
                        lastSyncId
                    }
                }
            """,
            "fragments": ["IssueFields", "UserFields", "TeamFields"],
            "description": "Create a new issue"
        },

        "issueUpdate": {
            "query": """
                mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
                    issueUpdate(id: $id, input: $input) {
                        success
                        issue {
                            ...IssueFields
                        }
                        lastSyncId
                    }
                }
            """,
            "fragments": ["IssueFields", "UserFields", "TeamFields"],
            "description": "Update an existing issue"
        },

        "issueDelete": {
            "query": """
                mutation IssueDelete($id: String!) {
                    issueDelete(id: $id) {
                        success
                        lastSyncId
                    }
                }
            """,
            "fragments": [],
            "description": "Delete an issue"
        },

        "commentCreate": {
            "query": """
                mutation CommentCreate($input: CommentCreateInput!) {
                    commentCreate(input: $input) {
                        success
                        comment {
                            ...CommentFields
                        }
                        lastSyncId
                    }
                }
            """,
            "fragments": ["CommentFields", "UserFields"],
            "description": "Create a comment on an issue"
        },

        "projectCreate": {
            "query": """
                mutation ProjectCreate($input: ProjectCreateInput!) {
                    projectCreate(input: $input) {
                        success
                        project {
                            ...ProjectFields
                        }
                        lastSyncId
                    }
                }
            """,
            "fragments": ["ProjectFields", "UserFields", "TeamFields"],
            "description": "Create a new project"
        },

        "projectUpdate": {
            "query": """
                mutation ProjectUpdate($id: String!, $input: ProjectUpdateInput!) {
                    projectUpdate(id: $id, input: $input) {
                        success
                        project {
                            ...ProjectFields
                        }
                        lastSyncId
                    }
                }
            """,
            "fragments": ["ProjectFields", "UserFields", "TeamFields"],
            "description": "Update a project"
        }
    }

    @classmethod
    def get_operation_with_fragments(cls, operation_type: str, operation_name: str) -> str:
        """Get a complete GraphQL operation with all required fragments."""
        operations = cls.QUERIES if operation_type == "query" else cls.MUTATIONS

        if operation_name not in operations:
            raise ValueError(f"Operation {operation_name} not found in {operation_type}s")
        operation = operations[operation_name]
        fragments_needed = operation.get("fragments", [])

        # Collect all fragments
        fragment_definitions = []
        for fragment_name in fragments_needed:
            if fragment_name in cls.FRAGMENTS:
                fragment_definitions.append(cls.FRAGMENTS[fragment_name])

        # Combine fragments and operation
        if fragment_definitions:
            return "\n\n".join(fragment_definitions) + "\n\n" + operation["query"]
        else:
            return operation["query"]

    @classmethod
    def get_all_operations(cls) -> Dict[str, Dict[str, Any]]:
        """Get all available operations."""
        return {
            "queries": cls.QUERIES,
            "mutations": cls.MUTATIONS,
            "fragments": cls.FRAGMENTS
        }
