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
from app.connectors.core.base.token_service.oauth_service import OAuthToken
from app.connectors.core.factory.connector_factory import ConnectorFactory
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
from app.services.featureflag.config.config import CONFIG
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
        connector = container.connectors_map.get(connector_name)
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

        creds = None
        if connector.lower() == Connectors.GOOGLE_DRIVE.value.lower() or connector.lower() == Connectors.GOOGLE_MAIL.value.lower():
            if org["accountType"] in [AccountType.ENTERPRISE.value, AccountType.BUSINESS.value]:
                # Use service account credentials
                creds = await get_service_account_credentials(org_id, user_id, logger, arango_service, google_token_handler, request.app.container, connector)
            else:
                # Individual account - use stored OAuth credentials
                creds = await get_user_credentials(org_id, user_id, logger, google_token_handler, request.app.container,connector=connector)
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
                        user_email, org_id, user_id, app_name=connector.lower()
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
                        user_email, org_id, user_id, app_name=connector.lower()
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
                        user_email, org_id, user_id, app_name=connector.lower()
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
                connector_name = connector.lower().replace(" ", "")
                container: ConnectorAppContainer = request.app.container
                connector: BaseConnector = container.connectors_map.get(connector_name)
                if not connector:
                    raise HTTPException(
                        status_code=HttpStatusCode.NOT_FOUND.value,
                        detail=f"Connector '{connector_name}' not found"
                    )
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
        recordType = record.record_type
        logger.info(f"Connector: {connector}")
        # Different auth handling based on account type
        creds = None
        if connector.lower() == Connectors.GOOGLE_DRIVE.value.lower() or connector.lower() == Connectors.GOOGLE_MAIL.value.lower():

            if org["accountType"] in [AccountType.ENTERPRISE.value, AccountType.BUSINESS.value]:
                # Use service account credentials
                creds = await get_service_account_credentials(org_id, user_id, logger, arango_service, google_token_handler, request.app.container,connector)
            else:
                # Individual account - use stored OAuth credentials
                creds = await get_user_credentials(org_id, user_id,logger, google_token_handler, request.app.container,connector=connector)
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
                connector_name = connector.lower().replace(" ", "")
                container: ConnectorAppContainer = request.app.container
                connector: BaseConnector = container.connectors_map.get(connector_name)
                if not connector:
                    raise HTTPException(
                        status_code=HttpStatusCode.NOT_FOUND.value,
                        detail=f"Connector '{connector_name}' not found"
                    )
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


async def get_service_account_credentials(org_id: str, user_id: str, logger, arango_service, google_token_handler, container,connector: str) -> google.oauth2.credentials.Credentials:
    """Helper function to get service account credentials"""
    try:
        service_creds_lock = container.service_creds_lock()

        async with service_creds_lock:
            if not hasattr(container, 'service_creds_cache'):
                container.service_creds_cache = {}
                logger.info("Created service credentials cache")

            cache_key = f"{org_id}_{user_id}"
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
            credentials_json = await google_token_handler.get_enterprise_token(org_id,app_name=connector)
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

async def get_user_credentials(org_id: str, user_id: str, logger, google_token_handler, container,connector: str) -> google.oauth2.credentials.Credentials:
    """Helper function to get cached user credentials"""
    try:
        cache_key = f"{org_id}_{user_id}_{connector}"
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
            SCOPES = await google_token_handler.get_account_scopes(app_name=connector)
            # Refresh token
            await google_token_handler.refresh_token(org_id, user_id,app_name=connector)
            creds_data = await google_token_handler.get_individual_token(org_id, user_id,app_name=connector)

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
    org_id: str,
    connector: str,
    arango_service: BaseArangoService = Depends(get_arango_service)
)-> Dict[str, Any]:
    result = await arango_service.get_connector_stats(org_id, connector)

    if result["success"]:
        return {"success": True, "data": result["data"]}
    else:
        raise HTTPException(status_code=500, detail=result["message"])


async def check_beta_connector_access(
    app_name: str,
    request: Request
) -> None:
    """
    Check if the connector is a beta connector and if beta connectors are enabled.
    Raises HTTPException if beta connectors are disabled and the connector is beta.

    Args:
        app_name: Name of the connector
        request: FastAPI request object

    Raises:
        HTTPException: 403 if beta connectors are disabled and connector is beta
    """
    try:
        container = request.app.container
        feature_flag_service = await container.feature_flag_service()

        # Refresh feature flags to get latest values
        try:
            await feature_flag_service.refresh()
        except Exception as e:
            container.logger().debug(f"Feature flag refresh failed: {e}")

        # Check if beta connectors are enabled
        beta_enabled = feature_flag_service.is_feature_enabled(CONFIG.ENABLE_BETA_CONNECTORS)

        if not beta_enabled:
            # Check if this connector is a beta connector
            beta_connectors = ConnectorFactory.list_beta_connectors()
            normalized_name = app_name.replace(' ', '').lower()

            if normalized_name in beta_connectors:
                raise HTTPException(
                    status_code=403,
                    detail=f"Beta connectors are not enabled. The connector '{app_name}' is a beta connector and cannot be accessed. Please enable beta connectors in platform settings to use this connector."
                )
    except HTTPException:
        raise
    except Exception as e:
        # On error, log but don't block access (fail-open for safety)
        container = request.app.container
        container.logger().debug(f"Beta connector check failed: {e}")


