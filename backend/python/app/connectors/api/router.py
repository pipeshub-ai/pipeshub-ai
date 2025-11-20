import asyncio
import base64
import io
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import google.oauth2.credentials
import jwt
from dependency_injector.wiring import Provide, inject
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from jose import JWTError
from pydantic import BaseModel, ValidationError

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AccountType,
    CollectionNames,
    Connectors,
    MimeTypes,
    RecordRelations,
    RecordTypes,
)
from app.config.constants.http_status_code import (
    HttpStatusCode,
)
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.connectors.api.middleware import WebhookAuthVerifier
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.token_service.oauth_service import (
    OAuthProvider,
    OAuthToken,
)
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.google.admin.admin_webhook_handler import (
    AdminWebhookHandler,
)
from app.connectors.sources.google.common.google_token_handler import (
    CredentialKeys,
    GoogleTokenHandler,
)
from app.connectors.sources.google.common.scopes import (
    GOOGLE_CONNECTOR_ENTERPRISE_SCOPES,
)
from app.connectors.sources.google.gmail.gmail_webhook_handler import (
    AbstractGmailWebhookHandler,
)
from app.connectors.sources.google.google_drive.drive_webhook_handler import (
    AbstractDriveWebhookHandler,
)
from app.containers.connector import ConnectorAppContainer
from app.modules.parsers.google_files.google_docs_parser import GoogleDocsParser
from app.modules.parsers.google_files.google_sheets_parser import GoogleSheetsParser
from app.modules.parsers.google_files.google_slides_parser import GoogleSlidesParser
from app.utils.api_call import make_api_call
from app.utils.jwt import generate_jwt
from app.utils.llm import get_llm
from app.utils.logger import create_logger
from app.utils.oauth_config import get_oauth_config
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = create_logger("connector_service")

router = APIRouter()


class ReindexFailedRequest(BaseModel):
    connector: str  # GOOGLE_DRIVE, GOOGLE_MAIL, KNOWLEDGE_BASE
    origin: str     # CONNECTOR, UPLOAD


async def get_arango_service(request: Request) -> BaseArangoService:
    container: ConnectorAppContainer = request.app.container
    arango_service = await container.arango_service()
    return arango_service

async def get_drive_webhook_handler(request: Request) -> Optional[AbstractDriveWebhookHandler]:
    try:
        container: ConnectorAppContainer = request.app.container
        drive_webhook_handler = container.drive_webhook_handler()
        return drive_webhook_handler
    except Exception as e:
        logger.warning(f"Failed to get drive webhook handler: {str(e)}")
        return None

def _parse_comma_separated_str(value: Optional[str]) -> Optional[List[str]]:
    """Parses a comma-separated string into a list of strings, filtering out empty items."""
    if not value:
        return None
    return [item.strip() for item in value.split(',') if item.strip()]

def _sanitize_app_name(app_name: str) -> str:
    return app_name.replace(" ", "").lower()

@router.post("/drive/webhook")
@inject
async def handle_drive_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Handle incoming webhook notifications from Google Drive"""
    try:

        verifier = WebhookAuthVerifier(logger)
        if not await verifier.verify_request(request):
            raise HTTPException(status_code=HttpStatusCode.UNAUTHORIZED.value, detail="Unauthorized webhook request")

        drive_webhook_handler = await get_drive_webhook_handler(request)

        if drive_webhook_handler is None:
            logger.warning(
                "Drive webhook handler not yet initialized - skipping webhook processing"
            )
            return {
                "status": "skipped",
                "message": "Webhook handler not yet initialized",
            }

        # Log incoming request details
        headers = dict(request.headers)
        logger.info("📥 Incoming webhook request")

        # Get important headers
        resource_state = (
            headers.get("X-Goog-Resource-State")
            or headers.get("x-goog-resource-state")
            or headers.get("X-GOOG-RESOURCE-STATE")
        )

        logger.info("Resource state: %s", resource_state)

        # Process notification in background
        if resource_state != "sync":
            background_tasks.add_task(
                drive_webhook_handler.process_notification, headers
            )
            return {"status": "accepted"}
        else:
            logger.info("Received sync verification request")
            return {"status": "sync_verified"}

    except Exception as e:
        logger.error("Error processing webhook: %s", str(e))
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail=str(e)) from e


async def get_gmail_webhook_handler(request: Request) -> Optional[AbstractGmailWebhookHandler]:
    try:
        container: ConnectorAppContainer = request.app.container
        gmail_webhook_handler = container.gmail_webhook_handler()
        return gmail_webhook_handler
    except Exception as e:
        logger.warning(f"Failed to get gmail webhook handler: {str(e)}")
        return None


@router.get("/gmail/webhook")
@router.post("/gmail/webhook")
@inject
async def handle_gmail_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Handles incoming Pub/Sub messages"""
    try:
        gmail_webhook_handler = await get_gmail_webhook_handler(request)

        if gmail_webhook_handler is None:
            logger.warning(
                "Gmail webhook handler not yet initialized - skipping webhook processing"
            )
            return {
                "status": "skipped",
                "message": "Webhook handler not yet initialized",
            }

        body = await request.json()
        logger.info("Received webhook request: %s", body)

        # Get the message from the body
        message = body.get("message")
        if not message:
            logger.warning("No message found in webhook body")
            return {"status": "error", "message": "No message found"}

        # Decode the message data
        data = message.get("data", "")
        if data:
            try:
                decoded_data = base64.b64decode(data).decode("utf-8")
                notification = json.loads(decoded_data)

                # Process the notification
                background_tasks.add_task(
                    gmail_webhook_handler.process_notification,
                    request.headers,
                    notification,
                )

                return {"status": "ok"}
            except Exception as e:
                logger.error("Error processing message data: %s", str(e))
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Invalid message data format: {str(e)}",
                )
        else:
            logger.warning("No data found in message")
            return {"status": "error", "message": "No data found"}

    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in webhook body: %s", str(e))
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail=f"Invalid JSON format: {str(e)}",
        )
    except Exception as e:
        logger.error("Error processing webhook: %s", str(e))
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail=str(e)
        )


@router.get("/api/v1/{org_id}/{user_id}/{connector}/record/{record_id}/signedUrl")
@inject
async def get_signed_url(
    org_id: str,
    user_id: str,
    connector: str,
    record_id: str,
    signed_url_handler=Depends(Provide[ConnectorAppContainer.signed_url_handler]),
) -> dict:
    """Get signed URL for a record"""
    try:
        additional_claims = {"connector": connector, "purpose": "file_processing"}

        signed_url = await signed_url_handler.get_signed_url(
            record_id,
            org_id,
            user_id,
            additional_claims=additional_claims,
            connector=connector,
        )
        # Return as JSON instead of plain text
        return {"signedUrl": signed_url}
    except Exception as e:
        logger.error(f"Error getting signed URL: {repr(e)}")
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail=str(e))


async def get_google_docs_parser(request: Request) -> Optional[GoogleDocsParser]:
    try:
        container: ConnectorAppContainer = request.app.container
        google_docs_parser = container.google_docs_parser()
        return google_docs_parser
    except Exception as e:
        logger.warning(f"Failed to get google docs parser: {str(e)}")
        return None


async def get_google_sheets_parser(request: Request) -> Optional[GoogleSheetsParser]:
    try:
        container: ConnectorAppContainer = request.app.container
        google_sheets_parser = container.google_sheets_parser()
        return google_sheets_parser
    except Exception as e:
        logger.warning(f"Failed to get google sheets parser: {str(e)}")
        return None


async def get_google_slides_parser(request: Request) -> Optional[GoogleSlidesParser]:
    try:
        container: ConnectorAppContainer = request.app.container
        google_slides_parser = container.google_slides_parser()
        return google_slides_parser
    except Exception as e:
        logger.warning(f"Failed to get google slides parser: {str(e)}")
        return None

@router.delete("/api/v1/delete/record/{record_id}")
@inject
async def handle_record_deletion(
    record_id: str, arango_service=Depends(Provide[ConnectorAppContainer.arango_service])
) -> Optional[dict]:
    try:
        response = await arango_service.delete_records_and_relations(
            record_id, hard_delete=True
        )
        if not response:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value, detail=f"Record with ID {record_id} not found"
            )
        return {
            "status": "success",
            "message": "Record deleted successfully",
            "response": response,
        }
    except HTTPException as he:
        raise he  # Re-raise HTTP exceptions as-is
    except Exception as e:
        logger.error(f"Error deleting record: {str(e)}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Internal server error while deleting record: {str(e)}",
        )

@router.get("/api/v1/internal/stream/record/{record_id}/", response_model=None)
@inject
async def stream_record_internal(
    request: Request,
    record_id: str,
    arango_service: BaseArangoService = Depends(Provide[ConnectorAppContainer.arango_service]),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Optional[dict | StreamingResponse]:
    """
    Stream a record to the client.
    """
    try:
        logger.info(f"Stream Record Start: {time.time()}")
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="Missing or invalid Authorization header",
            )
        # Extract the token
        token = auth_header.split(" ")[1]
        secret_keys = await config_service.get_config(
            config_node_constants.SECRET_KEYS.value
        )
        jwt_secret = secret_keys.get("scopedJwtSecret")
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        # TODO: Validate scopes ["connector:signedUrl"]

        org_id = payload.get("orgId")
        org_task = arango_service.get_document(org_id, CollectionNames.ORGS.value)
        record_task = arango_service.get_record_by_id(
            record_id
        )
        org, record = await asyncio.gather(org_task, record_task)

        if not org:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Organization not found")
        if not record:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Record not found")

        connector_name = record.connector_name.value.lower().replace(" ", "")
        container: ConnectorAppContainer = request.app.container
        if connector_name == Connectors.KNOWLEDGE_BASE.value.lower() or connector_name is None:
            endpoints = await config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            storage_url = endpoints.get("storage").get("endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value)
            buffer_url = f"{storage_url}/api/v1/document/internal/{record.external_record_id}/buffer"
            jwt_payload  = {
                "orgId": org_id,
                "scopes": ["storage:token"],
            }
            token = await generate_jwt(config_service, jwt_payload)
            response = await make_api_call(
                route=buffer_url, token=token
            )
            return response["data"]

        connector_id = record.connector_id
        connector = container.connectors_map[connector_id]
        if not connector:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector '{connector_name}' not found"
            )
        buffer = await connector.stream_record(record)
        return buffer

    except JWTError as e:
        logger.error("JWT validation error: %s", str(e))
        raise HTTPException(status_code=HttpStatusCode.UNAUTHORIZED.value, detail="Invalid or expired token")
    except ValidationError as e:
        logger.error("Payload validation error: %s", str(e))
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Invalid token payload")
    except Exception as e:
        logger.error("Unexpected error during token validation: %s", str(e))
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Error validating token")

