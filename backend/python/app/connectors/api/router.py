import asyncio
import base64
import io
import json
import mimetypes
import os
import tempfile
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Union
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from dependency_injector.wiring import Provide, inject
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import Response, StreamingResponse
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from jose import JWTError
from pydantic import BaseModel, ValidationError

from app.api.middlewares.auth import require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    EventTypes,
    MimeTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import (
    DefaultEndpoints,
    OAuthScopes,
    config_node_constants,
)
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.token_service.oauth_service import (
    OAuthProvider,
    OAuthToken,
)
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.sync.task_manager import sync_task_manager
from app.connectors.services.kafka_service import KafkaService
from app.containers.connector import ConnectorAppContainer
from app.models.entities import Record
from app.services.featureflag.config.config import CONFIG
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.api_call import make_api_call
from app.utils.jwt import generate_jwt
from app.utils.logger import create_logger
from app.utils.oauth_config import get_oauth_config
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = create_logger("connector_service")

router = APIRouter()


def get_mime_type_from_record(record: Record) -> str:
    """
    Get the MIME type for a record, using the following priority:
    1. Record's stored mime_type attribute
    2. Guess from record_name or name extension using mimetypes module
    3. Fallback to application/octet-stream

    Args:
        record: The record object
    Returns:
        str: The MIME type
    """
    # Try to get mime_type from record
    if hasattr(record, 'mime_type') and record.mime_type:
        return record.mime_type

    # Try to guess from record_name or name attribute
    record_name = getattr(record, 'record_name', None) or getattr(record, 'name', None)
    if record_name:
        guessed_type, _ = mimetypes.guess_type(record_name)
        if guessed_type:
            return guessed_type

    # Fallback to octet-stream
    return "application/octet-stream"


async def _stream_google_api_request(request, error_context: str = "download") -> AsyncGenerator[bytes, None]:
    """
    Helper function to stream data from a Google API request using MediaIoBaseDownload.

    Args:
        request: Google API request object (from files().get_media() or files().export_media())
        error_context: Context string for error messages (e.g., "PDF export", "file export")
    Yields:
        bytes: Chunks of data from the download
    """
    buffer = io.BytesIO()
    try:
        downloader = MediaIoBaseDownload(buffer, request)
        done = False

        while not done:
            try:
                _, done = downloader.next_chunk()

                buffer.seek(0)
                chunk = buffer.read()

                if chunk:  # Only yield if we have data
                    yield chunk

                # Clear buffer for next chunk
                buffer.seek(0)
                buffer.truncate(0)

                # Yield control back to event loop
                await asyncio.sleep(0)

            except HttpError as http_error:
                logger.error(f"HTTP error during {error_context}: {str(http_error)}")
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail=f"Error during {error_context}: {str(http_error)}",
                )
            except Exception as chunk_error:
                logger.error(f"Error during {error_context} chunk: {str(chunk_error)}")
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail=f"Error during {error_context}",
                )
    except Exception as stream_error:
        logger.error(f"Error in {error_context} stream: {str(stream_error)}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Error setting up {error_context} stream",
        )
    finally:
        buffer.close()


class ReindexFailedRequest(BaseModel):
    connector: str  # GOOGLE_DRIVE, GOOGLE_MAIL, KNOWLEDGE_BASE
    origin: str     # CONNECTOR, UPLOAD


async def get_validated_connector_instance(
    connector_id: str,
    request: Request,
) -> Dict[str, Any]:
    """
    FastAPI dependency to validate user authentication, retrieve connector instance,
    check beta access, and verify permissions.

    This dependency centralizes common validation logic used across multiple
    connector instance update endpoints to reduce code duplication.

    Args:
        connector_id: Unique connector instance key
        request: FastAPI request object

    Returns:
        Dictionary containing the validated connector instance

    Raises:
        HTTPException: 401 if not authenticated, 404 if instance not found,
                      403 for permission violations or beta access issues
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry

    # Extract user information
    user_id = request.state.user.get("userId")
    org_id = request.state.user.get("orgId")
    is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"

    # Validate authentication
    if not user_id or not org_id:
        logger.error(f"User not authenticated: {user_id} {org_id}")
        raise HTTPException(
            status_code=HttpStatusCode.UNAUTHORIZED.value,
            detail="User not authenticated"
        )

    # Retrieve connector instance
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

    # Check beta connector access
    connector_type = instance.get("type", "")
    await check_beta_connector_access(connector_type, request)

    # Validate permissions
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

    return instance


async def get_graph_provider(request: Request) -> IGraphDBProvider:
    """Return graph DB provider from app state (set at startup)."""
    return request.app.state.graph_provider

async def get_kafka_service(request: Request) -> KafkaService:
    container: ConnectorAppContainer = request.app.container
    kafka_service = container.kafka_service()
    return kafka_service

def _parse_comma_separated_str(value: Optional[str]) -> Optional[List[str]]:
    """Parses a comma-separated string into a list of strings, filtering out empty items."""
    if not value:
        return None
    return [item.strip() for item in value.split(',') if item.strip()]

def _sanitize_app_name(app_name: str) -> str:
    return app_name.replace(" ", "").lower()


def _trim_config_values(
    obj: Union[str, int, float, bool, None, List[Any], Dict[str, Any]],
    path: str = ""
) -> Union[str, int, float, bool, None, List[Any], Dict[str, Any]]:
    """
    Recursively trims leading and trailing whitespace from string values in a configuration object.
    Skips certain fields that may contain intentional whitespace (like certificates, keys, etc.)

    Only trims string values. Preserves:
    - Booleans (True/False)
    - Numbers (int, float)
    - Date/datetime objects
    - Other non-string types

    Args:
        obj: The object to trim (can be str, int, float, bool, None, list, or dict)
        path: Current path in the object (for tracking nested fields)

    Returns:
        A new object with trimmed string values (same type as input)
    """
    if obj is None:
        return obj

    # Fields that should NOT be trimmed (they may contain intentional whitespace)
    skip_trim_fields = {
        'certificate', 'privatekey', 'private_key', 'credentials', 'oauth',
        'json', 'jsondata', 'client_secret', 'clientsecret', 'secret',
        'token', 'accesstoken', 'refreshtoken'
    }

    # If it's a string, trim it (unless it's in a skip list)
    if isinstance(obj, str):
        # Check if current field name should be skipped
        field_name = path.split('.')[-1] if '.' in path else path
        if field_name.lower() in skip_trim_fields:
            return obj
        return obj.strip()

    # If it's a list, recursively trim each element
    if isinstance(obj, list):
        return [_trim_config_values(item, f"{path}[{i}]") for i, item in enumerate(obj)]

    # If it's a dict, recursively trim each property
    if isinstance(obj, dict):
        trimmed = {}
        for key, value in obj.items():
            new_path = f"{path}.{key}" if path else key
            trimmed[key] = _trim_config_values(value, new_path)
        return trimmed

    # Preserve all other types as-is:
    # - Booleans (isinstance(obj, bool))
    # - Numbers (isinstance(obj, (int, float)))
    # - Date/datetime objects (isinstance(obj, (datetime, date)))
    # - Other types
    return obj


def _trim_connector_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Trims whitespace from connector configuration before saving.
    This ensures consistent data without leading/trailing spaces.

    Args:
        config: The configuration dictionary to trim

    Returns:
        A new configuration dictionary with trimmed values
    """
    if not config or not isinstance(config, dict):
        return config

    trimmed_config = config.copy()

    for section in ["auth", "sync", "filters"]:
        if section in trimmed_config and isinstance(trimmed_config[section], dict):
            trimmed_config[section] = _trim_config_values(trimmed_config[section], section)

    return trimmed_config

