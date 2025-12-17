
# NOTE:
# - Authentication headers are handled by HTTPClient
# - DataSource must not manage auth or tokens

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.monday.monday import MondayClient, MondayResponse

HTTP_ERROR_THRESHOLD = 400


class MondayDataSource:
    """Generated Monday.com DataSource"""

    def __init__(self, monday_client: MondayClient) -> None:
        self.http_client = monday_client.get_client()
        self._monday_client = monday_client
        self.base_url = monday_client.get_base_url().rstrip("/")

    def get_client(self) -> MondayClient:
        return self._monday_client

    async def get_boards(
        self,
    ) -> MondayResponse:

        variables = None

        payload = {
            "query": """query {
                  boards {
                    id
                    name
                  }
                }""",
            "variables": variables,
        }

        try:
            request = HTTPRequest(
                url=self.base_url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=payload,
            )
            response = await self.http_client.execute(request)

            success = response.status < HTTP_ERROR_THRESHOLD
            return MondayResponse(
                success=success,
                data=response.json() if response.text else None,
                message="get_boards succeeded" if success else "get_boards failed",
                error=response.text if not success else None,
            )
        except Exception as e:
            return MondayResponse(
                success=False,
                error=str(e),
                message="get_boards failed",
            )

    async def get_items(
        self,
        board_id: int,
    ) -> MondayResponse:

        variables = {
            "board_id": board_id,
        }

        payload = {
            "query": """query ($board_id: [Int]) {
                  boards(ids: $board_id) {
                    items {
                      id
                      name
                    }
                  }
                }""",
            "variables": variables,
        }

        try:
            request = HTTPRequest(
                url=self.base_url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=payload,
            )
            response = await self.http_client.execute(request)

            success = response.status < HTTP_ERROR_THRESHOLD
            return MondayResponse(
                success=success,
                data=response.json() if response.text else None,
                message="get_items succeeded" if success else "get_items failed",
                error=response.text if not success else None,
            )
        except Exception as e:
            return MondayResponse(
                success=False,
                error=str(e),
                message="get_items failed",
            )

    async def get_columns(
        self,
        board_id: int,
    ) -> MondayResponse:

        variables = {
            "board_id": board_id,
        }

        payload = {
            "query": """query ($board_id: [Int]) {
                  boards(ids: $board_id) {
                    columns {
                      id
                      title
                      type
                    }
                  }
                }""",
            "variables": variables,
        }

        try:
            request = HTTPRequest(
                url=self.base_url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=payload,
            )
            response = await self.http_client.execute(request)

            success = response.status < HTTP_ERROR_THRESHOLD
            return MondayResponse(
                success=success,
                data=response.json() if response.text else None,
                message="get_columns succeeded" if success else "get_columns failed",
                error=response.text if not success else None,
            )
        except Exception as e:
            return MondayResponse(
                success=False,
                error=str(e),
                message="get_columns failed",
            )

    async def get_users(
        self,
    ) -> MondayResponse:

        variables = None

        payload = {
            "query": """query {
                  users {
                    id
                    name
                    email
                  }
                }""",
            "variables": variables,
        }

        try:
            request = HTTPRequest(
                url=self.base_url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=payload,
            )
            response = await self.http_client.execute(request)

            success = response.status < HTTP_ERROR_THRESHOLD
            return MondayResponse(
                success=success,
                data=response.json() if response.text else None,
                message="get_users succeeded" if success else "get_users failed",
                error=response.text if not success else None,
            )
        except Exception as e:
            return MondayResponse(
                success=False,
                error=str(e),
                message="get_users failed",
            )