@router.get("/api/v1/index/{org_id}/{connector}/record/{record_id}", response_model=None)
@inject
async def download_file(
    request: Request,
    org_id: str,
    record_id: str,
    connector: str,
    token: str,
    signed_url_handler=Depends(Provide[ConnectorAppContainer.signed_url_handler]),
    arango_service: BaseArangoService = Depends(Provide[ConnectorAppContainer.arango_service]),
    google_token_handler: GoogleTokenHandler = Depends(Provide[ConnectorAppContainer.google_token_handler]),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Optional[dict | StreamingResponse]:
    try:
        logger.info(f"Downloading file {record_id} with connector {connector}")
        # Verify signed URL using the handler

        payload = signed_url_handler.validate_token(token)
        user_id = payload.user_id
        user = await arango_service.get_user_by_user_id(user_id)
        user_email = user.get("email")

        # Verify file_id matches the token
        if payload.record_id != record_id:
            logger.error(
                f"""Token does not match requested file: {
                         payload.record_id} != {record_id}"""
            )
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value, detail="Token does not match requested file"
            )

        # Get org details to determine account type
        org = await arango_service.get_document(org_id, CollectionNames.ORGS.value)
        if not org:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Organization not found")

        # Get record details
        record = await arango_service.get_record_by_id(
            record_id
        )
        if not record:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Record not found")

        external_record_id = record.external_record_id
        connector_id = record.connector_id
        creds = None
        if connector.lower() == Connectors.GOOGLE_DRIVE.value.lower() or connector.lower() == Connectors.GOOGLE_MAIL.value.lower():
            # Get connector instance to check scope
            connector_instance = await arango_service.get_document(connector_id, CollectionNames.APPS.value)
            connector_scope = connector_instance.get("scope", ConnectorScope.PERSONAL.value) if connector_instance else ConnectorScope.PERSONAL.value

            # Use service account credentials only for TEAM scope connectors in enterprise/business accounts
            # Personal scope connectors always use user credentials regardless of account type
            if (org["accountType"] in [AccountType.ENTERPRISE.value, AccountType.BUSINESS.value] and
                connector_scope == ConnectorScope.TEAM.value):
                # Use service account credentials for team scope in enterprise accounts
                creds = await get_service_account_credentials(org_id, user_id, logger, arango_service, google_token_handler, request.app.container, connector, connector_id)
            else:
                # Use user credentials for personal scope or individual accounts
                creds = await get_user_credentials(org_id, user_id, logger, google_token_handler, request.app.container,connector,connector_id)
        # Download file based on connector type
        try:
            if connector.lower() == Connectors.GOOGLE_DRIVE.value.lower():
                file_id = external_record_id
                logger.info(f"Downloading Drive file: {file_id}")
                # Build the Drive service
                drive_service = build("drive", "v3", credentials=creds)

                file = await arango_service.get_document(
                    record_id, CollectionNames.FILES.value
                )
                if not file:
                    raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="File not found")
                mime_type = file.get("mimeType", "application/octet-stream")

                if mime_type == "application/vnd.google-apps.presentation":
                    logger.info("🚀 Processing Google Slides")
                    google_slides_parser = await get_google_slides_parser(request)
                    await google_slides_parser.connect_service(
                        user_email, org_id, user_id, connector_id
                    )
                    result = await google_slides_parser.process_presentation(file_id)

                    # Convert result to JSON and return as StreamingResponse
                    json_data = json.dumps(result).encode("utf-8")
                    return StreamingResponse(
                        iter([json_data]), media_type="application/json"
                    )

                if mime_type == "application/vnd.google-apps.document":
                    logger.info("🚀 Processing Google Docs")
                    google_docs_parser = await get_google_docs_parser(request)
                    await google_docs_parser.connect_service(
                        user_email, org_id, user_id, connector_id
                    )
                    content = await google_docs_parser.parse_doc_content(file_id)
                    all_content, headers, footers = (
                        google_docs_parser.order_document_content(content)
                    )
                    result = {
                        "all_content": all_content,
                        "headers": headers,
                        "footers": footers,
                    }

                    # Convert result to JSON and return as StreamingResponse
                    json_data = json.dumps(result).encode("utf-8")
                    return StreamingResponse(
                        iter([json_data]), media_type="application/json"
                    )

                if mime_type == "application/vnd.google-apps.spreadsheet":
                    logger.info("🚀 Processing Google Sheets")
                    google_sheets_parser = await get_google_sheets_parser(request)
                    await google_sheets_parser.connect_service(
                        user_email, org_id, user_id, connector_id
                    )
                    llm, _ = await get_llm(config_service)
                    # List and process spreadsheets
                    parsed_result = await google_sheets_parser.parse_spreadsheet(
                        file_id
                    )
                    all_sheet_results = []
                    for sheet_idx, sheet in enumerate(parsed_result["sheets"], 1):
                        sheet_name = sheet["name"]

                        # Process sheet with summaries
                        sheet_data = (
                            await google_sheets_parser.process_sheet_with_summaries(
                                llm, sheet_name, file_id
                            )
                        )
                        if sheet_data is None:
                            continue

                        all_sheet_results.append(sheet_data)

                    result = {
                        "parsed_result": parsed_result,
                        "all_sheet_results": all_sheet_results,
                    }

                    # Convert result to JSON and return as StreamingResponse
                    json_data = json.dumps(result).encode("utf-8")
                    logger.info("Streaming Google Sheets result")
                    return StreamingResponse(
                        iter([json_data]), media_type="application/json"
                    )

                # Enhanced logging for regular file download
                logger.info(f"Starting binary file download for file_id: {file_id}")

                async def file_stream() -> AsyncGenerator[bytes, None]:
                    file_buffer = io.BytesIO()
                    try:
                        logger.info("Initiating download process...")
                        request = drive_service.files().get_media(fileId=file_id)
                        downloader = MediaIoBaseDownload(file_buffer, request)

                        done = False
                        while not done:
                            status, done = downloader.next_chunk()
                            logger.info(f"Download {int(status.progress() * 100)}%.")

                        # Reset buffer position to start
                        file_buffer.seek(0)

                        # Stream the response with content type from metadata
                        logger.info("Initiating streaming response...")
                        yield file_buffer.read()

                    except Exception as download_error:
                        logger.error(f"Download failed: {repr(download_error)}")
                        if hasattr(download_error, "response"):
                            logger.error(
                                f"Response status: {download_error.response.status_code}"
                            )
                            logger.error(
                                f"Response content: {download_error.response.content}"
                            )
                        raise HTTPException(
                            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                            detail=f"File download failed: {repr(download_error)}",
                        )
                    finally:
                        file_buffer.close()

                # Return streaming response with proper headers
                headers = {
                    "Content-Disposition": f'attachment; filename="{record.record_name}"'
                }

                return StreamingResponse(
                    file_stream(), media_type=mime_type, headers=headers
                )

            elif connector.lower() == Connectors.GOOGLE_MAIL.value.lower():
                file_id = external_record_id
                logger.info(f"Downloading Gmail attachment for record_id: {record_id}")
                gmail_service = build("gmail", "v1", credentials=creds)

                # Get the related message's externalRecordId using AQL
                aql_query = f"""
                FOR v, e IN 1..1 ANY '{CollectionNames.RECORDS.value}/{record_id}' {CollectionNames.RECORD_RELATIONS.value}
                    FILTER e.relationType == '{RecordRelations.ATTACHMENT.value}'
                    RETURN {{
                        messageId: v.externalRecordId,
                        _key: v._key,
                        relationType: e.relationType
                    }}
                """

                cursor = arango_service.db.aql.execute(aql_query)
                messages = list(cursor)

                async def attachment_stream() -> AsyncGenerator[bytes, None]:
                    try:
                        # First try getting the attachment from Gmail
                        message_id = None
                        if messages and messages[0]:
                            message = messages[0]
                            message_id = message["messageId"]
                            logger.info(f"Found message ID: {message_id}")
                        else:
                            logger.warning("Related message not found, returning empty buffer")
                            yield b""
                            return

                        try:
                            # Check if file_id is a combined ID (messageId_partId format)
                            actual_attachment_id = file_id
                            if "_" in file_id:
                                try:
                                    message_id, part_id = file_id.split("_", 1)

                                    # Fetch the message to get the actual attachment ID
                                    try:
                                        message = (
                                            gmail_service.users()
                                            .messages()
                                            .get(userId="me", id=message_id, format="full")
                                            .execute()
                                        )
                                    except Exception as access_error:
                                        if hasattr(access_error, 'resp') and access_error.resp.status == HttpStatusCode.NOT_FOUND.value:
                                            logger.info(f"Message not found with ID {message_id}, searching for related messages...")

                                            # Get messageIdHeader from the original mail
                                            file_key = await arango_service.get_key_by_external_message_id(message_id)
                                            aql_query = """
                                            FOR mail IN mails
                                                FILTER mail._key == @file_key
                                                RETURN mail.messageIdHeader
                                            """
                                            bind_vars = {"file_key": file_key}
                                            cursor = arango_service.db.aql.execute(aql_query, bind_vars=bind_vars)
                                            message_id_header = next(cursor, None)

                                            if not message_id_header:
                                                raise HTTPException(
                                                    status_code=HttpStatusCode.NOT_FOUND.value,
                                                    detail="Original mail not found"
                                                )

                                            # Find all mails with the same messageIdHeader
                                            aql_query = """
                                            FOR mail IN mails
                                                FILTER mail.messageIdHeader == @message_id_header
                                                AND mail._key != @file_key
                                                RETURN mail._key
                                            """
                                            bind_vars = {"message_id_header": message_id_header, "file_key": file_key}
                                            cursor = arango_service.db.aql.execute(aql_query, bind_vars=bind_vars)
                                            related_mail_keys = list(cursor)

                                            # Try each related mail ID until we find one that works
                                            message = None
                                            for related_key in related_mail_keys:
                                                related_mail = await arango_service.get_document(related_key, CollectionNames.RECORDS.value)
                                                related_message_id = related_mail.get("externalRecordId")
                                                try:
                                                    message = (
                                                        gmail_service.users()
                                                        .messages()
                                                        .get(userId="me", id=related_message_id, format="full")
                                                        .execute()
                                                    )
                                                    if message:
                                                        logger.info(f"Found accessible message with ID: {related_message_id}")
                                                        message_id = related_message_id  # Update message_id to use the accessible one
                                                        break
                                                except Exception as e:
                                                    logger.warning(f"Failed to fetch message with ID {related_message_id}: {str(e)}")
                                                    continue

                                            if not message:
                                                raise HTTPException(
                                                    status_code=HttpStatusCode.NOT_FOUND.value,
                                                    detail="No accessible messages found."
                                                )
                                        else:
                                            raise access_error

                                    if not message or "payload" not in message:
                                        raise Exception(f"Message or payload not found for message ID {message_id}")

                                    # Search for the part with matching partId
                                    parts = message["payload"].get("parts", [])
                                    for part in parts:
                                        if part.get("partId") == part_id:
                                            actual_attachment_id = part.get("body", {}).get("attachmentId")
                                            if not actual_attachment_id:
                                                raise Exception("Attachment ID not found in part body")
                                            logger.info(f"Found attachment ID: {actual_attachment_id}")
                                            break
                                    else:
                                        raise Exception("Part ID not found in message")

                                except Exception as e:
                                    logger.error(f"Error extracting attachment ID: {str(e)}")
                                    raise HTTPException(
                                        status_code=HttpStatusCode.BAD_REQUEST.value,
                                        detail=f"Invalid attachment ID format: {str(e)}"
                                    )

                            # Try to get the attachment with potential fallback message_id
                            try:
                                attachment = (
                                    gmail_service.users()
                                    .messages()
                                    .attachments()
                                    .get(userId="me", messageId=message_id, id=actual_attachment_id)
                                    .execute()
                                )
                            except Exception as attachment_error:
                                if hasattr(attachment_error, 'resp') and attachment_error.resp.status == HttpStatusCode.NOT_FOUND.value:
                                    raise HTTPException(
                                        status_code=HttpStatusCode.NOT_FOUND.value,
                                        detail="Attachment not found in accessible messages"
                                    )
                                raise attachment_error

                            # Decode the attachment data
                            file_data = base64.urlsafe_b64decode(attachment["data"])
                            yield file_data

                        except Exception as gmail_error:
                            logger.info(
                                f"Failed to get attachment from Gmail: {str(gmail_error)}, trying Drive..."
                            )

                            # Try to get the file from Drive as fallback
                            file_buffer = io.BytesIO()
                            try:
                                drive_service = build("drive", "v3", credentials=creds)
                                request = drive_service.files().get_media(
                                    fileId=file_id
                                )
                                downloader = MediaIoBaseDownload(file_buffer, request)

                                done = False
                                while not done:
                                    status, done = downloader.next_chunk()
                                    logger.info(
                                        f"Download {int(status.progress() * 100)}%."
                                    )

                                    # Yield current chunk and reset buffer
                                    file_buffer.seek(0)
                                    yield file_buffer.getvalue()
                                    file_buffer.seek(0)
                                    file_buffer.truncate()

                            except Exception as drive_error:
                                logger.error(
                                    f"Failed to get file from both Gmail and Drive. Gmail error: {str(gmail_error)}, Drive error: {str(drive_error)}"
                                )
                                raise HTTPException(
                                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                                    detail="Failed to download file from both Gmail and Drive",
                                )
                            finally:
                                file_buffer.close()

                    except Exception as e:
                        logger.error(f"Error in attachment stream: {str(e)}")
                        raise HTTPException(
                            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                            detail=f"Error streaming attachment: {str(e)}",
                        )

                return StreamingResponse(
                    attachment_stream(), media_type="application/octet-stream"
                )

            else:
                container: ConnectorAppContainer = request.app.container
                connector: BaseConnector = container.connectors_map[connector_id]
                buffer = await connector.stream_record(record)
                return buffer

        except Exception as e:
            logger.error(f"Error downloading file: {str(e)}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail=f"Error downloading file: {str(e)}"
            )

    except HTTPException as e:
        logger.error("HTTPException: %s", str(e))
        raise e
    except Exception as e:
        logger.error("Error downloading file: %s", str(e))
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Error downloading file")


