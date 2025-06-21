import asyncio
import base64
import io
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

import google.oauth2.credentials
import jwt
from dependency_injector.wiring import Provide, inject
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from jose import JWTError
from pydantic import ValidationError

from app.config.configuration_service import config_node_constants
from app.config.utils.named_constants.arangodb_constants import (
    AccountType,
    CollectionNames,
    Connectors,
    MimeTypes,
    RecordRelations,
    RecordTypes,
)
from app.config.utils.named_constants.http_status_code_constants import (
    HttpStatusCode,
)
from app.connectors.api.middleware import WebhookAuthVerifier
from app.connectors.sources.google.admin.admin_webhook_handler import (
    AdminWebhookHandler,
)
from app.connectors.sources.google.common.google_token_handler import CredentialKeys
from app.connectors.sources.google.common.scopes import (
    GOOGLE_CONNECTOR_ENTERPRISE_SCOPES,
    GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES,
)
from app.connectors.sources.google.gmail.gmail_webhook_handler import (
    AbstractGmailWebhookHandler,
)
from app.connectors.sources.google.google_drive.drive_webhook_handler import (
    AbstractDriveWebhookHandler,
)
from app.modules.parsers.google_files.google_docs_parser import GoogleDocsParser
from app.modules.parsers.google_files.google_sheets_parser import GoogleSheetsParser
from app.modules.parsers.google_files.google_slides_parser import GoogleSlidesParser
from app.setups.connector_setup import AppContainer
from app.utils.llm import get_llm
from app.utils.logger import create_logger

logger = create_logger("connector_service")

router = APIRouter()