@router.get("/api/v1/{org_id}/{user_id}/{connector}/record/{record_id}/signedUrl", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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

@router.delete("/api/v1/delete/record/{record_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))])
@inject
async def handle_record_deletion(
    record_id: str, graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> Optional[dict]:
    try:
        response = await graph_provider.delete_records_and_relations(
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
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
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
        org_task = graph_provider.get_document(org_id, CollectionNames.ORGS.value)
        record_task = graph_provider.get_record_by_id(
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
            if isinstance(response["data"], dict):
                data = response['data'].get('data')
                buffer = bytes(data) if isinstance(data, list) else data
            else:
                buffer = response['data']

            # Get the correct MIME type from the record
            mime_type = get_mime_type_from_record(record)
            return Response(content=buffer or b'', media_type=mime_type)

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
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> Optional[dict | StreamingResponse]:
    try:
        logger.info(f"Downloading file {record_id} with connector {connector}")
        # Verify signed URL using the handler

        payload = signed_url_handler.validate_token(token)
        user_id = payload.user_id

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
        org = await graph_provider.get_document(org_id, CollectionNames.ORGS.value)
        if not org:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Organization not found")

        # Get record details
        record = await graph_provider.get_record_by_id(
            record_id
        )
        if not record:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Record not found")

        connector_id = record.connector_id
        # Get connector instance to check scope
        connector_instance = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)
        connector_type = connector_instance.get("type", None)
        if connector_type is None:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Connector not found")

        # Handle KB separately - fetch from storage service
        container: ConnectorAppContainer = request.app.container
        try:
            connector_obj: BaseConnector = container.connectors_map[connector_id]
            if not connector_obj:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail=f"Connector '{connector_id}' not found"
                )

            if connector_obj.get_app_name() == Connectors.GOOGLE_DRIVE_WORKSPACE or connector_obj.get_app_name() == Connectors.GOOGLE_MAIL_WORKSPACE:
                buffer = await connector_obj.stream_record(record, user_id)
            else:
                buffer = await connector_obj.stream_record(record)

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


@router.get("/api/v1/stream/record/{record_id}", response_model=None, dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def stream_record(
    request: Request,
    record_id: str,
    convertTo: str = Query(None, description="Convert file to this format"),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Optional[dict | StreamingResponse]:
    """
    Stream a record to the client.
    """
    try:
        try:
            logger.info(f"Stream Record Start: {time.time()}")
            logger.info(f"Convert To: {convertTo}")
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

        org_task = graph_provider.get_document(org_id, CollectionNames.ORGS.value)
        record_task = graph_provider.get_record_by_id(
            record_id
        )
        org, record = await asyncio.gather(org_task, record_task)
        if not org:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Organization not found")
        if not record:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Record not found")

        # Validate that the org_id matches the record's org_id
        if record and record.org_id and record.org_id != org_id:
            logger.warning(f"OrgId mismatch: JWT has {org_id}, but record has {record.org_id}. Using record's org_id.")
            org_id = record.org_id
            org = await graph_provider.get_document(org_id, CollectionNames.ORGS.value)
            if not org:
                raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Organization not found")

        # Permission check: Verify user has access to this record
        # This handles both KB-level and direct record permissions
        access_check = await graph_provider.check_record_access_with_details(user_id, org_id, record_id)
        if not access_check:
            logger.warning(f"User {user_id} does not have access to record {record_id}")
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="You do not have permission to access this record"
            )

        connector_name = record.connector_name.value.lower().replace(" ", "")
        connector_id = record.connector_id
        logger.info(f"Connector: {connector_name} connector_id: {connector_id}")
        # Different auth handling based on account type and connector scope

        container: ConnectorAppContainer = request.app.container

        try:
            logger.info("Stream Record called at router")
            logger.info(f"Connector: {connector_name} connector_id: {connector_id}")
            connector_obj: BaseConnector = container.connectors_map[connector_id]
            if not connector_obj:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail=f"Connector '{connector_id}' not found"
                )

            # Get the buffer from connector (without passing convertTo)
            if connector_obj.get_app_name() == Connectors.GOOGLE_DRIVE_WORKSPACE or connector_obj.get_app_name() == Connectors.GOOGLE_MAIL_WORKSPACE:
                buffer = await connector_obj.stream_record(record, user_id)
            else:
                buffer = await connector_obj.stream_record(record)

            # Handle conversion after getting the buffer
            if convertTo == MimeTypes.PDF.value:
                mime_type = get_mime_type_from_record(record)
                record_name = getattr(record, 'record_name', None) or getattr(record, 'name', None) or 'file'

                file_extension = None
                if record_name and '.' in record_name:
                    file_extension = record_name.split('.')[-1].lower()

                # Check if this file type needs conversion (PPT, PPTX, Google Slides)
                needs_conversion = (
                    file_extension in ['ppt', 'pptx'] or
                    mime_type in [
                        MimeTypes.PPT.value,
                        MimeTypes.PPTX.value,
                        MimeTypes.GOOGLE_SLIDES.value
                    ]
                )

                if needs_conversion:
                    try:
                        return await convert_buffer_to_pdf_stream(buffer, record_name, file_extension)
                    except HTTPException:
                        raise
                    except Exception as e:
                        logger.error(f"Error converting file to PDF: {str(e)}", exc_info=True)
                        raise HTTPException(
                            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                            detail="Failed to convert file to PDF"
                        )

            return buffer
        except Exception as e:
            logger.error(f"Error downloading file: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail=f"Error downloading file: {str(e)}"
            )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("Error downloading file: %s", str(e))
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Error downloading file")


@router.post("/api/v1/record/buffer/convert", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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

                    return create_stream_record_response(
                        file_iterator(),
                        filename=pdf_filename,
                        mime_type="application/pdf",
                        fallback_filename="converted_file.pdf"
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

async def convert_to_pdf(file_path: str, temp_dir: str) -> str:
    """
    Convert a file to PDF using LibreOffice.

    Args:
        file_path: Path to the input file to convert
        temp_dir: Temporary directory where the PDF will be created

    Returns:
        Path to the converted PDF file

    Raises:
        HTTPException: If conversion fails or output file is not found
    """
    pdf_path = os.path.join(temp_dir, f"{Path(file_path).stem}.pdf")

    conversion_cmd = [
        "soffice",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        temp_dir,
        file_path,
    ]

    try:
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
            logger.error("PDF conversion timed out")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="PDF conversion timed out"
            )

        if process.returncode != 0:
            error_msg = conversion_error.decode('utf-8', errors='replace')
            logger.error(f"PDF conversion failed: {error_msg}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to convert file to PDF"
            )

        if not os.path.exists(pdf_path):
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="PDF conversion failed - output file not found"
            )

        return pdf_path

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during PDF conversion: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Error converting file to PDF"
        )


async def convert_buffer_to_pdf_stream(
    buffer: Union[StreamingResponse, Response, bytes, io.IOBase],
    record_name: str,
    file_extension: Optional[str] = None
) -> StreamingResponse:
    """
    Convert a file buffer to PDF and return as a streaming response.

    Args:
        buffer: The file buffer (StreamingResponse, Response, bytes, or file-like object)
        record_name: Name of the record/file
        file_extension: Optional file extension

    Returns:
        StreamingResponse containing the converted PDF

    Raises:
        HTTPException: If conversion fails
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_file_name = record_name if record_name else f"file.{file_extension or 'tmp'}"
        temp_file_path = os.path.join(temp_dir, temp_file_name)

        # Write buffer content to temporary file
        with open(temp_file_path, "wb") as f:
            if isinstance(buffer, StreamingResponse):
                async for chunk in buffer.body_iterator:
                    await asyncio.to_thread(f.write, chunk)
            elif isinstance(buffer, Response):
                body_content = buffer.body
                if not body_content:
                    raise HTTPException(
                        status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                        detail="Response object has no body content"
                    )
                if callable(body_content):
                    body_content = body_content()
                if not isinstance(body_content, bytes):
                    raise HTTPException(
                        status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                        detail=f"Response body is not bytes: {type(body_content)}"
                    )
                await asyncio.to_thread(f.write, body_content)
            elif hasattr(buffer, 'read'):
                while True:
                    chunk = await asyncio.to_thread(buffer.read, 8192)
                    if not chunk:
                        break
                    await asyncio.to_thread(f.write, chunk)
            elif isinstance(buffer, bytes):
                await asyncio.to_thread(f.write, buffer)
            else:
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail=f"Unsupported buffer type for conversion: {type(buffer)}"
                )

        # Convert to PDF
        pdf_path = await convert_to_pdf(temp_file_path, temp_dir)

        # Handle case where LibreOffice may have renamed the file
        if not os.path.exists(pdf_path):
            pdf_files = [f for f in os.listdir(temp_dir) if f.endswith('.pdf')]
            if pdf_files:
                pdf_path = os.path.join(temp_dir, pdf_files[0])
            else:
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail="PDF conversion failed - output file not found"
                )

        # Read PDF into memory before temp directory cleanup
        with open(pdf_path, "rb") as pdf_file:
            pdf_content = await asyncio.to_thread(pdf_file.read)

        # Create streaming iterator
        pdf_filename = f"{Path(record_name).stem}.pdf" if record_name else "converted_file.pdf"

        async def pdf_file_iterator() -> AsyncGenerator[bytes, None]:
            chunk_size = 8192
            for i in range(0, len(pdf_content), chunk_size):
                yield pdf_content[i:i + chunk_size]

        return create_stream_record_response(
            pdf_file_iterator(),
            filename=pdf_filename,
            mime_type="application/pdf",
            fallback_filename="converted_file.pdf"
        )

@router.get("/api/v1/records", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_records(
    request:Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(20, ge=1, le=100, description="Number of items per page"),
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
        user = await graph_provider.get_user_by_user_id(user_id=user_id)

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

        records, total_count, available_filters = await graph_provider.get_records(
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

@router.get("/api/v1/records/{record_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_record_by_id(
    record_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> Optional[Dict]:
    """
    Check if the current user has access to a specific record
    """
    try:
        container = request.app.container
        logger = container.logger()
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")

        has_access = await graph_provider.check_record_access_with_details(
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

@router.delete("/api/v1/records/{record_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))])
@inject
async def delete_record(
    record_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    kafka_service: KafkaService = Depends(get_kafka_service),
) -> Dict:
    """
    Delete a specific record with permission validation
    """
    try:
        container = request.app.container
        logger = container.logger()
        user_id = request.state.user.get("userId")
        logger.info(f"🗑️ Attempting to delete record {record_id}")

        result = await graph_provider.delete_record(
            record_id=record_id,
            user_id=user_id
        )

        if result["success"]:
            # Publish deletion event
            event_data = result.get("eventData")
            if event_data and event_data.get("payload"):
                try:
                    timestamp = get_epoch_timestamp_in_ms()
                    event = {
                        "eventType": event_data["eventType"],
                        "timestamp": timestamp,
                        "payload": event_data["payload"]
                    }
                    await kafka_service.publish_event(event_data["topic"], event)
                    logger.info(f"✅ Published {event_data['eventType']} event for record {record_id}")
                except Exception as e:
                    logger.error(f"❌ Failed to publish deletion event: {str(e)}")

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

@router.post("/api/v1/records/{record_id}/reindex", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_SYNC))])
@inject
async def reindex_single_record(
    record_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    kafka_service: KafkaService = Depends(get_kafka_service),
) -> Dict:
    """
    Reindex a single record with permission validation.

    Request Body (optional):
        depth: int - Depth of children to reindex.
               -1 = unlimited, 0 = only this record (default),
               1 = direct children, 2 = children + grandchildren, etc.
    """
    try:
        container = request.app.container
        logger = container.logger()
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")

        # Parse optional depth from request body (0 = only this record; 100/full from all-records tree)
        depth = 0
        try:
            request_body = await request.json()
            depth = request_body.get("depth", 0)
        except (json.JSONDecodeError, TypeError):
            depth = 0

        logger.info(f"🔄 Attempting to reindex record {record_id} with depth {depth}")

        result = await graph_provider.reindex_single_record(
            record_id=record_id,
            user_id=user_id,
            org_id=org_id,
            depth=depth,
            request=request
        )

        if result["success"]:
            # Publish event in router
            event_data = result.get("eventData")
            if event_data:
                try:
                    timestamp = get_epoch_timestamp_in_ms()
                    event = {
                        "eventType": event_data["eventType"],
                        "timestamp": timestamp,
                        "payload": event_data["payload"]
                    }
                    await kafka_service.publish_event(event_data["topic"], event)
                    logger.info(f"✅ Published {event_data['eventType']} event for record {record_id}")
                except Exception as e:
                    logger.error(f"❌ Failed to publish event: {str(e)}")

            logger.info(f"✅ Successfully initiated reindex for record {record_id} with depth {depth}")
            return {
                "success": True,
                "message": f"Reindex initiated for record {record_id}" + (f" with depth {depth}" if depth != 0 else ""),
                "recordId": result.get("recordId"),
                "recordName": result.get("recordName"),
                "connector": result.get("connector"),
                "eventPublished": event_data is not None,
                "userRole": result.get("userRole"),
                "depth": depth
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

@router.get("/api/v1/stats", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_connector_stats_endpoint(
    request: Request,
    org_id: str,
    connector_id: str,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider)
)-> Dict[str, Any]:
    try:
        result = await graph_provider.get_connector_stats(org_id, connector_id)
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

@router.post("/api/v1/record-groups/{record_group_id}/reindex", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_SYNC))])
@inject
async def reindex_record_group(
    record_group_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    kafka_service: KafkaService = Depends(get_kafka_service),
) -> Dict:
    """
    Reindex all records in a record group up to a specified depth
    """
    try:
        container = request.app.container
        logger = container.logger()
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")

        # Parse optional depth from request body
        depth = 0  # Default to 0 (only direct records)
        try:
            request_body = await request.json()
            depth = request_body.get("depth", 0)
        except json.JSONDecodeError:
            # No body or invalid JSON - use default depth
            depth = 0

        logger.info(f"🔄 Attempting to reindex record group {record_group_id} with depth {depth}")

        # Get record group data and validate permissions (does not publish events)
        result = await graph_provider.reindex_record_group_records(
            record_group_id=record_group_id,
            depth=depth,
            user_id=user_id,
            org_id=org_id
        )

        if not result["success"]:
            logger.error(f"❌ Failed to reindex record group {record_group_id}: {result.get('reason')}")
            raise HTTPException(
                status_code=result.get("code", 500),
                detail=result.get("reason", "Failed to reindex record group")
            )

        # Publish reindex event (router is responsible for event publishing)
        connector_id = result.get("connectorId")
        connector_name = result.get("connectorName")
        user_key = result.get("userKey")
        depth = result.get("depth", depth)

        try:
            connector_normalized = connector_name.replace(" ", "").lower()
            event_type = f"{connector_normalized}.reindex"

            payload = {
                "orgId": org_id,
                "recordGroupId": record_group_id,
                "depth": depth,
                "connectorId": connector_id,
                "userKey": user_key
            }

            # Publish event directly using KafkaService
            timestamp = get_epoch_timestamp_in_ms()
            event = {
                "eventType": event_type,
                "timestamp": timestamp,
                "payload": payload
            }
            await kafka_service.publish_event("sync-events", event)
            logger.info(f"✅ Published {event_type} event for record group {record_group_id}")

            return {
                "success": True,
                "message": f"Reindex initiated for record group {record_group_id} with depth {depth}",
                "recordGroupId": record_group_id,
                "depth": depth,
                "connector": connector_id,
                "eventPublished": True
            }
        except Exception as event_error:
            logger.error(f"❌ Failed to publish reindex event: {str(event_error)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to publish reindex event: {str(event_error)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error reindexing record group {record_group_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while reindexing record group: {str(e)}"
        )

def _validate_connector_deletion_permissions(
    instance: Dict[str, Any],
    user_id: str,
    is_admin: bool,
    logger
) -> None:
    """
    Validate that the user has permission to delete the connector instance.

    Permission rules:
    - Personal connectors: Only the owning user (creator) can delete
    - Team connectors: Only administrators can delete

    Args:
        instance: Connector instance dictionary
        user_id: ID of the user attempting deletion
        is_admin: Whether the user is an administrator
        logger: Logger instance

    Raises:
        HTTPException: 403 if user doesn't have permission to delete
    """
    scope = instance.get("scope")
    created_by = instance.get("createdBy")

    # For team connectors, only admins can delete
    if scope == ConnectorScope.TEAM.value and not is_admin:
        logger.error("Only administrators can delete team connectors")
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value,
            detail="Only administrators can delete team connectors"
        )

    # For personal connectors, only the creator (owning user) can delete
    # Admins cannot delete personal connectors
    if scope == ConnectorScope.PERSONAL.value and created_by != user_id:
        logger.error("Only the creator can delete this personal connector")
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value,
            detail="Only the creator can delete this personal connector"
        )


async def check_beta_connector_access(
    connector_type: str,
    request: Request
) -> None:
    """
    Check if the connector is a beta connector and if beta connectors are enabled.
    Raises HTTPException if beta connectors are disabled and the connector is beta.

    Args:
        connector_type: Type of the connector
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
            normalized_name = connector_type.replace(' ', '').lower()

            if normalized_name in beta_connectors:
                raise HTTPException(
                    status_code=403,
                    detail=f"Beta connectors are not enabled. The connector '{connector_type}' is a beta connector and cannot be accessed. Please enable beta connectors in platform settings to use this connector."
                )
    except HTTPException:
        raise
    except Exception as e:
        # On error, log but don't block access (fail-open for safety)
        container = request.app.container
        container.logger().debug(f"Beta connector check failed: {e}")


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


async def _get_settings_base_path(graph_provider: IGraphDBProvider) -> str:
    """
    Determine frontend settings base path based on organization account type.

    Args:
        graph_provider: Graph DB provider instance

    Returns:
        Settings base path URL
    """
    try:
        organizations = await graph_provider.get_all_documents(
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

@router.get("/api/v1/connectors/registry", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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
    graph_provider = request.app.state.graph_provider

    try:
        # Validate scope
        if scope and scope not in [ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value]:
            logger.error(f"Invalid scope: {scope}")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Invalid scope. Must be 'personal' or 'team'"
            )

        # Get account type to filter beta connectors for enterprise accounts
        account_type = None
        try:
            user = getattr(request.state, 'user', None)
            if user and user.get("orgId"):
                account_type = await graph_provider.get_account_type(user.get("orgId"))
        except Exception as e:
            # If we can't get account type, log but don't fail (fail-open)
            logger.debug(f"Could not get account type: {e}")

        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        result = await connector_registry.get_all_registered_connectors(
            is_admin=is_admin,
            scope=scope,
            page=page,
            limit=limit,
            search=search,
            account_type=account_type
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



@router.get("/api/v1/connectors/", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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


@router.get("/api/v1/connectors/active", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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


@router.get("/api/v1/connectors/inactive", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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


@router.get("/api/v1/connectors/configured", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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

async def _handle_oauth_config_creation(
    connector_type: str,
    auth_config: Dict[str, Any],
    instance_name: str,
    user_id: str,
    org_id: str,
    is_admin: bool,
    config_service: ConfigurationService,
    oauth_config_id: Optional[str],
    auth_type: str,
    base_url: str,
    logger
) -> Optional[str]:
    """
    Handle OAuth config creation or update for a new connector instance.

    Checks for name conflicts BEFORE attempting to create/update OAuth config.

    Args:
        connector_type: Type of connector
        auth_config: Authentication configuration from request
        instance_name: Name of the connector instance
        user_id: User ID
        org_id: Organization ID
        is_admin: Whether user is admin
        config_service: Configuration service instance
        oauth_config_id: Existing OAuth config ID (if updating)
        auth_type: Authentication type (from connector instance)
        base_url: Base URL for OAuth redirects
        logger: Logger instance

    Returns:
        OAuth config ID if created/updated, None otherwise
    """
    # Only handle OAUTH type (not OAUTH_ADMIN_CONSENT or others)
    if auth_type.upper() != "OAUTH":
        logger.debug(f"Skipping OAuth config creation for {connector_type} - authType is {auth_type}, not OAUTH")
        return None

    # Get OAuth app ID (from auth config or parameter)
    oauth_app_id = auth_config.get("oauthConfigId") or oauth_config_id

    # Check if OAuth credential fields are present
    oauth_field_names = _get_oauth_field_names_from_registry(connector_type)
    has_oauth_credentials = any(
        auth_config.get(field_name) or
        auth_config.get(field_name.replace("Id", "_id").replace("Secret", "_secret"))
        for field_name in oauth_field_names
    )

    if not has_oauth_credentials:
        if oauth_app_id:
            logger.info(f"No OAuth credentials provided, using existing OAuth config for {connector_type}")
            return oauth_app_id
        else:
            logger.info(f"No OAuth credentials provided and no oauthConfigId specified for {connector_type} - skipping OAuth config creation")
            logger.debug(f"Expected OAuth fields for {connector_type}: {oauth_field_names}")
            logger.debug(f"Received auth config keys: {list(auth_config.keys())}")
            return None

    # Get existing OAuth configs
    oauth_config_path = _get_oauth_config_path(connector_type)
    existing_oauth_configs = await config_service.get_config(oauth_config_path, default=[])

    if not isinstance(existing_oauth_configs, list):
        existing_oauth_configs = []

    # Determine OAuth instance name
    oauth_instance_name_from_request = auth_config.get("oauthInstanceName", "").strip()

    # If creating new (no oauth_app_id)
    if not oauth_app_id:
        # Use provided name or fall back to connector instance name
        oauth_instance_name = oauth_instance_name_from_request or instance_name
        _check_oauth_name_conflict(existing_oauth_configs, oauth_instance_name, org_id)
        logger.info(f"Creating new OAuth config '{oauth_instance_name}' for {connector_type}")
    # If updating existing (has oauth_app_id)
    else:
        # Find the existing config being updated
        config_index = None
        existing_config = None
        for idx, cfg in enumerate(existing_oauth_configs):
            if cfg.get("_id") == oauth_app_id and cfg.get("orgId") == org_id:
                config_index = idx
                existing_config = cfg
                break

        if config_index is not None and existing_config:
            # When updating: if name is empty/not provided, keep existing name
            if oauth_instance_name_from_request:
                oauth_instance_name = oauth_instance_name_from_request
                # Only check conflict if name is actually changing
                existing_name = existing_config.get("oauthInstanceName", "")
                if oauth_instance_name != existing_name:
                    _check_oauth_name_conflict(
                        existing_oauth_configs, oauth_instance_name, org_id, exclude_index=config_index
                    )
                    logger.info(f"Updating OAuth config {oauth_app_id} with new name '{oauth_instance_name}'")
                else:
                    logger.info(f"Updating OAuth config {oauth_app_id} (name unchanged)")
            else:
                # Keep existing name when updating
                oauth_instance_name = existing_config.get("oauthInstanceName", instance_name)
                logger.info(f"Updating OAuth config {oauth_app_id} with existing name '{oauth_instance_name}'")
        else:
            # Config not found, create new instead
            logger.warning(f"OAuth config {oauth_app_id} not found, will create new one")
            oauth_app_id = None
            oauth_instance_name = oauth_instance_name_from_request or instance_name
            _check_oauth_name_conflict(existing_oauth_configs, oauth_instance_name, org_id)

    # Create or update OAuth config
    return await _create_or_update_oauth_config(
        connector_type=connector_type,
        auth_config=auth_config,
        instance_name=oauth_instance_name,
        user_id=user_id,
        org_id=org_id,
        is_admin=is_admin,
        config_service=config_service,
        base_url=base_url,
        oauth_app_id=oauth_app_id,
        logger=logger
    )


async def _prepare_connector_config(
    config: Dict[str, Any],
    connector_type: str,
    scope: str,
    oauth_config_id: Optional[str],
    metadata: Dict[str, Any],
    selected_auth_type: str,
    user_id: str,
    org_id: str,
    is_admin: bool,
    config_service: ConfigurationService,
    base_url: str,
    logger
) -> Dict[str, Any]:
    """
    Prepare connector configuration for storage in etcd.

    Args:
        config: Raw configuration from request
        connector_type: Type of connector
        scope: Connector scope (personal/team)
        oauth_config_id: OAuth config ID if applicable
        metadata: Connector metadata from registry
        selected_auth_type: Selected authentication type
        user_id: User ID
        org_id: Organization ID
        is_admin: Whether user is admin
        config_service: Configuration service instance
        base_url: Base URL for OAuth redirects
        logger: Logger instance

    Returns:
        Prepared configuration dictionary
    """
    # ============================================================
    # 1. Filter OAuth Credential Fields from Auth Config
    # Only filter for OAUTH type - other auth types need these fields
    # ============================================================
    auth_config_clean = {}
    if config and config.get("auth"):
        auth_config_raw = config.get("auth", {})
        auth_type = selected_auth_type.upper() if selected_auth_type else "NONE"

        if auth_type == "OAUTH":
            # Only filter OAuth credential fields when authType is OAUTH
            # For OAUTH, credentials are stored in OAuth config registry, only reference ID is kept
            oauth_field_names = _get_oauth_field_names_from_registry(connector_type)

            for key, value in auth_config_raw.items():
                # Keep OAuth references and metadata
                if key in ["oauthConfigId", "oauthInstanceName", "authType", "connectorScope"]:
                    auth_config_clean[key] = value
                # Keep non-OAuth credential fields (skip OAuth credential fields like clientId, clientSecret, etc.)
                elif key not in oauth_field_names:
                    auth_config_clean[key] = value
                # OAuth credential fields are intentionally excluded - they're stored in OAuth config registry
        else:
            # For non-OAUTH auth types, keep all fields as they may be needed
            # (e.g., clientId/clientSecret for OAUTH_ADMIN_CONSENT, API_TOKEN, etc.)
            auth_config_clean = auth_config_raw.copy()

    prepared_config = {
        "auth": auth_config_clean,
        "sync": config.get("sync", {}) if config else {},
        "filters": config.get("filters", {}) if config else {},
        "credentials": None,
        "oauth": None,
        "scope": scope,
        "createdBy": user_id,
    }

    # ============================================================
    # 2. Fetch and Reference OAuth Config if Provided
    # ============================================================
    if oauth_config_id:
        oauth_config_path = _get_oauth_config_path(connector_type)
        oauth_configs = await config_service.get_config(oauth_config_path, default=[])

        if not isinstance(oauth_configs, list):
            oauth_configs = []

        # Find OAuth config with access control
        oauth_config = None
        for oauth_cfg in oauth_configs:
            if oauth_cfg.get("_id") == oauth_config_id:
                oauth_config = oauth_cfg
                break

        if not oauth_config:
            logger.error(f"OAuth config {oauth_config_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"OAuth config {oauth_config_id} not found or access denied"
            )

        # Store only reference, not sensitive credentials
        if "auth" not in prepared_config:
            prepared_config["auth"] = {}
        prepared_config["auth"]["oauthConfigId"] = oauth_config_id
        logger.info(f"Referenced OAuth config {oauth_config_id}")

    # ============================================================
    # 3. Add Auth Metadata from Registry
    # ============================================================
    prepared_config["auth"]["connectorType"] = connector_type

    auth_type = selected_auth_type.upper() if selected_auth_type else "NONE"
    auth_metadata = metadata.get("config", {}).get("auth", {})
    auth_schemas = auth_metadata.get("schemas", {})
    selected_auth_schema = auth_schemas.get(auth_type, {}) if auth_type != "NONE" else {}

    # Add OAuth infrastructure fields for OAUTH type
    if auth_type == "OAUTH":
        oauth_configs = auth_metadata.get("oauthConfigs", {})
        oauth_config = oauth_configs.get(auth_type, {}) if oauth_configs else {}

        # Get and prepare redirect URI
        redirect_uri = selected_auth_schema.get("redirectUri", "")
        if redirect_uri:
            if base_url:
                redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"
            else:
                endpoints = await config_service.get_config("/services/endpoints", use_cache=False)
                fallback_url = endpoints.get("frontend",{}).get("publicEndpoint", "http://localhost:3001")
                redirect_uri = f"{fallback_url.rstrip('/')}/{redirect_uri}"

        prepared_config["auth"].update({
            "authorizeUrl": oauth_config.get("authorizeUrl", ""),
            "tokenUrl": oauth_config.get("tokenUrl", ""),
            "scopes": oauth_config.get("scopes", []),
            "redirectUri": redirect_uri
        })

    # Store auth type and connector scope
    prepared_config["auth"].update({
        "authType": auth_type,
        "connectorScope": scope
    })

    return prepared_config


@router.post("/api/v1/connectors/", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def create_connector_instance(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> Dict[str, Any]:
    """
    Create a new connector instance.

    Request body should contain:
    - connector_type: Type of connector (from registry)
    - instance_name: Name for this instance
    - config: Initial configuration (auth, sync, filters)

    Args:
        request: FastAPI request object
        graph_provider: Injected graph DB provider

    Returns:
        Dictionary with created instance details including connector_id

    Raises:
        HTTPException: 400 for invalid data, 404 if connector type not found
    """
    container = request.app.container
    logger = container.logger()
    config_service = container.config_service()
    connector_registry = request.app.state.connector_registry

    try:
        # ============================================================
        # 1. Authentication & Parse Request Body
        # ============================================================
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"

        if not user_id or not org_id:
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )

        body = await request.json()
        connector_type = body.get("connectorType")
        instance_name = (body.get("instanceName") or "").strip()
        config = _trim_connector_config(body.get("config", {})) if body.get("config") else {}
        oauth_config_id = body.get("oauthConfigId")
        selected_auth_type = body.get("authType")
        base_url = body.get("baseUrl", "")
        scope = (body.get("scope") or "personal").lower()

        # ============================================================
        # 2. Validate Request Parameters
        # ============================================================
        if not connector_type or not instance_name:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="connector_type and instance_name are required"
            )

        if scope not in [ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value]:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Invalid scope. Must be 'personal' or 'team'"
            )

        # ============================================================
        # 3. Verify Connector Type & Get Metadata
        # ============================================================
        metadata = await connector_registry.get_connector_metadata(connector_type)
        if not metadata:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector type '{connector_type}' not found in registry"
            )

        # ============================================================
        # 4. Beta Connector Validation
        # ============================================================
        account_type = None
        try:
            account_type = await graph_provider.get_account_type(org_id)
        except Exception as e:
            logger.debug(f"Could not get account type: {e}")

        normalized_connector_type = connector_registry._normalize_connector_name(connector_type)
        beta_names = connector_registry._get_beta_connector_names()
        is_beta_connector = normalized_connector_type in beta_names

        if is_beta_connector and account_type and account_type.lower() in ['enterprise', 'business'] and scope == ConnectorScope.TEAM.value:
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail=f"Beta connector '{connector_type}' cannot be created for team scope in enterprise accounts."
            )

        await check_beta_connector_access(connector_type, request)

        # ============================================================
        # 5. Validate Scope & Permissions
        # ============================================================
        supported_scopes = metadata.get("scope", [ConnectorScope.PERSONAL])
        if scope not in supported_scopes:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Connector '{connector_type}' does not support scope '{scope}'"
            )

        if scope == ConnectorScope.TEAM.value and not is_admin:
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only administrators can create team connectors"
            )

        # ============================================================
        # 6. Validate & Determine Auth Type
        # ============================================================
        supported_auth_types = metadata.get("supportedAuthTypes", [])

        if not selected_auth_type:
            selected_auth_type = supported_auth_types[0] if supported_auth_types else "NONE"
            logger.info(f"Using auto-selected auth type: {selected_auth_type}")

        # Validate auth type compatibility
        if supported_auth_types and selected_auth_type not in supported_auth_types:
            if not (selected_auth_type.upper() == "NONE" and len(supported_auth_types) == 0):
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Auth type '{selected_auth_type}' is not supported. Supported: {', '.join(supported_auth_types)}"
                )

        # ============================================================
        # 7. Pre-validate OAuth Config (if applicable)
        # ============================================================
        # Check for OAuth name conflicts BEFORE creating connector instance
        # This prevents orphaned connector instances if OAuth name validation fails
        if is_admin and config and config.get("auth") and selected_auth_type and selected_auth_type.upper() == "OAUTH":
            oauth_field_names = _get_oauth_field_names_from_registry(connector_type)
            has_oauth_credentials = any(
                config.get("auth", {}).get(field_name) or
                config.get("auth", {}).get(field_name.replace("Id", "_id").replace("Secret", "_secret"))
                for field_name in oauth_field_names
            )

            if has_oauth_credentials:
                # Determine OAuth instance name
                oauth_instance_name = config.get("auth", {}).get("oauthInstanceName", "").strip() or instance_name

                # Get existing OAuth configs to check for name conflicts
                oauth_config_path = _get_oauth_config_path(connector_type)
                existing_oauth_configs = await config_service.get_config(oauth_config_path, default=[])

                if not isinstance(existing_oauth_configs, list):
                    existing_oauth_configs = []

                # Check if we're updating an existing OAuth config or creating a new one
                provided_oauth_config_id = oauth_config_id or config.get("auth", {}).get("oauthConfigId")

                if provided_oauth_config_id:
                    # Updating existing - check conflict excluding the config being updated
                    config_index = None
                    for idx, cfg in enumerate(existing_oauth_configs):
                        if cfg.get("_id") == provided_oauth_config_id and cfg.get("orgId") == org_id:
                            config_index = idx
                            break

                    if config_index is not None:
                        # Conflict check excluding the config being updated
                        _check_oauth_name_conflict(
                            existing_oauth_configs, oauth_instance_name, org_id, exclude_index=config_index
                        )
                        logger.debug(f"Pre-validation: OAuth config {provided_oauth_config_id} can be updated with name '{oauth_instance_name}'")
                    else:
                        # Config not found, will create new one instead - check as new
                        _check_oauth_name_conflict(existing_oauth_configs, oauth_instance_name, org_id)
                        logger.debug(f"Pre-validation: OAuth config {provided_oauth_config_id} not found, will create new config with name '{oauth_instance_name}'")
                else:
                    # Creating new - check for any name conflicts
                    _check_oauth_name_conflict(existing_oauth_configs, oauth_instance_name, org_id)
                    logger.debug(f"Pre-validation: New OAuth config with name '{oauth_instance_name}' can be created")

        # ============================================================
        # 8. Create Connector Instance in Database
        # ============================================================
        try:
            instance = await connector_registry.create_connector_instance_on_configuration(
                connector_type=connector_type,
                instance_name=instance_name,
                scope=scope,
                created_by=user_id,
                org_id=org_id,
                is_admin=is_admin,
                selected_auth_type=selected_auth_type
            )
        except ValueError as e:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=str(e)
            )

        if not instance:
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to create connector instance"
            )

        connector_id = instance.get("_key")

        # ============================================================
        # 9. Store Initial Configuration
        # ============================================================
        if config or oauth_config_id:
            logger.info(f"Storing initial config for instance {connector_id}")

            # Handle OAuth config creation/update if admin provides credentials
            if is_admin and config and config.get("auth"):
                logger.info(f"Admin provided auth config, attempting OAuth config creation/update for {connector_type}")
                logger.debug(f"Auth config keys: {list(config.get('auth', {}).keys())}")
                logger.debug(f"Connector authType: {selected_auth_type}")

                created_oauth_id = await _handle_oauth_config_creation(
                    connector_type=connector_type,
                    auth_config=config.get("auth"),
                    instance_name=instance_name,
                    user_id=user_id,
                    org_id=org_id,
                    is_admin=is_admin,
                    config_service=config_service,
                    oauth_config_id=oauth_config_id,
                    auth_type=selected_auth_type,
                    base_url=base_url,
                    logger=logger
                )

                if created_oauth_id:
                    oauth_config_id = created_oauth_id
                    if "auth" not in config:
                        config["auth"] = {}
                    config["auth"]["oauthConfigId"] = created_oauth_id
                    logger.info(f"OAuth config created/updated for connector {connector_id}")
                else:
                    logger.info(f"No OAuth config created for connector {connector_id} (credentials not provided or existing ID used)")
            elif config and config.get("auth"):
                logger.debug(f"Non-admin user provided auth config for {connector_id} - skipping OAuth config creation")
            else:
                logger.debug(f"No auth config provided for {connector_id}")

            # Prepare configuration for storage
            config_path = _get_config_path_for_instance(connector_id)
            prepared_config = await _prepare_connector_config(
                config=config,
                connector_type=connector_type,
                scope=scope,
                oauth_config_id=oauth_config_id,
                metadata=metadata,
                selected_auth_type=selected_auth_type or "NONE",
                user_id=user_id,
                org_id=org_id,
                is_admin=is_admin,
                config_service=config_service,
                base_url=base_url,
                logger=logger
            )

            await config_service.set_config(config_path, prepared_config)
            logger.info(f"Stored initial config for instance {connector_id}")

        # ============================================================
        # 10. Return Success Response
        # ============================================================
        logger.info(
            f"Created connector '{instance_name}' ({connector_type}) "
            f"with scope '{scope}' for user {user_id}"
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
                "isAuthenticated": False,
                "isConfigured": bool(config)
            },
            "message": "Connector instance created successfully."
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating connector instance: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to create connector instance: {str(e)}"
        )


