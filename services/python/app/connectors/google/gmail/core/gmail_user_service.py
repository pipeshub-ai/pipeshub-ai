# pylint: disable=E1101, W0718

import base64
import re
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import os
import jwt
import aiohttp
from app.connectors.google.scopes import GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES
from typing import Dict, List
from googleapiclient.discovery import build
import google.oauth2.credentials
from app.config.configuration_service import ConfigurationService
from app.utils.logger import logger
from app.connectors.utils.decorators import exponential_backoff, token_refresh
from app.connectors.utils.rate_limiter import GoogleAPIRateLimiter
from app.connectors.google.gmail.core.gmail_drive_interface import GmailDriveInterface
from app.utils.time_conversion import get_epoch_timestamp_in_ms
import asyncio

class GmailUserService:
    """GmailUserService class for interacting with Google Gmail API"""

    def __init__(self, config: ConfigurationService, rate_limiter: GoogleAPIRateLimiter, google_token_handler, credentials=None):
        """Initialize GmailUserService"""

        logger.info("🚀 Initializing GmailUserService")
        self.config_service = config
        self.service = None

        self.credentials = credentials
        self.google_token_handler = google_token_handler
        self.gmail_drive_interface = GmailDriveInterface(
            config=self.config_service,
            rate_limiter=rate_limiter
        )

        # Rate limiters
        self.rate_limiter = rate_limiter
        self.google_limiter = self.rate_limiter.google_limiter

        self.token_expiry = None
        self.org_id = None
        self.user_id = None

    @token_refresh
    async def connect_individual_user(self, org_id: str, user_id: str) -> bool:
        """Connect using Oauth2 credentials for individual user"""
        try:
            self.org_id = org_id
            self.user_id = user_id
            
            SCOPES = GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES
            
            creds_data = await self.google_token_handler.get_individual_token(org_id, user_id)
            
            # Create credentials object from the response using google.oauth2.credentials.Credentials
            creds = google.oauth2.credentials.Credentials(
                token=creds_data.get('access_token'),
                refresh_token=creds_data.get('refresh_token'),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_data.get('client_id'),
                client_secret=creds_data.get('client_secret'),
                scopes=SCOPES
            )
            
            # Update token expiry time
            self.token_expiry = datetime.fromtimestamp(
                creds_data.get('access_token_expiry_time', 0) / 1000,
                tz=timezone.utc
            )
            
            logger.info("✅ Token expiry time: %s", self.token_expiry)


            self.service = build('gmail', 'v1', credentials=creds)
            logger.info("✅ GmailUserService connected successfully")
            return True

        except Exception as e:
            logger.error("❌ Failed to connect to Individual Gmail Service: %s", str(e))
            return False

    async def _check_and_refresh_token(self):
        """Check token expiry and refresh if needed"""
        if not self.token_expiry:
            logger.warning("⚠️ Token expiry time not set.")
            return
        
        if not self.org_id or not self.user_id:
            logger.warning("⚠️ Org ID or User ID not set yet.")
            return
        
        now = datetime.now(timezone.utc)
        time_until_refresh = self.token_expiry - now - timedelta(minutes=20)
        logger.info(f"Time until refresh: {time_until_refresh.total_seconds()} seconds")
        
        if time_until_refresh.total_seconds() <= 0:
            await self.google_token_handler.refresh_token(self.org_id, self.user_id)
                    
            creds_data = await self.google_token_handler.get_individual_token(self.org_id, self.user_id)

            creds = google.oauth2.credentials.Credentials(
                token=creds_data.get('access_token'),
                refresh_token=creds_data.get('refresh_token'),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_data.get('client_id'),
                client_secret=creds_data.get('client_secret'),
                scopes=GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES
            )

            self.service = build('gmail', 'v1', credentials=creds)

            # Update token expiry time
            self.token_expiry = datetime.fromtimestamp(
                creds_data.get('access_token_expiry_time', 0) / 1000,
                tz=timezone.utc
            )

            logger.info("✅ Token refreshed, new expiry: %s", self.token_expiry)

    async def connect_enterprise_user(self) -> bool:
        """Connect using OAuth2 credentials for enterprise user"""
        try:
            logger.info("🚀 Connecting to Enterprise Gmail Service")
            self.service = build(
                'gmail',
                'v1',
                credentials=self.credentials,
                cache_discovery=False
            )
            logger.info("✅ GmailUserService connected successfully")
            return True

        except Exception as e:
            logger.error(
                "❌ Failed to connect to Enterprise Gmail Service: %s", str(e))
            return False

    async def disconnect(self):
        """Disconnect and cleanup Gmail service"""
        try:
            logger.info("🔄 Disconnecting Gmail service")

            # Close the service connections if they exist
            if self.service:
                self.service.close()
                self.service = None

            # Clear credentials
            self.credentials = None

            logger.info("✅ Gmail service disconnected successfully")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to disconnect Gmail service: {str(e)}")
            return False


    @exponential_backoff()
    @token_refresh
    async def list_individual_user(self, org_id: str) -> List[Dict]:
        """Get individual user info"""
        try:
            logger.info("🚀 Getting individual user info")
            async with self.google_limiter:
                user = self.service.users().getProfile(
                    userId='me'
                ).execute()

                logger.info("✅ Individual user info fetched successfully")
                logger.info("🚀 User info: %s", user)
                                
                user = {
                    '_key': str(uuid4()),
                    'userId': str(uuid4()),
                    'orgId': org_id,
                    'email': user.get('emailAddress'),
                    'fullName': user.get('displayName'),
                    'firstName': user.get('givenName', ''),
                    'middleName': user.get('middleName', ''),
                    'lastName': user.get('familyName', ''),
                    'designation': user.get('designation', ''),
                    'businessPhones': user.get('businessPhones', []),
                    'isActive': False,
                    'createdAtTimestamp': get_epoch_timestamp_in_ms(),
                    'updatedAtTimestamp': get_epoch_timestamp_in_ms()
                }
                return [user]

        except Exception as e:
            logger.error("❌ Failed to get individual user info: %s", str(e))
            return []

    @exponential_backoff()
    @token_refresh
    async def list_messages(self, query: str = 'newer_than:180d') -> List[Dict]:
        """Get list of messages"""
        try:
            logger.info("🚀 Getting list of messages")
            messages = []
            page_token = None

            while True:
                async with self.google_limiter:
                    results = self.service.users().messages().list(
                        userId='me',
                        pageToken=page_token,
                        q=query
                    ).execute()

                    current_messages = results.get('messages', [])
                    messages.extend(current_messages)

                    page_token = results.get('nextPageToken')
                    if not page_token:
                        break

            logger.info("✅ Found %s messages", len(messages))
            return messages

        except Exception as e:
            logger.error("❌ Failed to get list of messages: %s", str(e))
            return []

    @exponential_backoff()
    @token_refresh
    async def get_message(self, message_id: str) -> Dict:
        """Get message by id"""
        try:
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()

            headers = message.get('payload', {}).get('headers', [])
            logger.info("🚀 Headers: %s", headers)
            header_dict = {}
            for header in headers:
                if header['name'] in ['Subject', 'From', 'To', 'Cc', 'Bcc', 'Date', 'Message-ID']:
                    if header['name'] in ['From', 'To', 'Cc', 'Bcc']:
                        # Extract email address between < and >
                        start = header['value'].find('<')
                        end = header['value'].find('>')
                        if start != -1 and end != -1:
                            header['value'] = header['value'][start+1:end]
                    header_dict[header['name']] = header['value']

            def get_message_content(payload):
                """Recursively extract message content from MIME parts"""
                if not payload:
                    return ''

                # If this part is multipart, recursively process its parts
                if payload.get('mimeType', '').startswith('multipart/'):
                    parts = payload.get('parts', [])
                    # For multipart/alternative, prefer HTML over plain text
                    if payload['mimeType'] == 'multipart/alternative':
                        html_content = ''
                        plain_content = ''
                        for part in parts:
                            if part['mimeType'] == 'text/html':
                                html_content = get_message_content(part)
                            elif part['mimeType'] == 'text/plain':
                                plain_content = get_message_content(part)
                        return html_content or plain_content
                    # For other multipart types, concatenate all text content
                    text_parts = []
                    for part in parts:
                        if part['mimeType'].startswith('text/') or part['mimeType'].startswith('multipart/'):
                            content = get_message_content(part)
                            if content:
                                text_parts.append(content)
                    return '\n'.join(text_parts)

                # If this is a text part, decode and return its content
                if payload['mimeType'].startswith('text/'):
                    if 'data' in payload.get('body', {}):
                        try:
                            decoded_content = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')
                            return decoded_content
                        except Exception as e:
                            logger.error(f"❌ Error decoding content: {str(e)}")
                            return ''

                return ''

            # Extract message content
            payload = message.get('payload', {})
            # logger.info("🚀 Payload: %s", payload)
            message_content = get_message_content(payload)
            # logger.info("🚀 Message content: %s", message_content)

            message['body'] = message_content
            message['headers'] = header_dict

            logger.info("✅ Successfully retrieved message %s", message.get('id'))
            return message

        except Exception as e:
            logger.error("❌ Failed to get message %s: %s", message_id, str(e))
            return {}

    @exponential_backoff()
    @token_refresh
    async def list_threads(self, query: str = 'newer_than:180d') -> List[Dict]:
        """Get list of unique threads"""
        try:
            logger.info("🚀 Getting list of threads")
            threads = []
            page_token = None

            while True:
                async with self.google_limiter:
                    results = self.service.users().threads().list(
                        userId='me',
                        pageToken=page_token,
                        q=query
                    ).execute()

                    current_threads = results.get('threads', [])
                    threads.extend(current_threads)

                    page_token = results.get('nextPageToken')
                    if not page_token:
                        break

            logger.info("✅ Found %s threads", len(threads))
            return threads

        except Exception as e:
            logger.error("❌ Failed to get list of threads: %s", str(e))
            return []

    @exponential_backoff()
    @token_refresh
    async def list_attachments(self, message, org_id: str, user_id: str, account_type: str) -> List[Dict]:
        """Get list of attachments for a message"""
        try:
            attachments = []
            if 'payload' in message and 'parts' in message['payload']:
                for part in message['payload']['parts']:
                    # Check if this part has a body with an attachmentId
                    if part.get('body', {}).get('attachmentId'):
                        # Parse file extension from filename
                        filename = part.get('filename', '')
                        extension = os.path.splitext(filename)[1].lower()[1:] if filename else ''

                        attachments.append({
                            'message_id': message['id'],
                            'org_id': org_id,
                            'attachment_id': part['body']['attachmentId'],
                            'filename': filename,
                            'extension': extension,
                            'mimeType': part.get('mimeType', ''),
                            'size': part['body'].get('size', 0)
                        })

            # Get any Google Drive file IDs from the message
            file_ids = await self.get_file_ids(message)
            logger.info("🚀 File IDs: %s", file_ids)
            if file_ids:
                # If we have a gmail_drive_interface instance, get file metadata
                for file_id in file_ids:
                    file_metadata = await self.gmail_drive_interface.get_drive_file(
                        file_id=file_id,
                        user_email=message.get('user_email'),
                        org_id=org_id,
                        user_id=user_id,
                        account_type=account_type
                    )
                    if file_metadata:
                        attachments.append({
                            'message_id': message['id'],
                            'org_id': org_id,
                            'attachment_id': file_id,
                            'filename': file_metadata.get('name', ''),
                            'mimeType': file_metadata.get('mimeType', ''),
                            'extension': file_metadata.get('extension', ''),
                            'size': file_metadata.get('size', 0),
                            'drive_file': True
                        })
                    else:
                        logger.info("🚀 File Metadata not found for file ID: %s", file_id)

            logger.info("✅ Found %s attachments", len(attachments))
            return attachments

        except Exception as e:
            logger.error(
                "❌ Failed to get attachments for message %s: %s", message['id'], str(e))
            return []

    @exponential_backoff()
    @token_refresh
    async def get_file_ids(self, message):
        """Get file ids from message by recursively checking all parts and MIME types"""
        try:
            def extract_file_ids(html_content):
                if not isinstance(html_content, str):
                    return []
                try:
                    unencoded_data = base64.urlsafe_b64decode(html_content).decode('UTF-8')
                    return re.findall(
                        r'https://drive\.google\.com/file/d/([^/]+)/view\?usp=drive_web',
                        unencoded_data
                    )
                except Exception as e:
                    logger.warning(f"Failed to decode content: {str(e)}")
                    return []

            def process_part(part):
                if not isinstance(part, dict):
                    return []
                
                file_ids = []

                # Check for body data
                body = part.get('body', {})
                if isinstance(body, dict) and body.get('data'):
                    mime_type = part.get('mimeType', '')
                    if 'text/html' in mime_type or 'text/plain' in mime_type:
                        file_ids.extend(extract_file_ids(body['data']))

                # Recursively process nested parts
                parts = part.get('parts', [])
                if isinstance(parts, list):
                    for nested_part in parts:
                        file_ids.extend(process_part(nested_part))

                return file_ids

            # Start processing from the payload
            if not isinstance(message, dict):
                return []
            
            payload = message.get('payload', {})
            all_file_ids = process_part(payload)

            # Remove duplicates while preserving order
            return list(dict.fromkeys(all_file_ids))

        except Exception as e:
            logger.error(
                "❌ Failed to get file ids for message %s: %s", 
                message.get('id', 'unknown'), 
                str(e)
            )
            return []

    @exponential_backoff()
    @token_refresh
    async def create_user_watch(self, user_id="me") -> Dict:
        """Create user watch"""
        try:
            logger.info("🚀 Creating user watch for user %s", user_id)
            # topic = await self.config.get_config('google/auth/gmail_pub_topic')
            topic = "projects/agile-seeker-447812-p3/topics/gmail-connector"
            async with self.google_limiter:
                request_body = {
                    'topicName': topic
                }
                response = self.service.users().watch(
                    userId=user_id, body=request_body).execute()
                logger.info("✅ User watch created successfully")
                return response
        except Exception as e:
            logger.error(
                "❌ Failed to create user watch (personal account): %s", str(e))
            return {}

    @exponential_backoff()
    @token_refresh
    async def fetch_gmail_changes(self, user_email, history_id):
        """Fetches new emails using Gmail API's history endpoint"""
        try:
            logger.info("🚀 Fetching changes in user mail")
            async with self.google_limiter:
                response = self.service.users().history().list(
                    userId=user_email,
                    startHistoryId=history_id,
                    historyTypes=['messageAdded', 'messageDeleted'],
                ).execute()

            logger.info("✅ Fetched Changes successfully: %s", response)
            return response

        except Exception as e:
            logger.error("❌ Failed to fetch changes in user mail: %s", str(e))
            return {}