@router.get("/api/v1/stream/record/{record_id}", response_model=None)
@inject
async def stream_record(
    request: Request,
    record_id: str,
    convertTo: Optional[str] = None,
    arango_service: BaseArangoService = Depends(Provide[ConnectorAppContainer.arango_service]),
    google_token_handler: GoogleTokenHandler = Depends(Provide[ConnectorAppContainer.google_token_handler]),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Optional[dict | StreamingResponse]:
    """
    Stream a record to the client.
    """
    try:
        try:
            logger.info(f"Stream Record Start: {time.time()}")
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                raise HTTPException(
                    status_code=HttpStatusCode.UNAUTHORIZED.value,
                    detail="Missing or invalid Authorization header",
                )
            # Extract the token
            token = auth_header.split(" ")[1]
            secret_keys = await config_service.get_config(
                config_node_constants.SECRET_KEYS.value
            )
            jwt_secret = secret_keys.get("jwtSecret")
            payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
            org_id = payload.get("orgId")
            user_id = payload.get("userId")
        except JWTError as e:
            logger.error("JWT validation error: %s", str(e))
            raise HTTPException(status_code=HttpStatusCode.UNAUTHORIZED.value, detail="Invalid or expired token")
        except ValidationError as e:
            logger.error("Payload validation error: %s", str(e))
            raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Invalid token payload")
        except Exception as e:
            logger.error("Unexpected error during token validation: %s", str(e))
            raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Error validating token")

        org_task = arango_service.get_document(org_id, CollectionNames.ORGS.value)
        record_task = arango_service.get_record_by_id(
            record_id
        )
        org, record = await asyncio.gather(org_task, record_task)

        if not org:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Organization not found")
        if not record:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Record not found")

        external_record_id = record.external_record_id
        connector = record.connector_name.value
        connector_id = record.connector_id
        recordType = record.record_type
        logger.info(f"Connector: {connector} connector_id: {connector_id}")
        # Different auth handling based on account type and connector scope
        creds = None
        if connector.lower() == Connectors.GOOGLE_DRIVE.value.lower() or connector.lower() == Connectors.GOOGLE_MAIL.value.lower():
            # Get connector instance to check scope
            connector_instance = await arango_service.get_document(connector_id, CollectionNames.APPS.value)
            connector_scope = connector_instance.get("scope", ConnectorScope.PERSONAL.value) if connector_instance else ConnectorScope.PERSONAL.value

            # Use service account credentials only for TEAM scope connectors in enterprise/business accounts
            # Personal scope connectors always use user credentials regardless of account type
            if (org["accountType"] in [AccountType.ENTERPRISE.value, AccountType.BUSINESS.value] and
                connector_scope == ConnectorScope.TEAM.value):
                # Use service account credentials for team scope in enterprise accounts
                creds = await get_service_account_credentials(org_id, user_id, logger, arango_service, google_token_handler, request.app.container,connector, connector_id)
            else:
                # Use user credentials for personal scope or individual accounts
                creds = await get_user_credentials(org_id, user_id,logger, google_token_handler, request.app.container,connector=connector, connector_id=connector_id)
        # Download file based on connector type
        try:
            if connector.lower() == Connectors.GOOGLE_DRIVE.value.lower():
                file_id = external_record_id
                logger.info(f"Downloading Drive file: {file_id}")
                drive_service = build("drive", "v3", credentials=creds)
                file_name = record.record_name
                file = await arango_service.get_document(
                    record_id, CollectionNames.FILES.value
                )
                if not file:
                    raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="File not found")

                mime_type = file.get("mimeType", "application/octet-stream")

                # Check if PDF conversion is requested
                if convertTo == MimeTypes.PDF.value:
                    with tempfile.TemporaryDirectory() as temp_dir:
                        temp_file_path = os.path.join(temp_dir, file_name)

                        # Download file to temp directory
                        with open(temp_file_path, "wb") as f:
                            request = drive_service.files().get_media(fileId=file_id)
                            downloader = MediaIoBaseDownload(f, request)

                            done = False
                            while not done:
                                status, done = downloader.next_chunk()
                                logger.info(
                                    f"Download {int(status.progress() * 100)}%."
                                )

                        # Convert to PDF
                        pdf_path = await convert_to_pdf(temp_file_path, temp_dir)

                        # Create async generator to properly handle file cleanup
                        async def file_iterator() -> AsyncGenerator[bytes, None]:
                            try:
                                with open(pdf_path, "rb") as pdf_file:
                                    yield await asyncio.to_thread(pdf_file.read)
                            except Exception as e:
                                logger.error(f"Error reading PDF file: {str(e)}")
                                raise HTTPException(
                                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                                    detail="Error reading converted PDF file",
                                )

                        return StreamingResponse(
                            file_iterator(),
                            media_type="application/pdf",
                            headers={
                                "Content-Disposition": f'inline; filename="{Path(file_name).stem}.pdf"'
                            },
                        )

                # Regular file download without conversion - now with direct streaming
                async def file_stream() -> AsyncGenerator[bytes, None]:
                    try:
                        chunk_count = 0
                        total_bytes = 0

                        request = drive_service.files().get_media(fileId=file_id)
                        buffer = io.BytesIO()
                        downloader = MediaIoBaseDownload(buffer, request)

                        done = False
                        while not done:
                            try:
                                _ , done = downloader.next_chunk()
                                chunk_count += 1

                                buffer.seek(0)
                                chunk = buffer.read()
                                total_bytes += len(chunk)

                                if chunk:  # Only yield if we have data
                                    yield chunk

                                # Clear buffer for next chunk
                                buffer.seek(0)
                                buffer.truncate(0)

                                # Yield control back to event loop
                                await asyncio.sleep(0)

                            except Exception as chunk_error:
                                logger.error(
                                    f"Error streaming chunk: {str(chunk_error)}"
                                )
                                raise HTTPException(
                                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                                    detail="Error during file streaming",
                                )

                    except Exception as stream_error:
                        logger.error(f"Error in file stream: {str(stream_error)}")
                        raise HTTPException(
                            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Error setting up file stream"
                        )
                    finally:
                        buffer.close()


                # Return streaming response with proper headers
                headers = {"Content-Disposition": f'attachment; filename="{file_name}"'}
                return StreamingResponse(
                    file_stream(), media_type=mime_type, headers=headers
                )

            elif connector.lower() == Connectors.GOOGLE_MAIL.value.lower():
                file_id = external_record_id
                logger.info(
                    f"Handling Gmail request for record_id: {record_id}, type: {recordType}"
                )
                gmail_service = build("gmail", "v1", credentials=creds)

                if recordType == RecordTypes.MAIL.value:
                    try:
                        # First attempt to fetch the message directly
                        try:
                            message = (
                                gmail_service.users()
                                .messages()
                                .get(userId="me", id=file_id, format="full")
                                .execute()
                            )
                        except Exception as access_error:
                            if hasattr(access_error, 'resp') and access_error.resp.status == HttpStatusCode.NOT_FOUND.value:
                                logger.info(f"Message not found with ID {file_id}, searching for related messages...")

                                # Get messageIdHeader from the original mail
                                file_key = await arango_service.get_key_by_external_message_id(file_id)
                                aql_query = """
                                FOR mail IN mails
                                    FILTER mail._key == @file_key
                                    RETURN mail.messageIdHeader
                                """
                                bind_vars = {"file_key": file_key}
                                cursor = arango_service.db.aql.execute(aql_query, bind_vars=bind_vars)
                                message_id_header = next(cursor, None)

                                if not message_id_header:
                                    raise HTTPException(
                                        status_code=HttpStatusCode.NOT_FOUND.value,
                                        detail="Original mail not found"
                                    )

                                # Find all mails with the same messageIdHeader
                                aql_query = """
                                FOR mail IN mails
                                    FILTER mail.messageIdHeader == @message_id_header
                                    AND mail._key != @file_key
                                    RETURN mail._key
                                """
                                bind_vars = {"message_id_header": message_id_header, "file_key": file_key}
                                cursor = arango_service.db.aql.execute(aql_query, bind_vars=bind_vars)
                                related_mail_keys = list(cursor)

                                # Try each related mail ID until we find one that works
                                message = None
                                for related_key in related_mail_keys:
                                    related_mail = await arango_service.get_document(related_key, CollectionNames.RECORDS.value)
                                    related_id = related_mail.get("externalRecordId")
                                    try:
                                        message = (
                                            gmail_service.users()
                                            .messages()
                                            .get(userId="me", id=related_id, format="full")
                                            .execute()
                                        )
                                        if message:
                                            logger.info(f"Found accessible message with ID: {related_id}")
                                            break
                                    except Exception as e:
                                        logger.warning(f"Failed to fetch message with ID {related_id}: {str(e)}")
                                        continue

                                if not message:
                                    raise HTTPException(
                                        status_code=HttpStatusCode.NOT_FOUND.value,
                                        detail="No accessible messages found."
                                    )
                            else:
                                raise access_error

                        # Continue with existing code for processing the message
                        def extract_body(payload: dict) -> str:
                            # If there are no parts, return the direct body data
                            if "parts" not in payload:
                                return payload.get("body", {}).get("data", "")

                            # Search for a text/html part that isn't an attachment (empty filename)
                            for part in payload.get("parts", []):
                                if (
                                    part.get("mimeType") == "text/html"
                                    and part.get("filename", "") == ""
                                ):
                                    content = part.get("body", {}).get("data", "")
                                    return content

                            # Fallback: if no html text, try to use text/plain
                            for part in payload.get("parts", []):
                                if (
                                    part.get("mimeType") == "text/plain"
                                    and part.get("filename", "") == ""
                                ):
                                    content = part.get("body", {}).get("data", "")
                                    return content
                            return ""

                        # Extract the encoded body content
                        mail_content_base64 = extract_body(message.get("payload", {}))
                        # Decode the Gmail URL-safe base64 encoded content; errors are replaced to avoid issues with malformed text
                        mail_content = base64.urlsafe_b64decode(
                            mail_content_base64.encode("ASCII")
                        ).decode("utf-8", errors="replace")

                        # Async generator to stream only the mail content
                        async def message_stream() -> AsyncGenerator[bytes, None]:
                            yield mail_content.encode("utf-8")

                        # Return the streaming response with only the mail body
                        return StreamingResponse(
                            message_stream(), media_type="text/plain"
                        )
                    except Exception as mail_error:
                        logger.error(f"Failed to fetch mail content: {str(mail_error)}")
                        raise HTTPException(
                            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to fetch mail content"
                        )

                # Handle attachment download
                logger.info(f"Downloading Gmail attachment for record_id: {record_id}")

                # Get file metadata first
                file = await arango_service.get_document(
                    record_id, CollectionNames.FILES.value
                )
                if not file:
                    raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="File not found")

                file_name = file.get("name", "")
                mime_type = file.get("mimeType", "application/octet-stream")

                # Get the related message's externalRecordId using AQL
                aql_query = f"""
                FOR v, e IN 1..1 ANY '{CollectionNames.RECORDS.value}/{record_id}' {CollectionNames.RECORD_RELATIONS.value}
                    FILTER e.relationType == '{RecordRelations.ATTACHMENT.value}'
                    RETURN {{
                        messageId: v.externalRecordId,
                        _key: v._key,
                        relationType: e.relationType
                    }}
                """

                cursor = arango_service.db.aql.execute(aql_query)
                messages = list(cursor)

                # First try getting the attachment from Gmail
                try:
                    message_id = None
                    if messages and messages[0]:
                        message = messages[0]
                        message_id = message["messageId"]
                        logger.info(f"Found message ID: {message_id}")
                    else:
                        raise Exception("Related message not found")

                    # Check if file_id is a combined ID (messageId_partId format)
                    actual_attachment_id = file_id
                    if "_" in file_id:
                        try:
                            message_id, part_id = file_id.split("_", 1)

                            # Fetch the message to get the actual attachment ID
                            try:
                                message = (
                                    gmail_service.users()
                                    .messages()
                                    .get(userId="me", id=message_id, format="full")
                                    .execute()
                                )
                            except Exception as access_error:
                                if hasattr(access_error, 'resp') and access_error.resp.status == HttpStatusCode.NOT_FOUND.value:
                                    logger.info(f"Message not found with ID {message_id}, searching for related messages...")

                                    # Get messageIdHeader from the original mail
                                    file_key = await arango_service.get_key_by_external_message_id(message_id)
                                    aql_query = """
                                    FOR mail IN mails
                                        FILTER mail._key == @file_key
                                        RETURN mail.messageIdHeader
                                    """
                                    bind_vars = {"file_key": file_key}
                                    cursor = arango_service.db.aql.execute(aql_query, bind_vars=bind_vars)
                                    message_id_header = next(cursor, None)

                                    if not message_id_header:
                                        raise HTTPException(
                                            status_code=HttpStatusCode.NOT_FOUND.value,
                                            detail="Original mail not found"
                                        )

                                    # Find all mails with the same messageIdHeader
                                    aql_query = """
                                    FOR mail IN mails
                                        FILTER mail.messageIdHeader == @message_id_header
                                        AND mail._key != @file_key
                                        RETURN mail._key
                                    """
                                    bind_vars = {"message_id_header": message_id_header, "file_key": file_key}
                                    cursor = arango_service.db.aql.execute(aql_query, bind_vars=bind_vars)
                                    related_mail_keys = list(cursor)

                                    # Try each related mail ID until we find one that works
                                    message = None
                                    for related_key in related_mail_keys:
                                        related_mail = await arango_service.get_document(related_key, CollectionNames.RECORDS.value)
                                        related_message_id = related_mail.get("externalRecordId")
                                        try:
                                            message = (
                                                gmail_service.users()
                                                .messages()
                                                .get(userId="me", id=related_message_id, format="full")
                                                .execute()
                                            )
                                            if message:
                                                logger.info(f"Found accessible message with ID: {related_message_id}")
                                                message_id = related_message_id  # Update message_id to use the accessible one
                                                break
                                        except Exception as e:
                                            logger.warning(f"Failed to fetch message with ID {related_message_id}: {str(e)}")
                                            continue

                                    if not message:
                                        raise HTTPException(
                                            status_code=HttpStatusCode.NOT_FOUND.value,
                                            detail="No accessible messages found."
                                        )
                                else:
                                    raise access_error

                            if not message or "payload" not in message:
                                raise Exception(f"Message or payload not found for message ID {message_id}")

                            # Search for the part with matching partId
                            parts = message["payload"].get("parts", [])
                            for part in parts:
                                if part.get("partId") == part_id:
                                    actual_attachment_id = part.get("body", {}).get("attachmentId")
                                    if not actual_attachment_id:
                                        raise Exception("Attachment ID not found in part body")
                                    logger.info(f"Found attachment ID: {actual_attachment_id}")
                                    break
                            else:
                                raise Exception("Part ID not found in message")

                        except Exception as e:
                            logger.error(f"Error extracting attachment ID: {str(e)}")
                            raise HTTPException(
                                status_code=HttpStatusCode.BAD_REQUEST.value,
                                detail=f"Invalid attachment ID format: {str(e)}"
                            )

                    # Try to get the attachment with potential fallback message_id
                    try:
                        attachment = (
                            gmail_service.users()
                            .messages()
                            .attachments()
                            .get(userId="me", messageId=message_id, id=actual_attachment_id)
                            .execute()
                        )
                    except Exception as attachment_error:
                        if hasattr(attachment_error, 'resp') and attachment_error.resp.status == HttpStatusCode.NOT_FOUND.value:
                            raise HTTPException(
                                status_code=HttpStatusCode.NOT_FOUND.value,
                                detail="Attachment not found in accessible messages"
                            )
                        raise attachment_error

                    # Decode the attachment data
                    file_data = base64.urlsafe_b64decode(attachment["data"])

                    if convertTo == MimeTypes.PDF.value:
                        with tempfile.TemporaryDirectory() as temp_dir:
                            temp_file_path = os.path.join(temp_dir, file_name)

                            # Write attachment data to temp file
                            with open(temp_file_path, "wb") as f:
                                f.write(file_data)

                            # Convert to PDF
                            pdf_path = await convert_to_pdf(temp_file_path, temp_dir)
                            return StreamingResponse(
                                open(pdf_path, "rb"),
                                media_type="application/pdf",
                                headers={
                                    "Content-Disposition": f'inline; filename="{Path(file_name).stem}.pdf"'
                                },
                            )

                    # Return original file if no conversion requested
                    return StreamingResponse(
                        iter([file_data]), media_type="application/octet-stream"
                    )

                except Exception as gmail_error:
                    logger.info(
                        f"Failed to get attachment from Gmail: {str(gmail_error)}, trying Drive..."
                    )

                    # Try Drive as fallback
                    try:
                        drive_service = build("drive", "v3", credentials=creds)

                        if convertTo == MimeTypes.PDF.value:
                            with tempfile.TemporaryDirectory() as temp_dir:
                                temp_file_path = os.path.join(temp_dir, file_name)

                                # Download from Drive to temp file
                                with open(temp_file_path, "wb") as f:
                                    request = drive_service.files().get_media(
                                        fileId=file_id
                                    )
                                    downloader = MediaIoBaseDownload(f, request)

                                    done = False
                                    while not done:
                                        status, done = downloader.next_chunk()
                                        logger.info(
                                            f"Download {int(status.progress() * 100)}%."
                                        )

                                # Convert to PDF
                                pdf_path = await convert_to_pdf(
                                    temp_file_path, temp_dir
                                )
                                return StreamingResponse(
                                    open(pdf_path, "rb"),
                                    media_type="application/pdf",
                                    headers={
                                        "Content-Disposition": f'inline; filename="{Path(file_name).stem}.pdf"'
                                    },
                                )


                        headers = {
                            "Content-Disposition": f'attachment; filename="{file_name}"'
                        }

                        # Use the same streaming logic as Drive downloads
                        async def file_stream() -> AsyncGenerator[bytes, None]:
                            try:
                                request = drive_service.files().get_media(
                                    fileId=file_id
                                )
                                buffer = io.BytesIO()
                                downloader = MediaIoBaseDownload(buffer, request)

                                done = False
                                while not done:
                                    try:
                                        status, done = downloader.next_chunk()
                                        if status:
                                            logger.debug(
                                                f"Download progress: {int(status.progress() * 100)}%"
                                            )

                                        buffer.seek(0)
                                        chunk = buffer.read()

                                        if chunk:
                                            yield chunk

                                        buffer.seek(0)
                                        buffer.truncate(0)

                                        await asyncio.sleep(0)

                                    except Exception as chunk_error:
                                        logger.error(
                                            f"Error streaming chunk: {str(chunk_error)}"
                                        )
                                        raise HTTPException(
                                            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                                            detail="Error during file streaming",
                                        )

                            except Exception as stream_error:
                                logger.error(
                                    f"Error in file stream: {str(stream_error)}"
                                )
                                raise HTTPException(
                                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                                    detail="Error setting up file stream",
                                )
                            finally:
                                buffer.close()

                        return StreamingResponse(
                            file_stream(), media_type=mime_type, headers=headers
                        )

                    except Exception as drive_error:
                        logger.error(
                            f"Failed to get file from both Gmail and Drive. Gmail error: {str(gmail_error)}, Drive error: {str(drive_error)}"
                        )
                        raise HTTPException(
                            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                            detail="Failed to download file from both Gmail and Drive",
                        )
            else:
                container: ConnectorAppContainer = request.app.container
                connector: BaseConnector = container.connectors_map[connector_id]
                buffer = await connector.stream_record(record)
                return buffer
        except Exception as e:
            logger.error(f"Error downloading file: {str(e)}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail=f"Error downloading file: {str(e)}"
            )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("Error downloading file: %s", str(e))
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Error downloading file")


