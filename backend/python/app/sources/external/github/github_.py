from __future__ import annotations

import datetime
from collections.abc import Sequence
from typing import List

from github import (
    Github,  # type: ignore
)
from github.AuthenticatedUser import AuthenticatedUser  # type: ignore
from github.Branch import Branch  # type: ignore
from github.Commit import Commit  # type: ignore
from github.ContentFile import ContentFile  # type: ignore
from github.Deployment import Deployment  # type: ignore
from github.DeploymentStatus import DeploymentStatus  # type: ignore
from github.File import File  # type: ignore
from github.GitBlob import GitBlob  # type: ignore
from github.GitRef import GitRef  # type: ignore
from github.GitRelease import GitRelease  # type: ignore
from github.GitTag import GitTag  # type: ignore
from github.GitTree import GitTree  # type: ignore
from github.Hook import Hook  # type: ignore
from github.Invitation import Invitation  # type: ignore
from github.Issue import Issue  # type: ignore
from github.IssueComment import IssueComment  # type: ignore
from github.Label import Label  # type: ignore
from github.NamedUser import NamedUser  # type: ignore
from github.Organization import Organization  # type: ignore
from github.PullRequest import PullRequest  # type: ignore
from github.PullRequestComment import PullRequestComment  # type: ignore
from github.RateLimit import RateLimit  # type: ignore
from github.Repository import Repository  # type: ignore
from github.Tag import Tag  # type: ignore
from github.Team import Team  # type: ignore
from github.Workflow import Workflow  # type: ignore
from github.WorkflowRun import WorkflowRun  # type: ignore

from app.sources.client.github.github import GitHubResponse


