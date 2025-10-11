# ruff: noqa
from __future__ import annotations

import os

from dotenv import load_dotenv
import uuid

from app.sources.client.gitlab.gitlab import GitLabClient, GitLabConfig, GitLabResponse
from app.sources.external.gitlab.gitlab_ import GitLabDataSource
from gitlab.exceptions import GitlabCreateError, GitlabGetError, GitlabError


def _print_status(title: str, res: GitLabResponse) -> None:
    print(f"\n== {title} ==")
    if not res.success:
        print("error:", res.error or res.message)
    else:
        print("ok")


def main() -> None:
    # Load .env if present
    load_dotenv()

    # Minimal envs
    token = os.getenv("GITLAB_TOKEN")
    private_project = os.getenv("GITLAB_PRIVATE_PROJECT")  # numeric ID or full path
    group_identifier = os.getenv("GITLAB_GROUP")  # numeric group ID or full path (org)

    if not token:
        raise RuntimeError("GITLAB_TOKEN is not set (load from .env or environment)")

    # Initialize client and datasource (default host: gitlab.com) with a reasonable timeout
    client = GitLabClient.build_with_config(
        GitLabConfig(
            token=token,
            url="https://gitlab.com",
            timeout=10.0,
        )
    )
    ds = GitLabDataSource(client)

    # 1) Public browse: fetch a small page of public projects (avoid get_all to prevent blocking)
    projects_res: GitLabResponse = ds.list_projects(search="gitlab", get_all=False)
    _print_status("List Public Projects (sample)", projects_res)
    if projects_res.success and projects_res.data:
        try:
            sample = projects_res.data[:5]
            names = [
                getattr(p, "path_with_namespace", getattr(p, "name", "<unknown>"))
                for p in sample
            ]
            print("projects:", names)
            # Pick one public project and list 5 commits (non-blocking)
            try:
                first_proj = sample[0]
                target_project = (
                    getattr(first_proj, "id", None)
                    or getattr(first_proj, "path_with_namespace", None)
                    or getattr(first_proj, "name_with_namespace", None)
                )
                commits_res: GitLabResponse = ds.list_commits(
                    target_project, get_all=False
                )
                _print_status(
                    f"List Commits (public project: {target_project})", commits_res
                )
                if commits_res.success and commits_res.data:
                    items = commits_res.data[:5]
                    rows = [
                        (
                            getattr(c, "short_id", getattr(c, "id", None)),
                            getattr(c, "title", None),
                        )
                        for c in items
                    ]
                    print("commits:", rows)
            except GitlabError as e:
                print("Error listing commits:", str(e))
        except Exception as e:
            print("Error listing commits:", str(e))
            pass

    # 2) Organizations equivalent: GitLab groups
    groups_res: GitLabResponse = ds.list_groups(get_all=False)
    _print_status("List Groups (organizations)", groups_res)
    if groups_res.success and groups_res.data:
        try:
            sample = groups_res.data[:5]
            names = [
                getattr(g, "full_path", getattr(g, "name", "<unknown>")) for g in sample
            ]
            print("groups:", names)
        except Exception:
            pass

    # 2b) If a specific group is provided, list projects in that group (org)
    if group_identifier:
        try:
            grp_res = ds.get_group(group_identifier)
            _print_status(f"Get Group ({group_identifier})", grp_res)
            if grp_res.success and grp_res.data:
                group_obj = grp_res.data
                try:
                    # list a small page to avoid blocking
                    projects_in_group = group_obj.projects.list(get_all=False)
                    names = [
                        getattr(
                            p, "path_with_namespace", getattr(p, "name", "<unknown>")
                        )
                        for p in projects_in_group[:10]
                    ]
                    print("group projects:", names)
                except GitlabError as e:
                    print("Error listing projects in group:", str(e))

                # pick one project in the group for actions
                if projects_in_group:
                    org_target_project = (
                        getattr(projects_in_group[0], "id", None)
                        or getattr(projects_in_group[0], "path_with_namespace", None)
                        or getattr(projects_in_group[0], "name_with_namespace", None)
                    )

                    # (a) create an issue in the selected org project
                    try:
                        org_issue_title = "Example issue in org project"
                        org_issue_desc = "Created by example script in group project."
                        org_issue_res: GitLabResponse = ds.create_issue(
                            org_target_project,
                            title=org_issue_title,
                            description=org_issue_desc,
                        )
                        _print_status(
                            f"Create Issue (group project: {org_target_project})",
                            org_issue_res,
                        )
                        # update then delete the created org issue (if created)
                        if org_issue_res.success and org_issue_res.data:
                            try:
                                issue = org_issue_res.data
                                issue_iid = getattr(issue, "iid", None)
                                if issue_iid is not None:
                                    upd_res = ds.update_issue(
                                        org_target_project,
                                        issue_iid,
                                        title=f"[UPDATED] {org_issue_title}",
                                    )
                                    _print_status(
                                        f"Update Issue (group project: {org_target_project})",
                                        upd_res,
                                    )
                                    del_res = ds.delete_issue(
                                        org_target_project, issue_iid
                                    )
                                    _print_status(
                                        f"Delete Issue (group project: {org_target_project})",
                                        del_res,
                                    )
                            except GitlabError as e:
                                print("Error updating/deleting org issue:", str(e))
                    except GitlabCreateError as e:
                        print("Failed to create issue in group project:", str(e))
                    except GitlabError as e:
                        print("GitLab error creating issue in group project:", str(e))

                    # (b) show 5 commits from the selected org project
                    try:
                        org_commits_res: GitLabResponse = ds.list_commits(
                            org_target_project, get_all=False
                        )
                        _print_status(
                            f"List Commits (group project: {org_target_project})",
                            org_commits_res,
                        )
                        if org_commits_res.success and org_commits_res.data:
                            rows = [
                                (
                                    getattr(c, "short_id", getattr(c, "id", None)),
                                    getattr(c, "title", None),
                                )
                                for c in org_commits_res.data[:5]
                            ]
                            print("commits:", rows)
                    except GitlabError as e:
                        print("Error listing commits in group project:", str(e))

                    # (c) create a blank project inside the group (private visibility)
                    try:
                        new_proj_name = f"ph-example-{uuid.uuid4().hex[:8]}"
                        create_proj_res = ds.create_project(
                            name=new_proj_name,
                            namespace_id=getattr(group_obj, "id", None),
                            visibility="private",
                        )
                        _print_status(
                            f"Create Blank Project in Group ({group_identifier})",
                            create_proj_res,
                        )
                    except GitlabError as e:
                        print("Error creating project in group:", str(e))
        except GitlabGetError as e:
            print("Group Not Found (GitLab):", str(e))
        except GitlabError as e:
            print("Error fetching group:", str(e))

    # 3) Private action: create a single issue in your private project (if provided)
    if private_project:
        # Resolve project first to give a clear error if the identifier is wrong
        try:
            proj_check = ds.get_project(private_project)
            _print_status(f"Resolve Project ({private_project})", proj_check)
            if not proj_check.success or not proj_check.data:
                print(
                    "Project not accessible. Use numeric ID or full path 'namespace/project' and ensure your token has access."
                )
                return
        except GitlabGetError as e:
            print("Project Not Found (GitLab):", str(e))
            print(
                "Tip: Provide numeric project ID or full path 'namespace/project' (e.g., 'your-username/pipeshub-ai-gitlab-datasource-test')."
            )
            return
        except GitlabError as e:
            print("Error fetching project:", str(e))
            return

        # List issues in the private project (non-blocking)
        try:
            issues_list_res: GitLabResponse = ds.list_issues(
                private_project, get_all=False
            )
            _print_status("List Issues (private project)", issues_list_res)
            if issues_list_res.success and issues_list_res.data:
                titles = [getattr(i, "title", None) for i in issues_list_res.data[:10]]
                print("issues:", titles)
        except GitlabError as e:
            print("Error listing issues:", str(e))

        title = "Example issue from script"
        description = "This is a test issue created by the example script. Please close if not needed."

        try:
            create_issue_res: GitLabResponse = ds.create_issue(
                private_project, title=title, description=description
            )
            _print_status(f"Create Issue ({private_project})", create_issue_res)
            if create_issue_res.success and create_issue_res.data:
                try:
                    issue = create_issue_res.data
                    issue_iid = getattr(issue, "iid", None)
                    print(
                        "created issue:",
                        issue_iid,
                        getattr(issue, "title", None),
                    )
                    if issue_iid is not None:
                        upd_priv = ds.update_issue(
                            private_project,
                            issue_iid,
                            title=f"[UPDATED] {title}",
                        )
                        _print_status("Update Issue (private project)", upd_priv)
                        del_priv = ds.delete_issue(private_project, issue_iid)
                        _print_status("Delete Issue (private project)", del_priv)
                except Exception:
                    pass
        except GitlabCreateError as e:
            print("Failed to create issue (GitLab):", str(e))
        except GitlabError as e:
            print("GitLab error creating issue:", str(e))
    else:
        print(
            "\nNote: Set GITLAB_PRIVATE_PROJECT to create an issue in your private project."
        )


if __name__ == "__main__":
    main()
