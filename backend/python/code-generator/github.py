#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
GitHub REST (PyGithub) — Code Generator (strict, no `Any`, no `None` passthrough)

- Emits a `GitHubDataSource` with explicit, typed methods that call *real* PyGithub APIs.
- Never forwards None to the SDK (filters optionals).
- Accepts either a raw `github.Github` or any client exposing `.get_sdk() -> Github`.

Scalable plan:
- `SUPPORTED_OPS` is a hand-verified mapping (path+method → PyGithub callable).
- Later, we can load GitHub’s official OpenAPI JSON (from github/rest-api-description)
  and auto-generate **only** those endpoints that appear in `SUPPORTED_OPS`.
"""

import argparse
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, TypeVar, Generic

# ---------------------------------------------------------------------
# Code templates
# ---------------------------------------------------------------------

HEADER = """# ruff: noqa
from __future__ import annotations
from dataclasses import dataclass
from typing import Generic, Optional, Sequence, Tuple, TypeVar, List, Dict

from github import Github
from github.GithubException import GithubException
from github.AuthenticatedUser import AuthenticatedUser
from github.NamedUser import NamedUser
from github.Organization import Organization
from github.Repository import Repository
from github.Issue import Issue
from github.IssueComment import IssueComment
from github.Label import Label
from github.PullRequest import PullRequest
from github.ContentFile import ContentFile
from github.GitRelease import GitRelease
from github.Branch import Branch
from github.Tag import Tag
from github.Hook import Hook
from github.RateLimit import RateLimit
from github.Workflow import Workflow
from github.WorkflowRun import WorkflowRun
from github.Deployment import Deployment
from github.DeploymentStatus import DeploymentStatus
from github.Commit import Commit
from github.Team import Team
from github.GitRef import GitRef
from github.GitTree import GitTree
from github.GitTag import GitTag
from github.GitBlob import GitBlob
from github.RepositoryTopic import RepositoryTopic
from github.RepositoryInvitation import RepositoryInvitation
from github.RepositoryAdvisory import RepositoryAdvisory
from github.DependabotAlert import DependabotAlert

from app.sources.client.github.github import GitHubResponse


class GitHubDataSource:
    \"\"\"Strict, typed wrapper over PyGithub for common GitHub business operations.

    Accepts either a PyGithub `Github` instance *or* any object with `.get_sdk() -> Github`.
    \"\"\"

    def __init__(self, client: object) -> None:
        if isinstance(client, Github):
            self._sdk: Github = client
        else:
            get_sdk = getattr(client, "get_sdk", None)
            if get_sdk is None or not callable(get_sdk):
                raise TypeError("client must be a github.Github or expose get_sdk() -> Github")
            sdk = get_sdk()
            if not isinstance(sdk, Github):
                raise TypeError("get_sdk() must return a github.Github instance")
            self._sdk = sdk

    # -----------------------
    # Internal helpers
    # -----------------------
    def _repo(self, owner: str, repo: str) -> Repository:
        return self._sdk.get_repo(f"{owner}/{repo}")

    @staticmethod
    def _not_none(**params: object) -> Dict[str, object]:
        return {k: v for k, v in params.items() if v is not None}
