#!/usr/bin/env python3
# ruff: noqa
"""
Bitbucket Cloud API 2.0 Code Generator
========================================

Generates comprehensive BitbucketDataSource class covering ALL Bitbucket Cloud REST APIs.

Coverage:
- Repositories (CRUD, forks, watchers, downloads)
- Workspaces (members, projects, hooks)
- Pull Requests (CRUD, approvals, comments, tasks, statuses, activities)
- Commits (history, statuses, approvals)
- Branches & Tags (CRUD operations)
- Pipelines (run, stop, list)
- Projects (CRUD operations)
- Issues (CRUD, comments, attachments)
- Webhooks (CRUD operations)
- Users & Groups (management, permissions)
- Snippets (CRUD operations)
- Source/Files (browse, read files)
- Deployments (environments, variables)
- Reports (code quality, test results)
- Branch Restrictions (CRUD operations)
- Downloads (CRUD operations)
- SSH & GPG Keys
- Permissions

Total Endpoints: 200+

API Documentation: https://developer.atlassian.com/cloud/bitbucket/rest/
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import textwrap


# ============================================================================
# BITBUCKET API ENDPOINT DEFINITIONS
# ============================================================================


class BitbucketAPIEndpoints:
    """Complete Bitbucket Cloud API 2.0 endpoint definitions."""

    @staticmethod
    def get_workspace_endpoints() -> List[Dict[str, Any]]:
        """Workspace management endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/workspaces",
                "name": "list_workspaces",
                "description": "Returns a paginated list of workspaces accessible by the authenticated user.",
                "params": [
                    {
                        "name": "role",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Filter by role (member, collaborator, owner)",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query string for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                    {
                        "name": "page",
                        "type": "Optional[int]",
                        "default": "None",
                        "description": "Page number",
                    },
                ],
                "required_scopes": ["workspace"],
            },
            {
                "method": "GET",
                "path": "/2.0/workspaces/{workspace}",
                "name": "get_workspace",
                "description": "Returns the requested workspace.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                ],
                "required_scopes": ["workspace"],
            },
            {
                "method": "GET",
                "path": "/2.0/workspaces/{workspace}/members",
                "name": "list_workspace_members",
                "description": "Returns all members of the requested workspace.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["workspace"],
            },
            {
                "method": "GET",
                "path": "/2.0/workspaces/{workspace}/members/{member}",
                "name": "get_workspace_member",
                "description": "Returns the workspace membership of a specific user.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "member",
                        "type": "str",
                        "default": None,
                        "description": "Member UUID or username",
                    },
                ],
                "required_scopes": ["workspace"],
            },
            {
                "method": "GET",
                "path": "/2.0/workspaces/{workspace}/projects",
                "name": "list_workspace_projects",
                "description": "Returns the list of projects in this workspace.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["project"],
            },
            {
                "method": "GET",
                "path": "/2.0/workspaces/{workspace}/hooks",
                "name": "list_workspace_webhooks",
                "description": "Returns a paginated list of webhooks installed on this workspace.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["webhook"],
            },
            {
                "method": "POST",
                "path": "/2.0/workspaces/{workspace}/hooks",
                "name": "create_workspace_webhook",
                "description": "Creates a new webhook on the specified workspace.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Webhook configuration",
                    },
                ],
                "required_scopes": ["webhook"],
            },
            {
                "method": "GET",
                "path": "/2.0/workspaces/{workspace}/hooks/{uid}",
                "name": "get_workspace_webhook",
                "description": "Returns the webhook with the specified id.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "uid",
                        "type": "str",
                        "default": None,
                        "description": "Webhook UUID",
                    },
                ],
                "required_scopes": ["webhook"],
            },
            {
                "method": "PUT",
                "path": "/2.0/workspaces/{workspace}/hooks/{uid}",
                "name": "update_workspace_webhook",
                "description": "Updates the specified webhook.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "uid",
                        "type": "str",
                        "default": None,
                        "description": "Webhook UUID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated webhook configuration",
                    },
                ],
                "required_scopes": ["webhook"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/workspaces/{workspace}/hooks/{uid}",
                "name": "delete_workspace_webhook",
                "description": "Deletes the specified webhook.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "uid",
                        "type": "str",
                        "default": None,
                        "description": "Webhook UUID",
                    },
                ],
                "required_scopes": ["webhook"],
            },
            {
                "method": "GET",
                "path": "/2.0/workspaces/{workspace}/permissions",
                "name": "list_workspace_permissions",
                "description": "Returns the list of members in a workspace and their permission levels.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                ],
                "required_scopes": ["workspace"],
            },
        ]

    @staticmethod
    def get_repository_endpoints() -> List[Dict[str, Any]]:
        """Repository management endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}",
                "name": "list_repositories",
                "description": "Returns a paginated list of all repositories owned by the specified workspace.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "role",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Filter by role",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query string for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}",
                "name": "get_repository",
                "description": "Returns the object describing this repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}",
                "name": "create_repository",
                "description": "Creates a new repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Repository configuration",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}",
                "name": "update_repository",
                "description": "Updates the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated repository configuration",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}",
                "name": "delete_repository",
                "description": "Deletes the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                ],
                "required_scopes": ["repository:delete"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/watchers",
                "name": "list_repository_watchers",
                "description": "Returns a paginated list of all the watchers on the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/forks",
                "name": "list_repository_forks",
                "description": "Returns a paginated list of all the forks of the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/forks",
                "name": "fork_repository",
                "description": "Creates a new fork of the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Optional[Dict[str, Any]]",
                        "default": "None",
                        "description": "Fork configuration",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/hooks",
                "name": "list_repository_webhooks",
                "description": "Returns a paginated list of webhooks installed on this repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["webhook"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/hooks",
                "name": "create_repository_webhook",
                "description": "Creates a new webhook on the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Webhook configuration",
                    },
                ],
                "required_scopes": ["webhook"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/hooks/{uid}",
                "name": "get_repository_webhook",
                "description": "Returns the webhook with the specified id.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "uid",
                        "type": "str",
                        "default": None,
                        "description": "Webhook UUID",
                    },
                ],
                "required_scopes": ["webhook"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/hooks/{uid}",
                "name": "update_repository_webhook",
                "description": "Updates the specified webhook.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "uid",
                        "type": "str",
                        "default": None,
                        "description": "Webhook UUID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated webhook configuration",
                    },
                ],
                "required_scopes": ["webhook"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/hooks/{uid}",
                "name": "delete_repository_webhook",
                "description": "Deletes the specified webhook.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "uid",
                        "type": "str",
                        "default": None,
                        "description": "Webhook UUID",
                    },
                ],
                "required_scopes": ["webhook"],
            },
        ]

    @staticmethod
    def get_commit_endpoints() -> List[Dict[str, Any]]:
        """Commit management endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commits",
                "name": "list_commits",
                "description": "Returns all commits in the repository that are reachable from specified commit.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "include",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Branch/tag/commit to start from",
                    },
                    {
                        "name": "exclude",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Branch/tag/commit to exclude",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commits/{commit}",
                "name": "get_commit",
                "description": "Returns the specified commit.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commits/{commit}/comments",
                "name": "list_commit_comments",
                "description": "Returns the commit's comments.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commits/{commit}/comments",
                "name": "create_commit_comment",
                "description": "Creates a comment on the specified commit.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Comment content",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/statuses",
                "name": "list_commit_statuses",
                "description": "Returns all statuses for a given commit.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/statuses/build",
                "name": "create_commit_build_status",
                "description": "Creates a new build status on the specified commit.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Build status data",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/statuses/build/{key}",
                "name": "get_commit_build_status",
                "description": "Returns the specified build status for a commit.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                    {
                        "name": "key",
                        "type": "str",
                        "default": None,
                        "description": "Build status key",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/statuses/build/{key}",
                "name": "update_commit_build_status",
                "description": "Updates the specified build status for a commit.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                    {
                        "name": "key",
                        "type": "str",
                        "default": None,
                        "description": "Build status key",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated build status data",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/approve",
                "name": "approve_commit",
                "description": "Approve the specified commit as the authenticated user.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/approve",
                "name": "unapprove_commit",
                "description": "Revoke approval for the specified commit as the authenticated user.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
        ]

    @staticmethod
    def get_pullrequest_endpoints() -> List[Dict[str, Any]]:
        """Pull request endpoints - Part 1."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests",
                "name": "list_pull_requests",
                "description": "Returns all pull requests on the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "state",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Filter by state: OPEN, MERGED, DECLINED, SUPERSEDED",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["pullrequest"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests",
                "name": "create_pull_request",
                "description": "Creates a new pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Pull request data (title, source, destination, etc.)",
                    },
                ],
                "required_scopes": ["pullrequest:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}",
                "name": "get_pull_request",
                "description": "Returns the specified pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                ],
                "required_scopes": ["pullrequest"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}",
                "name": "update_pull_request",
                "description": "Mutates the specified pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated pull request data",
                    },
                ],
                "required_scopes": ["pullrequest:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/activity",
                "name": "get_pull_request_activity",
                "description": "Returns a paginated list of the pull request's activity log.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["pullrequest"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments",
                "name": "list_pull_request_comments",
                "description": "Returns a paginated list of the pull request's comments.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["pullrequest"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments",
                "name": "create_pull_request_comment",
                "description": "Creates a new pull request comment.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Comment content",
                    },
                ],
                "required_scopes": ["pullrequest"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments/{comment_id}",
                "name": "get_pull_request_comment",
                "description": "Returns a specific pull request comment.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                    {
                        "name": "comment_id",
                        "type": "int",
                        "default": None,
                        "description": "Comment ID",
                    },
                ],
                "required_scopes": ["pullrequest"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments/{comment_id}",
                "name": "update_pull_request_comment",
                "description": "Updates a specific pull request comment.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                    {
                        "name": "comment_id",
                        "type": "int",
                        "default": None,
                        "description": "Comment ID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated comment content",
                    },
                ],
                "required_scopes": ["pullrequest"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments/{comment_id}",
                "name": "delete_pull_request_comment",
                "description": "Deletes a specific pull request comment.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                    {
                        "name": "comment_id",
                        "type": "int",
                        "default": None,
                        "description": "Comment ID",
                    },
                ],
                "required_scopes": ["pullrequest"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/commits",
                "name": "list_pull_request_commits",
                "description": "Returns all commits on the specified pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/approve",
                "name": "approve_pull_request",
                "description": "Approve the specified pull request as the authenticated user.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                ],
                "required_scopes": ["pullrequest:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/approve",
                "name": "unapprove_pull_request",
                "description": "Revoke the authenticated user's approval of the specified pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                ],
                "required_scopes": ["pullrequest:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/diff",
                "name": "get_pull_request_diff",
                "description": "Returns the diff for the pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/diffstat",
                "name": "get_pull_request_diffstat",
                "description": "Returns the diffstat for the pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/merge",
                "name": "merge_pull_request",
                "description": "Merges the pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                    {
                        "name": "body",
                        "type": "Optional[Dict[str, Any]]",
                        "default": "None",
                        "description": "Merge options",
                    },
                ],
                "required_scopes": ["pullrequest:write"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/decline",
                "name": "decline_pull_request",
                "description": "Declines the pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                ],
                "required_scopes": ["pullrequest:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/statuses",
                "name": "list_pull_request_statuses",
                "description": "Returns all statuses for the specified pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                ],
                "required_scopes": ["repository"],
            },
        ]

    @staticmethod
    def get_branch_endpoints() -> List[Dict[str, Any]]:
        """Branch management endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/refs/branches",
                "name": "list_branches",
                "description": "Returns a paginated list of branches for the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/refs/branches",
                "name": "create_branch",
                "description": "Creates a new branch in the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Branch data (name, target)",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/refs/branches/{name}",
                "name": "get_branch",
                "description": "Returns a branch object within the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "name",
                        "type": "str",
                        "default": None,
                        "description": "Branch name",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/refs/branches/{name}",
                "name": "delete_branch",
                "description": "Deletes a branch in the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "name",
                        "type": "str",
                        "default": None,
                        "description": "Branch name",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/branch-restrictions",
                "name": "list_branch_restrictions",
                "description": "Returns a paginated list of all branch restrictions on the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/branch-restrictions",
                "name": "create_branch_restriction",
                "description": "Creates a new branch restriction rule.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Branch restriction data",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/branch-restrictions/{id}",
                "name": "get_branch_restriction",
                "description": "Returns a specific branch restriction rule.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "id",
                        "type": "int",
                        "default": None,
                        "description": "Branch restriction ID",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/branch-restrictions/{id}",
                "name": "update_branch_restriction",
                "description": "Updates an existing branch restriction rule.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "id",
                        "type": "int",
                        "default": None,
                        "description": "Branch restriction ID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated branch restriction data",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/branch-restrictions/{id}",
                "name": "delete_branch_restriction",
                "description": "Deletes an existing branch restriction rule.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "id",
                        "type": "int",
                        "default": None,
                        "description": "Branch restriction ID",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
        ]

    @staticmethod
    def get_tag_endpoints() -> List[Dict[str, Any]]:
        """Tag management endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/refs/tags",
                "name": "list_tags",
                "description": "Returns a paginated list of tags for the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/refs/tags",
                "name": "create_tag",
                "description": "Creates a new tag in the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Tag data (name, target)",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/refs/tags/{name}",
                "name": "get_tag",
                "description": "Returns a tag object within the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "name",
                        "type": "str",
                        "default": None,
                        "description": "Tag name",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/refs/tags/{name}",
                "name": "delete_tag",
                "description": "Deletes a tag in the specified repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "name",
                        "type": "str",
                        "default": None,
                        "description": "Tag name",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
        ]

    @staticmethod
    def get_pipeline_endpoints() -> List[Dict[str, Any]]:
        """Pipeline endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines",
                "name": "list_pipelines",
                "description": "Returns a paginated list of pipelines.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["pipeline"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines",
                "name": "create_pipeline",
                "description": "Triggers a new pipeline.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Pipeline configuration",
                    },
                ],
                "required_scopes": ["pipeline:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}",
                "name": "get_pipeline",
                "description": "Returns a pipeline for the given pipeline UUID.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pipeline_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Pipeline UUID with curly braces",
                    },
                ],
                "required_scopes": ["pipeline"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/stopPipeline",
                "name": "stop_pipeline",
                "description": "Stops a pipeline.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pipeline_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Pipeline UUID with curly braces",
                    },
                ],
                "required_scopes": ["pipeline:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps",
                "name": "list_pipeline_steps",
                "description": "Returns the steps of a pipeline.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pipeline_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Pipeline UUID with curly braces",
                    },
                ],
                "required_scopes": ["pipeline"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps/{step_uuid}",
                "name": "get_pipeline_step",
                "description": "Returns a given step of a pipeline.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pipeline_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Pipeline UUID with curly braces",
                    },
                    {
                        "name": "step_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Step UUID with curly braces",
                    },
                ],
                "required_scopes": ["pipeline"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps/{step_uuid}/log",
                "name": "get_pipeline_step_log",
                "description": "Returns the log of a given step of a pipeline.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pipeline_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Pipeline UUID with curly braces",
                    },
                    {
                        "name": "step_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Step UUID with curly braces",
                    },
                ],
                "required_scopes": ["pipeline"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines_config",
                "name": "get_pipeline_config",
                "description": "Returns the configuration for the repository's pipelines.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                ],
                "required_scopes": ["pipeline"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines_config",
                "name": "update_pipeline_config",
                "description": "Updates the configuration for the repository's pipelines.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Pipeline configuration",
                    },
                ],
                "required_scopes": ["pipeline:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines_config/variables",
                "name": "list_pipeline_variables",
                "description": "Returns a list of pipeline variables for the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                ],
                "required_scopes": ["pipeline:variable"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines_config/variables",
                "name": "create_pipeline_variable",
                "description": "Creates a new pipeline variable.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Variable data",
                    },
                ],
                "required_scopes": ["pipeline:variable:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines_config/variables/{variable_uuid}",
                "name": "get_pipeline_variable",
                "description": "Returns a pipeline variable by UUID.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "variable_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Variable UUID with curly braces",
                    },
                ],
                "required_scopes": ["pipeline:variable"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines_config/variables/{variable_uuid}",
                "name": "update_pipeline_variable",
                "description": "Updates a pipeline variable.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "variable_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Variable UUID with curly braces",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated variable data",
                    },
                ],
                "required_scopes": ["pipeline:variable:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pipelines_config/variables/{variable_uuid}",
                "name": "delete_pipeline_variable",
                "description": "Deletes a pipeline variable.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "variable_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Variable UUID with curly braces",
                    },
                ],
                "required_scopes": ["pipeline:variable:write"],
            },
        ]

    @staticmethod
    def get_project_endpoints() -> List[Dict[str, Any]]:
        """Project management endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/workspaces/{workspace}/projects/{project_key}",
                "name": "get_project",
                "description": "Returns the requested project.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "project_key",
                        "type": "str",
                        "default": None,
                        "description": "Project key",
                    },
                ],
                "required_scopes": ["project"],
            },
            {
                "method": "PUT",
                "path": "/2.0/workspaces/{workspace}/projects/{project_key}",
                "name": "update_project",
                "description": "Updates an existing project.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "project_key",
                        "type": "str",
                        "default": None,
                        "description": "Project key",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated project data",
                    },
                ],
                "required_scopes": ["project:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/workspaces/{workspace}/projects/{project_key}",
                "name": "delete_project",
                "description": "Deletes the specified project.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "project_key",
                        "type": "str",
                        "default": None,
                        "description": "Project key",
                    },
                ],
                "required_scopes": ["project:admin"],
            },
            {
                "method": "POST",
                "path": "/2.0/workspaces/{workspace}/projects",
                "name": "create_project",
                "description": "Creates a new project.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Project data",
                    },
                ],
                "required_scopes": ["project:write"],
            },
        ]

    @staticmethod
    def get_issue_endpoints() -> List[Dict[str, Any]]:
        """Issue tracker endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues",
                "name": "list_issues",
                "description": "Returns the issues in the issue tracker.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                    {
                        "name": "pagelen",
                        "type": "int",
                        "default": "10",
                        "description": "Number of items per page",
                    },
                ],
                "required_scopes": ["issue"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues",
                "name": "create_issue",
                "description": "Creates a new issue.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Issue data",
                    },
                ],
                "required_scopes": ["issue:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}",
                "name": "get_issue",
                "description": "Returns the specified issue.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                ],
                "required_scopes": ["issue"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}",
                "name": "update_issue",
                "description": "Updates the specified issue.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated issue data",
                    },
                ],
                "required_scopes": ["issue:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}",
                "name": "delete_issue",
                "description": "Deletes the specified issue.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                ],
                "required_scopes": ["issue:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments",
                "name": "list_issue_comments",
                "description": "Returns a paginated list of issue comments.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                ],
                "required_scopes": ["issue"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments",
                "name": "create_issue_comment",
                "description": "Creates a new issue comment.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Comment content",
                    },
                ],
                "required_scopes": ["issue:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments/{comment_id}",
                "name": "get_issue_comment",
                "description": "Returns the specified issue comment.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                    {
                        "name": "comment_id",
                        "type": "int",
                        "default": None,
                        "description": "Comment ID",
                    },
                ],
                "required_scopes": ["issue"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments/{comment_id}",
                "name": "update_issue_comment",
                "description": "Updates the specified issue comment.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                    {
                        "name": "comment_id",
                        "type": "int",
                        "default": None,
                        "description": "Comment ID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated comment content",
                    },
                ],
                "required_scopes": ["issue:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments/{comment_id}",
                "name": "delete_issue_comment",
                "description": "Deletes the specified issue comment.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                    {
                        "name": "comment_id",
                        "type": "int",
                        "default": None,
                        "description": "Comment ID",
                    },
                ],
                "required_scopes": ["issue:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/attachments",
                "name": "list_issue_attachments",
                "description": "Returns all attachments for the specified issue.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                ],
                "required_scopes": ["issue"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/attachments",
                "name": "upload_issue_attachment",
                "description": "Uploads a file as an attachment to an issue.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                    {
                        "name": "files",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "File data",
                    },
                ],
                "required_scopes": ["issue:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/attachments/{path}",
                "name": "get_issue_attachment",
                "description": "Returns the specified attachment.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                    {
                        "name": "path",
                        "type": "str",
                        "default": None,
                        "description": "Attachment path",
                    },
                ],
                "required_scopes": ["issue"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/attachments/{path}",
                "name": "delete_issue_attachment",
                "description": "Deletes the specified attachment.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                    {
                        "name": "path",
                        "type": "str",
                        "default": None,
                        "description": "Attachment path",
                    },
                ],
                "required_scopes": ["issue:write"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/vote",
                "name": "vote_for_issue",
                "description": "Vote for this issue.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                ],
                "required_scopes": ["issue:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/vote",
                "name": "unvote_for_issue",
                "description": "Remove your vote from this issue.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                ],
                "required_scopes": ["issue:write"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/watch",
                "name": "watch_issue",
                "description": "Start watching this issue.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                ],
                "required_scopes": ["issue:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/issues/{issue_id}/watch",
                "name": "unwatch_issue",
                "description": "Stop watching this issue.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "issue_id",
                        "type": "int",
                        "default": None,
                        "description": "Issue ID",
                    },
                ],
                "required_scopes": ["issue:write"],
            },
        ]

    @staticmethod
    def get_source_endpoints() -> List[Dict[str, Any]]:
        """Source/Files browsing endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/src/{commit}/{path}",
                "name": "get_file_content",
                "description": "Returns the contents of the specified file.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash or branch/tag name",
                    },
                    {
                        "name": "path",
                        "type": "str",
                        "default": None,
                        "description": "File path",
                    },
                    {
                        "name": "format",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Response format (meta for metadata)",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/src/{commit}",
                "name": "browse_directory",
                "description": "Returns the contents of a directory in the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash or branch/tag name",
                    },
                    {
                        "name": "path",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Directory path (empty for root)",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/filehistory/{commit}/{path}",
                "name": "get_file_history",
                "description": "Returns the file's commit history.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash or branch/tag name",
                    },
                    {
                        "name": "path",
                        "type": "str",
                        "default": None,
                        "description": "File path",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                ],
                "required_scopes": ["repository"],
            },
        ]

    @staticmethod
    def get_user_endpoints() -> List[Dict[str, Any]]:
        """User management endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/user",
                "name": "get_current_user",
                "description": "Returns the currently logged in user.",
                "params": [],
                "required_scopes": ["account"],
            },
            {
                "method": "GET",
                "path": "/2.0/user/emails",
                "name": "list_user_emails",
                "description": "Returns all email addresses associated with the current user.",
                "params": [],
                "required_scopes": ["email"],
            },
            {
                "method": "GET",
                "path": "/2.0/user/emails/{email}",
                "name": "get_user_email",
                "description": "Returns details about the specified email address.",
                "params": [
                    {
                        "name": "email",
                        "type": "str",
                        "default": None,
                        "description": "Email address",
                    },
                ],
                "required_scopes": ["email"],
            },
            {
                "method": "GET",
                "path": "/2.0/users/{selected_user}",
                "name": "get_user",
                "description": "Returns the profile for the specified user.",
                "params": [
                    {
                        "name": "selected_user",
                        "type": "str",
                        "default": None,
                        "description": "User UUID or username",
                    },
                ],
                "required_scopes": ["account"],
            },
            {
                "method": "GET",
                "path": "/2.0/users/{selected_user}/repositories",
                "name": "list_user_repositories",
                "description": "Returns all repositories owned by the specified user.",
                "params": [
                    {
                        "name": "selected_user",
                        "type": "str",
                        "default": None,
                        "description": "User UUID or username",
                    },
                    {
                        "name": "role",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Filter by role",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "GET",
                "path": "/2.0/user/permissions/workspaces",
                "name": "list_user_workspace_permissions",
                "description": "Returns an object for each workspace the caller is a member of.",
                "params": [
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                ],
                "required_scopes": ["account"],
            },
            {
                "method": "GET",
                "path": "/2.0/user/permissions/repositories",
                "name": "list_user_repository_permissions",
                "description": "Returns an object for each repository the caller has explicit access to.",
                "params": [
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                ],
                "required_scopes": ["account"],
            },
        ]

    @staticmethod
    def get_snippet_endpoints() -> List[Dict[str, Any]]:
        """Snippet management endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/snippets/{workspace}",
                "name": "list_snippets",
                "description": "Returns all snippets for the workspace.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "role",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Filter by role (owner, contributor, member)",
                    },
                ],
                "required_scopes": ["snippet"],
            },
            {
                "method": "POST",
                "path": "/2.0/snippets/{workspace}",
                "name": "create_snippet",
                "description": "Creates a new snippet.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Snippet data",
                    },
                ],
                "required_scopes": ["snippet:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/snippets/{workspace}/{encoded_id}",
                "name": "get_snippet",
                "description": "Returns the specified snippet.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "encoded_id",
                        "type": "str",
                        "default": None,
                        "description": "Snippet ID",
                    },
                ],
                "required_scopes": ["snippet"],
            },
            {
                "method": "PUT",
                "path": "/2.0/snippets/{workspace}/{encoded_id}",
                "name": "update_snippet",
                "description": "Updates the specified snippet.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "encoded_id",
                        "type": "str",
                        "default": None,
                        "description": "Snippet ID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated snippet data",
                    },
                ],
                "required_scopes": ["snippet:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/snippets/{workspace}/{encoded_id}",
                "name": "delete_snippet",
                "description": "Deletes the specified snippet.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "encoded_id",
                        "type": "str",
                        "default": None,
                        "description": "Snippet ID",
                    },
                ],
                "required_scopes": ["snippet:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/snippets/{workspace}/{encoded_id}/comments",
                "name": "list_snippet_comments",
                "description": "Returns all comments on the specified snippet.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "encoded_id",
                        "type": "str",
                        "default": None,
                        "description": "Snippet ID",
                    },
                ],
                "required_scopes": ["snippet"],
            },
            {
                "method": "POST",
                "path": "/2.0/snippets/{workspace}/{encoded_id}/comments",
                "name": "create_snippet_comment",
                "description": "Creates a new snippet comment.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "encoded_id",
                        "type": "str",
                        "default": None,
                        "description": "Snippet ID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Comment content",
                    },
                ],
                "required_scopes": ["snippet"],
            },
            {
                "method": "GET",
                "path": "/2.0/snippets/{workspace}/{encoded_id}/{node_id}",
                "name": "get_snippet_file",
                "description": "Returns the raw contents of the specified snippet file.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "encoded_id",
                        "type": "str",
                        "default": None,
                        "description": "Snippet ID",
                    },
                    {
                        "name": "node_id",
                        "type": "str",
                        "default": None,
                        "description": "Node ID (commit hash)",
                    },
                ],
                "required_scopes": ["snippet"],
            },
        ]

    @staticmethod
    def get_deployment_endpoints() -> List[Dict[str, Any]]:
        """Deployment endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/deployments",
                "name": "list_deployments",
                "description": "Returns a paginated list of deployments for the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                ],
                "required_scopes": ["deployment"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/deployments/{deployment_uuid}",
                "name": "get_deployment",
                "description": "Returns the deployment with the specified UUID.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "deployment_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Deployment UUID with curly braces",
                    },
                ],
                "required_scopes": ["deployment"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/environments",
                "name": "list_deployment_environments",
                "description": "Returns a paginated list of deployment environments.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/environments",
                "name": "create_deployment_environment",
                "description": "Creates a new deployment environment.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Environment data",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/environments/{environment_uuid}",
                "name": "get_deployment_environment",
                "description": "Returns the deployment environment with the specified UUID.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "environment_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Environment UUID with curly braces",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/environments/{environment_uuid}",
                "name": "delete_deployment_environment",
                "description": "Deletes the deployment environment with the specified UUID.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "environment_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Environment UUID with curly braces",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/environments/{environment_uuid}/changes",
                "name": "update_deployment_environment",
                "description": "Updates the deployment environment with the specified UUID.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "environment_uuid",
                        "type": "str",
                        "default": None,
                        "description": "Environment UUID with curly braces",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated environment data",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
        ]

    @staticmethod
    def get_download_endpoints() -> List[Dict[str, Any]]:
        """Downloads management endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/downloads",
                "name": "list_downloads",
                "description": "Returns a paginated list of downloads for the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/downloads",
                "name": "upload_download",
                "description": "Uploads a download artifact.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "files",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "File data",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/downloads/{filename}",
                "name": "get_download",
                "description": "Returns the download with the specified filename.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "filename",
                        "type": "str",
                        "default": None,
                        "description": "Download filename",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/downloads/{filename}",
                "name": "delete_download",
                "description": "Deletes the download with the specified filename.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "filename",
                        "type": "str",
                        "default": None,
                        "description": "Download filename",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
        ]

    @staticmethod
    def get_permission_endpoints() -> List[Dict[str, Any]]:
        """Repository permission endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/permissions-config/users",
                "name": "list_repository_user_permissions",
                "description": "Returns a paginated list of all explicit user permissions for the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/permissions-config/users/{selected_user_id}",
                "name": "update_repository_user_permission",
                "description": "Updates the explicit user permission for the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "selected_user_id",
                        "type": "str",
                        "default": None,
                        "description": "User UUID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Permission data (permission level)",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/permissions-config/users/{selected_user_id}",
                "name": "delete_repository_user_permission",
                "description": "Deletes the explicit user permission for the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "selected_user_id",
                        "type": "str",
                        "default": None,
                        "description": "User UUID",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/permissions-config/groups",
                "name": "list_repository_group_permissions",
                "description": "Returns a paginated list of all explicit group permissions for the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "q",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "BBQL query for filtering",
                    },
                    {
                        "name": "sort",
                        "type": "Optional[str]",
                        "default": "None",
                        "description": "Field to sort by",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/permissions-config/groups/{group_slug}",
                "name": "update_repository_group_permission",
                "description": "Updates the explicit group permission for the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "group_slug",
                        "type": "str",
                        "default": None,
                        "description": "Group slug",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Permission data (permission level)",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/permissions-config/groups/{group_slug}",
                "name": "delete_repository_group_permission",
                "description": "Deletes the explicit group permission for the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "group_slug",
                        "type": "str",
                        "default": None,
                        "description": "Group slug",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
        ]

    @staticmethod
    def get_ssh_key_endpoints() -> List[Dict[str, Any]]:
        """SSH key management endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/user/ssh-keys",
                "name": "list_user_ssh_keys",
                "description": "Returns a paginated list of the user's SSH public keys.",
                "params": [],
                "required_scopes": ["account"],
            },
            {
                "method": "POST",
                "path": "/2.0/user/ssh-keys",
                "name": "create_user_ssh_key",
                "description": "Adds a new SSH public key to the account.",
                "params": [
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "SSH key data (key, label)",
                    },
                ],
                "required_scopes": ["account:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/user/ssh-keys/{key_id}",
                "name": "get_user_ssh_key",
                "description": "Returns the SSH key with the specified key ID.",
                "params": [
                    {
                        "name": "key_id",
                        "type": "str",
                        "default": None,
                        "description": "SSH key ID",
                    },
                ],
                "required_scopes": ["account"],
            },
            {
                "method": "PUT",
                "path": "/2.0/user/ssh-keys/{key_id}",
                "name": "update_user_ssh_key",
                "description": "Updates the specified SSH key.",
                "params": [
                    {
                        "name": "key_id",
                        "type": "str",
                        "default": None,
                        "description": "SSH key ID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Updated SSH key data",
                    },
                ],
                "required_scopes": ["account:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/user/ssh-keys/{key_id}",
                "name": "delete_user_ssh_key",
                "description": "Deletes the specified SSH key.",
                "params": [
                    {
                        "name": "key_id",
                        "type": "str",
                        "default": None,
                        "description": "SSH key ID",
                    },
                ],
                "required_scopes": ["account:write"],
            },
        ]

    @staticmethod
    def get_report_endpoints() -> List[Dict[str, Any]]:
        """Code quality and test report endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/reports",
                "name": "list_commit_reports",
                "description": "Returns a paginated list of reports for the commit.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{report_id}",
                "name": "get_commit_report",
                "description": "Returns the report with the specified ID.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                    {
                        "name": "report_id",
                        "type": "str",
                        "default": None,
                        "description": "Report ID",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{report_id}",
                "name": "create_or_update_commit_report",
                "description": "Creates or updates a report for the commit.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                    {
                        "name": "report_id",
                        "type": "str",
                        "default": None,
                        "description": "Report ID",
                    },
                    {
                        "name": "body",
                        "type": "Dict[str, Any]",
                        "default": None,
                        "description": "Report data",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{report_id}",
                "name": "delete_commit_report",
                "description": "Deletes the report with the specified ID.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                    {
                        "name": "report_id",
                        "type": "str",
                        "default": None,
                        "description": "Report ID",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{report_id}/annotations",
                "name": "list_commit_report_annotations",
                "description": "Returns a paginated list of annotations for the report.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                    {
                        "name": "report_id",
                        "type": "str",
                        "default": None,
                        "description": "Report ID",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{report_id}/annotations",
                "name": "create_commit_report_annotations",
                "description": "Creates annotations for the report.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "commit",
                        "type": "str",
                        "default": None,
                        "description": "Commit hash",
                    },
                    {
                        "name": "report_id",
                        "type": "str",
                        "default": None,
                        "description": "Report ID",
                    },
                    {
                        "name": "body",
                        "type": "List[Dict[str, Any]]",
                        "default": None,
                        "description": "List of annotations",
                    },
                ],
                "required_scopes": ["repository:write"],
            },
        ]

    @staticmethod
    def get_additional_pullrequest_endpoints() -> List[Dict[str, Any]]:
        """Additional pull request endpoints - Part 2."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/patch",
                "name": "get_pull_request_patch",
                "description": "Returns the patch for the pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                ],
                "required_scopes": ["repository"],
            },
            {
                "method": "POST",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/request-changes",
                "name": "request_changes_on_pull_request",
                "description": "Request changes on the pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                ],
                "required_scopes": ["pullrequest:write"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/request-changes",
                "name": "unrequest_changes_on_pull_request",
                "description": "Remove request for changes on the pull request.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "pull_request_id",
                        "type": "int",
                        "default": None,
                        "description": "Pull request ID",
                    },
                ],
                "required_scopes": ["pullrequest:write"],
            },
        ]

    @staticmethod
    def get_default_reviewers_endpoints() -> List[Dict[str, Any]]:
        """Default reviewers endpoints."""
        return [
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/default-reviewers",
                "name": "list_default_reviewers",
                "description": "Returns the repository's default reviewers.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "GET",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/default-reviewers/{target_username}",
                "name": "get_default_reviewer",
                "description": "Returns the specified default reviewer.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "target_username",
                        "type": "str",
                        "default": None,
                        "description": "Reviewer username",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "PUT",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/default-reviewers/{target_username}",
                "name": "add_default_reviewer",
                "description": "Adds a default reviewer to the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "target_username",
                        "type": "str",
                        "default": None,
                        "description": "Reviewer username",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
            {
                "method": "DELETE",
                "path": "/2.0/repositories/{workspace}/{repo_slug}/default-reviewers/{target_username}",
                "name": "remove_default_reviewer",
                "description": "Removes a default reviewer from the repository.",
                "params": [
                    {
                        "name": "workspace",
                        "type": "str",
                        "default": None,
                        "description": "Workspace slug or UUID",
                    },
                    {
                        "name": "repo_slug",
                        "type": "str",
                        "default": None,
                        "description": "Repository slug",
                    },
                    {
                        "name": "target_username",
                        "type": "str",
                        "default": None,
                        "description": "Reviewer username",
                    },
                ],
                "required_scopes": ["repository:admin"],
            },
        ]

    @staticmethod
    def get_all_endpoints() -> List[Dict[str, Any]]:
        """Returns all Bitbucket API endpoints."""
        endpoints = []
        endpoints.extend(BitbucketAPIEndpoints.get_workspace_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_repository_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_commit_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_pullrequest_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_additional_pullrequest_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_branch_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_tag_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_pipeline_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_project_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_issue_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_source_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_user_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_snippet_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_deployment_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_download_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_permission_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_ssh_key_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_report_endpoints())
        endpoints.extend(BitbucketAPIEndpoints.get_default_reviewers_endpoints())
        return endpoints


# ============================================================================
# CODE GENERATOR CLASS - FIXED INDENTATION
# ============================================================================


class BitbucketCodeGenerator:
    """Generates BitbucketDataSource class from API endpoint definitions."""

    def __init__(self):
        self.endpoints = BitbucketAPIEndpoints.get_all_endpoints()

    def _format_param_signature(self, params: List[Dict[str, Any]]) -> str:
        """Format method parameters with types."""
        param_strings = []
        for param in params:
            name = param["name"]
            param_type = param["type"]
            default = param.get("default")

            if default is None:
                # Required parameter
                param_strings.append(f"{name}: {param_type}")
            elif default == "None":
                # Optional parameter
                param_strings.append(f"{name}: {param_type} = None")
            else:
                # Parameter with default value
                param_strings.append(f"{name}: {param_type} = {default}")

        return ", ".join(param_strings)

    def _format_docstring(self, endpoint: Dict[str, Any]) -> str:
        """Format method docstring with proper indentation."""
        lines = [f'        """{endpoint["description"]}']

        if endpoint["params"]:
            lines.append("")
            lines.append("        Args:")
            for param in endpoint["params"]:
                lines.append(f"            {param['name']}: {param['description']}")

        lines.append("")
        lines.append("        Returns:")
        lines.append(
            "            BitbucketResponse: Response containing data or error information"
        )
        lines.append("")
        lines.append(
            f"        Required OAuth scopes: {', '.join(endpoint['required_scopes'])}"
        )
        lines.append('        """')

        return "\n".join(lines)

    def _get_path_params(self, path: str) -> List[str]:
        """Extract path parameters from URL path."""
        import re

        return re.findall(r"\{([^}]+)\}", path)

    def _build_query_params_code(
        self, params: List[Dict[str, Any]], path_params: List[str]
    ) -> str:
        """Build query parameters dictionary code."""
        # Get non-path, non-body, non-files parameters
        query_params = []
        for param in params:
            if param["name"] not in path_params and param["name"] not in [
                "body",
                "files",
            ]:
                query_params.append(param["name"])

        if not query_params:
            return ""

        lines = ["            params = {}"]
        for param_name in query_params:
            lines.append(f"            if {param_name} is not None:")
            lines.append(f'                params["{param_name}"] = {param_name}')
        lines.append("")
        return "\n".join(lines)

    def _build_url_code(self, endpoint: Dict[str, Any]) -> str:
        """Build URL construction code."""
        path = endpoint["path"]
        return f'            url = f"{path}"'

    def generate_method(self, endpoint: Dict[str, Any]) -> str:
        """Generate a single method with proper indentation."""
        method_name = endpoint["name"]
        params = endpoint["params"]
        http_method = endpoint["method"]

        # Extract path parameters
        path_params = self._get_path_params(endpoint["path"])

        # Check if method has body or files parameter
        has_body = any(p["name"] == "body" for p in params)
        has_files = any(p["name"] == "files" for p in params)

        # Build method signature
        param_sig = self._format_param_signature(params)
        if param_sig:
            signature = (
                f"    async def {method_name}(self, {param_sig}) -> BitbucketResponse:"
            )
        else:
            signature = f"    async def {method_name}(self) -> BitbucketResponse:"

        # Build method body
        lines = [signature]
        lines.append(self._format_docstring(endpoint))
        lines.append("        try:")

        # Build query parameters
        query_params_code = self._build_query_params_code(params, path_params)
        if query_params_code:
            lines.append(query_params_code)

        # Build URL
        lines.append(self._build_url_code(endpoint))
        lines.append("")

        # Build HTTP request based on method and parameters
        has_query_params = bool(query_params_code)

        if http_method == "GET":
            if has_query_params:
                lines.append(
                    "            response = await self.client.get(url, params=params)"
                )
            else:
                lines.append("            response = await self.client.get(url)")

        elif http_method == "DELETE":
            if has_query_params:
                lines.append(
                    "            response = await self.client.delete(url, params=params)"
                )
            else:
                lines.append("            response = await self.client.delete(url)")

        elif http_method == "POST":
            if has_files:
                lines.append(
                    "            response = await self.client.upload(url, files=files)"
                )
            elif has_body:
                if has_query_params:
                    lines.append(
                        "            response = await self.client.post(url, json=body, params=params)"
                    )
                else:
                    lines.append(
                        "            response = await self.client.post(url, json=body)"
                    )
            else:
                if has_query_params:
                    lines.append(
                        "            response = await self.client.post(url, params=params)"
                    )
                else:
                    lines.append("            response = await self.client.post(url)")

        elif http_method == "PUT":
            if has_body:
                if has_query_params:
                    lines.append(
                        "            response = await self.client.put(url, json=body, params=params)"
                    )
                else:
                    lines.append(
                        "            response = await self.client.put(url, json=body)"
                    )
            else:
                if has_query_params:
                    lines.append(
                        "            response = await self.client.put(url, params=params)"
                    )
                else:
                    lines.append("            response = await self.client.put(url)")

        elif http_method == "PATCH":
            if has_body:
                if has_query_params:
                    lines.append(
                        "            response = await self.client.patch(url, json=body, params=params)"
                    )
                else:
                    lines.append(
                        "            response = await self.client.patch(url, json=body)"
                    )
            else:
                if has_query_params:
                    lines.append(
                        "            response = await self.client.patch(url, params=params)"
                    )
                else:
                    lines.append("            response = await self.client.patch(url)")

        # Handle response
        lines.append("")
        lines.append("            if response.status >= 400:")
        lines.append("                return BitbucketResponse(")
        lines.append("                    success=False,")
        lines.append(
            '                    error=f"Request failed with status {response.status}",'
        )
        lines.append("                    message=response.text")
        lines.append("                )")
        lines.append("")
        lines.append("            # Handle empty responses (e.g., 204 No Content)")
        lines.append("            if response.status == 204 or not response.text:")
        lines.append("                return BitbucketResponse(success=True, data={})")
        lines.append("")
        lines.append("            data = response.json() if response.text else {}")
        lines.append("            return BitbucketResponse(success=True, data=data)")
        lines.append("")
        lines.append("        except Exception as e:")
        lines.append("            return BitbucketResponse(")
        lines.append("                success=False,")
        lines.append("                error=str(e),")
        lines.append(f'                message=f"Failed to {method_name}: {{str(e)}}"')
        lines.append("            )")
        lines.append("")  # Add blank line after method
        lines.append("")  # Add second line for proper spacing

        return "\n".join(lines)

    def generate_datasource_header(self) -> str:
        """Generate the datasource file header."""
        header = '''# ruff: noqa
"""
Bitbucket Cloud API 2.0 DataSource
===================================

Generated comprehensive datasource for Bitbucket Cloud REST API.
Covers all major API groups with proper typing and error handling.

Total endpoints: {total_endpoints}

API Documentation: https://developer.atlassian.com/cloud/bitbucket/rest/
"""

from typing import Dict, List, Optional, Any
from app.sources.client.bitbucket.bitbucket import (
    BitbucketClient,
    BitbucketRESTClientViaToken,
    BitbucketRESTClientViaOAuth,
    BitbucketResponse,
)


class BitbucketDataSource:
    """Comprehensive Bitbucket Cloud API DataSource.

    Provides async wrapper methods for all Bitbucket Cloud REST API operations.
    All methods return standardized BitbucketResponse objects.

    Usage:
        ```python
        from app.sources.client.bitbucket.bitbucket import BitbucketClient, BitbucketTokenConfig
        from app.sources.external.bitbucket.bitbucket_data_source import BitbucketDataSource

        # Create client
        config = BitbucketTokenConfig(token="your_token")
        client = BitbucketClient.build_with_config(config)

        # Create datasource
        datasource = BitbucketDataSource(client)

        # Use it
        response = await datasource.list_workspaces()
        if response.success:
            print(response.data)
        ```

    API Groups Covered:
    - Workspaces (members, projects, webhooks, permissions)
    - Repositories (CRUD, forks, watchers, webhooks)
    - Commits (history, statuses, approvals, comments)
    - Pull Requests (CRUD, approvals, comments, merge, decline)
    - Branches & Tags (CRUD, restrictions)
    - Pipelines (run, stop, steps, variables, config)
    - Projects (CRUD operations)
    - Issues (CRUD, comments, attachments, votes, watch)
    - Source/Files (browse, read, history)
    - Users (profile, repositories, permissions, emails)
    - Snippets (CRUD, comments, files)
    - Deployments (environments, variables)
    - Downloads (upload, download, delete)
    - Permissions (users, groups, repository access)
    - SSH Keys (CRUD operations)
    - Reports (code quality, test results, annotations)
    - Default Reviewers (CRUD operations)

    Generated methods: {total_endpoints}
    """

    def __init__(self, bitbucket_client: BitbucketClient) -> None:
        """Initialize Bitbucket DataSource.

        Args:
            bitbucket_client: BitbucketClient instance (supports both Token and OAuth)
        """
        self.client = bitbucket_client.get_client()
        self._bitbucket_client = bitbucket_client

    def get_client(self) -> BitbucketClient:
        """Get the underlying BitbucketClient."""
        return self._bitbucket_client

'''
        return header.format(total_endpoints=len(self.endpoints))

    def generate_datasource(self) -> str:
        """Generate the complete datasource class."""
        code_parts = [self.generate_datasource_header()]

        for endpoint in self.endpoints:
            code_parts.append(self.generate_method(endpoint))

        return "".join(code_parts)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Save generated datasource to file."""
        if filename is None:
            filename = "bitbucket_data_source.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        bitbucket_dir = script_dir / "bitbucket"
        bitbucket_dir.mkdir(exist_ok=True)

        full_path = bitbucket_dir / filename
        class_code = self.generate_datasource()
        full_path.write_text(class_code, encoding="utf-8")

        # Print statistics
        endpoint_groups = {}
        for endpoint in self.endpoints:
            # Group by first part of path after /2.0/
            path_parts = endpoint["path"].split("/")
            group = path_parts[2] if len(path_parts) > 2 else "other"
            endpoint_groups[group] = endpoint_groups.get(group, 0) + 1

        print(f"✅ Generated Bitbucket data source with {len(self.endpoints)} methods")
        print(f"📁 Saved to: {full_path}")
        print(f"\n📊 Summary:")
        print(f"   - Total methods: {len(self.endpoints)}")
        print(f"\n   Methods by API group:")
        for group, count in sorted(endpoint_groups.items()):
            print(f"     • {group}: {count} methods")


def main():
    """Main function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Bitbucket Cloud API DataSource"
    )
    parser.add_argument("--filename", "-f", help="Output filename")
    args = parser.parse_args()

    try:
        generator = BitbucketCodeGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
