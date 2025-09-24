# ruff: noqa
import asyncio
import os

from app.sources.client.linear.linear import LinearClient, LinearTokenConfig
from app.sources.external.linear.linear import LinearDataSource


async def main() -> None:
    """Example usage of Linear Issues API."""
    LINEAR_API_TOKEN = os.getenv("LINEAR_API_TOKEN")

    if not LINEAR_API_TOKEN:
        print("Please set LINEAR_API_TOKEN environment variable")
        return

    # Initialize Linear client and data source
    print("Initializing Linear client...")
    client = LinearClient.build_with_config(LinearTokenConfig(token=LINEAR_API_TOKEN))
    data_source = LinearDataSource(client)
    
    try:
        # Validate connection
        print("Validating connection...")
        is_connected = await data_source.organization()
        if not is_connected:
            print("Failed to connect to Linear API")
            return
        print("Connected successfully!")

        # Get current user information
        print("\n=== Getting current user info ===")
        user_response = await data_source.viewer()
        if user_response.success:
            user_data = user_response.data.get("organization", {})
            print(f"Organization: {user_data.get('name')} (ID: {user_data.get('id')})")
            print(f"Organization URL Key: {user_data.get('urlKey')}")
        else:
            print(f"Failed to get user info: {user_response.message}")
            print("Note: Linear API may not support the viewer query in the current schema")

        # Get all teams
        print("\n=== Getting teams ===")
        teams_response = await data_source.teams(first=10)
        if teams_response.success:
            teams_data = teams_response.data.get("teams", {})
            teams = teams_data.get("nodes", [])
            print(f"Found {len(teams)} teams:")
            for team in teams[:3]:  # Show first 3 teams
                print(f"  - {team.get('name')} (ID: {team.get('id')}, Key: {team.get('key')})")
        else:
            print(f"Failed to get teams: {teams_response.message}")
            return

        # Get issues from the first team instead of searching (since search API is deprecated)
        print("\n=== Getting issues from first team ===")
        if teams and len(teams) > 0:
            first_team_id = teams[0].get('id')
            first_team_key = teams[0].get('key')
            print(f"Getting issues for team: {first_team_key}")
            
            issues_response = await data_source.issues(
                first=5,
                filter={"team": {"id": {"eq": first_team_id}}}
            )
            if issues_response.success:
                issues_data = issues_response.data.get("issues", {})
                found_issues = issues_data.get("nodes", [])
                print(f"Found {len(found_issues)} issues:")
                for issue in found_issues:
                    print(f"  - {issue.get('identifier')}: {issue.get('title')}")
                    print(f"    Status: {issue.get('state', {}).get('name')}")
                    print(f"    Assignee: {issue.get('assignee', {}).get('name', 'Unassigned')}")
            else:
                print(f"Failed to get issues: {issues_response.message}")
        else:
            print("No teams available to get issues from")

        # Create a new issue (commented out due to API limitations)
        print("\n=== Creating a new issue ===")
        print("Issue creation is currently disabled due to API limitations")
        new_issue_id = None
        new_issue_identifier = None
        
        # Get recent issues across all teams
        print("\n=== Getting recent issues (last 10) ===")
        recent_issues_response = await data_source.issues(
            first=10,
            orderBy="createdAt"
        )
        if recent_issues_response.success:
            recent_data = recent_issues_response.data.get("issues", {})
            recent_issues = recent_data.get("nodes", [])
            print(f"Recent issues:")
            for issue in recent_issues:
                created_at = issue.get('createdAt', '')[:10]  # Just the date part
                print(f"  - {issue.get('identifier')}: {issue.get('title')} ({created_at})")
        else:
            print(f"Failed to get recent issues: {recent_issues_response.message}")

        # Archive the test issue (uncomment to test)
        if new_issue_id:
            print(f"\n=== Archiving test issue {new_issue_identifier} ===")
            archive_response = await data_source.issueArchive(id=new_issue_id)
            if archive_response.success:
                print(f"Successfully archived issue {new_issue_identifier}")
            else:
                print(f"Failed to archive issue: {archive_response.message}")

        #Delete the test issue (uncomment to test - be careful!)
        if new_issue_id:
            print(f"\n=== Deleting test issue {new_issue_identifier} ===")
            delete_response = await data_source.issueDelete(id=new_issue_id)
            if delete_response.success:
                print(f"Successfully deleted issue {new_issue_identifier}")
            else:
                print(f"Failed to delete issue: {delete_response.message}")

    finally:
        # Properly close the client session
        await client.get_client().close()


if __name__ == "__main__":
    asyncio.run(main())