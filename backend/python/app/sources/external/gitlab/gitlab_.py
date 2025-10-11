# ruff: noqa
from __future__ import annotations

from gitlab import Gitlab
from typing import Dict, List, Optional, Tuple, Union, cast

from app.sources.client.gitlab.gitlab import GitLabResponse


class GitLabDataSource:
    """
    Strict, typed wrapper over python-gitlab for common GitLab business operations.

    Accepts either a python-gitlab `Gitlab` instance *or* any object with `.get_sdk() -> Gitlab`.
    """

    def __init__(self, client_or_sdk: Union[Gitlab, object]) -> None:
        # Support a raw SDK or a wrapper that exposes `.get_sdk()`
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: Gitlab = cast(Gitlab, sdk_obj)
        else:
            self._sdk = cast(Gitlab, client_or_sdk)

    # ---- helpers ----
    def _project(self, project_id: Union[int, str]) -> object:
        # python-gitlab allows numeric ID or full path for project lookup
        return self._sdk.projects.get(project_id)

    @staticmethod
    def _params(**kwargs: object) -> Dict[str, object]:
        # Filter out Nones to avoid overriding SDK defaults
        out: Dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            # Skip empty containers that GitLab rejects in some endpoints
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out

    def list_projects(
        self,
        search: Optional[str] = None,
        membership: Optional[bool] = None,
        owned: Optional[bool] = None,
        starred: Optional[bool] = None,
        simple: Optional[bool] = None,
        get_all: bool = True,
    ) -> GitLabResponse:
        """List accessible projects (optionally filtered).  [projects]"""
        params = self._params(
            search=search,
            membership=membership,
            owned=owned,
            starred=starred,
            simple=simple,
        )
        projects = self._sdk.projects.list(get_all=get_all, **params)
        return GitLabResponse(success=True, data=projects)

    def get_project(self, project_id: Union[int, str]) -> GitLabResponse:
        """Get a single project by ID or path.  [projects]"""
        p = self._project(project_id)
        return GitLabResponse(success=True, data=p)

    def create_project(
        self,
        name: str,
        namespace_id: Optional[int] = None,
        visibility: Optional[str] = None,
        description: Optional[str] = None,
        initialize_with_readme: Optional[bool] = None,
        default_branch: Optional[str] = None,
    ) -> GitLabResponse:
        """Create a project.  [projects]"""
        payload = self._params(
            name=name,
            namespace_id=namespace_id,
            visibility=visibility,
            description=description,
            initialize_with_readme=initialize_with_readme,
            default_branch=default_branch,
        )
        proj = self._sdk.projects.create(payload)
        return GitLabResponse(success=True, data=proj)

    def update_project(
        self,
        project_id: Union[int, str],
        name: Optional[str] = None,
        description: Optional[str] = None,
        visibility: Optional[str] = None,
        default_branch: Optional[str] = None,
        topics: Optional[List[str]] = None,
    ) -> GitLabResponse:
        """Update mutable project fields.  [projects]"""
        p = self._project(project_id)
        changed = False
        if name is not None:
            setattr(p, "name", name)
            changed = True
        if description is not None:
            setattr(p, "description", description)
            changed = True
        if visibility is not None:
            setattr(p, "visibility", visibility)
            changed = True
        if default_branch is not None:
            setattr(p, "default_branch", default_branch)
            changed = True
        if topics is not None and len(topics) > 0:
            setattr(p, "topics", topics)
            changed = True
        if changed:
            p.save()
        return GitLabResponse(success=True, data=p)

    def delete_project(self, project_id: Union[int, str]) -> GitLabResponse:
        """Delete project.  [projects]"""
        p = self._project(project_id)
        p.delete()
        return GitLabResponse(success=True, data=True)

    def list_issues(
        self,
        project_id: Union[int, str],
        state: Optional[str] = None,
        labels: Optional[List[str]] = None,
        search: Optional[str] = None,
        author_id: Optional[int] = None,
        assignee_id: Optional[int] = None,
        get_all: bool = True,
    ) -> GitLabResponse:
        """List project issues with filters.  [issues]"""
        p = self._project(project_id)
        params = self._params(
            state=state,
            labels=labels,
            search=search,
            author_id=author_id,
            assignee_id=assignee_id,
        )
        items = p.issues.list(get_all=get_all, **params)
        return GitLabResponse(success=True, data=items)

    def get_issue(self, project_id: Union[int, str], issue_iid: int) -> GitLabResponse:
        """Get a single issue by IID.  [issues]"""
        p = self._project(project_id)
        issue = p.issues.get(issue_iid)
        return GitLabResponse(success=True, data=issue)

    def create_issue(
        self,
        project_id: Union[int, str],
        title: str,
        description: Optional[str] = None,
        labels: Optional[List[str]] = None,
        assignee_ids: Optional[List[int]] = None,
        milestone_id: Optional[int] = None,
    ) -> GitLabResponse:
        """Create an issue.  [issues]"""
        p = self._project(project_id)
        payload = self._params(
            title=title,
            description=description,
            labels=labels,
            assignee_ids=assignee_ids,
            milestone_id=milestone_id,
        )
        issue = p.issues.create(payload)
        return GitLabResponse(success=True, data=issue)

    def update_issue(
        self,
        project_id: Union[int, str],
        issue_iid: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        labels: Optional[List[str]] = None,
        state_event: Optional[str] = None,
    ) -> GitLabResponse:
        """Update issue fields; use state_event='close'/'reopen' to change state.  [issues]"""
        p = self._project(project_id)
        issue = p.issues.get(issue_iid)
        changed = False
        if title is not None:
            setattr(issue, "title", title)
            changed = True
        if description is not None:
            setattr(issue, "description", description)
            changed = True
        if labels is not None and len(labels) > 0:
            setattr(issue, "labels", labels)
            changed = True
        if state_event is not None:
            setattr(issue, "state_event", state_event)
            changed = True
        if changed:
            issue.save()
        return GitLabResponse(success=True, data=issue)

    def delete_issue(
        self, project_id: Union[int, str], issue_iid: int
    ) -> GitLabResponse:
        """Delete issue.  [issues]"""
        p = self._project(project_id)
        p.issues.delete(issue_iid)
        return GitLabResponse(success=True, data=True)

    def list_merge_requests(
        self,
        project_id: Union[int, str],
        state: Optional[str] = None,
        labels: Optional[List[str]] = None,
        search: Optional[str] = None,
        author_id: Optional[int] = None,
        assignee_id: Optional[int] = None,
        get_all: bool = True,
    ) -> GitLabResponse:
        """List merge requests with filters.  [mrs]"""
        p = self._project(project_id)
        params = self._params(
            state=state,
            labels=labels,
            search=search,
            author_id=author_id,
            assignee_id=assignee_id,
        )
        mrs = p.mergerequests.list(get_all=get_all, **params)
        return GitLabResponse(success=True, data=mrs)

    def get_merge_request(
        self, project_id: Union[int, str], mr_iid: int
    ) -> GitLabResponse:
        """Get a single merge request by IID.  [mrs]"""
        p = self._project(project_id)
        mr = p.mergerequests.get(mr_iid)
        return GitLabResponse(success=True, data=mr)

    def create_merge_request(
        self,
        project_id: Union[int, str],
        source_branch: str,
        target_branch: str,
        title: str,
        description: Optional[str] = None,
        labels: Optional[List[str]] = None,
        assignee_id: Optional[int] = None,
        assignee_ids: Optional[List[int]] = None,
        remove_source_branch: Optional[bool] = None,
        draft: Optional[bool] = None,
    ) -> GitLabResponse:
        """Create a merge request.  [mrs]"""
        p = self._project(project_id)
        payload = self._params(
            source_branch=source_branch,
            target_branch=target_branch,
            title=title,
            description=description,
            labels=labels,
            assignee_id=assignee_id,
            assignee_ids=assignee_ids,
            remove_source_branch=remove_source_branch,
            draft=draft,
        )
        mr = p.mergerequests.create(payload)
        return GitLabResponse(success=True, data=mr)

    def update_merge_request(
        self,
        project_id: Union[int, str],
        mr_iid: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        labels: Optional[List[str]] = None,
        state_event: Optional[str] = None,
    ) -> GitLabResponse:
        """Update MR fields; use state_event to close/reopen.  [mrs]"""
        p = self._project(project_id)
        mr = p.mergerequests.get(mr_iid)
        changed = False
        if title is not None:
            setattr(mr, "title", title)
            changed = True
        if description is not None:
            setattr(mr, "description", description)
            changed = True
        if labels is not None and len(labels) > 0:
            setattr(mr, "labels", labels)
            changed = True
        if state_event is not None:
            setattr(mr, "state_event", state_event)
            changed = True
        if changed:
            mr.save()
        return GitLabResponse(success=True, data=mr)

    def delete_merge_request(
        self, project_id: Union[int, str], mr_iid: int
    ) -> GitLabResponse:
        """Delete a merge request.  [mrs]"""
        p = self._project(project_id)
        p.mergerequests.delete(mr_iid)
        return GitLabResponse(success=True, data=True)

    def merge_merge_request(
        self,
        project_id: Union[int, str],
        mr_iid: int,
        merge_when_pipeline_succeeds: Optional[bool] = None,
        squash: Optional[bool] = None,
    ) -> GitLabResponse:
        """Accept/merge a merge request.  [mrs]"""
        p = self._project(project_id)
        mr = p.mergerequests.get(mr_iid)
        params = self._params(
            merge_when_pipeline_succeeds=merge_when_pipeline_succeeds, squash=squash
        )
        res = mr.merge(**params)
        return GitLabResponse(success=True, data=res)

    def list_branches(
        self, project_id: Union[int, str], get_all: bool = True
    ) -> GitLabResponse:
        """List branches for a project.  [branches]"""
        p = self._project(project_id)
        items = p.branches.list(get_all=get_all)
        return GitLabResponse(success=True, data=items)

    def get_branch(self, project_id: Union[int, str], branch: str) -> GitLabResponse:
        """Get a single branch.  [branches]"""
        p = self._project(project_id)
        b = p.branches.get(branch)
        return GitLabResponse(success=True, data=b)

    def create_branch(
        self, project_id: Union[int, str], branch: str, ref: str
    ) -> GitLabResponse:
        """Create a branch from ref.  [branches]"""
        p = self._project(project_id)
        b = p.branches.create({"branch": branch, "ref": ref})
        return GitLabResponse(success=True, data=b)

    def delete_branch(self, project_id: Union[int, str], branch: str) -> GitLabResponse:
        """Delete a branch.  [branches]"""
        p = self._project(project_id)
        p.branches.delete(branch)
        return GitLabResponse(success=True, data=True)

    def list_tags(
        self, project_id: Union[int, str], get_all: bool = True
    ) -> GitLabResponse:
        """List tags."""
        p = self._project(project_id)
        items = p.tags.list(get_all=get_all)
        return GitLabResponse(success=True, data=items)

    def get_tag(self, project_id: Union[int, str], tag_name: str) -> GitLabResponse:
        """Get a tag."""
        p = self._project(project_id)
        t = p.tags.get(tag_name)
        return GitLabResponse(success=True, data=t)

    def create_tag(
        self,
        project_id: Union[int, str],
        tag_name: str,
        ref: str,
        message: Optional[str] = None,
    ) -> GitLabResponse:
        """Create a tag."""
        p = self._project(project_id)
        payload = self._params(tag_name=tag_name, ref=ref, message=message)
        t = p.tags.create(payload)
        return GitLabResponse(success=True, data=t)

    def delete_tag(self, project_id: Union[int, str], tag_name: str) -> GitLabResponse:
        """Delete a tag."""
        p = self._project(project_id)
        p.tags.delete(tag_name)
        return GitLabResponse(success=True, data=True)

    def list_commits(
        self,
        project_id: Union[int, str],
        ref_name: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        get_all: bool = True,
    ) -> GitLabResponse:
        """List commits (supports ref_name/since/until).  [commits]"""
        p = self._project(project_id)
        params = self._params(ref_name=ref_name, since=since, until=until)
        items = p.commits.list(get_all=get_all, **params)
        return GitLabResponse(success=True, data=items)

    def get_commit(self, project_id: Union[int, str], sha: str) -> GitLabResponse:
        """Get a single commit.  [commits]"""
        p = self._project(project_id)
        c = p.commits.get(sha)
        return GitLabResponse(success=True, data=c)

    def create_commit(
        self,
        project_id: Union[int, str],
        branch: str,
        commit_message: str,
        actions: List[Dict[str, str]],
    ) -> GitLabResponse:
        """Create a commit with actions."""
        p = self._project(project_id)
        payload = {
            "branch": branch,
            "commit_message": commit_message,
            "actions": actions,
        }
        c = p.commits.create(payload)
        return GitLabResponse(success=True, data=c)

    def list_pipelines(
        self,
        project_id: Union[int, str],
        ref: Optional[str] = None,
        status: Optional[str] = None,
        get_all: bool = True,
    ) -> GitLabResponse:
        """List pipelines."""
        p = self._project(project_id)
        params = self._params(ref=ref, status=status)
        items = p.pipelines.list(get_all=get_all, **params)
        return GitLabResponse(success=True, data=items)

    def get_pipeline(
        self, project_id: Union[int, str], pipeline_id: int
    ) -> GitLabResponse:
        """Get a pipeline."""
        p = self._project(project_id)
        pl = p.pipelines.get(pipeline_id)
        return GitLabResponse(success=True, data=pl)

    def create_pipeline(
        self,
        project_id: Union[int, str],
        ref: str,
        variables: Optional[Dict[str, str]] = None,
    ) -> GitLabResponse:
        """Create a pipeline on a ref."""
        p = self._project(project_id)
        payload = self._params(ref=ref)
        if variables is not None and len(variables) > 0:
            # API expects a list of {key, value} dicts for variables
            payload["variables"] = [
                {"key": k, "value": v} for k, v in variables.items()
            ]
        pl = p.pipelines.create(payload)
        return GitLabResponse(success=True, data=pl)

    def delete_pipeline(
        self, project_id: Union[int, str], pipeline_id: int
    ) -> GitLabResponse:
        """Delete a pipeline."""
        p = self._project(project_id)
        p.pipelines.delete(pipeline_id)
        return GitLabResponse(success=True, data=True)

    def list_releases(
        self, project_id: Union[int, str], get_all: bool = True
    ) -> GitLabResponse:
        """List releases."""
        p = self._project(project_id)
        items = p.releases.list(get_all=get_all)
        return GitLabResponse(success=True, data=items)

    def get_release(self, project_id: Union[int, str], tag_name: str) -> GitLabResponse:
        """Get a release by tag."""
        p = self._project(project_id)
        r = p.releases.get(tag_name)
        return GitLabResponse(success=True, data=r)

    def create_release(
        self,
        project_id: Union[int, str],
        tag_name: str,
        name: str,
        description: str,
        ref: Optional[str] = None,
    ) -> GitLabResponse:
        """Create a release."""
        p = self._project(project_id)
        payload = self._params(
            tag_name=tag_name, name=name, description=description, ref=ref
        )
        r = p.releases.create(payload)
        return GitLabResponse(success=True, data=r)

    def update_release(
        self,
        project_id: Union[int, str],
        tag_name: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> GitLabResponse:
        """Update a release."""
        p = self._project(project_id)
        r = p.releases.get(tag_name)
        changed = False
        if name is not None:
            setattr(r, "name", name)
            changed = True
        if description is not None:
            setattr(r, "description", description)
            changed = True
        if changed:
            r.save()
        return GitLabResponse(success=True, data=r)

    def delete_release(
        self, project_id: Union[int, str], tag_name: str
    ) -> GitLabResponse:
        """Delete a release."""
        p = self._project(project_id)
        p.releases.delete(tag_name)
        return GitLabResponse(success=True, data=True)

    def list_milestones(
        self,
        project_id: Union[int, str],
        state: Optional[str] = None,
        get_all: bool = True,
    ) -> GitLabResponse:
        """List project milestones."""
        p = self._project(project_id)
        params = self._params(state=state)
        items = p.milestones.list(get_all=get_all, **params)
        return GitLabResponse(success=True, data=items)

    def get_milestone(
        self, project_id: Union[int, str], milestone_id: int
    ) -> GitLabResponse:
        """Get a milestone."""
        p = self._project(project_id)
        m = p.milestones.get(milestone_id)
        return GitLabResponse(success=True, data=m)

    def create_milestone(
        self,
        project_id: Union[int, str],
        title: str,
        description: Optional[str] = None,
        due_date: Optional[str] = None,
        start_date: Optional[str] = None,
    ) -> GitLabResponse:
        """Create a milestone."""
        p = self._project(project_id)
        payload = self._params(
            title=title,
            description=description,
            due_date=due_date,
            start_date=start_date,
        )
        m = p.milestones.create(payload)
        return GitLabResponse(success=True, data=m)

    def update_milestone(
        self,
        project_id: Union[int, str],
        milestone_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        state_event: Optional[str] = None,
        due_date: Optional[str] = None,
        start_date: Optional[str] = None,
    ) -> GitLabResponse:
        """Update a milestone."""
        p = self._project(project_id)
        m = p.milestones.get(milestone_id)
        changed = False
        for field, val in [
            ("title", title),
            ("description", description),
            ("state_event", state_event),
            ("due_date", due_date),
            ("start_date", start_date),
        ]:
            if val is not None:
                setattr(m, field, val)
                changed = True
        if changed:
            m.save()
        return GitLabResponse(success=True, data=m)

    def delete_milestone(
        self, project_id: Union[int, str], milestone_id: int
    ) -> GitLabResponse:
        """Delete a milestone."""
        p = self._project(project_id)
        p.milestones.delete(milestone_id)
        return GitLabResponse(success=True, data=True)

    def list_labels(
        self, project_id: Union[int, str], get_all: bool = True
    ) -> GitLabResponse:
        """List labels."""
        p = self._project(project_id)
        items = p.labels.list(get_all=get_all)
        return GitLabResponse(success=True, data=items)

    def create_label(
        self,
        project_id: Union[int, str],
        name: str,
        color: str,
        description: Optional[str] = None,
    ) -> GitLabResponse:
        """Create a label."""
        p = self._project(project_id)
        payload = self._params(name=name, color=color, description=description)
        lb = p.labels.create(payload)
        return GitLabResponse(success=True, data=lb)

    def update_label(
        self,
        project_id: Union[int, str],
        current_name: str,
        new_name: Optional[str] = None,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> GitLabResponse:
        """Update a label."""
        p = self._project(project_id)
        # API expects 'name' for the *current* label when updating via manager
        payload = self._params(
            name=current_name, new_name=new_name, color=color, description=description
        )
        lb = p.labels.update(payload)
        return GitLabResponse(success=True, data=lb)

    def delete_label(self, project_id: Union[int, str], name: str) -> GitLabResponse:
        """Delete a label."""
        p = self._project(project_id)
        p.labels.delete(name)
        return GitLabResponse(success=True, data=True)

    def list_project_members(
        self, project_id: Union[int, str], get_all: bool = True
    ) -> GitLabResponse:
        """List project members."""
        p = self._project(project_id)
        items = p.members.list(get_all=get_all)
        return GitLabResponse(success=True, data=items)

    def add_project_member(
        self,
        project_id: Union[int, str],
        user_id: int,
        access_level: int,
        expires_at: Optional[str] = None,
    ) -> GitLabResponse:
        """Add member to project."""
        p = self._project(project_id)
        payload = self._params(
            user_id=user_id, access_level=access_level, expires_at=expires_at
        )
        m = p.members.create(payload)
        return GitLabResponse(success=True, data=m)

    def update_project_member(
        self,
        project_id: Union[int, str],
        user_id: int,
        access_level: Optional[int] = None,
        expires_at: Optional[str] = None,
    ) -> GitLabResponse:
        """Update project member."""
        p = self._project(project_id)
        m = p.members.get(user_id)
        changed = False
        if access_level is not None:
            setattr(m, "access_level", access_level)
            changed = True
        if expires_at is not None:
            setattr(m, "expires_at", expires_at)
            changed = True
        if changed:
            m.save()
        return GitLabResponse(success=True, data=m)

    def remove_project_member(
        self, project_id: Union[int, str], user_id: int
    ) -> GitLabResponse:
        """Remove project member."""
        p = self._project(project_id)
        p.members.delete(user_id)
        return GitLabResponse(success=True, data=True)

    def list_groups(
        self, search: Optional[str] = None, get_all: bool = True
    ) -> GitLabResponse:
        """List groups."""
        params = self._params(search=search)
        groups = self._sdk.groups.list(get_all=get_all, **params)
        return GitLabResponse(success=True, data=groups)

    def get_group(self, group_id: Union[int, str]) -> GitLabResponse:
        """Get a group by ID or full path."""
        g = self._sdk.groups.get(group_id)
        return GitLabResponse(success=True, data=g)

    def create_group(
        self,
        name: str,
        path: str,
        parent_id: Optional[int] = None,
        description: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> GitLabResponse:
        """Create a group or subgroup (see GitLab.com restriction).  [groups]"""
        payload = self._params(
            name=name,
            path=path,
            parent_id=parent_id,
            description=description,
            visibility=visibility,
        )
        g = self._sdk.groups.create(payload)
        return GitLabResponse(success=True, data=g)

    def update_group(
        self,
        group_id: Union[int, str],
        name: Optional[str] = None,
        description: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> GitLabResponse:
        """Update group fields.  [groups]"""
        g = self._sdk.groups.get(group_id)
        changed = False
        if name is not None:
            setattr(g, "name", name)
            changed = True
        if description is not None:
            setattr(g, "description", description)
            changed = True
        if visibility is not None:
            setattr(g, "visibility", visibility)
            changed = True
        if changed:
            g.save()
        return GitLabResponse(success=True, data=g)

    def delete_group(self, group_id: Union[int, str]) -> GitLabResponse:
        """Delete a group."""
        g = self._sdk.groups.get(group_id)
        g.delete()
        return GitLabResponse(success=True, data=True)
