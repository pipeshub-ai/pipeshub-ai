# ruff: noqa
from __future__ import annotations

import contextlib
from typing import Dict, List, Optional, Sequence, Union, Iterable

import asana
from asana.rest import ApiException


def _clean_opts(opts: Optional[Dict]) -> Dict:
    """Drop None-valued keys. Return a plain dict."""
    if not opts:
        return {}
    return {k: v for k, v in opts.items() if v is not None}


@contextlib.contextmanager
def _temporary_headers(client: asana.ApiClient, headers: Optional[Dict[str, str]]):
    """Temporarily merge headers into api client's default headers for one call."""
    if not headers:
        yield
        return
    default = getattr(client, "default_headers", None)
    if default is None:
        # modern sdk keeps default headers on `client.default_headers`
        client.default_headers = {}  # type: ignore[attr-defined]
        default = client.default_headers
    # snapshot
    original = dict(default)
    try:
        default.update(headers)
        yield
    finally:
        default.clear()
        default.update(original)


def _maybe_iterate(result, materialize: bool) -> Union[Iterable, List]:
    """If SDK returns a page iterator and materialize=True, consume and return list."""
    if not materialize:
        return result
    # PageIterator has __iter__, so try to iterate safely.
    try:
        return list(result)
    except TypeError:
        return result


