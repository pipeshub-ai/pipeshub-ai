"""
Figma API Example - Using FigmaClient

This example demonstrates how to use the FigmaClient to interact with the Figma API.
"""

import asyncio
import logging
import os
import sys
from typing import Any, Dict, Optional

from app.sources.client.figma.figma import FigmaClient, FigmaConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class FigmaExample:
    """Example class demonstrating FigmaClient usage."""

    def __init__(self, access_token: str) -> None:
        """Initialize with Figma personal access token."""
        self.access_token = access_token
        self.client: Optional[FigmaClient] = None

    async def __aenter__(self) -> "FigmaExample":
        """Set up the Figma client."""
        config = FigmaConfig(access_token=self.access_token)
        self.client = config.create_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Clean up resources."""
        if self.client:
            await self.client.close()

    async def get_me(self) -> Dict[str, Any]:
        """Get current user's information."""
        if not self.client:
            raise RuntimeError("Client not initialized")

        response = await self.client._make_request("/me", method="GET")
        return response.data if response.data else {}

    async def get_file(self, file_key: str) -> Dict[str, Any]:
        """Get file data."""
        if not self.client:
            raise RuntimeError("Client not initialized")

        response = await self.client._make_request(f"/files/{file_key}", method="GET")
        return response.data if response.data else {}

    async def get_team_projects(self, team_id: str) -> Dict[str, Any]:
        """Get all projects for a team."""
        if not self.client:
            raise RuntimeError("Client not initialized")

        response = await self.client._make_request(
            f"/teams/{team_id}/projects",
            method="GET"
        )
        return response.data if response.data else {}

    async def get_project_files(self, project_id: str) -> Dict[str, Any]:
        """Get all files in a project."""
        if not self.client:
            raise RuntimeError("Client not initialized")

        response = await self.client._make_request(
            f"/projects/{project_id}/files",
            method="GET"
        )
        return response.data if response.data else {}

    async def get_file_styles(self, file_key: str) -> Dict[str, Any]:
        """Get all styles from a file."""
        if not self.client:
            raise RuntimeError("Client not initialized")

        response = await self.client._make_request(
            f"/files/{file_key}/styles",
            method="GET"
        )
        return response.data if response.data else {}

    async def get_file_components(self, file_key: str) -> Dict[str, Any]:
        """Get all components from a file."""
        if not self.client:
            raise RuntimeError("Client not initialized")

        response = await self.client._make_request(
            f"/files/{file_key}/components",
            method="GET"
        )
        return response.data if response.data else {}


async def main() -> None:
    """Run the example."""
    # Get access token from environment variable
    access_token = os.getenv("FIGMA_ACCESS_TOKEN")
    if not access_token:
        print("Error: FIGMA_ACCESS_TOKEN environment variable not set")
        sys.exit(1)

    try:
        async with FigmaExample(access_token) as figma:
            # First, authenticate and show user info
            print("\n=== AUTHENTICATING TO FIGMA ===")
            user = await figma.get_me()
            print(f"✅ Successfully authenticated as: {user.get('email', 'N/A')}")
            print(f"👤 User ID: {user.get('id', 'N/A')}")

            # Now ask for additional inputs
            print("\n=== ENTER DETAILS ===")
            print("(Leave any field empty to skip)")

            team_id = input("\nEnter Team ID to list projects: ").strip()
            if team_id:
                print("\nFetching team projects...")
                team_projects = await figma.get_team_projects(team_id)
                projects = team_projects.get("projects", [])
                if projects:
                    print("\n📂 Available Projects:")
                    for project in projects:
                        print(f"- {project.get('name')} (ID: {project.get('id')})")
                else:
                    print("No projects found for this team or invalid Team ID")

            project_id = input("\nEnter Project ID to list files: ").strip()
            if project_id:
                print("\nFetching project files...")
                project_files = await figma.get_project_files(project_id)
                files = project_files.get("files", [])
                if files:
                    print("\n📄 Available Files:")
                    for file in files:
                        print(f"- {file.get('name')} (Key: {file.get('key')})")
                else:
                    print("No files found in this project or invalid Project ID")

            file_key = input("\nEnter File Key to view details: ").strip()
            if file_key:
                print("\nFetching file details...")

                file_data = await figma.get_file(file_key)
                print("\n📝 FILE DETAILS")
                print(f"Name: {file_data.get('name', 'N/A')}")
                print(f"Key: {file_key}")
                print(f"Last modified: {file_data.get('lastModified', 'N/A')}")
                print(f"Version: {file_data.get('version', 'N/A')}")

                # Get and display styles
                print("\n🎨 Fetching styles...")
                try:
                    styles = await figma.get_file_styles(file_key)
                    if (
                        isinstance(styles, dict)
                        and "meta" in styles
                        and isinstance(styles["meta"], dict)
                    ):
                        styles_list = styles["meta"].get("styles", [])
                        if styles_list:
                            print(f"\nFound {len(styles_list)} styles:")
                            for style in styles_list:
                                if isinstance(style, dict):
                                    style_type = style.get(
                                        "styleType", "UNKNOWN"
                                    ).title()
                                    print(
                                        f"- {style.get('name', 'Unnamed')} ({style_type}): {style.get('key')}"
                                    )
                        else:
                            print("No published styles found in this file.")
                    else:
                        print("No published styles found or invalid response format.")
                except Exception as e:
                    print(f"Could not fetch styles: {str(e)}")

                print("\n Fetching components...")
                try:
                    components = await figma.get_file_components(file_key)
                    if (
                        isinstance(components, dict)
                        and "meta" in components
                        and isinstance(components["meta"], dict)
                    ):
                        components_list = components["meta"].get("components", [])
                        if components_list:
                            print(f"\nFound {len(components_list)} components:")
                            for comp in components_list:
                                if isinstance(comp, dict):
                                    print(
                                        f"- {comp.get('name', 'Unnamed')} (Key: {comp.get('key', 'N/A')})"
                                    )
                        else:
                            print("No published components found in this file.")
                    else:
                        print(
                            "No published components found or invalid response format."
                        )
                except Exception as e:
                    print(f"Could not fetch components: {str(e)}")

            if not any([team_id, project_id, file_key]):
                print("\nNo additional actions selected. Exiting...")

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nCancelled by user")
        sys.exit(1)
