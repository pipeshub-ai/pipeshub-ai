# ruff: noqa

import os

from dotenv import load_dotenv
from github.GithubException import GithubException

from app.sources.client.github.github import GitHubClient, GitHubConfig
from app.sources.external.github.github_ import GitHubDataSource, GitHubResponse
from github.AuthenticatedUser import AuthenticatedUser  # type: ignore


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

    # In order to test Github organization specific APIs, you need to set the GITHUB_ORGANIZATION environment variable
    organization = os.getenv("GITHUB_ORGANIZATION")

    if not token:
        raise RuntimeError("GITHUB_PAT is not set (load from .env or environment)")

    # Initialize client and datasource
    client = GitHubClient.build_with_config(GitHubConfig(token=token))
    ds = GitHubDataSource(client)

    # Authenticated user
    auth_res: GitHubResponse[AuthenticatedUser] = ds.get_authenticated()
    print_result("Authenticated User", auth_res)

    print("\n=== GitHubResponse Fields ===")
    print(f"success: {auth_res.success}")
    print(f"data: {auth_res.data}")
    print(f"error: {auth_res.error}")
    print(f"message: {auth_res.message}")

    if auth_res.success and auth_res.data:
        print("\n=== AuthenticatedUser Fields ===")
        user = auth_res.data

        # List of possible fields to check
        fields_to_check = [
            "login",
            "id",
            "node_id",
            "avatar_url",
            "gravatar_id",
            "url",
            "html_url",
            "followers_url",
            "following_url",
            "gists_url",
            "starred_url",
            "subscriptions_url",
            "organizations_url",
            "repos_url",
            "events_url",
            "received_events_url",
            "type",
            "site_admin",
            "name",
            "company",
            "blog",
            "location",
            "email",
            "hireable",
            "bio",
            "twitter_username",
            "public_repos",
            "public_gists",
            "followers",
            "following",
            "created_at",
            "updated_at",
            "private_gists",
            "total_private_repos",
            "owned_private_repos",
            "disk_usage",
            "collaborators",
            "two_factor_authentication",
            "plan",
        ]

        for field in fields_to_check:
            try:
                value = getattr(user, field, None)
                print(f"{field}: {value}")
            except AttributeError:
                print(f"{field}: <AttributeError - field not available>")

        # Print plan details if available
        if hasattr(user, "plan") and user.plan:
            print(f"\n=== Plan Details ===")
            plan_fields = ["name", "space", "private_repos", "collaborators"]
            for field in plan_fields:
                try:
                    value = getattr(user.plan, field, None)
                    print(f"  plan.{field}: {value}")
                except AttributeError:
                    print(f"  plan.{field}: <AttributeError - field not available>")

    print("\n****************************")

    # Extract user_login, owner, and repo from authenticated user data
    user_login = auth_res.data.login
    owner = user_login  # Use the same user as owner
    repo = "pipeshub-ai"  # Use this repository for testing, fork this repository to your account and give a star :D

    # Fetch a specific user
    user_res = ds.get_user(user_login)
    print_result(f"Get User ({user_login})", user_res)
    if user_res.success and user_res.data:
        print("login:", user_res.data.login)

    # Get a repository
    repo_res = ds.get_repo(owner, repo)
    print_result(f"Get Repo ({owner}/{repo})", repo_res)
    if repo_res.success and repo_res.data:
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

    # List pending invitations
    invitations_res = ds.list_pending_invitations(owner, repo)
    print_result("List Pending Invitations", invitations_res)
    if invitations_res.success:
        names = [i.login for i in (invitations_res.data or [])]
        print("invitations:", names)

    # # List Dependabot alerts
    # alerts_res = ds.list_dependabot_alerts(owner, repo)
    # print_result("List Dependabot Alerts", alerts_res)
    # if alerts_res.success:
    #     names = [a.number for a in (alerts_res.data or [])]
    #     print("alerts:", names)

    # # Get Dependabot alert
    # alert_res = ds.get_dependabot_alert(owner, repo, 1)
    # print_result("Get Dependabot Alert", alert_res)
    # if alert_res.success:
    #     print("alert:", alert_res.data)

    # Get enterprise organization info
    if organization:
        organizations_res = ds.get_organization(organization)
        print_result("Get Organization", organizations_res)

        if organizations_res.success and organizations_res.data:
            print("\n=== Organization Fields ===")
            org = organizations_res.data
            org_fields_to_check = [
                "login",
                "id",
                "node_id",
                "name",
                "description",
                "url",
                "html_url",
                "blog",
                "location",
                "email",
                "created_at",
                "updated_at",
            ]
            for field in org_fields_to_check:
                try:
                    value = getattr(org, field, None)
                    print(f"{field}: {value}")
                except AttributeError:
                    print(f"{field}: <AttributeError - field not available>")

            repos = org.get_repos()
            if repos:
                print("\n=== List of Repos in Organization ===")
                for repo in repos:
                    print("name:", getattr(repo, "name", None))
                    try:
                        issues = [issue.title for issue in repo.get_issues()]
                        print("list of issues:", issues)
                    except GithubException as e:
                        print(f"list of issues: <GithubException - issues not found: {e}")
                    except Exception as e:
                        print(f"list of issues: <Error fetching issues: {e}")


if __name__ == "__main__":
    main()
