"""ParserAdminService module for parsing Google Workspace files using admin credentials"""

import os
from typing import Dict, Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from app.config.configuration_service import ConfigurationService, Routes, TokenScopes, config_node_constants
from app.utils.logger import logger
from app.connectors.utils.rate_limiter import GoogleAPIRateLimiter
from app.modules.parsers.google_files.parser_user_service import ParserUserService
from app.connectors.google.scopes import GOOGLE_PARSER_SCOPES
import jwt
import aiohttp

class ParserAdminService:
    """ParserAdminService class for parsing Google Workspace files using admin credentials"""

    def __init__(self, config: ConfigurationService, rate_limiter: GoogleAPIRateLimiter):
        self.config_service = config
        self.rate_limiter = rate_limiter
        self.google_limiter = self.rate_limiter.google_limiter
        self.credentials = None
        self.admin_service = None

        # Services for different Google Workspace apps
        self.docs_service = None
        self.sheets_service = None
        self.slides_service = None

    async def connect_admin(self, org_id: str) -> bool:
        """Initialize admin parser services with domain-wide delegation"""
        try:
            SCOPES = GOOGLE_PARSER_SCOPES
            
            credentials_json = await self.google_token_handler.get_enterprise_token(org_id)
            admin_email = credentials_json.get('adminEmail')
            # Create credentials from JSON
            self.credentials = service_account.Credentials.from_service_account_info(
                credentials_json,
                scopes=SCOPES,
                subject=admin_email
            )

            # Initialize services for different Google Workspace apps
            self.docs_service = build(
                'docs', 'v1', credentials=self.credentials)
            self.sheets_service = build(
                'sheets', 'v4', credentials=self.credentials)
            self.slides_service = build(
                'slides', 'v1', credentials=self.credentials)

            return True

        except Exception as e:
            logger.error(
                "❌ Failed to connect Parser Admin Service: %s", str(e))
            return False

    async def create_user_service(self, user_email: str) -> Optional[ParserUserService]:
        """Get or create a ParserUserService for a specific user"""
        try:
            # Create delegated credentials for the user
            user_credentials = self.credentials.with_subject(user_email)

            # Create new user service
            user_service = ParserUserService(
                config=self.config,
                rate_limiter=self.rate_limiter,
                credentials=user_credentials
            )

            # Connect the user service
            if not await user_service.connect_enterprise_user():
                return None

            return user_service

        except Exception as e:
            logger.error(
                "❌ Failed to create user service for %s: %s", user_email, str(e))
            return None
