# ruff: noqa
#!/usr/bin/env python3
"""
Monday.com API Code Generator

Generates:
- backend/python/app/sources/external/monday/monday.py
- backend/python/app/sources/external/monday/example.py

Monday is GraphQL-only, so operations are manually defined.
"""

import sys
from pathlib import Path
from typing import Dict, List

HTTP_ERROR_THRESHOLD = 400


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
                "variables": ["board_id"],
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
                "variables": ["board_id"],
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
    """Generates MondayDataSource and example.py"""

    def _generate_method(self, operation: Dict) -> str:
        name = operation["name"]
        query = operation["query"].strip()
        variables = operation.get("variables", [])

        args = ["self"]
        for v in variables:
            args.append(f"{v}: int")

        args_str = ",\n        ".join(args)

        if variables:
            variables_block = (
                "\n        variables = {\n"
                + "\n".join([f'            "{v}": {v},' for v in variables])
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
                message="{name} failed: " + str(e),
            )
"""

    def generate_datasource(self) -> str:
        header = """
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
        return """import asyncio

from app.sources.client.monday.monday import MondayClient
from app.sources.external.monday.monday import MondayDataSource


async def main() -> None:
    client = MondayClient()
    datasource = MondayDataSource(client)

    response = await datasource.get_boards()
    print(response)


if __name__ == "__main__":
    asyncio.run(main())
"""

    def write_files(self, base_dir: Path) -> None:
        base_dir.mkdir(parents=True, exist_ok=True)
        (base_dir / "monday.py").write_text(
            self.generate_datasource(), encoding="utf-8"
        )
        (base_dir / "example.py").write_text(
            self.generate_example(), encoding="utf-8"
        )


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> int:
    output_dir = Path("backend/python/app/sources/external/monday")
    generator = MondayCodeGenerator()
    generator.write_files(output_dir)

    print("✅ Generated MondayDataSource and example.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
