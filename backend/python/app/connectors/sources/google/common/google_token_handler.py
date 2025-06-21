from enum import Enum

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
from app.config.utils.named_constants.http_status_code_constants import HttpStatusCode


class CredentialKeys(Enum):
    CLIENT_ID = "clientId"
    CLIENT_SECRET = "clientSecret"
    ACCESS_TOKEN = "access_token"
    REFRESH_TOKEN = "refresh_token"

class GoogleTokenHandler:
    def __init__(self, logger, config_service, arango_service) -> None:
        self.logger = logger
        self.token_expiry = None
        self.service = None
        self.config_service = config_service
        self.arango_service = arango_service

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, Exception)),
        reraise=True,
    )
    async def get_individual_token(self, org_id, user_id) -> dict:
        # Prepare payload for credentials API
        payload = {
            "orgId": org_id,
            "userId": user_id,
            "scopes": [TokenScopes.FETCH_CONFIG.value],
        }

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

        # Fetch credentials from API
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{nodejs_endpoint}{Routes.INDIVIDUAL_CREDENTIALS.value}",
                json=payload,
                headers=headers,
            ) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    raise Exception(
                        f"Failed to fetch credentials: {await response.json()}"
                    )
                creds_data = await response.json()
                self.logger.info(
                    "🚀 Fetched individual credentials for Google"
                )

        return creds_data

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, Exception)),
        reraise=True,
    )
    async def refresh_token(self, org_id, user_id) -> None:
        """Refresh the access token"""
        try:
            self.logger.info("🔄 Refreshing access token")

            payload = {
                "orgId": org_id,
                "userId": user_id,
                "scopes": [TokenScopes.FETCH_CONFIG.value],
            }
            secret_keys = await self.config_service.get_config(
                config_node_constants.SECRET_KEYS.value
            )
            scoped_jwt_secret = secret_keys.get("scopedJwtSecret")

            jwt_token = jwt.encode(payload, scoped_jwt_secret, algorithm="HS256")

            headers = {"Authorization": f"Bearer {jwt_token}"}

            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            nodejs_endpoint = endpoints.get("cm").get("endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value)

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{nodejs_endpoint}{Routes.INDIVIDUAL_REFRESH_TOKEN.value}",
                    json=payload,
                    headers=headers,
                ) as response:
                    if response.status != HttpStatusCode.SUCCESS.value:
                        raise Exception(
                            f"Failed to refresh token: {await response.json()}"
                        )

                    await response.json()

            self.logger.info("✅ Successfully refreshed access token")

        except Exception as e:
            self.logger.error(f"❌ Failed to refresh token: {str(e)}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, Exception)),
        reraise=True,
    )
    async def get_enterprise_token(self, org_id) -> dict:
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
                f"{nodejs_endpoint}{Routes.BUSINESS_CREDENTIALS.value}",
                json=payload,
                headers=headers,
            ) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    raise Exception(
                        f"Failed to fetch credentials: {await response.json()}"
                    )
                credentials_json = await response.json()

        return credentials_json