@router.post("/api/v1/record/buffer/convert")
async def get_record_stream(request: Request, file: UploadFile = File(...)) -> StreamingResponse:
    request.query_params.get("from")
    to_format = request.query_params.get("to")

    if to_format == MimeTypes.PDF.value:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                try:
                    ppt_path = os.path.join(tmpdir, file.filename)
                    with open(ppt_path, "wb") as f:
                        f.write(await file.read())

                    conversion_cmd = [
                        "libreoffice",
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        tmpdir,
                        ppt_path,
                    ]
                    process = await asyncio.create_subprocess_exec(
                        *conversion_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )

                    try:
                        conversion_output, conversion_error = await asyncio.wait_for(
                            process.communicate(), timeout=30.0
                        )
                    except asyncio.TimeoutError:
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            process.kill()
                        logger.error(
                            "LibreOffice conversion timed out after 30 seconds"
                        )
                        raise HTTPException(
                            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="PDF conversion timed out"
                        )

                    pdf_filename = file.filename.rsplit(".", 1)[0] + ".pdf"
                    pdf_path = os.path.join(tmpdir, pdf_filename)

                    if process.returncode != 0:
                        error_msg = f"LibreOffice conversion failed: {conversion_error.decode('utf-8', errors='replace')}"
                        logger.error(error_msg)
                        raise HTTPException(
                            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to convert file to PDF"
                        )

                    if not os.path.exists(pdf_path):
                        raise FileNotFoundError(
                            "PDF conversion failed - output file not found"
                        )

                    async def file_iterator() -> AsyncGenerator[bytes, None]:
                        try:
                            with open(pdf_path, "rb") as pdf_file:
                                yield await asyncio.to_thread(pdf_file.read)
                        except Exception as e:
                            logger.error(f"Error reading PDF file: {str(e)}")
                            raise HTTPException(
                                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                                detail="Error reading converted PDF file",
                            )

                    return StreamingResponse(
                        file_iterator(),
                        media_type="application/pdf",
                        headers={
                            "Content-Disposition": f"attachment; filename={pdf_filename}"
                        },
                    )

                except FileNotFoundError as e:
                    logger.error(str(e))
                    raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail=str(e))
                except Exception as e:
                    logger.error(f"Conversion error: {str(e)}")
                    raise HTTPException(
                        status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail=f"Conversion error: {str(e)}"
                    )
        finally:
            await file.close()

    raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Invalid conversion request")


async def get_admin_webhook_handler(request: Request) -> Optional[AdminWebhookHandler]:
    try:
        container: ConnectorAppContainer = request.app.container
        admin_webhook_handler = container.admin_webhook_handler()
        return admin_webhook_handler
    except Exception as e:
        logger.warning(f"Failed to get admin webhook handler: {str(e)}")
        return None


@router.post("/admin/webhook")
@inject
async def handle_admin_webhook(request: Request, background_tasks: BackgroundTasks) -> Optional[Dict[str, Any]]:
    """Handle incoming webhook notifications from Google Workspace Admin"""
    try:
        verifier = WebhookAuthVerifier(logger)
        if not await verifier.verify_request(request):
            raise HTTPException(status_code=HttpStatusCode.UNAUTHORIZED.value, detail="Unauthorized webhook request")

        admin_webhook_handler = await get_admin_webhook_handler(request)

        if admin_webhook_handler is None:
            logger.warning(
                "Admin webhook handler not yet initialized - skipping webhook processing"
            )
            return {
                "status": "skipped",
                "message": "Webhook handler not yet initialized",
            }

        # Try to get the request body, handle empty body case
        try:
            body = await request.json()
        except json.JSONDecodeError:
            # This might be a verification request
            logger.info(
                "Received request with empty/invalid JSON body - might be verification request"
            )
            return {"status": "accepted", "message": "Verification request received"}

        logger.info("📥 Incoming admin webhook request: %s", body)

        # Get the event type from the events array
        events = body.get("events", [])
        if not events:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value, detail="No events found in webhook body"
            )

        event_type = events[0].get("name")  # We'll process the first event
        if not event_type:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value, detail="Missing event name in webhook body"
            )

        # Process notification in background
        background_tasks.add_task(
            admin_webhook_handler.process_notification, event_type, body
        )
        return {"status": "accepted"}

    except Exception as e:
        logger.error("Error processing webhook: %s", str(e))
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail=str(e)
        )


async def convert_to_pdf(file_path: str, temp_dir: str) -> str:
    """Helper function to convert file to PDF"""
    pdf_path = os.path.join(temp_dir, f"{Path(file_path).stem}.pdf")

    try:
        conversion_cmd = [
            "soffice",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            temp_dir,
            file_path,
        ]
        process = await asyncio.create_subprocess_exec(
            *conversion_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Add timeout to communicate
        try:
            conversion_output, conversion_error = await asyncio.wait_for(
                process.communicate(), timeout=30.0
            )
        except asyncio.TimeoutError:
            # Make sure to terminate the process if it times out
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()  # Force kill if termination takes too long
            logger.error("LibreOffice conversion timed out after 30 seconds")
            raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="PDF conversion timed out")

        if process.returncode != 0:
            error_msg = f"LibreOffice conversion failed: {conversion_error.decode('utf-8', errors='replace')}"
            logger.error(error_msg)
            raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to convert file to PDF")

        if os.path.exists(pdf_path):
            return pdf_path
        else:
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="PDF conversion failed - output file not found"
            )
    except asyncio.TimeoutError:
        # This catch is for any other timeout that might occur
        logger.error("Timeout during PDF conversion")
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="PDF conversion timed out")
    except Exception as conv_error:
        logger.error(f"Error during conversion: {str(conv_error)}")
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Error converting file to PDF")


async def get_service_account_credentials(org_id: str, user_id: str, logger, arango_service, google_token_handler, container,connector: str, connector_id: str) -> google.oauth2.credentials.Credentials:
    """Helper function to get service account credentials"""
    try:
        service_creds_lock = container.service_creds_lock()

        async with service_creds_lock:
            if not hasattr(container, 'service_creds_cache'):
                container.service_creds_cache = {}
                logger.info("Created service credentials cache")

            cache_key = f"{org_id}_{user_id}_{connector_id}"
            logger.info(f"Service account cache key: {cache_key}")

            if cache_key in container.service_creds_cache:
                logger.info(f"Service account cache hit: {cache_key}")
                return container.service_creds_cache[cache_key]

            # Cache miss - create new credentials
            logger.info(f"Service account cache miss: {cache_key}. Creating new credentials.")

            # Get user email
            user = await arango_service.get_user_by_user_id(user_id)
            if not user:
                raise Exception(f"User not found: {user_id}")

            # Create new credentials
            SCOPES = GOOGLE_CONNECTOR_ENTERPRISE_SCOPES
            credentials_json = await google_token_handler.get_enterprise_token(connector_id=connector_id)
            credentials = service_account.Credentials.from_service_account_info(
                credentials_json, scopes=SCOPES
            )
            credentials = credentials.with_subject(user["email"])

            # Cache the credentials
            container.service_creds_cache[cache_key] = credentials
            logger.info(f"Cached new service credentials for {cache_key}")

            return credentials

    except Exception as e:
        logger.error(f"Error getting service account credentials: {str(e)}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Error accessing service account credentials"
        )

async def get_user_credentials(org_id: str, user_id: str, logger, google_token_handler, container,connector: str, connector_id: str) -> google.oauth2.credentials.Credentials:
    """Helper function to get cached user credentials"""
    try:
        cache_key = f"{org_id}_{user_id}_{connector_id}"
        user_creds_lock = container.user_creds_lock()

        async with user_creds_lock:
            if not hasattr(container, 'user_creds_cache'):
                container.user_creds_cache = {}
                logger.info("Created user credentials cache")

            logger.info(f"User credentials cache key: {cache_key}")

            if cache_key in container.user_creds_cache:
                creds = container.user_creds_cache[cache_key]
                logger.info(f"Expiry time: {creds.expiry}")
                expiry = creds.expiry

                try:
                    now = datetime.now(timezone.utc).replace(tzinfo=None)
                    # Add 5 minute buffer before expiry to ensure we refresh early
                    buffer_time = timedelta(minutes=5)

                    if expiry and (expiry - buffer_time) > now:
                        logger.info(f"User credentials cache hit: {cache_key}")
                        return creds
                    else:
                        logger.info(f"User credentials expired or expiring soon for {cache_key}")
                        # Remove expired credentials from cache
                        container.user_creds_cache.pop(cache_key, None)
                except Exception as e:
                    logger.error(f"Failed to check credentials for {cache_key}: {str(e)}")
                    container.user_creds_cache.pop(cache_key, None)
            # Cache miss or expired - create new credentials
            logger.info(f"User credentials cache miss: {cache_key}. Creating new credentials.")

            # Create new credentials
            SCOPES = await google_token_handler.get_account_scopes(connector_id=connector_id)
            # Refresh token
            await google_token_handler.refresh_token(connector_id=connector_id)
            creds_data = await google_token_handler.get_individual_token(connector_id=connector_id)

            if not creds_data.get("access_token"):
                raise HTTPException(
                    status_code=HttpStatusCode.UNAUTHORIZED.value,
                    detail="Invalid credentials. Access token not found",
                )

            required_keys = {
                CredentialKeys.ACCESS_TOKEN.value: "Access token not found",
                CredentialKeys.REFRESH_TOKEN.value: "Refresh token not found",
                CredentialKeys.CLIENT_ID.value: "Client ID not found",
                CredentialKeys.CLIENT_SECRET.value: "Client secret not found",
            }

            for key, error_detail in required_keys.items():
                if not creds_data.get(key):
                    logger.error(f"Missing {key} in credentials")
                    raise HTTPException(
                        status_code=HttpStatusCode.UNAUTHORIZED.value,
                        detail=f"Invalid credentials. {error_detail}",
                    )

            access_token = creds_data.get(CredentialKeys.ACCESS_TOKEN.value)
            refresh_token = creds_data.get(CredentialKeys.REFRESH_TOKEN.value)
            client_id = creds_data.get(CredentialKeys.CLIENT_ID.value)
            client_secret = creds_data.get(CredentialKeys.CLIENT_SECRET.value)

            new_creds = google.oauth2.credentials.Credentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=SCOPES,
            )

            # Update token expiry time - make it timezone-naive for Google client compatibility
            token_expiry = datetime.fromtimestamp(
                creds_data.get("access_token_expiry_time", 0) / 1000, timezone.utc
            ).replace(tzinfo=None)  # Convert to naive UTC for Google client compatibility
            new_creds.expiry = token_expiry

            # Cache the credentials
            container.user_creds_cache[cache_key] = new_creds
            logger.info(f"Cached new user credentials for {cache_key} with expiry: {new_creds.expiry}")

            return new_creds

    except Exception as e:
        logger.error(f"Error getting user credentials: {str(e)}")
        # Remove from cache if there's an error
        if hasattr(container, 'user_creds_cache'):
            container.user_creds_cache.pop(cache_key, None)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Error accessing user credentials"
        )


@router.get("/api/v1/records")
@inject
async def get_records(
    request:Request,
    arango_service: BaseArangoService = Depends(get_arango_service),
    page: int = 1,
    limit: int = 20,
    search: Optional[str] = None,
    record_types: Optional[str] = Query(None, description="Comma-separated list of record types"),
    origins: Optional[str] = Query(None, description="Comma-separated list of origins"),
    connectors: Optional[str] = Query(None, description="Comma-separated list of connectors"),
    indexing_status: Optional[str] = Query(None, description="Comma-separated list of indexing statuses"),
    permissions: Optional[str] = Query(None, description="Comma-separated list of permissions"),
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    sort_by: str = "createdAtTimestamp",
    sort_order: str = "desc",
    source: str = "all",
) -> Optional[Dict]:
    """
    List all records the user can access (from all KBs, folders, and direct connector permissions), with filters.
    """
    try:
        container = request.app.container
        logger = container.logger()

        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")

        logger.info(f"Looking up user by user_id: {user_id}")
        user = await arango_service.get_user_by_user_id(user_id=user_id)

        if not user:
            logger.warning(f"⚠️ User not found for user_id: {user_id}")
            return {
                "success": False,
                "code": 404,
                "reason": f"User not found for user_id: {user_id}"
            }
        user_key = user.get('_key')

        skip = (page - 1) * limit
        sort_order = sort_order.lower() if sort_order.lower() in ["asc", "desc"] else "desc"
        sort_by = sort_by if sort_by in [
            "recordName", "createdAtTimestamp", "updatedAtTimestamp", "recordType", "origin", "indexingStatus"
        ] else "createdAtTimestamp"

        # Parse comma-separated strings into lists
        parsed_record_types = _parse_comma_separated_str(record_types)
        parsed_origins = _parse_comma_separated_str(origins)
        parsed_connectors = _parse_comma_separated_str(connectors)
        parsed_indexing_status = _parse_comma_separated_str(indexing_status)
        parsed_permissions = _parse_comma_separated_str(permissions)

        records, total_count, available_filters = await arango_service.get_records(
            user_id=user_key,
            org_id=org_id,
            skip=skip,
            limit=limit,
            search=search,
            record_types=parsed_record_types,
            origins=parsed_origins,
            connectors=parsed_connectors,
            indexing_status=parsed_indexing_status,
            permissions=parsed_permissions,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by,
            sort_order=sort_order,
            source=source,
        )

        total_pages = (total_count + limit - 1) // limit

        applied_filters = {
            k: v for k, v in {
                "search": search,
                "recordTypes": parsed_record_types,
                "origins": parsed_origins,
                "connectors": parsed_connectors,
                "indexingStatus": parsed_indexing_status,
                "source": source if source != "all" else None,
                "dateRange": {"from": date_from, "to": date_to} if date_from or date_to else None,
            }.items() if v
        }

        return {
            "records": records,
            "pagination": {
                "page": page,
                "limit": limit,
                "totalCount": total_count,
                "totalPages": total_pages,
            },
            "filters": {
                "applied": applied_filters,
                "available": available_filters,
            }
        }
    except Exception as e:
        logger.error(f"❌ Failed to list all records: {str(e)}")
        return {
            "records": [],
            "pagination": {"page": page, "limit": limit, "totalCount": 0, "totalPages": 0},
            "filters": {"applied": {}, "available": {}},
            "error": str(e),
        }

