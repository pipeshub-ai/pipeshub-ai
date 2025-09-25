# ruff: noqa: PGH004
import asyncio
import os
import sys

from dotenv import load_dotenv # type: ignore

load_dotenv()

from app.sources.client.github.github import (
    GitHubClient,
    GitHubClientViaToken,
)
from app.sources.external.github.github_ import GitHubDataSource


async def main() -> None:
    """Example usage of GitHub API using GitHubDataSource."""
    token = os.getenv("GITHUB_PAT")
    print("token", token)
    if not token:
        print("Error: token environment variable not set.", file=sys.stderr)
        sys.exit(1)

    print("Initializing GitHub client...")
    raw_client = GitHubClientViaToken(token=token)
    raw_client.create_client()
    client = GitHubClient(raw_client)

    data_source = GitHubDataSource(client)

    try:
        # Method 1: Get authenticated user info
        print("\n=== Method 1: Getting authenticated user info ===")
        login_info = await data_source.users_get_authenticated()
        print(login_info.data)

        # Method 2: List user repositories
        print("\n=== Method 2: Listing user repositories ===")
        user_repos = await data_source.repos_list_for_authenticated_user()
        if user_repos.success and user_repos.data:
            repos_list = (
                user_repos.data
                if isinstance(user_repos.data, list)
                else [user_repos.data]
            )
            print(f"Found {len(repos_list)} repositories:")
            for repo in repos_list[:3]:
                print(
                    f"  - {repo.get('full_name', 'N/A')}: {repo.get('description', 'No description')}",
                )
        else:
            print(f"Error listing repositories: {user_repos.error}")

        # Method 3: Get specific repository
        print("\n=== Method 3: Getting repository by owner/repo name ===")
        repo_info = await data_source.repos_get(owner="uncleSlayer", repo="file_keeper")
        if repo_info.success:
            repo_data = repo_info.data
            print(f"Repository: {repo_data.get('full_name', 'N/A')}")
            print(f"Description: {repo_data.get('description', 'No description')}")
            print(f"Stars: {repo_data.get('stargazers_count', 0)}")
        else:
            print(f"Error getting repository: {repo_info.error}")

        # Method 4: Get user by username
        print("\n=== Method 4: Getting user by username ===")
        user_info = await data_source.users_get_by_username(username="uncleSlayer")
        if user_info.success and user_info.data:
            user_data = user_info.data
            print(f"User: {user_data.get('login', 'N/A')}")
            print(f"Name: {user_data.get('name', 'No name')}")
            print(f"Bio: {user_data.get('bio', 'No bio')}")
            print(f"Public repos: {user_data.get('public_repos', 0)}")
            print(f"Followers: {user_data.get('followers', 0)}")
        else:
            print(f"Error getting user: {user_info.error}")

        # Method 5: Get organization info
        print("\n=== Method 5: Getting organization info ===")
        org_info = await data_source.orgs_get(org="github")
        if org_info.success and org_info.data:
            org_data = org_info.data
            print(f"Organization: {org_data.get('login', 'N/A')}")
            print(f"Name: {org_data.get('name', 'No name')}")
            print(f"Description: {org_data.get('description', 'No description')}")
            print(f"Public repos: {org_data.get('public_repos', 0)}")
            print(f"Location: {org_data.get('location', 'Not specified')}")
        else:
            print(f"Error getting organization: {org_info.error}")

    except Exception as e:
        print(f"Error occurred: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
