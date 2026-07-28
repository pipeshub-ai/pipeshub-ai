import asyncio
import json
import time
from typing import Any, Dict, TypedDict

import aiohttp
import jwt
from yarl import URL

from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import (
    DefaultEndpoints,
    Routes,
    TokenScopes,
    config_node_constants,
)
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.request_context import inject_request_headers
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class CustomMetadataEntry(TypedDict):
    key: str
    value: Any  # NOTE: 'Any' is used here because storage metadata values may be str, int, bool, or even structured types, depending on the client and blob store requirements.

def _add_custom_metadata_to_form(
    form_data: aiohttp.FormData,
    custom_metadata: list[CustomMetadataEntry],
) -> None:
    """Append ``customMetadata`` fields for multipart storage uploads."""
    for i, meta in enumerate(custom_metadata):
        form_data.add_field(f"customMetadata[{i}][key]", meta["key"])
        value = meta["value"]
        if isinstance(value, bool):
            form_data.add_field(
                f"customMetadata[{i}][value]",
                str(value).lower(),
            )
        elif isinstance(value, str):
            form_data.add_field(f"customMetadata[{i}][value]", value)
        else:
            form_data.add_field(f"customMetadata[{i}][value]", str(value))


class BlobStorage(Transformer):
    def __init__(self,logger,config_service, graph_provider: IGraphDBProvider = None) -> None:
        self.logger = logger
        self.config_service = config_service
        self.graph_provider = graph_provider

    async def _get_auth_and_config(self, org_id: str) -> tuple[dict, str, str]:
        """
        Returns (headers, nodejs_endpoint, storage_type).
        """
        payload = {
            "orgId": org_id,
            "scopes": [TokenScopes.STORAGE_TOKEN.value],
        }
        secret_keys = await self.config_service.get_config(
            config_node_constants.SECRET_KEYS.value
        )
        scoped_jwt_secret = secret_keys.get("scopedJwtSecret")
        if not scoped_jwt_secret:
            raise ValueError("Missing scoped JWT secret")

        jwt_token = jwt.encode(payload, scoped_jwt_secret, algorithm="HS256")
        headers = inject_request_headers({"Authorization": f"Bearer {jwt_token}"})

        endpoints = await self.config_service.get_config(
            config_node_constants.ENDPOINTS.value
        )
        nodejs_endpoint = endpoints.get("cm", {}).get(
            "endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value
        )
        if not nodejs_endpoint:
            raise ValueError("Missing CM endpoint configuration")

        storage = await self.config_service.get_config(
            config_node_constants.STORAGE.value
        )
        storage_type = storage.get("storageType")
        if not storage_type:
            raise ValueError("Missing storage type configuration")

        return headers, nodejs_endpoint, storage_type

    async def _get_public_download_base_url(self) -> str:
        """Resolve the externally-reachable base URL for user-facing document links.

        The CM endpoint (``cm.endpoint``) is the *internal* Node.js URL — in a
        containerised deployment it resolves to e.g. ``http://nodejs:3000`` and
        in a vanilla local stack it may be absent entirely, falling through to
        ``http://localhost:3000``. Neither is reachable from the user's
        browser, so it must not be used as the prefix for links returned to
        the client.

        Falls back through ``frontend.publicEndpoint`` (the standard
        user-facing URL across the codebase) → ``storage.endpoint`` → the
        ``FRONTEND_ENDPOINT`` default.
        """
        endpoints = await self.config_service.get_config(
            config_node_constants.ENDPOINTS.value
        )
        return (
            endpoints.get("frontend", {}).get("publicEndpoint")
            or endpoints.get("storage", {}).get("endpoint")
            or DefaultEndpoints.FRONTEND_ENDPOINT.value
        )

    def _compress_record(self, record: dict) -> str:
        """
        Compress record data using msgspec (C-based) + zstd.
        Returns: base64_encoded_compressed_data
        """
        import base64

        import msgspec
        import zstandard as zstd

        # Serialize directly to bytes using msgspec (high-performance msgpack encoder)
        msgpack_bytes = msgspec.msgpack.encode(record)
        original_size = len(msgpack_bytes)

        # Compression level 10: maximum compression
        compressor = zstd.ZstdCompressor(level=10)
        compressed = compressor.compress(msgpack_bytes)

        compressed_size = len(compressed)
        ratio = (1 - compressed_size / original_size) * 100
        self.logger.debug("📦 Compressed record (msgspec): %d -> %d bytes (%.1f%% reduction)",
                        original_size, compressed_size, ratio)

        return base64.b64encode(compressed).decode('utf-8')



    def _decompress_bytes(self, compressed_bytes: bytes) -> bytes:
        """
        Decompress raw bytes using zstd.
        Returns decompressed bytes.
        """
        import zstandard as zstd

        decompressor = zstd.ZstdDecompressor()
        return decompressor.decompress(compressed_bytes)

    def _process_downloaded_record(self, data: dict) -> dict:
        """
        Process downloaded record data, handling decompression if needed.
        Supports new isCompressed flag format and backward compatibility with uncompressed records.
        """
        import base64

        import msgspec

        # NEW FORMAT: Check for isCompressed flag
        if data.get("isCompressed"):
            compressed_base64 = data.get("record")
            if not compressed_base64:
                self.logger.error("❌ isCompressed is true but no record found")
                raise Exception("Missing record in compressed record")

            try:
                compressed_bytes = base64.b64decode(compressed_base64)
                decompressed_bytes = self._decompress_bytes(compressed_bytes)
                record = msgspec.msgpack.decode(decompressed_bytes)
                return record

            except Exception as e:
                self.logger.error("❌ Failed to decompress record: %s", str(e))
                raise Exception(f"Decompression failed: {str(e)}")

        # OLD FORMAT: Uncompressed record
        elif data.get("record"):
            return data.get("record")

        else:
            # Unknown format
            self.logger.error("❌ Unknown record format in S3")
            raise Exception("Unknown record format")

    async def _get_content_length(self, session: aiohttp.ClientSession, url: str) -> int:
        """
        Get content length of S3 object using Range GET request to fetch only headers.

        Args:
            session: aiohttp session
            url: S3 signed URL

        Returns:
            Content length in bytes, or 0 if not available
        """
        try:
            # Use Range header to request only the first byte to avoid downloading entire file
            headers = {'Range': 'bytes=0-0'}

            async with session.get(URL(url, encoded=True), headers=headers) as response:
                # For Range requests, Content-Range header contains the total size
                # Format: "bytes 0-0/total_size"
                if response.status == HttpStatusCode.PARTIAL_CONTENT.value:  # Partial Content
                    content_range = response.headers.get('Content-Range', '')
                    if content_range and '/' in content_range:
                        total_size = content_range.split('/')[-1]
                        return int(total_size)

                # Fallback to Content-Length if available (status 200)
                content_length = response.headers.get('Content-Length', None)
                return int(content_length) if content_length else None
        except Exception as e:
            self.logger.warning("⚠️ Failed to get content length: %s", str(e))
            return None

    async def _download_chunk_with_retry(
        self,
        session: aiohttp.ClientSession,
        url: str,
        start: int,
        end: int,
        chunk_index: int,
        max_retries: int = 3
    ) -> tuple[int, bytes]:
        """
        Download a single chunk with retry logic.

        Args:
            session: aiohttp session
            url: S3 signed URL
            start: Start byte position
            end: End byte position
            chunk_index: Index of this chunk (for ordering)
            max_retries: Maximum retry attempts

        Returns:
            Tuple of (chunk_index, chunk_bytes)
        """
        for attempt in range(max_retries):
            try:
                headers = {'Range': f'bytes={start}-{end}'}

                async with session.get(URL(url, encoded=True), headers=headers) as response:
                    if response.status in (HttpStatusCode.SUCCESS.value, HttpStatusCode.PARTIAL_CONTENT.value):
                        chunk_bytes = await response.read()
                        return (chunk_index, chunk_bytes)
                    else:
                        raise aiohttp.ClientError(f"Unexpected status {response.status}")
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 0.5 * (2 ** attempt)  # Exponential backoff
                    self.logger.warning(
                        "⚠️ Chunk %d download failed (attempt %d/%d): %s. Retrying in %.1fs...",
                        chunk_index, attempt + 1, max_retries, str(e), wait_time
                    )
                    await asyncio.sleep(wait_time)
                else:
                    self.logger.exception(
                        "❌ Chunk %d download failed after %d attempts: %s",
                        chunk_index,
                        max_retries,
                        e,
                    )
                    raise


    async def _download_with_range_requests(
        self,
        session: aiohttp.ClientSession,
        signed_url: str,
        chunk_size_mb: int = 2,
        max_connections: int = 6
    ) -> bytes:
        """
        Download file in parallel chunks using HTTP Range requests.

        Args:
            session: aiohttp session
            signed_url: S3 signed URL
            chunk_size_mb: Size of each chunk in MB (default: 8MB)
            max_connections: Max parallel connections (default: 6)

        Returns:
            Complete file bytes

        Raises:
            Exception: If download fails or range requests not supported
        """
        download_start_time = time.time()

        # Get total file size
        total_size = await self._get_content_length(session, signed_url)

        if total_size is None or total_size == 0:
            raise Exception("Could not determine file size for parallel download")

        # Calculate chunk ranges
        chunk_size_bytes = chunk_size_mb * 1024 * 1024
        chunks = []
        for i in range(0, total_size, chunk_size_bytes):
            start = i
            end = min(i + chunk_size_bytes - 1, total_size - 1)
            chunks.append((start, end))

        num_chunks = len(chunks)

        # Download chunks in parallel with semaphore to limit concurrent connections
        semaphore = asyncio.Semaphore(max_connections)

        async def download_with_semaphore(chunk_index: int, start: int, end: int) -> tuple[int, bytes]:
            async with semaphore:
                return await self._download_chunk_with_retry(
                    session, signed_url, start, end, chunk_index
                )

        # Create tasks for all chunks
        tasks = [
            download_with_semaphore(i, start, end)
            for i, (start, end) in enumerate(chunks)
        ]

        # Execute all downloads in parallel
        try:
            results = await asyncio.gather(*tasks, return_exceptions=False)
        except Exception as e:
            self.logger.exception("❌ Parallel download failed: %s", e)
            raise

        # Reassemble chunks in correct order
        results.sort(key=lambda x: x[0])
        file_bytes = b''.join(chunk_data for _, chunk_data in results)

        # Calculate and log overall performance
        total_download_duration_ms = (time.time() - download_start_time) * 1000
        total_size_mb = total_size / (1024 * 1024)
        effective_speed_mbps = 0
        if total_download_duration_ms > 0:
            effective_speed_mbps = total_size_mb / (total_download_duration_ms / 1000)

        self.logger.info(
            "🚀 Parallel download complete: %.2f MB in %.0fms (%.2f MB/s, %d chunks)",
            total_size_mb, total_download_duration_ms, effective_speed_mbps, num_chunks
        )

        # Verify size
        if len(file_bytes) != total_size:
            raise Exception(f"Size mismatch: expected {total_size} bytes, got {len(file_bytes)} bytes")

        return file_bytes

    def _clean_top_level_empty_values(self, obj: Dict[str, Any]) -> Dict[str, Any]:
        """
        Remove top-level keys with None, empty strings, empty lists, and empty dicts.
        Only processes the first level of the given object.
        """
        return {
            k: v
            for k, v in obj.items()
            if v is not None and v != "" and v != [] and v != {}
        }

    def _clean_empty_values(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Clean empty values at the top level of:
        1. The main record object
        2. Each block in block_containers.blocks
        3. Each block group in block_containers.block_groups
        """
        # Clean top-level record fields
        cleaned = self._clean_top_level_empty_values(data)

        # Clean each block's top-level fields
        if "block_containers" in cleaned and isinstance(cleaned["block_containers"], dict):
            block_containers = cleaned["block_containers"]

            if "blocks" in block_containers and isinstance(block_containers["blocks"], list):
                block_containers["blocks"] = [
                    self._clean_top_level_empty_values(block) if isinstance(block, dict) else block
                    for block in block_containers["blocks"]
                ]

            if "block_groups" in block_containers and isinstance(block_containers["block_groups"], list):
                block_containers["block_groups"] = [
                    self._clean_top_level_empty_values(bg) if isinstance(bg, dict) else bg
                    for bg in block_containers["block_groups"]
                ]

        return cleaned

    def _is_non_versioned_exception(self, error: Exception) -> bool:
        """Detect Node storage errors for legacy documents that are not version-enabled."""
        return "cannot be versioned" in str(error).lower()

    async def apply(self, ctx: TransformContext) -> TransformContext:
        record = ctx.record
        org_id = record.org_id
        record_id = record.id
        virtual_record_id = record.virtual_record_id
        # Use exclude_none=True to skip None values, then clean empty values
        record_dict = record.model_dump(mode='json', exclude_none=True)
        record_dict = self._clean_empty_values(record_dict)

        existing_lookup = None
        if self.graph_provider:
            existing_lookup = await self.get_document_id_by_virtual_record_id(virtual_record_id)

        if existing_lookup and existing_lookup.get("record_doc_id"):
            existing_doc_id = existing_lookup["record_doc_id"]
            self.logger.info(
                "📄 Existing storage doc found for vrid %s (doc_id=%s), uploading next version",
                virtual_record_id, existing_doc_id
            )
            try:
                document_id, file_size_bytes = await self.upload_next_version(
                    org_id, record_id, existing_doc_id, record_dict, virtual_record_id
                )
            except Exception as e:
                if not self._is_non_versioned_exception(e):
                    raise
                self.logger.warning(
                    "⚠️ Existing storage doc %s is not version-enabled; creating replacement document",
                    existing_doc_id,
                )
                document_id, file_size_bytes = await self.save_record_to_storage(
                    org_id, record_id, virtual_record_id, record_dict
                )
        else:
            self.logger.info(
                "📄 No existing storage doc for vrid %s, creating new document",
                virtual_record_id
            )
            document_id, file_size_bytes = await self.save_record_to_storage(
                org_id, record_id, virtual_record_id, record_dict
            )

        if document_id and self.graph_provider:
            await self.store_virtual_record_mapping(virtual_record_id, document_id, file_size_bytes)

        ctx.record = record
        return ctx

    async def _get_signed_url(self, session, url, data, headers) -> dict | None:
        """Helper method to get signed URL with retry logic"""
        try:
            async with session.post(url, json=data, headers=headers) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    error_detail = ""
                    try:
                        error_response = await response.json()
                        self.logger.error("❌ Failed to get signed URL. Status: %d, Error: %s",
                                        response.status, error_response)
                        if isinstance(error_response, dict):
                            error_obj = error_response.get("error")
                            if isinstance(error_obj, dict):
                                error_detail = str(error_obj.get("message", "")).strip()
                            elif error_obj is not None:
                                error_detail = str(error_obj).strip()
                            if not error_detail:
                                error_detail = str(error_response)
                        else:
                            error_detail = str(error_response)
                        if "cannot be versioned" in error_detail.lower():
                            self.logger.warning("⚠️ Signed URL request indicates legacy non-versioned document")
                    except aiohttp.ContentTypeError:
                        error_text = await response.text()
                        error_detail = error_text[:200].strip()
                        self.logger.error("❌ Failed to get signed URL. Status: %d, Response: %s",
                                        response.status, error_text[:200])
                    if error_detail:
                        raise aiohttp.ClientError(f"Failed with status {response.status}: {error_detail}")
                    raise aiohttp.ClientError(f"Failed with status {response.status}")

                response_data = await response.json()
                return response_data
        except aiohttp.ClientError as e:
            self.logger.error("❌ Network error getting signed URL: %s", str(e))
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error getting signed URL: %s", str(e))
            raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    # async def _upload_to_signed_url(self, session, signed_url, data) -> int | None:
    #     """Upload data to a pre-signed URL using httpx.
    #     Uses httpx instead of aiohttp because aiohttp's yarl URL parser
    #     normalises percent-encoded characters (e.g. %2F → /) in query
    #     strings even with encoded=True, which invalidates S3/Azure
    #     pre-signed signatures
    #     """
    #     try:
    #         json_bytes = json.dumps(data).encode('utf-8')

    #         async with httpx.AsyncClient() as client:
    #             response = await client.put(
    #                 signed_url,
    #                 content=json_bytes,
    #                 headers={
    #                     "Content-Type": "application/json",
    #                 },
    #             )

    #             if response.status_code != HttpStatusCode.SUCCESS.value:
    #                 response_text = response.text[:200]
    #                 self.logger.error(
    #                     "❌ Failed to upload to signed URL. Status: %d, Response: %s",
    #                     response.status_code, response_text,
    #                 )
    #                 raise aiohttp.ClientError(f"Failed to upload with status {response.status_code}")

    #             self.logger.debug("✅ Successfully uploaded to signed URL")
    #             return response.status_code
    #     except aiohttp.ClientError:
    #         raise
    #     except Exception as e:
    #         self.logger.error("❌ Unexpected error uploading to signed URL: %s", str(e))
    #         raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    # async def _upload_raw_to_signed_url(
    #     self, signed_url: str, content: bytes, content_type: str
    # ) -> None:
    #     """Upload raw bytes to a pre-signed URL (for CSV, images, etc.)."""
    #     try:
    #         async with httpx.AsyncClient() as client:
    #             response = await client.put(
    #                 signed_url,
    #                 content=content,
    #                 headers={"Content-Type": content_type},
    #             )
    #             if response.status_code != HttpStatusCode.SUCCESS.value:
    #                 response_text = response.text[:200]
    #                 self.logger.error(
    #                     "❌ Failed to upload raw content. Status: %d, Response: %s",
    #                     response.status_code, response_text,
    #                 )
    #                 raise aiohttp.ClientError(
    #                     f"Failed to upload with status {response.status_code}"
    #                 )
    #             self.logger.debug("✅ Successfully uploaded raw content to signed URL")
    #     except aiohttp.ClientError:
    #         raise
    #     except Exception as e:
    #         self.logger.error("❌ Unexpected error uploading raw content: %s", str(e))
    #         raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    async def _upload_to_signed_url(self, session, signed_url, data) -> int | None:
        """Helper method to upload to signed URL with retry logic"""
        try:
            async with session.put(
                signed_url,
                json=data,
                skip_auto_headers={'Content-Type'}
            ) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    try:
                        error_response = await response.json()
                        self.logger.error("❌ Failed to upload to signed URL. Status: %d, Error: %s",
                                        response.status, error_response)
                    except aiohttp.ContentTypeError:
                        error_text = await response.text()
                        self.logger.error("❌ Failed to upload to signed URL. Status: %d, Response: %s",
                                        response.status, error_text[:200])
                    raise aiohttp.ClientError(f"Failed to upload with status {response.status}")

                return response.status
        except aiohttp.ClientError as e:
            self.logger.error("❌ Network error uploading to signed URL: %s", str(e))
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error uploading to signed URL: %s", str(e))
            raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    async def _upload_raw_to_signed_url(
        self,
        session: aiohttp.ClientSession,
        signed_url: str,
        content: bytes,
        content_type: str,
    ) -> None:
        """Upload raw bytes to a pre-signed URL (for CSV, images, etc.)."""
        try:
            async with session.put(
                signed_url,
                data=content,
                skip_auto_headers={"Content-Type"},
            ) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    response_text = (await response.text())[:200]
                    self.logger.error(
                        "❌ Failed to upload raw content. Status: %d, Response: %s",
                        response.status,
                        response_text,
                    )
                    raise aiohttp.ClientError(
                        f"Failed to upload with status {response.status}"
                    )
        except aiohttp.ClientError:
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error uploading raw content: %s", str(e))
            raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    async def _create_placeholder(self, session, url, data, headers) -> dict | None:
        """Helper method to create placeholder with retry logic"""
        try:
            async with session.post(url, json=data, headers=headers) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    try:
                        error_response = await response.json()
                        self.logger.error("❌ Failed to create placeholder. Status: %d, Error: %s",
                                        response.status, error_response)
                    except aiohttp.ContentTypeError:
                        error_text = await response.text()
                        self.logger.error("❌ Failed to create placeholder. Status: %d, Response: %s",
                                        response.status, error_text[:200])
                    raise aiohttp.ClientError(f"Failed with status {response.status}")

                response_data = await response.json()
                return response_data
        except aiohttp.ClientError as e:
            self.logger.error("❌ Network error creating placeholder: %s", str(e))
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error creating placeholder: %s", str(e))
            raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    async def save_record_to_storage(self, org_id: str, record_id: str, virtual_record_id: str, record: dict) -> tuple[str | None, int | None]:
        """
        Save document to storage using FormData upload
        Returns:
            tuple[str | None, int | None]: (document_id, file_size_bytes) if successful, (None, None) if failed
        """
        try:
            headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)

            # Compress record for both local and S3 storage
            try:
                compressed_record = self._compress_record(record)
                use_compression = True
            except Exception as e:
                self.logger.warning("⚠️ Compression failed, uploading uncompressed: %s", str(e))
                compressed_record = None
                use_compression = False

            if storage_type == "local":
                try:
                    async with aiohttp.ClientSession() as session:
                        # Use compressed data if available
                        upload_data = {
                            "isCompressed": use_compression,
                            "record": compressed_record if use_compression else record,
                            "virtualRecordId": virtual_record_id
                        }

                        json_data = json.dumps(upload_data).encode('utf-8')
                        file_size_bytes = len(json_data)

                        # Create form data
                        form_data = aiohttp.FormData()
                        form_data.add_field('file',
                                        json_data,
                                        filename=f'record_{virtual_record_id}.json',
                                        content_type='application/json')
                        form_data.add_field('documentName', f'record_{virtual_record_id}')
                        form_data.add_field('documentPath', f'records/{virtual_record_id}')
                        form_data.add_field('isVersionedFile', 'true')
                        form_data.add_field('extension', 'json')
                        form_data.add_field('recordId', record_id)
                        if use_compression:
                            compression_metadata = [
                                {
                                    "key": "compression",
                                    "value": {
                                        "algorithm": "zstd",
                                        "level": 10,
                                        "format": "msgspec",
                                        "version": "v1",
                                        "compressed": True,
                                    },
                                },
                            ]
                            for i, meta in enumerate(compression_metadata):
                                form_data.add_field(f'customMetadata[{i}][key]', meta['key'])
                                form_data.add_field(f'customMetadata[{i}][value][algorithm]', meta['value']['algorithm'])
                                form_data.add_field(f'customMetadata[{i}][value][level]', str(meta['value']['level']))
                                form_data.add_field(f'customMetadata[{i}][value][format]', meta['value']['format'])
                                form_data.add_field(f'customMetadata[{i}][value][version]', meta['value']['version'])
                                form_data.add_field(f'customMetadata[{i}][value][compressed]', str(meta['value']['compressed']).lower())

                        upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD.value}"

                        async with session.post(upload_url,
                                            data=form_data,
                                            headers=headers) as response:
                            if response.status != HttpStatusCode.SUCCESS.value:
                                try:
                                    error_response = await response.json()
                                    self.logger.error("❌ Failed to upload record. Status: %d, Error: %s",
                                                    response.status, error_response)
                                except aiohttp.ContentTypeError:
                                    error_text = await response.text()
                                    self.logger.error("❌ Failed to upload record. Status: %d, Response: %s",
                                                    response.status, error_text[:200])
                                raise Exception("Failed to upload record")

                            response_data = await response.json()
                            document_id = response_data.get('_id')

                            if not document_id:
                                self.logger.error("❌ No document ID in upload response")
                                raise Exception("No document ID in upload response")

                            self.logger.info("✅ Successfully uploaded record for document: %s", document_id)
                            return document_id, file_size_bytes
                except Exception as e:
                    raise
            else:
                # Prepare placeholder for S3 storage
                if use_compression:
                    # Prepare placeholder with compression metadata for MongoDB
                    placeholder_data = {
                        "documentName": f"record_{virtual_record_id}",
                        "documentPath": f"records/{virtual_record_id}",
                        "extension": "json",
                        "isVersionedFile": True,
                        "recordId": record_id,
                        "customMetadata": [
                            {
                                "key": "compression",
                                "value": {
                                    "algorithm": "zstd",
                                    "level": 10,
                                    "format": "msgspec",
                                    "version": "v1",
                                    "compressed": True
                                }
                            },
                        ]
                    }
                else:
                    # Fallback to uncompressed placeholder
                    placeholder_data = {
                        "documentName": f"record_{virtual_record_id}",
                        "documentPath": f"records/{virtual_record_id}",
                        "extension": "json",
                        "isVersionedFile": True,
                        "recordId": record_id,
                    }

                try:
                    async with aiohttp.ClientSession() as session:
                        placeholder_url = f"{nodejs_endpoint}{Routes.STORAGE_PLACEHOLDER.value}"
                        document = await self._create_placeholder(session, placeholder_url, placeholder_data, headers)

                        document_id = document.get("_id")
                        if not document_id:
                            self.logger.error("❌ No document ID found in placeholder response")
                            raise Exception("No document ID found in placeholder response")

                        upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                        upload_result = await self._get_signed_url(session, upload_url, {}, headers)

                        signed_url = upload_result.get('signedUrl')
                        if not signed_url:
                            self.logger.error("❌ No signed URL in response for document: %s", document_id)
                            raise Exception("No signed URL in response for document")

                        # Upload with isCompressed flag format
                        if compressed_record:
                            # Compressed format
                            upload_data = {
                                "isCompressed": True,
                                "record": compressed_record,
                                "virtualRecordId": virtual_record_id,
                            }
                        else:
                            # Uncompressed fallback format
                            upload_data = {
                                "record": record,
                                "isCompressed": False,
                                "virtualRecordId": virtual_record_id,
                            }

                        file_size_bytes = len(json.dumps(upload_data).encode('utf-8'))

                        await self._upload_to_signed_url(session, signed_url, upload_data)

                        self.logger.info("✅ Successfully completed record storage process for document: %s", document_id)
                        return document_id, file_size_bytes

                except Exception as e:
                    raise

        except Exception as e:
            self.logger.exception("❌ Error in saving record to storage: %s", str(e))
            raise e

    async def save_binary_to_storage(
        self,
        org_id: str,
        record_id: str,
        file_name: str,
        extension: str,
        content_type: str,
        binary_data: bytes,
    ) -> tuple[str | None, int | None]:
        """Upload a raw binary file (e.g. PDF) to storage for later retrieval via the buffer endpoint."""
        import os

        try:
            headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)
            file_size_bytes = len(binary_data)
            doc_name_no_ext = os.path.splitext(file_name)[0]

            # Single session for all HTTP steps in this upload (local: one POST; cloud: placeholder + signed URL + PUT).
            async with aiohttp.ClientSession() as session:
                if storage_type == "local":
                    form_data = aiohttp.FormData()
                    form_data.add_field(
                        "file", binary_data, filename=file_name, content_type=content_type
                    )
                    form_data.add_field("documentName", doc_name_no_ext)
                    form_data.add_field("documentPath", f"attachments/{record_id}")
                    form_data.add_field("isVersionedFile", "false")
                    form_data.add_field("extension", extension)
                    form_data.add_field("recordId", record_id)

                    upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD.value}"
                    async with session.post(upload_url, data=form_data, headers=headers) as response:
                        if response.status != HttpStatusCode.SUCCESS.value:
                            text = await response.text()
                            self.logger.error(
                                "❌ Failed to upload binary to storage: %d %s", response.status, text[:200]
                            )
                            return None, None
                        response_data = await response.json()
                        document_id = response_data.get("_id")
                        return document_id, file_size_bytes
                else:
                    # S3/cloud: placeholder → signed URL → raw upload
                    placeholder_data = {
                        "documentName": doc_name_no_ext,
                        "documentPath": f"attachments/{record_id}",
                        "extension": extension,
                        "isVersionedFile": False,
                        "recordId": record_id,
                    }
                    placeholder_url = f"{nodejs_endpoint}{Routes.STORAGE_PLACEHOLDER.value}"
                    document = await self._create_placeholder(session, placeholder_url, placeholder_data, headers)
                    document_id = document.get("_id")
                    if not document_id:
                        self.logger.error("❌ No document ID in placeholder response for binary upload")
                        return None, None

                    upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                    upload_result = await self._get_signed_url(session, upload_url, {}, headers)
                    signed_url = upload_result.get("signedUrl")
                    if not signed_url:
                        self.logger.error("❌ No signed URL for binary upload of document: %s", document_id)
                        return None, None

                    await self._upload_raw_to_signed_url(session, signed_url, binary_data, content_type)
                    return document_id, file_size_bytes

        except Exception as e:
            self.logger.error("❌ Failed to save binary to storage for record %s: %s", record_id, str(e))
            return None, None

    async def get_document_id_by_virtual_record_id(self, virtual_record_id: str) -> dict | None:
        """
        Get the document ID(s) and file size by virtual record ID from ArangoDB.

        Returns:
            dict | None: A dict with keys 'record_doc_id', 'fileSizeBytes', and optionally
                         'record_metadata_doc_id' if found, else None.
        """
        if not self.graph_provider:
            self.logger.error("❌ GraphProvider not initialized, cannot get document ID by virtual record ID.")
            raise Exception("GraphProvider not initialized, cannot get document ID by virtual record ID.")


        try:
            collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value

            nodes = await self.graph_provider.get_nodes_by_filters(
                collection_name,
                {"virtualRecordId": virtual_record_id}
            )
            if not nodes:
                doc = await self.graph_provider.get_document(
                    virtual_record_id,
                    collection_name
                )
                if doc:
                    nodes = [doc]

            if nodes:
                doc = nodes[0]
                record_doc_id = doc.get("record_doc_id") or doc.get("documentId")
                file_size_bytes = doc.get("fileSizeBytes")
                record_metadata_doc_id = doc.get("record_metadata_doc_id")
                result = {
                    "record_doc_id": record_doc_id,
                    "fileSizeBytes": file_size_bytes,
                }
                if record_metadata_doc_id:
                    result["record_metadata_doc_id"] = record_metadata_doc_id
                return result
            else:
                self.logger.info("No document ID found for virtual record ID: %s", virtual_record_id)
                return None
        except Exception as e:
            self.logger.exception(
                "❌ Error getting document ID by virtual record ID: %s",
                virtual_record_id,
            )
            raise e

    async def get_record_from_storage(self, virtual_record_id: str, org_id: str) -> dict | None:
            """
            Retrieve a record's content from blob storage using the virtual_record_id.
            Returns:
                str: The content of the record if found, else an empty string.
            """
            try:
                headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)

                lookup_result = await self.get_document_id_by_virtual_record_id(virtual_record_id)

                if not lookup_result:
                    self.logger.info("No document ID found for virtual record ID: %s", virtual_record_id)
                    return None

                document_id = lookup_result.get("record_doc_id")
                file_size_bytes = lookup_result.get("fileSizeBytes")

                if not document_id:
                    self.logger.debug("No document ID found for virtual record ID: %s", virtual_record_id)
                    return None

                download_url = f"{nodejs_endpoint}{Routes.STORAGE_DOWNLOAD.value.format(documentId=document_id)}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(download_url, headers=headers) as resp:
                        if resp.status == HttpStatusCode.SUCCESS.value:
                            data = await resp.json()

                            if data.get("record"):
                                record = self._process_downloaded_record(data)
                                record_name = record.get("record_name")
                                self.logger.info("✅ Successfully retrieved record %s from storage for virtual_record_id: %s", record_name, virtual_record_id)
                                return record
                            elif data.get("signedUrl"):
                                signed_url = data.get("signedUrl")

                                # Determine download strategy based on stored size
                                if file_size_bytes is None:
                                    use_parallel = True
                                else:
                                    MIN_SIZE_FOR_PARALLEL = 3 * 1024 * 1024
                                    use_parallel = file_size_bytes >= MIN_SIZE_FOR_PARALLEL

                                try:
                                    if use_parallel:
                                        file_bytes = await self._download_with_range_requests(
                                            session,
                                            signed_url,
                                            chunk_size_mb=2,
                                            max_connections=6
                                        )
                                        data = json.loads(file_bytes.decode('utf-8'))
                                    else:
                                        async with session.get(URL(signed_url, encoded=True)) as res:
                                            if res.status == HttpStatusCode.SUCCESS.value:
                                                data = await res.json(content_type=None)
                                            else:
                                                raise Exception(f"Failed to retrieve record: status {res.status}")
                                except Exception as e:
                                    if use_parallel:
                                        self.logger.warning("⚠️ Parallel download failed: %s. Falling back to single download...", str(e))
                                        try:
                                            async with session.get(URL(signed_url, encoded=True)) as res:
                                                if res.status == HttpStatusCode.SUCCESS.value:
                                                    data = await res.json(content_type=None)
                                                else:
                                                    raise Exception(f"Fallback download failed with status {res.status}")
                                        except Exception as fallback_error:
                                            self.logger.error("❌ Fallback download also failed: %s", str(fallback_error))
                                            raise Exception(f"Both parallel and fallback downloads failed: {str(e)}") from fallback_error
                                    else:
                                        self.logger.error("❌ Failed to retrieve record: %s", str(e))
                                        raise

                                if data.get("record"):
                                    record = self._process_downloaded_record(data)
                                    record_name = record.get("record_name")
                                    self.logger.info("✅ Successfully retrieved record %s from storage for virtual_record_id: %s", record_name, virtual_record_id)
                                    return record
                                else:
                                    self.logger.error("❌ No record found for virtual_record_id: %s", virtual_record_id)
                                    raise Exception("No record found for virtual_record_id")
                            else:
                                self.logger.error("❌ No record found for virtual_record_id: %s", virtual_record_id)
                                raise Exception("No record found for virtual_record_id")
                        else:
                            self.logger.error("❌ Failed to retrieve record: status %s, virtual_record_id: %s", resp.status, virtual_record_id)
                            raise Exception("Failed to retrieve record from storage")
            except Exception as e:
                self.logger.exception(
                    "❌ Error retrieving record from storage (virtual_record_id=%s)",
                    virtual_record_id,
                )
                raise e

    async def store_virtual_record_mapping(self, virtual_record_id: str, document_id: str, file_size_bytes: int | None = None) -> bool:
        """
        Stores the mapping between virtual_record_id and document_id in graph database.
        Args:
            virtual_record_id: The virtual record ID
            document_id: The document ID
            file_size_bytes: Optional file size in bytes
        Returns:
            bool: True if successful, False otherwise.
        """

        try:
            collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value

            # Create a unique key for the mapping using both IDs
            mapping_key = virtual_record_id

            mapping_document = {
                "id": mapping_key,
                "documentId": document_id,
                "updatedAt": get_epoch_timestamp_in_ms()
            }

            # Add file size if provided
            if file_size_bytes is not None:
                mapping_document["fileSizeBytes"] = file_size_bytes

            success = await self.graph_provider.batch_upsert_nodes(
                [mapping_document],
                collection_name
            )

            if success:
                size_info = f", file_size={file_size_bytes} bytes" if file_size_bytes is not None else ""
                self.logger.info("✅ Successfully stored virtual record mapping: virtual_record_id=%s, document_id=%s%s", virtual_record_id, document_id, size_info)
                return True
            else:
                self.logger.error("❌ Failed to store virtual record mapping")
                raise Exception("Failed to store virtual record mapping")

        except Exception as e:
            self.logger.exception(
                "❌ Failed to store virtual record mapping: %s",
                virtual_record_id,
            )
            raise e

    async def upload_next_version(
        self,
        org_id: str,
        record_id: str,
        document_id: str,
        record: dict,
        virtual_record_id: str = None
    ):
        """
        Args:
            org_id: Organization ID
            record_id: Record ID
            document_id: Existing document ID to add version to
            record: Record data to upload
            virtual_record_id: Virtual record ID

        Returns:
            tuple[str | None, int | None]: (document_id, file_size_bytes) if successful
        """
        try:
            headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)

            # Compress record for upload
            try:
                compressed_record = self._compress_record(record)
                use_compression = True
            except Exception as e:
                self.logger.warning("⚠️ Compression failed, uploading uncompressed: %s", str(e))
                compressed_record = None
                use_compression = False

            upload_data = {
                "isCompressed": use_compression,
                "record": compressed_record if use_compression else record,
                "virtualRecordId": virtual_record_id
            }
            json_data = json.dumps(upload_data).encode('utf-8')
            file_size_bytes = len(json_data)

            if storage_type == "local":
                async with aiohttp.ClientSession() as session:
                    form_data = aiohttp.FormData()
                    form_data.add_field('file',
                                    json_data,
                                    filename=f'record_{record_id}.json',
                                    content_type='application/json')

                    upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD_NEXT_VERSION.value.format(documentId=document_id)}"

                    async with session.post(upload_url, data=form_data, headers=headers) as response:
                        if response.status != HttpStatusCode.SUCCESS.value:
                            error_response = None
                            try:
                                error_response = await response.json()
                                self.logger.error("❌ Failed to upload next version. Status: %d, Error: %s",
                                                response.status, error_response)
                            except aiohttp.ContentTypeError:
                                error_text = await response.text()
                                self.logger.error("❌ Failed to upload next version. Status: %d, Response: %s",
                                                response.status, error_text[:200])
                            if (
                                response.status == HttpStatusCode.BAD_REQUEST.value
                                and isinstance(error_response, dict)
                                and "cannot be versioned"
                                in str(error_response.get("error", {}).get("message", "")).lower()
                            ):
                                raise Exception("This document cannot be versioned")

                            raise Exception(
                                f"Failed to upload next version (status: {response.status})"
                            )

                    self.logger.info("✅ Successfully uploaded next version for document: %s", document_id)
                    return document_id, file_size_bytes
            else:
                async with aiohttp.ClientSession() as session:
                    upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                    upload_result = await self._get_signed_url(session, upload_url, {}, headers)

                    signed_url = upload_result.get('signedUrl')
                    if not signed_url:
                        raise Exception("No signed URL in response for next version upload")

                    await self._upload_to_signed_url(session, signed_url, upload_data)

                    self.logger.info("✅ Successfully uploaded next version for document: %s", document_id)
                    return document_id, file_size_bytes

        except Exception as e:
            self.logger.error("❌ Error uploading next version: %s", str(e))
            raise e

    async def save_reconciliation_metadata(
        self, org_id: str, record_id: str, virtual_record_id: str, metadata_dict: dict
    ) -> str | None:
        """
        On first call, creates a new document. On subsequent calls, uploads next version.

        The metadata document ID is stored in the same virtual-record-to-doc mapping
        under the field 'record_metadata_doc_id', alongside the record's own 'record_doc_id'.

        Args:
            org_id: Organization ID
            record_id: Record ID
            virtual_record_id: Virtual record ID
            metadata_dict: Reconciliation metadata dictionary

        Returns:
            str | None: metadata document_id if successful
        """
        try:

            # Check if metadata document already exists in the same mapping doc
            existing_metadata_doc_id = None
            if self.graph_provider:
                try:
                    collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
                    doc = await self.graph_provider.get_document(
                        virtual_record_id, collection_name
                    )
                    if doc and doc.get("record_metadata_doc_id"):
                        existing_metadata_doc_id = doc["record_metadata_doc_id"]
                except Exception as e:
                    self.logger.warning("Could not check existing metadata mapping: %s", str(e))

            metadata_document_id = None
            if existing_metadata_doc_id:
                try:
                    doc_id, _ = await self.upload_next_version(
                        org_id, record_id, existing_metadata_doc_id,
                        metadata_dict, virtual_record_id
                    )
                    metadata_document_id = doc_id
                except Exception as e:
                    if not self._is_non_versioned_exception(e):
                        raise
                    self.logger.warning(
                        "⚠️ Existing metadata doc %s is not version-enabled; creating replacement metadata document",
                        existing_metadata_doc_id,
                    )
                    metadata_document_id = await self._create_metadata_document(
                        org_id, record_id, virtual_record_id, metadata_dict
                    )
            else:
                metadata_document_id = await self._create_metadata_document(
                    org_id, record_id, virtual_record_id, metadata_dict
                )

            if metadata_document_id and self.graph_provider:
                mapping_document = {
                    "_key": virtual_record_id,
                    "record_metadata_doc_id": metadata_document_id,
                    "updatedAt": get_epoch_timestamp_in_ms()
                }
                await self.graph_provider.batch_upsert_nodes(
                    [mapping_document],
                    CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
                )
                self.logger.info(
                    "✅ Stored metadata mapping: %s -> record_metadata_doc_id=%s",
                    virtual_record_id, metadata_document_id
                )

            return metadata_document_id

        except Exception as e:
            self.logger.error("❌ Error saving reconciliation metadata: %s", str(e))
            raise e

    async def _create_metadata_document(
        self, org_id: str, record_id: str, virtual_record_id: str, metadata_dict: dict
    ) -> str | None:
        """Create a new metadata document in blob storage."""
        try:
            headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)

            try:
                compressed_metadata = self._compress_record(metadata_dict)
                use_compression = True
            except Exception as e:
                self.logger.warning("⚠️ Metadata compression failed, uploading uncompressed: %s", str(e))
                compressed_metadata = None
                use_compression = False

            upload_data = {
                "isCompressed": use_compression,
                "record": compressed_metadata if use_compression else metadata_dict,
                "virtualRecordId": virtual_record_id,
            }
            json_data = json.dumps(upload_data).encode('utf-8')

            if storage_type == "local":
                async with aiohttp.ClientSession() as session:
                    form_data = aiohttp.FormData()
                    form_data.add_field('file',
                                    json_data,
                                    filename=f'metadata_{virtual_record_id}.json',
                                    content_type='application/json')
                    form_data.add_field('documentName', f'metadata_{virtual_record_id}')
                    form_data.add_field('documentPath', f'records/{virtual_record_id}')
                    form_data.add_field('isVersionedFile', 'true')
                    form_data.add_field('extension', 'json')
                    form_data.add_field('recordId', record_id)
                    if use_compression:
                        compression_metadata = [
                            {
                                "key": "compression",
                                "value": {
                                    "algorithm": "zstd",
                                    "level": 10,
                                    "format": "msgspec",
                                    "version": "v1",
                                    "compressed": True,
                                },
                            },
                        ]
                        for i, meta in enumerate(compression_metadata):
                            form_data.add_field(f'customMetadata[{i}][key]', meta['key'])
                            form_data.add_field(f'customMetadata[{i}][value][algorithm]', meta['value']['algorithm'])
                            form_data.add_field(f'customMetadata[{i}][value][level]', str(meta['value']['level']))
                            form_data.add_field(f'customMetadata[{i}][value][format]', meta['value']['format'])
                            form_data.add_field(f'customMetadata[{i}][value][version]', meta['value']['version'])
                            form_data.add_field(f'customMetadata[{i}][value][compressed]', str(meta['value']['compressed']).lower())

                    upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD.value}"

                    async with session.post(upload_url, data=form_data, headers=headers) as response:
                        if response.status != HttpStatusCode.SUCCESS.value:
                            try:
                                error_response = await response.json()
                                self.logger.error("❌ Failed to create metadata. Status: %d, Error: %s",
                                                response.status, error_response)
                            except aiohttp.ContentTypeError:
                                error_text = await response.text()
                                self.logger.error("❌ Failed to create metadata. Status: %d, Response: %s",
                                                response.status, error_text[:200])
                            raise Exception("Failed to create metadata document")

                        response_data = await response.json()
                        document_id = response_data.get('_id')

                        if not document_id:
                            raise Exception("No document ID in metadata upload response")

                        self.logger.info("✅ Created metadata document: %s", document_id)
                        return document_id
            else:

                if use_compression:
                    # Prepare placeholder with compression metadata for MongoDB
                    placeholder_data = {
                        "documentName": f"metadata_{virtual_record_id}",
                        "documentPath": f"records/{virtual_record_id}",
                        "extension": "json",
                        "isVersionedFile": True,
                        "recordId": record_id,
                        "customMetadata": [
                            {
                                "key": "compression",
                                "value": {
                                    "algorithm": "zstd",
                                    "level": 10,
                                    "format": "msgspec",
                                    "version": "v1",
                                    "compressed": True
                                }
                            },
                        ]
                    }
                else:

                    placeholder_data = {
                        "documentName": f"metadata_{virtual_record_id}",
                        "documentPath": f"records/{virtual_record_id}",
                        "extension": "json",
                        "isVersionedFile": True,
                        "recordId": record_id,
                    }

                async with aiohttp.ClientSession() as session:
                    placeholder_url = f"{nodejs_endpoint}{Routes.STORAGE_PLACEHOLDER.value}"
                    document = await self._create_placeholder(session, placeholder_url, placeholder_data, headers)

                    document_id = document.get("_id")
                    if not document_id:
                        raise Exception("No document ID in metadata placeholder response")

                    upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                    upload_result = await self._get_signed_url(session, upload_url, {}, headers)

                    signed_url = upload_result.get('signedUrl')
                    if not signed_url:
                        raise Exception("No signed URL in response for metadata document")

                    await self._upload_to_signed_url(session, signed_url, upload_data)

                    self.logger.info("✅ Created metadata document: %s", document_id)
                    return document_id

        except Exception as e:
            self.logger.error("❌ Error creating metadata document: %s", str(e))
            raise e


    async def save_conversation_file_to_storage(
        self,
        org_id: str,
        conversation_id: str,
        file_name: str,
        file_bytes: bytes,
        content_type: str = "text/csv",
        custom_metadata: list[CustomMetadataEntry] | None = None,
    ) -> dict:
        """Save a file (CSV, etc.) under a conversation path and return download info.

        Args:
            org_id: Organisation ID (used for auth / routing).
            conversation_id: Conversation this file belongs to.
            file_name: Human-readable file name **with** extension
                       (e.g. ``query_result_1709640000.csv``).
            file_bytes: Raw file content.
            content_type: MIME type for the upload.
            custom_metadata: Optional ``customMetadata`` entries for the
                storage document.

        Returns:
            dict with ``documentId``, ``fileName``, and either ``signedUrl``
            (S3) or ``downloadUrl`` (local).
        """
        import os

        try:
            headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)
            public_base_url = await self._get_public_download_base_url()

            document_path = f"conversations/{conversation_id}"
            doc_name_no_ext = os.path.splitext(file_name)[0]
            extension = os.path.splitext(file_name)[1].lstrip(".")

            if storage_type == "local":
                async with aiohttp.ClientSession() as session:
                    form_data = aiohttp.FormData()
                    form_data.add_field(
                        "file", file_bytes,
                        filename=file_name,
                        content_type=content_type,
                    )
                    form_data.add_field("documentName", doc_name_no_ext)
                    form_data.add_field("documentPath", document_path)
                    form_data.add_field("isVersionedFile", "false")
                    if custom_metadata:
                        _add_custom_metadata_to_form(form_data, custom_metadata)

                    upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD.value}"
                    async with session.post(upload_url, data=form_data, headers=headers) as response:
                        if response.status != HttpStatusCode.SUCCESS.value:
                            try:
                                error_body = await response.json()
                                self.logger.error(
                                    "❌ Conversation file upload failed. Status: %d, Error: %s",
                                    response.status, error_body,
                                )
                            except Exception:
                                error_text = await response.text()
                                self.logger.error(
                                    "❌ Conversation file upload failed. Status: %d, Response: %s",
                                    response.status, error_text[:500],
                                )
                            raise Exception(f"Local upload failed with status {response.status}")
                        response_data = await response.json()
                        document_id = response_data.get("_id")
                        if not document_id:
                            raise Exception("No document ID in local upload response")

                    download_url = (
                        f"{public_base_url}"
                        f"{Routes.STORAGE_DOWNLOAD_EXTERNAL.value.format(documentId=document_id)}"
                    )
                    self.logger.info("✅ Conversation file saved (local): %s", document_id)
                    return {
                        "documentId": document_id,
                        "downloadUrl": download_url,
                        "fileName": file_name,
                    }
            else:
                placeholder_data = {
                    "documentName": doc_name_no_ext,
                    "documentPath": document_path,
                    "extension": extension,
                    "isVersionedFile": False,
                }
                if custom_metadata:
                    placeholder_data["customMetadata"] = custom_metadata

                async with aiohttp.ClientSession() as session:
                    placeholder_url = f"{nodejs_endpoint}{Routes.STORAGE_PLACEHOLDER.value}"
                    document = await self._create_placeholder(
                        session, placeholder_url, placeholder_data, headers,
                    )
                    document_id = document.get("_id")
                    if not document_id:
                        raise Exception("No document ID in placeholder response")

                    upload_url = (
                        f"{nodejs_endpoint}"
                        f"{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                    )
                    upload_result = await self._get_signed_url(session, upload_url, {}, headers)
                    signed_url = upload_result.get("signedUrl")
                    if not signed_url:
                        raise Exception("No signed URL for conversation file upload")

                    await self._upload_raw_to_signed_url(
                        session,
                        signed_url,
                        file_bytes,
                        content_type,
                    )

                    download_api = (
                        f"{nodejs_endpoint}"
                        f"{Routes.STORAGE_DOWNLOAD.value.format(documentId=document_id)}"
                    )
                    async with session.get(download_api, headers=headers) as resp:
                        if resp.status == HttpStatusCode.SUCCESS.value:
                            data = await resp.json()
                            download_signed_url = data.get("signedUrl")
                            if download_signed_url:
                                self.logger.info(
                                    "✅ Conversation file saved (S3): %s", document_id,
                                )
                                return {
                                    "documentId": document_id,
                                    "signedUrl": download_signed_url,
                                    "fileName": file_name,
                                }

                    self.logger.info(
                        "✅ Conversation file saved (fallback URL): %s", document_id,
                    )
                    download_url_external = (
                        f"{public_base_url}"
                        f"{Routes.STORAGE_DOWNLOAD_EXTERNAL.value.format(documentId=document_id)}"
                    )
                    return {
                        "documentId": document_id,
                        "downloadUrl": download_url_external,
                        "fileName": file_name,
                    }
        except Exception as e:
            self.logger.exception(
                "❌ Error saving conversation file: %s",
                conversation_id,
            )
            raise

    async def save_versioned_artifact_to_storage(
        self,
        org_id: str,
        conversation_id: str,
        file_name: str,
        file_bytes: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict:
        """Create a NEW, version-enabled document for an agent/LLM artifact.

        Same wire path as :meth:`save_conversation_file_to_storage` (raw
        bytes, ``artifacts/{conversation_id}`` path) but with
        ``isVersionedFile: true`` so a later :meth:`upload_artifact_version`
        call can append version 1, 2, ... to THIS SAME document instead of
        creating a parallel, unrelated file per version — the single
        invariant :class:`~app.services.artifact_registry.versioning.VersionManager`
        depends on.

        Returns dict with ``documentId``, ``fileName``, and either
        ``signedUrl`` (S3) or ``downloadUrl`` (local).
        """
        import os as _os

        headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)
        document_path = f"artifacts/{conversation_id}"
        doc_name_no_ext = _os.path.splitext(file_name)[0]
        extension = _os.path.splitext(file_name)[1].lstrip(".")

        if storage_type == "local":
            async with aiohttp.ClientSession() as session:
                form_data = aiohttp.FormData()
                form_data.add_field(
                    "file", file_bytes, filename=file_name, content_type=content_type,
                )
                form_data.add_field("documentName", doc_name_no_ext)
                form_data.add_field("documentPath", document_path)
                form_data.add_field("isVersionedFile", "true")

                upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD.value}"
                async with session.post(upload_url, data=form_data, headers=headers) as response:
                    if response.status != HttpStatusCode.SUCCESS.value:
                        error_text = (await response.text())[:500]
                        self.logger.error(
                            "❌ Versioned artifact upload failed. Status: %d, Response: %s",
                            response.status, error_text,
                        )
                        raise Exception(f"Local upload failed with status {response.status}")
                    response_data = await response.json()
                    document_id = response_data.get("_id")
                    if not document_id:
                        raise Exception("No document ID in local upload response")

                public_base_url = await self._get_public_download_base_url()
                download_url = (
                    f"{public_base_url}"
                    f"{Routes.STORAGE_DOWNLOAD_EXTERNAL.value.format(documentId=document_id)}"
                )
                return {"documentId": document_id, "downloadUrl": download_url, "fileName": file_name}
        else:
            placeholder_data = {
                "documentName": doc_name_no_ext,
                "documentPath": document_path,
                "extension": extension,
                "isVersionedFile": True,
            }
            async with aiohttp.ClientSession() as session:
                placeholder_url = f"{nodejs_endpoint}{Routes.STORAGE_PLACEHOLDER.value}"
                document = await self._create_placeholder(session, placeholder_url, placeholder_data, headers)
                document_id = document.get("_id") if document else None
                if not document_id:
                    raise Exception("No document ID in placeholder response")

                upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                upload_result = await self._get_signed_url(session, upload_url, {}, headers)
                signed_url = (upload_result or {}).get("signedUrl")
                if not signed_url:
                    raise Exception("No signed URL for versioned artifact upload")

                await self._upload_raw_to_signed_url(session, signed_url, file_bytes, content_type)

                download_api = f"{nodejs_endpoint}{Routes.STORAGE_DOWNLOAD.value.format(documentId=document_id)}"
                async with session.get(download_api, headers=headers) as resp:
                    if (
                        resp.status == HttpStatusCode.SUCCESS.value
                        and resp.content_type == "application/json"
                    ):
                        data = await resp.json()
                        if data.get("signedUrl"):
                            return {
                                "documentId": document_id,
                                "signedUrl": data["signedUrl"],
                                "fileName": file_name,
                            }
                public_base_url = await self._get_public_download_base_url()
                return {
                    "documentId": document_id,
                    "downloadUrl": f"{public_base_url}{Routes.STORAGE_DOWNLOAD_EXTERNAL.value.format(documentId=document_id)}",
                    "fileName": file_name,
                }

    async def get_download_url(self, org_id: str, document_id: str, version: int | None = None) -> str:
        """Resolve a user-facing download URL for `document_id` — an S3/Azure
        signed URL when available, else the org-scoped external download
        route (local storage / any fallback where the download route
        didn't return a `signedUrl`). Used by
        `app.services.artifact_registry.signed_urls.SignedUrlBroker` so
        that module never reaches into this class's private helpers.

        `version` is a storage-layer `versionHistory` index (not a registry
        version number); both the internal and external `/download` routes
        accept it identically (same `downloadDocument` handler mounted
        twice — see `storage.routes.ts`)."""
        headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)
        version_query = f"?version={version}" if version is not None else ""
        # Local storage's download route STREAMS the file bytes back
        # (`serveFileFromLocalStorage` in storage.controller.ts) — there is
        # no `{signedUrl}` JSON to fetch, so calling it here would download
        # the whole file just to throw it away. Go straight to the
        # org-scoped external route.
        if storage_type != "local":
            download_api = (
                f"{nodejs_endpoint}{Routes.STORAGE_DOWNLOAD.value.format(documentId=document_id)}"
                f"{version_query}"
            )
            async with aiohttp.ClientSession() as session:
                async with session.get(download_api, headers=headers) as resp:
                    # Content-type guard: any storage vendor that streams the
                    # file on this route (rather than returning JSON) falls
                    # through to the external-route fallback below.
                    if (
                        resp.status == HttpStatusCode.SUCCESS.value
                        and resp.content_type == "application/json"
                    ):
                        data = await resp.json()
                        signed = data.get("signedUrl")
                        if signed:
                            return str(signed)
        public_base_url = await self._get_public_download_base_url()
        return (
            f"{public_base_url}{Routes.STORAGE_DOWNLOAD_EXTERNAL.value.format(documentId=document_id)}"
            f"{version_query}"
        )

    async def get_direct_upload_url(self, org_id: str, document_id: str) -> str:
        """Signed PUT URL for an EXISTING document — the first phase of the
        artifact registry's two-phase upload (`get_upload_grant` in
        `signed_urls.py`). Cloud storage (S3/Azure) only: local storage has
        no equivalent unauthenticated PUT target, since `uploadNextVersion`
        requires the same scoped internal JWT this method itself uses to
        fetch the URL — callers must fall back to the inline-content path
        for local deployments (see `SignedUrlBroker.get_upload_grant`).
        """
        headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)
        if storage_type == "local":
            raise Exception("Direct signed upload URLs are not supported for local storage")
        async with aiohttp.ClientSession() as session:
            upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
            upload_result = await self._get_signed_url(session, upload_url, {}, headers)
            signed_url = (upload_result or {}).get("signedUrl")
            if not signed_url:
                raise Exception("No signed URL returned for direct upload")
            return signed_url

    async def upload_artifact_version(
        self,
        org_id: str,
        document_id: str,
        file_name: str,
        file_bytes: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict:
        """Append a new version of RAW bytes to an existing artifact document.

        Distinct from :meth:`upload_next_version` (which JSON-wraps/compresses
        a KB-record snapshot and, for cloud storage, PUTs straight to the
        CURRENT object key via a `directUpload` signed URL — bypassing
        ``versionHistory`` bookkeeping entirely). ``uploadNextVersionDocument``
        (`storage.controller.ts`) is storage-vendor-agnostic — it reads the
        uploaded buffer via `FileProcessorService` and writes it through the
        SAME adapter abstraction (`adapter.getBufferFromStorageService`/
        `cloneDocument`) regardless of S3/Azure/local — so unlike
        `upload_next_version`, this method always posts multipart bytes to
        that one route for EVERY storage vendor, guaranteeing a real
        ``versionHistory`` entry is appended everywhere.

        Returns dict with ``documentId``, ``sizeBytes``, ``storageVersion``
        (the ``versionHistory`` index Node just wrote these bytes to — the
        LAST entry of the response document's ``versionHistory``), and
        ``priorStorageVersion`` (the entry immediately before it, non-None
        only when Node had to lazily snapshot the previous "current" content
        first — i.e. the very first version bump for this document). Callers
        MUST use these indices rather than deriving them arithmetically —
        see `VersionManager.add_version`.
        """
        headers, nodejs_endpoint, _storage_type = await self._get_auth_and_config(org_id)
        file_size_bytes = len(file_bytes)

        async with aiohttp.ClientSession() as session:
            form_data = aiohttp.FormData()
            form_data.add_field(
                "file", file_bytes, filename=file_name, content_type=content_type,
            )
            upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD_NEXT_VERSION.value.format(documentId=document_id)}"
            async with session.post(upload_url, data=form_data, headers=headers) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    error_text = (await response.text())[:500]
                    self.logger.error(
                        "❌ Artifact version upload failed. Status: %d, Response: %s",
                        response.status, error_text,
                    )
                    if response.status == HttpStatusCode.BAD_REQUEST.value and "cannot be versioned" in error_text.lower():
                        raise Exception("This document cannot be versioned")
                    raise Exception(f"Failed to upload artifact version (status: {response.status})")
                try:
                    response_data = await response.json()
                except aiohttp.ContentTypeError:
                    response_data = {}

            version_history = response_data.get("versionHistory") or []
            storage_version = version_history[-1].get("version") if version_history else None
            prior_storage_version = (
                version_history[-2].get("version") if len(version_history) >= 2 else None
            )
            if storage_version is None:
                self.logger.warning(
                    "⚠️ Artifact version upload for document %s returned no versionHistory; "
                    "version-pinned retrieval for this bump will fall back to latest.",
                    document_id,
                )
            return {
                "documentId": document_id,
                "sizeBytes": file_size_bytes,
                "storageVersion": storage_version,
                "priorStorageVersion": prior_storage_version,
            }

    async def get_document_version_history(self, org_id: str, document_id: str) -> list[dict]:
        """Fetch the authoritative ``versionHistory`` array for `document_id`
        straight from the storage document (``GET /internal/{documentId}``).
        Used by `artifact_cleanup.py`'s `PENDING_RECONCILE` repair pass to
        recover the storage index of a version bump whose graph-side write
        failed — the blob write itself already succeeded, so this list is
        the ground truth to reconcile FROM."""
        headers, nodejs_endpoint, _storage_type = await self._get_auth_and_config(org_id)
        url = f"{nodejs_endpoint}{Routes.STORAGE_DOCUMENT.value.format(documentId=document_id)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    error_text = (await response.text())[:500]
                    self.logger.error(
                        "❌ Failed to fetch document version history for %s. Status: %d, Response: %s",
                        document_id, response.status, error_text,
                    )
                    raise Exception(f"Failed to fetch document {document_id} (status: {response.status})")
                data = await response.json()
                return data.get("versionHistory") or []

    async def get_reconciliation_metadata(self, virtual_record_id: str, org_id: str) -> dict | None:
        """
        Args:
            virtual_record_id: Virtual record ID
            org_id: Organization ID

        Returns:
            dict | None: Metadata dict (hash_to_block_ids, block_id_to_index with block_id -> index int) if found, None otherwise
        """
        try:
            if not self.graph_provider:
                self.logger.error("❌ ArangoService not initialized")
                return None

            try:
                collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
                doc = await self.graph_provider.get_document(
                    virtual_record_id, collection_name
                )
                if not doc or not doc.get("record_metadata_doc_id"):
                    self.logger.info("No metadata document found for virtual_record_id: %s", virtual_record_id)
                    return None
                metadata_document_id = doc["record_metadata_doc_id"]
            except Exception as e:
                self.logger.warning("Error looking up metadata mapping: %s", str(e))
                return None

            # Download metadata from storage
            headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)

            download_url = f"{nodejs_endpoint}{Routes.STORAGE_DOWNLOAD.value.format(documentId=metadata_document_id)}"

            async with aiohttp.ClientSession() as session:
                async with session.get(download_url, headers=headers) as resp:
                    if resp.status == HttpStatusCode.SUCCESS.value:
                        data = await resp.json()
                        if data.get("signedUrl"):
                            signed_url = data.get("signedUrl")
                            async with session.get(URL(signed_url, encoded=True)) as signed_resp:
                                if signed_resp.status == HttpStatusCode.SUCCESS.value:
                                    data = await signed_resp.json(content_type=None)
                        # Handle both compressed (from upload_next_version) and uncompressed formats
                        if data.get("isCompressed"):
                            record = self._process_downloaded_record(data)
                        elif isinstance(data, dict) and "record" in data:
                            record = data.get("record", data)
                        else:
                            record = data
                        self.logger.debug(
                            "✅ Retrieved reconciliation metadata for virtual_record_id: %s",
                            virtual_record_id
                        )
                        return record
                    else:
                        self.logger.warning(
                            "⚠️ Failed to retrieve metadata: status %s, virtual_record_id: %s",
                            resp.status, virtual_record_id
                        )
                        return None

        except Exception as e:
            self.logger.error("❌ Error retrieving reconciliation metadata: %s", str(e))
            return None