@router.get("/api/v1/connectors/{connector_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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

        connector_type = connector.get("type", "")
        await check_beta_connector_access(connector_type, request)

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

@router.get("/api/v1/connectors/{connector_id}/config", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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

        connector_type = instance.get("type", "")
        await check_beta_connector_access(connector_type, request)

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

        # Remove sensitive data and internal fields
        config = config.copy()
        config.pop("credentials", None)
        config.pop("oauth", None)

        # Clean auth section in config (remove redundant OAuth fields that aren't needed)
        if "auth" in config:
            auth_config = config["auth"].copy()
            # Remove OAuth-specific fields that are fetched from OAuth config registry when needed
            # These are stored in etcd but not needed in the response
            auth_config.pop("authorizeUrl", None)
            auth_config.pop("tokenUrl", None)
            auth_config.pop("scopes", None)
            # oauthConfigs is not needed in config response (OAuth configs are fetched separately)
            auth_config.pop("oauthConfigs", None)
            config["auth"] = auth_config

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


@router.put("/api/v1/connectors/{connector_id}/config/auth", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def update_connector_instance_auth_config(
    connector_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> Dict[str, Any]:
    """
    Update authentication configuration for a connector instance.

    Request body must contain:
    - auth: Authentication configuration
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
        # Use dependency to validate and retrieve connector instance
        instance = await get_validated_connector_instance(connector_id, request)

        # Extract user info for later use
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        connector_type = instance.get("type", "")

        body = await request.json()
        base_url = body.get("baseUrl", "")

        if "auth" not in body:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Auth configuration is required"
            )

        # Trim whitespace from config values before processing
        body = _trim_connector_config(body)

        # Additional validation: Connector must be disabled for auth config updates
        if instance.get("isActive"):
            logger.error("Cannot update authentication configuration while connector is active. Please disable the connector first.")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot update authentication configuration while connector is active. Please disable the connector first."
            )

        config_service = container.config_service()
        config_path = _get_config_path_for_instance(connector_id)

        # Get existing config to merge with new values
        existing_config = await config_service.get_config(config_path)
        if not existing_config:
            existing_config = {}

        # Merge new auth configuration with existing config
        # Filter out OAuth credential fields - only store reference ID
        new_config = existing_config.copy() if existing_config else {}
        auth_config_raw = body.get("auth", {})

        # Auto-create or update OAuth config if OAuth fields are provided and user is admin
        # This happens when admin updates connector auth with OAuth credentials directly
        auth_type = instance.get("authType", "").upper()
        oauth_app_id = auth_config_raw.get("oauthConfigId")

        # Only OAUTH type supports OAuth configs, not OAUTH_ADMIN_CONSENT
        if is_admin and auth_type == "OAUTH":
            # ============================================================
            # Step 1: Determine OAuth instance name and check credentials
            # ============================================================
            oauth_field_names = _get_oauth_field_names_from_registry(connector_type)
            has_oauth_credentials = any(
                auth_config_raw.get(field_name) or
                auth_config_raw.get(field_name.replace("Id", "_id").replace("Secret", "_secret"))
                for field_name in oauth_field_names
            )

            if has_oauth_credentials:
                logger.info(f"OAuth credentials detected for {connector_type}, proceeding with OAuth config creation/update")
                logger.debug(f"OAuth field names from registry: {oauth_field_names}")
                logger.debug(f"Auth config keys received: {list(auth_config_raw.keys())}")

                # ============================================================
                # Step 2: Get existing OAuth configs and determine name
                # ============================================================
                oauth_config_path = _get_oauth_config_path(connector_type)
                existing_oauth_configs = await config_service.get_config(oauth_config_path, default=[])

                if not isinstance(existing_oauth_configs, list):
                    existing_oauth_configs = []

                # Determine OAuth instance name
                instance_name = instance.get("name", f"{connector_type} Connector")
                oauth_instance_name_from_request = auth_config_raw.get("oauthInstanceName", "").strip()

                # If creating new (no oauth_app_id)
                if not oauth_app_id:
                    # Use provided name or fall back to connector instance name
                    oauth_instance_name = oauth_instance_name_from_request or instance_name
                    _check_oauth_name_conflict(existing_oauth_configs, oauth_instance_name, org_id)
                    logger.info(f"Creating new OAuth config '{oauth_instance_name}' for {connector_type}")
                # If updating existing (has oauth_app_id)
                else:
                    # Find the existing config being updated
                    config_index = None
                    existing_config = None
                    for idx, cfg in enumerate(existing_oauth_configs):
                        if cfg.get("_id") == oauth_app_id and cfg.get("orgId") == org_id:
                            config_index = idx
                            existing_config = cfg
                            break

                    if config_index is not None and existing_config:
                        # When updating: if name is empty/not provided, keep existing name
                        if oauth_instance_name_from_request:
                            oauth_instance_name = oauth_instance_name_from_request
                            # Only check conflict if name is actually changing
                            existing_name = existing_config.get("oauthInstanceName", "")
                            if oauth_instance_name != existing_name:
                                _check_oauth_name_conflict(
                                    existing_oauth_configs, oauth_instance_name, org_id, exclude_index=config_index
                                )
                                logger.info(f"Updating OAuth config {oauth_app_id} with new name '{oauth_instance_name}'")
                            else:
                                logger.info(f"Updating OAuth config {oauth_app_id} (name unchanged)")
                        else:
                            # Keep existing name when updating
                            oauth_instance_name = existing_config.get("oauthInstanceName", instance_name)
                            logger.info(f"Updating OAuth config {oauth_app_id} with existing name '{oauth_instance_name}'")
                    else:
                        # Config not found, create new instead
                        logger.warning(f"OAuth config {oauth_app_id} not found, will create new one")
                        oauth_app_id = None
                        oauth_instance_name = oauth_instance_name_from_request or instance_name
                        _check_oauth_name_conflict(existing_oauth_configs, oauth_instance_name, org_id)

                # ============================================================
                # Step 3: Create or update OAuth config
                # ============================================================
                created_or_updated_oauth_app_id = await _create_or_update_oauth_config(
                    connector_type=connector_type,
                    auth_config=auth_config_raw,
                    instance_name=oauth_instance_name,
                    user_id=user_id,
                    org_id=org_id,
                    is_admin=is_admin,
                    config_service=config_service,
                    base_url=base_url,
                    oauth_app_id=oauth_app_id,
                    logger=logger
                )

                if created_or_updated_oauth_app_id:
                    oauth_app_id = created_or_updated_oauth_app_id
                    action = "Updated" if oauth_app_id else "Created"
                    logger.info(f"{action} OAuth config for connector {connector_id}")
                else:
                    logger.error("Failed to create/update OAuth config")
                    raise HTTPException(
                        status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                        detail="Failed to create/update OAuth configuration"
                    )
            elif oauth_app_id:
                # No credentials provided but has oauth_app_id - just use existing config
                logger.info(f"Using existing OAuth config for {connector_type} without credential updates")
            else:
                # No credentials and no oauth_app_id
                logger.info(f"No OAuth credentials or oauthConfigId provided for {connector_type} - skipping OAuth config operations")
                logger.debug(f"Expected OAuth fields: {oauth_field_names}")
                logger.debug(f"Received auth config keys: {list(auth_config_raw.keys())}")

        # Merge auth config with existing to preserve important fields like connectorScope
        existing_auth_config = existing_config.get("auth", {}) or {}

        # Filter out OAuth credential fields from auth config - only for OAUTH type
        # For other auth types (OAUTH_ADMIN_CONSENT, API_TOKEN, etc.), keep all fields
        # as they may be needed for those authentication methods
        auth_config_clean = {}

        if auth_type == "OAUTH":
            # Only filter OAuth credential fields when authType is OAUTH
            # For OAUTH, credentials are stored in OAuth config registry, only reference ID is kept
            oauth_field_names = _get_oauth_field_names_from_registry(connector_type)
            for key, value in auth_config_raw.items():
                # Keep OAuth app ID references and metadata fields
                if key in ["oauthConfigId", "oauthInstanceName", "authType", "connectorScope"]:
                    auth_config_clean[key] = value
                # Keep non-OAuth credential fields (skip OAuth credential fields like clientId, clientSecret, etc.)
                elif key not in oauth_field_names:
                    auth_config_clean[key] = value
                # OAuth credential fields are intentionally excluded - they're stored in OAuth config registry

            # Ensure OAuth app ID is set if it was created/updated
            if oauth_app_id:
                auth_config_clean["oauthConfigId"] = oauth_app_id
        else:
            # For non-OAUTH auth types, keep all fields as they may be needed
            # (e.g., clientId/clientSecret for OAUTH_ADMIN_CONSENT, API_TOKEN, etc.)
            auth_config_clean = auth_config_raw.copy()

        # Merge with existing auth config to preserve fields like connectorScope
        # Start with existing config, then overlay with new values
        merged_auth_config = existing_auth_config.copy()
        merged_auth_config.update(auth_config_clean)

        new_config["auth"] = merged_auth_config

        # Clear credentials and OAuth state when auth config is updated
        new_config["credentials"] = None
        new_config["oauth"] = None

        # Add OAuth metadata from registry if applicable
        # Only OAUTH type supports OAuth configs, not OAUTH_ADMIN_CONSENT
        auth_type = instance.get("authType", "").upper()
        if auth_type == "OAUTH":
            metadata = await connector_registry.get_connector_metadata(connector_type)
            auth_metadata = metadata.get("config", {}).get("auth", {})


            # Get OAuth config from oauthConfigs (same as _prepare_connector_config)
            oauth_configs = auth_metadata.get("oauthConfigs", {})
            oauth_config = oauth_configs.get(auth_type, {}) if oauth_configs else {}

            # Get redirect URI from auth schema (same as _prepare_connector_config)
            auth_schemas = auth_metadata.get("schemas", {})
            selected_auth_schema = auth_schemas.get(auth_type, {}) if auth_schemas else {}
            redirect_uri = selected_auth_schema.get("redirectUri", "")
            if redirect_uri:
                if base_url:
                    redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"
                else:
                    endpoints = await config_service.get_config(
                        "/services/endpoints",
                        use_cache=False
                    )
                    base_url = endpoints.get("frontend",{}).get("publicEndpoint", "http://localhost:3001")
                    redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"

            # Only use registry defaults if user hasn't provided these values
            oauth_updates = {
                "scopes": oauth_config.get("scopes", []),
                "redirectUri": redirect_uri,
                "authType": auth_type,
            }

            # Preserve user-provided authorizeUrl and tokenUrl if they exist
            if not new_config["auth"].get("authorizeUrl"):
                oauth_updates["authorizeUrl"] = oauth_config.get("authorizeUrl", "")
            if not new_config["auth"].get("tokenUrl"):
                oauth_updates["tokenUrl"] = oauth_config.get("tokenUrl", "")
            new_config["auth"].update(oauth_updates)

        if not new_config["auth"].get("connectorScope"):
            connector_doc = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)
            new_config["auth"]["connectorScope"] = connector_doc.get("scope", "")

        # Preserve scope and createdBy (connector instance metadata) so connectors receive them in config
        if "scope" not in new_config and existing_config.get("scope"):
            new_config["scope"] = existing_config["scope"]
        if "createdBy" not in new_config and existing_config.get("createdBy"):
            new_config["createdBy"] = existing_config["createdBy"]
        if "scope" not in new_config or "createdBy" not in new_config:
            connector_doc = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)
            if "scope" not in new_config:
                new_config["scope"] = connector_doc.get("scope", "personal")
            if "createdBy" not in new_config:
                new_config["createdBy"] = connector_doc.get("createdBy", "")

        # Save configuration
        await config_service.set_config(config_path, new_config)
        logger.info(f"Updated auth config for instance {connector_id}")

        # Cleanup existing connector instance if it exists (auth config changed)
        # User will need to toggle/enable again to re-initialize with new auth config
        if hasattr(container, 'connectors_map') and connector_id in container.connectors_map:
            logger.info(f"Cleaning up existing instance for {connector_id} due to auth config update")
            existing_connector = container.connectors_map.pop(connector_id)
            try:
                if hasattr(existing_connector, 'cleanup'):
                    await existing_connector.cleanup()
                logger.info(f"Cleaned up existing connector instance {connector_id}")
            except Exception as e:
                logger.error(f"Error cleaning up existing connector {connector_id}: {e}")

        # Update instance status - mark as configured but not authenticated
        # Connector will be initialized and authenticated when user clicks Enable
        updates = {
            "isConfigured": True,
            "isAuthenticated": False,  # Will be set to True after successful toggle/enable
            "isActive": False,  # Disable if auth config changed - user must re-enable
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
            "config": new_config,
            "message": "Authentication configuration saved successfully."
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating auth config for instance {connector_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to update connector authentication configuration: {str(e)}"
        )


@router.put("/api/v1/connectors/{connector_id}/config/filters-sync", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def update_connector_instance_filters_sync_config(
    connector_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> Dict[str, Any]:
    """
    Update filters and sync configuration for a connector instance.

    Request body can contain:
    - sync: Sync settings
    - filters: Filter configuration

    Validation:
    - Connector must be disabled (isActive = false)
    - For OAUTH connectors, isAuthenticated must be true

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
        # Use dependency to validate and retrieve connector instance
        instance = await get_validated_connector_instance(connector_id, request)

        # Extract user info for later use
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"

        body = await request.json()

        if "sync" not in body and "filters" not in body:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Sync or filters configuration is required"
            )

        # Trim whitespace from config values before processing
        body = _trim_connector_config(body)

        # Validation: Connector must be disabled
        if instance.get("isActive"):
            logger.error("Cannot update filters and sync configuration while connector is active. Please disable the connector first.")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot update filters and sync configuration while connector is active. Please disable the connector first."
            )

        config_service = container.config_service()
        config_path = _get_config_path_for_instance(connector_id)

        # Get existing config to merge with new values
        existing_config = await config_service.get_config(config_path)
        if not existing_config:
            existing_config = {}

        # Merge new configuration with existing config
        # Only update sections that are provided in the request
        new_config = existing_config.copy() if existing_config else {}

        # Update sync section if provided
        if "sync" in body and isinstance(body["sync"], dict):
            if "sync" in new_config and isinstance(new_config["sync"], dict):
                new_config["sync"] = {**new_config["sync"], **body["sync"]}
            else:
                new_config["sync"] = body["sync"]

        # Update filters section if provided
        if "filters" in body and isinstance(body["filters"], dict):
            # Initialize filters section if it doesn't exist
            if "filters" not in new_config:
                new_config["filters"] = {}

            # Merge filters section: preserve sync and indexing separately
            for key in ["sync", "indexing"]:
                if key in body["filters"]:
                    new_config["filters"][key] = body["filters"][key]

        # Save configuration
        await config_service.set_config(config_path, new_config)
        logger.info(f"Updated filters-sync config for instance {connector_id}")

        # Cancel any running sync task for this connector (safety net — normally the
        # connector must be disabled before filters can be changed, but a task may
        # still be winding down from a previous run)
        await sync_task_manager.cancel_sync(connector_id)
        logger.info(f"Cancelled any running sync task for connector {connector_id}")

        # Delete all sync points so the next sync is a clean full sweep based on
        # the updated filter configuration
        try:
            deleted_count, success = await graph_provider.delete_sync_points_by_connector_id(
                connector_id=connector_id
            )
            if success:
                logger.info(f"Deleted {deleted_count} sync points for connector {connector_id} after filter change")
            else:
                logger.warning(f"Failed to delete sync points for connector {connector_id} after filter change, continuing anyway")
        except Exception as sp_error:
            logger.error(f"Error deleting sync points for connector {connector_id} after filter change: {sp_error}")
            # Non-fatal — continue with the config update response

        # For filters/sync updates, keep connector status as is
        # Only update the timestamp
        updates = {
            "isConfigured": True,
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
            "config": new_config,
            "message": "Filters and sync configuration saved successfully."
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating filters-sync config for instance {connector_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to update connector filters and sync configuration: {str(e)}"
        )


@router.put("/api/v1/connectors/{connector_id}/config", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def update_connector_instance_config(
    connector_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
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
        # Use dependency to validate and retrieve connector instance
        instance = await get_validated_connector_instance(connector_id, request)

        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"
        connector_type = instance.get("type", "")
        body = await request.json()
        base_url = body.get("baseUrl", "")
        oauth_config_id = body.get("oauthConfigId")  # Reference to stored OAuth config

        # Trim whitespace from config values before processing
        body = _trim_connector_config(body)

        # Prevent saving configuration when connector is active
        # Only allow filter/sync updates when connector is active (these don't require re-initialization)
        if instance.get("isActive"):
            logger.error("Cannot update configuration while connector is active. Please disable the connector first.")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot update configuration while connector is active. Please disable the connector first."
            )

        config_service = container.config_service()
        config_path = _get_config_path_for_instance(connector_id)

        # Get existing config to merge with new values
        existing_config = await config_service.get_config(config_path)
        if not existing_config:
            existing_config = {}

        # Merge new configuration with existing config
        # Only update sections that are provided in the request
        new_config = existing_config.copy() if existing_config else {}

        # Determine which sections are being updated
        auth_updated = "auth" in body

        for section in ["auth", "sync", "filters"]:
            if section in body and isinstance(body[section], dict):
                # For filters section, we need special handling to preserve sync/indexing separately
                if section == "filters":
                    # Initialize filters section if it doesn't exist
                    if section not in new_config:
                        new_config[section] = {}

                    # Merge filters section: preserve sync and indexing separately
                    # If body has filters.sync, update only filters.sync (preserve filters.indexing)
                    # If body has filters.indexing, update only filters.indexing (preserve filters.sync)
                    for key in ["sync", "indexing"]:
                        if key in body[section]:
                            # Replace the entire sync or indexing subsection
                            new_config[section][key] = body[section][key]
                elif section in new_config and isinstance(new_config[section], dict):
                    # For auth and sync sections, merge at top level (preserve existing values)
                    new_config[section] = {**new_config[section], **body[section]}
                else:
                    # Section doesn't exist, add it
                    new_config[section] = body[section]


        # Clear credentials and OAuth state only if auth config is being updated
        # Filters and sync updates don't require re-authentication
        if auth_updated:
            new_config["credentials"] = None
            new_config["oauth"] = None


        # Prevent auth type changes after connector creation
        # Check both body.auth.authType and body.authType (for backward compatibility)
        if auth_updated:
            new_auth_type = None
            if "auth" in body and "authType" in body.get("auth", {}):
                new_auth_type = body["auth"].get("authType", "").upper()
            elif "authType" in body:
                new_auth_type = body.get("authType", "").upper()

            if new_auth_type:
                existing_auth_type = instance.get("authType", "").upper()
                if new_auth_type != existing_auth_type:
                    logger.error(f"Cannot change auth type from '{existing_auth_type}' to '{new_auth_type}'. Auth type cannot be changed after connector creation.")
                    raise HTTPException(
                        status_code=HttpStatusCode.BAD_REQUEST.value,
                        detail=f"Cannot change auth type from '{existing_auth_type}' to '{new_auth_type}'. Auth type is locked after connector creation."
                    )

        # Add OAuth metadata from registry if applicable (only if auth is being updated)
        if auth_updated:
            # Use existing auth type from instance (cannot be changed)
            auth_type = instance.get("authType", "").upper()
            if auth_type in ["OAUTH", "OAUTH_ADMIN_CONSENT"]:
                # If oauth_config_id is provided, fetch and merge OAuth config from etcd
                if oauth_config_id:
                    try:
                        oauth_config_path = _get_oauth_config_path(connector_type)
                        oauth_configs = await config_service.get_config(oauth_config_path, default=[])

                        if not isinstance(oauth_configs, list):
                            oauth_configs = []

                        # Find the OAuth config (all users in org can use published OAuth configs)
                        oauth_config = None
                        for oauth_cfg in oauth_configs:
                            if oauth_cfg.get("_id") == oauth_config_id:
                                oauth_org_id = oauth_cfg.get("orgId")
                                # All users in the same org can use published OAuth configs
                                if oauth_org_id == org_id:
                                    oauth_config = oauth_cfg
                                    break

                        if not oauth_config:
                            logger.error(f"OAuth config {oauth_config_id} not found or access denied")
                            raise HTTPException(
                                status_code=HttpStatusCode.NOT_FOUND.value,
                                detail=f"OAuth config {oauth_config_id} not found or access denied"
                            )

                        # Store only the reference to OAuth config, not the sensitive fields
                        # The actual client_id/client_secret will be fetched during OAuth flow
                        if "auth" not in new_config:
                            new_config["auth"] = {}
                        # Store only the reference and metadata, not sensitive credentials
                        new_config["auth"]["oauthConfigId"] = oauth_config_id
                        new_config["auth"]["oauthInstanceName"] = oauth_config.get("oauthInstanceName")
                        logger.info(f"Referenced OAuth config {oauth_config_id} for connector auth config")

                    except HTTPException:
                        raise
                    except Exception as e:
                        logger.error(f"Error fetching OAuth config {oauth_config_id}: {e}")
                        raise HTTPException(
                            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                            detail=f"Failed to fetch OAuth configuration: {str(e)}"
                        )

                metadata = await connector_registry.get_connector_metadata(connector_type)
                auth_metadata = metadata.get("config", {}).get("auth", {})

                if "auth" not in new_config:
                    new_config["auth"] = {}

                # Get auth schema and OAuth config for the existing auth type
                auth_schemas = auth_metadata.get("schemas", {})
                selected_auth_schema = auth_schemas.get(auth_type, {}) if auth_schemas else {}
                oauth_configs = auth_metadata.get("oauthConfigs", {})
                oauth_config = oauth_configs.get(auth_type, {}) if oauth_configs else {}

                # Get redirect URI from the auth type's schema
                redirect_uri = selected_auth_schema.get("redirectUri", "")

                if redirect_uri:
                    if base_url:
                        redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"
                    else:
                        endpoints = await config_service.get_config(
                            "/services/endpoints",
                            use_cache=False
                        )
                        base_url = endpoints.get("frontend",{}).get("publicEndpoint", "http://localhost:3001")
                        redirect_uri = f"{base_url.rstrip('/')}/{redirect_uri}"


                new_config["auth"].update({
                    "authorizeUrl": oauth_config.get("authorizeUrl", ""),
                    "tokenUrl": oauth_config.get("tokenUrl", ""),
                    "scopes": oauth_config.get("scopes", []),
                    "redirectUri": redirect_uri,
                    "authType": auth_type,  # Keep existing auth type (cannot be changed)
                })

        # Preserve scope and createdBy (connector instance metadata) so connectors receive them in config
        if "scope" not in new_config and existing_config.get("scope"):
            new_config["scope"] = existing_config["scope"]
        if "createdBy" not in new_config and existing_config.get("createdBy"):
            new_config["createdBy"] = existing_config["createdBy"]
        if "scope" not in new_config or "createdBy" not in new_config:
            connector_doc = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)
            if "scope" not in new_config:
                new_config["scope"] = connector_doc.get("scope", "personal")
            if "createdBy" not in new_config:
                new_config["createdBy"] = connector_doc.get("createdBy", "")

        # Save configuration
        await config_service.set_config(config_path, new_config)
        logger.info(f"Updated config for instance {connector_id}")

        # Only cleanup and disable connector if auth config is being updated
        # Filters and sync updates don't require re-authentication, so connector can stay active
        if auth_updated:
            # Cleanup existing connector instance if it exists (auth config changed)
            # User will need to toggle/enable again to re-initialize with new auth config
            if hasattr(container, 'connectors_map') and connector_id in container.connectors_map:
                logger.info(f"Cleaning up existing instance for {connector_id} due to auth config update")
                existing_connector = container.connectors_map.pop(connector_id)
                try:
                    if hasattr(existing_connector, 'cleanup'):
                        await existing_connector.cleanup()
                    logger.info(f"Cleaned up existing connector instance {connector_id}")
                except Exception as e:
                    logger.error(f"Error cleaning up existing connector {connector_id}: {e}")

            # Update instance status - mark as configured but not authenticated
            # Connector will be initialized and authenticated when user clicks Enable
            updates = {
                "isConfigured": True,
                "isAuthenticated": False,  # Will be set to True after successful toggle/enable
                "isActive": False,  # Disable if auth config changed - user must re-enable
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedBy": user_id
            }
        else:
            # For filters/sync updates, keep connector active and authenticated
            # Only update the timestamp and preserve all other status fields
            updates = {
                "isConfigured": True,
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
            "config": new_config,
            "message": "Configuration saved successfully."
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating config for instance {connector_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to update connector configuration: {str(e)}"
        )
@router.put("/api/v1/connectors/{connector_id}/name", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def update_connector_instance_name(
    connector_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider)
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

        connector_type = instance.get("type", "")
        await check_beta_connector_access(connector_type, request)

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
# Common Helper Functions
# ============================================================================

def _get_user_context(request: Request) -> Dict[str, Any]:
    """
    Extract and validate user authentication context from request.

    Args:
        request: FastAPI request object

    Returns:
        Dictionary with user_id, org_id, and is_admin

    Raises:
        HTTPException: If user is not authenticated
    """
    user_id = request.state.user.get("userId")
    org_id = request.state.user.get("orgId")
    is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"

    if not user_id or not org_id:
        raise HTTPException(
            status_code=HttpStatusCode.UNAUTHORIZED.value,
            detail="User not authenticated"
        )

    return {
        "user_id": user_id,
        "org_id": org_id,
        "is_admin": is_admin
    }


def _validate_admin_only(is_admin: bool, action: str = "perform this action") -> None:
    """
    Validate that user is an administrator.

    Args:
        is_admin: Whether user is admin
        action: Action being performed (for error message)

    Raises:
        HTTPException: If user is not admin
    """
    if not is_admin:
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value,
            detail=f"Only administrators can {action}"
        )


def _validate_connector_permissions(
    instance: Dict[str, Any],
    user_id: str,
    is_admin: bool,
    action: str = "access"
) -> None:
    """
    Validate user permissions for connector instance operations.

    Validates based on connector scope (team/personal) and user role.

    Args:
        instance: Connector instance dictionary
        user_id: User ID performing the action
        is_admin: Whether user is admin
        action: Action being performed (for error messages)

    Raises:
        HTTPException: If user doesn't have permission
    """
    scope = instance.get("scope")
    created_by = instance.get("createdBy")

    # Team connectors require admin access
    if scope == ConnectorScope.TEAM.value and not is_admin:
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value,
            detail=f"Only administrators can {action} team connectors"
        )

    # Personal connectors: creator or admin can access
    if scope == ConnectorScope.PERSONAL.value:
        if created_by != user_id and not is_admin:
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail=f"Only the creator can {action} this connector"
            )
    # For any scope: non-creator non-admins cannot access
    elif created_by != user_id and not is_admin:
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value,
            detail=f"Only the creator or an administrator can {action} this connector"
        )


async def _get_and_validate_connector_instance(
    connector_id: str,
    user_context: Dict[str, Any],
    connector_registry,
    logger
) -> Dict[str, Any]:
    """
    Retrieve connector instance and validate access.

    Args:
        connector_id: Connector instance ID
        user_context: User context from _get_user_context
        connector_registry: Connector registry instance
        logger: Logger instance

    Returns:
        Connector instance dictionary

    Raises:
        HTTPException: If instance not found or access denied
    """
    instance = await connector_registry.get_connector_instance(
        connector_id=connector_id,
        user_id=user_context["user_id"],
        org_id=user_context["org_id"],
        is_admin=user_context["is_admin"]
    )

    if not instance:
        logger.error(f"Connector instance {connector_id} not found or access denied")
        raise HTTPException(
            status_code=HttpStatusCode.NOT_FOUND.value,
            detail=f"Connector instance {connector_id} not found or access denied"
        )

    return instance


async def _find_oauth_config_in_list(
    oauth_configs: List[Dict[str, Any]],
    config_id: str,
    org_id: str,
    logger
) -> tuple[Optional[Dict[str, Any]], Optional[int]]:
    """
    Find OAuth config by ID in list with access control.

    Args:
        oauth_configs: List of OAuth configs
        config_id: Config ID to find
        org_id: Organization ID for filtering
        logger: Logger instance

    Returns:
        Tuple of (config dict, index) if found, (None, None) otherwise
    """
    for idx, config in enumerate(oauth_configs):
        if config.get("_id") == config_id:
            config_org_id = config.get("orgId")
            if config_org_id == org_id:
                return config, idx

    return None, None


def _check_oauth_name_conflict(
    oauth_configs: List[Dict[str, Any]],
    name: str,
    org_id: str,
    exclude_index: Optional[int] = None
) -> None:
    """
    Check if OAuth config name conflicts with existing configs.

    Args:
        oauth_configs: List of existing OAuth configs
        name: Name to check
        org_id: Organization ID for filtering
        exclude_index: Index to exclude from check (for updates)

    Raises:
        HTTPException: If name conflict exists
    """
    for idx, config in enumerate(oauth_configs):
        if idx == exclude_index:
            continue

        if (config.get("oauthInstanceName") == name and
            config.get("orgId") == org_id):
            raise HTTPException(
                status_code=HttpStatusCode.CONFLICT.value,
                detail=f"An OAuth configuration with the name '{name}' already exists. Please use a different name."
            )


async def _update_oauth_infrastructure_fields(
    oauth_config: Dict[str, Any],
    connector_type: str,
    config_service: ConfigurationService,
    base_url: str
) -> None:
    """
    Ensure OAuth infrastructure fields are present from registry.

    Updates the config dict in-place with missing infrastructure fields.

    Args:
        oauth_config: OAuth config dictionary to update
        connector_type: Type of connector
        config_service: Configuration service for endpoints
        base_url: Base URL for OAuth redirects
    """
    from app.connectors.core.registry.oauth_config_registry import (
        get_oauth_config_registry,
    )

    oauth_registry = get_oauth_config_registry()
    oauth_registry_config = oauth_registry.get_config(connector_type)

    if not oauth_registry_config:
        return

    # Update OAuth infrastructure fields if missing
    if "authorizeUrl" not in oauth_config:
        oauth_config["authorizeUrl"] = oauth_registry_config.authorize_url

    if "tokenUrl" not in oauth_config:
        oauth_config["tokenUrl"] = oauth_registry_config.token_url

    if "redirectUri" not in oauth_config:
        redirect_uri_path = oauth_registry_config.redirect_uri
        if redirect_uri_path:
            if base_url:
                oauth_config["redirectUri"] = f"{base_url.rstrip('/')}/{redirect_uri_path}"
            else:
                endpoints = await config_service.get_config("/services/endpoints", use_cache=False)
                fallback_url = endpoints.get("frontend",{}).get("publicEndpoint", "http://localhost:3001")
                oauth_config["redirectUri"] = f"{fallback_url.rstrip('/')}/{redirect_uri_path}"
        else:
            oauth_config["redirectUri"] = ""

    if "scopes" not in oauth_config:
        oauth_config["scopes"] = oauth_registry_config.scopes.to_dict()

    if "tokenAccessType" not in oauth_config and oauth_registry_config.token_access_type:
        oauth_config["tokenAccessType"] = oauth_registry_config.token_access_type

    if "additionalParams" not in oauth_config and oauth_registry_config.additional_params:
        oauth_config["additionalParams"] = oauth_registry_config.additional_params

    # Update metadata fields if missing
    if "iconPath" not in oauth_config:
        oauth_config["iconPath"] = oauth_registry_config.icon_path
    if "appGroup" not in oauth_config:
        oauth_config["appGroup"] = oauth_registry_config.app_group
    if "appDescription" not in oauth_config:
        oauth_config["appDescription"] = oauth_registry_config.app_description
    if "appCategories" not in oauth_config:
        oauth_config["appCategories"] = oauth_registry_config.app_categories


# ============================================================================
# OAuth Endpoints
# ============================================================================

async def _build_oauth_flow_config(
    auth_config: Dict[str, Any],
    connector_type: str,
    org_id: str,
    config_service: ConfigurationService,
    logger
) -> Dict[str, Any]:
    """
    Build OAuth flow configuration from either shared OAuth config or direct auth config.

    Args:
        auth_config: Connector's auth configuration
        connector_type: Type of connector
        org_id: Organization ID for access control
        config_service: Configuration service instance
        logger: Logger instance

    Returns:
        OAuth flow configuration dictionary with all necessary fields
    Raises:
        HTTPException: If shared OAuth config is referenced but not found
    """

    oauth_config_id = auth_config.get("oauthConfigId")

    # Use shared OAuth config if available
    if oauth_config_id:
        oauth_config_path = _get_oauth_config_path(connector_type)
        oauth_configs = await config_service.get_config(oauth_config_path, default=[])

        if not isinstance(oauth_configs, list):
            oauth_configs = []

        # Find the OAuth config for this organization
        shared_oauth_config = None
        for oauth_cfg in oauth_configs:
            if oauth_cfg.get("_id") == oauth_config_id and oauth_cfg.get("orgId") == org_id:
                shared_oauth_config = oauth_cfg
                break

        if not shared_oauth_config:
            logger.error(f"OAuth config {oauth_config_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"OAuth config {oauth_config_id} not found or access denied"
            )

        # Build flow config from shared OAuth config
        # Prioritize values from connector instance config (auth_config) if they exist
        oauth_flow_config = {
            "authorizeUrl": auth_config.get("authorizeUrl") or shared_oauth_config.get("authorizeUrl", ""),
            "tokenUrl": auth_config.get("tokenUrl") or shared_oauth_config.get("tokenUrl", ""),
            "redirectUri": auth_config.get("redirectUri") or shared_oauth_config.get("redirectUri", ""),
        }

        # Prioritize scopes from connector instance config (auth_config) if they exist
        # This ensures we use the scopes that were set during connector creation/update
        if auth_config.get("scopes"):
            oauth_flow_config["scopes"] = auth_config["scopes"] if isinstance(auth_config["scopes"], list) else []
        else:
            # Fall back to shared OAuth config scopes if not in connector instance config
            # Convert scopes from dict to list based on connector scope
            connector_scope = auth_config.get("connectorScope", "team").lower()
            scopes_data = shared_oauth_config.get("scopes", {})

            if isinstance(scopes_data, dict):
                scope_key_map = {"personal": "personal_sync", "team": "team_sync", "agent": "agent"}
                scope_key = scope_key_map.get(connector_scope, "team_sync")
                scope_list = scopes_data.get(scope_key, [])
                oauth_flow_config["scopes"] = scope_list if isinstance(scope_list, list) else []
            else:
                oauth_flow_config["scopes"] = scopes_data if isinstance(scopes_data, list) else []

        # Add optional fields
        if "tokenAccessType" in shared_oauth_config:
            oauth_flow_config["tokenAccessType"] = shared_oauth_config["tokenAccessType"]
        if "additionalParams" in shared_oauth_config:
            oauth_flow_config["additionalParams"] = shared_oauth_config["additionalParams"]

        # Get OAuth credential fields from config section
        oauth_config_data = shared_oauth_config.get("config", {})
        if oauth_config_data:
            oauth_config_copy = oauth_config_data.copy()
            # Normalize field names
            if "client_id" in oauth_config_copy and "clientId" not in oauth_config_copy:
                oauth_config_copy["clientId"] = oauth_config_copy.pop("client_id")
            if "client_secret" in oauth_config_copy and "clientSecret" not in oauth_config_copy:
                oauth_config_copy["clientSecret"] = oauth_config_copy.pop("client_secret")
            oauth_flow_config.update(oauth_config_copy)

        # Preserve connector-specific settings
        if "authType" in auth_config:
            oauth_flow_config["authType"] = auth_config["authType"]
        if "connectorScope" in auth_config:
            oauth_flow_config["connectorScope"] = auth_config["connectorScope"]

        logger.info(f"Using shared OAuth config {oauth_config_id}")
    else:
        # Use connector's auth config directly
        oauth_flow_config = auth_config.copy()

    return oauth_flow_config


@router.get("/api/v1/connectors/{connector_id}/oauth/authorize", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def get_oauth_authorization_url(
    connector_id: str,
    request: Request,
    base_url: Optional[str] = Query(None),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> Dict[str, Any]:
    """
    Get OAuth authorization URL for a connector instance.

    Args:
        connector_id: Unique instance key
        request: FastAPI request object
        base_url: Optional base URL for redirect
        graph_provider: Injected graph DB provider

    Returns:
        Dictionary with authorization URL and encoded state

    Raises:
        HTTPException: 400 if OAuth not supported, 404 if instance not found
    """
    container = request.app.container
    logger = container.logger()
    config_service = container.config_service()
    connector_registry = request.app.state.connector_registry

    try:
        # ============================================================
        # 1. Authentication & Authorization
        # ============================================================
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"

        if not user_id or not org_id:
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )

        # Get connector instance
        instance = await connector_registry.get_connector_instance(
            connector_id=connector_id,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not instance:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector instance {connector_id} not found or access denied"
            )

        connector_type = instance.get("type", "").replace(" ", "").upper()
        await check_beta_connector_access(connector_type, request)

        # Verify permissions based on scope
        is_team_connector = instance.get("scope") == ConnectorScope.TEAM.value
        is_creator = instance.get("createdBy") == user_id

        if is_team_connector and not is_admin:
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only administrators can get OAuth authorization URL for team connectors"
            )
        if not is_creator and not is_admin:
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator or an administrator can get OAuth authorization URL for this connector"
            )

        # Verify OAuth support
        auth_type = (instance.get("authType") or "").upper()
        if auth_type not in ["OAUTH", "OAUTH_ADMIN_CONSENT"]:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Connector instance does not support OAuth"
            )

        # ============================================================
        # 2. Configuration Retrieval
        # ============================================================
        config_path = _get_config_path_for_instance(connector_id)
        config = await config_service.get_config(config_path)

        if not config or not config.get("auth"):
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="OAuth configuration not found. Please configure first."
            )

        auth_config = config["auth"]
        connector_scope = instance.get("scope", "team").lower()


        # ============================================================
        # 3. Build OAuth Flow Configuration
        # ============================================================
        oauth_flow_config = await _build_oauth_flow_config(
            auth_config=auth_config,
            connector_type=connector_type,
            org_id=org_id,
            config_service=config_service,
            logger=logger
        )

        logger.info(f"Redirect URI: {oauth_flow_config.get('redirectUri', '')}")

        # ============================================================
        # 4. Generate Authorization URL
        # ============================================================
        oauth_config = get_oauth_config(oauth_flow_config)
        # Fallback: if scope is empty, use scopes from instance document
        if not oauth_config.scope:
            scopes_list = instance.get("scopes", [])
            if scopes_list and isinstance(scopes_list, list):
                oauth_config.scope = ' '.join(scopes_list)
        logger.info(f"Using {connector_scope} with scopes: {oauth_config.scope}")


        oauth_provider = OAuthProvider(
            config=oauth_config,
            configuration_service=config_service,
            credentials_path=config_path
        )

        try:
            # Generate base authorization URL
            auth_url = await oauth_provider.start_authorization()

            # Apply provider-specific URL modifications
            if connector_type == "ONEDRIVE":
                parsed_url = urlparse(auth_url)
                params = parse_qs(parsed_url.query)
                params["response_mode"] = ["query"]

                if auth_type == "OAUTH_ADMIN_CONSENT":
                    params["prompt"] = ["admin_consent"]

                auth_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?{urlencode(params, doseq=True)}"

            # Encode state with connector_id for callback routing
            parsed_url = urlparse(auth_url)
            query_params = parse_qs(parsed_url.query)
            original_state = query_params.get("state", [None])[0]

            if not original_state:
                raise ValueError("No state parameter in authorization URL")

            encoded_state = _encode_state_with_instance(original_state, connector_id)
            query_params["state"] = [encoded_state]

            final_auth_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?{urlencode(query_params, doseq=True)}"

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


