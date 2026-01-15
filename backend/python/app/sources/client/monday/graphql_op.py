"""Monday.com GraphQL Operations Registry.

This module contains all GraphQL queries and mutations for Monday.com API.
Based on Monday.com API documentation: https://developer.monday.com/api-reference/reference/about-the-api-reference

Monday.com uses a GraphQL API at https://api.monday.com/v2
"""

from typing import Any, Dict, List, TypedDict


class Operation(TypedDict):
    """Type definition for a GraphQL operation."""
    query: str
    fragments: List[str]
    description: str


class MondayGraphQLOperations:
    """Registry of Monday.com GraphQL operations and fragments."""

    # Common fragments for reusable field selections
    FRAGMENTS: Dict[str, str] = {
        "UserFields": """
            fragment UserFields on User {
                id
                name
                email
                url
                photo_original
                photo_thumb
                title
                birthday
                country_code
                location
                time_zone_identifier
                phone
                mobile_phone
                is_guest
                is_pending
                is_view_only
                is_admin
                is_verified
                enabled
                created_at
                sign_up_product_kind
            }
        """,

        "TeamFields": """
            fragment TeamFields on Team {
                id
                name
                picture_url
                owners {
                    id
                    name
                }
            }
        """,

        "BoardFields": """
            fragment BoardFields on Board {
                id
                name
                description
                state
                board_folder_id
                board_kind
                url
                item_terminology
                items_count
                permissions
                type
                updated_at
                workspace_id
                workspace {
                    id
                    name
                }
                creator {
                    id
                    name
                }
            }
        """,

        "ColumnFields": """
            fragment ColumnFields on Column {
                id
                title
                description
                type
                settings_str
                archived
                width
            }
        """,

        "GroupFields": """
            fragment GroupFields on Group {
                id
                title
                color
                position
                archived
                deleted
            }
        """,

        "ItemFields": """
            fragment ItemFields on Item {
                id
                name
                state
                url
                relative_link
                created_at
                updated_at
                creator_id
                board {
                    id
                    name
                }
                group {
                    id
                    title
                }
                column_values {
                    id
                    type
                    text
                    value
                }
            }
        """,

        "SubitemFields": """
            fragment SubitemFields on Item {
                id
                name
                state
                url
                relative_link
                created_at
                updated_at
                column_values {
                    id
                    type
                    text
                    value
                }
            }
        """,

        "UpdateFields": """
            fragment UpdateFields on Update {
                id
                body
                text_body
                created_at
                updated_at
                creator_id
                creator {
                    id
                    name
                }
                assets {
                    id
                    name
                    url
                    file_extension
                    file_size
                }
                replies {
                    id
                    body
                    created_at
                    creator {
                        id
                        name
                    }
                }
            }
        """,

        "WorkspaceFields": """
            fragment WorkspaceFields on Workspace {
                id
                name
                kind
                description
                state
                created_at
                account_product {
                    id
                    kind
                }
            }
        """,

        "TagFields": """
            fragment TagFields on Tag {
                id
                name
                color
            }
        """,

        "WebhookFields": """
            fragment WebhookFields on Webhook {
                id
                board_id
                event
                config
            }
        """,

        "DocFields": """
            fragment DocFields on Document {
                id
                name
                url
                created_at
                created_by {
                    id
                    name
                }
                doc_folder_id
                object_id
                workspace_id
            }
        """,

        "FolderFields": """
            fragment FolderFields on Folder {
                id
                name
                color
                created_at
                owner_id
                parent_folder_id
            }
        """,

        "AccountFields": """
            fragment AccountFields on Account {
                id
                name
                logo
                show_timeline_weekends
                slug
                tier
                country_code
                plan {
                    max_users
                    period
                    tier
                    version
                }
                products {
                    id
                    kind
                }
            }
        """,

        "ActivityLogFields": """
            fragment ActivityLogFields on ActivityLogType {
                id
                account_id
                created_at
                data
                entity
                event
                user_id
            }
        """,

        "NotificationFields": """
            fragment NotificationFields on Notification {
                id
                text
                created_at
                user_id
                read
            }
        """,

        "VersionFields": """
            fragment VersionFields on Version {
                kind
                value
            }
        """,

        "ComplexityFields": """
            fragment ComplexityFields on Complexity {
                after
                before
                query
                reset_in_x_seconds
            }
        """,
    }

    # Query operations
    QUERIES: Dict[str, Operation] = {
        # ========== USER QUERIES ==========
        "me": {
            "query": """
                query me {
                    me {
                        ...UserFields
                        account {
                            ...AccountFields
                        }
                        teams {
                            ...TeamFields
                        }
                    }
                }
            """,
            "fragments": ["UserFields", "AccountFields", "TeamFields"],
            "description": "Get the current authenticated user"
        },

        "users": {
            "query": """
                query users(
                    $ids: [ID!]
                    $kind: UserKind
                    $newest_first: Boolean
                    $limit: Int
                    $page: Int
                    $emails: [String]
                    $name: String
                    $non_active: Boolean
                ) {
                    users(
                        ids: $ids
                        kind: $kind
                        newest_first: $newest_first
                        limit: $limit
                        page: $page
                        emails: $emails
                        name: $name
                        non_active: $non_active
                    ) {
                        ...UserFields
                    }
                }
            """,
            "fragments": ["UserFields"],
            "description": "Get users with optional filtering"
        },

        # ========== ACCOUNT QUERIES ==========
        "account": {
            "query": """
                query account {
                    account {
                        ...AccountFields
                    }
                }
            """,
            "fragments": ["AccountFields"],
            "description": "Get account information"
        },

        # ========== BOARD QUERIES ==========
        "boards": {
            "query": """
                query boards(
                    $ids: [ID!]
                    $board_kind: BoardKind
                    $limit: Int
                    $page: Int
                    $state: State
                    $order_by: BoardsOrderBy
                    $workspace_ids: [ID!]
                ) {
                    boards(
                        ids: $ids
                        board_kind: $board_kind
                        limit: $limit
                        page: $page
                        state: $state
                        order_by: $order_by
                        workspace_ids: $workspace_ids
                    ) {
                        ...BoardFields
                        groups {
                            ...GroupFields
                        }
                        columns {
                            ...ColumnFields
                        }
                        owners {
                            id
                            name
                        }
                        subscribers {
                            id
                            name
                        }
                        tags {
                            ...TagFields
                        }
                    }
                }
            """,
            "fragments": ["BoardFields", "GroupFields", "ColumnFields", "TagFields"],
            "description": "Get boards with optional filtering"
        },

        # ========== ITEM QUERIES ==========
        "items": {
            "query": """
                query items(
                    $ids: [ID!]!
                    $limit: Int
                    $page: Int
                ) {
                    items(
                        ids: $ids
                        limit: $limit
                        page: $page
                    ) {
                        ...ItemFields
                        subitems {
                            ...SubitemFields
                        }
                        updates {
                            id
                            body
                            created_at
                        }
                    }
                }
            """,
            "fragments": ["ItemFields", "SubitemFields"],
            "description": "Get items by IDs"
        },

        "items_page_by_column_values": {
            "query": """
                query items_page_by_column_values(
                    $board_id: ID!
                    $columns: [ItemsPageByColumnValuesQuery!]!
                    $limit: Int
                    $cursor: String
                ) {
                    items_page_by_column_values(
                        board_id: $board_id
                        columns: $columns
                        limit: $limit
                        cursor: $cursor
                    ) {
                        cursor
                        items {
                            ...ItemFields
                        }
                    }
                }
            """,
            "fragments": ["ItemFields"],
            "description": "Get items page by column values"
        },

        "next_items_page": {
            "query": """
                query next_items_page(
                    $cursor: String!
                    $limit: Int
                ) {
                    next_items_page(
                        cursor: $cursor
                        limit: $limit
                    ) {
                        cursor
                        items {
                            ...ItemFields
                        }
                    }
                }
            """,
            "fragments": ["ItemFields"],
            "description": "Get next page of items using cursor"
        },

        # ========== UPDATE QUERIES ==========
        "updates": {
            "query": """
                query updates(
                    $ids: [ID!]
                    $limit: Int
                    $page: Int
                ) {
                    updates(
                        ids: $ids
                        limit: $limit
                        page: $page
                    ) {
                        ...UpdateFields
                    }
                }
            """,
            "fragments": ["UpdateFields"],
            "description": "Get updates with optional filtering"
        },

        # ========== WORKSPACE QUERIES ==========
        "workspaces": {
            "query": """
                query workspaces(
                    $ids: [ID!]
                    $kind: WorkspaceKind
                    $limit: Int
                    $page: Int
                    $state: State
                    $order_by: WorkspacesOrderBy
                ) {
                    workspaces(
                        ids: $ids
                        kind: $kind
                        limit: $limit
                        page: $page
                        state: $state
                        order_by: $order_by
                    ) {
                        ...WorkspaceFields
                        owners_subscribers {
                            id
                            name
                        }
                        teams_subscribers {
                            id
                            name
                        }
                    }
                }
            """,
            "fragments": ["WorkspaceFields"],
            "description": "Get workspaces with optional filtering"
        },

        # ========== TEAM QUERIES ==========
        "teams": {
            "query": """
                query teams(
                    $ids: [ID!]
                ) {
                    teams(ids: $ids) {
                        ...TeamFields
                        users {
                            ...UserFields
                        }
                    }
                }
            """,
            "fragments": ["TeamFields", "UserFields"],
            "description": "Get teams with optional ID filtering"
        },

        # ========== TAG QUERIES ==========
        "tags": {
            "query": """
                query tags(
                    $ids: [ID!]
                ) {
                    tags(ids: $ids) {
                        ...TagFields
                    }
                }
            """,
            "fragments": ["TagFields"],
            "description": "Get tags with optional ID filtering"
        },

        # ========== WEBHOOK QUERIES ==========
        "webhooks": {
            "query": """
                query webhooks(
                    $board_id: ID!
                    $app_webhooks_only: Boolean
                ) {
                    webhooks(
                        board_id: $board_id
                        app_webhooks_only: $app_webhooks_only
                    ) {
                        ...WebhookFields
                    }
                }
            """,
            "fragments": ["WebhookFields"],
            "description": "Get webhooks for a board"
        },

        # ========== DOCUMENT QUERIES ==========
        "docs": {
            "query": """
                query docs(
                    $ids: [ID!]
                    $limit: Int
                    $page: Int
                    $workspace_ids: [ID!]
                    $object_ids: [ID!]
                ) {
                    docs(
                        ids: $ids
                        limit: $limit
                        page: $page
                        workspace_ids: $workspace_ids
                        object_ids: $object_ids
                    ) {
                        ...DocFields
                        blocks {
                            id
                            type
                            content
                            created_at
                            updated_at
                        }
                    }
                }
            """,
            "fragments": ["DocFields"],
            "description": "Get documents with optional filtering"
        },

        # ========== FOLDER QUERIES ==========
        "folders": {
            "query": """
                query folders(
                    $ids: [ID!]
                    $limit: Int
                    $page: Int
                    $workspace_ids: [ID!]
                ) {
                    folders(
                        ids: $ids
                        limit: $limit
                        page: $page
                        workspace_ids: $workspace_ids
                    ) {
                        ...FolderFields
                        children {
                            id
                            name
                        }
                    }
                }
            """,
            "fragments": ["FolderFields"],
            "description": "Get folders with optional filtering"
        },

        # ========== ACTIVITY LOG QUERIES ==========
        "boards_activity_logs": {
            "query": """
                query boards_activity_logs(
                    $board_ids: [ID!]!
                    $limit: Int
                    $page: Int
                    $user_ids: [ID!]
                    $column_ids: [String!]
                    $group_ids: [String!]
                    $item_ids: [ID!]
                    $from: ISO8601DateTime
                    $to: ISO8601DateTime
                ) {
                    boards(ids: $board_ids) {
                        activity_logs(
                            limit: $limit
                            page: $page
                            user_ids: $user_ids
                            column_ids: $column_ids
                            group_ids: $group_ids
                            item_ids: $item_ids
                            from: $from
                            to: $to
                        ) {
                            ...ActivityLogFields
                        }
                    }
                }
            """,
            "fragments": ["ActivityLogFields"],
            "description": "Get activity logs for boards"
        },

        # ========== VERSION QUERIES ==========
        "version": {
            "query": """
                query version {
                    version {
                        ...VersionFields
                    }
                }
            """,
            "fragments": ["VersionFields"],
            "description": "Get API version information"
        },

        # ========== COMPLEXITY QUERIES ==========
        "complexity": {
            "query": """
                query complexity {
                    complexity {
                        ...ComplexityFields
                    }
                }
            """,
            "fragments": ["ComplexityFields"],
            "description": "Get current query complexity information"
        },

        # ========== APP QUERIES ==========
        "apps_monetization_status": {
            "query": """
                query apps_monetization_status {
                    apps_monetization_status {
                        is_supported
                    }
                }
            """,
            "fragments": [],
            "description": "Check app monetization status"
        },

        # ========== ASSETS QUERIES ==========
        "assets": {
            "query": """
                query assets(
                    $ids: [ID!]!
                ) {
                    assets(ids: $ids) {
                        id
                        name
                        url
                        url_thumbnail
                        public_url
                        file_extension
                        file_size
                        created_at
                        uploaded_by {
                            id
                            name
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "Get assets by IDs"
        },

        # ========== COLUMN VALUES QUERIES ==========
        "board_columns": {
            "query": """
                query board_columns(
                    $board_id: ID!
                ) {
                    boards(ids: [$board_id]) {
                        columns {
                            ...ColumnFields
                        }
                    }
                }
            """,
            "fragments": ["ColumnFields"],
            "description": "Get columns for a specific board"
        },

        # ========== GROUP QUERIES ==========
        "board_groups": {
            "query": """
                query board_groups(
                    $board_id: ID!
                ) {
                    boards(ids: [$board_id]) {
                        groups {
                            ...GroupFields
                            items_page(limit: 25) {
                                cursor
                                items {
                                    id
                                    name
                                }
                            }
                        }
                    }
                }
            """,
            "fragments": ["GroupFields"],
            "description": "Get groups for a specific board with items"
        },

        # ========== ITEMS PAGE QUERIES ==========
        "board_items_page": {
            "query": """
                query board_items_page(
                    $board_id: ID!
                    $limit: Int
                    $cursor: String
                    $query_params: ItemsQuery
                ) {
                    boards(ids: [$board_id]) {
                        items_page(
                            limit: $limit
                            cursor: $cursor
                            query_params: $query_params
                        ) {
                            cursor
                            items {
                                ...ItemFields
                            }
                        }
                    }
                }
            """,
            "fragments": ["ItemFields"],
            "description": "Get paginated items for a board"
        },
    }

    # Mutation operations
    MUTATIONS: Dict[str, Operation] = {
        # ========== BOARD MUTATIONS ==========
        "create_board": {
            "query": """
                mutation create_board(
                    $board_name: String!
                    $board_kind: BoardKind!
                    $description: String
                    $folder_id: ID
                    $template_id: ID
                    $workspace_id: ID
                    $board_owner_ids: [ID!]
                    $board_subscriber_ids: [ID!]
                    $empty: Boolean
                ) {
                    create_board(
                        board_name: $board_name
                        board_kind: $board_kind
                        description: $description
                        folder_id: $folder_id
                        template_id: $template_id
                        workspace_id: $workspace_id
                        board_owner_ids: $board_owner_ids
                        board_subscriber_ids: $board_subscriber_ids
                        empty: $empty
                    ) {
                        id
                        name
                        board_kind
                        description
                        url
                    }
                }
            """,
            "fragments": [],
            "description": "Create a new board"
        },

        "update_board": {
            "query": """
                mutation update_board(
                    $board_id: ID!
                    $board_attribute: BoardAttributes!
                    $new_value: String!
                ) {
                    update_board(
                        board_id: $board_id
                        board_attribute: $board_attribute
                        new_value: $new_value
                    )
                }
            """,
            "fragments": [],
            "description": "Update board attributes"
        },

        "archive_board": {
            "query": """
                mutation archive_board(
                    $board_id: ID!
                ) {
                    archive_board(board_id: $board_id) {
                        id
                        state
                    }
                }
            """,
            "fragments": [],
            "description": "Archive a board"
        },

        "delete_board": {
            "query": """
                mutation delete_board(
                    $board_id: ID!
                ) {
                    delete_board(board_id: $board_id) {
                        id
                    }
                }
            """,
            "fragments": [],
            "description": "Delete a board"
        },

        "duplicate_board": {
            "query": """
                mutation duplicate_board(
                    $board_id: ID!
                    $duplicate_type: DuplicateBoardType!
                    $board_name: String
                    $folder_id: ID
                    $keep_subscribers: Boolean
                    $workspace_id: ID
                ) {
                    duplicate_board(
                        board_id: $board_id
                        duplicate_type: $duplicate_type
                        board_name: $board_name
                        folder_id: $folder_id
                        keep_subscribers: $keep_subscribers
                        workspace_id: $workspace_id
                    ) {
                        board {
                            id
                            name
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "Duplicate a board"
        },

        # ========== COLUMN MUTATIONS ==========
        "create_column": {
            "query": """
                mutation create_column(
                    $board_id: ID!
                    $title: String!
                    $column_type: ColumnType!
                    $description: String
                    $defaults: JSON
                    $id: String
                    $after_column_id: ID
                ) {
                    create_column(
                        board_id: $board_id
                        title: $title
                        column_type: $column_type
                        description: $description
                        defaults: $defaults
                        id: $id
                        after_column_id: $after_column_id
                    ) {
                        ...ColumnFields
                    }
                }
            """,
            "fragments": ["ColumnFields"],
            "description": "Create a new column on a board"
        },

        "change_column_title": {
            "query": """
                mutation change_column_title(
                    $board_id: ID!
                    $column_id: String!
                    $title: String!
                ) {
                    change_column_title(
                        board_id: $board_id
                        column_id: $column_id
                        title: $title
                    ) {
                        ...ColumnFields
                    }
                }
            """,
            "fragments": ["ColumnFields"],
            "description": "Change column title"
        },

        "change_column_metadata": {
            "query": """
                mutation change_column_metadata(
                    $board_id: ID!
                    $column_id: String!
                    $column_property: ColumnProperty!
                    $value: String!
                ) {
                    change_column_metadata(
                        board_id: $board_id
                        column_id: $column_id
                        column_property: $column_property
                        value: $value
                    ) {
                        ...ColumnFields
                    }
                }
            """,
            "fragments": ["ColumnFields"],
            "description": "Change column metadata"
        },

        "delete_column": {
            "query": """
                mutation delete_column(
                    $board_id: ID!
                    $column_id: String!
                ) {
                    delete_column(
                        board_id: $board_id
                        column_id: $column_id
                    ) {
                        id
                    }
                }
            """,
            "fragments": [],
            "description": "Delete a column from a board"
        },

        # ========== GROUP MUTATIONS ==========
        "create_group": {
            "query": """
                mutation create_group(
                    $board_id: ID!
                    $group_name: String!
                    $group_color: String
                    $position: String
                    $position_relative_method: PositionRelative
                    $relative_to: String
                ) {
                    create_group(
                        board_id: $board_id
                        group_name: $group_name
                        group_color: $group_color
                        position: $position
                        position_relative_method: $position_relative_method
                        relative_to: $relative_to
                    ) {
                        ...GroupFields
                    }
                }
            """,
            "fragments": ["GroupFields"],
            "description": "Create a new group on a board"
        },

        "update_group": {
            "query": """
                mutation update_group(
                    $board_id: ID!
                    $group_id: String!
                    $group_attribute: GroupAttributes!
                    $new_value: String!
                ) {
                    update_group(
                        board_id: $board_id
                        group_id: $group_id
                        group_attribute: $group_attribute
                        new_value: $new_value
                    )
                }
            """,
            "fragments": [],
            "description": "Update group attributes"
        },

        "duplicate_group": {
            "query": """
                mutation duplicate_group(
                    $board_id: ID!
                    $group_id: String!
                    $add_to_top: Boolean
                    $group_title: String
                ) {
                    duplicate_group(
                        board_id: $board_id
                        group_id: $group_id
                        add_to_top: $add_to_top
                        group_title: $group_title
                    ) {
                        ...GroupFields
                    }
                }
            """,
            "fragments": ["GroupFields"],
            "description": "Duplicate a group"
        },

        "archive_group": {
            "query": """
                mutation archive_group(
                    $board_id: ID!
                    $group_id: String!
                ) {
                    archive_group(
                        board_id: $board_id
                        group_id: $group_id
                    ) {
                        ...GroupFields
                    }
                }
            """,
            "fragments": ["GroupFields"],
            "description": "Archive a group"
        },

        "delete_group": {
            "query": """
                mutation delete_group(
                    $board_id: ID!
                    $group_id: String!
                ) {
                    delete_group(
                        board_id: $board_id
                        group_id: $group_id
                    ) {
                        ...GroupFields
                    }
                }
            """,
            "fragments": ["GroupFields"],
            "description": "Delete a group"
        },

        "move_item_to_group": {
            "query": """
                mutation move_item_to_group(
                    $item_id: ID!
                    $group_id: String!
                ) {
                    move_item_to_group(
                        item_id: $item_id
                        group_id: $group_id
                    ) {
                        id
                        group {
                            id
                            title
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "Move an item to a different group"
        },

        # ========== ITEM MUTATIONS ==========
        "create_item": {
            "query": """
                mutation create_item(
                    $board_id: ID!
                    $item_name: String!
                    $column_values: JSON
                    $group_id: String
                    $create_labels_if_missing: Boolean
                    $position_relative_method: PositionRelative
                    $relative_to: ID
                ) {
                    create_item(
                        board_id: $board_id
                        item_name: $item_name
                        column_values: $column_values
                        group_id: $group_id
                        create_labels_if_missing: $create_labels_if_missing
                        position_relative_method: $position_relative_method
                        relative_to: $relative_to
                    ) {
                        ...ItemFields
                    }
                }
            """,
            "fragments": ["ItemFields"],
            "description": "Create a new item on a board"
        },

        "change_column_value": {
            "query": """
                mutation change_column_value(
                    $board_id: ID!
                    $item_id: ID!
                    $column_id: String!
                    $value: JSON!
                    $create_labels_if_missing: Boolean
                ) {
                    change_column_value(
                        board_id: $board_id
                        item_id: $item_id
                        column_id: $column_id
                        value: $value
                        create_labels_if_missing: $create_labels_if_missing
                    ) {
                        ...ItemFields
                    }
                }
            """,
            "fragments": ["ItemFields"],
            "description": "Change a column value for an item"
        },

        "change_multiple_column_values": {
            "query": """
                mutation change_multiple_column_values(
                    $board_id: ID!
                    $item_id: ID!
                    $column_values: JSON!
                    $create_labels_if_missing: Boolean
                ) {
                    change_multiple_column_values(
                        board_id: $board_id
                        item_id: $item_id
                        column_values: $column_values
                        create_labels_if_missing: $create_labels_if_missing
                    ) {
                        ...ItemFields
                    }
                }
            """,
            "fragments": ["ItemFields"],
            "description": "Change multiple column values for an item"
        },

        "change_simple_column_value": {
            "query": """
                mutation change_simple_column_value(
                    $board_id: ID!
                    $item_id: ID!
                    $column_id: String!
                    $value: String!
                    $create_labels_if_missing: Boolean
                ) {
                    change_simple_column_value(
                        board_id: $board_id
                        item_id: $item_id
                        column_id: $column_id
                        value: $value
                        create_labels_if_missing: $create_labels_if_missing
                    ) {
                        ...ItemFields
                    }
                }
            """,
            "fragments": ["ItemFields"],
            "description": "Change a simple column value (text-based)"
        },

        "clear_item_updates": {
            "query": """
                mutation clear_item_updates(
                    $item_id: ID!
                ) {
                    clear_item_updates(item_id: $item_id) {
                        id
                    }
                }
            """,
            "fragments": [],
            "description": "Clear all updates for an item"
        },

        "duplicate_item": {
            "query": """
                mutation duplicate_item(
                    $board_id: ID!
                    $item_id: ID!
                    $with_updates: Boolean
                ) {
                    duplicate_item(
                        board_id: $board_id
                        item_id: $item_id
                        with_updates: $with_updates
                    ) {
                        ...ItemFields
                    }
                }
            """,
            "fragments": ["ItemFields"],
            "description": "Duplicate an item"
        },

        "move_item_to_board": {
            "query": """
                mutation move_item_to_board(
                    $board_id: ID!
                    $item_id: ID!
                    $group_id: String
                    $columns_mapping: [ColumnMappingInput!]
                    $subitems_columns_mapping: [ColumnMappingInput!]
                ) {
                    move_item_to_board(
                        board_id: $board_id
                        item_id: $item_id
                        group_id: $group_id
                        columns_mapping: $columns_mapping
                        subitems_columns_mapping: $subitems_columns_mapping
                    ) {
                        id
                        board {
                            id
                            name
                        }
                    }
                }
            """,
            "fragments": [],
            "description": "Move an item to a different board"
        },

        "archive_item": {
            "query": """
                mutation archive_item(
                    $item_id: ID!
                ) {
                    archive_item(item_id: $item_id) {
                        id
                        state
                    }
                }
            """,
            "fragments": [],
            "description": "Archive an item"
        },

        "delete_item": {
            "query": """
                mutation delete_item(
                    $item_id: ID!
                ) {
                    delete_item(item_id: $item_id) {
                        id
                    }
                }
            """,
            "fragments": [],
            "description": "Delete an item"
        },

        # ========== SUBITEM MUTATIONS ==========
        "create_subitem": {
            "query": """
                mutation create_subitem(
                    $parent_item_id: ID!
                    $item_name: String!
                    $column_values: JSON
                    $create_labels_if_missing: Boolean
                ) {
                    create_subitem(
                        parent_item_id: $parent_item_id
                        item_name: $item_name
                        column_values: $column_values
                        create_labels_if_missing: $create_labels_if_missing
                    ) {
                        ...SubitemFields
                    }
                }
            """,
            "fragments": ["SubitemFields"],
            "description": "Create a subitem under a parent item"
        },

        # ========== UPDATE MUTATIONS ==========
        "create_update": {
            "query": """
                mutation create_update(
                    $item_id: ID
                    $body: String!
                    $parent_id: ID
                ) {
                    create_update(
                        item_id: $item_id
                        body: $body
                        parent_id: $parent_id
                    ) {
                        ...UpdateFields
                    }
                }
            """,
            "fragments": ["UpdateFields"],
            "description": "Create an update (comment) on an item"
        },

        "edit_update": {
            "query": """
                mutation edit_update(
                    $id: ID!
                    $body: String!
                ) {
                    edit_update(
                        id: $id
                        body: $body
                    ) {
                        ...UpdateFields
                    }
                }
            """,
            "fragments": ["UpdateFields"],
            "description": "Edit an existing update"
        },

        "like_update": {
            "query": """
                mutation like_update(
                    $update_id: ID!
                ) {
                    like_update(update_id: $update_id) {
                        ...UpdateFields
                    }
                }
            """,
            "fragments": ["UpdateFields"],
            "description": "Like an update"
        },

        "delete_update": {
            "query": """
                mutation delete_update(
                    $id: ID!
                ) {
                    delete_update(id: $id) {
                        id
                    }
                }
            """,
            "fragments": [],
            "description": "Delete an update"
        },

        # ========== WORKSPACE MUTATIONS ==========
        "create_workspace": {
            "query": """
                mutation create_workspace(
                    $name: String!
                    $kind: WorkspaceKind!
                    $description: String
                ) {
                    create_workspace(
                        name: $name
                        kind: $kind
                        description: $description
                    ) {
                        ...WorkspaceFields
                    }
                }
            """,
            "fragments": ["WorkspaceFields"],
            "description": "Create a new workspace"
        },

        "update_workspace": {
            "query": """
                mutation update_workspace(
                    $id: ID!
                    $attributes: UpdateWorkspaceAttributesInput!
                ) {
                    update_workspace(
                        id: $id
                        attributes: $attributes
                    ) {
                        ...WorkspaceFields
                    }
                }
            """,
            "fragments": ["WorkspaceFields"],
            "description": "Update workspace attributes"
        },

        "delete_workspace": {
            "query": """
                mutation delete_workspace(
                    $workspace_id: ID!
                ) {
                    delete_workspace(workspace_id: $workspace_id) {
                        id
                    }
                }
            """,
            "fragments": [],
            "description": "Delete a workspace"
        },

        # ========== FOLDER MUTATIONS ==========
        "create_folder": {
            "query": """
                mutation create_folder(
                    $name: String!
                    $workspace_id: ID!
                    $color: FolderColor
                    $font_weight: FolderFontWeight
                    $parent_folder_id: ID
                ) {
                    create_folder(
                        name: $name
                        workspace_id: $workspace_id
                        color: $color
                        font_weight: $font_weight
                        parent_folder_id: $parent_folder_id
                    ) {
                        ...FolderFields
                    }
                }
            """,
            "fragments": ["FolderFields"],
            "description": "Create a new folder"
        },

        "update_folder": {
            "query": """
                mutation update_folder(
                    $folder_id: ID!
                    $color: FolderColor
                    $font_weight: FolderFontWeight
                    $name: String
                    $parent_folder_id: ID
                ) {
                    update_folder(
                        folder_id: $folder_id
                        color: $color
                        font_weight: $font_weight
                        name: $name
                        parent_folder_id: $parent_folder_id
                    ) {
                        ...FolderFields
                    }
                }
            """,
            "fragments": ["FolderFields"],
            "description": "Update folder attributes"
        },

        "delete_folder": {
            "query": """
                mutation delete_folder(
                    $folder_id: ID!
                ) {
                    delete_folder(folder_id: $folder_id) {
                        id
                    }
                }
            """,
            "fragments": [],
            "description": "Delete a folder"
        },

        # ========== WEBHOOK MUTATIONS ==========
        "create_webhook": {
            "query": """
                mutation create_webhook(
                    $board_id: ID!
                    $url: String!
                    $event: WebhookEventType!
                    $config: JSON
                ) {
                    create_webhook(
                        board_id: $board_id
                        url: $url
                        event: $event
                        config: $config
                    ) {
                        ...WebhookFields
                    }
                }
            """,
            "fragments": ["WebhookFields"],
            "description": "Create a webhook"
        },

        "delete_webhook": {
            "query": """
                mutation delete_webhook(
                    $id: ID!
                ) {
                    delete_webhook(id: $id) {
                        id
                    }
                }
            """,
            "fragments": [],
            "description": "Delete a webhook"
        },

        # ========== TAG MUTATIONS ==========
        "create_or_get_tag": {
            "query": """
                mutation create_or_get_tag(
                    $tag_name: String
                    $board_id: ID
                ) {
                    create_or_get_tag(
                        tag_name: $tag_name
                        board_id: $board_id
                    ) {
                        ...TagFields
                    }
                }
            """,
            "fragments": ["TagFields"],
            "description": "Create a tag or get existing one"
        },

        # ========== USER MUTATIONS ==========
        "add_users_to_board": {
            "query": """
                mutation add_users_to_board(
                    $board_id: ID!
                    $user_ids: [ID!]!
                    $kind: BoardSubscriberKind
                ) {
                    add_users_to_board(
                        board_id: $board_id
                        user_ids: $user_ids
                        kind: $kind
                    ) {
                        id
                        name
                    }
                }
            """,
            "fragments": [],
            "description": "Add users to a board"
        },

        "delete_subscribers_from_board": {
            "query": """
                mutation delete_subscribers_from_board(
                    $board_id: ID!
                    $user_ids: [ID!]!
                ) {
                    delete_subscribers_from_board(
                        board_id: $board_id
                        user_ids: $user_ids
                    ) {
                        id
                        name
                    }
                }
            """,
            "fragments": [],
            "description": "Remove users from a board"
        },

        "add_users_to_workspace": {
            "query": """
                mutation add_users_to_workspace(
                    $workspace_id: ID!
                    $user_ids: [ID!]!
                    $kind: WorkspaceSubscriberKind!
                ) {
                    add_users_to_workspace(
                        workspace_id: $workspace_id
                        user_ids: $user_ids
                        kind: $kind
                    ) {
                        ...UserFields
                    }
                }
            """,
            "fragments": ["UserFields"],
            "description": "Add users to a workspace"
        },

        "delete_users_from_workspace": {
            "query": """
                mutation delete_users_from_workspace(
                    $workspace_id: ID!
                    $user_ids: [ID!]!
                ) {
                    delete_users_from_workspace(
                        workspace_id: $workspace_id
                        user_ids: $user_ids
                    ) {
                        ...UserFields
                    }
                }
            """,
            "fragments": ["UserFields"],
            "description": "Remove users from a workspace"
        },

        "add_teams_to_board": {
            "query": """
                mutation add_teams_to_board(
                    $board_id: ID!
                    $team_ids: [ID!]!
                    $kind: BoardSubscriberKind
                ) {
                    add_teams_to_board(
                        board_id: $board_id
                        team_ids: $team_ids
                        kind: $kind
                    ) {
                        ...TeamFields
                    }
                }
            """,
            "fragments": ["TeamFields"],
            "description": "Add teams to a board"
        },

        "add_teams_to_workspace": {
            "query": """
                mutation add_teams_to_workspace(
                    $workspace_id: ID!
                    $team_ids: [ID!]!
                    $kind: WorkspaceSubscriberKind!
                ) {
                    add_teams_to_workspace(
                        workspace_id: $workspace_id
                        team_ids: $team_ids
                        kind: $kind
                    ) {
                        ...TeamFields
                    }
                }
            """,
            "fragments": ["TeamFields"],
            "description": "Add teams to a workspace"
        },

        "delete_teams_from_workspace": {
            "query": """
                mutation delete_teams_from_workspace(
                    $workspace_id: ID!
                    $team_ids: [ID!]!
                ) {
                    delete_teams_from_workspace(
                        workspace_id: $workspace_id
                        team_ids: $team_ids
                    ) {
                        ...TeamFields
                    }
                }
            """,
            "fragments": ["TeamFields"],
            "description": "Remove teams from a workspace"
        },

        # ========== NOTIFICATION MUTATIONS ==========
        "create_notification": {
            "query": """
                mutation create_notification(
                    $text: String!
                    $user_id: ID!
                    $target_id: ID!
                    $target_type: NotificationTargetType!
                ) {
                    create_notification(
                        text: $text
                        user_id: $user_id
                        target_id: $target_id
                        target_type: $target_type
                    ) {
                        ...NotificationFields
                    }
                }
            """,
            "fragments": ["NotificationFields"],
            "description": "Create a notification"
        },

        # ========== DOCUMENT MUTATIONS ==========
        "create_doc": {
            "query": """
                mutation create_doc(
                    $location: DocLocation!
                ) {
                    create_doc(location: $location) {
                        ...DocFields
                    }
                }
            """,
            "fragments": ["DocFields"],
            "description": "Create a document"
        },

        "create_doc_block": {
            "query": """
                mutation create_doc_block(
                    $doc_id: ID!
                    $type: DocBlockContentType!
                    $content: JSON
                    $after_block_id: ID
                    $parent_block_id: ID
                ) {
                    create_doc_block(
                        doc_id: $doc_id
                        type: $type
                        content: $content
                        after_block_id: $after_block_id
                        parent_block_id: $parent_block_id
                    ) {
                        id
                        type
                        content
                        created_at
                    }
                }
            """,
            "fragments": [],
            "description": "Create a block within a document"
        },

        "update_doc_block": {
            "query": """
                mutation update_doc_block(
                    $block_id: ID!
                    $content: JSON!
                ) {
                    update_doc_block(
                        block_id: $block_id
                        content: $content
                    ) {
                        id
                        type
                        content
                        updated_at
                    }
                }
            """,
            "fragments": [],
            "description": "Update a document block"
        },

        "delete_doc_block": {
            "query": """
                mutation delete_doc_block(
                    $block_id: ID!
                ) {
                    delete_doc_block(block_id: $block_id) {
                        id
                    }
                }
            """,
            "fragments": [],
            "description": "Delete a document block"
        },

        # ========== ASSET MUTATIONS ==========
        "add_file_to_column": {
            "query": """
                mutation add_file_to_column(
                    $item_id: ID!
                    $column_id: String!
                    $file: File!
                ) {
                    add_file_to_column(
                        item_id: $item_id
                        column_id: $column_id
                        file: $file
                    ) {
                        id
                        name
                        url
                    }
                }
            """,
            "fragments": [],
            "description": "Add a file to a file column"
        },

        "add_file_to_update": {
            "query": """
                mutation add_file_to_update(
                    $update_id: ID!
                    $file: File!
                ) {
                    add_file_to_update(
                        update_id: $update_id
                        file: $file
                    ) {
                        id
                        name
                        url
                    }
                }
            """,
            "fragments": [],
            "description": "Add a file to an update"
        },
    }

    @classmethod
    def get_all_operations(cls) -> Dict[str, Any]:
        """Get all GraphQL operations."""
        return {
            "queries": cls.QUERIES,
            "mutations": cls.MUTATIONS,
            "fragments": cls.FRAGMENTS
        }

    @classmethod
    def get_operation(cls, operation_type: str, operation_name: str) -> Dict[str, object]:
        """Get a specific operation by type and name."""
        if operation_type == "query":
            return cls.QUERIES.get(operation_name, {})
        elif operation_type == "mutation":
            return cls.MUTATIONS.get(operation_name, {})
        return {}

    @classmethod
    def get_fragment(cls, fragment_name: str) -> str:
        """Get a specific fragment by name."""
        return cls.FRAGMENTS.get(fragment_name, "")

    @classmethod
    def get_operation_with_fragments(cls, operation_type: str, operation_name: str) -> str:
        """Get complete operation with all required fragments."""
        operation = cls.get_operation(operation_type, operation_name)
        if not operation:
            return ""

        query = str(operation.get("query", ""))
        fragment_names: List[str] = operation.get("fragments", [])  # type: ignore

        # Collect all required fragments
        all_fragments: List[str] = []
        collected: set[str] = set()

        def collect_fragment(name: str) -> None:
            if name in collected:
                return
            collected.add(name)
            fragment = cls.FRAGMENTS.get(name, "")
            if fragment:
                all_fragments.append(fragment)
                # Check for nested fragment references
                for other_name in cls.FRAGMENTS:
                    if f"...{other_name}" in fragment and other_name not in collected:
                        collect_fragment(other_name)

        for fragment_name in fragment_names:
            collect_fragment(fragment_name)

        # Combine fragments and query
        complete_query = "\n".join(all_fragments) + "\n" + query
        return complete_query.strip()

    @classmethod
    def list_operations(cls) -> Dict[str, List[str]]:
        """List all available operations."""
        return {
            "queries": list(cls.QUERIES.keys()),
            "mutations": list(cls.MUTATIONS.keys())
        }