class AsanaDataSource:
    """
    Unified datasource wrapping core Asana APIs. Accepts an existing ApiClient.

    client: asana.ApiClient created by your asana2.AsanaRESTClientViaToken/OAuth.
    return_iterators: if True, return raw SDK iterators; else materialize to list when iterable.
    """

    def __init__(self, client: asana.ApiClient, return_iterators: bool = True) -> None:
        self._client = client
        self._return_iterators = return_iterators

        # Instantiate API groups once
        self.tasks = asana.TasksApi(client)
        self.projects = asana.ProjectsApi(client)
        self.users = asana.UsersApi(client)
        self.workspaces = asana.WorkspacesApi(client)
        self.teams = asana.TeamsApi(client)
        self.sections = asana.SectionsApi(client)
        self.tags = asana.TagsApi(client)
        self.attachments = asana.AttachmentsApi(client)
        self.stories = asana.StoriesApi(client)
        self.events = asana.EventsApi(client)

        self.task_templates = asana.TaskTemplatesApi(client)
        self.custom_fields = asana.CustomFieldsApi(client)
        self.custom_field_settings = asana.CustomFieldSettingsApi(client)
        self.reactions = asana.ReactionsApi(client)

        self.project_memberships = asana.ProjectMembershipsApi(client)
        self.project_statuses = asana.ProjectStatusesApi(client)
        self.project_templates = asana.ProjectTemplatesApi(client)
        self.project_briefs = asana.ProjectBriefsApi(client)
        self.portfolio_memberships = asana.PortfolioMembershipsApi(client)
        self.portfolios = asana.PortfoliosApi(client)

        self.goals = asana.GoalsApi(client)
        self.goal_relationships = asana.GoalRelationshipsApi(client)
        self.status_updates = asana.StatusUpdatesApi(client)
        self.organization_exports = asana.OrganizationExportsApi(client)
        self.jobs = asana.JobsApi(client)
        self.exports = asana.ExportsApi(client)
        self.rules = asana.RulesApi(client)
        self.audit_logs = asana.AuditLogAPIApi(client)
        self.batch = asana.BatchAPIApi(client)

    # ---------------------------
    # TASKS (27 endpoints)
    # Match YAML examples: positional args + opts dict.
    # ---------------------------

    def add_dependencies_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.add_dependencies_for_task(
                    body, task_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"add_dependencies_for_task failed: {e}")

    def add_dependents_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.add_dependents_for_task(
                    body, task_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"add_dependents_for_task failed: {e}")

    def add_followers_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.add_followers_for_task(
                    body, task_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"add_followers_for_task failed: {e}")

    def add_project_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.add_project_for_task(
                    body, task_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"add_project_for_task failed: {e}")

    def add_tag_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.add_tag_for_task(body, task_gid)
        except ApiException as e:
            raise RuntimeError(f"add_tag_for_task failed: {e}")

    def create_subtask_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.create_subtask_for_task(
                    body, task_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"create_subtask_for_task failed: {e}")

    def create_task(
        self,
        body: Dict,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.create_task(body, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"create_task failed: {e}")

    def delete_task(self, task_gid: str, headers: Optional[Dict[str, str]] = None):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.delete_task(task_gid)
        except ApiException as e:
            raise RuntimeError(f"delete_task failed: {e}")

    def duplicate_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.duplicate_task(body, task_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"duplicate_task failed: {e}")

    def get_dependencies_for_task(
        self,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.tasks.get_dependencies_for_task(task_gid, _clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_dependencies_for_task failed: {e}")

    def get_dependents_for_task(
        self,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.tasks.get_dependents_for_task(task_gid, _clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_dependents_for_task failed: {e}")

    def get_subtasks_for_task(
        self,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.tasks.get_subtasks_for_task(task_gid, _clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_subtasks_for_task failed: {e}")

    def get_task(
        self,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.get_task(task_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"get_task failed: {e}")

    def get_task_for_custom_id(
        self,
        custom_task_id: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.get_task_for_custom_id(
                    custom_task_id, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"get_task_for_custom_id failed: {e}")

    def get_tasks(
        self, opts: Optional[Dict] = None, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.tasks.get_tasks(_clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_tasks failed: {e}")

    def get_tasks_for_project(
        self,
        project_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.tasks.get_tasks_for_project(project_gid, _clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_tasks_for_project failed: {e}")

    def get_tasks_for_section(
        self,
        section_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.tasks.get_tasks_for_section(section_gid, _clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_tasks_for_section failed: {e}")

    def get_tasks_for_tag(
        self,
        tag_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.tasks.get_tasks_for_tag(tag_gid, _clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_tasks_for_tag failed: {e}")

    def get_tasks_for_user_task_list(
        self,
        user_task_list_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.tasks.get_tasks_for_user_task_list(
                    user_task_list_gid, _clean_opts(opts)
                )
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_tasks_for_user_task_list failed: {e}")

    def remove_dependencies_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.remove_dependencies_for_task(
                    body, task_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"remove_dependencies_for_task failed: {e}")

    def remove_dependents_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.remove_dependents_for_task(
                    body, task_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"remove_dependents_for_task failed: {e}")

    def remove_follower_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.remove_follower_for_task(
                    body, task_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"remove_follower_for_task failed: {e}")

    def remove_project_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.remove_project_for_task(
                    body, task_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"remove_project_for_task failed: {e}")

    def remove_tag_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.remove_tag_for_task(body, task_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"remove_tag_for_task failed: {e}")

    def search_tasks_for_workspace(
        self,
        workspace_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        # This returns a search iterator. Respect return_iterators.
        try:
            with _temporary_headers(self._client, headers):
                res = self.tasks.search_tasks_for_workspace(
                    workspace_gid, _clean_opts(opts)
                )
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"search_tasks_for_workspace failed: {e}")

    def set_parent_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.set_parent_for_task(body, task_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"set_parent_for_task failed: {e}")

    def update_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tasks.update_task(body, task_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"update_task failed: {e}")

    # ---------------------------
    # ATTACHMENTS
    # ---------------------------

    def create_attachment_for_task(
        self,
        file: object,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        # file: a file-like object per Asana examples.
        try:
            with _temporary_headers(self._client, headers):
                return self.attachments.create_attachment_for_task(
                    file, task_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"create_attachment_for_task failed: {e}")

    def get_attachment(
        self,
        attachment_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.attachments.get_attachment(
                    attachment_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"get_attachment failed: {e}")

    def delete_attachment(
        self, attachment_gid: str, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.attachments.delete_attachment(attachment_gid)
        except ApiException as e:
            raise RuntimeError(f"delete_attachment failed: {e}")

    def get_attachments_for_task(
        self,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.attachments.get_attachments_for_task(
                    task_gid, _clean_opts(opts)
                )
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_attachments_for_task failed: {e}")

    # ---------------------------
    # STORIES (comments)
    # ---------------------------

    def create_story_for_task(
        self,
        body: Dict,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.stories.create_story_for_task(body, task_gid)
        except ApiException as e:
            raise RuntimeError(f"create_story_for_task failed: {e}")

    def get_stories_for_task(
        self,
        task_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.stories.get_stories_for_task(task_gid, _clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_stories_for_task failed: {e}")

    def get_story(
        self,
        story_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.stories.get_story(story_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"get_story failed: {e}")

    def update_story(
        self, body: Dict, story_gid: str, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.stories.update_story(body, story_gid)
        except ApiException as e:
            raise RuntimeError(f"update_story failed: {e}")

    def delete_story(self, story_gid: str, headers: Optional[Dict[str, str]] = None):
        try:
            with _temporary_headers(self._client, headers):
                return self.stories.delete_story(story_gid)
        except ApiException as e:
            raise RuntimeError(f"delete_story failed: {e}")

    # ---------------------------
    # EVENTS
    # ---------------------------

    def get_events(
        self,
        resource: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        # Supports sync token via opts['sync']
        try:
            with _temporary_headers(self._client, headers):
                res = self.events.get_events(resource, _clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_events failed: {e}")

    # ---------------------------
    # PROJECTS (short, high-usage surface)
    # ---------------------------

    def create_project(
        self,
        body: Dict,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.projects.create_project(body, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"create_project failed: {e}")

    def get_project(
        self,
        project_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.projects.get_project(project_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"get_project failed: {e}")

    def update_project(
        self,
        body: Dict,
        project_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.projects.update_project(
                    body, project_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"update_project failed: {e}")

    def delete_project(
        self, project_gid: str, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.projects.delete_project(project_gid)
        except ApiException as e:
            raise RuntimeError(f"delete_project failed: {e}")

    def get_projects(
        self, opts: Optional[Dict] = None, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.projects.get_projects(_clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_projects failed: {e}")

    # ---------------------------
    # USERS
    # ---------------------------

    def get_user(
        self,
        user_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.users.get_user(user_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"get_user failed: {e}")

    def get_users(
        self, opts: Optional[Dict] = None, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.users.get_users(_clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_users failed: {e}")

    def get_users_for_workspace(
        self,
        workspace_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.users.get_users_for_workspace(
                    workspace_gid, _clean_opts(opts)
                )
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_users_for_workspace failed: {e}")

    # ---------------------------
    # WORKSPACES
    # ---------------------------

    def get_workspace(
        self,
        workspace_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.workspaces.get_workspace(workspace_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"get_workspace failed: {e}")

    def get_workspaces(self, headers: Optional[Dict[str, str]] = None):
        try:
            with _temporary_headers(self._client, headers):
                return self.workspaces.get_workspaces()
        except ApiException as e:
            raise RuntimeError(f"get_workspaces failed: {e}")

    def update_workspace(
        self, body: Dict, workspace_gid: str, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.workspaces.update_workspace(body, workspace_gid)
        except ApiException as e:
            raise RuntimeError(f"update_workspace failed: {e}")

    # ---------------------------
    # TEAMS
    # ---------------------------

    def get_teams_for_user(
        self,
        user_gid: str,
        organization: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.teams.get_teams_for_user(
                    user_gid, organization, _clean_opts(opts)
                )
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_teams_for_user failed: {e}")

    def get_team(
        self,
        team_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.teams.get_team(team_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"get_team failed: {e}")

    def get_teams_for_workspace(
        self,
        workspace_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.teams.get_teams_for_workspace(
                    workspace_gid, _clean_opts(opts)
                )
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_teams_for_workspace failed: {e}")

    # ---------------------------
    # SECTIONS
    # ---------------------------

    def create_section_for_project(
        self,
        body: Dict,
        project_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.sections.create_section_for_project(body, project_gid)
        except ApiException as e:
            raise RuntimeError(f"create_section_for_project failed: {e}")

    def get_section(
        self,
        section_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.sections.get_section(section_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"get_section failed: {e}")

    def update_section(
        self, body: Dict, section_gid: str, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.sections.update_section(body, section_gid)
        except ApiException as e:
            raise RuntimeError(f"update_section failed: {e}")

    def delete_section(
        self, section_gid: str, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.sections.delete_section(section_gid)
        except ApiException as e:
            raise RuntimeError(f"delete_section failed: {e}")

    # ---------------------------
    # TAGS
    # ---------------------------

    def create_tag(
        self,
        body: Dict,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tags.create_tag(body, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"create_tag failed: {e}")

    def get_tag(
        self,
        tag_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tags.get_tag(tag_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"get_tag failed: {e}")

    def update_tag(
        self, body: Dict, tag_gid: str, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.tags.update_tag(body, tag_gid)
        except ApiException as e:
            raise RuntimeError(f"update_tag failed: {e}")

    def delete_tag(self, tag_gid: str, headers: Optional[Dict[str, str]] = None):
        try:
            with _temporary_headers(self._client, headers):
                return self.tags.delete_tag(tag_gid)
        except ApiException as e:
            raise RuntimeError(f"delete_tag failed: {e}")

    def get_tags(
        self, opts: Optional[Dict] = None, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.tags.get_tags(_clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_tags failed: {e}")

    # ---------------------------
    # PROJECT ADJACENT
    # ---------------------------

    def get_project_memberships(
        self,
        project_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.project_memberships.get_project_memberships(
                    project_gid, _clean_opts(opts)
                )
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_project_memberships failed: {e}")

    def get_project_membership(
        self,
        project_membership_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.project_memberships.get_project_membership(
                    project_membership_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"get_project_membership failed: {e}")

    def get_project_statuses(
        self,
        project_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.project_statuses.get_project_statuses(
                    project_gid, _clean_opts(opts)
                )
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_project_statuses failed: {e}")

    def create_project_status(
        self,
        body: Dict,
        project_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.project_statuses.create_project_status(
                    body, project_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"create_project_status failed: {e}")

    def get_project_templates(
        self, opts: Optional[Dict] = None, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.project_templates.get_project_templates(_clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_project_templates failed: {e}")

    def get_project_template(
        self,
        project_template_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.project_templates.get_project_template(
                    project_template_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"get_project_template failed: {e}")

    def get_project_brief(
        self,
        project_brief_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.project_briefs.get_project_brief(
                    project_brief_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"get_project_brief failed: {e}")

    # ---------------------------
    # PORTFOLIOS
    # ---------------------------

    def get_portfolio(
        self,
        portfolio_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.portfolios.get_portfolio(portfolio_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"get_portfolio failed: {e}")

    def get_portfolios(
        self, opts: Optional[Dict] = None, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.portfolios.get_portfolios(_clean_opts(opts))
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_portfolios failed: {e}")

    def get_portfolio_memberships(
        self,
        portfolio_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.portfolio_memberships.get_portfolio_memberships(
                    portfolio_gid, _clean_opts(opts)
                )
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_portfolio_memberships failed: {e}")

    # ---------------------------
    # TASK TEMPLATES
    # ---------------------------

    def get_task_template(
        self,
        task_template_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.task_templates.get_task_template(
                    task_template_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"get_task_template failed: {e}")

    def instantiate_task(
        self,
        body: Dict,
        task_template_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        # Creates a task from a task template
        try:
            with _temporary_headers(self._client, headers):
                return self.task_templates.instantiate_task(
                    body, task_template_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"instantiate_task failed: {e}")

    # ---------------------------
    # CUSTOM FIELDS
    # ---------------------------

    def get_custom_field(
        self,
        custom_field_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.custom_fields.get_custom_field(
                    custom_field_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"get_custom_field failed: {e}")

    def update_custom_field(
        self,
        body: Dict,
        custom_field_gid: str,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.custom_fields.update_custom_field(body, custom_field_gid)
        except ApiException as e:
            raise RuntimeError(f"update_custom_field failed: {e}")

    # ---------------------------
    # CUSTOM FIELD SETTINGS
    # ---------------------------

    def get_custom_field_settings_for_project(
        self,
        project_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.custom_field_settings.get_custom_field_settings_for_project(
                    project_gid, _clean_opts(opts)
                )
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_custom_field_settings_for_project failed: {e}")

    # ---------------------------
    # REACTIONS
    # ---------------------------

    def add_reaction(
        self, body: Dict, story_gid: str, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.reactions.add_reaction(body, story_gid)
        except ApiException as e:
            raise RuntimeError(f"add_reaction failed: {e}")

    def remove_reaction(
        self, body: Dict, story_gid: str, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.reactions.remove_reaction(body, story_gid)
        except ApiException as e:
            raise RuntimeError(f"remove_reaction failed: {e}")

    # ---------------------------
    # GOALS
    # ---------------------------

    def create_goal(
        self,
        body: Dict,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.goals.create_goal(body, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"create_goal failed: {e}")

    def get_goal(
        self,
        goal_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.goals.get_goal(goal_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"get_goal failed: {e}")

    # ---------------------------
    # GOAL RELATIONSHIPS
    # ---------------------------

    def get_goal_relationship(
        self,
        goal_relationship_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.goal_relationships.get_goal_relationship(
                    goal_relationship_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"get_goal_relationship failed: {e}")

    # ---------------------------
    # STATUS UPDATES
    # ---------------------------

    def create_status_update(
        self,
        body: Dict,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.status_updates.create_status_update(body, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"create_status_update failed: {e}")

    def get_status_update(
        self,
        status_update_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.status_updates.get_status_update(
                    status_update_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"get_status_update failed: {e}")

    # ---------------------------
    # ORG EXPORTS
    # ---------------------------

    def create_organization_export(
        self, body: Dict, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.organization_exports.create_organization_export(body)
        except ApiException as e:
            raise RuntimeError(f"create_organization_export failed: {e}")

    def get_organization_export(
        self,
        organization_export_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.organization_exports.get_organization_export(
                    organization_export_gid, _clean_opts(opts)
                )
        except ApiException as e:
            raise RuntimeError(f"get_organization_export failed: {e}")

    # ---------------------------
    # JOBS
    # ---------------------------

    def get_job(
        self,
        job_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.jobs.get_job(job_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"get_job failed: {e}")

    # ---------------------------
    # EXPORTS
    # ---------------------------

    def create_export(self, body: Dict, headers: Optional[Dict[str, str]] = None):
        try:
            with _temporary_headers(self._client, headers):
                return self.exports.create_export(body)
        except ApiException as e:
            raise RuntimeError(f"create_export failed: {e}")

    def get_export(
        self,
        export_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.exports.get_export(export_gid, _clean_opts(opts))
        except ApiException as e:
            raise RuntimeError(f"get_export failed: {e}")

    # ---------------------------
    # RULES
    # ---------------------------

    def trigger_rule(
        self,
        body: Dict,
        rule_trigger_gid: str,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.rules.trigger_rule(body, rule_trigger_gid)
        except ApiException as e:
            raise RuntimeError(f"trigger_rule failed: {e}")

    # ---------------------------
    # AUDIT LOG
    # ---------------------------

    def get_audit_log_events(
        self,
        workspace_gid: str,
        opts: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        try:
            with _temporary_headers(self._client, headers):
                res = self.audit_logs.get_audit_log_events(
                    workspace_gid, _clean_opts(opts)
                )
                return _maybe_iterate(res, not self._return_iterators)
        except ApiException as e:
            raise RuntimeError(f"get_audit_log_events failed: {e}")

    # ---------------------------
    # BATCH
    # ---------------------------

    def create_batch_request(
        self, body: Dict, headers: Optional[Dict[str, str]] = None
    ):
        try:
            with _temporary_headers(self._client, headers):
                return self.batch.create_batch_request(body)
        except ApiException as e:
            raise RuntimeError(f"create_batch_request failed: {e}")
