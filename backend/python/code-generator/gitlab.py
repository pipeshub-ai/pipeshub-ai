#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
GitLab (python-gitlab) — Code Generator (strict, no `Any`, no `None` passthrough)

Emits a `GitLabDataSource` with explicit, typed methods mapped to *real* python-gitlab APIs.
- No `Any` in signatures or implementation.
- Never forwards None to the SDK (filters optionals).
- Accepts either a raw `gitlab.Gitlab` instance or any client exposing `.get_sdk() -> gitlab.Gitlab`.

Doc alignment (examples):
- Projects: gl.projects.list/get/create; project.save(); project.delete().  See docs.  # [projects]
- Issues: project.issues.list/get/create/save/delete.                       See docs.  # [issues]
- Merge requests: project.mergerequests.list/get/create/save/delete/merge.  See docs.  # [mrs]
- Branches: project.branches.list/get/create/delete.                        See docs.  # [branches]
- Commits: project.commits.list/get/create.                                 See docs.  # [commits]
- Groups: gl.groups.list/get/create/save.                                   See docs.  # [groups]
- Users: gl.users.get/list (for lookups).                                   See docs.  # [users]

References (for maintainers):
- Projects: https://python-gitlab.readthedocs.io/en/stable/gl_objects/projects.html
- Issues: https://python-gitlab.readthedocs.io/en/stable/gl_objects/issues.html
- Merge Requests: https://python-gitlab.readthedocs.io/en/stable/gl_objects/merge_requests.html
- Branches: https://python-gitlab.readthedocs.io/en/stable/gl_objects/branches.html
- Commits: https://python-gitlab.readthedocs.io/en/stable/gl_objects/commits.html
- Groups: https://python-gitlab.readthedocs.io/en/stable/gl_objects/groups.html
- Users: https://python-gitlab.readthedocs.io/en/stable/gl_objects/users.html
"""

import argparse
import textwrap
from typing import Dict, List, Optional, Tuple, Union

# -----------------------------
# Configuration knobs (CLI-set)
# -----------------------------

DEFAULT_RESPONSE_IMPORT = "from app.sources.client.gitlab.gitlab import GitLabResponse"
DEFAULT_CLASS_NAME = "GitLabDataSource"
DEFAULT_OUT = "gitlab_data_source.py"


HEADER = '''\
# ruff: noqa
from __future__ import annotations

import gitlab
from gitlab import Gitlab
from typing import Dict, List, Optional, Tuple, Union, cast

{response_import}

class {class_name}:
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
'''

FOOTER = """
"""

# Each tuple: (signature, body, short_doc)
METHODS: List[Tuple[str, str, str]] = []

# ---------- Projects ----------
METHODS += [
    (
        # Docs: list projects, filters like membership/owned/starred/simple, pagination via get_all
        # https://python-gitlab.readthedocs.io/en/stable/cli-examples.html#projects
        "list_projects(self, search: Optional[str] = None, membership: Optional[bool] = None, owned: Optional[bool] = None, starred: Optional[bool] = None, simple: Optional[bool] = None, get_all: bool = True) -> GitLabResponse",
        "            params = self._params(search=search, membership=membership, owned=owned, starred=starred, simple=simple)\n"
        "            projects = self._sdk.projects.list(get_all=get_all, **params)\n"
        "            return GitLabResponse(success=True, data=projects)",
        "List accessible projects (optionally filtered).  [projects]",
    ),
    (
        # Docs: get project by ID/path
        "get_project(self, project_id: Union[int, str]) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            return GitLabResponse(success=True, data=p)",
        "Get a single project by ID or path.  [projects]",
    ),
    (
        # Docs: create project
        "create_project(self, name: str, namespace_id: Optional[int] = None, visibility: Optional[str] = None, description: Optional[str] = None, initialize_with_readme: Optional[bool] = None, default_branch: Optional[str] = None) -> GitLabResponse",
        "            payload = self._params(name=name, namespace_id=namespace_id, visibility=visibility, description=description, initialize_with_readme=initialize_with_readme, default_branch=default_branch)\n"
        "            proj = self._sdk.projects.create(payload)\n"
        "            return GitLabResponse(success=True, data=proj)",
        "Create a project.  [projects]",
    ),
    (
        # Docs: update via attribute assignment + save()
        "update_project(self, project_id: Union[int, str], name: Optional[str] = None, description: Optional[str] = None, visibility: Optional[str] = None, default_branch: Optional[str] = None, topics: Optional[List[str]] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            changed = False\n"
        "            updates = {\n"
        "                'name': name,\n"
        "                'description': description,\n"
        "                'visibility': visibility,\n"
        "                'default_branch': default_branch,\n"
        "            }\n"
        "            for field, value in updates.items():\n"
        "                if value is not None:\n"
        "                    setattr(p, field, value)\n"
        "                    changed = True\n"
        "            if topics is not None and len(topics) > 0:\n"
        "                setattr(p, 'topics', topics)\n"
        "                changed = True\n"
        "            if changed:\n"
        "                p.save()\n"
        "            return GitLabResponse(success=True, data=p)",
        "Update mutable project fields.  [projects]",
    ),
    (
        # Docs: project.delete()
        "delete_project(self, project_id: Union[int, str]) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            p.delete()\n"
        "            return GitLabResponse(success=True, data=True)",
        "Delete project.  [projects]",
    ),
]

# ---------- Issues ----------
METHODS += [
    (
        # Docs: project.issues.list(get_all=...), supports labels, state, search, etc.
        # https://python-gitlab.readthedocs.io/en/stable/gl_objects/issues.html
        "list_issues(self, project_id: Union[int, str], state: Optional[str] = None, labels: Optional[List[str]] = None, search: Optional[str] = None, author_id: Optional[int] = None, assignee_id: Optional[int] = None, get_all: bool = True) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            params = self._params(state=state, labels=labels, search=search, author_id=author_id, assignee_id=assignee_id)\n"
        "            items = p.issues.list(get_all=get_all, **params)\n"
        "            return GitLabResponse(success=True, data=items)",
        "List project issues with filters.  [issues]",
    ),
    (
        # Docs: project.issues.get(iid)
        "get_issue(self, project_id: Union[int, str], issue_iid: int) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            issue = p.issues.get(issue_iid)\n"
        "            return GitLabResponse(success=True, data=issue)",
        "Get a single issue by IID.  [issues]",
    ),
    (
        # Docs: project.issues.create({...})
        "create_issue(self, project_id: Union[int, str], title: str, description: Optional[str] = None, labels: Optional[List[str]] = None, assignee_ids: Optional[List[int]] = None, milestone_id: Optional[int] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            payload = self._params(title=title, description=description, labels=labels, assignee_ids=assignee_ids, milestone_id=milestone_id)\n"
        "            issue = p.issues.create(payload)\n"
        "            return GitLabResponse(success=True, data=issue)",
        "Create an issue.  [issues]",
    ),
    (
        # Docs: issue.save(), state_event=close/reopen
        "update_issue(self, project_id: Union[int, str], issue_iid: int, title: Optional[str] = None, description: Optional[str] = None, labels: Optional[List[str]] = None, state_event: Optional[str] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            issue = p.issues.get(issue_iid)\n"
        "            changed = False\n"
        "            updates = {\n"
        "                'title': title,\n"
        "                'description': description,\n"
        "                'state_event': state_event,\n"
        "            }\n"
        "            for field, value in updates.items():\n"
        "                if value is not None:\n"
        "                    setattr(issue, field, value)\n"
        "                    changed = True\n"
        "            if labels is not None and len(labels) > 0:\n"
        "                setattr(issue, 'labels', labels)\n"
        "                changed = True\n"
        "            if changed:\n"
        "                issue.save()\n"
        "            return GitLabResponse(success=True, data=issue)",
        "Update issue fields; use state_event='close'/'reopen' to change state.  [issues]",
    ),
    (
        # Docs: project.issues.delete(iid) or issue.delete()
        "delete_issue(self, project_id: Union[int, str], issue_iid: int) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            p.issues.delete(issue_iid)\n"
        "            return GitLabResponse(success=True, data=True)",
        "Delete issue.  [issues]",
    ),
]

# ---------- Merge Requests ----------
METHODS += [
    (
        # Docs: project.mergerequests.list(get_all=...)
        # https://python-gitlab.readthedocs.io/en/stable/gl_objects/merge_requests.html
        "list_merge_requests(self, project_id: Union[int, str], state: Optional[str] = None, labels: Optional[List[str]] = None, search: Optional[str] = None, author_id: Optional[int] = None, assignee_id: Optional[int] = None, get_all: bool = True) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            params = self._params(state=state, labels=labels, search=search, author_id=author_id, assignee_id=assignee_id)\n"
        "            mrs = p.mergerequests.list(get_all=get_all, **params)\n"
        "            return GitLabResponse(success=True, data=mrs)",
        "List merge requests with filters.  [mrs]",
    ),
    (
        # Docs: project.mergerequests.get(iid)
        "get_merge_request(self, project_id: Union[int, str], mr_iid: int) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            mr = p.mergerequests.get(mr_iid)\n"
        "            return GitLabResponse(success=True, data=mr)",
        "Get a single merge request by IID.  [mrs]",
    ),
    (
        # Docs: project.mergerequests.create({...})
        "create_merge_request(self, project_id: Union[int, str], source_branch: str, target_branch: str, title: str, description: Optional[str] = None, labels: Optional[List[str]] = None, assignee_id: Optional[int] = None, assignee_ids: Optional[List[int]] = None, remove_source_branch: Optional[bool] = None, draft: Optional[bool] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            payload = self._params(source_branch=source_branch, target_branch=target_branch, title=title, description=description, labels=labels, assignee_id=assignee_id, assignee_ids=assignee_ids, remove_source_branch=remove_source_branch, draft=draft)\n"
        "            mr = p.mergerequests.create(payload)\n"
        "            return GitLabResponse(success=True, data=mr)",
        "Create a merge request.  [mrs]",
    ),
    (
        # Docs: mr.save(); mr.state_event='close'/'reopen'; mr.merge()
        "update_merge_request(self, project_id: Union[int, str], mr_iid: int, title: Optional[str] = None, description: Optional[str] = None, labels: Optional[List[str]] = None, state_event: Optional[str] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            mr = p.mergerequests.get(mr_iid)\n"
        "            changed = False\n"
        "            updates = {\n"
        "                'title': title,\n"
        "                'description': description,\n"
        "                'state_event': state_event,\n"
        "            }\n"
        "            for field, value in updates.items():\n"
        "                if value is not None:\n"
        "                    setattr(mr, field, value)\n"
        "                    changed = True\n"
        "            if labels is not None and len(labels) > 0:\n"
        "                setattr(mr, 'labels', labels)\n"
        "                changed = True\n"
        "            if changed:\n"
        "                mr.save()\n"
        "            return GitLabResponse(success=True, data=mr)",
        "Update MR fields; use state_event to close/reopen.  [mrs]",
    ),
    (
        # Docs: project.mergerequests.delete(mr_iid) or mr.delete()
        "delete_merge_request(self, project_id: Union[int, str], mr_iid: int) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            p.mergerequests.delete(mr_iid)\n"
        "            return GitLabResponse(success=True, data=True)",
        "Delete a merge request.  [mrs]",
    ),
    (
        # Docs: mr.merge()
        "merge_merge_request(self, project_id: Union[int, str], mr_iid: int, merge_when_pipeline_succeeds: Optional[bool] = None, squash: Optional[bool] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            mr = p.mergerequests.get(mr_iid)\n"
        "            params = self._params(merge_when_pipeline_succeeds=merge_when_pipeline_succeeds, squash=squash)\n"
        "            res = mr.merge(**params)\n"
        "            return GitLabResponse(success=True, data=res)",
        "Accept/merge a merge request.  [mrs]",
    ),
]

# ---------- Branches ----------
METHODS += [
    (
        # Docs: project.branches.list/get/create/delete
        # https://python-gitlab.readthedocs.io/en/stable/gl_objects/branches.html
        "list_branches(self, project_id: Union[int, str], get_all: bool = True) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            items = p.branches.list(get_all=get_all)\n"
        "            return GitLabResponse(success=True, data=items)",
        "List branches for a project.  [branches]",
    ),
    (
        "get_branch(self, project_id: Union[int, str], branch: str) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            b = p.branches.get(branch)\n"
        "            return GitLabResponse(success=True, data=b)",
        "Get a single branch.  [branches]",
    ),
    (
        "create_branch(self, project_id: Union[int, str], branch: str, ref: str) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            b = p.branches.create({'branch': branch, 'ref': ref})\n"
        "            return GitLabResponse(success=True, data=b)",
        "Create a branch from ref.  [branches]",
    ),
    (
        "delete_branch(self, project_id: Union[int, str], branch: str) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            p.branches.delete(branch)\n"
        "            return GitLabResponse(success=True, data=True)",
        "Delete a branch.  [branches]",
    ),
]

# ---------- Tags ----------
METHODS += [
    (
        "list_tags(self, project_id: Union[int, str], get_all: bool = True) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            items = p.tags.list(get_all=get_all)\n"
        "            return GitLabResponse(success=True, data=items)",
        "List tags.",
    ),
    (
        "get_tag(self, project_id: Union[int, str], tag_name: str) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            t = p.tags.get(tag_name)\n"
        "            return GitLabResponse(success=True, data=t)",
        "Get a tag.",
    ),
    (
        "create_tag(self, project_id: Union[int, str], tag_name: str, ref: str, message: Optional[str] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            payload = self._params(tag_name=tag_name, ref=ref, message=message)\n"
        "            t = p.tags.create(payload)\n"
        "            return GitLabResponse(success=True, data=t)",
        "Create a tag.",
    ),
    (
        "delete_tag(self, project_id: Union[int, str], tag_name: str) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            p.tags.delete(tag_name)\n"
        "            return GitLabResponse(success=True, data=True)",
        "Delete a tag.",
    ),
]

# ---------- Commits ----------
METHODS += [
    (
        # https://python-gitlab.readthedocs.io/en/stable/gl_objects/commits.html
        "list_commits(self, project_id: Union[int, str], ref_name: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None, get_all: bool = True) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            params = self._params(ref_name=ref_name, since=since, until=until)\n"
        "            items = p.commits.list(get_all=get_all, **params)\n"
        "            return GitLabResponse(success=True, data=items)",
        "List commits (supports ref_name/since/until).  [commits]",
    ),
    (
        "get_commit(self, project_id: Union[int, str], sha: str) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            c = p.commits.get(sha)\n"
        "            return GitLabResponse(success=True, data=c)",
        "Get a single commit.  [commits]",
    ),
    (
        # Minimal create example, actions are required; keep typed as list[dict[str, str]]
        "create_commit(self, project_id: Union[int, str], branch: str, commit_message: str, actions: List[Dict[str, str]]) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            payload = {'branch': branch, 'commit_message': commit_message, 'actions': actions}\n"
        "            c = p.commits.create(payload)\n"
        "            return GitLabResponse(success=True, data=c)",
        "Create a commit with actions.",
    ),
]

# ---------- Pipelines ----------
METHODS += [
    (
        "list_pipelines(self, project_id: Union[int, str], ref: Optional[str] = None, status: Optional[str] = None, get_all: bool = True) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            params = self._params(ref=ref, status=status)\n"
        "            items = p.pipelines.list(get_all=get_all, **params)\n"
        "            return GitLabResponse(success=True, data=items)",
        "List pipelines.",
    ),
    (
        "get_pipeline(self, project_id: Union[int, str], pipeline_id: int) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            pl = p.pipelines.get(pipeline_id)\n"
        "            return GitLabResponse(success=True, data=pl)",
        "Get a pipeline.",
    ),
    (
        "create_pipeline(self, project_id: Union[int, str], ref: str, variables: Optional[Dict[str, str]] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            payload = self._params(ref=ref)\n"
        "            if variables is not None and len(variables) > 0:\n"
        "                # API expects a list of {key, value} dicts for variables\n"
        "                payload['variables'] = [{'key': k, 'value': v} for k, v in variables.items()]\n"
        "            pl = p.pipelines.create(payload)\n"
        "            return GitLabResponse(success=True, data=pl)",
        "Create a pipeline on a ref.",
    ),
    (
        "delete_pipeline(self, project_id: Union[int, str], pipeline_id: int) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            p.pipelines.delete(pipeline_id)\n"
        "            return GitLabResponse(success=True, data=True)",
        "Delete a pipeline.",
    ),
]

# ---------- Releases ----------
METHODS += [
    (
        "list_releases(self, project_id: Union[int, str], get_all: bool = True) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            items = p.releases.list(get_all=get_all)\n"
        "            return GitLabResponse(success=True, data=items)",
        "List releases.",
    ),
    (
        "get_release(self, project_id: Union[int, str], tag_name: str) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            r = p.releases.get(tag_name)\n"
        "            return GitLabResponse(success=True, data=r)",
        "Get a release by tag.",
    ),
    (
        "create_release(self, project_id: Union[int, str], tag_name: str, name: str, description: str, ref: Optional[str] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            payload = self._params(tag_name=tag_name, name=name, description=description, ref=ref)\n"
        "            r = p.releases.create(payload)\n"
        "            return GitLabResponse(success=True, data=r)",
        "Create a release.",
    ),
    (
        "update_release(self, project_id: Union[int, str], tag_name: str, name: Optional[str] = None, description: Optional[str] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            r = p.releases.get(tag_name)\n"
        "            changed = False\n"
        "            updates = {\n"
        "                'name': name,\n"
        "                'description': description,\n"
        "            }\n"
        "            for field, value in updates.items():\n"
        "                if value is not None:\n"
        "                    setattr(r, field, value)\n"
        "                    changed = True\n"
        "            if changed:\n"
        "                r.save()\n"
        "            return GitLabResponse(success=True, data=r)",
        "Update a release.",
    ),
    (
        "delete_release(self, project_id: Union[int, str], tag_name: str) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            p.releases.delete(tag_name)\n"
        "            return GitLabResponse(success=True, data=True)",
        "Delete a release.",
    ),
]

# ---------- Milestones ----------
METHODS += [
    (
        "list_milestones(self, project_id: Union[int, str], state: Optional[str] = None, get_all: bool = True) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            params = self._params(state=state)\n"
        "            items = p.milestones.list(get_all=get_all, **params)\n"
        "            return GitLabResponse(success=True, data=items)",
        "List project milestones.",
    ),
    (
        "get_milestone(self, project_id: Union[int, str], milestone_id: int) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            m = p.milestones.get(milestone_id)\n"
        "            return GitLabResponse(success=True, data=m)",
        "Get a milestone.",
    ),
    (
        "create_milestone(self, project_id: Union[int, str], title: str, description: Optional[str] = None, due_date: Optional[str] = None, start_date: Optional[str] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            payload = self._params(title=title, description=description, due_date=due_date, start_date=start_date)\n"
        "            m = p.milestones.create(payload)\n"
        "            return GitLabResponse(success=True, data=m)",
        "Create a milestone.",
    ),
    (
        "update_milestone(self, project_id: Union[int, str], milestone_id: int, title: Optional[str] = None, description: Optional[str] = None, state_event: Optional[str] = None, due_date: Optional[str] = None, start_date: Optional[str] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            m = p.milestones.get(milestone_id)\n"
        "            changed = False\n"
        "            for field, val in [('title', title), ('description', description), ('state_event', state_event), ('due_date', due_date), ('start_date', start_date)]:\n"
        "                if val is not None:\n"
        "                    setattr(m, field, val)\n"
        "                    changed = True\n"
        "            if changed:\n"
        "                m.save()\n"
        "            return GitLabResponse(success=True, data=m)",
        "Update a milestone.",
    ),
    (
        "delete_milestone(self, project_id: Union[int, str], milestone_id: int) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            p.milestones.delete(milestone_id)\n"
        "            return GitLabResponse(success=True, data=True)",
        "Delete a milestone.",
    ),
]

# ---------- Labels ----------
METHODS += [
    (
        "list_labels(self, project_id: Union[int, str], get_all: bool = True) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            items = p.labels.list(get_all=get_all)\n"
        "            return GitLabResponse(success=True, data=items)",
        "List labels.",
    ),
    (
        "create_label(self, project_id: Union[int, str], name: str, color: str, description: Optional[str] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            payload = self._params(name=name, color=color, description=description)\n"
        "            lb = p.labels.create(payload)\n"
        "            return GitLabResponse(success=True, data=lb)",
        "Create a label.",
    ),
    (
        "update_label(self, project_id: Union[int, str], current_name: str, new_name: Optional[str] = None, color: Optional[str] = None, description: Optional[str] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            # API expects 'name' for the *current* label when updating via manager\n"
        "            payload = self._params(name=current_name, new_name=new_name, color=color, description=description)\n"
        "            lb = p.labels.update(payload)\n"
        "            return GitLabResponse(success=True, data=lb)",
        "Update a label.",
    ),
    (
        "delete_label(self, project_id: Union[int, str], name: str) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            p.labels.delete(name)\n"
        "            return GitLabResponse(success=True, data=True)",
        "Delete a label.",
    ),
]

# ---------- Members ----------
METHODS += [
    (
        "list_project_members(self, project_id: Union[int, str], get_all: bool = True) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            items = p.members.list(get_all=get_all)\n"
        "            return GitLabResponse(success=True, data=items)",
        "List project members.",
    ),
    (
        "add_project_member(self, project_id: Union[int, str], user_id: int, access_level: int, expires_at: Optional[str] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            payload = self._params(user_id=user_id, access_level=access_level, expires_at=expires_at)\n"
        "            m = p.members.create(payload)\n"
        "            return GitLabResponse(success=True, data=m)",
        "Add member to project.",
    ),
    (
        "update_project_member(self, project_id: Union[int, str], user_id: int, access_level: Optional[int] = None, expires_at: Optional[str] = None) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            m = p.members.get(user_id)\n"
        "            changed = False\n"
        "            updates = {\n"
        "                'access_level': access_level,\n"
        "                'expires_at': expires_at,\n"
        "            }\n"
        "            for field, value in updates.items():\n"
        "                if value is not None:\n"
        "                    setattr(m, field, value)\n"
        "                    changed = True\n"
        "            if changed:\n"
        "                m.save()\n"
        "            return GitLabResponse(success=True, data=m)",
        "Update project member.",
    ),
    (
        "remove_project_member(self, project_id: Union[int, str], user_id: int) -> GitLabResponse",
        "            p = self._project(project_id)\n"
        "            p.members.delete(user_id)\n"
        "            return GitLabResponse(success=True, data=True)",
        "Remove project member.",
    ),
]

# ---------- Groups (basic CRUD-ish) ----------
METHODS += [
    (
        # https://python-gitlab.readthedocs.io/en/stable/gl_objects/groups.html
        "list_groups(self, search: Optional[str] = None, get_all: bool = True) -> GitLabResponse",
        "            params = self._params(search=search)\n"
        "            groups = self._sdk.groups.list(get_all=get_all, **params)\n"
        "            return GitLabResponse(success=True, data=groups)",
        "List groups.",
    ),
    (
        "get_group(self, group_id: Union[int, str]) -> GitLabResponse",
        "            g = self._sdk.groups.get(group_id)\n"
        "            return GitLabResponse(success=True, data=g)",
        "Get a group by ID or full path.",
    ),
    (
        # Note: On GitLab.com, top-level groups cannot be created via API; subgroups allowed with parent_id.
        "create_group(self, name: str, path: str, parent_id: Optional[int] = None, description: Optional[str] = None, visibility: Optional[str] = None) -> GitLabResponse",
        "            payload = self._params(name=name, path=path, parent_id=parent_id, description=description, visibility=visibility)\n"
        "            g = self._sdk.groups.create(payload)\n"
        "            return GitLabResponse(success=True, data=g)",
        "Create a group or subgroup (see GitLab.com restriction).  [groups]",
    ),
    (
        "update_group(self, group_id: Union[int, str], name: Optional[str] = None, description: Optional[str] = None, visibility: Optional[str] = None) -> GitLabResponse",
        "            g = self._sdk.groups.get(group_id)\n"
        "            changed = False\n"
        "            updates = {\n"
        "                'name': name,\n"
        "                'description': description,\n"
        "                'visibility': visibility,\n"
        "            }\n"
        "            for field, value in updates.items():\n"
        "                if value is not None:\n"
        "                    setattr(g, field, value)\n"
        "                    changed = True\n"
        "            if changed:\n"
        "                g.save()\n"
        "            return GitLabResponse(success=True, data=g)",
        "Update group fields.  [groups]",
    ),
    (
        "delete_group(self, group_id: Union[int, str]) -> GitLabResponse",
        "            g = self._sdk.groups.get(group_id)\n"
        "            g.delete()\n"
        "            return GitLabResponse(success=True, data=True)",
        "Delete a group.",
    ),
]

# -------------------------
# Code emission utilities
# -------------------------


def _emit_method(sig: str, body: str, doc: str) -> str:
    normalized_body = textwrap.indent(textwrap.dedent(body), "        ")
    return f'    def {sig}:\n        """{doc}"""\n{normalized_body}\n'


def build_class(
    response_import: str = DEFAULT_RESPONSE_IMPORT, class_name: str = DEFAULT_CLASS_NAME
) -> str:
    parts: List[str] = []
    header = HEADER.replace("{response_import}", response_import).replace(
        "{class_name}", class_name
    )
    parts.append(header)
    for sig, body, doc in METHODS:
        parts.append(_emit_method(sig, body, doc))
    parts.append(FOOTER)
    return "".join(parts)


def write_output(path: str, code: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate GitLabDataSource (python-gitlab)."
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help="Output path for the generated data source."
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line to bring in GitLabResponse (e.g. 'from app.sources.client.gitlab.gitlab import GitLabResponse').",
    )
    parser.add_argument(
        "--class-name",
        default=DEFAULT_CLASS_NAME,
        help="Name of the generated datasource class.",
    )
    parser.add_argument(
        "--print",
        dest="do_print",
        action="store_true",
        help="Also print generated code to stdout.",
    )
    args = parser.parse_args()

    code = build_class(response_import=args.response_import, class_name=args.class_name)
    write_output(args.out, code)
    if args.do_print:
        print(code)


if __name__ == "__main__":
    main()
