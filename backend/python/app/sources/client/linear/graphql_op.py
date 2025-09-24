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
                query teams($first: Int, $filter: TeamFilter) {
                    teams(first: $first, filter: $filter) {
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
            "description": "Get teams with optional filtering"
        },

        "issues": {
            "query": """
                query issues($first: Int, $filter: IssueFilter, $orderBy: PaginationOrderBy) {
                    issues(first: $first, filter: $filter, orderBy: $orderBy) {
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
            "description": "Get issues with filtering and pagination"
        },

        "issue": {
            "query": """
                query Issue($id: String!) {
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
                                url
                                metadata
                            }
                        }
                    }
                }
            """,
            "fragments": ["IssueFields", "CommentFields", "UserFields", "TeamFields"],
            "description": "Get single issue with comments and attachments"
        },

        "projects": {
            "query": """
                query Projects($first: Int, $filter: ProjectFilter) {
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
                query Organization {
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
