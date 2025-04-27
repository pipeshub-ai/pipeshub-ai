# pylint: disable=E1101, W0718

import base64
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from uuid import uuid4

import google.oauth2.credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config.configuration_service import ConfigurationService
from app.connectors.google.gmail.core.gmail_drive_interface import GmailDriveInterface
from app.connectors.google.scopes import GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES
from app.connectors.utils.decorators import exponential_backoff, token_refresh
from app.connectors.utils.rate_limiter import GoogleAPIRateLimiter
from app.exceptions.connector_google_exceptions import (
    BatchOperationError,
    GoogleAuthError,
    GoogleMailError,
    MailOperationError,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class GmailUserService:
    """GmailUserService class for interacting with Google Gmail API"""

    def __init__(
        self,
        logger,
        config: ConfigurationService,
        rate_limiter: GoogleAPIRateLimiter,
        google_token_handler,
        credentials=None,
        admin_service=None,
    ):
        """Initialize GmailUserService"""
        try:
            self.logger = logger
            self.logger.info("🚀 Initializing GmailUserService")
            self.config_service = config
            self.service = None
            self.credentials = credentials
            self.google_token_handler = google_token_handler
            self.gmail_drive_interface = GmailDriveInterface(
                logger=self.logger,
                config=self.config_service,
                google_token_handler=self.google_token_handler,
                rate_limiter=rate_limiter,
                admin_service=admin_service,
            )

            # Rate limiters
            self.rate_limiter = rate_limiter
            self.google_limiter = self.rate_limiter.google_limiter

            self.token_expiry = None
            self.org_id = None
            self.user_id = None
            self.is_delegated = (
                credentials is not None
            )  # True if created through admin service
        except Exception as e:
            raise GoogleMailError(
                "Failed to initialize Gmail service: " + str(e),
                details={"error": str(e)},
            )

    @token_refresh
    async def connect_individual_user(self, org_id: str, user_id: str) -> bool:
        """Connect using Oauth2 credentials for individual user"""
        try:
            self.org_id = org_id
            self.user_id = user_id

            SCOPES = GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES

            try:
                creds_data = await self.google_token_handler.get_individual_token(
                    org_id, user_id
                )
                if not creds_data:
                    raise GoogleAuthError(
                        "Failed to get individual token",
                        details={"org_id": org_id, "user_id": user_id},
                    )
            except Exception as e:
                raise GoogleAuthError(
                    "Error getting individual token: " + str(e),
                    details={"org_id": org_id, "user_id": user_id, "error": str(e)},
                )

            try:
                # Create credentials object from the response
                creds = google.oauth2.credentials.Credentials(
                    token=creds_data.get("access_token"),
                    refresh_token=creds_data.get("refresh_token"),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=creds_data.get("client_id"),
                    client_secret=creds_data.get("client_secret"),
                    scopes=SCOPES,
                )
            except Exception as e:
                raise GoogleAuthError(
                    "Failed to create credentials object: " + str(e),
                    details={"org_id": org_id, "user_id": user_id, "error": str(e)},
                )

            # Update token expiry time
            try:
                self.token_expiry = datetime.fromtimestamp(
                    creds_data.get("access_token_expiry_time", 0) / 1000,
                    tz=timezone.utc,
                )
                self.logger.info("✅ Token expiry time: %s", self.token_expiry)
            except Exception as e:
                raise GoogleAuthError(
                    "Failed to set token expiry: " + str(e),
                    details={
                        "org_id": org_id,
                        "user_id": user_id,
                        "expiry_time": creds_data.get("access_token_expiry_time"),
                        "error": str(e),
                    },
                )

            try:
                self.service = build("gmail", "v1", credentials=creds)
            except Exception as e:
                raise MailOperationError(
                    "Failed to build Gmail service: " + str(e),
                    details={"org_id": org_id, "user_id": user_id, "error": str(e)},
                )

            self.logger.info("✅ GmailUserService connected successfully")
            return True

        except (GoogleAuthError, MailOperationError):
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error connecting individual user: " + str(e),
                details={"org_id": org_id, "user_id": user_id, "error": str(e)},
            )

    async def _check_and_refresh_token(self):
        """Check token expiry and refresh if needed"""
        self.logger.info("Checking token expiry and refreshing if needed")

        if not self.token_expiry:
            # self.logger.warning("⚠️ Token expiry time not set.")
            return

        if not self.org_id or not self.user_id:
            self.logger.warning("⚠️ Org ID or User ID not set yet.")
            return

        now = datetime.now(timezone.utc)
        time_until_refresh = self.token_expiry - now - timedelta(minutes=20)
        self.logger.info(
            f"Time until refresh: {time_until_refresh.total_seconds()} seconds"
        )

        if time_until_refresh.total_seconds() <= 0:
            await self.google_token_handler.refresh_token(self.org_id, self.user_id)

            creds_data = await self.google_token_handler.get_individual_token(
                self.org_id, self.user_id
            )

            creds = google.oauth2.credentials.Credentials(
                token=creds_data.get("access_token"),
                refresh_token=creds_data.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_data.get("client_id"),
                client_secret=creds_data.get("client_secret"),
                scopes=GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES,
            )

            self.service = build("gmail", "v1", credentials=creds)

            # Update token expiry time
            self.token_expiry = datetime.fromtimestamp(
                creds_data.get("access_token_expiry_time", 0) / 1000, tz=timezone.utc
            )

            self.logger.info("✅ Token refreshed, new expiry: %s", self.token_expiry)

    async def connect_enterprise_user(self) -> bool:
        """Connect using OAuth2 credentials for enterprise user"""
        try:
            if not self.credentials:
                raise GoogleAuthError(
                    "No credentials provided for enterprise connection."
                )

            try:
                self.service = build(
                    "gmail", "v1", credentials=self.credentials, cache_discovery=False
                )
            except Exception as e:
                raise MailOperationError(
                    "Failed to build Gmail service: " + str(e),
                    details={"error": str(e)},
                )

            self.logger.info("✅ GmailUserService connected successfully")
            return True

        except (GoogleAuthError, MailOperationError):
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error connecting enterprise user: " + str(e),
                details={"error": str(e)},
            )

    async def disconnect(self):
        """Disconnect and cleanup Gmail service"""
        try:
            self.logger.info("🔄 Disconnecting Gmail service")

            try:
                if self.service:
                    self.service.close()
                    self.service = None
            except Exception as e:
                raise MailOperationError(
                    "Failed to close Gmail service: " + str(e),
                    details={"error": str(e)},
                )

            # Clear credentials
            self.credentials = None

            self.logger.info("✅ Gmail service disconnected successfully")
            return True
        except MailOperationError:
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error disconnecting Gmail service: " + str(e),
                details={"error": str(e)},
            )

    @exponential_backoff()
    @token_refresh
    async def list_individual_user(self, org_id: str) -> List[Dict]:
        """Get individual user info"""
        try:
            self.logger.info("🚀 Getting individual user info")
            try:
                async with self.google_limiter:
                    user = self.service.users().getProfile(userId="me").execute()
            except HttpError as e:
                if e.resp.status == 403:
                    raise GoogleAuthError(
                        "Permission denied getting user profile: " + str(e),
                        details={"org_id": org_id, "error": str(e)},
                    )
                raise MailOperationError(
                    "Failed to get user profile: " + str(e),
                    details={"org_id": org_id, "error": str(e)},
                )

            self.logger.info("✅ Individual user info fetched successfully")

            try:
                return [
                    {
                        "_key": str(uuid4()),
                        "userId": str(uuid4()),
                        "orgId": org_id,
                        "email": user.get("emailAddress"),
                        "fullName": user.get("displayName"),
                        "firstName": user.get("givenName", ""),
                        "middleName": user.get("middleName", ""),
                        "lastName": user.get("familyName", ""),
                        "designation": user.get("designation", ""),
                        "businessPhones": user.get("businessPhones", []),
                        "isActive": False,
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                ]
            except Exception as e:
                raise MailOperationError(
                    "Failed to process user info: " + str(e),
                    details={"org_id": org_id, "error": str(e)},
                )

        except (GoogleAuthError, MailOperationError):
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error getting individual user info: " + str(e),
                details={"org_id": org_id, "error": str(e)},
            )

    @exponential_backoff()
    @token_refresh
    async def list_messages(self, query: str = "newer_than:180d") -> List[Dict]:
        """Get list of messages"""
        try:
            self.logger.info("🚀 Getting list of messages")
            messages = []
            page_token = None

            while True:
                try:
                    async with self.google_limiter:
                        results = (
                            self.service.users()
                            .messages()
                            .list(userId="me", pageToken=page_token, q=query)
                            .execute()
                        )
                except HttpError as e:
                    if e.resp.status == 403:
                        raise GoogleAuthError(
                            "Permission denied listing messages: " + str(e),
                            details={"query": query, "error": str(e)},
                        )
                    raise MailOperationError(
                        "Failed to list messages: " + str(e),
                        details={"query": query, "error": str(e)},
                    )

                current_messages = results.get("messages", [])
                if not isinstance(current_messages, list):
                    raise MailOperationError(
                        "Invalid response format for messages",
                        details={
                            "query": query,
                            "response_type": type(current_messages),
                        },
                    )

                messages.extend(current_messages)

                page_token = results.get("nextPageToken")
                if not page_token:
                    break

            self.logger.info("✅ Found %s messages", len(messages))
            return messages

        except (GoogleAuthError, MailOperationError):
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error listing messages: " + str(e),
                details={"query": query, "error": str(e)},
            )

    @exponential_backoff()
    @token_refresh
    async def get_message(self, message_id: str) -> Dict:
        """Get message by id"""

        def get_message_content(payload):
            """Recursively extract message content from MIME parts"""
            if not payload:
                return ""

            # If this part is multipart, recursively process its parts
            if payload.get("mimeType", "").startswith("multipart/"):
                parts = payload.get("parts", [])
                # For multipart/alternative, prefer HTML over plain text
                if payload["mimeType"] == "multipart/alternative":
                    html_content = ""
                    plain_content = ""
                    for part in parts:
                        if part["mimeType"] == "text/html":
                            html_content = get_message_content(part)
                        elif part["mimeType"] == "text/plain":
                            plain_content = get_message_content(part)
                    return html_content or plain_content
                # For other multipart types, concatenate all text content
                text_parts = []
                for part in parts:
                    if part["mimeType"].startswith("text/") or part[
                        "mimeType"
                    ].startswith("multipart/"):
                        content = get_message_content(part)
                        if content:
                            text_parts.append(content)
                return "\n".join(text_parts)

            # If this is a text part, decode and return its content
            if payload["mimeType"].startswith("text/"):
                if "data" in payload.get("body", {}):
                    try:
                        decoded_content = base64.urlsafe_b64decode(
                            payload["body"]["data"]
                        ).decode("utf-8")
                        return decoded_content
                    except Exception as e:
                        self.logger.error(f"❌ Error decoding content: {str(e)}")
                        return ""

            return ""

        try:
            try:
                message = (
                    self.service.users()
                    .messages()
                    .get(userId="me", id=message_id, format="full")
                    .execute()
                )
            except HttpError as e:
                if e.resp.status == 404:
                    raise MailOperationError(
                        "Message not found: " + str(e),
                        details={"message_id": message_id},
                    )
                elif e.resp.status == 403:
                    raise GoogleAuthError(
                        "Permission denied accessing message: " + str(e),
                        details={"message_id": message_id},
                    )
                raise MailOperationError(
                    "Failed to get message: " + str(e),
                    details={"message_id": message_id, "error": str(e)},
                )

            try:
                headers = message.get("payload", {}).get("headers", [])
                header_dict = {}
                for header in headers:
                    if header["name"] in [
                        "Subject",
                        "From",
                        "To",
                        "Cc",
                        "Bcc",
                        "Date",
                        "Message-ID",
                    ]:
                        if header["name"] in ["From", "To", "Cc", "Bcc"]:
                            # Extract all email addresses using regex
                            emails = re.findall(
                                r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
                                header["value"],
                            )
                            header["value"] = emails if emails else []
                        header_dict[header["name"]] = header["value"]

                self.logger.debug("📝 Headers: %s", header_dict)

                # Extract message content
                payload = message.get("payload", {})
                message_content = get_message_content(payload)

                message["body"] = message_content
                message["headers"] = header_dict

            except Exception as e:
                raise MailOperationError(
                    "Failed to process message content: " + str(e),
                    details={"message_id": message_id, "error": str(e)},
                )

            self.logger.info("✅ Successfully retrieved message %s", message.get("id"))
            return message

        except (GoogleAuthError, MailOperationError):
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error getting message: " + str(e),
                details={"message_id": message_id, "error": str(e)},
            )

    @exponential_backoff()
    @token_refresh
    async def list_threads(self, query: str = "newer_than:30d") -> List[Dict]:
        """Get list of unique threads"""
        try:
            self.logger.info("🚀 Getting list of threads")
            threads = []
            page_token = None

            while True:
                try:
                    async with self.google_limiter:
                        results = (
                            self.service.users()
                            .threads()
                            .list(userId="me", pageToken=page_token, q=query)
                            .execute()
                        )
                except HttpError as e:
                    if e.resp.status == 403:
                        raise GoogleAuthError(
                            "Permission denied listing threads: " + str(e),
                            details={"query": query, "error": str(e)},
                        )
                    raise MailOperationError(
                        "Failed to list threads: " + str(e),
                        details={"query": query, "error": str(e)},
                    )

                current_threads = results.get("threads", [])
                if not isinstance(current_threads, list):
                    raise MailOperationError(
                        "Invalid response format for threads",
                        details={
                            "query": query,
                            "response_type": type(current_threads),
                        },
                    )

                threads.extend(current_threads)

                page_token = results.get("nextPageToken")
                if not page_token:
                    break

            self.logger.info("✅ Found %s threads", len(threads))
            return threads

        except (GoogleAuthError, MailOperationError):
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error listing threads: " + str(e),
                details={"query": query, "error": str(e)},
            )

    @exponential_backoff()
    @token_refresh
    async def list_attachments(
        self, message, org_id: str, user, account_type: str
    ) -> List[Dict]:
        """Get list of attachments for a message"""
        try:
            if not isinstance(message, dict):
                raise MailOperationError(
                    "Invalid message format", details={"type": type(message)}
                )

            user_id = user.get("userId")
            user_email = user.get("email")

            attachments = []
            failed_items = []

            self.logger.info(f"🎯 Processing attachments for message: {message['id']}")
            self.logger.info(f"🎯 Message: {message}")

            # Process regular attachments
            if "payload" in message and "parts" in message["payload"]:
                for part in message["payload"]["parts"]:
                    try:
                        if part.get("body", {}).get("attachmentId"):
                            filename = part.get("filename", "")
                            extension = (
                                os.path.splitext(filename)[1].lower()[1:]
                                if filename
                                else ""
                            )

                            attachments.append(
                                {
                                    "message_id": message["id"],
                                    "org_id": org_id,
                                    "attachment_id": part["body"]["attachmentId"],
                                    "filename": filename,
                                    "extension": extension,
                                    "mimeType": part.get("mimeType", ""),
                                    "size": part["body"].get("size", 0),
                                }
                            )
                    except Exception as e:
                        failed_items.append(
                            {
                                "part_filename": part.get("filename", "unknown"),
                                "error": str(e),
                            }
                        )

            # Process Drive attachments
            try:
                self.logger.info(
                    f"🎯 Processing Drive attachments for message: {message['id']}"
                )

                file_ids = await self.get_file_ids(message)
                if file_ids:
                    for file_id in file_ids:
                        try:
                            file_metadata = (
                                await self.gmail_drive_interface.get_drive_file(
                                    file_id=file_id,
                                    user_email=user_email,
                                    org_id=org_id,
                                    user_id=user_id,
                                    account_type=account_type,
                                )
                            )
                            if file_metadata:
                                attachments.append(
                                    {
                                        "message_id": message["id"],
                                        "org_id": org_id,
                                        "attachment_id": file_id,
                                        "filename": file_metadata.get("name", ""),
                                        "mimeType": file_metadata.get("mimeType", ""),
                                        "extension": file_metadata.get("extension", ""),
                                        "size": file_metadata.get("size", 0),
                                        "drive_file": True,
                                    }
                                )
                        except Exception as e:
                            failed_items.append({"file_id": file_id, "error": str(e)})
            except Exception as e:
                self.logger.error("Failed to process Drive attachments: %s", str(e))

            if failed_items:
                raise BatchOperationError(
                    f"Failed to process {len(failed_items)} attachments",
                    failed_items=failed_items,
                    details={
                        "message_id": message.get("id"),
                        "total_attachments": len(attachments) + len(failed_items),
                    },
                )

            self.logger.info("✅ Found %s attachments", len(attachments))
            return attachments

        except BatchOperationError:
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error listing attachments: " + str(e),
                details={"message_id": message.get("id", "unknown"), "error": str(e)},
            )

    @exponential_backoff()
    @token_refresh
    async def get_file_ids(self, message):
        """Get file ids from message by recursively checking all parts and MIME types"""
        try:

            def extract_file_ids(html_content):
                if not isinstance(html_content, str):
                    return []
                try:
                    unencoded_data = base64.urlsafe_b64decode(html_content).decode(
                        "UTF-8"
                    )
                    return re.findall(
                        r"https://drive\.google\.com/file/d/([^/]+)/view\?usp=drive_web",
                        unencoded_data,
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to decode content: {str(e)}")
                    return []

            def process_part(part):
                if not isinstance(part, dict):
                    return []

                file_ids = []

                # Check for body data
                body = part.get("body", {})
                if isinstance(body, dict) and body.get("data"):
                    mime_type = part.get("mimeType", "")
                    if "text/html" in mime_type or "text/plain" in mime_type:
                        file_ids.extend(extract_file_ids(body["data"]))

                # Recursively process nested parts
                parts = part.get("parts", [])
                if isinstance(parts, list):
                    for nested_part in parts:
                        file_ids.extend(process_part(nested_part))

                return file_ids

            # Start processing from the payload
            if not isinstance(message, dict):
                return []

            payload = message.get("payload", {})
            all_file_ids = process_part(payload)

            # Remove duplicates while preserving order
            return list(dict.fromkeys(all_file_ids))

        except Exception as e:
            self.logger.error(
                "❌ Failed to get file ids for message %s: %s",
                message.get("id", "unknown"),
                str(e),
            )
            return []

    @exponential_backoff()
    @token_refresh
    async def create_gmail_user_watch(self, user_id="me") -> Dict:
        """Create user watch"""
        try:
            self.logger.info("🚀 Creating user watch for user %s", user_id)
            creds_data = await self.google_token_handler.get_individual_token(
                self.org_id, self.user_id
            )
            self.logger.info(f"🚀 Google workspace config: {creds_data}")
            enable_real_time_updates = creds_data.get("enableRealTimeUpdates", False)
            self.logger.info(f"🚀 Enable real time updates: {enable_real_time_updates}")
            if not enable_real_time_updates:
                return {}

            topic = creds_data.get("topicName", "")
            self.logger.info(f"🚀 Topic: {topic}")
            if not topic:
                raise MailOperationError(
                    "Topic is required", details={"user_id": user_id}
                )

            self.logger.info("🚀 Creating user watch for user %s", user_id)

            try:
                async with self.google_limiter:
                    request_body = {"topicName": topic, "labelIds": ["INBOX"]}
                    # self.service.users().stop(
                    #     userId=user_id,
                    # )
                    response = (
                        self.service.users()
                        .watch(userId=user_id, body=request_body)
                        .execute()
                    )
                    response["expiration"] = int(response["expiration"])
            except HttpError as e:
                if e.resp.status == 403:
                    raise GoogleAuthError(
                        "Permission denied creating user watch: " + str(e),
                        details={"user_id": user_id, "error": str(e)},
                    )
                elif e.resp.status == 400:
                    raise MailOperationError(
                        "Invalid request creating user watch: " + str(e),
                        details={"user_id": user_id, "topic": topic, "error": str(e)},
                    )
                raise MailOperationError(
                    "Failed to create user watch: " + str(e),
                    details={"user_id": user_id, "error": str(e)},
                )

            if not isinstance(response, dict):
                raise MailOperationError(
                    "Invalid response format for user watch",
                    details={"user_id": user_id, "response_type": type(response)},
                )

            self.logger.info("✅ User watch created successfully for %s", user_id)
            return response

        except (GoogleAuthError, MailOperationError):
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error creating user watch: " + str(e),
                details={"user_id": user_id, "error": str(e)},
            )

    async def stop_gmail_user_watch(self, user_id="me") -> bool:
        """Stop user watch"""
        try:
            self.logger.info("🚀 Stopping user watch for user %s", user_id)
            self.service.users().stop(userId=user_id).execute()
            self.logger.info("✅ User watch stopped successfully for %s", user_id)
            return True
        except Exception as e:
            self.logger.error(
                "❌ Failed to delete user watch for user %s: %s", user_id, str(e)
            )
            return False

    @exponential_backoff()
    @token_refresh
    async def fetch_gmail_changes(self, user_email: str, history_id: str) -> Dict:
        """Fetches new emails using Gmail API's history endpoint"""
        try:
            self.logger.info("🚀 Fetching changes in user mail")

            if not history_id:
                raise MailOperationError(
                    "History ID is required", details={"user_email": user_email}
                )

            try:
                async with self.google_limiter:
                    # Fetch both inbox and sent changes
                    inbox_response = (
                        self.service.users()
                        .history()
                        .list(
                            userId=user_email,
                            startHistoryId=history_id,
                            labelId="INBOX",
                            historyTypes=["messageAdded", "messageDeleted"],
                        )
                        .execute()
                    )

                    sent_response = (
                        self.service.users()
                        .history()
                        .list(
                            userId=user_email,
                            startHistoryId=history_id,
                            labelId="SENT",
                            historyTypes=["messageAdded", "messageDeleted"],
                        )
                        .execute()
                    )

            except HttpError as e:
                if e.resp.status == 404:
                    raise MailOperationError(
                        "Invalid history ID: " + str(e),
                        details={
                            "user_email": user_email,
                            "history_id": history_id,
                            "error": str(e),
                        },
                    )
                elif e.resp.status == 403:
                    raise GoogleAuthError(
                        "Permission denied fetching changes: " + str(e),
                        details={"user_email": user_email, "error": str(e)},
                    )
                raise MailOperationError(
                    "Failed to fetch changes: " + str(e),
                    details={
                        "user_email": user_email,
                        "history_id": history_id,
                        "error": str(e),
                    },
                )

            if not isinstance(inbox_response, dict) or not isinstance(
                sent_response, dict
            ):
                raise MailOperationError(
                    "Invalid response format for history",
                    details={
                        "user_email": user_email,
                        "response_type": f"inbox: {type(inbox_response)}, sent: {type(sent_response)}",
                    },
                )

            # Combine the history lists from both responses
            combined_response = inbox_response.copy()
            if "history" in sent_response:
                if "history" not in combined_response:
                    combined_response["history"] = []
                combined_response["history"].extend(sent_response.get("history", []))

            self.logger.info("✅ Fetched changes successfully for user %s", user_email)
            self.logger.info(f"Combined response: {combined_response}")
            return combined_response

        except (GoogleAuthError, MailOperationError):
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error fetching changes: " + str(e),
                details={
                    "user_email": user_email,
                    "history_id": history_id,
                    "error": str(e),
                },
            )