@router.get("/api/v1/connectors/config/{app_name}")
async def get_connector_config(
    app_name: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service)
) -> Dict[str, Any]:
    """
    Retrieve connector configuration using registry metadata and etcd (no DB requirement).
    Optimized to use batch-fetched statuses.
    """
    # Check beta connector access
    await check_beta_connector_access(app_name, request)

    try:
        container = request.app.container
        logger = container.logger()
        logger.info(f"Getting connector config for {app_name}")

        # Read connector metadata from registry (source of truth)
        # This now uses batch-fetched statuses internally for better performance
        connector_registry = request.app.state.connector_registry
        registry_entry = await connector_registry.get_connector_by_name(app_name)
        if not registry_entry:
            raise HTTPException(status_code=404, detail=f"Connector {app_name} not found in registry")

        # Load config from etcd (may be empty on first load)
        try:
            config_service = container.config_service()
            filtered_app_name = _sanitize_app_name(app_name)
            config_key: str = f"/services/connectors/{filtered_app_name}/config"
            config: Optional[Dict[str, Any]] = await config_service.get_config(config_key)
        except Exception as e:
            logger.error(f"Failed to load config from etcd for {app_name}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to load config from etcd for {app_name}")

        if not config:
            config = {"auth": {}, "sync": {}, "filters": {}}
        config = config.copy() if config else {"auth": {}, "sync": {}, "filters": {}}
        config.pop("credentials", None)
        config.pop("oauth", None)
        response_dict: Dict[str, Any] = {
            "name": registry_entry["name"],
            "appGroupId": registry_entry.get("appGroupId"),
            "appGroup": registry_entry.get("appGroup"),
            "authType": registry_entry.get("authType"),
            "appDescription": registry_entry.get("appDescription", ""),
            "appCategories": registry_entry.get("appCategories", []),
            "supportsRealtime": registry_entry.get("supportsRealtime", False),
            "supportsSync": registry_entry.get("supportsSync", False),
            "iconPath": registry_entry.get("iconPath", "/assets/icons/connectors/default.svg"),
            "config": config,
            "isActive": registry_entry.get("isActive", False),
            "isConfigured": registry_entry.get("isConfigured", False),
            "isAuthenticated": registry_entry.get("isAuthenticated", False),
        }

        return {"success": True, "config": response_dict}
    except HTTPException:
        raise
    except Exception as e:
        logger = request.app.container.logger()
        logger.error(f"Failed to get connector config for {app_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get connector config for {app_name}")


@router.get("/api/v1/connectors/config-schema/{app_name}")
async def get_connector_config_and_schema(
    app_name: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service)
) -> Dict[str, Any]:
    """
    Retrieve both connector configuration and schema in a single request.
    This reduces API calls from 2 to 1, improving performance.
    """
    # Check beta connector access
    await check_beta_connector_access(app_name, request)

    try:
        container = request.app.container
        logger = container.logger()
        logger.info(f"Getting connector config and schema for {app_name}")

        # Read connector metadata from registry (source of truth)
        connector_registry = request.app.state.connector_registry
        registry_entry = await connector_registry.get_connector_by_name(app_name)
        if not registry_entry:
            raise HTTPException(status_code=404, detail=f"Connector {app_name} not found in registry")

        # Load config from etcd (may be empty on first load)
        try:
            config_service = container.config_service()
            filtered_app_name = _sanitize_app_name(app_name)
            config_key: str = f"/services/connectors/{filtered_app_name}/config"
            config: Optional[Dict[str, Any]] = await config_service.get_config(config_key)
        except Exception as e:
            logger.error(f"Failed to load config from etcd for {app_name}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to load config from etcd for {app_name}")

        if not config:
            config = {"auth": {}, "sync": {}, "filters": {}}
        config = config.copy() if config else {"auth": {}, "sync": {}, "filters": {}}
        config.pop("credentials", None)
        config.pop("oauth", None)

        # Get schema from registry entry
        schema = registry_entry.get("config", {})

        response_dict: Dict[str, Any] = {
            "name": registry_entry["name"],
            "appGroupId": registry_entry.get("appGroupId"),
            "appGroup": registry_entry.get("appGroup"),
            "authType": registry_entry.get("authType"),
            "appDescription": registry_entry.get("appDescription", ""),
            "appCategories": registry_entry.get("appCategories", []),
            "supportsRealtime": registry_entry.get("supportsRealtime", False),
            "supportsSync": registry_entry.get("supportsSync", False),
            "iconPath": registry_entry.get("iconPath", "/assets/icons/connectors/default.svg"),
            "config": config,
            "isActive": registry_entry.get("isActive", False),
            "isConfigured": registry_entry.get("isConfigured", False),
            "isAuthenticated": registry_entry.get("isAuthenticated", False),
        }

        return {"success": True, "config": response_dict, "schema": schema}
    except HTTPException:
        raise
    except Exception as e:
        logger = request.app.container.logger()
        logger.error(f"Failed to get connector config and schema for {app_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get connector config and schema for {app_name}")


@router.get("/api/v1/connectors")
async def get_connectors(
    request: Request,
) -> Dict[str, Any]:
    """
    Retrieve all available connectors.

    Args:
        request: FastAPI request object

    Returns:
        Dict containing success status and list of connectors

    Raises:
        HTTPException: 404 if no connectors found
    """
    connector_registry = request.app.state.connector_registry
    result: Optional[List[Dict[str, Any]]] = await connector_registry.get_all_connectors()

    if result:
        return {"success": True, "connectors": result}
    else:
        raise HTTPException(status_code=404, detail="No connectors found")


@router.get("/api/v1/connectors/active")
async def get_active_connector(
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service),
) -> Dict[str, Any]:
    """
    Get active connectors.
    """
    connector_registry = request.app.state.connector_registry
    result: List[Dict[str, Any]] = await connector_registry.get_active_connector()
    return {"success": True, "connectors": result}


@router.get("/api/v1/connectors/inactive")
async def get_inactive_connector(
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service),
) -> Dict[str, Any]:
    """
    Get inactive connectors.
    """
    connector_registry = request.app.state.connector_registry
    result: List[Dict[str, Any]] = await connector_registry.get_inactive_connector()
    return {"success": True, "connectors": result}


