from fastapi import APIRouter
import base64
import json
import jwt
from google.oauth2 import service_account
from dependency_injector.wiring import inject, Provide
from fastapi import Request, Depends, HTTPException, BackgroundTasks, status
from app.setups.connector_setup import AppContainer
from app.utils.logger import create_logger
from fastapi.responses import StreamingResponse
import os
from app.utils.llm import get_llm
from app.config.configuration_service import ConfigurationService, config_node_constants
from app.config.arangodb_constants import CollectionNames, RecordRelations, Connectors, RecordTypes
from app.connectors.google.scopes import GOOGLE_CONNECTOR_ENTERPRISE_SCOPES, GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES
from typing import Optional, Any
import google.oauth2.credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io
from jose import JWTError
from pydantic import ValidationError
from app.connectors.api.middleware import WebhookAuthVerifier
import tempfile
from pathlib import Path
import asyncio
import time
import uuid

logger = create_logger("Python Connector Service")

router = APIRouter()

async def get_drive_webhook_handler(request: Request) -> Optional[Any]:
    try:
        container: AppContainer = request.app.container
        drive_webhook_handler = container.drive_webhook_handler()
        return drive_webhook_handler
    except Exception as e:
        logger.warning(f"Failed to get drive webhook handler: {str(e)}")
        return None
    
@router.post("/drive/webhook")
@inject
async def handle_drive_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """Handle incoming webhook notifications from Google Drive"""
    try:
        
        verifier = WebhookAuthVerifier(logger)
        if not await verifier.verify_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized webhook request")

        drive_webhook_handler = await get_drive_webhook_handler(request)
        
        if drive_webhook_handler is None:
            logger.warning("Drive webhook handler not yet initialized - skipping webhook processing")
            return {"status": "skipped", "message": "Webhook handler not yet initialized"}

        # Log incoming request details
        headers = dict(request.headers)
        logger.info("📥 Incoming webhook request")

        # Get important headers
        resource_state = (
            headers.get('X-Goog-Resource-State') or
            headers.get('x-goog-resource-state') or
            headers.get('X-GOOG-RESOURCE-STATE')
        )

        logger.info("Resource state: %s", resource_state)

        # Process notification in background
        if resource_state != "sync":
            background_tasks.add_task(
                drive_webhook_handler.process_notification,
                headers
            )
            return {"status": "accepted"}
        else:
            logger.info("Received sync verification request")
            return {"status": "sync_verified"}

    except Exception as e:
        logger.error("Error processing webhook: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e

async def get_gmail_webhook_handler(request: Request) -> Optional[Any]:
    try:
        container: AppContainer = request.app.container
        gmail_webhook_handler = container.gmail_webhook_handler()
        return gmail_webhook_handler
    except Exception as e:
        logger.warning(f"Failed to get drive webhook handler: {str(e)}")
        return None

@router.get("/gmail/webhook")
@router.post("/gmail/webhook")
@inject
async def handle_gmail_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """Handles incoming Pub/Sub messages"""
    try:
        gmail_webhook_handler = await get_gmail_webhook_handler(request)
        
        if gmail_webhook_handler is None:
            logger.warning("Gmail webhook handler not yet initialized - skipping webhook processing")
            return {"status": "skipped", "message": "Webhook handler not yet initialized"}

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
                    notification
                )

                return {"status": "ok"}
            except Exception as e:
                logger.error("Error processing message data: %s", str(e))
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid message data format: {str(e)}"
                )
        else:
            logger.warning("No data found in message")
            return {"status": "error", "message": "No data found"}

    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in webhook body: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON format: {str(e)}"
        )
    except Exception as e:
        logger.error("Error processing webhook: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/drive/{org_id}")