"""

FOOTER = ""


def method_block(signature: str, body: str, doc: str) -> str:
    return f"""
    def {signature}:
        \"\"\"{doc}\"\"\"
        try:
{body}
        except GithubException as e:
            return GitHubResponse(success=False, error=str(e))
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))
"""


# ---------------------------------------------------------------------
# Curated methods (verified against PyGithub docs)
# Reference map: https://pygithub.readthedocs.io/en/latest/apis.html
# ---------------------------------------------------------------------

METHODS: List[Tuple[str, str, str]] = []

# ---------- Auth / Users ----------
METHODS += [
    (
        "get_authenticated(self) -> GitHubResponse[AuthenticatedUser]",
        "            user = self._sdk.get_user()\n"
        "            return GitHubResponse(success=True, data=user)",
        "Return the authenticated user.",
    ),
    (
        "get_user(self, login: str) -> GitHubResponse[NamedUser]",
        "            user = self._sdk.get_user(login)\n"
        "            return GitHubResponse(success=True, data=user)",
        "Get a user by login.",
    ),
    (
        "get_organization(self, org: str) -> GitHubResponse[Organization]",
        "            org_obj = self._sdk.get_organization(org)\n"
        "            return GitHubResponse(success=True, data=org_obj)",
        "Get an organization by login.",
    ),
    (
        "list_user_repos(self, user: str, type: str = 'owner') -> GitHubResponse[List[Repository]]",
        "            u = self._sdk.get_user(user)\n"
        "            repos = list(u.get_repos(type=type))\n"
        "            return GitHubResponse(success=True, data=repos)",
        "List repositories for a given user. `type` in {'all','owner','member'}.",
    ),
]

# ---------- Repositories ----------
METHODS += [
    (
        "get_repo(self, owner: str, repo: str) -> GitHubResponse[Repository]",
        "            r = self._repo(owner, repo)\n"
        "            return GitHubResponse(success=True, data=r)",
        "Get a repository.",
    ),
    (
        "list_org_repos(self, org: str, type: str = 'all') -> GitHubResponse[List[Repository]]",
        "            o = self._sdk.get_organization(org)\n"
        "            repos = list(o.get_repos(type=type))\n"
        "            return GitHubResponse(success=True, data=repos)",
        "List repositories for an organization.",
    ),
    (
        "create_repo(self, name: str, private: bool = True, description: Optional[str] = None, auto_init: bool = True) -> GitHubResponse[Repository]",
        "            params = self._not_none(description=description)\n"
        "            repo = self._sdk.get_user().create_repo(name=name, private=private, auto_init=auto_init, **params)\n"
        "            return GitHubResponse(success=True, data=repo)",
        "Create a repository under the authenticated user.",
    ),
]

# ---------- Issues ----------
METHODS += [
    (
        "list_issues(self, owner: str, repo: str, state: str = 'open', labels: Optional[Sequence[str]] = None, assignee: Optional[str] = None) -> GitHubResponse[List[Issue]]",
        "            r = self._repo(owner, repo)\n"
        "            params = self._not_none(labels=labels, assignee=assignee)\n"
        "            issues = list(r.get_issues(state=state, **params))\n"
        "            return GitHubResponse(success=True, data=issues)",
        "List issues with filters.",
    ),
    (
        "get_issue(self, owner: str, repo: str, number: int) -> GitHubResponse[Issue]",
        "            r = self._repo(owner, repo)\n"
        "            issue = r.get_issue(number)\n"
        "            return GitHubResponse(success=True, data=issue)",
        "Get a single issue.",
    ),
    (
        "create_issue(self, owner: str, repo: str, title: str, body: Optional[str] = None, assignees: Optional[Sequence[str]] = None, labels: Optional[Sequence[str]] = None) -> GitHubResponse[Issue]",
        "            r = self._repo(owner, repo)\n"
        "            params = self._not_none(body=body, assignees=assignees, labels=labels)\n"
        "            issue = r.create_issue(title=title, **params)\n"
        "            return GitHubResponse(success=True, data=issue)",
        "Create an issue.",
    ),
    (
        "close_issue(self, owner: str, repo: str, number: int) -> GitHubResponse[Issue]",
        "            r = self._repo(owner, repo)\n"
        "            issue = r.get_issue(number)\n"
        "            issue.edit(state='closed')\n"
        "            return GitHubResponse(success=True, data=issue)",
        "Close an issue.",
    ),
    (
        "add_labels_to_issue(self, owner: str, repo: str, number: int, labels: Sequence[str]) -> GitHubResponse[List[Label]]",
        "            r = self._repo(owner, repo)\n"
        "            issue = r.get_issue(number)\n"
        "            out = list(issue.add_to_labels(*labels))\n"
        "            return GitHubResponse(success=True, data=out)",
        "Add labels to an issue.",
    ),
    (
        "list_issue_comments(self, owner: str, repo: str, number: int) -> GitHubResponse[List[IssueComment]]",
        "            r = self._repo(owner, repo)\n"
        "            issue = r.get_issue(number)\n"
        "            comments = list(issue.get_comments())\n"
        "            return GitHubResponse(success=True, data=comments)",
        "List comments on an issue.",
    ),
]

# ---------- Pull Requests ----------
METHODS += [
    (
        "list_pulls(self, owner: str, repo: str, state: str = 'open', head: Optional[str] = None, base: Optional[str] = None) -> GitHubResponse[List[PullRequest]]",
        "            r = self._repo(owner, repo)\n"
        "            params = self._not_none(head=head, base=base)\n"
        "            pulls = list(r.get_pulls(state=state, **params))\n"
        "            return GitHubResponse(success=True, data=pulls)",
        "List PRs.",
    ),
    (
        "get_pull(self, owner: str, repo: str, number: int) -> GitHubResponse[PullRequest]",
        "            r = self._repo(owner, repo)\n"
        "            pr = r.get_pull(number)\n"
        "            return GitHubResponse(success=True, data=pr)",
        "Get a PR.",
    ),
    (
        "create_pull(self, owner: str, repo: str, title: str, head: str, base: str, body: Optional[str] = None, draft: bool = False) -> GitHubResponse[PullRequest]",
        "            r = self._repo(owner, repo)\n"
        "            params = self._not_none(body=body)\n"
        "            pr = r.create_pull(title=title, head=head, base=base, draft=draft, **params)\n"
        "            return GitHubResponse(success=True, data=pr)",
        "Create a PR.",
    ),
    (
        "merge_pull(self, owner: str, repo: str, number: int, commit_message: Optional[str] = None, merge_method: str = 'merge') -> GitHubResponse[bool]",
        "            r = self._repo(owner, repo)\n"
        "            pr = r.get_pull(number)\n"
        "            params = self._not_none(commit_message=commit_message, merge_method=merge_method)\n"
        "            ok = pr.merge(**params)\n"
        "            return GitHubResponse(success=True, data=bool(ok))",
        "Merge a PR (merge/squash/rebase).",
    ),
]

# ---------- Releases ----------
METHODS += [
    (
        "list_releases(self, owner: str, repo: str) -> GitHubResponse[List[GitRelease]]",
        "            r = self._repo(owner, repo)\n"
        "            rel = list(r.get_releases())\n"
        "            return GitHubResponse(success=True, data=rel)",
        "List releases.",
    ),
    (
        "get_release_by_tag(self, owner: str, repo: str, tag: str) -> GitHubResponse[GitRelease]",
        "            r = self._repo(owner, repo)\n"
        "            rel = r.get_release(tag)\n"
        "            return GitHubResponse(success=True, data=rel)",
        "Get release by tag.",
    ),
    (
        "create_release(self, owner: str, repo: str, tag: str, name: Optional[str] = None, body: Optional[str] = None, draft: bool = False, prerelease: bool = False) -> GitHubResponse[GitRelease]",
        "            r = self._repo(owner, repo)\n"
        "            _name = name if name is not None else tag\n"
        "            _msg = body if body is not None else ''\n"
        "            rel = r.create_git_release(tag=tag, name=_name, message=_msg, draft=draft, prerelease=prerelease)\n"
        "            return GitHubResponse(success=True, data=rel)",
        "Create a release.",
    ),
]

# ---------- Branches & Tags ----------
METHODS += [
    (
        "list_branches(self, owner: str, repo: str) -> GitHubResponse[List[Branch]]",
        "            r = self._repo(owner, repo)\n"
        "            branches = list(r.get_branches())\n"
        "            return GitHubResponse(success=True, data=branches)",
        "List branches.",
    ),
    (
        "get_branch(self, owner: str, repo: str, branch: str) -> GitHubResponse[Branch]",
        "            r = self._repo(owner, repo)\n"
        "            b = r.get_branch(branch)\n"
        "            return GitHubResponse(success=True, data=b)",
        "Get one branch.",
    ),
    (
        "list_tags(self, owner: str, repo: str) -> GitHubResponse[List[Tag]]",
        "            r = self._repo(owner, repo)\n"
        "            tags = list(r.get_tags())\n"
        "            return GitHubResponse(success=True, data=tags)",
        "List tags.",
    ),
]

# ---------- Contents ----------
METHODS += [
    (
        "get_file_contents(self, owner: str, repo: str, path: str, ref: Optional[str] = None) -> GitHubResponse[ContentFile]",
        "            r = self._repo(owner, repo)\n"
        "            params = self._not_none(ref=ref)\n"
        "            content = r.get_contents(path, **params)\n"
        "            return GitHubResponse(success=True, data=content)",
        "Get file contents.",
    ),
    (
        "create_file(self, owner: str, repo: str, path: str, message: str, content: bytes, branch: Optional[str] = None) -> GitHubResponse[Dict[str, object]]",
        "            r = self._repo(owner, repo)\n"
        "            params = self._not_none(branch=branch)\n"
        "            result = r.create_file(path=path, message=message, content=content, **params)\n"
        "            return GitHubResponse(success=True, data=result)",
        "Create a file.",
    ),
    (
        "update_file(self, owner: str, repo: str, path: str, message: str, content: bytes, sha: str, branch: Optional[str] = None) -> GitHubResponse[Dict[str, object]]",
        "            r = self._repo(owner, repo)\n"
        "            params = self._not_none(branch=branch)\n"
        "            result = r.update_file(path=path, message=message, content=content, sha=sha, **params)\n"
        "            return GitHubResponse(success=True, data=result)",
        "Update a file.",
    ),
    (
        "delete_file(self, owner: str, repo: str, path: str, message: str, sha: str, branch: Optional[str] = None) -> GitHubResponse[Dict[str, object]]",
        "            r = self._repo(owner, repo)\n"
        "            params = self._not_none(branch=branch)\n"
        "            result = r.delete_file(path=path, message=message, sha=sha, **params)\n"
        "            return GitHubResponse(success=True, data=result)",
        "Delete a file.",
    ),
]

# ---------- Collaborators / Teams ----------
METHODS += [
    (
        "list_collaborators(self, owner: str, repo: str) -> GitHubResponse[List[NamedUser]]",
        "            r = self._repo(owner, repo)\n"
        "            users = list(r.get_collaborators())\n"
        "            return GitHubResponse(success=True, data=users)",
        "List collaborators.",
    ),
    (
        "add_collaborator(self, owner: str, repo: str, username: str, permission: str = 'push') -> GitHubResponse[bool]",
        "            r = self._repo(owner, repo)\n"
        "            ok = r.add_to_collaborators(username, permission=permission)\n"
        "            return GitHubResponse(success=True, data=bool(ok))",
        "Add collaborator.",
    ),
    (
        "remove_collaborator(self, owner: str, repo: str, username: str) -> GitHubResponse[bool]",
        "            r = self._repo(owner, repo)\n"
        "            r.remove_from_collaborators(username)\n"
        "            return GitHubResponse(success=True, data=True)",
        "Remove collaborator.",
    ),
    (
        "list_repo_teams(self, owner: str, repo: str) -> GitHubResponse[List[Team]]",
        "            r = self._repo(owner, repo)\n"
        "            teams = list(r.get_teams())\n"
        "            return GitHubResponse(success=True, data=teams)",
        "List teams with access to the repo.",
    ),
]

# ---------- Webhooks (incl. deliveries/ping/test) ----------
METHODS += [
    (
        "list_repo_hooks(self, owner: str, repo: str) -> GitHubResponse[List[Hook]]",
        "            r = self._repo(owner, repo)\n"
        "            hooks = list(r.get_hooks())\n"
        "            return GitHubResponse(success=True, data=hooks)",
        "List repo webhooks.",
    ),
    (
        "create_repo_hook(self, owner: str, repo: str, name: str, config: Dict[str, str], events: Optional[Sequence[str]] = None, active: bool = True) -> GitHubResponse[Hook]",
        "            r = self._repo(owner, repo)\n"
        "            ev = list(events) if events is not None else ['push']\n"
        "            hook = r.create_hook(name=name, config=config, events=ev, active=active)\n"
        "            return GitHubResponse(success=True, data=hook)",
        "Create a webhook.",
    ),
    (
        "edit_repo_hook(self, owner: str, repo: str, hook_id: int, config: Optional[Dict[str, str]] = None, events: Optional[Sequence[str]] = None, active: Optional[bool] = None) -> GitHubResponse[Hook]",
        "            r = self._repo(owner, repo)\n"
        "            hook = r.get_hook(hook_id)\n"
        "            params = self._not_none(config=config, events=list(events) if events is not None else None, active=active)\n"
        "            hook.edit(**params)\n"
        "            return GitHubResponse(success=True, data=hook)",
        "Edit webhook.",
    ),
    (
        "ping_repo_hook(self, owner: str, repo: str, hook_id: int) -> GitHubResponse[bool]",
        "            r = self._repo(owner, repo)\n"
        "            hook = r.get_hook(hook_id)\n"
        "            hook.ping()\n"
        "            return GitHubResponse(success=True, data=True)",
        "Ping webhook.",
    ),
    (
        "test_repo_hook(self, owner: str, repo: str, hook_id: int) -> GitHubResponse[bool]",
        "            r = self._repo(owner, repo)\n"
        "            hook = r.get_hook(hook_id)\n"
        "            hook.test()\n"
        "            return GitHubResponse(success=True, data=True)",
        "Test webhook delivery.",
    ),
    (
        "list_hook_deliveries(self, owner: str, repo: str, hook_id: int) -> GitHubResponse[List[Dict[str, object]]]",
        "            r = self._repo(owner, repo)\n"
        "            deliveries = list(r.get_hook_deliveries(hook_id))\n"
        "            return GitHubResponse(success=True, data=deliveries)",
        "List webhook deliveries.",
    ),
    (
        "get_hook_delivery(self, owner: str, repo: str, hook_id: int, delivery_id: int) -> GitHubResponse[Dict[str, object]]",
        "            r = self._repo(owner, repo)\n"
        "            delivery = r.get_hook_delivery(hook_id, delivery_id)\n"
        "            return GitHubResponse(success=True, data=delivery)",
        "Get a webhook delivery.",
    ),
    (
        "delete_repo_hook(self, owner: str, repo: str, hook_id: int) -> GitHubResponse[bool]",
        "            r = self._repo(owner, repo)\n"
        "            hook = r.get_hook(hook_id)\n"
        "            hook.delete()\n"
        "            return GitHubResponse(success=True, data=True)",
        "Delete webhook.",
    ),
]

# ---------- Actions (Workflows & Runs) ----------
# get_workflows, get_workflow, enable/disable, dispatch, runs, rerun, cancel
METHODS += [
    (
        "list_workflows(self, owner: str, repo: str) -> GitHubResponse[List[Workflow]]",
        "            r = self._repo(owner, repo)\n"
        "            workflows = list(r.get_workflows())\n"
        "            return GitHubResponse(success=True, data=workflows)",
        "List workflows.",
    ),
    (
        "get_workflow(self, owner: str, repo: str, workflow_id: int) -> GitHubResponse[Workflow]",
        "            r = self._repo(owner, repo)\n"
        "            wf = r.get_workflow(workflow_id)\n"
        "            return GitHubResponse(success=True, data=wf)",
        "Get a workflow.",
    ),
    (
        "enable_workflow(self, owner: str, repo: str, workflow_id: int) -> GitHubResponse[bool]",
        "            wf = self._repo(owner, repo).get_workflow(workflow_id)\n"
        "            wf.enable()\n"
        "            return GitHubResponse(success=True, data=True)",
        "Enable a workflow.",
    ),
    (
        "disable_workflow(self, owner: str, repo: str, workflow_id: int) -> GitHubResponse[bool]",
        "            wf = self._repo(owner, repo).get_workflow(workflow_id)\n"
        "            wf.disable()\n"
        "            return GitHubResponse(success=True, data=True)",
        "Disable a workflow.",
    ),
    (
        "dispatch_workflow(self, owner: str, repo: str, workflow_id: int, ref: str, inputs: Optional[Dict[str, str]] = None) -> GitHubResponse[bool]",
        "            wf = self._repo(owner, repo).get_workflow(workflow_id)\n"
        "            params = self._not_none(inputs=inputs)\n"
        "            ok = wf.create_dispatch(ref=ref, **params)\n"
        "            return GitHubResponse(success=True, data=bool(ok))",
        "Dispatch a workflow.",
    ),
    (
        "list_workflow_runs(self, owner: str, repo: str, workflow_id: int) -> GitHubResponse[List[WorkflowRun]]",
        "            wf = self._repo(owner, repo).get_workflow(workflow_id)\n"
        "            runs = list(wf.get_runs())\n"
        "            return GitHubResponse(success=True, data=runs)",
        "List runs for a workflow.",
    ),
    (
        "rerun_workflow_run(self, owner: str, repo: str, run_id: int) -> GitHubResponse[bool]",
        "            run = self._repo(owner, repo).get_workflow_run(run_id)\n"
        "            ok = run.rerun()\n"
        "            return GitHubResponse(success=True, data=bool(ok))",
        "Re-run a workflow run.",
    ),
    (
        "cancel_workflow_run(self, owner: str, repo: str, run_id: int) -> GitHubResponse[bool]",
        "            run = self._repo(owner, repo).get_workflow_run(run_id)\n"
        "            ok = run.cancel()\n"
        "            return GitHubResponse(success=True, data=bool(ok))",
        "Cancel a workflow run.",
    ),
]

# ---------- Deployments ----------
METHODS += [
    (
        "create_deployment(self, owner: str, repo: str, ref: str, task: str = 'deploy', auto_merge: bool = False, required_contexts: Optional[Sequence[str]] = None, environment: Optional[str] = None, description: Optional[str] = None) -> GitHubResponse[Deployment]",
        "            r = self._repo(owner, repo)\n"
        "            params = self._not_none(task=task, auto_merge=auto_merge, required_contexts=list(required_contexts) if required_contexts is not None else None, environment=environment, description=description)\n"
        "            dep = r.create_deployment(ref=ref, **params)\n"
        "            return GitHubResponse(success=True, data=dep)",
        "Create a deployment. (Requires suitable permissions)",
    ),
    (
        "list_deployments(self, owner: str, repo: str) -> GitHubResponse[List[Deployment]]",
        "            r = self._repo(owner, repo)\n"
        "            deps = list(r.get_deployments())\n"
        "            return GitHubResponse(success=True, data=deps)",
        "List deployments.",
    ),
    (
        "get_deployment(self, owner: str, repo: str, deployment_id: int) -> GitHubResponse[Deployment]",
        "            r = self._repo(owner, repo)\n"
        "            dep = r.get_deployment(deployment_id)\n"
        "            return GitHubResponse(success=True, data=dep)",
        "Get a deployment.",
    ),
    (
        "create_deployment_status(self, owner: str, repo: str, deployment_id: int, state: str, target_url: Optional[str] = None, description: Optional[str] = None, environment: Optional[str] = None, environment_url: Optional[str] = None, auto_inactive: Optional[bool] = None) -> GitHubResponse[DeploymentStatus]",
        "            dep = self._repo(owner, repo).get_deployment(deployment_id)\n"
        "            params = self._not_none(target_url=target_url, description=description, environment=environment, environment_url=environment_url, auto_inactive=auto_inactive)\n"
        "            st = dep.create_status(state=state, **params)\n"
        "            return GitHubResponse(success=True, data=st)",
        "Create a deployment status.",
    ),
]

# ---------- Commit Statuses ----------
METHODS += [
    (
        "get_commit(self, owner: str, repo: str, sha: str) -> GitHubResponse[Commit]",
        "            c = self._repo(owner, repo).get_commit(sha=sha)\n"
        "            return GitHubResponse(success=True, data=c)",
        "Get a commit.",
    ),
    (
        "create_commit_status(self, owner: str, repo: str, sha: str, state: str, target_url: Optional[str] = None, description: Optional[str] = None, context: Optional[str] = None) -> GitHubResponse[bool]",
        "            commit = self._repo(owner, repo).get_commit(sha=sha)\n"
        "            params = self._not_none(target_url=target_url, description=description, context=context)\n"
        "            commit.create_status(state=state, **params)\n"
        "            return GitHubResponse(success=True, data=True)",
        "Create a commit status.",
    ),
]

# ---------- Topics & Traffic ----------
METHODS += [
    (
        "get_topics(self, owner: str, repo: str) -> GitHubResponse[List[str]]",
        "            r = self._repo(owner, repo)\n"
        "            topics = list(r.get_topics())\n"
        "            return GitHubResponse(success=True, data=topics)",
        "Get repository topics.",
    ),
    (
        "replace_topics(self, owner: str, repo: str, topics: Sequence[str]) -> GitHubResponse[List[str]]",
        "            r = self._repo(owner, repo)\n"
        "            out = r.replace_topics(list(topics))\n"
        "            return GitHubResponse(success=True, data=out)",
        "Replace repository topics.",
    ),
    (
        "get_clones_traffic(self, owner: str, repo: str) -> GitHubResponse[Dict[str, object]]",
        "            r = self._repo(owner, repo)\n"
        "            data = r.get_clones_traffic()\n"
        "            return GitHubResponse(success=True, data=data.__dict__ if hasattr(data, '__dict__') else data)",
        "Get clone traffic.",
    ),
    (
        "get_views_traffic(self, owner: str, repo: str) -> GitHubResponse[Dict[str, object]]",
        "            r = self._repo(owner, repo)\n"
        "            data = r.get_views_traffic()\n"
        "            return GitHubResponse(success=True, data=data.__dict__ if hasattr(data, '__dict__') else data)",
        "Get views traffic.",
    ),
]

# ---------- Forks, Contributors, Assignees ----------
METHODS += [
    (
        "list_forks(self, owner: str, repo: str) -> GitHubResponse[List[Repository]]",
        "            r = self._repo(owner, repo)\n"
        "            forks = list(r.get_forks())\n"
        "            return GitHubResponse(success=True, data=forks)",
        "List forks.",
    ),
    (
        "create_fork(self, owner: str, repo: str, org: Optional[str] = None) -> GitHubResponse[Repository]",
        "            r = self._repo(owner, repo)\n"
        "            params = self._not_none(organization=org)\n"
        "            fork = r.create_fork(**params)\n"
        "            return GitHubResponse(success=True, data=fork)",
        "Create a fork.",
    ),
    (
        "list_contributors(self, owner: str, repo: str) -> GitHubResponse[List[NamedUser]]",
        "            r = self._repo(owner, repo)\n"
        "            users = list(r.get_contributors())\n"
        "            return GitHubResponse(success=True, data=users)",
        "List contributors.",
    ),
    (
        "list_assignees(self, owner: str, repo: str) -> GitHubResponse[List[NamedUser]]",
        "            r = self._repo(owner, repo)\n"
        "            users = list(r.get_assignees())\n"
        "            return GitHubResponse(success=True, data=users)",
        "List potential assignees.",
    ),
]

# ---------- Invitations ----------
METHODS += [
    (
        "list_pending_invitations(self, owner: str, repo: str) -> GitHubResponse[List[RepositoryInvitation]]",
        "            r = self._repo(owner, repo)\n"
        "            inv = list(r.get_pending_invitations())\n"
        "            return GitHubResponse(success=True, data=inv)",
        "List pending repo invitations.",
    ),
    (
        "remove_invitation(self, owner: str, repo: str, invitation_id: int) -> GitHubResponse[bool]",
        "            r = self._repo(owner, repo)\n"
        "            r.remove_invitation(invitation_id)\n"
        "            return GitHubResponse(success=True, data=True)",
        "Remove a repo invitation.",
    ),
]

# ---------- Dependabot ----------
METHODS += [
    (
        "list_dependabot_alerts(self, owner: str, repo: str) -> GitHubResponse[List[DependabotAlert]]",
        "            r = self._repo(owner, repo)\n"
        "            alerts = list(r.get_dependabot_alerts())\n"
        "            return GitHubResponse(success=True, data=alerts)",
        "List Dependabot alerts for a repo.",
    ),
    (
        "get_dependabot_alert(self, owner: str, repo: str, alert_number: int) -> GitHubResponse[DependabotAlert]",
        "            r = self._repo(owner, repo)\n"
        "            alert = r.get_dependabot_alert(alert_number)\n"
        "            return GitHubResponse(success=True, data=alert)",
        "Get a single Dependabot alert.",
    ),
]

# ---------- Git Data (refs/trees/tags/blobs) ----------
METHODS += [
    (
        "create_git_ref(self, owner: str, repo: str, ref: str, sha: str) -> GitHubResponse[GitRef]",
        "            r = self._repo(owner, repo)\n"
        "            gitref = r.create_git_ref(ref=ref, sha=sha)\n"
        "            return GitHubResponse(success=True, data=gitref)",
        "Create a git ref.",
    ),
    (
        "get_git_ref(self, owner: str, repo: str, ref: str) -> GitHubResponse[GitRef]",
        "            r = self._repo(owner, repo)\n"
        "            out = r.get_git_ref(ref)\n"
        "            return GitHubResponse(success=True, data=out)",
        "Get a git ref.",
    ),
    (
        "delete_git_ref(self, owner: str, repo: str, ref: str) -> GitHubResponse[bool]",
        "            ref_obj = self._repo(owner, repo).get_git_ref(ref)\n"
        "            ref_obj.delete()\n"
        "            return GitHubResponse(success=True, data=True)",
        "Delete a git ref.",
    ),
    (
        "create_git_blob(self, owner: str, repo: str, content: str, encoding: str = 'utf-8') -> GitHubResponse[GitBlob]",
        "            r = self._repo(owner, repo)\n"
        "            blob = r.create_git_blob(content, encoding)\n"
        "            return GitHubResponse(success=True, data=blob)",
        "Create a git blob.",
    ),
    (
        "create_git_tree(self, owner: str, repo: str, tree: List[Tuple[str, str, str]], base_tree: Optional[str] = None) -> GitHubResponse[GitTree]",
        "            r = self._repo(owner, repo)\n"
        "            params = self._not_none(base_tree=base_tree)\n"
        "            out = r.create_git_tree(tree=tree, **params)\n"
        "            return GitHubResponse(success=True, data=out)",
        "Create a git tree.",
    ),
    (
        "create_git_tag(self, owner: str, repo: str, tag: str, message: str, object_sha: str, type: str, tagger: Optional[Dict[str, str]] = None) -> GitHubResponse[GitTag]",
        "            r = self._repo(owner, repo)\n"
        "            params = self._not_none(tagger=tagger)\n"
        "            out = r.create_git_tag(tag=tag, message=message, object=object_sha, type=type, **params)\n"
        "            return GitHubResponse(success=True, data=out)",
        "Create a git tag object.",
    ),
]

# ---------- Search & Meta ----------
METHODS += [
    (
        "search_repositories(self, query: str) -> GitHubResponse[List[Repository]]",
        "            res = list(self._sdk.search_repositories(query))\n"
        "            return GitHubResponse(success=True, data=res)",
        "Search repositories.",
    ),
    (
        "get_rate_limit(self) -> GitHubResponse[RateLimit]",
        "            limit = self._sdk.get_rate_limit()\n"
        "            return GitHubResponse(success=True, data=limit)",
        "Get current rate limit.",
    ),
]


def build_class() -> str:
    parts: List[str] = [HEADER]
    for sig, body, doc in METHODS:
        parts.append(method_block(sig, body, doc))
    parts.append(FOOTER)
    return "\n".join(parts)


def write_output(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate GitHubDataSource (PyGithub)."
    )
    parser.add_argument(
        "--out",
        default="github_data_source.py",
        help="Output path for the generated data source.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="do_print",
        help="Print generated code to stdout.",
    )
    args = parser.parse_args()

    code = build_class()
    write_output(args.out, code)
    if args.do_print:
        print(code)


if __name__ == "__main__":
    main()