@router.get("/api/v1/connectors/oauth/callback", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def handle_oauth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    base_url: Optional[str] = Query(None),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider)
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
        graph_provider: Injected graph DB provider

    Returns:
        Dictionary with redirect URL and status
    """
    container = request.app.container
    logger = container.logger()
    config_service = container.config_service()
    connector_registry = request.app.state.connector_registry

    settings_base_path = await _get_settings_base_path(graph_provider)
    connector_id = None  # For error handling

    try:
        # ============================================================
        # 1. Validate OAuth Parameters
        # ============================================================
        # Normalize error values
        if error in ["null", "undefined", "None", ""]:
            error = None

        if error:
            logger.error(f"OAuth error: {error}")
            return {
                "success": False,
                "error": error,
                "redirect_url": f"{base_url or ''}/connectors/oauth/callback?oauth_error={error}"
            }

        if not code or not state:
            logger.error("Missing OAuth parameters")
            return {
                "success": False,
                "error": "missing_parameters",
                "redirect_url": f"{base_url or ''}/connectors/oauth/callback?oauth_error=missing_parameters"
            }

        # ============================================================
        # 2. Authentication & Decode State
        # ============================================================
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"

        if not user_id or not org_id:
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )

        # Decode state to extract connector_id
        try:
            state_data = _decode_state_with_instance(state)
            original_state = state_data["state"]
            connector_id = state_data["connector_id"]
        except ValueError as e:
            logger.error(f"Invalid state format: {e}")
            return {
                "success": False,
                "error": "invalid_state",
                "redirect_url": f"{base_url or ''}/connectors/oauth/callback?oauth_error=invalid_state"
            }

        # ============================================================
        # 3. Authorization & Configuration Retrieval
        # ============================================================
        # Get connector instance
        instance = await connector_registry.get_connector_instance(
            connector_id=connector_id,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        if not instance:
            logger.error(f"Instance {connector_id} not found or access denied")
            return {
                "success": False,
                "error": "instance_not_found",
                "redirect_url": f"{base_url or ''}/connectors/oauth/callback?oauth_error=instance_not_found"
            }

        connector_type = instance.get("type", "").replace(" ", "")
        await check_beta_connector_access(connector_type, request)

        # Verify permissions based on scope
        is_team_connector = instance.get("scope") == ConnectorScope.TEAM.value
        is_creator = instance.get("createdBy") == user_id

        if is_team_connector and not is_admin:
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only administrators can handle OAuth callback for team connectors"
            )
        if not is_creator and not is_admin:
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="Only the creator or an administrator can handle OAuth callback for this connector"
            )

        # Get configuration
        config_path = _get_config_path_for_instance(connector_id)
        config = await config_service.get_config(config_path)

        if not config or not config.get("auth"):
            logger.error(f"No OAuth config for instance {connector_id}")
            return {
                "success": False,
                "error": "config_not_found",
                "redirect_url": f"{base_url or ''}/connectors/oauth/callback?oauth_error=config_not_found"
            }

        auth_config = config["auth"]

        # ============================================================
        # 4. Build OAuth Flow Configuration
        # ============================================================
        try:
            oauth_flow_config = await _build_oauth_flow_config(
                auth_config=auth_config,
                connector_type=connector_type,
                org_id=org_id,
                config_service=config_service,
                logger=logger
            )
        except HTTPException:
            return {
                "success": False,
                "error": "oauth_config_fetch_error",
                "redirect_url": f"{base_url or ''}/connectors/oauth/callback?oauth_error=oauth_config_fetch_error"
            }

        logger.info(f"Callback redirect URI: {oauth_flow_config.get('redirectUri', '')}")

        # ============================================================
        # 5. Exchange Code for Token
        # ============================================================
        oauth_config = get_oauth_config(oauth_flow_config)
        oauth_provider = OAuthProvider(
            config=oauth_config,
            configuration_service=config_service,
            credentials_path=config_path
        )

        try:
            token = await oauth_provider.handle_callback(code, original_state)
        finally:
            await oauth_provider.close()

        if not token or not token.access_token:
            logger.error(f"Invalid token received for instance {connector_id}")
            return {
                "success": False,
                "error": "invalid_token",
                "redirect_url": f"{base_url}{settings_base_path}?oauth_error=invalid_token"
            }

        # Log token information after OAuth callback completes
        logger.info(
            f"OAuth token exchange completed for instance {connector_id} (connector_type: {connector_type}). "
            f"Token details - has_access_token: {bool(token.access_token)}, "
            f"has_refresh_token: {bool(token.refresh_token)}, "
            f"token_type: {token.token_type}, "
            f"expires_in: {token.expires_in}, "
            f"refresh_token_expires_in: {token.refresh_token_expires_in}, "
            f"scope: {token.scope}, "
            f"has_id_token: {bool(token.id_token)}, "
            f"uid: {token.uid}, "
            f"account_id: {token.account_id}, "
            f"team_id: {token.team_id}, "
            f"created_at: {token.created_at}"
        )

        logger.info(f"OAuth tokens stored successfully for instance {connector_id}")

        # ============================================================
        # 6. Post-Processing: Cache, Token Refresh, Status Update
        # ============================================================
        # Refresh configuration cache
        try:
            updated_config = await config_service.get_config(config_path, use_cache=False)
            if isinstance(updated_config, dict):
                await config_service.set_config(config_path, updated_config)
                logger.info(f"Refreshed config cache for instance {connector_id}")
        except Exception as cache_err:
            logger.warning(f"Could not refresh config cache: {cache_err}")

        # Schedule token refresh
        try:
            from app.connectors.core.base.token_service.startup_service import (
                startup_service,
            )
            refresh_service = startup_service.get_token_refresh_service()

            if refresh_service:
                await refresh_service.schedule_token_refresh(connector_id, connector_type, token)
                logger.info(f"✅ Scheduled token refresh for instance {connector_id}")
            else:
                # Fallback: create temporary service
                logger.warning("⚠️ Token refresh service not initialized, using temporary service")
                from app.connectors.core.base.token_service.token_refresh_service import (
                    TokenRefreshService,
                )
                temp_service = TokenRefreshService(config_service, graph_provider)
                await temp_service.schedule_token_refresh(connector_id, connector_type, token)
                logger.info("✅ Scheduled token refresh using temporary service")
        except Exception as sched_err:
            logger.error(f"❌ Could not schedule token refresh for {connector_id}: {sched_err}", exc_info=True)

        # Update instance authentication status
        updates = {
            "isAuthenticated": True,
            "updatedAtTimestamp": get_epoch_timestamp_in_ms()
        }
        await connector_registry.update_connector_instance(
            connector_id=connector_id,
            updates=updates,
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )
        logger.info(f"Instance {connector_id} marked as authenticated")

        return {
            "success": True,
            "redirect_url": f"{base_url}{settings_base_path}/{connector_id}"
        }

    except Exception as e:
        logger.error(f"Error handling OAuth callback: {e}")

        # Update instance authentication status on error
        if connector_id:
            try:
                updates = {
                    "isAuthenticated": False,
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                }
                await connector_registry.update_connector_instance(
                    connector_id=connector_id,
                    updates=updates,
                    user_id=user_id,
                    org_id=org_id,
                    is_admin=is_admin
                )
            except Exception:
                pass

        return {
            "success": False,
            "error": "server_error",
            "redirect_url": f"{base_url or ''}/connectors/oauth/callback?oauth_error=server_error"
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


@router.get("/api/v1/connectors/{connector_id}/filters", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_connector_instance_filters(
    connector_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> Dict[str, Any]:
    """
    Get filter options for a connector instance.

    Args:
        connector_id: Unique instance key
        request: FastAPI request object
        graph_provider: Injected graph DB provider

    Returns:
        Dictionary with available filter options

    Raises:
        HTTPException: 400 for auth issues, 404 if instance not found
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry

    try:
        # Get and validate user context
        user_context = _get_user_context(request)

        # Get and validate connector instance
        instance = await _get_and_validate_connector_instance(
            connector_id, user_context, connector_registry, logger
        )

        connector_type = instance.get("type", "")
        await check_beta_connector_access(connector_type, request)

        # Validate permissions
        _validate_connector_permissions(
            instance, user_context["user_id"], user_context["is_admin"],
            "get filter options for"
        )

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

