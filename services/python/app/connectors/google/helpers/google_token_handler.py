import os
import jwt
import aiohttp
from app.config.configuration_service import config_node_constants, Routes, TokenScopes
from app.utils.logger import create_logger

logger = create_logger('google_token_handler')

class GoogleTokenHandler:
    def __init__(self, config_service, arango_service):
        self.token_expiry = None
        self.service = None
        self.config_service = config_service
        self.arango_service = arango_service
        
    async def get_individual_token(self, org_id, user_id):
        # Prepare payload for credentials API
        payload = {
            "orgId": org_id,
            "userId": user_id,
            "scopes": [TokenScopes.FETCH_CONFIG.value]
        }
        
        # Create JWT token
        jwt_token = jwt.encode(
            payload,
            os.getenv('SCOPED_JWT_SECRET'),
            algorithm='HS256'
        )
        logger.info(f"🚀 JWT Token: {jwt_token}")
        
        headers = {
            "Authorization": f"Bearer {jwt_token}"
        }
        
        nodejs_config = await self.config_service.get_config(config_node_constants.CONFIGURATION_MANAGER.value)
        nodejs_endpoint = nodejs_config.get('endpoint')
        logger.info(f"🚀 Nodejs Endpoint: {nodejs_endpoint}")
        
        # Fetch credentials from API
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{nodejs_endpoint}{Routes.INDIVIDUAL_CREDENTIALS.value}",
                json=payload,
                headers=headers
            ) as response:
                if response.status != 200:
                    raise Exception(f"Failed to fetch credentials: {await response.json()}")
                creds_data = await response.json()
                logger.info("🚀 Fetch refreshed access token response: %s", creds_data)
                        
        return creds_data

    async def refresh_token(self, org_id, user_id):
        """Refresh the access token"""
        try:
            logger.info("🔄 Refreshing access token")
            
            payload = {
                "orgId": org_id,
                "userId": user_id,
                "scopes": [TokenScopes.FETCH_CONFIG.value]
            }
            
            jwt_token = jwt.encode(
                payload,
                os.getenv('SCOPED_JWT_SECRET'),
                algorithm='HS256'
            )
            
            headers = {
                "Authorization": f"Bearer {jwt_token}"
            }
            
            nodejs_config = await self.config_service.get_config(config_node_constants.CONFIGURATION_MANAGER.value)
            nodejs_endpoint = nodejs_config.get('endpoint')

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{nodejs_endpoint}{Routes.INDIVIDUAL_REFRESH_TOKEN.value}",
                    json=payload,
                    headers=headers
                ) as response:
                    if response.status != 200:
                        raise Exception(f"Failed to refresh token: {await response.json()}")
                    
                    creds_data = await response.json()
                    logger.info("🚀 Access Token Refresh response: %s", creds_data)
                               
            logger.info("✅ Successfully refreshed access token")

        except Exception as e:
            logger.error(f"❌ Failed to refresh token: {str(e)}")
            raise

    async def get_enterprise_token(self, org_id):
        # Prepare payload for credentials API
        payload = {
            "orgId": org_id,
            "scopes": [TokenScopes.FETCH_CONFIG.value]
        }
        
        # Create JWT token
        jwt_token = jwt.encode(
            payload,
            os.getenv('SCOPED_JWT_SECRET'),
            algorithm='HS256'
        )
        
        headers = {
            "Authorization": f"Bearer {jwt_token}"
        }
        nodejs_config = await self.config_service.get_config(config_node_constants.CONFIGURATION_MANAGER.value)
        nodejs_endpoint = nodejs_config.get('endpoint')
        
        # Call credentials API
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{nodejs_endpoint}{Routes.BUSINESS_CREDENTIALS.value}",
                json=payload,
                headers=headers
            ) as response:
                if response.status != 200:
                    raise Exception(f"Failed to fetch credentials: {await response.json()}")
                credentials_json = await response.json()
        
        return credentials_json