@router.get("/api/v1/records/{record_id}")
@inject
async def get_record_by_id(
    record_id: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service),
) -> Optional[Dict]:
    """
    Check if the current user has access to a specific record
    """
    try:
        container = request.app.container
        logger = container.logger()
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")

        has_access = await arango_service.check_record_access_with_details(
            user_id=user_id,
            org_id=org_id,
            record_id=record_id,
        )
        logger.info(f"🚀 has_access: {has_access}")
        if has_access:
            return has_access
        else:
            raise HTTPException(
                status_code=404, detail="You do not have access to this record"
            )
    except Exception as e:
        logger.error(f"Error checking record access: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to check record access")

@router.delete("/api/v1/records/{record_id}")
@inject
async def delete_record(
    record_id: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service),
) -> Dict:
    """
    Delete a specific record with permission validation
    """
    try:
        container = request.app.container
        logger = container.logger()
        user_id = request.state.user.get("userId")
        logger.info(f"🗑️ Attempting to delete record {record_id}")

        result = await arango_service.delete_record(
            record_id=record_id,
            user_id=user_id
        )

        if result["success"]:
            logger.info(f"✅ Successfully deleted record {record_id}")
            return {
                "success": True,
                "message": f"Record {record_id} deleted successfully",
                "recordId": record_id,
                "connector": result.get("connector"),
                "timestamp": result.get("timestamp")
            }
        else:
            logger.error(f"❌ Failed to delete record {record_id}: {result.get('reason')}")
            raise HTTPException(
                status_code=result.get("code", 500),
                detail=result.get("reason", "Failed to delete record")
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error deleting record {record_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while deleting record: {str(e)}"
        )

@router.post("/api/v1/records/{record_id}/reindex")
@inject
async def reindex_single_record(
    record_id: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service),
) -> Dict:
    """
    Reindex a single record with permission validation
    """
    try:
        container = request.app.container
        logger = container.logger()
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")

        logger.info(f"🔄 Attempting to reindex record {record_id}")

        result = await arango_service.reindex_single_record(
            record_id=record_id,
            user_id=user_id,
            org_id=org_id,
            request=request
        )

        if result["success"]:
            logger.info(f"✅ Successfully initiated reindex for record {record_id}")
            return {
                "success": True,
                "message": f"Reindex initiated for record {record_id}",
                "recordId": result.get("recordId"),
                "recordName": result.get("recordName"),
                "connector": result.get("connector"),
                "eventPublished": result.get("eventPublished"),
                "userRole": result.get("userRole")
            }
        else:
            logger.error(f"❌ Failed to reindex record {record_id}: {result.get('reason')}")
            raise HTTPException(
                status_code=result.get("code", 500),
                detail=result.get("reason", "Failed to reindex record")
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error reindexing record {record_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while reindexing record: {str(e)}"
        )

@router.post("/api/v1/records/reindex-failed")
@inject
async def reindex_failed_records(
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service),
) -> Dict:
    """
    Reindex all failed records for a specific connector with permission validation
    """
    try:
        container = request.app.container
        logger = container.logger()
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")

        request_body = await request.json()

        logger.info(f"🔄 Attempting to reindex failed {request_body.get('connector')} records")

        result = await arango_service.reindex_failed_connector_records(
            user_id=user_id,
            org_id=org_id,
            connector=request_body.get('connector'),
            origin=request_body.get('origin')
        )

        if result["success"]:
            logger.info(f"✅ Successfully initiated reindex for failed {request_body.get('connector')} records")
            return {
                "success": True,
                "message": result.get("message"),
                "connector": result.get("connector"),
                "origin": result.get("origin"),
                "userPermissionLevel": result.get("user_permission_level"),
                "eventPublished": result.get("event_published")
            }
        else:
            logger.error(f"❌ Failed to reindex failed records: {result.get('reason')}")
            raise HTTPException(
                status_code=result.get("code", 500),
                detail=result.get("reason", "Failed to reindex failed records")
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error reindexing failed records: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while reindexing failed records: {str(e)}"
        )

@router.get("/api/v1/stats")
async def get_connector_stats_endpoint(
    request: Request,
    org_id: str,
    connector_id: str,
    arango_service: BaseArangoService = Depends(get_arango_service)
)-> Dict[str, Any]:
    try:
        result = await arango_service.get_connector_stats(org_id, connector_id)
        logger = request.app.container.logger()
        if result["success"]:
             return {"success": True, "data": result["data"]}
        else:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"No data found for connector {connector_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting connector stats: {str(e)}")
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail=f"Internal server error while getting connector stats: {str(e)}")


def _encode_state_with_instance(state: str, connector_id: str) -> str:
    """
    Encode OAuth state with connector instance key.

    Args:
        state: Original OAuth state
        connector_id: Connector instance key (_key)

    Returns:
        Encoded state containing both original state and connector_id
    """
    state_data = {
        "state": state,
        "connector_id": connector_id
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(state_data).encode()
    ).decode()
    return encoded


def _decode_state_with_instance(encoded_state: str) -> Dict[str, str]:
    """
    Decode OAuth state to extract original state and connector_id.
    Args:
        encoded_state: Encoded state string

    Returns:
        Dictionary with 'state' and 'connector_id'

    Raises:
        ValueError: If state cannot be decoded
    """
    try:
        decoded = base64.urlsafe_b64decode(encoded_state.encode()).decode()
        state_data = json.loads(decoded)
        return state_data
    except Exception as e:
        raise ValueError(f"Invalid state format: {e}")


def _get_config_path_for_instance(connector_id: str) -> str:
    """
    Get etcd configuration path for a connector instance.

    Args:
        connector_id: Connector instance key (_key)

    Returns:
        Configuration path in etcd
    """
    return f"/services/connectors/{connector_id}/config"


async def _get_settings_base_path(arango_service: BaseArangoService) -> str:
    """
    Determine frontend settings base path based on organization account type.

    Args:
        arango_service: ArangoDB service instance

    Returns:
        Settings base path URL
    """
    try:
        organizations = await arango_service.get_all_documents(
            CollectionNames.ORGS.value
        )

        if isinstance(organizations, list) and len(organizations) > 0:
            account_type = str(
                (organizations[0] or {}).get("accountType", "")
            ).lower()

            if account_type in ["business", "organization", "enterprise"]:
                return "/account/company-settings/settings/connector"

    except Exception:
        pass

    return "/account/individual/settings/connector"


# ============================================================================
# Registry & Instance Endpoints
# ============================================================================

@router.get("/api/v1/connectors/registry")
async def get_connector_registry(
    request: Request,
    scope: Optional[str] = Query(None, description="personal | team"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None, description="Search by name/group/description"),
) -> Dict[str, Any]:
    """
    Get all available connector types from registry.

    This endpoint returns connector types that can be configured,
    not the configured instances.

    Args:
        request: FastAPI request object

    Returns:
        Dictionary with success status and list of available connectors

    Raises:
        HTTPException: 404 if no connectors found in registry
    """
    connector_registry = request.app.state.connector_registry
    container = request.app.container
    logger = container.logger()

    try:
        # Validate scope
        if scope and scope not in [ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value]:
            logger.error(f"Invalid scope: {scope}")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Invalid scope. Must be 'personal' or 'team'"
            )
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        result = await connector_registry.get_all_registered_connectors(
            is_admin=is_admin,
            scope=scope,
            page=page,
            limit=limit,
            search=search
        )

        if not result:
            logger.error("No connectors found in registry")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="No connectors found in registry"
            )

        return {
            "success": True,
            **result
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ Error getting connector registry: {str(e)}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Error getting connector registry: {str(e)}"
        )



@router.get("/api/v1/connectors/")
async def get_connector_instances(
    request: Request,
    scope: Optional[str] = Query(None, description="personal | team"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None, description="Search by instance name/type/group"),
) -> Dict[str, Any]:
    """
    Get all configured connector instances.

    This endpoint returns actual configured instances with their status.

    Args:
        request: FastAPI request object

    Returns:
        Dictionary with success status and list of connector instances
    """
    connector_registry = request.app.state.connector_registry
    container = request.app.container
    logger = container.logger()
    user_id = request.state.user.get("userId")
    org_id = request.state.user.get("orgId")
    is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
    try:
        logger.info("Getting connector instances")
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )

        # Validate scope
        if scope and scope not in [ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value]:
            logger.error(f"Invalid scope: {scope}")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Invalid scope. Must be 'personal' or 'team'"
            )

        result = await connector_registry.get_all_connector_instances(
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin,
            scope=scope,
            page=page,
            limit=limit,
            search=search
        )

        return {
            "success": True,
            **result
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ Error getting connector instances: {str(e)}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Error getting connector instances: {str(e)}"
        )


@router.get("/api/v1/connectors/active")
async def get_active_connector_instances(request: Request) -> Dict[str, Any]:
    """
    Get all active connector instances.

    Args:
        request: FastAPI request object

    Returns:
        Dictionary with active connector instances
    """

    connector_registry = request.app.state.connector_registry
    container = request.app.container
    logger = container.logger()
    try:
        logger.info("Getting active connector instances")
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )
        connectors = await connector_registry.get_active_connector_instances(
            user_id=user_id,
            org_id=org_id
        )
        return {
            "success": True,
            "connectors": connectors
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error getting active connector instances: {str(e)}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to get active connector instances: {str(e)}"
        )


@router.get("/api/v1/connectors/inactive")
async def get_inactive_connector_instances(request: Request) -> Dict[str, Any]:
    """
    Get all inactive connector instances.

    Args:
        request: FastAPI request object

    Returns:
        Dictionary with inactive connector instances
    """
    connector_registry = request.app.state.connector_registry
    container = request.app.container
    logger = container.logger()
    try:
        logger.info("Getting inactive connector instances")
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )
        connectors = await connector_registry.get_inactive_connector_instances(
            user_id=user_id,
            org_id=org_id
        )
        return {
            "success": True,
            "connectors": connectors
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error getting inactive connector instances: {str(e)}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to get inactive connector instances: {str(e)}"
        )


@router.get("/api/v1/connectors/configured")
async def get_configured_connector_instances(
    request: Request,
    scope: Optional[str] = Query(None, description="personal | team"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None, description="Search by instance name/type/group"),
) -> Dict[str, Any]:
    """
    Get all configured connector instances.

    Args:
        request: FastAPI request object

    Returns:
        Dictionary with configured connector instances
    """
    connector_registry = request.app.state.connector_registry
    container = request.app.container
    logger = container.logger()
    user_id = request.state.user.get("userId")
    org_id = request.state.user.get("orgId")
    is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
    try:
        logger.info("Getting configured connector instances")
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )

        if scope and scope not in [ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value]:
            logger.error(f"Invalid scope: {scope}")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Invalid scope. Must be 'personal' or 'team'"
            )
        connectors = await connector_registry.get_configured_connector_instances(
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin,
            scope=scope,
            page=page,
            limit=limit,
            search=search
        )

        return {
            "success": True,
            "connectors": connectors
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ Error getting configured connector instances: {str(e)}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Error getting configured connector instances: {str(e)}"
        )

# ============================================================================
# Instance Configuration Endpoints
# ============================================================================