@router.get("/api/v1/connectors/{app_name}")
async def get_connector_by_name(
    app_name: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service)
) -> Dict[str, Any]:
    """
    Retrieve a single connector by name.
    Optimized to use batch-fetched statuses.
    Note: This route must come after /active and /inactive to avoid conflicts.

    Args:
        app_name: Name of the connector
        request: FastAPI request object
        arango_service: Injected ArangoService dependency

    Returns:
        Dict containing success status and connector data

    Raises:
        HTTPException: 404 if connector not found, 403 if beta connector access denied
    """
    # Check beta connector access
    await check_beta_connector_access(app_name, request)

    try:
        container = request.app.container
        logger = container.logger()
        logger.info(f"Getting connector by name: {app_name}")

        connector_registry = request.app.state.connector_registry
        result: Optional[Dict[str, Any]] = await connector_registry.get_connector_by_name(app_name)

        if not result:
            raise HTTPException(status_code=404, detail=f"Connector {app_name} not found")

        return {"success": True, "connector": result}
    except HTTPException:
        raise
    except Exception as e:
        logger = request.app.container.logger()
        logger.error(f"Failed to get connector {app_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get connector {app_name}")

@router.get("/api/v1/connectors/schema/{app_name}")
async def get_connector_schema(
    app_name: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service)
) -> Dict[str, Any]:
    """
    Retrieve connector schema from registry metadata.
    Optimized to use batch-fetched statuses.

    Args:
        app_name: Name of the connector
        request: FastAPI request object
        arango_service: Injected ArangoService dependency

    Returns:
        Dict containing success status and connector schema

    Raises:
        HTTPException: 404 if connector not found, 403 if beta connector access denied
    """
    # Check beta connector access
    await check_beta_connector_access(app_name, request)

    try:
        container = request.app.container
        logger = container.logger()
        logger.info(f"Getting connector schema for {app_name}")
        connector_registry = request.app.state.connector_registry
        result: Optional[Dict[str, Any]] = await connector_registry.get_connector_by_name(app_name)
        if not result:
            raise HTTPException(status_code=404, detail=f"Connector {app_name} not found")

        # Return the config object as schema
        schema = result.get("config", {})
        return {"success": True, "schema": schema}
    except HTTPException:
        raise
    except Exception as e:
        logger = request.app.container.logger()
        logger.error(f"Failed to get connector schema for {app_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get connector schema for {app_name}")


@router.get("/api/v1/connectors/{app_name}/oauth/authorize")
async def get_oauth_authorization_url(
    app_name: str,
    request: Request,
    base_url: Optional[str] = Query(None),
    arango_service: BaseArangoService = Depends(get_arango_service),
) -> Dict[str, Any]:
    """
    Get OAuth authorization URL for a connector.

    Args:
        app_name: Name of the connector
        request: FastAPI request object
        arango_service: Injected ArangoService dependency

    Returns:
        Dict containing authorization URL and state
    """
    # Check beta connector access
    await check_beta_connector_access(app_name, request)

    container = request.app.container
    logger = container.logger()

    try:
        # Get connector metadata from registry (source of truth)
        connector_registry = request.app.state.connector_registry
        registry_entry = await connector_registry.get_connector_by_name(app_name)
        if not registry_entry:
            raise HTTPException(status_code=404, detail=f"Connector {app_name} not found")

        # Check if it's an OAuth connector
        if (registry_entry.get('authType') or '').upper() not in ['OAUTH', 'OAUTH_ADMIN_CONSENT']:
            raise HTTPException(status_code=400, detail=f"Connector {app_name} does not support OAuth")

        # Get OAuth configuration from etcd
        config_service = container.config_service()
        filtered_app_name = _sanitize_app_name(app_name)
        config_key = f"/services/connectors/{filtered_app_name}/config"
        config = await config_service.get_config(config_key)
        if not config or not config.get('auth'):
            raise HTTPException(status_code=400, detail=f"OAuth configuration not found for {app_name}")

        auth_config = config['auth']

        # Get OAuth configuration from registry metadata
        connector_auth_config = registry_entry.get('config', {}).get('auth', {})
        redirect_uri = connector_auth_config.get('redirectUri', '')
        authorize_url = auth_config.get('authorizeUrl') or connector_auth_config.get('authorizeUrl', '')
        token_url = auth_config.get('tokenUrl') or connector_auth_config.get('tokenUrl', '')

        if not redirect_uri:
            raise HTTPException(status_code=400, detail=f"Redirect URI not configured for {app_name}")

        if not authorize_url or not token_url:
            raise HTTPException(status_code=400, detail=f"OAuth URLs not configured for {app_name}")

        # Create OAuth config using the OAuth service
        from app.connectors.core.base.token_service.oauth_service import (
            OAuthProvider,
        )
        if base_url and len(base_url) > 0:
            redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"
        else:
            endpoint_keys = '/services/endpoints'
            endpoints = await config_service.get_config(endpoint_keys,use_cache=False)
            base_url = endpoints.get('frontendPublicUrl', 'http://localhost:3001')
            redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"

        oauth_config = get_oauth_config(app_name, auth_config)


        # Create OAuth provider and generate authorization URL
        oauth_provider = OAuthProvider(
            config=oauth_config,
            key_value_store=container.key_value_store(),
            credentials_path=f"/services/connectors/{filtered_app_name}/config"
        )
        # Generate authorization URL using OAuth provider
        # Add provider-specific parameters to ensure refresh_token is issued where applicable
        extra_params = {}
        if app_name.upper() in ['DRIVE', 'GMAIL']:
            # Google requires these for refresh_token on repeated consents
            extra_params.update({
                'access_type': 'offline',
                'prompt': 'consent',
                'include_granted_scopes': 'true',
            })

        auth_url = await oauth_provider.start_authorization(**extra_params)

        # Clean up OAuth provider
        await oauth_provider.close()

        # Add tenant-specific parameters for Microsoft
        if app_name.upper() == 'ONEDRIVE':
            # Add Microsoft-specific parameters
            from urllib.parse import parse_qs, urlencode, urlparse
            parsed_url = urlparse(auth_url)
            params = parse_qs(parsed_url.query)
            params['response_mode'] = ['query']
            if (registry_entry.get('authType') or '').upper() == 'OAUTH_ADMIN_CONSENT':
                params['prompt'] = ['admin_consent']

            # Rebuild URL with additional parameters
            auth_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?{urlencode(params, doseq=True)}"

        # Extract state from the authorization URL for response
        from urllib.parse import parse_qs, urlparse
        parsed_url = urlparse(auth_url)
        query_params = parse_qs(parsed_url.query)
        state = query_params.get('state', [None])[0]

        return {
            "success": True,
            "authorizationUrl": auth_url,
            "state": state
        }

    except Exception as e:
        logger.error(f"Error generating OAuth URL for {app_name}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to generate OAuth URL: {str(e)}")


