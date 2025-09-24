# ruff: noqa
"""
Linear GraphQL Data Source Generator
Generates wrapper methods for Linear GraphQL operations.
Creates a comprehensive Linear data source with CRUD operations.
"""

import inspect
import re
from typing import Dict, Optional, Tuple, Union, List, Any
from pathlib import Path

# Import the Linear operations registry
from app.sources.client.linear.graphql_op import LinearGraphQLOperations


class LinearDataSourceGenerator:
    """Generate Linear data source methods from GraphQL operations."""
    
    def __init__(self):
        """Initialize the Linear data source generator."""
        self.generated_methods = []
        self.operations = LinearGraphQLOperations.get_all_operations()
        self.type_mappings = self._create_type_mappings()
        
        # Define comprehensive Linear operations based on the actual API
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
            
            # Linear specific types
            'User': 'Dict[str, Any]',
            'Team': 'Dict[str, Any]',
            'Issue': 'Dict[str, Any]',
            'Project': 'Dict[str, Any]',
            'Comment': 'Dict[str, Any]',
            'IssueState': 'Dict[str, Any]',
            'IssueLabel': 'Dict[str, Any]',
            'Organization': 'Dict[str, Any]',
            'Attachment': 'Dict[str, Any]',
            'WorkflowState': 'Dict[str, Any]',
            'Cycle': 'Dict[str, Any]',
            'Milestone': 'Dict[str, Any]',
            'Notification': 'Dict[str, Any]',
            'Favorite': 'Dict[str, Any]',
            'Template': 'Dict[str, Any]',
            'Integration': 'Dict[str, Any]',
            'Webhook': 'Dict[str, Any]',
            'ApiKey': 'Dict[str, Any]',
            'Roadmap': 'Dict[str, Any]',
            
            # Input types
            'IssueCreateInput': 'Dict[str, Any]',
            'IssueUpdateInput': 'Dict[str, Any]',
            'ProjectCreateInput': 'Dict[str, Any]',
            'ProjectUpdateInput': 'Dict[str, Any]',
            'CommentCreateInput': 'Dict[str, Any]',
            'TeamFilter': 'Dict[str, Any]',
            'IssueFilter': 'Dict[str, Any]',
            'ProjectFilter': 'Dict[str, Any]',
            'PaginationOrderBy': 'str',
            
            # Connection types
            'UserConnection': 'Dict[str, Any]',
            'TeamConnection': 'Dict[str, Any]',
            'IssueConnection': 'Dict[str, Any]',
            'ProjectConnection': 'Dict[str, Any]',
            'CommentConnection': 'Dict[str, Any]',
            
            # Response types
            'IssuePayload': 'Dict[str, Any]',
            'ProjectPayload': 'Dict[str, Any]',
            'CommentPayload': 'Dict[str, Any]',
            'ArchivePayload': 'Dict[str, Any]',
            
            # Collections
            'List[User]': 'List[Dict[str, Any]]',
            'List[Team]': 'List[Dict[str, Any]]',
            'List[Issue]': 'List[Dict[str, Any]]',
            'List[Project]': 'List[Dict[str, Any]]',
            'List[Comment]': 'List[Dict[str, Any]]',
            
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
        """Define comprehensive Linear operations based on actual API."""
        return {
            # ================= QUERY OPERATIONS =================
            'queries': {
                # User & Authentication Queries
                'viewer': {
                    'description': 'Get current user information',
                    'parameters': {},
                    'example_usage': 'await linear_datasource.viewer()'
                },
                'user': {
                    'description': 'Get user by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.user(id="user-id")'
                },
                'users': {
                    'description': 'Get users with filtering and pagination',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.users(first=50)'
                },
                
                # Organization Queries
                'organization': {
                    'description': 'Get organization information',
                    'parameters': {},
                    'example_usage': 'await linear_datasource.organization()'
                },
                
                # Team Queries
                'team': {
                    'description': 'Get team by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.team(id="team-id")'
                },
                'teams': {
                    'description': 'Get teams with optional filtering',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.teams(first=50)'
                },
                
                # Issue Queries
                'issue': {
                    'description': 'Get single issue with comments and attachments',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.issue(id="issue-id")'
                },
                'issues': {
                    'description': 'Get issues with filtering and pagination',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False},
                        'includeArchived': {'type': 'bool', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.issues(first=50)'
                },
                'issueSearch': {
                    'description': 'Search issues',
                    'parameters': {
                        'query': {'type': 'str', 'required': True},
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.issueSearch(query="bug")'
                },
                
                # Project Queries
                'project': {
                    'description': 'Get project by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.project(id="project-id")'
                },
                'projects': {
                    'description': 'Get projects with issues',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.projects(first=50)'
                },
                
                # Comment Queries
                'comment': {
                    'description': 'Get comment by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.comment(id="comment-id")'
                },
                'comments': {
                    'description': 'Get comments with filtering and pagination',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.comments(first=50)'
                },
                
                # Workflow State Queries
                'workflowState': {
                    'description': 'Get workflow state by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.workflowState(id="state-id")'
                },
                'workflowStates': {
                    'description': 'Get workflow states',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.workflowStates(first=50)'
                },
                
                # Label Queries
                'issueLabel': {
                    'description': 'Get issue label by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.issueLabel(id="label-id")'
                },
                'issueLabels': {
                    'description': 'Get issue labels',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.issueLabels(first=50)'
                },
                
                # Cycle Queries
                'cycle': {
                    'description': 'Get cycle by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.cycle(id="cycle-id")'
                },
                'cycles': {
                    'description': 'Get cycles',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.cycles(first=50)'
                },
                
                # Milestone Queries
                'milestone': {
                    'description': 'Get milestone by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.milestone(id="milestone-id")'
                },
                'milestones': {
                    'description': 'Get milestones',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.milestones(first=50)'
                },
                
                # Attachment Queries
                'attachment': {
                    'description': 'Get attachment by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.attachment(id="attachment-id")'
                },
                'attachments': {
                    'description': 'Get attachments',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.attachments(first=50)'
                },
                
                # Notification Queries
                'notification': {
                    'description': 'Get notification by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.notification(id="notification-id")'
                },
                'notifications': {
                    'description': 'Get notifications',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.notifications(first=50)'
                },
                
                # Additional Queries
                'favorite': {
                    'description': 'Get favorite by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.favorite(id="favorite-id")'
                },
                'favorites': {
                    'description': 'Get favorites',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.favorites(first=50)'
                },
                'template': {
                    'description': 'Get template by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.template(id="template-id")'
                },
                'templates': {
                    'description': 'Get templates',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.templates(first=50)'
                },
                'integration': {
                    'description': 'Get integration by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.integration(id="integration-id")'
                },
                'integrations': {
                    'description': 'Get integrations',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.integrations(first=50)'
                },
                'webhook': {
                    'description': 'Get webhook by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.webhook(id="webhook-id")'
                },
                'webhooks': {
                    'description': 'Get webhooks',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.webhooks(first=50)'
                },
                'apiKey': {
                    'description': 'Get API key by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.apiKey(id="key-id")'
                },
                'apiKeys': {
                    'description': 'Get API keys',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.apiKeys(first=50)'
                },
                'roadmap': {
                    'description': 'Get roadmap by ID',
                    'parameters': {'id': {'type': 'str', 'required': True}},
                    'example_usage': 'await linear_datasource.roadmap(id="roadmap-id")'
                },
                'roadmaps': {
                    'description': 'Get roadmaps',
                    'parameters': {
                        'first': {'type': 'int', 'required': False},
                        'after': {'type': 'str', 'required': False},
                        'filter': {'type': 'Dict[str, Any]', 'required': False},
                        'orderBy': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'example_usage': 'await linear_datasource.roadmaps(first=50)'
                }
            },
            
            # ================= MUTATION OPERATIONS =================
            'mutations': {
                # User Mutations
                'userUpdate': {
                    'description': 'Update user',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.userUpdate(id="user-id", input={})'
                },
                'userSettingsUpdate': {
                    'description': 'Update user settings',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.userSettingsUpdate(id="user-id", input={})'
                },
                
                # Organization Mutations
                'organizationUpdate': {
                    'description': 'Update organization',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.organizationUpdate(input={})'
                },
                'organizationInviteCreate': {
                    'description': 'Create organization invite',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.organizationInviteCreate(input={})'
                },
                'organizationInviteDelete': {
                    'description': 'Delete organization invite',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.organizationInviteDelete(id="invite-id")'
                },
                
                # Team Mutations
                'teamCreate': {
                    'description': 'Create a new team',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.teamCreate(input={})'
                },
                'teamUpdate': {
                    'description': 'Update a team',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.teamUpdate(id="team-id", input={})'
                },
                'teamDelete': {
                    'description': 'Delete a team',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.teamDelete(id="team-id")'
                },
                'teamMembershipCreate': {
                    'description': 'Create team membership',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.teamMembershipCreate(input={})'
                },
                'teamMembershipUpdate': {
                    'description': 'Update team membership',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.teamMembershipUpdate(id="membership-id", input={})'
                },
                'teamMembershipDelete': {
                    'description': 'Delete team membership',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.teamMembershipDelete(id="membership-id")'
                },
                
                # Issue Mutations
                'issueCreate': {
                    'description': 'Create a new issue',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.issueCreate(input={})'
                },
                'issueUpdate': {
                    'description': 'Update an existing issue',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.issueUpdate(id="issue-id", input={})'
                },
                'issueDelete': {
                    'description': 'Delete an issue',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.issueDelete(id="issue-id")'
                },
                'issueArchive': {
                    'description': 'Archive an issue',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.issueArchive(id="issue-id")'
                },
                'issueUnarchive': {
                    'description': 'Unarchive an issue',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.issueUnarchive(id="issue-id")'
                },
                'issueBatchUpdate': {
                    'description': 'Batch update issues',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.issueBatchUpdate(input={})'
                },
                
                # Project Mutations
                'projectCreate': {
                    'description': 'Create a new project',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.projectCreate(input={})'
                },
                'projectUpdate': {
                    'description': 'Update a project',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.projectUpdate(id="project-id", input={})'
                },
                'projectDelete': {
                    'description': 'Delete a project',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.projectDelete(id="project-id")'
                },
                'projectArchive': {
                    'description': 'Archive a project',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.projectArchive(id="project-id")'
                },
                'projectUnarchive': {
                    'description': 'Unarchive a project',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.projectUnarchive(id="project-id")'
                },
                
                # Comment Mutations
                'commentCreate': {
                    'description': 'Create a comment on an issue',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.commentCreate(input={})'
                },
                'commentUpdate': {
                    'description': 'Update a comment',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.commentUpdate(id="comment-id", input={})'
                },
                'commentDelete': {
                    'description': 'Delete a comment',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.commentDelete(id="comment-id")'
                },
                
                # Workflow State Mutations
                'workflowStateCreate': {
                    'description': 'Create a workflow state',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.workflowStateCreate(input={})'
                },
                'workflowStateUpdate': {
                    'description': 'Update a workflow state',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.workflowStateUpdate(id="state-id", input={})'
                },
                'workflowStateDelete': {
                    'description': 'Delete a workflow state',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.workflowStateDelete(id="state-id")'
                },
                
                # Label Mutations
                'issueLabelCreate': {
                    'description': 'Create an issue label',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.issueLabelCreate(input={})'
                },
                'issueLabelUpdate': {
                    'description': 'Update an issue label',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.issueLabelUpdate(id="label-id", input={})'
                },
                'issueLabelDelete': {
                    'description': 'Delete an issue label',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.issueLabelDelete(id="label-id")'
                },
                
                # Cycle Mutations
                'cycleCreate': {
                    'description': 'Create a cycle',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.cycleCreate(input={})'
                },
                'cycleUpdate': {
                    'description': 'Update a cycle',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.cycleUpdate(id="cycle-id", input={})'
                },
                'cycleArchive': {
                    'description': 'Archive a cycle',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.cycleArchive(id="cycle-id")'
                },
                
                # Milestone Mutations
                'milestoneCreate': {
                    'description': 'Create a milestone',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.milestoneCreate(input={})'
                },
                'milestoneUpdate': {
                    'description': 'Update a milestone',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.milestoneUpdate(id="milestone-id", input={})'
                },
                'milestoneDelete': {
                    'description': 'Delete a milestone',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.milestoneDelete(id="milestone-id")'
                },
                
                # Attachment Mutations
                'attachmentCreate': {
                    'description': 'Create an attachment',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.attachmentCreate(input={})'
                },
                'attachmentUpdate': {
                    'description': 'Update an attachment',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.attachmentUpdate(id="attachment-id", input={})'
                },
                'attachmentDelete': {
                    'description': 'Delete an attachment',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.attachmentDelete(id="attachment-id")'
                },
                
                # Notification Mutations
                'notificationUpdate': {
                    'description': 'Update a notification',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.notificationUpdate(id="notification-id", input={})'
                },
                'notificationMarkRead': {
                    'description': 'Mark notification as read',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.notificationMarkRead(id="notification-id")'
                },
                'notificationMarkUnread': {
                    'description': 'Mark notification as unread',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.notificationMarkUnread(id="notification-id")'
                },
                
                # Additional Mutations
                'favoriteCreate': {
                    'description': 'Create a favorite',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.favoriteCreate(input={})'
                },
                'favoriteUpdate': {
                    'description': 'Update a favorite',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.favoriteUpdate(id="favorite-id", input={})'
                },
                'favoriteDelete': {
                    'description': 'Delete a favorite',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.favoriteDelete(id="favorite-id")'
                },
                'templateCreate': {
                    'description': 'Create a template',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.templateCreate(input={})'
                },
                'templateUpdate': {
                    'description': 'Update a template',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.templateUpdate(id="template-id", input={})'
                },
                'templateDelete': {
                    'description': 'Delete a template',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.templateDelete(id="template-id")'
                },
                'integrationCreate': {
                    'description': 'Create an integration',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.integrationCreate(input={})'
                },
                'integrationUpdate': {
                    'description': 'Update an integration',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.integrationUpdate(id="integration-id", input={})'
                },
                'integrationDelete': {
                    'description': 'Delete an integration',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.integrationDelete(id="integration-id")'
                },
                'webhookCreate': {
                    'description': 'Create a webhook',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.webhookCreate(input={})'
                },
                'webhookUpdate': {
                    'description': 'Update a webhook',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.webhookUpdate(id="webhook-id", input={})'
                },
                'webhookDelete': {
                    'description': 'Delete a webhook',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.webhookDelete(id="webhook-id")'
                },
                'apiKeyCreate': {
                    'description': 'Create an API key',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.apiKeyCreate(input={})'
                },
                'apiKeyDelete': {
                    'description': 'Delete an API key',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.apiKeyDelete(id="key-id")'
                },
                'roadmapCreate': {
                    'description': 'Create a roadmap',
                    'parameters': {
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.roadmapCreate(input={})'
                },
                'roadmapUpdate': {
                    'description': 'Update a roadmap',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'input': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.roadmapUpdate(id="roadmap-id", input={})'
                },
                'roadmapDelete': {
                    'description': 'Delete a roadmap',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'example_usage': 'await linear_datasource.roadmapDelete(id="roadmap-id")'
                }
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
            'description': operation_data.get('description', f'Linear GraphQL {operation_type}: {operation_name}'),
            'parameters': parameters,
            'returns': 'GraphQLResponse',
            'graphql_operation': operation_name,
            'example_usage': operation_data.get('example_usage', '')
        }
        
        return method_info
    
    def _sanitize_method_name(self, name: str) -> str:
        """Sanitize method names to be valid Python identifiers."""
        # Convert camelCase to snake_case for certain operations
        conversions = {
            'issueLabel': 'issue_label',
            'issueLabels': 'issue_labels',
            'issueLabelCreate': 'issue_label_create',
            'issueLabelUpdate': 'issue_label_update',
            'issueLabelDelete': 'issue_label_delete',
            'issueCreate': 'issue_create',
            'issueUpdate': 'issue_update',
            'issueDelete': 'issue_delete',
            'issueArchive': 'issue_archive',
            'issueUnarchive': 'issue_unarchive',
            'issueBatchUpdate': 'issue_batch_update',
            'issueSearch': 'issue_search',
            'projectCreate': 'project_create',
            'projectUpdate': 'project_update',
            'projectDelete': 'project_delete',
            'projectArchive': 'project_archive',
            'projectUnarchive': 'project_unarchive',
            'commentCreate': 'comment_create',
            'commentUpdate': 'comment_update',
            'commentDelete': 'comment_delete',
            'teamCreate': 'team_create',
            'teamUpdate': 'team_update',
            'teamDelete': 'team_delete',
            'teamMembershipCreate': 'team_membership_create',
            'teamMembershipUpdate': 'team_membership_update',
            'teamMembershipDelete': 'team_membership_delete',
            'workflowState': 'workflow_state',
            'workflowStates': 'workflow_states',
            'workflowStateCreate': 'workflow_state_create',
            'workflowStateUpdate': 'workflow_state_update',
            'workflowStateDelete': 'workflow_state_delete',
            'userUpdate': 'user_update',
            'userSettingsUpdate': 'user_settings_update',
            'organizationUpdate': 'organization_update',
            'organizationInviteCreate': 'organization_invite_create',
            'organizationInviteDelete': 'organization_invite_delete',
            'cycleCreate': 'cycle_create',
            'cycleUpdate': 'cycle_update',
            'cycleArchive': 'cycle_archive',
            'milestoneCreate': 'milestone_create',
            'milestoneUpdate': 'milestone_update',
            'milestoneDelete': 'milestone_delete',
            'attachmentCreate': 'attachment_create',
            'attachmentUpdate': 'attachment_update',
            'attachmentDelete': 'attachment_delete',
            'notificationUpdate': 'notification_update',
            'notificationMarkRead': 'notification_mark_read',
            'notificationMarkUnread': 'notification_mark_unread',
            'favoriteCreate': 'favorite_create',
            'favoriteUpdate': 'favorite_update',
            'favoriteDelete': 'favorite_delete',
            'templateCreate': 'template_create',
            'templateUpdate': 'template_update',
            'templateDelete': 'template_delete',
            'integrationCreate': 'integration_create',
            'integrationUpdate': 'integration_update',
            'integrationDelete': 'integration_delete',
            'webhookCreate': 'webhook_create',
            'webhookUpdate': 'webhook_update',
            'webhookDelete': 'webhook_delete',
            'apiKey': 'api_key',
            'apiKeys': 'api_keys',
            'apiKeyCreate': 'api_key_create',
            'apiKeyDelete': 'api_key_delete',
            'roadmapCreate': 'roadmap_create',
            'roadmapUpdate': 'roadmap_update',
            'roadmapDelete': 'roadmap_delete'
        }
        
        if name in conversions:
            return conversions[name]
        
        # Keep simple names as-is
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
        description = method_info.get('description', f'Linear {operation_type}: {operation_name}')
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
        """Generate the method body that calls Linear GraphQL API."""
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
        query = LinearGraphQLOperations.get_operation_with_fragments("{operation_type}", "{operation_name}")
        
        # Prepare variables
{variables_setup}
        
        # Execute the GraphQL operation
        try:
            response = await self._linear_client.get_client().execute(
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
    
    def _discover_linear_operations(self) -> Dict[str, Dict[str, Any]]:
        """Discover all Linear GraphQL operations."""
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
    
    def generate_linear_datasource(self) -> str:
        """Generate the complete Linear data source class."""
        
        print("Discovering Linear GraphQL operations...")
        discovered_operations = self._discover_linear_operations()
        print(f"Found {len(discovered_operations)} GraphQL operations")
        
        class_name = "LinearDataSource"
        description = "Complete Linear GraphQL API client wrapper"
        
        # Class header and imports
        class_code = f'''from typing import Dict, List, Optional, Any
import asyncio

from app.sources.client.linear.linear import (
    LinearClient,  
)
from app.sources.client.graphql.response import GraphQLResponse
from app.sources.client.linear.graphql_op import LinearGraphQLOperations

class {class_name}:
    """
    {description}
    Auto-generated wrapper for Linear GraphQL operations.
    
    This class provides unified access to all Linear GraphQL operations while
    maintaining proper typing and error handling.
    
    Coverage:
    - Total GraphQL operations: {len(discovered_operations)}
    - Queries: {len([op for op in discovered_operations.values() if op['operation_type'] == 'query'])}
    - Mutations: {len([op for op in discovered_operations.values() if op['operation_type'] == 'mutation'])}
    - Auto-generated from Linear GraphQL schema
    """
    
    def __init__(self, linear_client: LinearClient) -> None:
        """
        Initialize the Linear GraphQL data source.
        
        Args:
            linear_client (LinearClient): Linear client instance
        """
        self._linear_client = linear_client

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

    def get_linear_client(self) -> LinearClient:
        """Get the underlying Linear client."""
        return self._linear_client
    
    def get_available_operations(self) -> Dict[str, Any]:
        """Get information about available GraphQL operations."""
        return LinearGraphQLOperations.get_all_operations()
    
    def get_operation_info(self) -> Dict[str, Any]:
        """Get comprehensive information about all available methods."""
        
        # Query operations
        query_operations = [
            "viewer", "user", "users", "organization", "team", "teams", 
            "issue", "issues", "issue_search", "project", "projects", 
            "comment", "comments", "workflow_state", "workflow_states", 
            "issue_label", "issue_labels", "cycle", "cycles", 
            "milestone", "milestones", "attachment", "attachments", 
            "notification", "notifications", "favorite", "favorites", 
            "template", "templates", "integration", "integrations", 
            "webhook", "webhooks", "api_key", "api_keys", 
            "roadmap", "roadmaps"
        ]
        
        # Mutation operations
        mutation_operations = [
            "user_update", "user_settings_update", "organization_update", 
            "organization_invite_create", "organization_invite_delete",
            "team_create", "team_update", "team_delete", "team_membership_create", 
            "team_membership_update", "team_membership_delete",
            "issue_create", "issue_update", "issue_delete", "issue_archive", 
            "issue_unarchive", "issue_batch_update",
            "project_create", "project_update", "project_delete", 
            "project_archive", "project_unarchive",
            "comment_create", "comment_update", "comment_delete",
            "workflow_state_create", "workflow_state_update", "workflow_state_delete",
            "issue_label_create", "issue_label_update", "issue_label_delete",
            "cycle_create", "cycle_update", "cycle_archive",
            "milestone_create", "milestone_update", "milestone_delete",
            "attachment_create", "attachment_update", "attachment_delete",
            "notification_update", "notification_mark_read", "notification_mark_unread",
            "favorite_create", "favorite_update", "favorite_delete",
            "template_create", "template_update", "template_delete",
            "integration_create", "integration_update", "integration_delete",
            "webhook_create", "webhook_update", "webhook_delete",
            "api_key_create", "api_key_delete",
            "roadmap_create", "roadmap_update", "roadmap_delete"
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
                "users": "Complete CRUD operations",
                "organizations": "Read and update operations + invites",
                "teams": "Complete CRUD operations + memberships",
                "issues": "Complete CRUD operations + search + batch operations",
                "projects": "Complete CRUD operations + archive/unarchive",
                "comments": "Complete CRUD operations",
                "workflow_states": "Complete CRUD operations",
                "labels": "Complete CRUD operations",
                "cycles": "Create, update, and archive operations",
                "milestones": "Complete CRUD operations",
                "attachments": "Complete CRUD operations",
                "notifications": "Update and mark read/unread operations",
                "favorites": "Complete CRUD operations",
                "templates": "Complete CRUD operations",
                "integrations": "Complete CRUD operations",
                "webhooks": "Complete CRUD operations",
                "api_keys": "Create and delete operations",
                "roadmaps": "Complete CRUD operations"
            }
        }
    
    async def validate_connection(self) -> bool:
        """Validate the Linear connection by fetching viewer information."""
        try:
            response = await self.viewer()
            return response.success and response.data is not None
        except Exception as e:
            print(f"Connection validation failed: {e}")
            return False

    # =============================================================================
    # CONVENIENCE METHODS FOR COMMON OPERATIONS
    # =============================================================================

    async def get_current_user(self) -> GraphQLResponse:
        """Get current user information."""
        return await self.viewer()
    
    async def get_all_teams(self, limit: int = 50) -> GraphQLResponse:
        """Get all teams."""
        return await self.teams(first=limit)
    
    async def get_team_issues(self, team_id: str, limit: int = 50) -> GraphQLResponse:
        """Get issues for a specific team."""
        team_filter = {"team": {"id": {"eq": team_id}}}
        return await self.issues(first=limit, filter=team_filter)
    
    async def get_user_issues(self, user_id: str, limit: int = 50) -> GraphQLResponse:
        """Get issues assigned to a specific user."""
        user_filter = {"assignee": {"id": {"eq": user_id}}}
        return await self.issues(first=limit, filter=user_filter)
    
    async def get_project_issues(self, project_id: str, limit: int = 50) -> GraphQLResponse:
        """Get issues for a specific project."""
        project_filter = {"project": {"id": {"eq": project_id}}}
        return await self.issues(first=limit, filter=project_filter)
    
    async def create_simple_issue(
        self, 
        title: str, 
        team_id: str, 
        description: Optional[str] = None,
        assignee_id: Optional[str] = None,
        priority: Optional[int] = None,
        labels: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Create a simple issue with basic information."""
        issue_input = {
            "title": title,
            "teamId": team_id
        }
        
        if description:
            issue_input["description"] = description
        if assignee_id:
            issue_input["assigneeId"] = assignee_id
        if priority is not None:
            issue_input["priority"] = priority
        if labels:
            issue_input["labelIds"] = labels
            
        return await self.issue_create(input=issue_input)
    
    async def update_issue_status(self, issue_id: str, state_id: str) -> GraphQLResponse:
        """Update an issue's status."""
        update_input = {"stateId": state_id}
        return await self.issue_update(id=issue_id, input=update_input)
    
    async def assign_issue(self, issue_id: str, assignee_id: str) -> GraphQLResponse:
        """Assign an issue to a user."""
        update_input = {"assigneeId": assignee_id}
        return await self.issue_update(id=issue_id, input=update_input)
    
    async def create_simple_project(
        self, 
        name: str, 
        description: Optional[str] = None,
        team_ids: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Create a simple project."""
        project_input = {"name": name}
        
        if description:
            project_input["description"] = description
        if team_ids:
            project_input["teamIds"] = team_ids
            
        return await self.project_create(input=project_input)
    
    async def add_comment_to_issue(
        self, 
        issue_id: str, 
        body: str
    ) -> GraphQLResponse:
        """Add a comment to an issue."""
        comment_input = {
            "issueId": issue_id,
            "body": body
        }
        return await self.comment_create(input=comment_input)
    
    async def create_team_with_members(
        self, 
        name: str, 
        key: str, 
        description: Optional[str] = None,
        member_ids: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Create a team with optional members."""
        team_input = {
            "name": name,
            "key": key
        }
        
        if description:
            team_input["description"] = description
            
        # First create the team
        team_response = await self.team_create(input=team_input)
        
        # If team creation was successful and member_ids provided, add members
        if team_response.success and member_ids and team_response.data:
            team_id = team_response.data.get("teamCreate", {}).get("team", {}).get("id")
            if team_id:
                for member_id in member_ids:
                    await self.team_membership_create(input={
                        "teamId": team_id,
                        "userId": member_id
                    })
        
        return team_response
'''
        
        return class_code
    
    def save_to_file(self, filename: Optional[str] = None):
        """Generate and save the complete class to a file."""
        if filename is None:
            filename = "linear_data_source.py"
            
        # Create linear directory
        script_dir = Path(__file__).parent if __file__ else Path('.')
        linear_dir = script_dir / 'linear'
        linear_dir.mkdir(exist_ok=True)
        
        # Set the full file path
        full_path = linear_dir / filename
        
        class_code = self.generate_linear_datasource()
        
        full_path.write_text(class_code, encoding='utf-8')
        
        print(f"Generated Linear data source with {len(self.generated_methods)} methods")
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


def process_linear_graphql_api(filename: Optional[str] = None) -> None:
    """End-to-end pipeline for Linear GraphQL API generation."""
    print(f"Starting Linear GraphQL data source generation...")
    
    generator = LinearDataSourceGenerator()
    
    try:
        print("Analyzing Linear GraphQL operations and generating wrapper methods...")
        generator.save_to_file(filename)
        
        script_dir = Path(__file__).parent if __file__ else Path('.')
        print(f"\nFiles generated in: {script_dir / 'linear'}")
        
        print(f"\nSuccessfully generated Linear data source with comprehensive GraphQL operations!")
        
    except Exception as e:
        print(f"Error: {e}")
        raise


def main():
    """Main function for Linear data source generator."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate Linear GraphQL data source')
    parser.add_argument('--filename', '-f', help='Output filename (optional)')
    
    args = parser.parse_args()
    
    try:
        process_linear_graphql_api(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Linear data source: {e}")
        return 1


if __name__ == "__main__":
    exit(main())