from fastapi import APIRouter
import asyncio
import base64
import json
import jwt
from google.oauth2 import service_account
from datetime import datetime, timezone, timedelta
from dependency_injector.wiring import inject, Provide
from fastapi import Request, Depends, HTTPException, BackgroundTasks, status
from app.setups.connector_setup import AppContainer
from app.utils.logger import logger
from fastapi.responses import StreamingResponse
import os
import aiohttp
from app.core.signed_url import TokenPayload
from app.config.configuration_service import Routes, TokenScopes, config_node_constants
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
        
        verifier = WebhookAuthVerifier()
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
        logger.info("Received message: %s", message)
        if not message:
            logger.warning("No message found in webhook body")
            return {"status": "error", "message": "No message found"}

        # Decode the message data
        data = message.get("data", "")
        if data:
            try:
                decoded_data = base64.b64decode(data).decode("utf-8")
                notification = json.loads(decoded_data)
                logger.info("Received notification: %s", notification)

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


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat()
    }


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


@router.get("/drive/{org_id}/sync/downtime")
@inject
async def downtime_handling(
    org_id,
    webhook_handler=Depends(Provide[AppContainer.drive_webhook_handler])
):
    """Get sync downtime"""
    try:
        downtime = await webhook_handler.handle_downtime(org_id)
        return {
            "status": "success",
            "downtime": downtime
        }
    except Exception as e:
        logger.error("Error getting sync downtime: %s", str(e))
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


@router.get("/gmail/{org_id}/sync/downtime")
@inject
async def downtime_handling(
    org_id,
    webhook_handler=Depends(Provide[AppContainer.gmail_webhook_handler])
):
    """Get sync downtime"""
    try:
        downtime = await webhook_handler.handle_downtime(org_id)
        return {
            "status": "success",
            "downtime": downtime
        }
    except Exception as e:
        logger.error("Error getting sync downtime: %s", str(e))
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
        logger.error(f"Error getting signed URL: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/index/{org_id}/{connector}/record/{record_id}")