@router.get("/api/v1/connectors/{app_name}/oauth/callback")
async def handle_oauth_callback(
    app_name: str,
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    base_url: Optional[str] = Query(None),
    arango_service: BaseArangoService = Depends(get_arango_service),
) -> Dict[str, Any]:
    """GET callback handler for OAuth redirects.
    This endpoint processes the OAuth callback and redirects to the frontend with the result.
    """
    # Check beta connector access
    await check_beta_connector_access(app_name, request)

    container = request.app.container
    logger = container.logger()
    config_service = container.config_service()
    connector_registry = request.app.state.connector_registry

    try:

        connector_name = app_name

        async def _get_settings_base_path() -> str:
            """Decide frontend settings base path by org account type.
            Falls back to individual when unknown.
            """
            try:
                orgs = await arango_service.get_all_documents(CollectionNames.ORGS.value)
                if isinstance(orgs, list) and len(orgs) > 0:
                    account_type = str((orgs[0] or {}).get("accountType", "")).lower()
                    if account_type in ["business", "organization", "enterprise"]:
                        return "/account/company-settings/settings/connector"
            except Exception:
                pass
            return "/account/individual/settings/connector"

        settings_base_path = await _get_settings_base_path()

        # Normalize common non-errors coming from frontend as strings
        if error in ["null", "undefined", "None", ""]:
            error = None

        # Check for OAuth errors
        if error:
            logger.error(f"OAuth error for {app_name}: {error}")
            return {"success": False, "error": error, "redirect_url": f"{base_url}/connectors/oauth/callback/{connector_name}?oauth_error={error}"}


        if not code or not state:
            logger.error(f"Missing OAuth parameters for {app_name}")
            return {"success": False, "error": "missing_parameters", "redirect_url": f"{base_url}/connectors/oauth/callback/{connector_name}?oauth_error=missing_parameters"}

        # Process OAuth callback directly here
        logger.info(f"Processing OAuth callback for {app_name}")

        # Get connector metadata from registry (source of truth)
        registry_entry = await connector_registry.get_connector_by_name(app_name)
        if not registry_entry:
            logger.error(f"Connector {app_name} not found")
            return {"success": False, "error": "connector_not_found", "redirect_url": f"{base_url}/connectors/oauth/callback/{connector_name}?oauth_error=connector_not_found"}

        # Get OAuth configuration
        filtered_app_name = _sanitize_app_name(app_name)
        config_key = f"/services/connectors/{filtered_app_name}/config"
        config = await config_service.get_config(config_key)

        if not config or not config.get('auth'):
            logger.error(f"OAuth configuration not found for {app_name}")
            return {"success": False, "error": "config_not_found", "redirect_url": f"{base_url}/connectors/oauth/callback/{connector_name}?oauth_error=config_not_found"}

        auth_config = config['auth']

        # Get OAuth configuration from registry metadata
        connector_auth_config = registry_entry.get('config', {}).get('auth', {})
        redirect_uri = connector_auth_config.get('redirectUri', '')
        authorize_url = auth_config.get('authorizeUrl') or connector_auth_config.get('authorizeUrl', '')
        token_url = auth_config.get('tokenUrl') or connector_auth_config.get('tokenUrl', '')

        if not redirect_uri:
            return {"success": False, "error": "redirect_uri_not_configured", "redirect_url": f"{base_url}/connectors/oauth/callback/{connector_name}?oauth_error=redirect_uri_not_configured"}

        if not authorize_url or not token_url:
            return {"success": False, "error": "oauth_urls_not_configured", "redirect_url": f"{base_url}/connectors/oauth/callback/{connector_name}?oauth_error=oauth_urls_not_configured"}

        # Create OAuth config using the OAuth service
        from app.connectors.core.base.token_service.oauth_service import (
            OAuthProvider,
        )
        if base_url and len(base_url) > 0:
            redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"
        else:
            endpoint_keys = '/services/endpoints'
            endpoints = await config_service.get_config(endpoint_keys,use_cache=False)
            base_url = endpoints.get('frontendPublicUrl', 'http://localhost:3001')
            redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"

        oauth_config = get_oauth_config(app_name, auth_config)

        # Create OAuth provider and exchange code for token
        oauth_provider = OAuthProvider(
            config=oauth_config,
            key_value_store=container.key_value_store(),
            credentials_path=f"/services/connectors/{filtered_app_name}/config"
        )

        # Exchange code for token using OAuth provider (ensure cleanup)
        try:
            token = await oauth_provider.handle_callback(code, state)
        finally:
            await oauth_provider.close()

        # Validate token before storing
        if not token or not token.access_token:
            logger.error(f"Invalid token received for {app_name}")
            return {"success": False, "error": "invalid_token", "redirect_url": f"{base_url}/connectors/oauth/callback/{connector_name}?oauth_error=invalid_token"}

        logger.info(f"OAuth tokens stored successfully for {app_name}")

        # Refresh configuration cache so subsequent reads see latest credentials
        try:
            config_service = container.config_service()
            kv_store = container.key_value_store()
            updated_config = await kv_store.get_key(f"/services/connectors/{filtered_app_name}/config")
            if isinstance(updated_config, dict):
                await config_service.set_config(f"/services/connectors/{filtered_app_name}/config", updated_config)
                logger.info(f"Refreshed config cache for {app_name} after OAuth callback")
        except Exception as cache_err:
            logger.warning(f"Could not refresh config cache for {app_name}: {cache_err}")

        # Schedule token refresh ~10 minutes before expiry
        try:
            from app.connectors.core.base.token_service.token_refresh_service import (
                TokenRefreshService,
            )
            refresh_service = TokenRefreshService(container.key_value_store(), arango_service)
            await refresh_service.schedule_token_refresh(_sanitize_app_name(app_name), token)
            logger.info(f"Scheduled token refresh for {app_name}")
        except Exception as sched_err:
            logger.warning(f"Could not schedule token refresh for {app_name}: {sched_err}")

        # Return redirect URL for frontend to follow
        redirect_url = f"{base_url}{settings_base_path}/{app_name}"
        logger.info(f"OAuth successful, redirecting to: {redirect_url}")
        logger.info("app name: " + app_name)
        logger.info("connector name: " + connector_name)
        # Return appropriate response based on caller
        updates = {
            "isAuthenticated": True,
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }
        await connector_registry.update_connector(app_name, updates)
        logger.info(f"Connector 'isAuthenticated' status for {app_name} set to True.")

        return {"success": True, "redirect_url": redirect_url}

    except Exception as e:
        logger.error(f"Error handling OAuth GET callback for {app_name}: {str(e)}")
        connector_name = app_name
        if base_url and len(base_url) > 0:
            base_url = f"{base_url.rstrip('/')}/"
        else:
            endpoint_keys = '/services/endpoints'
            endpoints = await config_service.get_config(endpoint_keys,use_cache=False)
            base_url = endpoints.get('frontendPublicUrl', 'http://localhost:3001')
            base_url = f"{base_url.rstrip('/')}/"
        try:
            orgs = await arango_service.get_all_documents(CollectionNames.ORGS.value)
            account_type = str((orgs[0] or {}).get("accountType", "")).lower() if isinstance(orgs, list) and orgs else ""
            settings_base_path = "/account/company-settings/settings/connector" if account_type in ["business", "organization", "enterprise"] else "/account/individual/settings/connector"
        except Exception:
            settings_base_path = "/account/individual/settings/connector"
        error_url = f"{base_url}/connectors/oauth/callback/{connector_name}?oauth_error=server_error"
        updates = {
            "isAuthenticated": False,
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }
        await connector_registry.update_connector(app_name, updates)
        logger.info(f"Connector 'isAuthenticated' status for {app_name} set to False due to an error.")
        return {"success": False, "error": "server_error", "redirect_url": error_url}