@router.post("/api/v1/connectors/")
async def create_connector_instance(
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service)
) -> Dict[str, Any]:
    """
    Create a new connector instance.

    Request body should contain:
    - connector_type: Type of connector (from registry)
    - instance_name: Name for this instance
    - config: Initial configuration (auth, sync, filters)

    Args:
        request: FastAPI request object
        arango_service: Injected ArangoDB service

    Returns:
        Dictionary with created instance details including connector_id

    Raises:
        HTTPException: 400 for invalid data, 404 if connector type not found
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry
    logger.info("Creating connector instance")
    try:
        user_id = request.state.user.get("userId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        logger.info(f"Is admin: {is_admin}")
        logger.info(f"Headers: {request.headers}")
        org_id = request.state.user.get("orgId")
        if not user_id or not org_id:
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )

        body = await request.json()
        connector_type = body.get("connectorType")
        instance_name = body.get("instanceName")
        config = body.get("config", {})
        base_url = body.get("baseUrl", "")
        scope = (body.get("scope") or "personal").lower()

        if scope and scope not in [ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value]:
            logger.error(f"Invalid scope: {scope}")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Invalid scope. Must be 'personal' or 'team'"
            )

        if not connector_type or not instance_name:
            logger.error(f"connector_type and instance_name are required: {connector_type} {instance_name}")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="connector_type and instance_name are required"
            )

        # Verify connector type exists in registry
        metadata = await connector_registry.get_connector_metadata(connector_type)
        if not metadata:
            logger.error(f"Connector type '{connector_type}' not found in registry")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector type '{connector_type}' not found in registry"
            )

        # Check if connector supports requested scope
        supported_scopes = metadata.get("scope", [ConnectorScope.PERSONAL])
        if scope not in supported_scopes:
            logger.error(f"Connector '{connector_type}' does not support scope '{scope}'")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Connector '{connector_type}' does not support scope '{scope}'"
            )

        # Validate team scope creation permission
        if scope == ConnectorScope.TEAM.value:
            if not is_admin:
                logger.error("Only administrators can create team connectors")
                raise HTTPException(
                    status_code=HttpStatusCode.FORBIDDEN.value,
                    detail="Only administrators can create team connectors"
                )

        # Create instance in database
        try:
            instance = await connector_registry.create_connector_instance_on_configuration(
                connector_type=connector_type,
                instance_name=instance_name,
                scope=scope,
                created_by=user_id,
                org_id=org_id,
                is_admin=is_admin
            )
        except ValueError as e:
            # Handle name uniqueness validation error
            logger.error(f"Name uniqueness validation failed: {str(e)}")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=str(e)
            )

        if not instance:
            logger.error("Failed to create connector instance")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to create connector instance"
            )

        connector_id = instance.get("_key")

        # Store initial configuration in etcd if provided
        if config:
            logger.info(f"Storing initial config for instance {connector_id}")
            config_service = container.config_service()
            config_path = _get_config_path_for_instance(connector_id)

            # Prepare configuration
            prepared_config = {
                "auth": config.get("auth", {}),
                "sync": config.get("sync", {}),
                "filters": config.get("filters", {}),
                "credentials": None,
                "oauth": None
            }
            auth_metadata = metadata.get("config", {}).get("auth", {})

            redirect_uri = auth_metadata.get("redirectUri", "")
            if base_url:
                redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"
            else:
                endpoints = await config_service.get_config(
                    "/services/endpoints",
                    use_cache=False
                )
                base_url = endpoints.get(
                    "frontendPublicUrl",
                    "http://localhost:3001"
                )
                redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"

            # Add OAuth metadata from registry if OAuth-based
            auth_type = metadata.get("authType", "").upper()
            if auth_type in ["OAUTH", "OAUTH_ADMIN_CONSENT"]:
                prepared_config["auth"].update({
                    "authorizeUrl": auth_metadata.get("authorizeUrl", ""),
                    "tokenUrl": auth_metadata.get("tokenUrl", ""),
                    "scopes": auth_metadata.get("scopes", []),
                    "redirectUri": redirect_uri
                })

            await config_service.set_config(config_path, prepared_config)
            logger.info(f"Stored initial config for instance {connector_id}")

        logger.info(
            f"Created connector instance '{instance_name}' of type {connector_type} "
            f"with scope {scope} for user {user_id} with key {connector_id}"
        )

        return {
            "success": True,
            "connector": {
                "connectorId": connector_id,
                "connectorType": connector_type,
                "instanceName": instance_name,
                "created": True,
                "scope": scope,
                "createdBy": user_id,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating connector instance: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to create connector instance: {str(e)}"
        )


@router.get("/api/v1/connectors/{connector_id}")
async def get_connector_instance(
    connector_id: str,
    request: Request
) -> Dict[str, Any]:
    """
    Get a specific connector instance by its key.

    Args:
        connector_id: Unique instance key (_key)
        request: FastAPI request object

    Returns:
        Dictionary with instance details

    Raises:
        HTTPException: 404 if instance not found
    """
    connector_registry = request.app.state.connector_registry
    container = request.app.container
    logger = container.logger()
    logger.info("Getting connector instance")
    user_id = request.state.user.get("userId")
    org_id = request.state.user.get("orgId")
    is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"

    try:
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )

        connector = await connector_registry.get_connector_instance(
            connector_id=connector_id,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )

        if not connector:
            logger.error(f"Connector instance {connector_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector instance {connector_id} not found or access denied"
            )

        return {
            "success": True,
            "connector": connector
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"❌ Error getting connector instance: {str(e)}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Error getting connector instance: {str(e)}"
        )

@router.get("/api/v1/connectors/{connector_id}/config")
async def get_connector_instance_config(
    connector_id: str,
    request: Request
) -> Dict[str, Any]:
    """
    Get configuration for a specific connector instance.

    Returns both registry metadata and instance-specific configuration
    from etcd (excluding sensitive credentials).

    Args:
        connector_id: Unique instance key
        request: FastAPI request object

    Returns:
        Dictionary with connector configuration

    Raises:
        HTTPException: 404 if instance not found
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry

    try:
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )
        # Get instance from registry
        instance = await connector_registry.get_connector_instance(
            connector_id=connector_id,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not instance:
            logger.error(f"Connector instance {connector_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector instance {connector_id} not found or access denied"
            )

        # Load configuration from etcd
        config_service = container.config_service()
        config_path = _get_config_path_for_instance(connector_id)

        try:
            config = await config_service.get_config(config_path)
        except Exception as e:
            logger.warning(f"No config found for instance {connector_id}: {e}")
            config = None

        if not config:
            config = {"auth": {}, "sync": {}, "filters": {}}

        # Remove sensitive data
        config = config.copy()
        config.pop("credentials", None)
        config.pop("oauth", None)

        # Build response
        response_data = {
            "connector_id": connector_id,
            "name": instance.get("name"),
            "type": instance.get("type"),
            "appGroup": instance.get("appGroup"),
            "authType": instance.get("authType"),
            "scope": instance.get("scope"),
            "createdBy": instance.get("createdBy"),
            "updatedBy": instance.get("updatedBy"),
            "appDescription": instance.get("appDescription", ""),
            "appCategories": instance.get("appCategories", []),
            "supportsRealtime": instance.get("supportsRealtime", False),
            "supportsSync": instance.get("supportsSync", False),
            "supportsAgent": instance.get("supportsAgent", False),
            "iconPath": instance.get("iconPath", "/assets/icons/connectors/default.svg"),
            "config": config,
            "isActive": instance.get("isActive", False),
            "isConfigured": instance.get("isConfigured", False),
            "isAuthenticated": instance.get("isAuthenticated", False),
            "createdAtTimestamp": instance.get("createdAtTimestamp"),
            "updatedAtTimestamp": instance.get("updatedAtTimestamp")
        }

        return {
            "success": True,
            "config": response_data
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting config for instance {connector_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to get connector configuration: {str(e)}"
        )


@router.put("/api/v1/connectors/{connector_id}/config")
async def update_connector_instance_config(
    connector_id: str,
    request: Request,
) -> Dict[str, Any]:
    """
    Update configuration for a connector instance.

    Request body can contain:
    - auth: Authentication configuration
    - sync: Sync settings
    - filters: Filter configuration
    - base_url: Optional base URL for OAuth redirects

    Args:
        connector_id: Unique instance key
        request: FastAPI request object

    Returns:
        Dictionary with updated configuration

    Raises:
        HTTPException: 400 for invalid data, 404 if instance not found
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry

    try:
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )
        body = await request.json()
        base_url = body.get("baseUrl", "")

        # Verify instance exists
        instance = await connector_registry.get_connector_instance(
            connector_id=connector_id,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not instance:
            logger.error(f"Connector instance {connector_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector instance {connector_id} not found or access denied"
            )

        if instance.get("scope") == ConnectorScope.TEAM.value and not is_admin:
            logger.error("Only administrators can update team connectors")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only administrators can update team connectors"
            )
        if instance.get("createdBy") != user_id and not is_admin:
            logger.error("Only the creator or an administrator can update this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator or an administrator can update this connector"
            )
        if instance.get("scope") == ConnectorScope.PERSONAL.value and instance.get("createdBy") != user_id:
            logger.error("Only the creator can update this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator can update this connector"
            )

        config_service = container.config_service()
        config_path = _get_config_path_for_instance(connector_id)

        # Build new configuration from request
        new_config = {}

        for section in ["auth", "sync", "filters"]:
            if section in body and isinstance(body[section], dict):
                new_config[section] = body[section]

        # Clear credentials and OAuth state on config updates
        new_config["credentials"] = None
        new_config["oauth"] = None

        # Add OAuth metadata from registry if applicable
        auth_type = instance.get("authType", "").upper()
        if auth_type in ["OAUTH", "OAUTH_ADMIN_CONSENT"]:
            connector_type = instance.get("type")
            metadata = await connector_registry.get_connector_metadata(connector_type)
            auth_metadata = metadata.get("config", {}).get("auth", {})

            if "auth" not in new_config:
                new_config["auth"] = {}

            # Determine redirect URI
            redirect_uri = auth_metadata.get("redirectUri", "")
            if base_url:
                redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"
            else:
                endpoints = await config_service.get_config(
                    "/services/endpoints",
                    use_cache=False
                )
                base_url = endpoints.get(
                    "frontendPublicUrl",
                    "http://localhost:3001"
                )
                redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"

            new_config["auth"].update({
                "authorizeUrl": auth_metadata.get("authorizeUrl", ""),
                "tokenUrl": auth_metadata.get("tokenUrl", ""),
                "scopes": auth_metadata.get("scopes", []),
                "redirectUri": redirect_uri
            })

        # Save configuration
        await config_service.set_config(config_path, new_config)
        logger.info(f"Updated config for instance {connector_id}")

        # Update instance status
        updates = {
            "isConfigured": True,
            "isAuthenticated": False,
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedBy": user_id
        }
        updated_instance = await connector_registry.update_connector_instance(
            connector_id=connector_id,
            updates=updates,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not updated_instance:
            logger.error(f"Failed to update {instance.get('name')} connector instance")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Failed to update {instance.get('name')} connector instance"
            )
        return {
            "success": True,
            "config": new_config
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating config for instance {connector_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to update connector configuration: {str(e)}"
        )


@router.delete("/api/v1/connectors/{connector_id}")
async def delete_connector_instance(
    connector_id: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service)
) -> Dict[str, Any]:
    """
    Delete a connector instance and its configuration.

    Args:
        connector_id: Unique instance key
        request: FastAPI request object
        arango_service: Injected ArangoDB service

    Returns:
        Dictionary with success status

    Raises:
        HTTPException: 404 if instance not found
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry

    try:
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )
        # Verify instance exists
        instance = await connector_registry.get_connector_instance(
            connector_id=connector_id,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not instance:
            logger.error(f"Connector instance {connector_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector instance {connector_id} not found or access denied"
            )
        if instance.get("scope") == ConnectorScope.TEAM.value and not is_admin:
            logger.error("Only administrators can delete team connectors")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only administrators can delete team connectors"
            )
        if instance.get("createdBy") != user_id and not is_admin:
            logger.error("Only the creator or an administrator can delete this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator or an administrator can delete this connector"
            )
        if instance.get("scope") == ConnectorScope.PERSONAL.value and instance.get("createdBy") != user_id:
            logger.error("Only the creator can delete this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator can delete this connector"
            )

        # Delete configuration from etcd
        config_service = container.config_service()
        config_path = _get_config_path_for_instance(connector_id)

        try:
            await config_service.delete_config(config_path)
        except Exception as e:
            logger.warning(f"Could not delete config for {connector_id}: {e}")

        # Delete instance from database
        await arango_service.delete_nodes(
            [connector_id],
            CollectionNames.APPS.value
        )

        await arango_service.delete_edge(
            org_id,
            connector_id,
            CollectionNames.ORG_APP_RELATION.value
        )

        logger.info(f"Deleted connector instance {connector_id}")

        return {
            "success": True,
            "message": f"Connector instance {connector_id} deleted successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting instance {connector_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to delete connector instance: {str(e)}"
        )


@router.put("/api/v1/connectors/{connector_id}/name")
async def update_connector_instance_name(
    connector_id: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service)
) -> Dict[str, Any]:
    """
    Update the display name for a connector instance.

    Args:
        connector_id: Unique instance key
        request: FastAPI request object

    Returns:
        Dictionary with success status and updated instance fields
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry

    try:
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )
        body = await request.json()
        instance_name = (body or {}).get("instanceName", "")

        if not instance_name or not instance_name.strip():
            logger.error("instanceName is required")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="instanceName is required"
            )

        # Verify instance exists
        instance = await connector_registry.get_connector_instance(connector_id=connector_id,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not instance:
            logger.error(f"Connector instance {connector_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector instance {connector_id} not found or access denied"
            )

        if instance.get("scope") == ConnectorScope.TEAM.value and not is_admin:
            logger.error("Only administrators can update team connectors")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only administrators can update team connectors"
            )
        if instance.get("createdBy") != user_id and not is_admin:
            logger.error("Only the creator or an administrator can update this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator or an administrator can update this connector"
            )
        if instance.get("scope") == ConnectorScope.PERSONAL.value and instance.get("createdBy") != user_id:
            logger.error("Only the creator can update this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator can update this connector"
            )
        updates = {
            "name": instance_name.strip(),
            "updatedBy": user_id
        }

        try:
            updated = await connector_registry.update_connector_instance(
                connector_id=connector_id,
                updates=updates,
                user_id=user_id,
                org_id=org_id,
                is_admin=is_admin
            )
        except ValueError as e:
            # Handle name uniqueness validation error
            logger.error(f"Name uniqueness validation failed: {str(e)}")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=str(e)
            )

        if not updated:
            logger.error(f"Failed to update {instance.get('name')} connector instance name")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Failed to update {instance.get('name')} connector instance name"
            )

        logger.info(f"Updated instance {connector_id} name to '{instance_name}'")

        return {
            "success": True,
            "connector": {
                "_key": connector_id,
                "name": instance_name.strip(),
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating instance name for {connector_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to update connector instance name: {str(e)}"
        )


# ============================================================================
# OAuth Endpoints
# ============================================================================

@router.get("/api/v1/connectors/{connector_id}/oauth/authorize")
async def get_oauth_authorization_url(
    connector_id: str,
    request: Request,
    base_url: Optional[str] = Query(None),
    arango_service: BaseArangoService = Depends(get_arango_service)
) -> Dict[str, Any]:
    """
    Get OAuth authorization URL for a connector instance.

    Args:
        connector_id: Unique instance key
        request: FastAPI request object
        base_url: Optional base URL for redirect
        arango_service: Injected ArangoDB service

    Returns:
        Dictionary with authorization URL and encoded state

    Raises:
        HTTPException: 400 if OAuth not supported, 404 if instance not found
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry

    try:
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )
        # Get instance
        instance = await connector_registry.get_connector_instance(
            connector_id=connector_id,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not instance:
            logger.error(f"Connector instance {connector_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector instance {connector_id} not found or access denied"
            )

        if instance.get("scope") == ConnectorScope.TEAM.value and not is_admin:
            logger.error("Only administrators can get OAuth authorization URL for team connectors")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only administrators can get OAuth authorization URL for team connectors"
            )
        if instance.get("createdBy") != user_id and not is_admin:
            logger.error("Only the creator or an administrator can get OAuth authorization URL for this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator or an administrator can get OAuth authorization URL for this connector"
            )
        if instance.get("scope") == ConnectorScope.PERSONAL.value and instance.get("createdBy") != user_id:
            logger.error("Only the creator can get OAuth authorization URL for this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator can get OAuth authorization URL for this connector"
            )

        # Verify OAuth support
        auth_type = (instance.get("authType") or "").upper()
        if auth_type not in ["OAUTH", "OAUTH_ADMIN_CONSENT"]:
            logger.error("Connector instance does not support OAuth")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Connector instance does not support OAuth"
            )

        # Get configuration
        config_service = container.config_service()
        config_path = _get_config_path_for_instance(connector_id)
        config = await config_service.get_config(config_path)

        if not config or not config.get("auth"):
            logger.error("OAuth configuration not found. Please configure first.")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="OAuth configuration not found. Please configure first."
            )

        auth_config = config["auth"]

        # Determine redirect URI - use the exact URI from config
        redirect_uri = auth_config.get("redirectUri", "")

        logger.info(f"Redirect URI: {redirect_uri}")

        # Create OAuth configuration
        oauth_config = get_oauth_config(instance.get("type", ""), auth_config)

        # Create OAuth provider
        oauth_provider = OAuthProvider(
            config=oauth_config,
            key_value_store=container.key_value_store(),
            credentials_path=config_path
        )

        try:
            # Add provider-specific parameters
            extra_params = {}
            connector_type = instance.get("type", "").upper()

            if connector_type in ["DRIVE", "GMAIL"]:
                extra_params.update({
                    "access_type": "offline",
                    "prompt": "consent",
                    "include_granted_scopes": "true"
                })

            # Generate authorization URL
            auth_url = await oauth_provider.start_authorization(**extra_params)

            # Add Microsoft-specific parameters
            if connector_type == "ONEDRIVE":
                parsed_url = urlparse(auth_url)
                params = parse_qs(parsed_url.query)
                params["response_mode"] = ["query"]

                if auth_type == "OAUTH_ADMIN_CONSENT":
                    params["prompt"] = ["admin_consent"]

                auth_url = (
                    f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?"
                    f"{urlencode(params, doseq=True)}"
                )

            # Extract and encode state with connector_id
            parsed_url = urlparse(auth_url)
            query_params = parse_qs(parsed_url.query)
            original_state = query_params.get("state", [None])[0]

            if not original_state:
                raise ValueError("No state parameter in authorization URL")

            # Encode state with connector_id
            encoded_state = _encode_state_with_instance(original_state, connector_id)

            # Replace state in URL
            query_params["state"] = [encoded_state]
            final_auth_url = (
                f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?"
                f"{urlencode(query_params, doseq=True)}"
            )

            return {
                "success": True,
                "authorizationUrl": final_auth_url,
                "state": encoded_state
            }

        finally:
            await oauth_provider.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating OAuth URL for {connector_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to generate OAuth URL: {str(e)}"
        )


