# ruff: noqa
#!/usr/bin/env python3
"""
Monday.com API Code Generator

Generates:
- backend/python/app/sources/external/monday/monday.py
- backend/python/app/sources/external/monday/example.py
- backend/python/app/sources/external/monday/example_build_from_services.py

DESIGN RULES:
- Authentication handled ONLY by MondayClient
- DataSource never manages auth or tokens
- Generator never injects Authorization headers
"""

import sys
from pathlib import Path
from typing import Dict, List

HTTP_ERROR_THRESHOLD = 400


# =============================================================================
# PATH RESOLUTION (THIS FIXES YOUR ISSUE)
# =============================================================================

# File location: backend/python/code-generator/monday.py
THIS_FILE = Path(__file__).resolve()

# Repo root = ../../../..
REPO_ROOT = THIS_FILE.parents[3]

# Correct output directory
OUTPUT_DIR = (
    REPO_ROOT
    / "backend"
    / "python"
    / "app"
    / "sources"
    / "external"
    / "monday"
)


# =============================================================================
# GRAPHQL OPERATION DEFINITIONS
# =============================================================================

class MondayAPIDefinition:
    """Manually defined Monday GraphQL operations."""

    @staticmethod
    def get_operations() -> List[Dict]:
        return [
            {
                "name": "get_boards",
                "query": """
                query {
                  boards {
                    id
                    name
                  }
                }
                """,
                "variables": [],
            },
            {
                "name": "get_items",
                "query": """
                query ($board_id: [Int]) {
                  boards(ids: $board_id) {
                    items {
                      id
                      name
                    }
                  }
                }
                """,
                "variables": [
                    {"name": "board_id", "type": "int"},
                ],
            },
            {
                "name": "get_columns",
                "query": """
                query ($board_id: [Int]) {
                  boards(ids: $board_id) {
                    columns {
                      id
                      title
                      type
                    }
                  }
                }
                """,
                "variables": [
                    {"name": "board_id", "type": "int"},
                ],
            },
            {
                "name": "get_users",
                "query": """
                query {
                  users {
                    id
                    name
                    email
                  }
                }
                """,
                "variables": [],
            },
        ]


# =============================================================================
# CODE GENERATOR
# =============================================================================

class MondayCodeGenerator:
    """Generates MondayDataSource and example files"""

    def _generate_method(self, operation: Dict) -> str:
        name = operation["name"]
        query = operation["query"].strip()
        variables = operation.get("variables", [])

        # Method signature
        args = ["self"]
        for v in variables:
            args.append(f"{v['name']}: {v['type']}")

        args_str = ",\n        ".join(args)

        # Variables payload
        if variables:
            variables_block = (
                "\n        variables = {\n"
                + "\n".join(
                    [f'            "{v["name"]}": {v["name"]},' for v in variables]
                )
                + "\n        }\n"
            )
        else:
            variables_block = "\n        variables = None\n"

        return f"""
    async def {name}(
        {args_str}
    ) -> MondayResponse:
{variables_block}
        payload = {{
            "query": \"\"\"{query}\"\"\",
            "variables": variables,
        }}

        try:
            request = HTTPRequest(
                url=self.base_url,
                method="POST",
                headers={{"Content-Type": "application/json"}},
                body=payload,
            )
            response = await self.http_client.execute(request)

            success = response.status < HTTP_ERROR_THRESHOLD
            return MondayResponse(
                success=success,
                data=response.json() if response.text else None,
                message="{name} succeeded" if success else "{name} failed",
                error=response.text if not success else None,
            )
        except Exception as e:
            return MondayResponse(
                success=False,
                error=str(e),
                message="{name} failed",
            )
"""

    def generate_datasource(self) -> str:
        header = """
# NOTE:
# - Authentication headers are handled by HTTPClient
# - DataSource must not manage auth or tokens

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.monday.monday import MondayClient, MondayResponse

HTTP_ERROR_THRESHOLD = 400


class MondayDataSource:
    \"\"\"Generated Monday.com DataSource\"\"\"

    def __init__(self, monday_client: MondayClient) -> None:
        self.http_client = monday_client.get_client()
        self._monday_client = monday_client
        self.base_url = monday_client.get_base_url().rstrip("/")

    def get_client(self) -> MondayClient:
        return self._monday_client
"""
        methods = ""
        for op in MondayAPIDefinition.get_operations():
            methods += self._generate_method(op)

        return header + methods

    def generate_example(self) -> str:
        return """import os
import asyncio

from app.sources.client.monday.monday import MondayClient, MondayConfig
from app.sources.external.monday.monday import MondayDataSource


async def main() -> None:
    config = MondayConfig(
        base_url=os.environ["MONDAY_BASE_URL"],
        token=os.environ["MONDAY_TOKEN"],
    )

    client = MondayClient.build_with_config(config)
    datasource = MondayDataSource(client)

    response = await datasource.get_boards()
    print(response)


if __name__ == "__main__":
    asyncio.run(main())
"""

    def generate_example_build_from_services(self) -> str:
        return """import asyncio
import logging

from app.config.configuration_service import ConfigurationService
from app.sources.client.monday.monday import MondayClient
from app.sources.external.monday.monday import MondayDataSource

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    config_service = ConfigurationService()

    client = await MondayClient.build_from_services(
        logger=logger,
        config_service=config_service,
    )

    datasource = MondayDataSource(client)

    response = await datasource.get_boards()

    if response.success:
        logger.info("Successfully fetched boards")
        logger.info(response.data)
    else:
        logger.error(f"Failed to fetch boards: {response.error}")


if __name__ == "__main__":
    asyncio.run(main())
"""

    def write_files(self) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        (OUTPUT_DIR / "monday.py").write_text(
            self.generate_datasource(), encoding="utf-8"
        )
        (OUTPUT_DIR / "example.py").write_text(
            self.generate_example(), encoding="utf-8"
        )
        (OUTPUT_DIR / "example_build_from_services.py").write_text(
            self.generate_example_build_from_services(), encoding="utf-8"
        )


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> int:
    generator = MondayCodeGenerator()
    generator.write_files()

    print(f"✅ Generated Monday files at: {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