async def get_connector_filter_options_from_config(app_name: str, connector_config: Dict[str, Any], token_or_credentials: OAuthToken | Dict[str, Any], config_service) -> Dict[str, Any]:
    """
    Get filter options for a connector based on its configuration by calling dynamic endpoints.

    Args:
        app_name: Name of the connector
        connector_config: Connector configuration from database
        token_or_credentials: OAuth token or other credentials
        config_service: Configuration service instance

    Returns:
        Dict containing available filter options
    """
    try:
        # Get filter endpoints from connector config
        filter_endpoints = connector_config.get('config', {}).get('filters', {}).get('endpoints', {})

        if not filter_endpoints:
            return {}

        filter_options = {}

        # Dynamically fetch filter options from configured endpoints
        for filter_type, endpoint in filter_endpoints.items():
            try:
                if endpoint == "static":
                    # Handle static filter types (like fileTypes)
                    filter_options[filter_type] = await _get_static_filter_options(app_name, filter_type)
                else:
                    # Call dynamic API endpoint
                    options = await _fetch_filter_options_from_api(endpoint, filter_type, token_or_credentials, app_name)
                    if options:
                        filter_options[filter_type] = options

            except Exception as e:
                print(f"Error fetching {filter_type} for {app_name}: {str(e)}")
                # Fallback to static options for this filter type
                filter_options[filter_type] = await _get_static_filter_options(app_name, filter_type)

        return filter_options

    except Exception as e:
        print(f"Error getting filter options for {app_name}: {str(e)}")
        # Return hardcoded fallback options
        return await _get_fallback_filter_options(app_name)


async def _fetch_filter_options_from_api(endpoint: str, filter_type: str, token_or_credentials: OAuthToken | Dict[str, Any], app_name: str) -> List[Dict[str, str]]:
    """
    Fetch filter options from a dynamic API endpoint.

    Args:
        endpoint: API endpoint URL
        filter_type: Type of filter (labels, folders, channels, etc.)
        token_or_credentials: OAuth token or other credentials
        app_name: Name of the connector

    Returns:
        List of filter options with value and label
    """

    import aiohttp

    headers = {}

    # Set up authentication headers based on token type
    if hasattr(token_or_credentials, 'access_token'):
        # OAuth token
        headers['Authorization'] = f"Bearer {token_or_credentials.access_token}"
    elif isinstance(token_or_credentials, dict):
        # API token or other credentials
        if 'access_token' in token_or_credentials:
            headers['Authorization'] = f"Bearer {token_or_credentials['access_token']}"
        elif 'api_token' in token_or_credentials:
            headers['Authorization'] = f"Bearer {token_or_credentials['api_token']}"
        elif 'token' in token_or_credentials:
            headers['Authorization'] = f"Bearer {token_or_credentials['token']}"

    async with aiohttp.ClientSession() as session:
        async with session.get(endpoint, headers=headers) as response:
            if response.status == HttpStatusCode.SUCCESS.value:
                data = await response.json()
                return _parse_filter_response(data, filter_type, app_name)
            else:
                print(f"API call failed for {filter_type}: {response.status}")
                return []
    return []