@router.get("/api/v1/connectors/{connector_id}/filters/{filter_key}/options", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_filter_field_options(
    connector_id: str,
    filter_key: str,
    request: Request,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search text to filter options"),
    cursor: Optional[str] = Query(None, description="Cursor for cursor-based pagination (API-specific)"),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> Dict[str, Any]:
    """
    Get dynamic options for a specific filter field with pagination support.

    This endpoint fetches the initialized connector instance from the container
    and calls its get_filter_options() method.

    Args:
        connector_id: Unique connector instance key
        filter_key: Filter field name (e.g., "space_keys", "page_ids")
        request: FastAPI request object
        page: Page number for pagination (default: 1)
        limit: Number of items per page (default: 20, max: 100)
        search: Optional search text to filter options
        graph_provider: Injected graph DB provider

    Returns:
        Dictionary with options and pagination info:
        {
            "success": True,
            "options": [{"id": "...", "value": "...", "label": "..."}],
            "pagination": {
                "page": 1,
                "limit": 20,
                "hasMore": True
            }
        }

    Raises:
        HTTPException: 400/401/403/404 for various error conditions
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry

    try:
        # Get and validate user context
        user_context = _get_user_context(request)

        # Get and validate connector instance
        instance = await _get_and_validate_connector_instance(
            connector_id, user_context, connector_registry, logger
        )

        # Check if connector is configured (has credentials)
        if instance.get("authType", "") == "OAUTH" and not instance.get("isAuthenticated", False):
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Connector is not authenticated. Please configure the connector with valid credentials first."
            )

        connector_type = instance.get("type", "")
        await check_beta_connector_access(connector_type, request)

        # Validate permissions
        _validate_connector_permissions(
            instance, user_context["user_id"], user_context["is_admin"],
            "access filter options for"
        )

        # Get connector metadata
        metadata = await connector_registry.get_connector_metadata(connector_type)
        if not metadata:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector type {connector_type} not found"
            )

        # Find filter configuration
        filter_config = _find_filter_field_config(metadata, filter_key)
        if not filter_config:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Filter field '{filter_key}' not found for connector {connector_type}"
            )

        # Check if filter supports dynamic options
        option_source_type = filter_config.get("optionSourceType", "manual")
        if option_source_type != "dynamic":
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Filter field '{filter_key}' does not support dynamic options (type: {option_source_type})"
            )

        # Get or initialize connector from container
        # get connector from container, if not found, initialize it
        connector = _get_connector_from_container(container, connector_id)
        if not connector:
            # Connector not in container, try to initialize it
            connector = await _ensure_connector_initialized(
                container=container,
                connector_id=connector_id,
                connector_type=connector_type,
                connector_registry=connector_registry,
                graph_provider=graph_provider,
                user_id=user_context["user_id"],
                org_id=user_context["org_id"],
                is_admin=user_context["is_admin"],
                logger=logger
            )

            # If still None, it means Gmail/Drive which don't support filter options via this endpoint
            if not connector:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Connector instance {connector_id} ({connector_type}) does not support filter options via this endpoint."
                )

        # Call get_filter_options method on initialized connector
        response = await connector.get_filter_options(
            filter_key=filter_key,
            page=page,
            limit=limit,
            search=search,
            cursor=cursor
        )

        # Return response as dictionary for JSON serialization
        return response.to_dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting filter field options for {filter_key}: {e}", exc_info=True)

        # Raise as HTTP 500 for proper error tracking and monitoring
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to get filter options: {str(e)}"
        )


def _get_connector_from_container(container, connector_id: str) -> Optional[BaseConnector]:
    """
    Get connector instance from app_container.
    """
    connector_key = f"{connector_id}_connector"

    if hasattr(container, connector_key):
        return getattr(container, connector_key)()
    elif hasattr(container, 'connectors_map'):
        return container.connectors_map.get(connector_id)

    return None


def _find_filter_field_config(
    metadata: Dict[str, Any],
    filter_key: str
) -> Optional[Dict[str, Any]]:
    """Find filter field configuration in connector metadata."""
    filters_config = metadata.get("config", {}).get("filters", {})

    for category in ["sync", "indexing"]:
        schema = filters_config.get(category, {}).get("schema", {})
        fields = schema.get("fields", [])

        for field in fields:
            if field.get("name") == filter_key:
                return field

    return None


@router.post("/api/v1/connectors/{connector_id}/filters", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def save_connector_instance_filters(
    connector_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> Dict[str, Any]:
    """
    Save filter selections for a connector instance.

    Args:
        connector_id: Unique instance key
        request: FastAPI request object
        graph_provider: Injected graph DB provider

    Returns:
        Dictionary with success status

    Raises:
        HTTPException: 400 if no filters provided, 404 if instance not found
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry

    try:
        # Get and validate user context
        user_context = _get_user_context(request)

        body = await request.json()
        filter_selections = body.get("filters", {})

        if not filter_selections:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="No filter selections provided"
            )

        # Get and validate connector instance
        instance = await _get_and_validate_connector_instance(
            connector_id, user_context, connector_registry, logger
        )

        connector_type = instance.get("type", "")
        await check_beta_connector_access(connector_type, request)

        # Validate permissions
        _validate_connector_permissions(
            instance, user_context["user_id"], user_context["is_admin"],
            "save filter options for"
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


async def _ensure_connector_initialized(
    container: ConnectorAppContainer,
    connector_id: str,
    connector_type: str,
    connector_registry,
    graph_provider: IGraphDBProvider,
    user_id: str,
    org_id: str,
    is_admin: bool,
    logger
) -> Optional[BaseConnector]:
    """
    Ensure connector is initialized in container. If not, initialize it.

    Args:
        container: App container
        connector_id: Connector instance ID
        connector_type: Connector type (e.g., "ONEDRIVE", "SHAREPOINT ONLINE")
        connector_registry: Connector registry instance
        graph_provider: Graph DB provider
        user_id: User ID
        org_id: Organization ID
        is_admin: Whether user is admin
        logger: Logger instance

    Returns:
        BaseConnector instance if successful, None if Gmail/Drive (they use event-based init)

    Raises:
        HTTPException: If initialization fails
    """
    # Check if connector already exists in container
    connector_exists = (
        hasattr(container, 'connectors_map') and
        connector_id in container.connectors_map
    )

    if connector_exists:
        logger.info(f"Connector {connector_id} already initialized and stored in container")
        return container.connectors_map.get(connector_id)

    # Initialize connector
    logger.info(f"Initializing connector {connector_id} before use")
    try:
        config_service = container.config_service()
        # Create data_store manually using already-resolved graph_provider to avoid coroutine reuse
        from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
        data_store_provider = GraphDataStore(logger, graph_provider)

        connector_type = connector_type.replace(" ", "").lower()

        # Create connector using factory
        connector = await ConnectorFactory.create_connector(
            name=connector_type,
            logger=logger,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id
        )

        if not connector:
            logger.error(f"Failed to create {connector_type} connector")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to create connector instance. Please check your configuration."
            )

        # Initialize connector
        logger.info(f"Calling init() for connector {connector_id}")
        is_initialized = await connector.init()

        if not is_initialized:
            error_msg = "Failed to initialize connector. Please check your credentials and configuration."
            logger.error(f"❌ {error_msg}")
            # Cleanup on failure
            try:
                await connector.cleanup()
            except Exception:
                pass
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=error_msg
            )

        # Test connection
        logger.info(f"Testing connection for connector {connector_id}")
        try:
            connection_ok = await connector.test_connection_and_access()
            if not connection_ok:
                error_msg = "Connection test failed. Please verify your credentials have proper access."
                logger.error(f"❌ {error_msg}")
                # Cleanup on failure
                try:
                    await connector.cleanup()
                except Exception:
                    pass
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=error_msg
                )
        except HTTPException:
            raise
        except Exception as test_error:
            error_msg = f"Connection test failed: {str(test_error)}"
            logger.error(f"❌ {error_msg}", exc_info=True)
            # Cleanup on failure
            try:
                await connector.cleanup()
            except Exception:
                pass
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=error_msg
            )

        # Success! Store connector in container
        logger.info(f"✅ Successfully initialized and tested {connector_type} connector")
        if not hasattr(container, 'connectors_map'):
            container.connectors_map = {}
        container.connectors_map[connector_id] = connector

        # Update isAuthenticated flag
        await connector_registry.update_connector_instance(
            connector_id=connector_id,
            updates={"isAuthenticated": True},
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin
        )

        return connector

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to initialize connector: {str(e)}"
        logger.error(f"❌ {error_msg}", exc_info=True)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=error_msg
        )


