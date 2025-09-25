# ruff: noqa: E501

import os

from dotenv import load_dotenv

from app.sources.client.github.github import GitHubClient, GitHubConfig
from app.sources.external.github.github_ import GitHubDataSource


def print_result(title: str, res) -> None:
    print(f"\n== {title} ==")
    if not res.success:
        print("error:", res.error)
        return
    print("ok")
    print(res.data)


def main() -> None:
    load_dotenv()

    token = os.getenv("GITHUB_PAT")
    if not token:
        raise RuntimeError("GITHUB_PAT is not set (load from .env or environment)")

    # Initialize client and datasource
    client = GitHubClient.build_with_config(GitHubConfig(token=token))
    ds = GitHubDataSource(client)

    # Public fixtures (exist on GitHub)
    user_login = "octocat"
    owner = "octocat"
    repo = "Hello-World"

    # Authenticated user
    auth_res = ds.get_authenticated()
    print_result("Authenticated User", auth_res)
    if auth_res.success and hasattr(auth_res.data, "login"):
        print("login:", auth_res.data.login)

    # Fetch a specific user
    user_res = ds.get_user(user_login)
    print_result(f"Get User ({user_login})", user_res)
    if user_res.success and hasattr(user_res.data, "login"):
        print("login:", user_res.data.login)

    # Get a repository
    repo_res = ds.get_repo(owner, repo)
    print_result(f"Get Repo ({owner}/{repo})", repo_res)
    if repo_res.success and hasattr(repo_res.data, "full_name"):
        print("full_name:", repo_res.data.full_name)

    # List repos for a user
    repos_res = ds.list_user_repos(user_login)
    print_result(f"List Repos for {user_login}", repos_res)
    if repos_res.success:
        names = [r.full_name for r in (repos_res.data or [])][:10]
        print("sample repos:", names)

    # List pulls (public repo has a few historical PRs)
    pulls_res = ds.list_pulls(owner, repo)
    print_result("List Pull Requests", pulls_res)
    if pulls_res.success:
        titles = [p.title for p in (pulls_res.data or [])][:10]
        print("sample PRs:", titles)

    # List issues
    issues_res = ds.list_issues(owner, repo)
    print_result("List Issues", issues_res)
    if issues_res.success:
        titles = [i.title for i in (issues_res.data or [])][:10]
        print("sample issues:", titles)

    # List branches
    branches_res = ds.list_branches(owner, repo)
    print_result("List Branches", branches_res)
    if branches_res.success:
        names = [b.name for b in (branches_res.data or [])]
        print("branches:", names)

    # List tags
    tags_res = ds.list_tags(owner, repo)
    print_result("List Tags", tags_res)
    if tags_res.success:
        names = [t.name for t in (tags_res.data or [])]
        print("tags:", names)

    # Rate limit
    rate_res = ds.get_rate_limit()
    print_result("Rate Limit", rate_res)

    # Safe example for creating an issue (commented out)
    # Note: Only works if you have write access to the repo

    create_issue_res = ds.create_issue("uncleSlayer", "file_keeper", "Test issue from API", "This is a body")
    print_result("Create Issue", create_issue_res)


if __name__ == "__main__":
    main()