@router.get("/api/v1/connectors/oauth/callback")
async def handle_oauth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    base_url: Optional[str] = Query(None),
    arango_service: BaseArangoService = Depends(get_arango_service)
) -> Dict[str, Any]:
    """
    Handle OAuth callback and exchange code for tokens.

    This endpoint processes OAuth callbacks for any connector instance.
    The connector_id is extracted from the encoded state parameter.

    Args:
        request: FastAPI request object
        code: Authorization code from OAuth provider
        state: Encoded state containing connector_id
        error: OAuth error if any
        base_url: Optional base URL for redirects
        arango_service: Injected ArangoDB service

    Returns:
        Dictionary with redirect URL and status
    """
    container = request.app.container
    logger = container.logger()
    config_service = container.config_service()
    connector_registry = request.app.state.connector_registry

    settings_base_path = await _get_settings_base_path(arango_service)

    # Normalize error values
    if error in ["null", "undefined", "None", ""]:
        error = None

    # Check for OAuth errors
    if error:
        logger.error(f"OAuth error: {error}")
        error_url = f"{base_url or ''}/connectors/oauth/callback?oauth_error={error}"
        return {
            "success": False,
            "error": error,
            "redirect_url": error_url
        }

    if not code or not state:
        logger.error("Missing OAuth parameters")
        error_url = f"{base_url or ''}/connectors/oauth/callback?oauth_error=missing_parameters"
        return {
            "success": False,
            "error": "missing_parameters",
            "redirect_url": error_url
        }


    user_id = request.state.user.get("userId")
    org_id = request.state.user.get("orgId")
    is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
    if not user_id or not org_id:
        logger.error(f"User not authenticated: {user_id} {org_id}")
        raise HTTPException(
            status_code=HttpStatusCode.UNAUTHORIZED.value,
            detail="User not authenticated"
        )
    try:
        # Decode state to get connector_id
        try:
            state_data = _decode_state_with_instance(state)
            original_state = state_data["state"]
            connector_id = state_data["connector_id"]
        except ValueError as e:
            logger.error(f"Invalid state format: {e}")
            error_url = f"{base_url or ''}/connectors/oauth/callback?oauth_error=invalid_state"
            return {
                "success": False,
                "error": "invalid_state",
                "redirect_url": error_url
            }

        # Get instance
        instance = await connector_registry.get_connector_instance(
            connector_id=connector_id,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not instance:
            logger.error(f"Instance {connector_id} not found or access denied")
            error_url = f"{base_url or ''}/connectors/oauth/callback?oauth_error=instance_not_found"
            return {
                "success": False,
                "error": "instance_not_found",
                "redirect_url": error_url
            }

        if instance.get("scope") == ConnectorScope.TEAM.value and not is_admin:
            logger.error("Only administrators can handle OAuth callback for team connectors")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only administrators can handle OAuth callback for team connectors"
            )
        if instance.get("createdBy") != user_id and not is_admin:
            logger.error("Only the creator or an administrator can handle OAuth callback for this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator or an administrator can handle OAuth callback for this connector"
            )
        if instance.get("scope") == ConnectorScope.PERSONAL.value and instance.get("createdBy") != user_id:
            logger.error("Only the creator can handle OAuth callback for this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator can handle OAuth callback for this connector"
            )
        connector_type = instance.get("type", "").replace(" ","")
        # Get configuration
        config_path = _get_config_path_for_instance(connector_id)
        config = await config_service.get_config(config_path)

        if not config or not config.get("auth"):
            logger.error(f"No OAuth config for instance {connector_id}")
            error_url = f"{base_url or ''}/connectors/oauth/callback?oauth_error=config_not_found"
            return {
                "success": False,
                "error": "config_not_found",
                "redirect_url": error_url
            }

        auth_config = config["auth"]

        # Determine redirect URI - must match exactly what was used in authorization URL
        redirect_uri = auth_config.get("redirectUri", "")

        logger.info(f"Callback redirect URI: {redirect_uri}")

        # Create OAuth configuration - use auth_config for all OAuth settings
        oauth_config = get_oauth_config(connector_type, auth_config)

        # Create OAuth provider and exchange code
        oauth_provider = OAuthProvider(
            config=oauth_config,
            key_value_store=container.key_value_store(),
            credentials_path=config_path
        )

        try:
            # Pass the original state (not the encoded one) to the OAuth provider
            token = await oauth_provider.handle_callback(code, original_state)
        finally:
            await oauth_provider.close()

        # Validate token
        if not token or not token.access_token:
            logger.error(f"Invalid token received for instance {connector_id}")
            error_url = f"{base_url}{settings_base_path}?oauth_error=invalid_token"
            return {
                "success": False,
                "error": "invalid_token",
                "redirect_url": error_url
            }

        logger.info(f"OAuth tokens stored successfully for instance {connector_id}")

        # Refresh configuration cache
        try:
            kv_store = container.key_value_store()
            updated_config = await kv_store.get_key(config_path)
            if isinstance(updated_config, dict):
                await config_service.set_config(config_path, updated_config)
                logger.info(f"Refreshed config cache for instance {connector_id}")
        except Exception as cache_err:
            logger.warning(f"Could not refresh config cache: {cache_err}")

        # Schedule token refresh
        try:
            from app.connectors.core.base.token_service.token_refresh_service import (
                TokenRefreshService,
            )
            refresh_service = TokenRefreshService(
                container.key_value_store(),
                arango_service
            )
            await refresh_service.schedule_token_refresh(connector_id, connector_type, token)
            logger.info(f"Scheduled token refresh for instance {connector_id}")
        except Exception as sched_err:
            logger.warning(f"Could not schedule token refresh: {sched_err}")

        # Update instance authentication status
        updates = {
            "isAuthenticated": True,
            "updatedAtTimestamp": get_epoch_timestamp_in_ms()
        }
        await connector_registry.update_connector_instance(connector_id=connector_id, updates=updates, user_id=user_id, org_id=org_id, is_admin=is_admin)
        logger.info(f"Instance {connector_id} marked as authenticated")

        # Build redirect URL
        redirect_url = f"{base_url}{settings_base_path}/{connector_id}"

        return {
            "success": True,
            "redirect_url": redirect_url
        }

    except Exception as e:
        logger.error(f"Error handling OAuth callback: {e}")

        # Update instance authentication status on error
        try:
            if 'connector_id' in locals():
                updates = {
                    "isAuthenticated": False,
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                }
                await connector_registry.update_connector_instance(connector_id=connector_id, updates=updates, user_id=user_id, org_id=org_id, is_admin=is_admin)
        except Exception:
            pass

        error_url = f"{base_url or ''}/connectors/oauth/callback?oauth_error=server_error"
        return {
            "success": False,
            "error": "server_error",
            "redirect_url": error_url
        }


# ============================================================================
# Filter Endpoints
# ============================================================================