@inject
async def download_file(
    org_id: str,
    record_id: str,
    connector: str,
    token: str,
    config_service=Depends(Provide[AppContainer.config_service]),
    arango_service=Depends(Provide[AppContainer.arango_service]),
    signed_url_handler=Depends(Provide[AppContainer.signed_url_handler]),
):
    
    async def get_service_account_credentials(user_id):
        """Helper function to get service account credentials"""
        try:
            # Load service account credentials from environment or secure storage
            SCOPES = GOOGLE_CONNECTOR_ENTERPRISE_SCOPES
            
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
            
            nodejs_config = await config_service.get_config(config_node_constants.NODEJS.value)
            nodejs_endpoint = nodejs_config.get('common', {}).get('endpoint')
            
            user = await arango_service.get_user_by_user_id(user_id)
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
                    logger.info(f"🚀 Credentials JSON: {credentials_json}")

            credentials = service_account.Credentials.from_service_account_info(
                credentials_json,
                scopes=SCOPES
            )
            
            # # Get the user email from the record to impersonate
            credentials = credentials.with_subject(user['email'])
            return credentials
        
        except Exception as e:
            logger.error(f"Error getting service account credentials: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Error accessing service account credentials"
            )
            
    async def get_user_credentials(user_id):
        """Helper function to get user credentials"""
        try:
            SCOPES = GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES

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

            headers = {
                "Authorization": f"Bearer {jwt_token}"
            }
            
            nodejs_config = await config_service.get_config(config_node_constants.NODEJS.value)
            nodejs_endpoint = nodejs_config.get('common', {}).get('endpoint')

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
            creds = await get_user_credentials(user_id)

        # Download file based on connector type
        try:
            if connector == "drive":
                logger.info(f"Downloading Drive file: {file_id}")
                # Build the Drive service
                drive_service = build('drive', 'v3', credentials=creds)
                
                # Create a BytesIO object to store the file content
                file_buffer = io.BytesIO()
                
                # Get the file metadata first to get mimeType
                # file_metadata = drive_service.files().get(fileId=file_id).execute()
                
                # Download the file
                request = drive_service.files().get_media(fileId=file_id)
                downloader = MediaIoBaseDownload(file_buffer, request)
                
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                
                # Reset buffer position to start
                file_buffer.seek(0)
                
                # Stream the response
                return StreamingResponse(
                    file_buffer,
                    media_type='application/octet-stream'
                )

            elif connector == "gmail":
                logger.info(f"Downloading Gmail attachment for record_id: {record_id}")
                gmail_service = build('gmail', 'v1', credentials=creds)
                
                # Get the related message's externalRecordId using AQL
                aql_query = f"""
                FOR v, e IN 1..1 OUTBOUND '{CollectionNames.RECORDS.value}/{record_id}' {CollectionNames.RECORD_RELATIONS.value}
                    FILTER e.relationType == '{RecordRelations.ATTACHMENT.value}'
                    RETURN {{
                        messageId: v.externalRecordId,
                        _key: v._key,
                        relationType: e.relationType
                    }}
                """

                cursor = arango_service.db.aql.execute(aql_query)
                messages = list(cursor)
                logger.info(f"🚀 Query results: {messages}")

                if not messages or not messages[0]:
                    # If no results found, try the reverse direction
                    logger.info("No results found with OUTBOUND, trying INBOUND...")
                    aql_query = f"""
                    FOR v, e IN 1..1 INBOUND '{CollectionNames.RECORDS.value}/{record_id}' {CollectionNames.RECORD_RELATIONS.value}
                        FILTER e.relationType == '{RecordRelations.ATTACHMENT.value}'
                        RETURN {{
                            messageId: v.externalRecordId,
                            _key: v._key,
                            relationType: e.relationType
                        }}
                    """
                    cursor = arango_service.db.aql.execute(aql_query)
                    messages = list(cursor)
                    logger.info(f"🚀 Query results: {messages}")

                if not messages or not messages[0]:
                    record_details = await arango_service.get_document(record_id, CollectionNames.RECORDS.value)
                    logger.error(f"Record details: {record_details}")
                    raise HTTPException(status_code=404, detail="Related message not found")

                message = messages[0]
                message_id = message['messageId']
                logger.info(f"Found message ID: {message_id}")

                # First try getting the attachment from Gmail
                try:
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
                    logger.warning(f"Failed to get attachment from Gmail: {str(gmail_error)}, trying Drive...")
                    
                    # Try to get the file from Drive as fallback
                    try:
                        drive_service = build('drive', 'v3', credentials=creds)
                        file_buffer = io.BytesIO()
                        
                        request = drive_service.files().get_media(fileId=file_id)
                        downloader = MediaIoBaseDownload(file_buffer, request)
                        
                        done = False
                        while not done:
                            _, done = downloader.next_chunk()
                        
                        file_buffer.seek(0)
                        
                        return StreamingResponse(
                            file_buffer,
                            media_type='application/octet-stream'
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
    

@router.get("/api/v1/stream/record/{record_id}")
@inject
async def stream_record(
    request: Request,
    record_id: str,
    config_service=Depends(Provide[AppContainer.config_service]),
    arango_service=Depends(Provide[AppContainer.arango_service]),
):
    async def get_service_account_credentials(user_id):
        """Helper function to get service account credentials"""
        try:
            # Load service account credentials from environment or secure storage
            SCOPES = GOOGLE_CONNECTOR_ENTERPRISE_SCOPES
            
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
            
            nodejs_config = await config_service.get_config(config_node_constants.NODEJS.value)
            nodejs_endpoint = nodejs_config.get('common', {}).get('endpoint')

            user = await arango_service.get_user_by_user_id(user_id)
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
                    logger.info(f"🚀 Credentials JSON: {credentials_json}")

            credentials = service_account.Credentials.from_service_account_info(
                credentials_json,
                scopes=SCOPES
            )

            # # Get the user email from the record to impersonate
            credentials = credentials.with_subject(user['email'])
            logger.info(f"Credentials: {credentials}")
            return credentials
        
        except Exception as e:
            logger.error(f"Error getting service account credentials: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Error accessing service account credentials"
            )

    async def get_user_credentials(user_id):
        """Helper function to get user credentials"""
        try:
            SCOPES = GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES

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
            headers = {
                "Authorization": f"Bearer {jwt_token}"
            }
            
            nodejs_config = await config_service.get_config(config_node_constants.NODEJS.value)
            nodejs_endpoint = nodejs_config.get('common', {}).get('endpoint')

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
                    print("creds_data", creds_data)

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
            logger.info(f"Auth header: {auth_header}")
            if not auth_header or not auth_header.startswith("Bearer "):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing or invalid Authorization header"
                )
            # Extract the token
            token = auth_header.split(" ")[1]
            logger.info(f"Token: {token}")
            payload = jwt.decode(
                token,
                os.getenv('JWT_SECRET'),
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
            creds = await get_user_credentials(user_id)

        # Download file based on connector type
        try:
            if connector == Connectors.GOOGLE_DRIVE.value:
                logger.info(f"Downloading Drive file: {file_id}")
                # Build the Drive service
                drive_service = build('drive', 'v3', credentials=creds)
                
                # Create a BytesIO object to store the file content
                file_buffer = io.BytesIO()
                
                # Download the file
                request = drive_service.files().get_media(fileId=file_id)
                downloader = MediaIoBaseDownload(file_buffer, request)
                
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                
                # Reset buffer position to start
                file_buffer.seek(0)
                
                # Stream the response
                return StreamingResponse(
                    file_buffer,
                    media_type='application/octet-stream'
                )

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
                        import base64
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
                
                # Handle attachment download (existing logic)
                logger.info(f"Downloading Gmail attachment for record_id: {record_id}")
                
                # Get the related message's externalRecordId using AQL
                aql_query = f"""
                FOR v, e IN 1..1 OUTBOUND '{CollectionNames.RECORDS.value}/{record_id}' {CollectionNames.RECORD_RELATIONS.value}
                    FILTER e.relationType == '{RecordRelations.ATTACHMENT.value}'
                    RETURN {{
                        messageId: v.externalRecordId,
                        _key: v._key,
                        relationType: e.relationType
                    }}
                """

                cursor = arango_service.db.aql.execute(aql_query)
                messages = list(cursor)
                logger.info(f"🚀 Query results: {messages}")

                if not messages or not messages[0]:
                    # If no results found, try the reverse direction
                    logger.info("No results found with OUTBOUND, trying INBOUND...")
                    aql_query = f"""
                    FOR v, e IN 1..1 INBOUND '{CollectionNames.RECORDS.value}/{record_id}' {CollectionNames.RECORD_RELATIONS.value}
                        FILTER e.relationType == '{RecordRelations.ATTACHMENT.value}'
                        RETURN {{
                            messageId: v.externalRecordId,
                            _key: v._key,
                            relationType: e.relationType
                        }}
                    """
                    cursor = arango_service.db.aql.execute(aql_query)
                    messages = list(cursor)
                    logger.info(f"🚀 Query results: {messages}")

                if not messages or not messages[0]:
                    record_details = await arango_service.get_document(record_id, CollectionNames.RECORDS.value)
                    logger.error(f"Record details: {record_details}")
                    raise HTTPException(status_code=404, detail="Related message not found")

                message = messages[0]
                message_id = message['messageId']
                logger.info(f"Found message ID: {message_id}")

                # First try getting the attachment from Gmail
                try:
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
                    logger.warning(f"Failed to get attachment from Gmail: {str(gmail_error)}, trying Drive...")
                    
                    # Try to get the file from Drive as fallback
                    try:
                        drive_service = build('drive', 'v3', credentials=creds)
                        file_buffer = io.BytesIO()

                        request = drive_service.files().get_media(fileId=file_id)
                        downloader = MediaIoBaseDownload(file_buffer, request)

                        done = False
                        while not done:
                            _, done = downloader.next_chunk()

                        file_buffer.seek(0)

                        return StreamingResponse(
                            file_buffer,
                            media_type='application/octet-stream'
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