async def get_drive_webhook_handler(request: Request) -> Optional[AbstractDriveWebhookHandler]:
    try:
        container: AppContainer = request.app.container
        drive_webhook_handler = container.drive_webhook_handler()
        return drive_webhook_handler
    except Exception as e:
        logger.warning(f"Failed to get drive webhook handler: {str(e)}")
        return None


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
        container: AppContainer = request.app.container
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
    signed_url_handler=Depends(Provide[AppContainer.signed_url_handler]),
) -> dict:
    """Get signed URL for a record"""
    try:
        additional_claims = {"connector": connector, "purpose": "file_processing"}

        signed_url = await signed_url_handler.create_signed_url(
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
        container: AppContainer = request.app.container
        google_docs_parser = container.google_docs_parser()
        return google_docs_parser
    except Exception as e:
        logger.warning(f"Failed to get google docs parser: {str(e)}")
        return None


async def get_google_sheets_parser(request: Request) -> Optional[GoogleSheetsParser]:
    try:
        container: AppContainer = request.app.container
        google_sheets_parser = container.google_sheets_parser()
        return google_sheets_parser
    except Exception as e:
        logger.warning(f"Failed to get google sheets parser: {str(e)}")
        return None


async def get_google_slides_parser(request: Request) -> Optional[GoogleSlidesParser]:
    try:
        container: AppContainer = request.app.container
        google_slides_parser = container.google_slides_parser()
        return google_slides_parser
    except Exception as e:
        logger.warning(f"Failed to get google slides parser: {str(e)}")
        return None


@router.delete("/api/v1/delete/record/{record_id}")
@inject
async def handle_record_deletion(
    record_id: str, arango_service=Depends(Provide[AppContainer.arango_service])
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


@router.get("/api/v1/index/{org_id}/{connector}/record/{record_id}", response_model=None)
@inject
async def download_file(
    request: Request,
    org_id: str,
    record_id: str,
    connector: str,
    token: str,
    signed_url_handler=Depends(Provide[AppContainer.signed_url_handler]),
) -> Optional[dict | StreamingResponse]:
    try:
        try:
            config_service = request.app.state.config_service
            google_token_handler = request.app.state.google_token_handler
            arango_service = request.app.state.arango_service
        except Exception as e:
            logger.error(f"Error getting dependencies: {str(e)}")
            raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Error getting dependencies")

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
        record = await arango_service.get_document(
            record_id, CollectionNames.RECORDS.value
        )
        if not record:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Record not found")

        file_id = record.get("externalRecordId")

        if org["accountType"] in [AccountType.ENTERPRISE.value, AccountType.BUSINESS.value]:
            # Use service account credentials
            creds = await get_service_account_credentials(org_id, user_id, logger, arango_service, google_token_handler, request.app.container)
        else:
            # Individual account - use stored OAuth credentials
            creds = await get_user_credentials(org_id, user_id, logger, google_token_handler, request.app.container)

        # Download file based on connector type
        try:
            if connector.lower() == Connectors.GOOGLE_DRIVE.value.lower():
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
                        user_email, org_id, user_id
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
                        user_email, org_id, user_id
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
                        user_email, org_id, user_id
                    )
                    llm = await get_llm(logger, config_service)
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
                    "Content-Disposition": f'attachment; filename="{record.get("recordName", "")}"'
                }

                return StreamingResponse(
                    file_stream(), media_type=mime_type, headers=headers
                )

            elif connector.lower() == Connectors.GOOGLE_MAIL.value.lower():
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
                raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Invalid connector type")

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
) -> Optional[dict | StreamingResponse]:
    try:
        try:
            config_service = request.app.state.config_service
            google_token_handler = request.app.state.google_token_handler
            arango_service = request.app.state.arango_service
        except Exception as e:
            logger.error(f"Error getting dependencies: {str(e)}")
            raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Error getting dependencies")
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
        record_task = arango_service.get_document(
            record_id, CollectionNames.RECORDS.value
        )
        org, record = await asyncio.gather(org_task, record_task)

        if not org:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Organization not found")
        if not record:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Record not found")

        file_id = record.get("externalRecordId")
        connector = record.get("connectorName")
        recordType = record.get("recordType")

        # Different auth handling based on account type
        if org["accountType"] in [AccountType.ENTERPRISE.value, AccountType.BUSINESS.value]:
            # Use service account credentials
            creds = await get_service_account_credentials(org_id, user_id, logger, arango_service, google_token_handler, request.app.container)
        else:
            # Individual account - use stored OAuth credentials
            creds = await get_user_credentials(org_id, user_id,logger, google_token_handler, request.app.container)
        # Download file based on connector type
        try:
            if connector.lower() == Connectors.GOOGLE_DRIVE.value.lower():
                logger.info(f"Downloading Drive file: {file_id}")
                drive_service = build("drive", "v3", credentials=creds)
                file_name = record.get("recordName", "")
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
                raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="Invalid connector type")

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
        container: AppContainer = request.app.container
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


async def get_service_account_credentials(org_id: str, user_id: str, logger, arango_service, google_token_handler, container) -> google.oauth2.credentials.Credentials:
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
            credentials_json = await google_token_handler.get_enterprise_token(org_id)
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

async def get_user_credentials(org_id: str, user_id: str, logger, google_token_handler, container) -> google.oauth2.credentials.Credentials:
    """Helper function to get cached user credentials"""
    try:
        cache_key = f"{org_id}_{user_id}"
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
            SCOPES = GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES
            # Refresh token
            await google_token_handler.refresh_token(org_id, user_id)
            creds_data = await google_token_handler.get_individual_token(org_id, user_id)

            if not creds_data.get("access_token"):
                raise HTTPException(
                    status_code=HttpStatusCode.UNAUTHORIZED.value,
                    detail="Invalid credentials. Access token not found",
                )

            new_creds = google.oauth2.credentials.Credentials(
                token=creds_data.get(CredentialKeys.ACCESS_TOKEN.value),
                refresh_token=creds_data.get(CredentialKeys.REFRESH_TOKEN.value),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_data.get(CredentialKeys.CLIENT_ID.value),
                client_secret=creds_data.get(CredentialKeys.CLIENT_SECRET.value),
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