class GitHubDataSource:
    """Strict, typed wrapper over PyGithub for common GitHub business operations.

    Accepts either a PyGithub `Github` instance *or* any object with `.get_sdk() -> Github`.
    """

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
    def _not_none(**params: object) -> dict[str, object]:
        return {k: v for k, v in params.items() if v is not None}


    def get_authenticated(self) -> GitHubResponse[AuthenticatedUser]:
        """Return the authenticated user."""
        try:
            user = self._sdk.get_user()
            return GitHubResponse(success=True, data=user)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_user(self, login: str) -> GitHubResponse[NamedUser]:
        """Get a user by login."""
        try:
            user = self._sdk.get_user(login)
            return GitHubResponse(success=True, data=user)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_organization(self, org: str) -> GitHubResponse[Organization]:
        """Get an organization by login."""
        try:
            org_obj = self._sdk.get_organization(org)
            return GitHubResponse(success=True, data=org_obj)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_user_repos(self, user: str, type: str = "owner") -> GitHubResponse[list[Repository]]:
        """List repositories for a given user. `type` in {'all','owner','member'}."""
        try:
            u = self._sdk.get_user()
            repos = list(u.get_repos(type=type))
            return GitHubResponse(success=True, data=repos)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_repo(self, owner: str, repo: str) -> GitHubResponse[Repository]:
        """Get a repository."""
        try:
            r = self._repo(owner, repo)
            return GitHubResponse(success=True, data=r)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_org_repos(self, org: str, type: str = "all") -> GitHubResponse[list[Repository]]:
        """List repositories for an organization."""
        try:
            o = self._sdk.get_organization(org)
            repos = list(o.get_repos(type=type))
            return GitHubResponse(success=True, data=repos)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_repo(self, name: str, private: bool = True, description: str | None = None, auto_init: bool = True) -> GitHubResponse[Repository]:
        """Create a repository under the authenticated user."""
        try:
            params = self._not_none(description=description)
            repo = self._sdk.get_user().create_repo(name=name, private=private, auto_init=auto_init, **params)
            return GitHubResponse(success=True, data=repo)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_issues(self, owner: str, repo: str, state: str = "open", labels: Sequence[str] | None = None, assignee: str | None = None,since:str |None = None) -> GitHubResponse[list[Issue]]:
        """List issues with filters."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(labels=labels, assignee=assignee, since=since)
            issues = list(r.get_issues(state=state, **params))
            return GitHubResponse(success=True, data=issues)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_issue(self, owner: str, repo: str, number: int) -> GitHubResponse[Issue]:
        """Get a single issue."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number)
            return GitHubResponse(success=True, data=issue)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_issue(self, owner: str, repo: str, title: str, body: str | None = None, assignees: Sequence[str] | None = None, labels: Sequence[str] | None = None) -> GitHubResponse[Issue]:
        """Create an issue."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(body=body, assignees=assignees, labels=labels)
            issue = r.create_issue(title=title, **params)
            return GitHubResponse(success=True, data=issue)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def close_issue(self, owner: str, repo: str, number: int) -> GitHubResponse[Issue]:
        """Close an issue."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number)
            issue.edit(state="closed")
            return GitHubResponse(success=True, data=issue)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def add_labels_to_issue(self, owner: str, repo: str, number: int, labels: Sequence[str]) -> GitHubResponse[list[Label]]:
        """Add labels to an issue."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number)
            out = list(issue.add_to_labels(*labels))
            return GitHubResponse(success=True, data=out)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_issue_comments(self, owner: str, repo: str, number: int,since:datetime.datetime|None=None) -> GitHubResponse[list[IssueComment]]:
        """List comments on an issue."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number)
            if since is None:
                comments=list(issue.get_comments())
            else:
                comments = list(issue.get_comments(since=since))
            return GitHubResponse(success=True, data=comments)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def get_issue_comment(self, owner: str, repo: str,number:int, comment_id: int) -> GitHubResponse[IssueComment]:
        """Get a single issue comment by ID."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number=number)
            comment = issue.get_comment(id=comment_id)
            return GitHubResponse(success=True, data=comment)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def list_pulls(self, owner: str, repo: str, state: str = "open", head: str | None = None, base: str | None = None) -> GitHubResponse[list[PullRequest]]:
        """List PRs."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(head=head, base=base)
            pulls = list(r.get_pulls(state=state, **params))
            return GitHubResponse(success=True, data=pulls)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_pull(self, owner: str, repo: str, number: int) -> GitHubResponse[PullRequest]:
        """Get a PR."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            return GitHubResponse(success=True, data=pr)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def get_pull_commits(self, owner: str, repo: str, number: int) -> GitHubResponse[list[Commit]]:
        """Get commits of a PR."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            commits = list(pr.get_commits())
            return GitHubResponse(success=True, data=commits)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def get_pull_file_changes(self, owner: str, repo: str, number: int) -> GitHubResponse[list[File]]:
        """Get file changes of a PR."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            files = list(pr.get_files())
            # file_dicts = [file.raw_data for file in files]
            return GitHubResponse(success=True, data=files)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def get_pull_review_comments(self, owner: str, repo: str, number: int) -> GitHubResponse[list[PullRequestComment]]:
        """Get review comments of a PR."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            comments = list(pr.get_review_comments())
            return GitHubResponse(success=True, data=comments)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def create_pull(self, owner: str, repo: str, title: str, head: str, base: str, body: str | None = None, draft: bool = False) -> GitHubResponse[PullRequest]:
        """Create a PR."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(body=body)
            pr = r.create_pull(title=title, head=head, base=base, draft=draft, **params)
            return GitHubResponse(success=True, data=pr)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def merge_pull(self, owner: str, repo: str, number: int, commit_message: str | None = None, merge_method: str = "merge") -> GitHubResponse[bool]:
        """Merge a PR (merge/squash/rebase)."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            params = self._not_none(commit_message=commit_message, merge_method=merge_method)
            ok = pr.merge(**params)
            return GitHubResponse(success=True, data=bool(ok))
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_releases(self, owner: str, repo: str) -> GitHubResponse[list[GitRelease]]:
        """List releases."""
        try:
            r = self._repo(owner, repo)
            rel = list(r.get_releases())
            return GitHubResponse(success=True, data=rel)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_release_by_tag(self, owner: str, repo: str, tag: str) -> GitHubResponse[GitRelease]:
        """Get release by tag."""
        try:
            r = self._repo(owner, repo)
            rel = r.get_release(tag)
            return GitHubResponse(success=True, data=rel)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_release(self, owner: str, repo: str, tag: str, name: str | None = None, body: str | None = None, draft: bool = False, prerelease: bool = False) -> GitHubResponse[GitRelease]:
        """Create a release."""
        try:
            r = self._repo(owner, repo)
            _name = name if name is not None else tag
            _msg = body if body is not None else ""
            rel = r.create_git_release(tag=tag, name=_name, message=_msg, draft=draft, prerelease=prerelease)
            return GitHubResponse(success=True, data=rel)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_branches(self, owner: str, repo: str) -> GitHubResponse[list[Branch]]:
        """List branches."""
        try:
            r = self._repo(owner, repo)
            branches = list(r.get_branches())
            return GitHubResponse(success=True, data=branches)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_branch(self, owner: str, repo: str, branch: str) -> GitHubResponse[Branch]:
        """Get one branch."""
        try:
            r = self._repo(owner, repo)
            b = r.get_branch(branch)
            return GitHubResponse(success=True, data=b)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_tags(self, owner: str, repo: str) -> GitHubResponse[list[Tag]]:
        """List tags."""
        try:
            r = self._repo(owner, repo)
            tags = list(r.get_tags())
            return GitHubResponse(success=True, data=tags)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def get_file_contents(self, owner: str, repo: str, path: str, ref: str | None = None) -> GitHubResponse[ContentFile]:
        """Get file contents."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(ref=ref)
            content = r.get_contents(path, **params)
            return GitHubResponse(success=True, data=content)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def create_file(self, owner: str, repo: str, path: str, message: str, content: bytes, branch: str | None = None) -> GitHubResponse[dict[str, object]]:
        """Create a file."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(branch=branch)
            result = r.create_file(path=path, message=message, content=content, **params)
            return GitHubResponse(success=True, data=result)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def update_file(self, owner: str, repo: str, path: str, message: str, content: bytes, sha: str, branch: str | None = None) -> GitHubResponse[dict[str, object]]:
        """Update a file."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(branch=branch)
            result = r.update_file(path=path, message=message, content=content, sha=sha, **params)
            return GitHubResponse(success=True, data=result)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def delete_file(self, owner: str, repo: str, path: str, message: str, sha: str, branch: str | None = None) -> GitHubResponse[dict[str, object]]:
        """Delete a file."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(branch=branch)
            result = r.delete_file(path=path, message=message, sha=sha, **params)
            return GitHubResponse(success=True, data=result)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_collaborators(self, owner: str, repo: str) -> GitHubResponse[list[NamedUser]]:
        """List collaborators."""
        try:
            r = self._repo(owner, repo)
            users = list(r.get_collaborators())
            return GitHubResponse(success=True, data=users)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def add_collaborator(self, owner: str, repo: str, username: str, permission: str = "push") -> GitHubResponse[bool]:
        """Add collaborator."""
        try:
            r = self._repo(owner, repo)
            ok = r.add_to_collaborators(username, permission=permission)
            return GitHubResponse(success=True, data=bool(ok))
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def remove_collaborator(self, owner: str, repo: str, username: str) -> GitHubResponse[bool]:
        """Remove collaborator."""
        try:
            r = self._repo(owner, repo)
            r.remove_from_collaborators(username)
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_repo_teams(self, owner: str, repo: str) -> GitHubResponse[list[Team]]:
        """List teams with access to the repo."""
        try:
            r = self._repo(owner, repo)
            teams = list(r.get_teams())
            return GitHubResponse(success=True, data=teams)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_repo_hooks(self, owner: str, repo: str) -> GitHubResponse[list[Hook]]:
        """List repo webhooks."""
        try:
            r = self._repo(owner, repo)
            hooks = list(r.get_hooks())
            return GitHubResponse(success=True, data=hooks)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_repo_hook(self, owner: str, repo: str, name: str, config: dict[str, str], events: Sequence[str] | None = None, active: bool = True) -> GitHubResponse[Hook]:
        """Create a webhook."""
        try:
            r = self._repo(owner, repo)
            ev = list(events) if events is not None else ["push"]
            hook = r.create_hook(name=name, config=config, events=ev, active=active)
            return GitHubResponse(success=True, data=hook)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def edit_repo_hook(self, owner: str, repo: str, hook_id: int, config: dict[str, str] | None = None, events: Sequence[str] | None = None, active: bool | None = None) -> GitHubResponse[Hook]:
        """Edit webhook."""
        try:
            r = self._repo(owner, repo)
            hook = r.get_hook(hook_id)
            params = self._not_none(config=config, events=list(events) if events is not None else None, active=active)
            hook.edit(**params)
            return GitHubResponse(success=True, data=hook)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def ping_repo_hook(self, owner: str, repo: str, hook_id: int) -> GitHubResponse[bool]:
        """Ping webhook."""
        try:
            r = self._repo(owner, repo)
            hook = r.get_hook(hook_id)
            hook.ping()
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def test_repo_hook(self, owner: str, repo: str, hook_id: int) -> GitHubResponse[bool]:
        """Test webhook delivery."""
        try:
            r = self._repo(owner, repo)
            hook = r.get_hook(hook_id)
            hook.test()
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_hook_deliveries(self, owner: str, repo: str, hook_id: int) -> GitHubResponse[list[dict[str, object]]]:
        """List webhook deliveries."""
        try:
            r = self._repo(owner, repo)
            deliveries = list(r.get_hook_deliveries(hook_id))
            return GitHubResponse(success=True, data=deliveries)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_hook_delivery(self, owner: str, repo: str, hook_id: int, delivery_id: int) -> GitHubResponse[dict[str, object]]:
        """Get a webhook delivery."""
        try:
            r = self._repo(owner, repo)
            delivery = r.get_hook_delivery(hook_id, delivery_id)
            return GitHubResponse(success=True, data=delivery)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def delete_repo_hook(self, owner: str, repo: str, hook_id: int) -> GitHubResponse[bool]:
        """Delete webhook."""
        try:
            r = self._repo(owner, repo)
            hook = r.get_hook(hook_id)
            hook.delete()
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_workflows(self, owner: str, repo: str) -> GitHubResponse[list[Workflow]]:
        """List workflows."""
        try:
            r = self._repo(owner, repo)
            workflows = list(r.get_workflows())
            return GitHubResponse(success=True, data=workflows)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_workflow(self, owner: str, repo: str, workflow_id: int) -> GitHubResponse[Workflow]:
        """Get a workflow."""
        try:
            r = self._repo(owner, repo)
            wf = r.get_workflow(workflow_id)
            return GitHubResponse(success=True, data=wf)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def enable_workflow(self, owner: str, repo: str, workflow_id: int) -> GitHubResponse[bool]:
        """Enable a workflow."""
        try:
            wf = self._repo(owner, repo).get_workflow(workflow_id)
            wf.enable()
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def disable_workflow(self, owner: str, repo: str, workflow_id: int) -> GitHubResponse[bool]:
        """Disable a workflow."""
        try:
            wf = self._repo(owner, repo).get_workflow(workflow_id)
            wf.disable()
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def dispatch_workflow(self, owner: str, repo: str, workflow_id: int, ref: str, inputs: dict[str, str] | None = None) -> GitHubResponse[bool]:
        """Dispatch a workflow."""
        try:
            wf = self._repo(owner, repo).get_workflow(workflow_id)
            params = self._not_none(inputs=inputs)
            ok = wf.create_dispatch(ref=ref, **params)
            return GitHubResponse(success=True, data=bool(ok))
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_workflow_runs(self, owner: str, repo: str, workflow_id: int) -> GitHubResponse[list[WorkflowRun]]:
        """List runs for a workflow."""
        try:
            wf = self._repo(owner, repo).get_workflow(workflow_id)
            runs = list(wf.get_runs())
            return GitHubResponse(success=True, data=runs)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def rerun_workflow_run(self, owner: str, repo: str, run_id: int) -> GitHubResponse[bool]:
        """Re-run a workflow run."""
        try:
            run = self._repo(owner, repo).get_workflow_run(run_id)
            ok = run.rerun()
            return GitHubResponse(success=True, data=bool(ok))
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def cancel_workflow_run(self, owner: str, repo: str, run_id: int) -> GitHubResponse[bool]:
        """Cancel a workflow run."""
        try:
            run = self._repo(owner, repo).get_workflow_run(run_id)
            ok = run.cancel()
            return GitHubResponse(success=True, data=bool(ok))
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_deployment(self, owner: str, repo: str, ref: str, task: str = "deploy", auto_merge: bool = False, required_contexts: Sequence[str] | None = None, environment: str | None = None, description: str | None = None) -> GitHubResponse[Deployment]:
        """Create a deployment. (Requires suitable permissions)"""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(task=task, auto_merge=auto_merge, required_contexts=list(required_contexts) if required_contexts is not None else None, environment=environment, description=description)
            dep = r.create_deployment(ref=ref, **params)
            return GitHubResponse(success=True, data=dep)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_deployments(self, owner: str, repo: str) -> GitHubResponse[list[Deployment]]:
        """List deployments."""
        try:
            r = self._repo(owner, repo)
            deps = list(r.get_deployments())
            return GitHubResponse(success=True, data=deps)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_deployment(self, owner: str, repo: str, deployment_id: int) -> GitHubResponse[Deployment]:
        """Get a deployment."""
        try:
            r = self._repo(owner, repo)
            dep = r.get_deployment(deployment_id)
            return GitHubResponse(success=True, data=dep)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_deployment_status(self, owner: str, repo: str, deployment_id: int, state: str, target_url: str | None = None, description: str | None = None, environment: str | None = None, environment_url: str | None = None, auto_inactive: bool | None = None) -> GitHubResponse[DeploymentStatus]:
        """Create a deployment status."""
        try:
            dep = self._repo(owner, repo).get_deployment(deployment_id)
            params = self._not_none(target_url=target_url, description=description, environment=environment, environment_url=environment_url, auto_inactive=auto_inactive)
            st = dep.create_status(state=state, **params)
            return GitHubResponse(success=True, data=st)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_commit(self, owner: str, repo: str, sha: str) -> GitHubResponse[Commit]:
        """Get a commit."""
        try:
            c = self._repo(owner, repo).get_commit(sha=sha)
            return GitHubResponse(success=True, data=c)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_commit_status(self, owner: str, repo: str, sha: str, state: str, target_url: str | None = None, description: str | None = None, context: str | None = None) -> GitHubResponse[bool]:
        """Create a commit status."""
        try:
            commit = self._repo(owner, repo).get_commit(sha=sha)
            params = self._not_none(target_url=target_url, description=description, context=context)
            commit.create_status(state=state, **params)
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_topics(self, owner: str, repo: str) -> GitHubResponse[list[str]]:
        """Get repository topics."""
        try:
            r = self._repo(owner, repo)
            topics = list(r.get_topics())
            return GitHubResponse(success=True, data=topics)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def replace_topics(self, owner: str, repo: str, topics: Sequence[str]) -> GitHubResponse[list[str]]:
        """Replace repository topics."""
        try:
            r = self._repo(owner, repo)
            out = r.replace_topics(list(topics))
            return GitHubResponse(success=True, data=out)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_clones_traffic(self, owner: str, repo: str) -> GitHubResponse[dict[str, object]]:
        """Get clone traffic."""
        try:
            r = self._repo(owner, repo)
            data = r.get_clones_traffic()
            return GitHubResponse(success=True, data=data.__dict__ if hasattr(data, "__dict__") else data)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_views_traffic(self, owner: str, repo: str) -> GitHubResponse[dict[str, object]]:
        """Get views traffic."""
        try:
            r = self._repo(owner, repo)
            data = r.get_views_traffic()
            return GitHubResponse(success=True, data=data.__dict__ if hasattr(data, "__dict__") else data)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_forks(self, owner: str, repo: str) -> GitHubResponse[list[Repository]]:
        """List forks."""
        try:
            r = self._repo(owner, repo)
            forks = list(r.get_forks())
            return GitHubResponse(success=True, data=forks)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_fork(self, owner: str, repo: str, org: str | None = None) -> GitHubResponse[Repository]:
        """Create a fork."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(organization=org)
            fork = r.create_fork(**params)
            return GitHubResponse(success=True, data=fork)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_contributors(self, owner: str, repo: str) -> GitHubResponse[list[NamedUser]]:
        """List contributors."""
        try:
            r = self._repo(owner, repo)
            users = list(r.get_contributors())
            return GitHubResponse(success=True, data=users)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_assignees(self, owner: str, repo: str) -> GitHubResponse[list[NamedUser]]:
        """List potential assignees."""
        try:
            r = self._repo(owner, repo)
            users = list(r.get_assignees())
            return GitHubResponse(success=True, data=users)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def list_pending_invitations(self, owner: str, repo: str) -> GitHubResponse[List[Invitation]]:
        """List pending repo invitations."""
        try:
            r = self._repo(owner, repo)
            inv = list(r.get_pending_invitations())
            return GitHubResponse(success=True, data=inv)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def remove_invitation(self, owner: str, repo: str, invitation_id: int) -> GitHubResponse[bool]:
        """Remove a repo invitation."""
        try:
            r = self._repo(owner, repo)
            r.remove_invitation(invitation_id)
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    # DependabotAlert not available in older PyGithub versions
    def list_dependabot_alerts(self, owner: str, repo: str) -> GitHubResponse[List[object]]:
        """List Dependabot alerts for a repo."""
        try:
            r = self._repo(owner, repo)
            alerts = list(r.get_dependabot_alerts())
            return GitHubResponse(success=True, data=alerts)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    # DependabotAlert not available in older PyGithub versions
    def get_dependabot_alert(self, owner: str, repo: str, alert_number: int) -> GitHubResponse[object]:
        """Get a single Dependabot alert."""
        try:
            r = self._repo(owner, repo)
            alert = r.get_dependabot_alert(alert_number)
            return GitHubResponse(success=True, data=alert)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_git_ref(self, owner: str, repo: str, ref: str, sha: str) -> GitHubResponse[GitRef]:
        """Create a git ref."""
        try:
            r = self._repo(owner, repo)
            gitref = r.create_git_ref(ref=ref, sha=sha)
            return GitHubResponse(success=True, data=gitref)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_git_ref(self, owner: str, repo: str, ref: str) -> GitHubResponse[GitRef]:
        """Get a git ref."""
        try:
            r = self._repo(owner, repo)
            out = r.get_git_ref(ref)
            return GitHubResponse(success=True, data=out)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def delete_git_ref(self, owner: str, repo: str, ref: str) -> GitHubResponse[bool]:
        """Delete a git ref."""
        try:
            ref_obj = self._repo(owner, repo).get_git_ref(ref)
            ref_obj.delete()
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_git_blob(self, owner: str, repo: str, content: str, encoding: str = "utf-8") -> GitHubResponse[GitBlob]:
        """Create a git blob."""
        try:
            r = self._repo(owner, repo)
            blob = r.create_git_blob(content, encoding)
            return GitHubResponse(success=True, data=blob)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_git_tree(self, owner: str, repo: str, tree: list[tuple[str, str, str]], base_tree: str | None = None) -> GitHubResponse[GitTree]:
        """Create a git tree."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(base_tree=base_tree)
            out = r.create_git_tree(tree=tree, **params)
            return GitHubResponse(success=True, data=out)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_git_tag(self, owner: str, repo: str, tag: str, message: str, object_sha: str, type: str, tagger: dict[str, str] | None = None) -> GitHubResponse[GitTag]:
        """Create a git tag object."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(tagger=tagger)
            out = r.create_git_tag(tag=tag, message=message, object=object_sha, type=type, **params)
            return GitHubResponse(success=True, data=out)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def search_repositories(self, query: str) -> GitHubResponse[list[Repository]]:
        """Search repositories."""
        try:
            res = list(self._sdk.search_repositories(query))
            return GitHubResponse(success=True, data=res)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_rate_limit(self) -> GitHubResponse[RateLimit]:
        """Get current rate limit."""
        try:
            limit = self._sdk.get_rate_limit()
            return GitHubResponse(success=True, data=limit)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

