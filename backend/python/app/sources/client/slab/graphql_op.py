"""Registry of Slab GraphQL operations and fragments.

Slab API: https://slab.com/api/
GraphQL endpoint: POST https://api.slab.com/v1/graphql
"""

from typing import Any


class SlabGraphQLOperations:
    """Registry of Slab GraphQL operations and fragments."""

    # Common fragments
    FRAGMENTS = {
        "UserFields": """
            fragment UserFields on User {
                id
                name
                email
                role
                deactivatedAt
            }
        """,
        "TopicFields": """
            fragment TopicFields on Topic {
                id
                name
                description
                postCount
                createdAt
                updatedAt
            }
        """,
        "PostFields": """
            fragment PostFields on Post {
                id
                title
                content
                insertedAt
                updatedAt
                publishedAt
                archivedAt
                version
                topics {
                    ...TopicFields
                }
                creator {
                    ...UserFields
                }
            }
        """,
    }

    # Query operations
    QUERIES = {
        "organization": {
            "query": """
                query organization {
                    organization {
                        id
                        name
                        hostname
                    }
                }
            """,
            "fragments": [],
            "description": "Get organization information",
        },
        "users": {
            "query": """
                query users {
                    organization {
                        members {
                            ...UserFields
                        }
                    }
                }
            """,
            "fragments": ["UserFields"],
            "description": "List all users in the organization",
        },
        "posts": {
            "query": """
                query posts($status: PostStatus) {
                    posts(status: $status) {
                        ...PostFields
                    }
                }
            """,
            "fragments": ["PostFields", "TopicFields", "UserFields"],
            "description": "List posts with optional status filter",
        },
        "post": {
            "query": """
                query post($id: ID!) {
                    post(id: $id) {
                        ...PostFields
                    }
                }
            """,
            "fragments": ["PostFields", "TopicFields", "UserFields"],
            "description": "Get a single post by ID",
        },
        "topics": {
            "query": """
                query topics {
                    topics {
                        ...TopicFields
                    }
                }
            """,
            "fragments": ["TopicFields"],
            "description": "List all topics",
        },
        "topic": {
            "query": """
                query topic($id: ID!) {
                    topic(id: $id) {
                        ...TopicFields
                        posts {
                            ...PostFields
                        }
                        ancestors {
                            id
                            name
                        }
                        children {
                            id
                            name
                        }
                    }
                }
            """,
            "fragments": ["TopicFields", "PostFields", "UserFields"],
            "description": "Get a single topic by ID with its posts",
        },
        "searchPosts": {
            "query": """
                query searchPosts($query: String!) {
                    searchPosts(query: $query) {
                        ...PostFields
                    }
                }
            """,
            "fragments": ["PostFields", "TopicFields", "UserFields"],
            "description": "Search posts by query string",
        },
    }

    # Mutation operations
    MUTATIONS = {
        "syncPost": {
            "query": """
                mutation syncPost($input: SyncPostInput!) {
                    syncPost(input: $input) {
                        ...PostFields
                    }
                }
            """,
            "fragments": ["PostFields", "TopicFields", "UserFields"],
            "description": "Create or update a post via sync",
        },
    }

    @classmethod
    def get_operation_with_fragments(
        cls, operation_type: str, operation_name: str
    ) -> str:
        """Get a complete GraphQL operation with all required fragments."""
        operations = cls.QUERIES if operation_type == "query" else cls.MUTATIONS

        if operation_name not in operations:
            raise ValueError(
                f"Operation {operation_name} not found in {operation_type}s"
            )
        operation = operations[operation_name]
        fragments_needed = operation.get("fragments", [])

        # Collect all fragments (deduplicate while preserving order)
        seen: set[str] = set()
        fragment_definitions: list[str] = []
        for fragment_name in fragments_needed:
            if fragment_name in cls.FRAGMENTS and fragment_name not in seen:
                fragment_definitions.append(cls.FRAGMENTS[fragment_name])
                seen.add(fragment_name)

        # Combine fragments and operation
        if fragment_definitions:
            return (
                "\n\n".join(fragment_definitions)
                + "\n\n"
                + operation["query"]
            )
        return str(operation["query"])

    @classmethod
    def get_all_operations(cls) -> dict[str, dict[str, Any]]:
        """Get all available operations."""
        return {
            "queries": cls.QUERIES,
            "mutations": cls.MUTATIONS,
            "fragments": cls.FRAGMENTS,
        }