def _parse_filter_response(data: Dict[str, Any], filter_type: str, app_name: str) -> List[Dict[str, str]]:
    """
    Parse API response to extract filter options.

    Args:
        data: API response data
        filter_type: Type of filter being parsed
        app_name: Name of the connector

    Returns:
        List of filter options with value and label
    """
    options = []

    try:
        if app_name.upper() == 'GMAIL' and filter_type == 'labels':
            # Gmail labels API response
            labels = data.get('labels', [])
            for label in labels:
                if label.get('type') == 'user':  # Only user-created labels, not system labels
                    options.append({
                        "value": label['id'],
                        "label": label['name']
                    })

        elif app_name.upper() == 'DRIVE' and filter_type == 'folders':
            # Google Drive folders API response
            files = data.get('files', [])
            for file in files:
                options.append({
                    "value": file['id'],
                    "label": file['name']
                })

        elif app_name.upper() == 'ONEDRIVE' and filter_type == 'folders':
            # OneDrive folders API response
            items = data.get('value', [])
            for item in items:
                if item.get('folder'):
                    options.append({
                        "value": item['id'],
                        "label": item['name']
                    })

        elif app_name.upper() == 'SLACK' and filter_type == 'channels':
            # Slack channels API response
            channels = data.get('channels', [])
            for channel in channels:
                if not channel.get('is_archived'):
                    options.append({
                        "value": channel['id'],
                        "label": f"#{channel['name']}"
                    })

        elif app_name.upper() == 'CONFLUENCE' and filter_type == 'spaces':
            # Confluence spaces API response
            spaces = data.get('results', [])
            for space in spaces:
                options.append({
                    "value": space['key'],
                    "label": space['name']
                })

    except Exception as e:
        print(f"Error parsing {filter_type} response for {app_name}: {str(e)}")

    return options


async def _get_static_filter_options(app_name: str, filter_type: str) -> List[Dict[str, str]]:
    """
    Get static filter options for connectors that don't have dynamic endpoints.

    Args:
        app_name: Name of the connector
        filter_type: Type of filter

    Returns:
        List of static filter options
    """
    if filter_type == 'fileTypes':
        return [
            {"value": "document", "label": "Documents"},
            {"value": "spreadsheet", "label": "Spreadsheets"},
            {"value": "presentation", "label": "Presentations"},
            {"value": "pdf", "label": "PDFs"},
            {"value": "image", "label": "Images"},
            {"value": "video", "label": "Videos"}
        ]
    elif filter_type == 'contentTypes':
        return [
            {"value": "page", "label": "Pages"},
            {"value": "blogpost", "label": "Blog Posts"},
            {"value": "comment", "label": "Comments"},
            {"value": "attachment", "label": "Attachments"}
        ]

    return []


async def _get_fallback_filter_options(app_name: str) -> Dict[str, List[Dict[str, str]]]:
    """
    Get hardcoded fallback filter options when dynamic fetching fails.

    Args:
        app_name: Name of the connector

    Returns:
        Dict containing fallback filter options
    """
    fallback_options = {
        'GMAIL': {
            "labels": [
                {"value": "INBOX", "label": "Inbox"},
                {"value": "SENT", "label": "Sent"},
                {"value": "DRAFT", "label": "Draft"},
                {"value": "SPAM", "label": "Spam"},
                {"value": "TRASH", "label": "Trash"}
            ]
        },
        'DRIVE': {
            "fileTypes": [
                {"value": "document", "label": "Documents"},
                {"value": "spreadsheet", "label": "Spreadsheets"},
                {"value": "presentation", "label": "Presentations"},
                {"value": "pdf", "label": "PDFs"},
                {"value": "image", "label": "Images"},
                {"value": "video", "label": "Videos"}
            ]
        },
        'ONEDRIVE': {
            "fileTypes": [
                {"value": "document", "label": "Documents"},
                {"value": "spreadsheet", "label": "Spreadsheets"},
                {"value": "presentation", "label": "Presentations"},
                {"value": "pdf", "label": "PDFs"},
                {"value": "image", "label": "Images"},
                {"value": "video", "label": "Videos"}
            ]
        },
        'SLACK': {
            "channels": [
                {"value": "general", "label": "#general"},
                {"value": "random", "label": "#random"}
            ]
        },
        'CONFLUENCE': {
            "spaces": [
                {"value": "DEMO", "label": "Demo Space"},
                {"value": "DOCS", "label": "Documentation"}
            ]
        }
    }

    return fallback_options.get(app_name.upper(), {})


@router.get("/api/v1/connectors/{app_name}/filters")
async def get_connector_filters(
    app_name: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service),
) -> Dict[str, Any]:
    """
    Get filter options for a connector based on its authentication type.

    Args:
        app_name: Name of the connector
        request: FastAPI request object
        arango_service: Injected ArangoService dependency

    Returns:
        Dict containing available filter options
    """
    # Check beta connector access
    await check_beta_connector_access(app_name, request)

    container = request.app.container
    logger = container.logger()

    try:
        # Convert app_name to uppercase for database lookup (connectors are stored in uppercase)
        app_name_upper = app_name.upper()

        # Get connector metadata from registry
        connector_registry = request.app.state.connector_registry
        connector_config = await connector_registry.get_connector_by_name(app_name_upper)
        if not connector_config:
            raise HTTPException(status_code=404, detail=f"Connector {app_name_upper} not found")

        # Get credentials based on auth type
        config_service = container.config_service()
        auth_type = (connector_config.get('authType') or '').upper()
        filtered_app_name = _sanitize_app_name(app_name)
        config_key = f"/services/connectors/{filtered_app_name}/config"
        config = await config_service.get_config(config_key)

        token_or_credentials = None

        if auth_type == 'OAUTH':
            # Only OAUTH requires user-specific OAuth credentials
            if not config or not config.get('credentials'):
                raise HTTPException(status_code=400, detail=f"OAuth credentials not found for {app_name}. Please authenticate first.")

            # Create token object
            token_or_credentials = OAuthToken.from_dict(config['credentials'])

        elif auth_type == 'OAUTH_ADMIN_CONSENT':
            # OAUTH_ADMIN_CONSENT doesn't require user tokens, use configured auth values
            if not config or not config.get('auth'):
                raise HTTPException(status_code=400, detail=f"Connector configuration not found for {app_name}. Please configure first.")
            token_or_credentials = config.get('auth', {})

        elif auth_type == 'API_TOKEN':
            # Get API token from config
            if not config or not config.get('auth'):
                raise HTTPException(status_code=400, detail=f"API token configuration not found for {app_name}. Please configure first.")
            token_or_credentials = config.get('auth', {})

        elif auth_type == 'USERNAME_PASSWORD':
            # Get username/password from config
            if not config or not config.get('auth'):
                raise HTTPException(status_code=400, detail=f"Authentication configuration not found for {app_name}. Please configure first.")
            token_or_credentials = config.get('auth', {})

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported authentication type: {auth_type}")

        # Get filter options from configured endpoints
        filter_options = await get_connector_filter_options_from_config(app_name, connector_config, token_or_credentials, config_service)

        return {
            "success": True,
            "filterOptions": filter_options
        }

    except Exception as e:
        logger.error(f"Error getting filter options for {app_name}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get filter options: {str(e)}")


