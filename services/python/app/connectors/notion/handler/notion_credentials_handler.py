import aiohttp
import jwt
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config.configuration_service import (
    DefaultEndpoints,
    Routes,
    TokenScopes,
    config_node_constants,
)


class NotionCredentialsHandler:
    def __init__(self, logger, config_service, arango_service):
        self.logger = logger
        self.service = None
        self.config_service = config_service
        self.arango_service = arango_service

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, Exception)),
        reraise=True,
    )

    async def get_notion_secret(self, org_id):
        # Prepare payload for credentials API
        payload = {"orgId": org_id, "scopes": [TokenScopes.FETCH_CONFIG.value]}

        secret_keys = await self.config_service.get_config(
            config_node_constants.SECRET_KEYS.value
        )
        scoped_jwt_secret = secret_keys.get("scopedJwtSecret")

        # Create JWT token
        jwt_token = jwt.encode(payload, scoped_jwt_secret, algorithm="HS256")

        headers = {"Authorization": f"Bearer {jwt_token}"}
        endpoints = await self.config_service.get_config(
            config_node_constants.ENDPOINTS.value
        )

        nodejs_endpoint = endpoints.get("cm").get("endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value)

        # Call credentials API
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{nodejs_endpoint}{Routes.NOTION_CONFIG.value}",
                json=payload,
                headers=headers,
            ) as response:
                if response.status != 200:
                    raise Exception(
                        f"Failed to fetch credentials: {await response.json()}"
                    )
                credentials_json = await response.json()

        return credentials_json
