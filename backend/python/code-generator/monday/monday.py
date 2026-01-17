# ruff: noqa
"""
Monday.com GraphQL Data Source Generator
Generates wrapper methods for Monday.com GraphQL operations.
Creates a comprehensive Monday.com data source with CRUD operations.
"""

import inspect
import re
from typing import Dict, Optional, Tuple, Union, List, Any
from pathlib import Path

# Import the Monday operations registry
from app.sources.client.monday.graphql_op import MondayGraphQLOperations


class MondayDataSourceGenerator:
    """Generate Monday.com data source methods from GraphQL operations."""

    def __init__(self):
        """Initialize the Monday.com data source generator."""
        self.generated_methods = []
        self.operations = MondayGraphQLOperations.get_all_operations()
        self.type_mappings = self._create_type_mappings()

        # Define comprehensive Monday operations based on the actual API
        self.comprehensive_operations = self._define_comprehensive_operations()

    def _create_type_mappings(self) -> Dict[str, str]:
        """Create mappings from GraphQL types to Python types."""
        return {
            # Basic types
            'String': 'str',
            'Int': 'int',
            'Float': 'float',
            'Boolean': 'bool',
            'ID': 'str',
            'JSON': 'Dict[str, Any]',

            # Monday specific types
            'User': 'Dict[str, Any]',
            'Board': 'Dict[str, Any]',
            'Item': 'Dict[str, Any]',
            'Column': 'Dict[str, Any]',
            'Group': 'Dict[str, Any]',
            'Workspace': 'Dict[str, Any]',
            'Account': 'Dict[str, Any]',
            'Team': 'Dict[str, Any]',
            'Tag': 'Dict[str, Any]',
            'Update': 'Dict[str, Any]',
            'Asset': 'Dict[str, Any]',
            'Doc': 'Dict[str, Any]',
            'Folder': 'Dict[str, Any]',
            'Webhook': 'Dict[str, Any]',
            'Notification': 'Dict[str, Any]',
            'ActivityLog': 'Dict[str, Any]',

            # Input types
            'BoardKind': 'str',
            'ColumnType': 'str',
            'BoardsOrderBy': 'str',
            'ItemsOrderBy': 'str',
            'StateType': 'str',

            # Collections
            'List[User]': 'List[Dict[str, Any]]',
            'List[Board]': 'List[Dict[str, Any]]',
            'List[Item]': 'List[Dict[str, Any]]',
            'List[Column]': 'List[Dict[str, Any]]',
            'List[Group]': 'List[Dict[str, Any]]',
            'List[Tag]': 'List[Dict[str, Any]]',

            # Optional types
            'Optional[String]': 'Optional[str]',
            'Optional[Int]': 'Optional[int]',
            'Optional[Float]': 'Optional[float]',
            'Optional[Boolean]': 'Optional[bool]',
            'Optional[ID]': 'Optional[str]',

            # Response wrapper
            'GraphQLResponse': 'GraphQLResponse'
        }

    def _define_comprehensive_operations(self) -> Dict[str, Dict[str, Any]]:
        """Define comprehensive Monday.com operations based on actual API."""
        return {
            # ================= QUERY OPERATIONS =================
            'queries': {
                # User & Authentication Queries
                'me': {
                    'description': 'Get current user information',
                    'parameters': {},
                    'example_usage': 'await monday_datasource.me()'
                },
                'users': {
                    'description': 'Get all users',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'page': {'type': 'int', 'required': False},
                        'kind': {'type': 'str', 'required': False},
                        'emails': {'type': 'List[str]', 'required': False},
                        'ids': {'type': 'List[str]', 'required': False},
                        'name': {'type': 'str', 'required': False},
                        'newest_first': {'type': 'bool', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.users(limit=50)'
                },

                # Account Queries
                'account': {
                    'description': 'Get account information',
                    'parameters': {},
                    'example_usage': 'await monday_datasource.account()'
                },

                # Board Queries
                'boards': {
                    'description': 'Get all boards',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'page': {'type': 'int', 'required': False},
                        'board_kind': {'type': 'str', 'required': False},
                        'ids': {'type': 'List[str]', 'required': False},
                        'order_by': {'type': 'str', 'required': False},
                        'state': {'type': 'str', 'required': False},
                        'workspace_ids': {'type': 'List[str]', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.boards(limit=50)'
                },

                # Item Queries
                'items': {
                    'description': 'Get items by IDs',
                    'parameters': {
                        'ids': {'type': 'List[str]', 'required': True},
                        'limit': {'type': 'int', 'required': False},
                        'page': {'type': 'int', 'required': False},
                        'newest_first': {'type': 'bool', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.items(ids=["123", "456"])'
                },
                'items_page_by_column_values': {
                    'description': 'Get items by column values',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'columns': {'type': 'List[Dict[str, Any]]', 'required': True},
                        'limit': {'type': 'int', 'required': False},
                        'cursor': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.items_page_by_column_values(board_id="123", columns=[{"column_id": "status", "column_values": ["Done"]}])'
                },
                'next_items_page': {
                    'description': 'Get next page of items',
                    'parameters': {
                        'cursor': {'type': 'str', 'required': True},
                        'limit': {'type': 'int', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.next_items_page(cursor="abc123")'
                },

                # Workspace Queries
                'workspaces': {
                    'description': 'Get all workspaces',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'page': {'type': 'int', 'required': False},
                        'ids': {'type': 'List[str]', 'required': False},
                        'kind': {'type': 'str', 'required': False},
                        'state': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.workspaces(limit=50)'
                },

                # Team Queries
                'teams': {
                    'description': 'Get all teams',
                    'parameters': {
                        'ids': {'type': 'List[str]', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.teams()'
                },

                # Tag Queries
                'tags': {
                    'description': 'Get all tags',
                    'parameters': {
                        'ids': {'type': 'List[str]', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.tags()'
                },

                # Update Queries
                'updates': {
                    'description': 'Get updates',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'page': {'type': 'int', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.updates(limit=50)'
                },

                # Docs Queries
                'docs': {
                    'description': 'Get documents',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'page': {'type': 'int', 'required': False},
                        'object_ids': {'type': 'List[str]', 'required': False},
                        'workspace_ids': {'type': 'List[str]', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.docs(limit=50)'
                },

                # Folders Queries
                'folders': {
                    'description': 'Get folders',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'page': {'type': 'int', 'required': False},
                        'workspace_ids': {'type': 'List[str]', 'required': False},
                        'ids': {'type': 'List[str]', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.folders(limit=50)'
                },

                # App Subscription Queries
                'app_subscription': {
                    'description': 'Get app subscription information',
                    'parameters': {},
                    'example_usage': 'await monday_datasource.app_subscription()'
                },

                # Webhooks Queries
                'webhooks': {
                    'description': 'Get webhooks for a board',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.webhooks(board_id="123")'
                },

                # Activity Logs Queries
                'boards_activity_logs': {
                    'description': 'Get activity logs for boards',
                    'parameters': {
                        'board_ids': {'type': 'List[str]', 'required': True},
                        'limit': {'type': 'int', 'required': False},
                        'page': {'type': 'int', 'required': False},
                        'from_date': {'type': 'str', 'required': False},
                        'to_date': {'type': 'str', 'required': False},
                        'user_ids': {'type': 'List[str]', 'required': False},
                        'column_ids': {'type': 'List[str]', 'required': False},
                        'group_ids': {'type': 'List[str]', 'required': False},
                        'item_ids': {'type': 'List[str]', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.boards_activity_logs(board_ids=["123"])'
                },

                # Version Queries
                'version': {
                    'description': 'Get API version information',
                    'parameters': {},
                    'example_usage': 'await monday_datasource.version()'
                },

                # Complexity Queries
                'complexity': {
                    'description': 'Get complexity information for current query',
                    'parameters': {},
                    'example_usage': 'await monday_datasource.complexity()'
                },

                # Rate Limit Status
                'rate_limit_status': {
                    'description': 'Get rate limit status (via complexity)',
                    'parameters': {},
                    'example_usage': 'await monday_datasource.rate_limit_status()'
                },

                # Assets Query
                'assets': {
                    'description': 'Get assets by IDs',
                    'parameters': {
                        'ids': {'type': 'List[str]', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.assets(ids=["123", "456"])'
                },
            },

            # ================= MUTATION OPERATIONS =================
            'mutations': {
                # Board Mutations
                'create_board': {
                    'description': 'Create a new board',
                    'parameters': {
                        'board_name': {'type': 'str', 'required': True},
                        'board_kind': {'type': 'str', 'required': True},
                        'workspace_id': {'type': 'str', 'required': False},
                        'template_id': {'type': 'str', 'required': False},
                        'folder_id': {'type': 'str', 'required': False},
                        'board_owner_ids': {'type': 'List[str]', 'required': False},
                        'board_subscriber_ids': {'type': 'List[str]', 'required': False},
                        'description': {'type': 'str', 'required': False},
                        'board_owner_team_ids': {'type': 'List[str]', 'required': False},
                        'board_subscriber_team_ids': {'type': 'List[str]', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.create_board(board_name="My Board", board_kind="public")'
                },
                'update_board': {
                    'description': 'Update a board',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'board_attribute': {'type': 'str', 'required': True},
                        'new_value': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.update_board(board_id="123", board_attribute="name", new_value="New Name")'
                },
                'archive_board': {
                    'description': 'Archive a board',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.archive_board(board_id="123")'
                },
                'delete_board': {
                    'description': 'Delete a board',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.delete_board(board_id="123")'
                },
                'duplicate_board': {
                    'description': 'Duplicate a board',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'duplicate_type': {'type': 'str', 'required': True},
                        'board_name': {'type': 'str', 'required': False},
                        'workspace_id': {'type': 'str', 'required': False},
                        'folder_id': {'type': 'str', 'required': False},
                        'keep_subscribers': {'type': 'bool', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.duplicate_board(board_id="123", duplicate_type="duplicate_board_with_structure")'
                },

                # Column Mutations
                'create_column': {
                    'description': 'Create a new column',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'title': {'type': 'str', 'required': True},
                        'column_type': {'type': 'str', 'required': True},
                        'description': {'type': 'str', 'required': False},
                        'defaults': {'type': 'str', 'required': False},
                        'id': {'type': 'str', 'required': False},
                        'after_column_id': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.create_column(board_id="123", title="Status", column_type="status")'
                },
                'change_column_title': {
                    'description': 'Change column title',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'column_id': {'type': 'str', 'required': True},
                        'title': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.change_column_title(board_id="123", column_id="status", title="New Status")'
                },
                'change_column_metadata': {
                    'description': 'Change column metadata',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'column_id': {'type': 'str', 'required': True},
                        'column_property': {'type': 'str', 'required': True},
                        'value': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.change_column_metadata(board_id="123", column_id="status", column_property="description", value="New Description")'
                },
                'delete_column': {
                    'description': 'Delete a column',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'column_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.delete_column(board_id="123", column_id="status")'
                },
                'change_column_value': {
                    'description': 'Change column value for an item',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'item_id': {'type': 'str', 'required': True},
                        'column_id': {'type': 'str', 'required': True},
                        'value': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.change_column_value(board_id="123", item_id="456", column_id="status", value="{\\"label\\": \\"Done\\"}")'
                },
                'change_multiple_column_values': {
                    'description': 'Change multiple column values for an item',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'item_id': {'type': 'str', 'required': True},
                        'column_values': {'type': 'str', 'required': True},
                        'create_labels_if_missing': {'type': 'bool', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.change_multiple_column_values(board_id="123", item_id="456", column_values="{\\"status\\": {\\"label\\": \\"Done\\"}}")'
                },
                'change_simple_column_value': {
                    'description': 'Change simple column value for an item',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'item_id': {'type': 'str', 'required': True},
                        'column_id': {'type': 'str', 'required': True},
                        'value': {'type': 'str', 'required': True},
                        'create_labels_if_missing': {'type': 'bool', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.change_simple_column_value(board_id="123", item_id="456", column_id="text", value="Hello World")'
                },

                # Group Mutations
                'create_group': {
                    'description': 'Create a new group',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'group_name': {'type': 'str', 'required': True},
                        'group_color': {'type': 'str', 'required': False},
                        'position': {'type': 'str', 'required': False},
                        'relative_to': {'type': 'str', 'required': False},
                        'position_relative_method': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.create_group(board_id="123", group_name="New Group")'
                },
                'update_group': {
                    'description': 'Update a group',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'group_id': {'type': 'str', 'required': True},
                        'group_attribute': {'type': 'str', 'required': True},
                        'new_value': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.update_group(board_id="123", group_id="group1", group_attribute="title", new_value="Updated Group")'
                },
                'duplicate_group': {
                    'description': 'Duplicate a group',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'group_id': {'type': 'str', 'required': True},
                        'add_to_top': {'type': 'bool', 'required': False},
                        'group_title': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.duplicate_group(board_id="123", group_id="group1")'
                },
                'archive_group': {
                    'description': 'Archive a group',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'group_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.archive_group(board_id="123", group_id="group1")'
                },
                'delete_group': {
                    'description': 'Delete a group',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'group_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.delete_group(board_id="123", group_id="group1")'
                },
                'move_item_to_group': {
                    'description': 'Move an item to a different group',
                    'parameters': {
                        'item_id': {'type': 'str', 'required': True},
                        'group_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.move_item_to_group(item_id="456", group_id="group2")'
                },

                # Item Mutations
                'create_item': {
                    'description': 'Create a new item',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'item_name': {'type': 'str', 'required': True},
                        'group_id': {'type': 'str', 'required': False},
                        'column_values': {'type': 'str', 'required': False},
                        'create_labels_if_missing': {'type': 'bool', 'required': False},
                        'position_relative_method': {'type': 'str', 'required': False},
                        'relative_to': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.create_item(board_id="123", item_name="New Item")'
                },
                'duplicate_item': {
                    'description': 'Duplicate an item',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'item_id': {'type': 'str', 'required': True},
                        'with_updates': {'type': 'bool', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.duplicate_item(board_id="123", item_id="456")'
                },
                'move_item_to_board': {
                    'description': 'Move an item to a different board',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'item_id': {'type': 'str', 'required': True},
                        'group_id': {'type': 'str', 'required': False},
                        'columns_mapping': {'type': 'List[Dict[str, str]]', 'required': False},
                        'subitems_columns_mapping': {'type': 'List[Dict[str, str]]', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.move_item_to_board(board_id="456", item_id="123")'
                },
                'archive_item': {
                    'description': 'Archive an item',
                    'parameters': {
                        'item_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.archive_item(item_id="123")'
                },
                'delete_item': {
                    'description': 'Delete an item',
                    'parameters': {
                        'item_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.delete_item(item_id="123")'
                },
                'clear_item_updates': {
                    'description': 'Clear all updates from an item',
                    'parameters': {
                        'item_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.clear_item_updates(item_id="123")'
                },

                # Subitem Mutations
                'create_subitem': {
                    'description': 'Create a subitem',
                    'parameters': {
                        'parent_item_id': {'type': 'str', 'required': True},
                        'item_name': {'type': 'str', 'required': True},
                        'column_values': {'type': 'str', 'required': False},
                        'create_labels_if_missing': {'type': 'bool', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.create_subitem(parent_item_id="123", item_name="Subitem")'
                },

                # Update Mutations
                'create_update': {
                    'description': 'Create an update',
                    'parameters': {
                        'item_id': {'type': 'str', 'required': True},
                        'body': {'type': 'str', 'required': True},
                        'parent_id': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.create_update(item_id="123", body="This is an update")'
                },
                'edit_update': {
                    'description': 'Edit an update',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'body': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.edit_update(id="123", body="Updated content")'
                },
                'delete_update': {
                    'description': 'Delete an update',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.delete_update(id="123")'
                },
                'like_update': {
                    'description': 'Like an update',
                    'parameters': {
                        'update_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.like_update(update_id="123")'
                },

                # Notification Mutations
                'create_notification': {
                    'description': 'Create a notification',
                    'parameters': {
                        'text': {'type': 'str', 'required': True},
                        'user_id': {'type': 'str', 'required': True},
                        'target_id': {'type': 'str', 'required': True},
                        'target_type': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.create_notification(text="Hello", user_id="123", target_id="456", target_type="Project")'
                },

                # Tag Mutations
                'create_or_get_tag': {
                    'description': 'Create or get a tag',
                    'parameters': {
                        'tag_name': {'type': 'str', 'required': True},
                        'board_id': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.create_or_get_tag(tag_name="Important")'
                },

                # Workspace Mutations
                'create_workspace': {
                    'description': 'Create a workspace',
                    'parameters': {
                        'name': {'type': 'str', 'required': True},
                        'kind': {'type': 'str', 'required': True},
                        'description': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.create_workspace(name="My Workspace", kind="open")'
                },
                'update_workspace': {
                    'description': 'Update a workspace',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'attributes': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.update_workspace(id="123", attributes={"name": "New Name"})'
                },
                'delete_workspace': {
                    'description': 'Delete a workspace',
                    'parameters': {
                        'workspace_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.delete_workspace(workspace_id="123")'
                },
                'add_users_to_workspace': {
                    'description': 'Add users to a workspace',
                    'parameters': {
                        'workspace_id': {'type': 'str', 'required': True},
                        'user_ids': {'type': 'List[str]', 'required': True},
                        'kind': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.add_users_to_workspace(workspace_id="123", user_ids=["456"], kind="subscriber")'
                },
                'delete_users_from_workspace': {
                    'description': 'Remove users from a workspace',
                    'parameters': {
                        'workspace_id': {'type': 'str', 'required': True},
                        'user_ids': {'type': 'List[str]', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.delete_users_from_workspace(workspace_id="123", user_ids=["456"])'
                },
                'add_teams_to_workspace': {
                    'description': 'Add teams to a workspace',
                    'parameters': {
                        'workspace_id': {'type': 'str', 'required': True},
                        'team_ids': {'type': 'List[str]', 'required': True},
                        'kind': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.add_teams_to_workspace(workspace_id="123", team_ids=["456"], kind="subscriber")'
                },
                'delete_teams_from_workspace': {
                    'description': 'Remove teams from a workspace',
                    'parameters': {
                        'workspace_id': {'type': 'str', 'required': True},
                        'team_ids': {'type': 'List[str]', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.delete_teams_from_workspace(workspace_id="123", team_ids=["456"])'
                },

                # Board Subscriber Mutations
                'add_subscribers_to_board': {
                    'description': 'Add subscribers to a board',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'user_ids': {'type': 'List[str]', 'required': True},
                        'kind': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.add_subscribers_to_board(board_id="123", user_ids=["456"])'
                },
                'delete_subscribers_from_board': {
                    'description': 'Remove subscribers from a board',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'user_ids': {'type': 'List[str]', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.delete_subscribers_from_board(board_id="123", user_ids=["456"])'
                },

                # Webhook Mutations
                'create_webhook': {
                    'description': 'Create a webhook',
                    'parameters': {
                        'board_id': {'type': 'str', 'required': True},
                        'url': {'type': 'str', 'required': True},
                        'event': {'type': 'str', 'required': True},
                        'config': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.create_webhook(board_id="123", url="https://example.com/webhook", event="create_item")'
                },
                'delete_webhook': {
                    'description': 'Delete a webhook',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.delete_webhook(id="123")'
                },

                # Doc Mutations
                'create_doc': {
                    'description': 'Create a document',
                    'parameters': {
                        'location': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.create_doc(location={"workspace": {"workspace_id": 123}})'
                },
                'create_doc_block': {
                    'description': 'Create a document block',
                    'parameters': {
                        'doc_id': {'type': 'str', 'required': True},
                        'type': {'type': 'str', 'required': True},
                        'content': {'type': 'str', 'required': True},
                        'after_block_id': {'type': 'str', 'required': False},
                        'parent_block_id': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.create_doc_block(doc_id="123", type="normal_text", content="Hello World")'
                },

                # Folder Mutations
                'create_folder': {
                    'description': 'Create a folder',
                    'parameters': {
                        'name': {'type': 'str', 'required': True},
                        'workspace_id': {'type': 'str', 'required': False},
                        'parent_folder_id': {'type': 'str', 'required': False},
                        'color': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.create_folder(name="My Folder")'
                },
                'update_folder': {
                    'description': 'Update a folder',
                    'parameters': {
                        'folder_id': {'type': 'str', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'color': {'type': 'str', 'required': False},
                        'parent_folder_id': {'type': 'str', 'required': False}
                    },
                    'example_usage': 'await monday_datasource.update_folder(folder_id="123", name="New Name")'
                },
                'delete_folder': {
                    'description': 'Delete a folder',
                    'parameters': {
                        'folder_id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.delete_folder(folder_id="123")'
                },

                # Asset Mutations
                'add_file_to_column': {
                    'description': 'Add a file to a file column',
                    'parameters': {
                        'item_id': {'type': 'str', 'required': True},
                        'column_id': {'type': 'str', 'required': True},
                        'file': {'type': 'Any', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.add_file_to_column(item_id="123", column_id="files", file=file_object)'
                },
                'add_file_to_update': {
                    'description': 'Add a file to an update',
                    'parameters': {
                        'update_id': {'type': 'str', 'required': True},
                        'file': {'type': 'Any', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.add_file_to_update(update_id="123", file=file_object)'
                },

                # Team Mutations
                'add_users_to_team': {
                    'description': 'Add users to a team',
                    'parameters': {
                        'team_id': {'type': 'str', 'required': True},
                        'user_ids': {'type': 'List[str]', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.add_users_to_team(team_id="123", user_ids=["456"])'
                },
                'remove_users_from_team': {
                    'description': 'Remove users from a team',
                    'parameters': {
                        'team_id': {'type': 'str', 'required': True},
                        'user_ids': {'type': 'List[str]', 'required': True}
                    },
                    'example_usage': 'await monday_datasource.remove_users_from_team(team_id="123", user_ids=["456"])'
                },
            }
        }

    def _extract_operation_info(self, operation_name: str, operation_data: Dict[str, Any], operation_type: str) -> Dict[str, Any]:
        """Extract operation information for method generation."""

        # Use comprehensive operations data
        parameters = operation_data.get('parameters', {})

        # Create method info
        method_info = {
            'name': operation_name,
            'operation_type': operation_type,
            'description': operation_data.get('description', f'Monday.com GraphQL {operation_type}: {operation_name}'),
            'parameters': parameters,
            'returns': 'GraphQLResponse',
            'graphql_operation': operation_name,
            'example_usage': operation_data.get('example_usage', '')
        }

        return method_info

    def _sanitize_method_name(self, name: str) -> str:
        """Sanitize method names to be valid Python identifiers."""
        # Monday.com operations are already in snake_case
        return name

    def _generate_method_signature(self, method_info: Dict[str, Any]) -> Tuple[str, str]:
        """Generate the method signature."""
        method_name = self._sanitize_method_name(method_info['name'])
        parameters = method_info.get('parameters', {})

        required_params = []
        optional_params = []

        for param_name, param_info in parameters.items():
            param_type = param_info['type']

            if param_info.get('required', False):
                required_params.append(f"{param_name}: {param_type}")
            else:
                if param_type.startswith('Optional['):
                    optional_params.append(f"{param_name}: {param_type} = None")
                else:
                    optional_params.append(f"{param_name}: Optional[{param_type}] = None")

        all_params = ['self'] + required_params + optional_params
        returns = 'GraphQLResponse'

        if len(all_params) == 1:
            signature = f"async def {method_name}(self) -> {returns}:"
        else:
            params_formatted = ',\n        '.join(all_params)
            signature = f"async def {method_name}(\n        {params_formatted}\n    ) -> {returns}:"

        return signature, method_name

    def _generate_docstring(self, method_info: Dict[str, Any]) -> str:
        """Generate method docstring."""
        operation_name = method_info['name']
        operation_type = method_info['operation_type']
        description = method_info.get('description', f'Monday.com {operation_type}: {operation_name}')
        parameters = method_info.get('parameters', {})
        example_usage = method_info.get('example_usage', '')

        docstring = f'        """{description}\n\n'
        docstring += f'        GraphQL Operation: {operation_type.title()} {operation_name}\n'

        if parameters:
            docstring += '\n        Args:\n'
            for param_name, param_info in parameters.items():
                param_type = param_info['type']
                required_text = 'required' if param_info.get('required', False) else 'optional'
                param_desc = param_info.get('description', f'Parameter for {param_name}')
                docstring += f'            {param_name} ({param_type}, {required_text}): {param_desc}\n'

        docstring += f'\n        Returns:\n            GraphQLResponse: The GraphQL response containing the operation result\n'

        # Add example usage
        if example_usage:
            docstring += f'\n        Example:\n            {example_usage}\n'

        docstring += '        """'
        return docstring

    def _generate_method_body(self, method_info: Dict[str, Any]) -> str:
        """Generate the method body that calls Monday.com GraphQL API."""
        operation_name = method_info['name']
        operation_type = method_info['operation_type']
        parameters = method_info.get('parameters', {})

        # Build variables dictionary
        if parameters:
            variables_lines = []
            variables_lines.append('        variables = {}')

            for param_name in parameters.keys():
                variables_lines.append(f'        if {param_name} is not None:')
                variables_lines.append(f'            variables["{param_name}"] = {param_name}')

            variables_setup = '\n'.join(variables_lines)
        else:
            variables_setup = '        variables = {}'

        # Get the complete GraphQL query with fragments
        method_body = f"""        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("{operation_type}", "{operation_name}")

        # Prepare variables
{variables_setup}

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="{operation_name}"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute {operation_type} {operation_name}: {{str(e)}}"
            )"""

        return method_body

    def _discover_monday_operations(self) -> Dict[str, Dict[str, Any]]:
        """Discover all Monday.com GraphQL operations."""
        discovered_operations = {}

        # Process queries from comprehensive operations
        for query_name, query_data in self.comprehensive_operations['queries'].items():
            method_info = self._extract_operation_info(query_name, query_data, 'query')
            discovered_operations[f"query_{query_name}"] = method_info

        # Process mutations from comprehensive operations
        for mutation_name, mutation_data in self.comprehensive_operations['mutations'].items():
            method_info = self._extract_operation_info(mutation_name, mutation_data, 'mutation')
            discovered_operations[f"mutation_{mutation_name}"] = method_info

        return discovered_operations

    def generate_monday_datasource(self) -> str:
        """Generate the complete Monday.com data source class."""

        print("Discovering Monday.com GraphQL operations...")
        discovered_operations = self._discover_monday_operations()
        print(f"Found {len(discovered_operations)} GraphQL operations")

        class_name = "MondayDataSource"
        description = "Complete Monday.com GraphQL API client wrapper"

        # Class header and imports
        class_code = f'''# ruff: noqa
from typing import Dict, List, Optional, Any
import asyncio

from app.sources.client.monday.monday import MondayClient
from app.sources.client.graphql.response import GraphQLResponse
from app.sources.client.monday.graphql_op import MondayGraphQLOperations


class {class_name}:
    """
    {description}
    Auto-generated wrapper for Monday.com GraphQL operations.

    This class provides unified access to all Monday.com GraphQL operations while
    maintaining proper typing and error handling.

    Coverage:
    - Total GraphQL operations: {len(discovered_operations)}
    - Queries: {len([op for op in discovered_operations.values() if op['operation_type'] == 'query'])}
    - Mutations: {len([op for op in discovered_operations.values() if op['operation_type'] == 'mutation'])}
    - Auto-generated from Monday.com GraphQL schema
    """

    def __init__(self, monday_client: MondayClient) -> None:
        """
        Initialize the Monday.com GraphQL data source.

        Args:
            monday_client (MondayClient): Monday.com client instance
        """
        self._monday_client = monday_client

    # =============================================================================
    # QUERY OPERATIONS
    # =============================================================================

'''

        # Add query operations first
        query_operations = {k: v for k, v in discovered_operations.items() if v['operation_type'] == 'query'}
        mutation_operations = {k: v for k, v in discovered_operations.items() if v['operation_type'] == 'mutation'}

        # Generate query methods
        for operation_id, method_info in query_operations.items():
            try:
                signature, method_name = self._generate_method_signature(method_info)
                docstring = self._generate_docstring(method_info)
                method_body = self._generate_method_body(method_info)

                complete_method = f"    {signature}\n{docstring}\n{method_body}\n\n"
                class_code += complete_method

                self.generated_methods.append({
                    'name': method_name,
                    'operation': method_info['name'],
                    'type': method_info['operation_type'],
                    'params': len(method_info.get('parameters', {})),
                    'description': method_info.get('description', '')
                })

            except Exception as e:
                print(f"Warning: Failed to generate method {operation_id}: {e}")

        # Add mutation operations section
        class_code += '''    # =============================================================================
    # MUTATION OPERATIONS
    # =============================================================================

'''

        # Generate mutation methods
        for operation_id, method_info in mutation_operations.items():
            try:
                signature, method_name = self._generate_method_signature(method_info)
                docstring = self._generate_docstring(method_info)
                method_body = self._generate_method_body(method_info)

                complete_method = f"    {signature}\n{docstring}\n{method_body}\n\n"
                class_code += complete_method

                self.generated_methods.append({
                    'name': method_name,
                    'operation': method_info['name'],
                    'type': method_info['operation_type'],
                    'params': len(method_info.get('parameters', {})),
                    'description': method_info.get('description', '')
                })

            except Exception as e:
                print(f"Warning: Failed to generate method {operation_id}: {e}")

        # Add utility methods
        class_code += '''    # =============================================================================
    # UTILITY AND HELPER METHODS
    # =============================================================================

    def get_monday_client(self) -> MondayClient:
        """Get the underlying Monday.com client."""
        return self._monday_client

    def get_available_operations(self) -> Dict[str, Any]:
        """Get information about available GraphQL operations."""
        return MondayGraphQLOperations.get_all_operations()

    def get_operation_info(self) -> Dict[str, Any]:
        """Get comprehensive information about all available methods."""

        # Query operations
        query_operations = [
            "me", "users", "account", "boards", "items",
            "items_page_by_column_values", "next_items_page",
            "workspaces", "teams", "tags", "updates", "docs",
            "folders", "app_subscription", "webhooks",
            "boards_activity_logs", "version", "complexity",
            "rate_limit_status", "assets"
        ]

        # Mutation operations
        mutation_operations = [
            "create_board", "update_board", "archive_board", "delete_board", "duplicate_board",
            "create_column", "change_column_title", "change_column_metadata", "delete_column",
            "change_column_value", "change_multiple_column_values", "change_simple_column_value",
            "create_group", "update_group", "duplicate_group", "archive_group", "delete_group",
            "move_item_to_group", "create_item", "duplicate_item", "move_item_to_board",
            "archive_item", "delete_item", "clear_item_updates", "create_subitem",
            "create_update", "edit_update", "delete_update", "like_update",
            "create_notification", "create_or_get_tag",
            "create_workspace", "update_workspace", "delete_workspace",
            "add_users_to_workspace", "delete_users_from_workspace",
            "add_teams_to_workspace", "delete_teams_from_workspace",
            "add_subscribers_to_board", "delete_subscribers_from_board",
            "create_webhook", "delete_webhook",
            "create_doc", "create_doc_block",
            "create_folder", "update_folder", "delete_folder",
            "add_file_to_column", "add_file_to_update",
            "add_users_to_team", "remove_users_from_team"
        ]

        return {
            "total_methods": len(query_operations) + len(mutation_operations),
            "queries": len(query_operations),
            "mutations": len(mutation_operations),
            "operations": {
                "queries": query_operations,
                "mutations": mutation_operations
            },
            "coverage": {
                "users": "Read operations + team membership",
                "account": "Read operations",
                "boards": "Complete CRUD operations + duplicate + subscribers",
                "items": "Complete CRUD operations + duplicate + move",
                "columns": "Complete CRUD operations + value changes",
                "groups": "Complete CRUD operations + duplicate",
                "subitems": "Create operations",
                "updates": "Complete CRUD operations + like",
                "workspaces": "Complete CRUD operations + user/team management",
                "teams": "User management operations",
                "tags": "Create/get operations",
                "docs": "Create operations + blocks",
                "folders": "Complete CRUD operations",
                "webhooks": "Create and delete operations",
                "notifications": "Create operations",
                "assets": "File upload operations"
            }
        }

    async def validate_connection(self) -> bool:
        """Validate the Monday.com connection by fetching current user information."""
        try:
            response = await self.me()
            return response.success and response.data is not None
        except Exception as e:
            print(f"Connection validation failed: {e}")
            return False

    # =============================================================================
    # CONVENIENCE METHODS FOR COMMON OPERATIONS
    # =============================================================================

    async def get_current_user(self) -> GraphQLResponse:
        """Get current user information."""
        return await self.me()

    async def get_all_boards(self, limit: int = 50) -> GraphQLResponse:
        """Get all boards."""
        return await self.boards(limit=limit)

    async def get_board_items(self, board_id: str, limit: int = 100) -> GraphQLResponse:
        """Get items from a specific board."""
        # First get the board to get its items
        response = await self.boards(ids=[board_id], limit=1)
        return response

    async def create_simple_item(
        self,
        board_id: str,
        item_name: str,
        group_id: Optional[str] = None,
        column_values: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Create a simple item with basic information."""
        column_values_str = None
        if column_values:
            import json
            column_values_str = json.dumps(column_values)

        return await self.create_item(
            board_id=board_id,
            item_name=item_name,
            group_id=group_id,
            column_values=column_values_str
        )

    async def update_item_column(
        self,
        board_id: str,
        item_id: str,
        column_id: str,
        value: Any
    ) -> GraphQLResponse:
        """Update a single column value for an item."""
        import json
        value_str = json.dumps(value) if not isinstance(value, str) else value
        return await self.change_column_value(
            board_id=board_id,
            item_id=item_id,
            column_id=column_id,
            value=value_str
        )

    async def add_comment_to_item(
        self,
        item_id: str,
        body: str
    ) -> GraphQLResponse:
        """Add a comment/update to an item."""
        return await self.create_update(item_id=item_id, body=body)

    async def get_board_groups(self, board_id: str) -> GraphQLResponse:
        """Get groups from a specific board."""
        return await self.boards(ids=[board_id], limit=1)

    async def get_board_columns(self, board_id: str) -> GraphQLResponse:
        """Get columns from a specific board."""
        return await self.boards(ids=[board_id], limit=1)

    async def create_simple_board(
        self,
        name: str,
        kind: str = "public",
        workspace_id: Optional[str] = None,
        description: Optional[str] = None
    ) -> GraphQLResponse:
        """Create a simple board."""
        return await self.create_board(
            board_name=name,
            board_kind=kind,
            workspace_id=workspace_id,
            description=description
        )

    async def search_items_by_column(
        self,
        board_id: str,
        column_id: str,
        column_values: List[str],
        limit: int = 50
    ) -> GraphQLResponse:
        """Search for items by column values."""
        columns = [{"column_id": column_id, "column_values": column_values}]
        return await self.items_page_by_column_values(
            board_id=board_id,
            columns=columns,
            limit=limit
        )
'''

        return class_code

    def save_to_file(self, filename: Optional[str] = None):
        """Generate and save the complete class to a file."""
        if filename is None:
            filename = "monday_data_source.py"

        # Create monday directory in external
        script_dir = Path(__file__).parent if '__file__' in dir() else Path('.')
        external_dir = script_dir.parent / 'app' / 'sources' / 'external' / 'monday'
        external_dir.mkdir(parents=True, exist_ok=True)

        # Set the full file path
        full_path = external_dir / filename

        class_code = self.generate_monday_datasource()

        full_path.write_text(class_code, encoding='utf-8')

        print(f"Generated Monday.com data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary
        query_count = len([m for m in self.generated_methods if m['type'] == 'query'])
        mutation_count = len([m for m in self.generated_methods if m['type'] == 'mutation'])

        print(f"\nSummary:")
        print(f"   - Total methods: {len(self.generated_methods)}")
        print(f"   - Query methods: {query_count}")
        print(f"   - Mutation methods: {mutation_count}")

        operations = {}
        for method in self.generated_methods:
            op_type = method['type']
            if op_type not in operations:
                operations[op_type] = []
            operations[op_type].append(method['operation'])

        print(f"   - Available operations:")
        for op_type, ops in operations.items():
            print(f"     * {op_type.title()}s: {', '.join(ops[:10])}" + ("..." if len(ops) > 10 else ""))


def process_monday_graphql_api(filename: Optional[str] = None) -> None:
    """End-to-end pipeline for Monday.com GraphQL API generation."""
    print(f"Starting Monday.com GraphQL data source generation...")

    generator = MondayDataSourceGenerator()

    try:
        print("Analyzing Monday.com GraphQL operations and generating wrapper methods...")
        generator.save_to_file(filename)

        script_dir = Path(__file__).parent if '__file__' in dir() else Path('.')
        print(f"\nFiles generated in: {script_dir.parent / 'app' / 'sources' / 'external' / 'monday'}")

        print(f"\nSuccessfully generated Monday.com data source with comprehensive GraphQL operations!")

    except Exception as e:
        print(f"Error: {e}")
        raise


def main():
    """Main function for Monday.com data source generator."""
    import argparse

    parser = argparse.ArgumentParser(description='Generate Monday.com GraphQL data source')
    parser.add_argument('--filename', '-f', help='Output filename (optional)')

    args = parser.parse_args()

    try:
        process_monday_graphql_api(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Monday.com data source: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