@router.post("/api/v1/connectors/{app_name}/filters")
async def save_connector_filters(
    app_name: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service),
) -> Dict[str, Any]:
    """
    Save filter selections for a connector.

    Args:
        app_name: Name of the connector
        request: FastAPI request object
        arango_service: Injected ArangoService dependency

    Returns:
        Dict containing success status
    """
    # Check beta connector access
    await check_beta_connector_access(app_name, request)

    container = request.app.container
    logger = container.logger()

    try:
        body = await request.json()
        filter_selections = body.get('filters', {})

        if not filter_selections:
            raise HTTPException(status_code=400, detail="No filter selections provided")

        # Get current config
        config_service = container.config_service()
        filtered_app_name = _sanitize_app_name(app_name)
        config_key = f"/services/connectors/{filtered_app_name}/config"
        config = await config_service.get_config(config_key)

        if not config:
            config = {}

        # Update filters in config
        if 'filters' not in config:
            config['filters'] = {}

        config['filters']['values'] = filter_selections

        # Save updated config
        await config_service.set_config(config_key, config)

        return {
            "success": True,
            "message": "Filter selections saved successfully"
        }

    except Exception as e:
        logger.error(f"Error saving filter selections for {app_name}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to save filter selections: {str(e)}")

@router.put("/api/v1/connectors/config/{app_name}")
async def update_connector_config(
    app_name: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service),
) -> Dict[str, Any]:
    """
    Update connector configuration including authentication, sync, and filter settings.

    Args:
        app_name: Name of the connector application
        request: FastAPI request object
        arango_service: Injected ArangoService dependency

    Returns:
        Dict containing success status and updated configuration

    Raises:
        HTTPException: 400 if invalid JSON, 404 if connector config not found, 403 if beta connector access denied
    """
    # Check beta connector access
    await check_beta_connector_access(app_name, request)

    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry
    try:
        body_dict: Dict[str, Any] = await request.json()
        logger.info(f"Body dict: {body_dict}")
        base_url = body_dict.get('base_url', '')
    except Exception as e:
        logger.error(f"Failed to parse request body for {app_name}: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    try:
        config_service = container.config_service()
        filtered_app_name = _sanitize_app_name(app_name)
        config_key: str = f"/services/connectors/{filtered_app_name}/config"

        # Load existing (unused now; we overwrite sections and clear auth artifacts)
        try:
            await config_service.get_config(config_key)
        except Exception:
            pass

        # Build new config from incoming sections only
        merged_config: Dict[str, Any] = {}

        for section in ["auth", "sync", "filters"]:
            if section in body_dict and isinstance(body_dict[section], dict):
                merged_config[section] = body_dict[section]
            elif section in body_dict:
                merged_config[section] = body_dict[section]

        # Explicitly clear credentials and oauth state on config updates so
        # the UI will require re-auth and we avoid stale secrets
        merged_config["credentials"] = None
        merged_config["oauth"] = None


        # Create app in database if it doesn't exist (only when configuring)
        try:
            await connector_registry.create_app_when_configured(app_name)
            logger.info(f"App created in database for {app_name} (if not already exists)")
        except Exception as e:
            logger.warning(f"App may already exist in database for {app_name}: {e}")

        app_doc = await connector_registry.get_connector_by_name(app_name)
        auth_type = app_doc.get('authType', '')
        connector_config = app_doc.get('config', {})

        redirect_uri = connector_config.get('auth', {}).get('redirectUri', '')
        if base_url and len(base_url) > 0:
            redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"
        else:
            endpoint_keys = '/services/endpoints'
            endpoints = await config_service.get_config(endpoint_keys,use_cache=False)
            base_url = endpoints.get('frontendPublicUrl', 'http://localhost:3001')
            redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"

        # Ensure OAuth static metadata from registry is present in etcd config
        auth_meta = connector_config.get('auth', {})
        if 'auth' not in merged_config or not isinstance(merged_config['auth'], dict):
            merged_config['auth'] = {}

        merged_config['auth']['authorizeUrl'] = merged_config['auth'].get('authorizeUrl') or auth_meta.get('authorizeUrl', '')
        merged_config['auth']['tokenUrl'] = merged_config['auth'].get('tokenUrl') or auth_meta.get('tokenUrl', '')
        merged_config['auth']['scopes'] = auth_meta.get('scopes', [])
        merged_config["auth"]["redirectUri"] = redirect_uri
        merged_config["auth"]["authType"] = auth_type

        await config_service.set_config(config_key, merged_config)
        logger.info(f"Config stored in etcd for {app_name}")

        updates = {
            "isConfigured": True,
            "isAuthenticated": False,
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }
        await connector_registry.update_connector(app_name, updates)
        logger.info(f"Connector updated in database for {app_name}")
        return {"success": True, "config": merged_config}
    except Exception as e:
        logger.error(f"Failed to store config in etcd for {app_name}: {e}")
        raise HTTPException(
                status_code=500,
                detail=f"Internal error updating connector config for {app_name}"
        )


@router.post("/api/v1/connectors/toggle/{app_name}")
async def toggle_connector(
    app_name: str,
    request: Request,
    arango_service: BaseArangoService = Depends(get_arango_service),
) -> Dict[str, Any]:
    """
    Toggle connector active status and trigger sync events.

    Args:
        app_name: Name of the connector to toggle
        request: FastAPI request object
        arango_service: Injected ArangoService dependency

    Returns:
        Dict containing success status and message

    Raises:
        HTTPException: 404 if org/connector not found, 403 if beta connector access denied, 500 for internal errors
    """
    # Check beta connector access
    await check_beta_connector_access(app_name, request)

    container = request.app.container
    logger = container.logger()
    producer = container.messaging_producer
    connector_registry = request.app.state.connector_registry

    user_info: Dict[str, Optional[str]] = {
        "orgId": request.state.user.get("orgId"),
        "userId": request.state.user.get("userId"),
    }

    logger.info(f"Toggling connector {app_name}")
    logger.debug(f"User info: {user_info}")

    try:
        # Fetch organization data
        org = await arango_service.get_document(user_info["orgId"], CollectionNames.ORGS.value)
        if not org:
            raise HTTPException(status_code=404, detail="No organizations found")

        # Fetch and validate app
        app = await connector_registry.get_connector_by_name(app_name)
        if not app:
            raise HTTPException(status_code=404, detail=f"Connector {app_name} not found")

        current_status: bool = app["isActive"]

        # If attempting to enable, enforce prerequisites based on auth type
        try:
            if not current_status:  # enabling
                auth_type = (app.get("authType") or "").upper()
                config_service = container.config_service()
                filtered_app_name = _sanitize_app_name(app_name)
                config_key = f"/services/connectors/{filtered_app_name}/config"
                cfg = await config_service.get_config(config_key)
                # Allow enabling rules:
                # - OAUTH: require credentials.access_token
                # - OAUTH_ADMIN_CONSENT: no user token required; must be configured
                # - Others (API_TOKEN, USERNAME_PASSWORD, etc.): must be configured
                org_account_type = str(org.get("accountType", "")).lower()
                custom_google_business_logic = org_account_type == "enterprise" and app_name.upper() in ["GMAIL", "DRIVE"]
                if auth_type == "OAUTH":
                    if custom_google_business_logic:
                        auth_creds = cfg.get("auth", {})
                        if not auth_creds or not (auth_creds.get("client_id") and auth_creds.get("adminEmail")):
                            logger.error(f"Connector {app_name} cannot be enabled until OAuth authentication is completed")
                            raise HTTPException(
                                status_code=HttpStatusCode.BAD_REQUEST.value,
                                detail="Connector cannot be enabled until OAuth authentication is completed",
                            )
                    else:
                        creds = (cfg or {}).get("credentials") if cfg else None
                        if not creds or not creds.get("access_token"):
                            logger.error(f"Connector {app_name} cannot be enabled until OAuth authentication is completed")
                            raise HTTPException(
                                status_code=HttpStatusCode.BAD_REQUEST.value,
                                detail="Connector cannot be enabled until OAuth authentication is completed",
                            )
                elif auth_type == "OAUTH_ADMIN_CONSENT":
                    if not app.get("isConfigured", False):
                        logger.error(f"Connector {app_name} must be configured before enabling")
                        raise HTTPException(
                            status_code=HttpStatusCode.BAD_REQUEST.value,
                            detail="Connector must be configured before enabling",
                        )
                else:
                    if not app.get("isConfigured", False):
                        logger.error(f"Connector {app_name} must be configured before enabling")
                        raise HTTPException(
                            status_code=HttpStatusCode.BAD_REQUEST.value,
                            detail="Connector must be configured before enabling",
                        )
        except HTTPException:
            raise
        except Exception as prereq_err:
            logger.error(f"Failed to validate enable preconditions for {app_name}: {prereq_err}")
            raise HTTPException(status_code=500, detail="Failed to validate connector state")

        # Update connector status using connector registry
        try:
            logger.info(f"🚀 Updating connector status: {app_name}")

            updates = {
                "isActive": not current_status,
                "updatedAtTimestamp": get_epoch_timestamp_in_ms()
            }

            success = await connector_registry.update_connector(app_name, updates)
            if not success:
                logger.warning(f"⚠️ Failed to update connector: {app_name}")
                raise HTTPException(status_code=404, detail=f"Connector {app_name} not found")

            logger.info(f"✅ Successfully updated connector: {app_name}")
            new_status: bool = not current_status

        except HTTPException:
            # Re-raise HTTP exceptions to preserve status codes
            raise
        except Exception as e:
            logger.error(f"❌ Failed to update connector {app_name}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to update connector {app_name}")

        # Prepare event messaging
        event_type: str = "appEnabled" if new_status else "appDisabled"

        # Determine credentials routes based on account type
        filtered_app_name = _sanitize_app_name(app_name)
        credentials_route: str = f"api/v1/configurationManager/internal/connectors/{filtered_app_name}/config"

        # Build message payload
        payload: Dict[str, Any] = {
            "orgId": user_info["orgId"],
            "appGroup": app["appGroup"],
            "appGroupId": app["appGroupId"],
            "credentialsRoute": credentials_route,
            "apps": [filtered_app_name],
            "syncAction": "immediate",
        }

        message: Dict[str, Any] = {
            'eventType': event_type,
            'payload': payload,
            'timestamp': get_epoch_timestamp_in_ms()
        }

        # Send message to sync-events topic
        await producer.send_message(topic='entity-events', message=message)

        return {"success": True, "message": f"Connector {app_name} toggled successfully"}

    except HTTPException:
        # Re-raise HTTP exceptions to preserve status codes and messages
        raise
    except Exception as e:
        logger.error(f"Failed to toggle connector {app_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to toggle connector {app_name}")