# ============================================================================
# Connector Toggle Endpoint
# ============================================================================

@router.post("/api/v1/connectors/{connector_id}/toggle", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_SYNC))])
async def toggle_connector_instance(
    connector_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider)
) -> Dict[str, Any]:
    """
    Toggle connector instance active status and trigger sync events.

    Args:
        connector_id: Unique instance key
        request: FastAPI request object
        graph_provider: Injected graph DB provider

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
        org = await graph_provider.get_document(
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

        current_sync_status = instance["isActive"]
        current_agent_status = instance.get("isAgentActive", False)
        connector_type = instance.get("type", "").upper()

        await check_beta_connector_access(connector_type, request)

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
                connector_type in ["GMAIL", "GMAIL WORKSPACE", "DRIVE", "DRIVE WORKSPACE", "GCS"] and
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

            # Initialize connector when enabling (if not already initialized)
            await _ensure_connector_initialized(
                container=container,
                connector_id=connector_id,
                connector_type=connector_type,
                connector_registry=connector_registry,
                graph_provider=graph_provider,
                user_id=user_id,
                org_id=org_id,
                is_admin=is_admin,
                logger=logger
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


@router.delete("/api/v1/connectors/{connector_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))])
async def delete_connector_instance(
    connector_id: str,
    request: Request,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> Dict[str, Any]:
    """
    Delete a connector instance and all its related data.

    This is a destructive operation that:
    1. Stops any active sync services for this connector
    2. Deletes all records, record groups, roles, groups, drives for this connector
    3. Deletes all edges (permissions, relations, classifications)
    4. Publishes event to delete embeddings from Qdrant
    5. Deletes the connector app itself
    6. Cleans up connector credentials from configuration store

    Classification nodes (departments, categories, topics, languages) are NOT deleted
    as they are shared resources across connectors.
    Users are NOT deleted - only userAppRelation edges are removed.

    Args:
        connector_id: Unique connector instance key
        request: FastAPI request object
        graph_provider: Injected graph DB provider

    Returns:
        Dictionary with deletion statistics

    Raises:
        HTTPException: 401 if not authenticated, 403 if not authorized,
                      404 if connector not found, 500 for internal errors
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry

    try:
        # 1. Get and validate user context
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        is_admin = request.headers.get("X-Is-Admin", "false").lower() == "true"

        if not user_id or not org_id:
            logger.error("User not authenticated for connector deletion")
            raise HTTPException(
                status_code=HttpStatusCode.UNAUTHORIZED.value,
                detail="User not authenticated"
            )

        # 2. Get and validate connector instance
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

        connector_type = instance.get("type", "")

        # Check beta connector access
        await check_beta_connector_access(connector_type, request)

        # 3. Check permissions - only creator or admin can delete
        _validate_connector_deletion_permissions(instance, user_id, is_admin, logger)

        logger.info(f"🗑️ Starting deletion of connector instance {connector_id} by user {user_id}")

        # 4. Stop any active sync services for this connector (send appDisabled event)
        try:
            producer = container.messaging_producer
            disable_payload = {
                "orgId": org_id,
                "appGroup": instance.get("appGroup"),
                "appGroupId": instance.get("appGroupId"),
                "connectorId": connector_id,
                "apps": [connector_type.replace(" ", "").lower()],  # Normalize for appDisabled event
                "scope": instance.get("scope")
            }
            disable_message = {
                "eventType": "appDisabled",
                "payload": disable_payload,
                "timestamp": get_epoch_timestamp_in_ms()
            }
            await producer.send_message(topic="entity-events", message=disable_message)
            logger.info(f"✅ Sent appDisabled event for connector {connector_id}")
        except Exception as e:
            logger.error(
                f"❌ Failed to send appDisabled event for connector {connector_id}: {e}. "
                f"This is critical - sync services may continue running. Manual intervention may be required. "
                f"Continuing with data deletion..."
            )
            # Continue with deletion - data cleanup is more important than event publishing
            # Manual cleanup of sync services may be needed if this event fails

        # 5. Delete from graph database (returns records for Qdrant cleanup)
        deletion_result = await graph_provider.delete_connector_instance(
            connector_id=connector_id,
            org_id=org_id
        )

        if not deletion_result.get("success"):
            error_msg = deletion_result.get("error", "Unknown error occurred during deletion")
            logger.error(f"Failed to delete connector data: {error_msg}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Failed to delete connector data: {error_msg}"
            )

        # 6. Publish BULK_DELETE_RECORDS event to Kafka for Qdrant cleanup
        virtual_record_ids = deletion_result.get("virtual_record_ids", [])
        if virtual_record_ids:
            try:
                bulk_delete_payload = {
                    "orgId": org_id,
                    "connectorId": connector_id,
                    "virtualRecordIds": virtual_record_ids,
                    "totalRecords": len(virtual_record_ids)
                }
                bulk_delete_message = {
                    "eventType": EventTypes.BULK_DELETE_RECORDS.value,
                    "payload": bulk_delete_payload,
                    "timestamp": get_epoch_timestamp_in_ms()
                }

                # Publish bulk delete event using messaging producer for consistency
                await producer.send_message(topic="record-events", message=bulk_delete_message)
                logger.info(f"✅ Published bulk delete event for {len(virtual_record_ids)} records")
            except Exception as e:
                logger.error(
                    f"❌ Failed to publish bulk delete event for connector {connector_id}: {e}. "
                    f"This is critical - embeddings may persist in Qdrant. Manual cleanup may be required. "
                    f"Continuing with deletion completion..."
                )
                # Continue with deletion - ArangoDB cleanup is complete
                # Qdrant cleanup can be triggered manually if needed

        # 7. Clean up connector credentials from etcd/config store
        try:
            config_service = container.config_service()
            config_path = _get_config_path_for_instance(connector_id)
            await config_service.delete_config(config_path)
            logger.info(f"✅ Deleted config for connector {connector_id}")
        except Exception as e:
            logger.error(
                f"❌ Failed to delete config for connector {connector_id}: {e}. "
                f"Orphaned configuration may remain in etcd."
            )
            # Don't re-raise - config cleanup failure is less critical than data deletion

        logger.info(
            f"✅ Successfully deleted connector instance {connector_id}. "
            f"Records: {deletion_result.get('deleted_records_count', 0)}, "
            f"Embeddings queued for deletion: {len(virtual_record_ids)}"
        )

        return {
            "success": True,
            "message": f"Connector instance {connector_id} deleted successfully",
            "deletedRecords": deletion_result.get("deleted_records_count", 0),
            "deletedRecordGroups": deletion_result.get("deleted_record_groups_count", 0),
            "deletedRoles": deletion_result.get("deleted_roles_count", 0),
            "deletedGroups": deletion_result.get("deleted_groups_count", 0),
            "deletedDrives": deletion_result.get("deleted_drives_count", 0),
            "embeddingsQueuedForDeletion": len(virtual_record_ids)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to delete connector instance {connector_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to delete connector instance. Please try again."
        )


# ============================================================================
# Schema Endpoint
# ============================================================================

def _clean_schema_for_response(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clean schema response by removing internal/redundant fields.

    Removes:
    - _oauth_configs: Internal OAuth config storage (not needed in schema)
    - Top-level OAuth fields in auth (authorizeUrl, tokenUrl, scopes, oauthConfigs)
      These are redundant - OAuth configs are fetched separately from OAuth config registry
      when needed (using oauth_config_id stored in etcd)

    Keeps:
    - redirectUri and displayRedirectUri in auth type's schema (needed for form display)
    - All schema fields and structure needed for form rendering
    - supportedAuthTypes (needed for auth type selection)

    Args:
        schema: Raw schema from metadata

    Returns:
        Cleaned schema without redundant fields
    """
    import copy
    cleaned = copy.deepcopy(schema)

    # Remove internal OAuth configs storage
    cleaned.pop("_oauth_configs", None)

    # Clean auth section
    if "auth" in cleaned:
        auth = cleaned["auth"]

        # Remove top-level OAuth fields (these are redundant)
        # OAuth configs are fetched from OAuth config registry when needed
        # using the oauth_config_id stored in etcd
        auth.pop("authorizeUrl", None)
        auth.pop("tokenUrl", None)
        auth.pop("scopes", None)
        auth.pop("oauthConfigs", None)

        # Keep redirectUri and displayRedirectUri at top level for backward compatibility
        # but they should primarily come from the auth type's schema
        # The auth type's schema is the source of truth for redirectUri

    return cleaned


@router.get("/api/v1/connectors/registry/{connector_type}/schema", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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
        Dictionary with connector schema (cleaned of redundant fields)

    Raises:
        HTTPException: 404 if connector type not found
    """
    container = request.app.container
    logger = container.logger()
    connector_registry = request.app.state.connector_registry
    logger.info("Getting connector schema")
    try:
        await check_beta_connector_access(connector_type, request)
        metadata = await connector_registry.get_connector_metadata(connector_type)
        if not metadata:
            logger.error(f"Connector type {connector_type} not found")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Connector type {connector_type} not found"
            )

        raw_schema = metadata.get("config", {})
        cleaned_schema = _clean_schema_for_response(raw_schema)

        return {
            "success": True,
            "schema": cleaned_schema
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting schema for {connector_type}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to get connector schema: {str(e)}"
        )

@router.get("/api/v1/connectors/agents/active", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
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


# ============================================================================
# OAuth Config Management Endpoints
# ============================================================================

@router.get("/api/v1/oauth/registry", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_oauth_config_registry(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None, description="Search by name/group/description"),
) -> Dict[str, Any]:
    """
    Get all available connector/toolset types that have OAuth configurations registered.

    This endpoint returns connector/toolset types from the OAuth config registry that can be used
    to create OAuth config instances. It includes auth schema fields needed for creating OAuth configs.

    Args:
        request: FastAPI request object
        page: Page number (1-indexed)
        limit: Number of items per page
        search: Optional search query

    Returns:
        Dictionary with success status and list of available OAuth-enabled connectors/toolsets
        Each connector includes auth schema fields for OAuth configuration

    Raises:
        HTTPException: 500 if error occurs
    """
    container = request.app.container
    logger = container.logger()

    try:
        from app.connectors.core.registry.oauth_config_registry import (
            get_oauth_config_registry,
        )

        # Get OAuth config registry (completely independent, no connector registry required)
        oauth_registry = get_oauth_config_registry()

        # Get paginated and filtered connectors from registry
        # This uses only OAuth config metadata and auth fields, making it generic and independent
        result = await oauth_registry.get_oauth_config_registry_connectors(
            page=page,
            limit=limit,
            search=search
        )

        return {
            "success": True,
            **result
        }

    except Exception as e:
        logger.error(f"Error getting OAuth config registry: {str(e)}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Error getting OAuth config registry: {str(e)}"
        )


@router.get("/api/v1/oauth/registry/{connector_type}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_oauth_config_registry_by_type(
    connector_type: str,
    request: Request,
) -> Dict[str, Any]:
    """
    Get OAuth registry information for a specific connector type.

    This endpoint returns the OAuth configuration details for a single connector type,
    including auth fields, documentation links, and other metadata. This is more efficient
    than fetching the entire registry when you only need one connector's information.

    Args:
        connector_type: Type of connector (e.g., "Gmail", "Drive")
        request: FastAPI request object

    Returns:
        Dictionary with success status and connector registry information

    Raises:
        HTTPException: 404 if connector type not found, 500 if error occurs
    """
    container = request.app.container
    logger = container.logger()

    try:
        from app.connectors.core.registry.oauth_config_registry import (
            get_oauth_config_registry,
        )

        # Get OAuth config registry (completely independent, no connector registry required)
        oauth_registry = get_oauth_config_registry()

        # Get connector registry info for the specific connector type
        connector_info = oauth_registry.get_connector_registry_info(connector_type)

        if not connector_info:
            logger.warning(f"OAuth config registry info not found for connector type: {connector_type}")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"OAuth configuration not found for connector type: {connector_type}"
            )

        return {
            "success": True,
            "connector": connector_info
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting OAuth config registry for {connector_type}: {str(e)}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Error getting OAuth config registry: {str(e)}"
        )


@router.get("/api/v1/oauth", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_all_oauth_configs(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None, description="Search by instance name/group/description"),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Get all OAuth configurations across all connector types with pagination and search.

    Optimized implementation:
    - Parallel etcd fetches using asyncio.gather
    - Early filtering by org_id during extraction
    - Efficient list comprehension for search
    - Optimized sorting with tuple key

    Args:
        request: FastAPI request object
        page: Page number (1-indexed)
        limit: Number of items per page
        search: Optional search query
        config_service: Injected configuration service

    Returns:
        Dictionary with list of OAuth configs and pagination info
    """
    container = request.app.container
    logger = container.logger()

    try:
        # Get and validate user context
        user_context = _get_user_context(request)

        # Get all connector types that support OAuth from registry (completely independent)
        from app.connectors.core.registry.oauth_config_registry import (
            get_oauth_config_registry,
        )
        oauth_registry = get_oauth_config_registry()

        # Get all OAuth-enabled connector types
        oauth_connector_names = oauth_registry.get_oauth_connectors()

        if not oauth_connector_names:
            return {
                "success": True,
                "oauthConfigs": [],
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "search": search,
                    "totalItems": 0,
                    "totalPages": 0,
                    "hasNext": False,
                    "hasPrev": False
                }
            }

        # Fetch OAuth configs for all connector types in PARALLEL
        # This is the key optimization - all etcd calls happen concurrently
        async def fetch_configs_for_type(connector_type: str) -> List[Dict[str, Any]]:
            """Fetch and filter configs for a single connector type"""
            try:
                oauth_configs = await _get_oauth_configs_from_etcd(connector_type, config_service)
                # Filter by org and extract fields in one pass
                filtered_configs = []
                for config in oauth_configs:
                    if config.get("orgId") == user_context["org_id"]:
                        config_info = _extract_essential_oauth_fields(config, connector_type)
                        filtered_configs.append(config_info)
                return filtered_configs
            except Exception as e:
                logger.warning(f"Error fetching OAuth configs for {connector_type}: {e}")
                return []

        # Execute all etcd fetches in parallel
        results = await asyncio.gather(*[
            fetch_configs_for_type(connector_type)
            for connector_type in oauth_connector_names
        ], return_exceptions=True)

        # Flatten results efficiently - filter out exceptions and extend in one pass
        all_configs = []
        for result in results:
            if isinstance(result, list):
                all_configs.extend(result)
            elif isinstance(result, Exception):
                logger.warning(f"Error in parallel fetch: {result}", exc_info=result)

        # Apply search filter if provided (optimized single-pass filter)
        if search:
            search_lower = search.lower()
            all_configs = [
                config for config in all_configs
                if (
                    search_lower in (config.get("oauthInstanceName") or "").lower() or
                    search_lower in (config.get("appGroup") or "").lower() or
                    search_lower in (config.get("appDescription") or "").lower() or
                    search_lower in (config.get("connectorType") or "").lower() or
                    any(search_lower in (cat or "").lower() for cat in (config.get("appCategories") or []))
                )
            ]

        # Sort by updated timestamp (newest first) - single pass with tuple key
        all_configs.sort(
            key=lambda x: (
                -(x.get("updatedAtTimestamp") or x.get("createdAtTimestamp") or 0),
                (x.get("oauthInstanceName") or "").lower()
            )
        )

        # Apply pagination
        total_items = len(all_configs)
        total_pages = (total_items + limit - 1) // limit
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_configs = all_configs[start_idx:end_idx]

        return {
            "success": True,
            "oauthConfigs": paginated_configs,
            "pagination": {
                "page": page,
                "limit": limit,
                "search": search,
                "totalItems": total_items,
                "totalPages": total_pages,
                "hasNext": page < total_pages,
                "hasPrev": page > 1
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting all OAuth configs: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to get all OAuth configurations: {str(e)}"
        )


def _get_oauth_config_path(connector_type: str) -> str:
    """
    Get etcd configuration path for OAuth configs of a connector type.

    Args:
        connector_type: Type of connector (e.g., "GOOGLE_DRIVE", "SLACK")

    Returns:
        Configuration path in etcd
    """
    normalized_type = connector_type.lower().replace(" ", "")
    return f"/services/oauth/{normalized_type}"


def _generate_oauth_config_id() -> str:
    """Generate a unique ID for OAuth config"""
    import uuid
    return str(uuid.uuid4())


def _get_oauth_field_names_from_registry(connector_type: str) -> List[str]:
    """
    Get OAuth field names from the OAuth config registry for a connector type.
    This makes the code generic and maintainable - no hardcoded field names.

    Args:
        connector_type: Type of connector

    Returns:
        List of OAuth field names (e.g., ["clientId", "clientSecret", "domain", ...])
    """
    try:
        from app.connectors.core.registry.oauth_config_registry import (
            get_oauth_config_registry,
        )

        oauth_registry = get_oauth_config_registry()
        oauth_config = oauth_registry.get_config(connector_type)

        if not oauth_config or not oauth_config.auth_fields:
            # Return default/common OAuth fields as fallback
            return ["clientId", "clientSecret"]

        # Extract field names from auth_fields
        field_names = [field.name for field in oauth_config.auth_fields]
        return field_names
    except Exception:
        # Fallback to common OAuth fields if registry lookup fails
        return ["clientId", "clientSecret"]


async def _create_or_update_oauth_config(
    connector_type: str,
    auth_config: Dict[str, Any],
    instance_name: str,
    user_id: str,
    org_id: str,
    is_admin: bool,
    config_service: ConfigurationService,
    base_url: str,
    oauth_app_id: Optional[str] = None,
    logger = None
) -> Optional[str]:
    """
    Create or update an OAuth config based on auth_config fields.
    This is a reusable function that extracts OAuth fields dynamically from the registry.

    Args:
        connector_type: Type of connector
        auth_config: Auth configuration dictionary with OAuth fields
        instance_name: Name for the OAuth config instance
        user_id: User ID creating/updating the config
        org_id: Organization ID
        is_admin: Whether user is admin
        config_service: Configuration service instance
        base_url: Base URL for OAuth redirects
        oauth_app_id: Optional existing OAuth app ID to update
        logger: Optional logger instance

    Returns:
        OAuth config ID if created/updated successfully, None otherwise
    """
    if logger is None:
        import logging
        logger = logging.getLogger(__name__)

    try:
        # Get OAuth field names from registry (dynamic, no hardcoding)
        oauth_field_names = _get_oauth_field_names_from_registry(connector_type)

        # Get OAuth configs for this connector type
        oauth_config_path = _get_oauth_config_path(connector_type)
        oauth_configs = await config_service.get_config(oauth_config_path, default=[])

        if not isinstance(oauth_configs, list):
            oauth_configs = []

        if oauth_app_id:
            # Update existing OAuth config (when user selects existing OAuth app and provides credentials)
            # This allows overriding/updating existing OAuth config credentials
            oauth_config = None
            for idx, oauth_cfg in enumerate(oauth_configs):
                if oauth_cfg.get("_id") == oauth_app_id:
                    # Check permissions
                    oauth_user_id = oauth_cfg.get("userId")
                    oauth_org_id = oauth_cfg.get("orgId")
                    if (is_admin and oauth_org_id == org_id) or (oauth_user_id == user_id and oauth_org_id == org_id):
                        # Update the config with new credentials from form
                        if "config" not in oauth_cfg:
                            oauth_cfg["config"] = {}

                        # Ensure OAuth infrastructure fields are present (if missing, add from registry)
                        await _update_oauth_infrastructure_fields(oauth_cfg, connector_type, config_service, base_url)

                        # Update all OAuth credential fields dynamically from auth_config
                        # This allows overriding existing OAuth config credentials
                        for field_name in oauth_field_names:
                            # Try both camelCase and snake_case variants
                            value = auth_config.get(field_name) or auth_config.get(
                                field_name.replace("Id", "_id").replace("Secret", "_secret")
                            )
                            # Update field if value is provided (including empty strings for clearing)
                            if value is not None:
                                oauth_cfg["config"][field_name] = value
                            # If value is None and field exists, keep existing value

                        oauth_cfg["updatedAtTimestamp"] = get_epoch_timestamp_in_ms()
                        oauth_configs[idx] = oauth_cfg
                        oauth_config = oauth_cfg
                        logger.info(f"Updated existing OAuth config for connector {connector_type}")
                        break

            if not oauth_config:
                logger.warning(f"OAuth config not found or access denied for {connector_type}, will create new one if credentials provided")
                oauth_app_id = None  # Will create new one below if credentials are provided

        if not oauth_app_id:
            # Create new OAuth config
            logger.info(f"Auto-creating OAuth config for connector {connector_type}")

            new_oauth_config = {
                "_id": _generate_oauth_config_id(),
                "oauthInstanceName": instance_name,
                "connectorType": connector_type,
                "userId": user_id,
                "orgId": org_id,
                "config": {},
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Store OAuth infrastructure fields from registry (needed for OAuth flow)
            await _update_oauth_infrastructure_fields(new_oauth_config, connector_type, config_service, base_url)

            # Populate all OAuth credential fields dynamically from auth_config
            for field_name in oauth_field_names:
                # Try both camelCase and snake_case variants
                value = auth_config.get(field_name) or auth_config.get(
                    field_name.replace("Id", "_id").replace("Secret", "_secret")
                )
                if value is not None:
                    new_oauth_config["config"][field_name] = value

            oauth_configs.append(new_oauth_config)
            oauth_app_id = new_oauth_config["_id"]
            logger.info(f"Created new OAuth config for connector {connector_type}")

        # Save OAuth configs
        await config_service.set_config(oauth_config_path, oauth_configs)
        logger.info(f"Successfully saved OAuth config for connector {connector_type}")
        return oauth_app_id

    except Exception as e:
        logger.error(f"Error creating/updating OAuth config: {e}", exc_info=True)
        return None


async def _get_oauth_configs_from_etcd(
    connector_type: str,
    config_service: ConfigurationService
) -> List[Dict[str, Any]]:
    """
    Get OAuth configs from etcd for a connector type.
    Optimized to use cache when available.

    Args:
        connector_type: Type of connector
        config_service: Configuration service instance

    Returns:
        List of OAuth configs (empty list if none found)
    """
    config_path = _get_oauth_config_path(connector_type)
    # Use cache for faster retrieval (cache is enabled by default)
    oauth_configs = await config_service.get_config(config_path, default=[], use_cache=True)
    return oauth_configs if isinstance(oauth_configs, list) else []


def _extract_essential_oauth_fields(oauth_config: Dict[str, Any], connector_type: str) -> Dict[str, Any]:
    """
    Extract only essential, non-sensitive fields from an OAuth config.
    Returns camelCase for frontend consistency.

    Args:
        oauth_config: Full OAuth config dictionary from etcd
        connector_type: Type of connector (fallback if not in config)

    Returns:
        Dictionary with only essential fields in camelCase
    """
    return {
        "_id": oauth_config.get("_id"),
        "oauthInstanceName": oauth_config.get("oauthInstanceName"),  # camelCase for frontend
        "iconPath": oauth_config.get("iconPath", "/assets/icons/connectors/default.svg"),
        "appGroup": oauth_config.get("appGroup", ""),
        "appDescription": oauth_config.get("appDescription", ""),
        "appCategories": oauth_config.get("appCategories", []),
        "connectorType": oauth_config.get("connectorType", connector_type),
        "createdAtTimestamp": oauth_config.get("createdAtTimestamp"),
        "updatedAtTimestamp": oauth_config.get("updatedAtTimestamp")
    }


def _find_oauth_config_by_id(
    oauth_configs: List[Dict[str, Any]],
    config_id: str,
    org_id: str
) -> Optional[Dict[str, Any]]:
    """
    Find an OAuth config by ID within the same organization.

    Args:
        oauth_configs: List of OAuth configs
        config_id: Config ID to find
        org_id: Organization ID for filtering

    Returns:
        OAuth config if found and accessible, None otherwise
    """
    for config in oauth_configs:
        if config.get("_id") == config_id:
            config_org_id = config.get("orgId")
            if config_org_id == org_id:
                return config
    return None


@router.post("/api/v1/oauth/{connector_type}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def create_oauth_config(
    connector_type: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Create a new OAuth configuration for a connector type (Admin only).

    Request body should contain:
    - oauthInstanceName: Name for this OAuth config instance
    - config: Dictionary containing all auth fields (client_id, client_secret, etc.)

    Args:
        connector_type: Type of connector (e.g., "GOOGLE_DRIVE", "SLACK")
        request: FastAPI request object
        config_service: Injected configuration service

    Returns:
        Dictionary with created OAuth config details (essential fields only)

    Raises:
        HTTPException: 400 for invalid data, 403 if not admin, 409 if name already exists
    """
    container = request.app.container
    logger = container.logger()

    try:
        # Get and validate user context (admin only)
        user_context = _get_user_context(request)
        _validate_admin_only(user_context["is_admin"], "create OAuth configurations")

        body = await request.json()
        oauth_instance_name = (body.get("oauthInstanceName") or "").strip()
        config = body.get("config", {})
        base_url = body.get("baseUrl", "")

        if not oauth_instance_name:
            logger.error("oauthInstanceName is required")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="oauthInstanceName is required"
            )

        if not config:
            logger.error("config is required")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="config is required"
            )

        # Get OAuth config from registry (completely independent)
        # OAuth configs are self-contained and don't depend on connector/toolset registries
        from app.connectors.core.registry.oauth_config_registry import (
            get_oauth_config_registry,
        )
        oauth_registry = get_oauth_config_registry()

        # Get metadata
        metadata = oauth_registry.get_metadata(connector_type)
        icon_path = metadata.get("iconPath", "/assets/icons/connectors/default.svg")
        app_group = metadata.get("appGroup", "")
        app_description = metadata.get("appDescription", "")
        app_categories = metadata.get("appCategories", [])

        # Get existing OAuth configs for this connector type
        existing_configs = await _get_oauth_configs_from_etcd(connector_type, config_service)

        # Check if name already exists (within same org)
        _check_oauth_name_conflict(
            existing_configs, oauth_instance_name, user_context["org_id"]
        )

        # Create new OAuth config
        new_config = {
            "_id": _generate_oauth_config_id(),
            "oauthInstanceName": oauth_instance_name,
            "userId": user_context["user_id"],
            "orgId": user_context["org_id"],
            "config": config,  # Full config with sensitive fields
            "iconPath": icon_path,
            "appGroup": app_group,
            "appDescription": app_description,
            "appCategories": app_categories,
            "connectorType": connector_type,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            "createdBy": user_context["user_id"],
            "updatedBy": user_context["user_id"]
        }

        # Store OAuth infrastructure fields from registry (needed for OAuth flow)
        await _update_oauth_infrastructure_fields(new_config, connector_type, config_service, base_url)

        # Add to existing configs
        existing_configs.append(new_config)

        # Save to etcd
        config_path = _get_oauth_config_path(connector_type)
        success = await config_service.set_config(config_path, existing_configs)

        if not success:
            logger.error(f"Failed to save OAuth config for {connector_type}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to save OAuth configuration"
            )

        logger.info(f"Created OAuth config '{oauth_instance_name}' for {connector_type}")

        # Return only essential fields (no sensitive config data) - camelCase for frontend
        return {
            "success": True,
            "oauthConfig": _extract_essential_oauth_fields(new_config, connector_type),
            "message": "OAuth configuration created successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating OAuth config for {connector_type}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to create OAuth configuration: {str(e)}"
        )