@router.post("/drive/{org_id}")
@inject
async def root(
    background_tasks: BackgroundTasks,
    org_id,
    sync_tasks=Depends(Provide[AppContainer.sync_tasks])
):
    """Initialize sync service and wait for schedule"""
    try:
        # Add initialization to background tasks
        background_tasks.add_task(sync_tasks.drive_sync_service.initialize, org_id)
        return {
            "status": "accepted",
            "message": "Sync service initialization queued",
            "service": "Google Drive Sync"
        }
    except Exception as e:
        logger.error(
            "Failed to queue sync service initialization: %s", {str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e

@router.get("/drive/{org_id}/sync/start")
@router.post("/drive/{org_id}/sync/start")
@inject
async def start_sync(
    org_id: str,
    background_tasks: BackgroundTasks,
    sync_tasks=Depends(Provide[AppContainer.sync_tasks])
):
    """Queue immediate start of the sync service"""
    try:
        logger.info(f"Sync tasks: {sync_tasks}, {type(sync_tasks)}")
        background_tasks.add_task(
            sync_tasks.drive_manual_sync_control, 'start', org_id)
        return {
            "status": "accepted",
            "message": "Sync service start request queued"
        }
    except Exception as e:
        logger.error("Failed to queue sync service start: %s", {str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e


@router.get("/drive/{org_id}/sync/pause")
@router.post("/drive/{org_id}/sync/pause")
@inject
async def pause_sync(
    org_id: str,
    background_tasks: BackgroundTasks,
    sync_tasks=Depends(Provide[AppContainer.sync_tasks])
):
    """Pause the sync service"""
    try:
        background_tasks.add_task(
            sync_tasks.drive_manual_sync_control, 'pause', org_id)
        return {
            "status": "accepted",
            "message": "Sync service pause request queued"
        }
    except Exception as e:
        logger.error("Failed to queue sync service pause: %s", {str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e


@router.get("/drive/{org_id}/sync/resume")
@router.post("/drive/{org_id}/sync/resume")
@inject
async def resume_sync(
    org_id: str,
    background_tasks: BackgroundTasks,
    sync_tasks=Depends(Provide[AppContainer.sync_tasks])
):
    """Resume the sync service"""
    try:
        background_tasks.add_task(
            sync_tasks.drive_manual_sync_control, 'resume', org_id)
        return {
            "status": "accepted",
            "message": "Sync service resume request queued"
        }
    except Exception as e:
        logger.error("Failed to queue sync service resume: %s", {str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e


@router.get("/drive/sync/user/{user_email}")
@router.post("/drive/sync/user/{user_email}")
@inject
async def sync_user(
    user_email: str,
    drive_sync_service=Depends(Provide[AppContainer.drive_sync_service])
):
    """Sync a user's Google Drive"""
    try:
        return await drive_sync_service.sync_specific_user(user_email)
    except Exception as e:
        logger.error("Error syncing user: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e

@router.get("/gmail/{org_id}")
@router.post("/gmail/{org_id}")
@inject
async def root(
    org_id,
    background_tasks: BackgroundTasks,
    sync_tasks=Depends(Provide[AppContainer.sync_tasks])
):
    """Initialize sync service and wait for schedule"""
    try:
        # Add initialization to background tasks
        background_tasks.add_task(sync_tasks.gmail_sync_service.initialize, org_id)
        return {
            "status": "accepted",
            "message": "Sync service initialization queued",
            "service": "Google Gmail Sync"
        }
    except Exception as e:
        logger.error(
            "Failed to queue sync service initialization: %s", {str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e

@router.get("/gmail/{org_id}/sync/start")
@router.post("/gmail/{org_id}/sync/start")
@inject
async def start_sync(
    org_id,
    background_tasks: BackgroundTasks,
    sync_tasks=Depends(Provide[AppContainer.sync_tasks])
):
    """Queue immediate start of the sync service"""
    try:
        background_tasks.add_task(
            sync_tasks.gmail_manual_sync_control, 'start', org_id)
        return {
            "status": "accepted",
            "message": "Sync service start request queued"
        }
    except Exception as e:
        logger.error("Failed to queue sync service start: %s", {str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e

@router.get("/gmail/{org_id}/sync/pause")
@router.post("/gmail/{org_id}/sync/pause")
@inject
async def pause_sync(
    org_id,
    background_tasks: BackgroundTasks,
    sync_tasks=Depends(Provide[AppContainer.sync_tasks])
):
    """Pause the sync service"""
    try:
        background_tasks.add_task(
            sync_tasks.gmail_manual_sync_control, 'pause', org_id)
        return {
            "status": "accepted",
            "message": "Sync service pause request queued"
        }
    except Exception as e:
        logger.error("Failed to queue sync service pause: %s", {str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e


@router.get("/gmail/{org_id}/sync/resume")
@router.post("/gmail/{org_id}/sync/resume")
@inject
async def resume_sync(
    org_id,
    background_tasks: BackgroundTasks,
    sync_tasks=Depends(Provide[AppContainer.sync_tasks])
):
    """Resume the sync service"""
    try:
        background_tasks.add_task(
            sync_tasks.gmail_manual_sync_control, 'resume', org_id)
        return {
            "status": "accepted",
            "message": "Sync service resume request queued"
        }
    except Exception as e:
        logger.error("Failed to queue sync service resume: %s", {str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e

@router.get("/gmail/sync/user/{user_email}")
@router.post("/gmail/sync/user/{user_email}")
@inject
async def sync_user(
    user_email: str,
    gmail_sync_service=Depends(Provide[AppContainer.gmail_sync_service])
):
    """Sync a user's Google Drive"""
    try:
        return await gmail_sync_service.sync_specific_user(user_email)
    except Exception as e:
        logger.error("Error syncing user: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e


@router.get("/api/v1/{org_id}/{user_id}/{connector}/record/{record_id}/signedUrl")
@inject
async def get_signed_url(
    org_id: str,
    user_id: str,
    connector: str,
    record_id: str,
    signed_url_handler=Depends(Provide[AppContainer.signed_url_handler])
):
    """Get signed URL for a record"""
    try:
        additional_claims = {
            'connector': connector,
            'purpose': 'file_processing'
        }

        signed_url = await signed_url_handler.create_signed_url(
            record_id,
            org_id,
            user_id,
            additional_claims=additional_claims,
            connector=connector
        )
        # Return as JSON instead of plain text
        return {"signedUrl": signed_url}
    except Exception as e:
        logger.error(f"Error getting signed URL: {repr(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
async def get_google_docs_parser(request: Request):
    try:
        container: AppContainer = request.app.container
        google_docs_parser = container.google_docs_parser()
        return google_docs_parser
    except Exception as e:
        logger.warning(f"Failed to get google docs parser: {str(e)}")
        return None
    
async def get_google_sheets_parser(request: Request):
    try:
        container: AppContainer = request.app.container
        google_sheets_parser = container.google_sheets_parser()
        return google_sheets_parser
    except Exception as e:
        logger.warning(f"Failed to get google sheets parser: {str(e)}")
        return None
    
async def get_google_slides_parser(request: Request):
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
    record_id: str,
    arango_service = Depends(Provide[AppContainer.arango_service])
):
    try:
        response = await arango_service.delete_records_and_relations(record_id, hard_delete=True)
        if not response:
            raise HTTPException(
                status_code=404,
                detail=f"Record with ID {record_id} not found"
            )
        return {
            "status": "success",
            "message": "Record deleted successfully",
            "response": response
        }
    except HTTPException as he:
        raise he  # Re-raise HTTP exceptions as-is
    except Exception as e:
        logger.error(f"Error deleting record: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while deleting record: {str(e)}"
        )

@router.get("/api/v1/index/{org_id}/{connector}/record/{record_id}")
@inject
async def download_file(
    request: Request,
    org_id: str,
    record_id: str,
    connector: str,
    token: str,
    google_token_handler=Depends(Provide[AppContainer.google_token_handler]),
    arango_service=Depends(Provide[AppContainer.arango_service]),
    signed_url_handler=Depends(Provide[AppContainer.signed_url_handler]),
    config_service=Depends(Provide[AppContainer.config_service]),
):
    
    async def get_service_account_credentials(user_id):
        """Helper function to get service account credentials"""
        try:
            # Load service account credentials from environment or secure storage
            SCOPES = GOOGLE_CONNECTOR_ENTERPRISE_SCOPES
            
            credentials_json = await google_token_handler.get_enterprise_token(org_id)
            credentials = service_account.Credentials.from_service_account_info(
                credentials_json,
                scopes=SCOPES
            )
            user = await arango_service.get_user_by_user_id(user_id)
            
            # # Get the user email from the record to impersonate
            credentials = credentials.with_subject(user['email'])
            return credentials
        
        except Exception as e:
            logger.error(f"Error getting service account credentials: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Error accessing service account credentials"
            )
            
    async def get_user_credentials(org_id, user_id):
        """Helper function to get user credentials"""
        try:
            SCOPES = GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES

            await google_token_handler.refresh_token(org_id, user_id)
            creds_data = await google_token_handler.get_individual_token(org_id, user_id)
            # Create credentials object from the response using google.oauth2.credentials.Credentials
            creds = google.oauth2.credentials.Credentials(
                token=creds_data.get('access_token'),
                refresh_token=creds_data.get('refresh_token'),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_data.get('client_id'),
                client_secret=creds_data.get('client_secret'),
                scopes=SCOPES
            )
            if not creds_data.get('access_token'):
                raise HTTPException(status_code=401, detail="Invalid credentials. Access token not found")
            
            return creds
        
        except Exception as e:
            logger.error(f"Error getting user credentials: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Error accessing user credentials"
            )

    try:
        logger.info(f"Downloading file {record_id} with connector {connector}")
        # Verify signed URL using the handler
        
        payload = signed_url_handler.validate_token(token)
        user_id = payload.user_id
        user = await arango_service.get_user_by_user_id(user_id)
        user_email = user.get('email')

        # Verify file_id matches the token
        if payload.record_id != record_id:
            logger.error(f"""Token does not match requested file: {
                         payload.record_id} != {record_id}""")
            raise HTTPException(
                status_code=401, detail="Token does not match requested file")

        # Get org details to determine account type
        org = await arango_service.get_document(org_id, CollectionNames.ORGS.value)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Get record details
        record = await arango_service.get_document(record_id, CollectionNames.RECORDS.value)
        if not record:
            raise HTTPException(status_code=404, detail="Record not found")
        
        file_id = record.get('externalRecordId')
        
        # Different auth handling based on account type
        if org['accountType'] in ['enterprise', 'business']:
            # Use service account credentials
            creds = await get_service_account_credentials(user_id)
        else:
            # Individual account - use stored OAuth credentials
            creds = await get_user_credentials(org_id, user_id)

        # Download file based on connector type
        try:
            if connector == "drive":
                logger.info(f"Downloading Drive file: {file_id}")
                # Build the Drive service
                drive_service = build('drive', 'v3', credentials=creds)
                
                file = await arango_service.get_document(record_id, CollectionNames.FILES.value)
                if not file:
                    raise HTTPException(status_code=404, detail="File not found")
                mime_type = file.get('mimeType')
                                
                if mime_type == "application/vnd.google-apps.presentation":
                    logger.info("🚀 Processing Google Slides")
                    google_slides_parser = await get_google_slides_parser(request)
                    await google_slides_parser.connect_service(user_email, org_id, user_id)
                    result = await google_slides_parser.process_presentation(file_id)
                    
                    # Convert result to JSON and return as StreamingResponse
                    json_data = json.dumps(result).encode('utf-8')
                    return StreamingResponse(
                        iter([json_data]),
                        media_type='application/json'
                    )

                if mime_type == "application/vnd.google-apps.document":
                    logger.info("🚀 Processing Google Docs")
                    google_docs_parser = await get_google_docs_parser(request)
                    await google_docs_parser.connect_service(user_email, org_id, user_id)
                    content = await google_docs_parser.parse_doc_content(file_id)
                    logger.debug(f"content: {content}")
                    all_content, headers, footers = google_docs_parser.order_document_content(content)
                    logger.debug(f"all_content: {all_content}")
                    logger.debug(f"headers: {headers}")
                    logger.debug(f"footers: {footers}")
                    result = {
                        'all_content': all_content,
                        'headers': headers,
                        'footers': footers
                    }
                    
                    # Convert result to JSON and return as StreamingResponse
                    json_data = json.dumps(result).encode('utf-8')
                    return StreamingResponse(
                        iter([json_data]),
                        media_type='application/json'
                    )

                if mime_type == "application/vnd.google-apps.spreadsheet":
                    logger.info("🚀 Processing Google Sheets")
                    google_sheets_parser = await get_google_sheets_parser(request)
                    await google_sheets_parser.connect_service(user_email, org_id, user_id)
                    llm = await get_llm(logger, config_service)
                    # List and process spreadsheets
                    parsed_result = await google_sheets_parser.parse_spreadsheet(file_id)
                    all_sheet_results = []
                    for sheet_idx, sheet in enumerate(parsed_result['sheets'], 1):
                        sheet_name = sheet['name']
                        
                        # Process sheet with summaries
                        sheet_data = await google_sheets_parser.process_sheet_with_summaries(llm, sheet_name, file_id)
                        if sheet_data is None:
                            continue
                        
                        all_sheet_results.append(sheet_data)
                                            
                    result = {
                        'parsed_result': parsed_result,
                        'all_sheet_results': all_sheet_results
                    }
                                 
                    # Convert result to JSON and return as StreamingResponse
                    json_data = json.dumps(result).encode('utf-8')
                    return StreamingResponse(
                        iter([json_data]),
                        media_type='application/json'
                    )
                
                # Enhanced logging for regular file download
                logger.info(f"Starting binary file download for file_id: {file_id}")
                file_buffer = io.BytesIO()

                # Download the file with enhanced progress logging
                logger.info("Initiating download process...")
                request = drive_service.files().get_media(fileId=file_id)
                
                downloader = MediaIoBaseDownload(file_buffer, request)

                done = False
                try:
                    while not done:
                        status, done = downloader.next_chunk()
                        logger.info(f"Download {int(status.progress() * 100)}%.")

                    # Reset buffer position to start
                    file_buffer.seek(0)
                                    
                    # Stream the response with content type from metadata
                    logger.info("Initiating streaming response...")
                    return StreamingResponse(
                        file_buffer,
                        media_type=mime_type
                    )

                except Exception as download_error:
                    logger.error(f"Download failed: {repr(download_error)}")
                    if hasattr(download_error, 'response'):
                        logger.error(f"Response status: {download_error.response.status_code}")
                        logger.error(f"Response content: {download_error.response.content}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"File download failed: {repr(download_error)}"
                    )

            elif connector == "gmail":
                logger.info(f"Downloading Gmail attachment for record_id: {record_id}")
                gmail_service = build('gmail', 'v1', credentials=creds)
                
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
                        message_id = message['messageId']
                        logger.info(f"Found message ID: {message_id}")
                    else:
                        raise Exception("Related message not found")

                    attachment = gmail_service.users().messages().attachments().get(
                        userId='me',
                        messageId=message_id,
                        id=file_id
                    ).execute()
                    
                    # Decode the attachment data
                    file_data = base64.urlsafe_b64decode(attachment['data'])
                    
                    # Stream the response
                    return StreamingResponse(
                        iter([file_data]),
                        media_type='application/octet-stream'
                    )
                    
                except Exception as gmail_error:
                    logger.info(f"Failed to get attachment from Gmail: {str(gmail_error)}, trying Drive...")
                    
                    # Try to get the file from Drive as fallback
                    try:
                        drive_service = build('drive', 'v3', credentials=creds)
                        file_buffer = io.BytesIO()
                        
                        logger.info("Initiating download process...")
                                                
                        request = drive_service.files().get_media(fileId=file_id)
                        downloader = MediaIoBaseDownload(file_buffer, request)
                        
                        done = False
                        
                        try:                        
                            while not done:
                                status, done = downloader.next_chunk()
                                logger.info(f"Download {int(status.progress() * 100)}%.")
                        
                            file_buffer.seek(0)
                        
                            return StreamingResponse(
                                file_buffer,
                                media_type='application/octet-stream'
                            )
                        except Exception as download_error:
                            logger.error(f"Download failed: {repr(download_error)}")
                            raise HTTPException(
                                status_code=500,
                                detail=f"File download failed: {repr(download_error)}"
                            )

                    except Exception as drive_error:
                        logger.error(f"Failed to get file from both Gmail and Drive. Gmail error: {str(gmail_error)}, Drive error: {str(drive_error)}")
                        raise HTTPException(
                            status_code=500,
                            detail="Failed to download file from both Gmail and Drive"
                        )
            else:
                raise HTTPException(status_code=400, detail="Invalid connector type")


        except Exception as e:
            logger.error(f"Error downloading file: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Error downloading file: {str(e)}"
            )

    except HTTPException as e:
        logger.error("HTTPException: %s", str(e))
        raise e
    except Exception as e:
        logger.error("Error downloading file: %s", str(e))
        raise HTTPException(status_code=500, detail="Error downloading file")


@router.get("/api/v1/stream/record/{record_id}")
@inject
async def stream_record(
    request: Request,
    record_id: str,
    convertTo: Optional[str] = None,
    google_token_handler=Depends(Provide[AppContainer.google_token_handler]),
    arango_service=Depends(Provide[AppContainer.arango_service]),
    config_service=Depends(Provide[AppContainer.config_service]),
):
    async def get_service_account_credentials(user_id):
        """Helper function to get service account credentials"""
        try:
            # Load service account credentials from environment or secure storage
            SCOPES = GOOGLE_CONNECTOR_ENTERPRISE_SCOPES
            
            credentials_json = await google_token_handler.get_enterprise_token(org_id)
            credentials = service_account.Credentials.from_service_account_info(
                credentials_json,
                scopes=SCOPES
            )
            user = await arango_service.get_user_by_user_id(user_id)
            
            # # Get the user email from the record to impersonate
            credentials = credentials.with_subject(user['email'])
            return credentials
        
        except Exception as e:
            logger.error(f"Error getting service account credentials: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Error accessing service account credentials"
            )
            
    async def get_user_credentials(org_id, user_id):
        """Helper function to get user credentials"""
        try:
            SCOPES = GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES
            await google_token_handler.refresh_token(org_id, user_id)
            creds_data = await google_token_handler.get_individual_token(org_id, user_id)
            
            # Create credentials object from the response using google.oauth2.credentials.Credentials
            creds = google.oauth2.credentials.Credentials(
                token=creds_data.get('access_token'),
                refresh_token=creds_data.get('refresh_token'),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_data.get('client_id'),
                client_secret=creds_data.get('client_secret'),
                scopes=SCOPES
            )
            if not creds_data.get('access_token'):
                raise HTTPException(status_code=401, detail="Invalid credentials. Access token not found")
            
            return creds
        
        except Exception as e:
            logger.error(f"Error getting user credentials: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Error accessing user credentials"
            )            
    try:
        try:
            auth_header = request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith("Bearer "):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing or invalid Authorization header"
                )
            # Extract the token
            token = auth_header.split(" ")[1]
            secret_keys = await config_service.get_config(config_node_constants.SECRET_KEYS.value)
            jwt_secret = secret_keys.get('jwtSecret')
            payload = jwt.decode(
                token,
                jwt_secret,
                algorithms=['HS256']
            )

            org_id = payload.get('orgId')
            user_id = payload.get('userId')
            
        except JWTError as e:
            logger.error("JWT validation error: %s", str(e))
            raise HTTPException(
                status_code=401, detail="Invalid or expired token")
        except ValidationError as e:
            logger.error("Payload validation error: %s", str(e))
            raise HTTPException(
                status_code=400, detail="Invalid token payload")
        except Exception as e:
            logger.error(
                "Unexpected error during token validation: %s", str(e))
            raise HTTPException(
                status_code=500, detail="Error validating token")

        org = await arango_service.get_document(org_id, CollectionNames.ORGS.value)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        
        record = await arango_service.get_document(record_id, CollectionNames.RECORDS.value)
        if not record:
            raise HTTPException(status_code=404, detail="Record not found")
        
        file_id = record.get('externalRecordId')
        connector = record.get('connectorName')
        recordType = record.get('recordType')
        
        # Different auth handling based on account type
        if org['accountType'] in ['enterprise', 'business']:
            # Use service account credentials
            creds = await get_service_account_credentials(user_id)
        else:
            # Individual account - use stored OAuth credentials
            creds = await get_user_credentials(org_id, user_id)

        # Download file based on connector type
        try:
            if connector == Connectors.GOOGLE_DRIVE.value:
                logger.info(f"Downloading Drive file: {file_id}")
                drive_service = build('drive', 'v3', credentials=creds)
                file_name = record.get('recordName', '')
                
                # Check if PDF conversion is requested
                if convertTo == 'pdf':
                    with tempfile.TemporaryDirectory() as temp_dir:
                        temp_file_path = os.path.join(temp_dir, file_name)
                        
                        # Download file to temp directory
                        with open(temp_file_path, 'wb') as f:
                            request = drive_service.files().get_media(fileId=file_id)
                            downloader = MediaIoBaseDownload(f, request)
                            
                            done = False
                            while not done:
                                status, done = downloader.next_chunk()
                                logger.info(f"Download {int(status.progress() * 100)}%.")
                        
                        # Convert to PDF
                        pdf_path = await convert_to_pdf(temp_file_path, temp_dir)
                        return StreamingResponse(
                            open(pdf_path, 'rb'),
                            media_type='application/pdf',
                            headers={
                                'Content-Disposition': f'inline; filename="{Path(file_name).stem}.pdf"'
                            }
                        )
                
                # Regular file download without conversion - now with direct streaming
                async def file_stream():
                    try:
                        request = drive_service.files().get_media(fileId=file_id)
                        buffer = io.BytesIO()
                        downloader = MediaIoBaseDownload(
                            buffer,
                            request
                        )
                        
                        done = False
                        while not done:
                            try:
                                status, done = downloader.next_chunk()
                                if status:
                                    logger.debug(f"Download progress: {int(status.progress() * 100)}%")
                                
                                # Get the data from buffer
                                buffer.seek(0)
                                chunk = buffer.read()
                                
                                if chunk:  # Only yield if we have data
                                    yield chunk
                                
                                # Clear buffer for next chunk
                                buffer.seek(0)
                                buffer.truncate(0)
                                
                                # Yield control back to event loop
                                await asyncio.sleep(0)
                                
                            except Exception as chunk_error:
                                logger.error(f"Error streaming chunk: {str(chunk_error)}")
                                raise HTTPException(
                                    status_code=500,
                                    detail="Error during file streaming"
                                )
                                
                    except Exception as stream_error:
                        logger.error(f"Error in file stream: {str(stream_error)}")
                        raise HTTPException(
                            status_code=500,
                            detail="Error setting up file stream"
                        )
                    finally:
                        buffer.close()
                
                # Get file metadata to set correct content type
                try:
                    file_metadata = drive_service.files().get(
                        fileId=file_id, 
                        fields='mimeType'
                    ).execute()
                    mime_type = file_metadata.get('mimeType', 'application/octet-stream')
                except Exception as e:
                    logger.warning(f"Could not get file mime type: {str(e)}")
                    mime_type = 'application/octet-stream'
                
                # Return streaming response with proper headers
                headers = {
                    'Content-Disposition': f'attachment; filename="{file_name}"'
                }
                
                return StreamingResponse(
                    file_stream(),
                    media_type=mime_type,
                    headers=headers
                )
                # Google download chunk size
                # streaming response chunk size
                # download and stream time
                # direct stream while download.
                

            elif connector == Connectors.GOOGLE_MAIL.value:
                logger.info(f"Handling Gmail request for record_id: {record_id}, type: {recordType}")
                gmail_service = build('gmail', 'v1', credentials=creds)
                
                if recordType == RecordTypes.MAIL.value:
                    try:
                        # Fetch the full message from Gmail
                        message = gmail_service.users().messages().get(
                            userId='me',
                            id=file_id,
                            format='full'
                        ).execute()

                        def extract_body(payload):
                            # If there are no parts, return the direct body data
                            if 'parts' not in payload:
                                return payload.get('body', {}).get('data', '')
                            
                            # Search for a text/html part that isn't an attachment (empty filename)
                            for part in payload.get('parts', []):
                                if part.get('mimeType') == 'text/html' and part.get('filename', '') == '':
                                    content = part.get('body', {}).get('data', '')
                                    return content

                            # Fallback: if no html text, try to use text/plain
                            for part in payload.get('parts', []):
                                if part.get('mimeType') == 'text/plain' and part.get('filename', '') == '':
                                    content = part.get('body', {}).get('data', '')
                                    return content
                            return ''

                        # Extract the encoded body content
                        mail_content_base64 = extract_body(message.get('payload', {}))
                        # Decode the Gmail URL-safe base64 encoded content; errors are replaced to avoid issues with malformed text
                        mail_content = base64.urlsafe_b64decode(mail_content_base64.encode('ASCII')).decode('utf-8', errors='replace')

                        # Async generator to stream only the mail content
                        async def message_stream():
                            yield mail_content.encode('utf-8')

                        # Return the streaming response with only the mail body
                        return StreamingResponse(
                            message_stream(),
                            media_type='text/plain'
                        )
                    except Exception as mail_error:
                        logger.error(f"Failed to fetch mail content: {str(mail_error)}")
                        raise HTTPException(
                            status_code=500,
                            detail="Failed to fetch mail content"
                        )
                
                # Handle attachment download
                logger.info(f"Downloading Gmail attachment for record_id: {record_id}")
                
                # Get file metadata first
                file = await arango_service.get_document(record_id, CollectionNames.FILES.value)
                if not file:
                    raise HTTPException(status_code=404, detail="File not found")
                
                file_name = file.get('name', '')
                
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
                        message_id = message['messageId']
                        logger.info(f"Found message ID: {message_id}")
                    else:
                        raise Exception("Related message not found")

                    attachment = gmail_service.users().messages().attachments().get(
                        userId='me',
                        messageId=message_id,
                        id=file_id
                    ).execute()
                    
                    file_data = base64.urlsafe_b64decode(attachment['data'])
                    
                    if convertTo == 'pdf':
                        with tempfile.TemporaryDirectory() as temp_dir:
                            temp_file_path = os.path.join(temp_dir, file_name)
                            
                            # Write attachment data to temp file
                            with open(temp_file_path, 'wb') as f:
                                f.write(file_data)
                            
                            # Convert to PDF
                            pdf_path = await convert_to_pdf(temp_file_path, temp_dir)
                            return StreamingResponse(
                                open(pdf_path, 'rb'),
                                media_type='application/pdf',
                                headers={
                                    'Content-Disposition': f'inline; filename="{Path(file_name).stem}.pdf"'
                                }
                            )
                    
                    # Return original file if no conversion requested
                    return StreamingResponse(
                        iter([file_data]),
                        media_type='application/octet-stream'
                    )
                    
                except Exception as gmail_error:
                    logger.info(f"Failed to get attachment from Gmail: {str(gmail_error)}, trying Drive...")
                    
                    # Try Drive as fallback
                    try:
                        drive_service = build('drive', 'v3', credentials=creds)
                        
                        if convertTo == 'pdf':
                            with tempfile.TemporaryDirectory() as temp_dir:
                                temp_file_path = os.path.join(temp_dir, file_name)
                                
                                # Download from Drive to temp file
                                with open(temp_file_path, 'wb') as f:
                                    request = drive_service.files().get_media(fileId=file_id)
                                    downloader = MediaIoBaseDownload(f, request)
                                    
                                    done = False
                                    while not done:
                                        status, done = downloader.next_chunk()
                                        logger.info(f"Download {int(status.progress() * 100)}%.")
                                
                                # Convert to PDF
                                pdf_path = await convert_to_pdf(temp_file_path, temp_dir)
                                return StreamingResponse(
                                    open(pdf_path, 'rb'),
                                    media_type='application/pdf',
                                    headers={
                                        'Content-Disposition': f'inline; filename="{Path(file_name).stem}.pdf"'
                                    }
                                )
                        
                        # Regular file download with streaming
                        # Get file metadata to set correct content type
                        try:
                            file_metadata = drive_service.files().get(
                                fileId=file_id, 
                                fields='mimeType'
                            ).execute()
                            mime_type = file_metadata.get('mimeType', 'application/octet-stream')
                        except Exception as e:
                            logger.warning(f"Could not get file mime type: {str(e)}")
                            mime_type = 'application/octet-stream'

                        headers = {
                            'Content-Disposition': f'attachment; filename="{file_name}"'
                        }

                        # Use the same streaming logic as Drive downloads
                        async def file_stream():
                            try:
                                request = drive_service.files().get_media(fileId=file_id)
                                buffer = io.BytesIO()
                                downloader = MediaIoBaseDownload(
                                    buffer,
                                    request
                                )
                                
                                done = False
                                while not done:
                                    try:
                                        status, done = downloader.next_chunk()
                                        if status:
                                            logger.debug(f"Download progress: {int(status.progress() * 100)}%")
                                        
                                        buffer.seek(0)
                                        chunk = buffer.read()
                                        
                                        if chunk:
                                            yield chunk
                                        
                                        buffer.seek(0)
                                        buffer.truncate(0)
                                        
                                        await asyncio.sleep(0)
                                        
                                    except Exception as chunk_error:
                                        logger.error(f"Error streaming chunk: {str(chunk_error)}")
                                        raise HTTPException(
                                            status_code=500,
                                            detail="Error during file streaming"
                                        )
                                    
                            except Exception as stream_error:
                                logger.error(f"Error in file stream: {str(stream_error)}")
                                raise HTTPException(
                                    status_code=500,
                                    detail="Error setting up file stream"
                                )
                            finally:
                                buffer.close()

                        return StreamingResponse(
                            file_stream(),
                            media_type=mime_type,
                            headers=headers
                        )
                        
                    except Exception as drive_error:
                        logger.error(f"Failed to get file from both Gmail and Drive. Gmail error: {str(gmail_error)}, Drive error: {str(drive_error)}")
                        raise HTTPException(
                            status_code=500,
                            detail="Failed to download file from both Gmail and Drive"
                        )

            else:
                raise HTTPException(status_code=400, detail="Invalid connector type")

        except Exception as e:
            logger.error(f"Error downloading file: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Error downloading file: {str(e)}"
            )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("Error downloading file: %s", str(e))
        raise HTTPException(status_code=500, detail="Error downloading file")

@router.get("/api/v1/stream/kb/{buffer}")
async def get_record_stream(request: Request, buffer: str):
    source = request.query_params.get('source')
    convertTo = request.query_params.get('convertTo')
    file_name = str(uuid.uuid4())
    
    if convertTo == 'pdf':
        with tempfile.TemporaryDirectory() as temp_dir:
            # Write the buffer content to a temporary file
            temp_file_path = os.path.join(temp_dir, f"{file_name}.{source}")
            with open(temp_file_path, 'wb') as f:
                f.write(buffer.encode())
            
            # Convert to PDF
            pdf_path = await convert_to_pdf(temp_file_path, temp_dir)
            
            # Read the PDF file and return it as a streaming response
            return StreamingResponse(
                open(pdf_path, 'rb'),
                media_type='application/pdf',
                headers={
                    'Content-Disposition': f'inline; filename="{file_name}.pdf"'
                }
            )
    
    raise HTTPException(
        status_code=400,
        detail="Invalid conversion request"
    )

async def get_admin_webhook_handler(request: Request) -> Optional[Any]:
    try:
        container: AppContainer = request.app.container
        admin_webhook_handler = container.admin_webhook_handler()
        return admin_webhook_handler
    except Exception as e:
        logger.warning(f"Failed to get admin webhook handler: {str(e)}")
        return None

@router.post("/admin/webhook")
@inject
async def handle_admin_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """Handle incoming webhook notifications from Google Workspace Admin"""
    try:
        verifier = WebhookAuthVerifier(logger)
        if not await verifier.verify_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized webhook request")

        admin_webhook_handler = await get_admin_webhook_handler(request)
        
        if admin_webhook_handler is None:
            logger.warning("Admin webhook handler not yet initialized - skipping webhook processing")
            return {"status": "skipped", "message": "Webhook handler not yet initialized"}

        # Log incoming request details
        body = await request.json()
        logger.info("📥 Incoming admin webhook request: %s", body)

        # Get the event type from the events array
        events = body.get('events', [])
        if not events:
            raise HTTPException(status_code=400, detail="No events found in webhook body")
        
        event_type = events[0].get('name')  # We'll process the first event
        if not event_type:
            raise HTTPException(status_code=400, detail="Missing event name in webhook body")

        # Process notification in background
        background_tasks.add_task(
            admin_webhook_handler.process_notification,
            event_type,
            body
        )
        return {"status": "accepted"}

    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in webhook body: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON format: {str(e)}"
        )
    except Exception as e:
        logger.error("Error processing webhook: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

async def convert_to_pdf(file_path: str, temp_dir: str) -> str:
    """Helper function to convert file to PDF"""
    pdf_path = os.path.join(temp_dir, f"{Path(file_path).stem}.pdf")
    
    try:
        conversion_cmd = [
            'soffice',
            '--headless',
            '--convert-to', 'pdf',
            '--outdir', temp_dir,
            file_path
        ]
        process = await asyncio.create_subprocess_exec(
            *conversion_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"LibreOffice conversion failed: {stderr.decode()}")
            raise HTTPException(
                status_code=500,
                detail="Failed to convert file to PDF"
            )
        
        if os.path.exists(pdf_path):
            return pdf_path
        else:
            raise HTTPException(
                status_code=500,
                detail="PDF conversion failed"
            )
    except Exception as conv_error:
        logger.error(f"Error during conversion: {str(conv_error)}")
        raise HTTPException(
            status_code=500,
            detail="Error converting file to PDF"
        )