async def _get_connector_filter_options_from_config(
    connector_type: str,
    connector_config: Dict[str, Any],
    token_or_credentials: Dict[str, Any],
    config_service: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Get filter options for a connector by calling dynamic endpoints.

    Args:
        connector_type: Type of the connector
        connector_config: Connector configuration
        token_or_credentials: OAuth token or credentials
        config_service: Configuration service instance

    Returns:
        Dictionary of available filter options
    """
    try:
        filter_endpoints = connector_config.get("config", {}).get("filters", {}).get("endpoints", {})

        if not filter_endpoints:
            return {}

        filter_options = {}

        for filter_type, endpoint in filter_endpoints.items():
            try:
                if endpoint == "static":
                    options = await _get_static_filter_options(
                        connector_type,
                        filter_type
                    )
                    filter_options[filter_type] = options
                else:
                    options = await _fetch_filter_options_from_api(
                        endpoint,
                        filter_type,
                        token_or_credentials,
                        connector_type
                    )
                    if options:
                        filter_options[filter_type] = options

            except Exception as e:
                logger.warning(f"Error fetching {filter_type} for {connector_type}: {e}")
                filter_options[filter_type] = await _get_static_filter_options(
                    connector_type,
                    filter_type
                )

        return filter_options

    except Exception as e:
        logger.error(f"Error getting filter options for {connector_type}: {e}")
        return await _get_fallback_filter_options(connector_type)


async def _fetch_filter_options_from_api(
    endpoint: str,
    filter_type: str,
    token_or_credentials: Dict[str, Any],
    connector_type: str
) -> List[Dict[str, str]]:
    """
    Fetch filter options from a dynamic API endpoint.

    Args:
        endpoint: API endpoint URL
        filter_type: Type of filter
        token_or_credentials: Authentication token or credentials
        connector_type: Type of connector

    Returns:
        List of filter options with value and label
    """
    import aiohttp

    headers = {}

    # Set up authentication headers
    if hasattr(token_or_credentials, "access_token"):
        headers["Authorization"] = f"Bearer {token_or_credentials.access_token}"
    elif isinstance(token_or_credentials, dict):
        if "access_token" in token_or_credentials:
            headers["Authorization"] = f"Bearer {token_or_credentials['access_token']}"
        elif "api_token" in token_or_credentials:
            headers["Authorization"] = f"Bearer {token_or_credentials['api_token']}"
        elif "token" in token_or_credentials:
            headers["Authorization"] = f"Bearer {token_or_credentials['token']}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, headers=headers) as response:
                if response.status == HttpStatusCode.SUCCESS.value:
                    data = await response.json()
                    return _parse_filter_response(data, filter_type, connector_type)
                else:
                    logger.warning(
                        f"API call failed for {filter_type}: {response.status}"
                    )
                    return []
    except Exception as e:
        logger.error(f"Error fetching filter options from API: {e}")
        return []


def _parse_filter_response(
    data: Dict[str, Any],
    filter_type: str,
    connector_type: str
) -> List[Dict[str, str]]:
    """
    Parse API response to extract filter options.

    Args:
        data: API response data
        filter_type: Type of filter being parsed
        connector_type: Type of connector

    Returns:
        List of filter options with value and label
    """
    options = []

    try:
        connector_upper = connector_type.upper()

        if connector_upper == "GMAIL" and filter_type == "labels":
            labels = data.get("labels", [])
            for label in labels:
                if label.get("type") == "user":
                    options.append({
                        "value": label["id"],
                        "label": label["name"]
                    })

        elif connector_upper == "DRIVE" and filter_type == "folders":
            files = data.get("files", [])
            for file in files:
                options.append({
                    "value": file["id"],
                    "label": file["name"]
                })

        elif connector_upper == "ONEDRIVE" and filter_type == "folders":
            items = data.get("value", [])
            for item in items:
                if item.get("folder"):
                    options.append({
                        "value": item["id"],
                        "label": item["name"]
                    })

        elif connector_upper == "SLACK" and filter_type == "channels":
            channels = data.get("channels", [])
            for channel in channels:
                if not channel.get("is_archived"):
                    options.append({
                        "value": channel["id"],
                        "label": f"#{channel['name']}"
                    })

        elif connector_upper == "CONFLUENCE" and filter_type == "spaces":
            spaces = data.get("results", [])
            for space in spaces:
                options.append({
                    "value": space["key"],
                    "label": space["name"]
                })

    except Exception as e:
        logger.error(f"Error parsing {filter_type} response: {e}")

    return options


async def _get_static_filter_options(
    connector_type: str,
    filter_type: str
) -> List[Dict[str, str]]:
    """
    Get static filter options for connectors.

    Args:
        connector_type: Type of connector
        filter_type: Type of filter

    Returns:
        List of static filter options
    """
    if filter_type == "fileTypes":
        return [
            {"value": "document", "label": "Documents"},
            {"value": "spreadsheet", "label": "Spreadsheets"},
            {"value": "presentation", "label": "Presentations"},
            {"value": "pdf", "label": "PDFs"},
            {"value": "image", "label": "Images"},
            {"value": "video", "label": "Videos"}
        ]
    elif filter_type == "contentTypes":
        return [
            {"value": "page", "label": "Pages"},
            {"value": "blogpost", "label": "Blog Posts"},
            {"value": "comment", "label": "Comments"},
            {"value": "attachment", "label": "Attachments"}
        ]

    return []


async def _get_fallback_filter_options(
    connector_type: str
) -> Dict[str, List[Dict[str, str]]]:
    """
    Get hardcoded fallback filter options when dynamic fetching fails.

    Args:
        connector_type: Type of connector

    Returns:
        Dictionary of fallback filter options
    """
    fallback_options = {
        "GMAIL": {
            "labels": [
                {"value": "INBOX", "label": "Inbox"},
                {"value": "SENT", "label": "Sent"},
                {"value": "DRAFT", "label": "Draft"},
                {"value": "SPAM", "label": "Spam"},
                {"value": "TRASH", "label": "Trash"}
            ]
        },
        "DRIVE": {
            "fileTypes": [
                {"value": "document", "label": "Documents"},
                {"value": "spreadsheet", "label": "Spreadsheets"},
                {"value": "presentation", "label": "Presentations"},
                {"value": "pdf", "label": "PDFs"},
                {"value": "image", "label": "Images"},
                {"value": "video", "label": "Videos"}
            ]
        },
        "ONEDRIVE": {
            "fileTypes": [
                {"value": "document", "label": "Documents"},
                {"value": "spreadsheet", "label": "Spreadsheets"},
                {"value": "presentation", "label": "Presentations"},
                {"value": "pdf", "label": "PDFs"},
                {"value": "image", "label": "Images"},
                {"value": "video", "label": "Videos"}
            ]
        },
        "SLACK": {
            "channels": [
                {"value": "general", "label": "#general"},
                {"value": "random", "label": "#random"}
            ]
        },
        "CONFLUENCE": {
            "spaces": [
                {"value": "DEMO", "label": "Demo Space"},
                {"value": "DOCS", "label": "Documentation"}
            ]
        }
    }

    return fallback_options.get(connector_type.upper(), {})


@router.get("/api/v1/connectors/{connector_id}/filters")
async def get_connector_instance_filters(
    connector_id: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service)
) -> Dict[str, Any]:
    """
    Get filter options for a connector instance.

    Args:
        connector_id: Unique instance key
        request: FastAPI request object
        arango_service: Injected ArangoDB service

    Returns:
        Dictionary with available filter options

    Raises:
        HTTPException: 400 for auth issues, 404 if instance not found
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry

    try:
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )
        # Get instance
        instance = await connector_registry.get_connector_instance(connector_id=connector_id,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not instance:
            logger.error(f"Connector instance {connector_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector instance {connector_id} not found or access denied"
            )

        if instance.get("scope") == ConnectorScope.TEAM.value and not is_admin:
            logger.error("Only administrators can get filter options for team connectors")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only administrators can get filter options for team connectors"
            )
        if instance.get("createdBy") != user_id and not is_admin:
            logger.error("Only the creator or an administrator can get filter options for this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator or an administrator can get filter options for this connector"
            )
        if instance.get("scope") == ConnectorScope.PERSONAL.value and instance.get("createdBy") != user_id:
            logger.error("Only the creator can get filter options for this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator can get filter options for this connector"
            )
        connector_type = instance.get("type")

        # Get connector metadata
        connector_config = await connector_registry.get_connector_metadata(connector_type)
        if not connector_config:
            logger.error(f"Connector type {connector_type} not found")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector type {connector_type} not found"
            )

        # Get credentials based on auth type
        config_service = container.config_service()
        config_path = _get_config_path_for_instance(connector_id)
        config = await config_service.get_config(config_path)

        auth_type = (instance.get("authType") or "").upper()
        token_or_credentials = None

        if auth_type == "OAUTH":
            if not config or not config.get("credentials"):
                logger.error("OAuth credentials not found. Please authenticate first.")
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail="OAuth credentials not found. Please authenticate first."
                )
            token_or_credentials = OAuthToken.from_dict(config["credentials"])

        elif auth_type in ["OAUTH_ADMIN_CONSENT", "API_TOKEN", "USERNAME_PASSWORD"]:
            if not config or not config.get("auth"):
                logger.error("Configuration not found. Please configure first.")
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail="Configuration not found. Please configure first."
                )
            token_or_credentials = config.get("auth", {})

        else:
            logger.error(f"Unsupported authentication type: {auth_type}")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Unsupported authentication type: {auth_type}"
            )

        # Get filter options
        filter_options = await _get_connector_filter_options_from_config(
            connector_type,
            connector_config,
            token_or_credentials,
            config_service
        )

        return {
            "success": True,
            "filterOptions": filter_options
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting filter options for {connector_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to get filter options: {str(e)}"
        )


@router.post("/api/v1/connectors/{connector_id}/filters")
async def save_connector_instance_filters(
    connector_id: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service)
) -> Dict[str, Any]:
    """
    Save filter selections for a connector instance.

    Args:
        connector_id: Unique instance key
        request: FastAPI request object
        arango_service: Injected ArangoDB service

    Returns:
        Dictionary with success status

    Raises:
        HTTPException: 400 if no filters provided, 404 if instance not found
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry

    try:
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )
        body = await request.json()
        filter_selections = body.get("filters", {})

        if not filter_selections:
            logger.error("No filter selections provided")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="No filter selections provided"
            )

        # Verify instance exists
        instance = await connector_registry.get_connector_instance(connector_id=connector_id,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not instance:
            logger.error(f"Connector instance {connector_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector instance {connector_id} not found or access denied"
            )

        if instance.get("scope") == ConnectorScope.TEAM.value and not is_admin:
            logger.error("Only administrators can save filter options for team connectors")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only administrators can save filter options for team connectors"
            )
        if instance.get("createdBy") != user_id and not is_admin:
            logger.error("Only the creator or an administrator can save filter options for this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator or an administrator can save filter options for this connector"
            )
        if instance.get("scope") == ConnectorScope.PERSONAL.value and instance.get("createdBy") != user_id:
            logger.error("Only the creator can save filter options for this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator can save filter options for this connector"
            )
        # Get current config
        config_service = container.config_service()
        config_path = _get_config_path_for_instance(connector_id)
        config = await config_service.get_config(config_path)

        if not config:
            logger.error("Configuration not found. Please configure first.")
            config = {}

        # Update filters
        if "filters" not in config:
            logger.error("Filters not found. Please configure first.")
            config["filters"] = {}

        config["filters"]["values"] = filter_selections

        # Save updated config
        await config_service.set_config(config_path, config)

        logger.info(f"Saved filter selections for instance {connector_id}")

        return {
            "success": True,
            "message": "Filter selections saved successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving filter selections for {connector_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to save filter selections: {str(e)}"
        )


# ============================================================================
# Connector Toggle Endpoint
# ============================================================================

@router.post("/api/v1/connectors/{connector_id}/toggle")
async def toggle_connector_instance(
    connector_id: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service)
) -> Dict[str, Any]:
    """
    Toggle connector instance active status and trigger sync events.

    Args:
        connector_id: Unique instance key
        request: FastAPI request object
        arango_service: Injected ArangoDB service

    Returns:
        Dictionary with success status

    Raises:
        HTTPException: 400 for validation errors, 404 if instance not found
    """
    container = request.app.container
    logger = container.logger()
    producer = container.messaging_producer
    connector_registry = request.app.state.connector_registry

    user_info = {
        "orgId": request.state.user.get("orgId"),
        "userId": request.state.user.get("userId")
    }


    try:
        body = await request.json()
        toggle_type = body.get("type")
        if not toggle_type or toggle_type not in ["sync", "agent"]:
            logger.error(f"Toggle type is required and must be 'sync' or 'agent'. Got {toggle_type}")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Toggle type is required and must be 'sync' or 'agent'. Got {toggle_type}"
            )

        logger.info(f"Toggling connector instance {connector_id} {toggle_type} status")


        # Get organization
        org = await arango_service.get_document(
            user_info["orgId"],
            CollectionNames.ORGS.value
        )
        if not org:
            logger.error("Organization not found")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="Organization not found"
            )
        org_id = user_info["orgId"]
        user_id = user_info["userId"]
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )

        # Get instance
        instance = await connector_registry.get_connector_instance(connector_id=connector_id,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not instance:
            logger.error(f"Connector instance {connector_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector instance {connector_id} not found or access denied"
            )
        if instance.get("scope") == ConnectorScope.TEAM.value and not is_admin:
            logger.error("Only administrators can toggle team connectors")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only administrators can toggle team connectors"
            )
        if instance.get("createdBy") != user_id and not is_admin:
            logger.error("Only the creator or an administrator can toggle this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator or an administrator can toggle this connector"
            )
        if instance.get("scope") == ConnectorScope.PERSONAL.value and instance.get("createdBy") != user_id:
            logger.error("Only the creator can toggle this connector")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator can toggle this connector"
            )
        current_sync_status = instance["isActive"]
        current_agent_status = instance.get("isAgentActive", False)
        connector_type = instance.get("type", "").upper()

        # Determine target status
        if toggle_type == "sync":
            target_status = not current_sync_status
            status_field = "isActive"
        else:  # agent
            target_status = not current_agent_status
            status_field = "isAgentActive"

        # Validate prerequisites when enabling
        if toggle_type == "sync" and not current_sync_status:
            auth_type = (instance.get("authType") or "").upper()
            config_service = container.config_service()
            config_path = _get_config_path_for_instance(connector_id)
            config = await config_service.get_config(config_path)

            org_account_type = str(org.get("accountType", "")).lower()
            custom_google_business_logic = (
                org_account_type == "enterprise" and
                connector_type in ["GMAIL", "DRIVE"] and
                instance.get("scope") == ConnectorScope.TEAM.value
            )

            if auth_type == "OAUTH":
                if custom_google_business_logic:
                    auth_creds = config.get("auth", {}) if config else {}
                    if not auth_creds or not (
                        auth_creds.get("client_id") and
                        auth_creds.get("adminEmail")
                    ):
                        logger.error("Connector cannot be enabled until OAuth authentication is completed")
                        raise HTTPException(
                            status_code=HttpStatusCode.BAD_REQUEST.value,
                            detail="Connector cannot be enabled until OAuth authentication is completed"
                        )
                else:
                    creds = (config or {}).get("credentials") if config else None
                    if not creds or not creds.get("access_token"):
                        logger.error("Connector cannot be enabled until OAuth authentication is completed")
                        raise HTTPException(
                            status_code=HttpStatusCode.BAD_REQUEST.value,
                            detail="Connector cannot be enabled until OAuth authentication is completed"
                        )
            else:
                if not instance.get("isConfigured", False):
                    logger.error("Connector must be configured before enabling")
                    raise HTTPException(
                        status_code=HttpStatusCode.BAD_REQUEST.value,
                        detail="Connector must be configured before enabling"
                    )

        if toggle_type == "agent" and not current_agent_status:
            # Check if connector supports agent functionality
            if not instance.get("supportsAgent", False):
                logger.error("This connector does not support agent functionality")
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail="This connector does not support agent functionality"
                )

            if not instance.get("isConfigured", False):
                logger.error("Connector must be configured before enabling")
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail="Connector must be configured before enabling"
                )


        # Update connector status
        updates = {
            status_field: target_status,
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedBy": user_id
        }

        success = await connector_registry.update_connector_instance(
            connector_id=connector_id,
            updates=updates,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not success:
            logger.error(f"Failed to update {instance.get('name')} connector instance status")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Failed to update {instance.get('name')} connector instance status"
            )

        logger.info(f"Successfully toggled connector instance {connector_id} {toggle_type} to {target_status}")

        if toggle_type == "sync":
            # Prepare event messaging
            event_type = "appEnabled" if target_status else "appDisabled"
            credentials_route = f"api/v1/configurationManager/internal/connectors/{connector_id}/config"

            payload = {
                "orgId": user_info["orgId"],
                "appGroup": instance["appGroup"],
                "appGroupId": instance.get("appGroupId"),
                "credentialsRoute": credentials_route,
                "apps": [connector_type.replace(" ", "").lower()],
                "connectorId": connector_id,
                "syncAction": "immediate",
                "scope": instance.get("scope")
            }

            message = {
                "eventType": event_type,
                "payload": payload,
                "timestamp": get_epoch_timestamp_in_ms()
            }

            # Send message to sync-events topic
            logger.info(f"Sending message to sync-events topic: {message}")
            await producer.send_message(topic="entity-events", message=message)

        return {
            "success": True,
            "message": f"Connector instance {connector_id} {toggle_type} toggled successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle connector instance {connector_id} {toggle_type}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to toggle connector instance {connector_id} {toggle_type}: {str(e)}"
        )


# ============================================================================
# Schema Endpoint
# ============================================================================

@router.get("/api/v1/connectors/registry/{connector_type}/schema")
async def get_connector_schema(
    connector_type: str,
    request: Request
) -> Dict[str, Any]:
    """
    Get connector schema from registry.

    Args:
        connector_type: Type of connector
        request: FastAPI request object

    Returns:
        Dictionary with connector schema

    Raises:
        HTTPException: 404 if connector type not found
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry
    logger.info("Getting connector schema")
    try:
        metadata = await connector_registry.get_connector_metadata(connector_type)
        if not metadata:
            logger.error(f"Connector type {connector_type} not found")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector type {connector_type} not found"
            )

        schema = metadata.get("config", {})

        return {
            "success": True,
            "schema": schema
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting schema for {connector_type}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to get connector schema: {str(e)}"
        )

@router.get("/api/v1/connectors/agents/active")
async def get_active_agent_instances(
    request: Request,
    scope: Optional[str] = Query(None, description="personal | team"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None, description="Search by instance name/type/group")
) -> Dict[str, Any]:
    """
    Get all active agent instances for the current user.

    Args:
        request: FastAPI request object
        scope: Optional scope filter (personal/team)
        page: Page number (1-indexed)
        limit: Number of items per page
        search: Optional search query
    Returns:
        Dictionary with active agent instances
    """
    container = request.app.container
    logger = container.logger()
    try:
        logger.info("Getting active agent instances")
        connector_registry = request.app.state.connector_registry
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        if not user_id or not org_id:
            logger.error(f"User not authenticated: {user_id} {org_id}")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )

        if scope and scope not in [ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value]:
            logger.error("Invalid scope. Must be 'personal' or 'team'")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Invalid scope. Must be 'personal' or 'team'"
            )
        connectors = await connector_registry.get_active_agent_connector_instances(
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin,
            scope=scope,
            page=page,
            limit=limit,
            search=search
        )

        return {
                "success": True,
                **connectors
            }
    except Exception as e:
        logger.error(f"Error getting active agent instances: {str(e)}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to get active agent instances: {str(e)}"
        )