@router.get("/api/v1/oauth/{connector_type}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def list_oauth_configs(
    connector_type: str,
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None, description="Search by instance name/group/description"),
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    List all OAuth configurations for a connector type with pagination and search.

    Args:
        connector_type: Type of connector
        request: FastAPI request object
        page: Page number (1-indexed)
        limit: Number of items per page
        search: Optional search query
        config_service: Injected configuration service

    Returns:
        Dictionary with list of OAuth configs and pagination info

    Security & Performance:
        - Admins automatically receive full config details (includes credentials)
        - Non-admins receive only essential fields (credentials excluded)
        - Decision is made server-side based on authentication, not client request
        - This eliminates the need for a second API call for admins, improving performance
    """
    container = request.app.container
    logger = container.logger()

    try:
        # Get and validate user context (from authentication headers, not query params!)
        user_context = _get_user_context(request)

        # Get OAuth configs for this connector type
        oauth_configs = await _get_oauth_configs_from_etcd(connector_type, config_service)

        # Get OAuth config registry and use its pagination/search logic (completely independent)
        from app.connectors.core.registry.oauth_config_registry import (
            get_oauth_config_registry,
        )
        oauth_registry = get_oauth_config_registry()

        # Security: Backend automatically decides what data to return based on user role
        # Admins get full config (performance optimization - no second API call needed)
        # Non-admins get only essential fields (credentials are always excluded)
        result = await oauth_registry.get_oauth_configs_for_connector(
            connector_type=connector_type,
            oauth_configs=oauth_configs,
            org_id=user_context["org_id"],
            page=page,
            limit=limit,
            search=search,
            include_full_config=True,  # Always true - registry method filters based on is_admin
            is_admin=user_context["is_admin"]  # Server-side authorization check
        )

        # Return camelCase for frontend consistency
        return {
            "success": True,
            "oauthConfigs": result.get("oauthConfigs", []),
            "pagination": result.get("pagination", {})
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing OAuth configs for {connector_type}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to list OAuth configurations: {str(e)}"
        )


@router.get("/api/v1/oauth/{connector_type}/{config_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
@inject
async def get_oauth_config_by_id(
    connector_type: str,
    config_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Get a specific OAuth configuration by ID.

    For admins: Returns full config including sensitive fields (clientId, clientSecret, etc.)
    For regular users: Returns only essential metadata (no sensitive config data)

    Args:
        connector_type: Type of connector
        config_id: OAuth config ID
        request: FastAPI request object
        config_service: Injected configuration service

    Returns:
        Dictionary with OAuth config details
        - For admins: Includes full config with sensitive fields
        - For regular users: Only essential metadata

    Raises:
        HTTPException: 404 if config not found
    """
    container = request.app.container
    logger = container.logger()

    try:
        # Get and validate user context
        user_context = _get_user_context(request)

        # Get OAuth configs for this connector type
        oauth_configs = await _get_oauth_configs_from_etcd(connector_type, config_service)

        # Find the config with matching ID (all users in org can view)
        oauth_config, _ = await _find_oauth_config_in_list(
            oauth_configs, config_id, user_context["org_id"], logger
        )

        if not oauth_config:
            logger.error(f"OAuth config {config_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"OAuth config {config_id} not found or access denied"
            )

        logger.info(f"oauth_config: {oauth_config}")

        # For admins: return full config including sensitive fields (camelCase for frontend)
        if user_context["is_admin"]:
            return {
                "success": True,
                "oauthConfig": {
                    "_id": oauth_config.get("_id"),
                    "oauthInstanceName": oauth_config.get("oauthInstanceName"),  # camelCase
                    "iconPath": oauth_config.get("iconPath", "/assets/icons/connectors/default.svg"),
                    "appGroup": oauth_config.get("appGroup", ""),
                    "appDescription": oauth_config.get("appDescription", ""),
                    "appCategories": oauth_config.get("appCategories", []),
                    "connectorType": oauth_config.get("connectorType", connector_type),
                    "createdAtTimestamp": oauth_config.get("createdAtTimestamp"),
                    "updatedAtTimestamp": oauth_config.get("updatedAtTimestamp"),
                    "config": oauth_config.get("config", {})  # Include full config with sensitive fields
                }
            }

        # For regular users: return only essential fields (no sensitive config data)
        return {
            "success": True,
            "oauthConfig": _extract_essential_oauth_fields(oauth_config, connector_type)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting OAuth config {config_id} for {connector_type}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to get OAuth configuration: {str(e)}"
        )


@router.put("/api/v1/oauth/{connector_type}/{config_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
@inject
async def update_oauth_config(
    connector_type: str,
    config_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Update an existing OAuth configuration.

    Request body can contain:
    - oauthInstanceName: Updated name (optional)
    - config: Updated config dictionary (optional)

    Args:
        connector_type: Type of connector
        config_id: OAuth config ID
        request: FastAPI request object
        config_service: Injected configuration service

    Returns:
        Dictionary with updated OAuth config details

    Raises:
        HTTPException: 404 if config not found, 400 for invalid data
    """
    container = request.app.container
    logger = container.logger()

    try:
        # Get and validate user context (admin only)
        user_context = _get_user_context(request)
        _validate_admin_only(user_context["is_admin"], "update OAuth configurations")

        body = await request.json()
        new_name = body.get("oauthInstanceName")
        new_config = body.get("config")
        base_url = body.get("baseUrl", "")

        # Get OAuth configs for this connector type
        oauth_configs = await _get_oauth_configs_from_etcd(connector_type, config_service)

        # Find the config with matching ID (admin can update any config in their org)
        oauth_config, config_index = await _find_oauth_config_in_list(
            oauth_configs, config_id, user_context["org_id"], logger
        )

        if not oauth_config or config_index is None:
            logger.error(f"OAuth config {config_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"OAuth config {config_id} not found or access denied"
            )

        # Check if new name conflicts with existing configs (within same org)
        if new_name and new_name.strip() != oauth_config.get("oauthInstanceName"):
            new_name = new_name.strip()
            _check_oauth_name_conflict(
                oauth_configs, new_name, user_context["org_id"], exclude_index=config_index
            )

        # Update config
        if new_name:
            oauth_config["oauthInstanceName"] = new_name.strip()
        if new_config:
            oauth_config["config"] = new_config

        # Ensure OAuth infrastructure fields are present (if missing, add from registry)
        await _update_oauth_infrastructure_fields(oauth_config, connector_type, config_service, base_url)

        oauth_config["updatedAtTimestamp"] = get_epoch_timestamp_in_ms()
        oauth_config["updatedBy"] = user_context["user_id"]

        # Update in array
        oauth_configs[config_index] = oauth_config

        # Save to etcd
        config_path = _get_oauth_config_path(connector_type)
        success = await config_service.set_config(config_path, oauth_configs)

        if not success:
            logger.error(f"Failed to update OAuth config {config_id} for {connector_type}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to update OAuth configuration"
            )

        logger.info(f"Updated OAuth config {config_id} for {connector_type}")

        # Return only essential fields (no sensitive config data) - camelCase for frontend
        return {
            "success": True,
            "oauthConfig": _extract_essential_oauth_fields(oauth_config, connector_type),
            "message": "OAuth configuration updated successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating OAuth config {config_id} for {connector_type}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to update OAuth configuration: {str(e)}"
        )


@router.delete("/api/v1/oauth/{connector_type}/{config_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))])
@inject
async def delete_oauth_config(
    connector_type: str,
    config_id: str,
    request: Request,
    config_service: ConfigurationService = Depends(Provide[ConnectorAppContainer.config_service])
) -> Dict[str, Any]:
    """
    Delete an OAuth configuration.

    Args:
        connector_type: Type of connector
        config_id: OAuth config ID
        request: FastAPI request object
        config_service: Injected configuration service

    Returns:
        Dictionary with success status

    Raises:
        HTTPException: 404 if config not found
    """
    container = request.app.container
    logger = container.logger()

    try:
        # Get and validate user context (admin only)
        user_context = _get_user_context(request)
        _validate_admin_only(user_context["is_admin"], "delete OAuth configurations")

        # Get OAuth configs for this connector type
        oauth_configs = await _get_oauth_configs_from_etcd(connector_type, config_service)

        # Find and remove the config with matching ID (admin can delete any config in their org)
        oauth_config, config_index = await _find_oauth_config_in_list(
            oauth_configs, config_id, user_context["org_id"], logger
        )

        if not oauth_config or config_index is None:
            logger.error(f"OAuth config {config_id} not found or access denied")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"OAuth config {config_id} not found or access denied"
            )

        # Remove from array
        oauth_configs.pop(config_index)

        # Save to etcd (if empty array, still save it)
        config_path = _get_oauth_config_path(connector_type)
        success = await config_service.set_config(config_path, oauth_configs)

        if not success:
            logger.error(f"Failed to delete OAuth config {config_id} for {connector_type}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to delete OAuth configuration"
            )

        logger.info(f"Deleted OAuth config {config_id} for {connector_type}")

        return {
            "success": True,
            "message": f"OAuth configuration {config_id} deleted successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting OAuth config {config_id} for {connector_type}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Failed to delete OAuth configuration: {str(e)}"
        )
