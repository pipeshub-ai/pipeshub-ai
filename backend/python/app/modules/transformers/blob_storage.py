import asyncio
import json
import time
from typing import Any, Dict

import aiohttp
import jwt

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
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class BlobStorage(Transformer):
    def __init__(self,logger,config_service, graph_provider: IGraphDBProvider = None) -> None:
        self.logger = logger
        self.config_service = config_service
        self.graph_provider = graph_provider

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
        self.logger.info("📦 Compressed record (msgspec): %d -> %d bytes (%.1f%% reduction)",
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
            self.logger.info("🔍 Decompressing compressed record (msgspec format)")
            compressed_base64 = data.get("record")
            if not compressed_base64:
                self.logger.error("❌ isCompressed is true but no record found")
                raise Exception("Missing record in compressed record")

            try:
                overall_processing_start = time.time()

                # Step 1: Base64 decode
                base64_start = time.time()
                compressed_bytes = base64.b64decode(compressed_base64)
                base64_duration_ms = (time.time() - base64_start) * 1000
                self.logger.info("⏱️ Base64 decode completed in %.2fms (decoded size: %d bytes)", base64_duration_ms, len(compressed_bytes))

                # Step 2: Decompress
                decompress_start = time.time()
                decompressed_bytes = self._decompress_bytes(compressed_bytes)
                decompress_duration_ms = (time.time() - decompress_start) * 1000
                self.logger.info("⏱️ Decompression completed in %.2fms (decompressed size: %d bytes)", decompress_duration_ms, len(decompressed_bytes))

                # Step 3: Ultra-fast msgspec parse (no UTF-8 decode needed - direct bytes to dict)
                msgpack_parse_start = time.time()
                record = msgspec.msgpack.decode(decompressed_bytes)
                msgpack_parse_duration_ms = (time.time() - msgpack_parse_start) * 1000
                self.logger.info("⏱️ msgspec parsing completed in %.2fms", msgpack_parse_duration_ms)

                overall_processing_ms = (time.time() - overall_processing_start) * 1000
                self.logger.info("📦 Total record processing completed in %.2fms (base64: %.2fms, decompress: %.2fms, msgspec: %.2fms)",
                                overall_processing_ms, base64_duration_ms, decompress_duration_ms, msgpack_parse_duration_ms)
                return record

            except Exception as e:
                self.logger.error("❌ Failed to decompress record: %s", str(e))
                raise Exception(f"Decompression failed: {str(e)}")

        # OLD FORMAT: Uncompressed record
        elif data.get("record"):
            self.logger.info("📄 Processing uncompressed record (no decompression needed)")
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
            async with session.get(url, headers=headers) as response:
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
        chunk_start_time = time.time()
        for attempt in range(max_retries):
            try:
                headers = {'Range': f'bytes={start}-{end}'}
                async with session.get(url, headers=headers) as response:
                    if response.status in (HttpStatusCode.SUCCESS.value, HttpStatusCode.PARTIAL_CONTENT.value):  # 200 for full content, 206 for partial
                        chunk_bytes = await response.read()
                        chunk_duration_ms = (time.time() - chunk_start_time) * 1000
                        chunk_size_mb = len(chunk_bytes) / (1024 * 1024)
                        self.logger.debug(
                            "✅ Chunk %d downloaded: %.2f MB in %.0fms (%.2f MB/s)",
                            chunk_index, chunk_size_mb, chunk_duration_ms,
                            chunk_size_mb / (chunk_duration_ms / 1000) if chunk_duration_ms > 0 else 0
                        )
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
                    self.logger.error("❌ Chunk %d download failed after %d attempts: %s", chunk_index, max_retries, str(e))
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
        size_check_start = time.time()
        total_size = await self._get_content_length(session, signed_url)
        size_check_duration_ms = (time.time() - size_check_start) * 1000
        self.logger.info("⏱️ File size check completed in %.0fms: %.2f MB",
                        size_check_duration_ms, total_size / (1024 * 1024))

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
        self.logger.info(
            "📦 Splitting %.2f MB file into %d chunks of ~%.2f MB each (max %d parallel connections)",
            total_size / (1024 * 1024), num_chunks, chunk_size_mb, max_connections
        )

        # Download chunks in parallel with semaphore to limit concurrent connections
        parallel_download_start = time.time()
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
            parallel_download_duration_ms = (time.time() - parallel_download_start) * 1000
            self.logger.info("⏱️ Parallel download completed in %.0fms", parallel_download_duration_ms)
        except Exception as e:
            self.logger.error("❌ Parallel download failed: %s", str(e))
            raise

        # Reassemble chunks in correct order
        reassembly_start = time.time()
        results.sort(key=lambda x: x[0])  # Sort by chunk index
        file_bytes = b''.join(chunk_data for _, chunk_data in results)
        reassembly_duration_ms = (time.time() - reassembly_start) * 1000
        self.logger.info("⏱️ Chunk reassembly completed in %.0fms", reassembly_duration_ms)

        # Calculate and log overall performance
        total_download_duration_ms = (time.time() - download_start_time) * 1000
        total_size_mb = total_size / (1024 * 1024)
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

    async def apply(self, ctx: TransformContext) -> TransformContext:
        record = ctx.record
        org_id = record.org_id
        record_id = record.id
        virtual_record_id = record.virtual_record_id
        # Use exclude_none=True to skip None values, then clean empty values
        record_dict = record.model_dump(mode='json', exclude_none=True)
        record_dict = self._clean_empty_values(record_dict)
        document_id, file_size_bytes = await self.save_record_to_storage(org_id, record_id, virtual_record_id, record_dict)

        # Store the mapping if we have both IDs and graph_provider is available
        if document_id and self.graph_provider:
            await self.store_virtual_record_mapping(virtual_record_id, document_id, file_size_bytes)

        ctx.record = record
        return ctx

    async def _get_signed_url(self, session, url, data, headers) -> dict | None:
        """Helper method to get signed URL with retry logic"""
        try:
            async with session.post(url, json=data, headers=headers) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    try:
                        error_response = await response.json()
                        self.logger.error("❌ Failed to get signed URL. Status: %d, Error: %s",
                                        response.status, error_response)
                    except aiohttp.ContentTypeError:
                        error_text = await response.text()
                        self.logger.error("❌ Failed to get signed URL. Status: %d, Response: %s",
                                        response.status, error_text[:200])
                    raise aiohttp.ClientError(f"Failed with status {response.status}")

                response_data = await response.json()
                self.logger.debug("✅ Successfully retrieved signed URL")
                return response_data
        except aiohttp.ClientError as e:
            self.logger.error("❌ Network error getting signed URL: %s", str(e))
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error getting signed URL: %s", str(e))
            raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    async def _upload_to_signed_url(self, session, signed_url, data) -> int | None:
        """Helper method to upload to signed URL with retry logic"""
        try:
            async with session.put(
                signed_url,
                json=data,
                headers={"Content-Type": "application/json"}
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

                self.logger.debug("✅ Successfully uploaded to signed URL")
                return response.status
        except aiohttp.ClientError as e:
            self.logger.error("❌ Network error uploading to signed URL: %s", str(e))
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error uploading to signed URL: %s", str(e))
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
                self.logger.debug("✅ Successfully created placeholder")
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
            self.logger.info("🚀 Starting storage process for record: %s", record_id)

            # Generate JWT token
            try:
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
                headers = {
                    "Authorization": f"Bearer {jwt_token}"
                }
            except Exception as e:
                self.logger.error("❌ Failed to generate JWT token: %s", str(e))
                raise e

            # Get endpoint configuration
            try:
                endpoints = await self.config_service.get_config(
                    config_node_constants.ENDPOINTS.value
                )
                nodejs_endpoint = endpoints.get("cm", {}).get("endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value)
                if not nodejs_endpoint:
                    raise ValueError("Missing CM endpoint configuration")

                storage = await self.config_service.get_config(
                    config_node_constants.STORAGE.value
                )
                storage_type = storage.get("storageType")
                if not storage_type:
                    raise ValueError("Missing storage type configuration")
                self.logger.info("🚀 Storage type: %s", storage_type)
            except Exception as e:
                self.logger.error("❌ Failed to get endpoint configuration: %s", str(e))
                raise e

            # Compress record for both local and S3 storage
            try:
                start_time = time.time()
                compressed_record = self._compress_record(record)
                compression_time_ms = (time.time() - start_time) * 1000
                self.logger.info("⏱️ Compression completed in %.0fms", compression_time_ms)

                use_compression = True
            except Exception as e:
                self.logger.warning("⚠️ Compression failed, uploading uncompressed: %s", str(e))
                compressed_record = None
                use_compression = False

            self.logger.info("Used compression: %s", use_compression)

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

                        self.logger.info("📏 Calculated local storage file size: %d bytes (%.2f MB)",file_size_bytes, file_size_bytes / (1024 * 1024))

                        # Create form data
                        form_data = aiohttp.FormData()
                        form_data.add_field('file',
                                        json_data,
                                        filename=f'record_{virtual_record_id}.json',
                                        content_type='application/json')
                        form_data.add_field('documentName', f'record_{virtual_record_id}')
                        form_data.add_field('documentPath', f'records/{virtual_record_id}')
                        form_data.add_field('isVersionedFile', 'false')
                        form_data.add_field('extension', 'json')
                        form_data.add_field('recordId', record_id)

                        # Make upload request
                        upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD.value}"
                        self.logger.info("📤 Uploading record to storage: %s", record_id)

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
                except aiohttp.ClientError as e:
                    self.logger.error("❌ Network error during upload process: %s", str(e))
                    raise e
                except Exception as e:
                    self.logger.error("❌ Unexpected error during upload process: %s", str(e))
                    self.logger.exception("Detailed error trace:")
                    raise e
            else:
                # Prepare placeholder for S3 storage
                if use_compression:
                    # Prepare placeholder with compression metadata for MongoDB
                    placeholder_data = {
                        "documentName": f"record_{virtual_record_id}",
                        "documentPath": f"records/{virtual_record_id}",
                        "extension": "json",
                        "isVersionedFile": False,
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
                        "isVersionedFile": False,
                        "recordId": record_id,
                    }

                try:
                    async with aiohttp.ClientSession() as session:
                        # Step 1: Create placeholder
                        self.logger.info("📝 Creating placeholder for record: %s", record_id)
                        placeholder_url = f"{nodejs_endpoint}{Routes.STORAGE_PLACEHOLDER.value}"
                        document = await self._create_placeholder(session, placeholder_url, placeholder_data, headers)

                        document_id = document.get("_id")
                        if not document_id:
                            self.logger.error("❌ No document ID in placeholder response")
                            raise Exception("No document ID in placeholder response")

                        self.logger.info("📄 Created placeholder with ID: %s", document_id)

                        # Step 2: Get signed URL (only send metadata, not the full record)
                        self.logger.info("🔑 Getting signed URL for document: %s", document_id)

                        upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                        upload_result = await self._get_signed_url(session, upload_url, {}, headers)

                        signed_url = upload_result.get('signedUrl')
                        if not signed_url:
                            self.logger.error("❌ No signed URL in response for document: %s", document_id)
                            raise Exception("No signed URL in response for document")

                        # Step 3: Upload to signed URL with new format
                        self.logger.info("📤 Uploading record to storage for document: %s", document_id)

                        # Upload with isCompressed flag format
                        if compressed_record:
                            # Compressed format
                            upload_data = {
                                "isCompressed": True,
                                "record": compressed_record
                            }
                        else:
                            # Uncompressed fallback format
                            upload_data = {
                                "record": record,
                                "isCompressed": False,
                            }

                        file_size_bytes = len(json.dumps(upload_data).encode('utf-8'))

                        await self._upload_to_signed_url(session, signed_url, upload_data)

                        self.logger.info("✅ Successfully completed record storage process for document: %s", document_id)
                        return document_id, file_size_bytes

                except aiohttp.ClientError as e:
                    self.logger.error("❌ Network error during storage process: %s", str(e))
                    raise e
                except Exception as e:
                    self.logger.error("❌ Unexpected error during storage process: %s", str(e))
                    self.logger.exception("Detailed error trace:")
                    raise e

        except Exception as e:
            self.logger.error("❌ Critical error in saving record to storage: %s", str(e))
            self.logger.exception("Detailed error trace:")
            raise e

    async def get_document_id_by_virtual_record_id(self, virtual_record_id: str) -> tuple[str | None, int | None]:
        """
        Get the document ID and file size by virtual record ID from ArangoDB.
        Returns:
            tuple[str | None, int | None]: (document_id, file_size_bytes) if found, else (None, None).
        """
        if not self.graph_provider:
            self.logger.error("❌ GraphProvider not initialized, cannot get document ID by virtual record ID.")
            raise Exception("GraphProvider not initialized, cannot get document ID by virtual record ID.")

        try:
            collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value

            # Try to find by virtualRecordId field first
            nodes = await self.graph_provider.get_nodes_by_filters(
                collection_name,
                {"virtualRecordId": virtual_record_id}
            )
            # If not found, try to find by _key/id
            if not nodes:
                # Try getting document by key/id
                doc = await self.graph_provider.get_document(
                    virtual_record_id,
                    collection_name
                )
                if doc:
                    nodes = [doc]

            if nodes:
                # Return documentId and fileSizeBytes from the first matching node
                document_id = nodes[0].get("documentId")
                file_size_bytes = nodes[0].get("fileSizeBytes")

                if document_id:
                    return document_id, file_size_bytes
                else:
                    self.logger.warning("Found mapping document but no documentId field for virtual record ID: %s", virtual_record_id)
                    return None, None
            else:
                self.logger.info("No document ID found for virtual record ID: %s", virtual_record_id)
                return None, None
        except Exception as e:
            self.logger.error("❌ Error getting document ID by virtual record ID: %s", str(e))
            raise e

    async def get_record_from_storage(self, virtual_record_id: str, org_id: str) -> dict | None:
            """
            Retrieve a record's content from blob storage using the virtual_record_id.
            Returns:
                str: The content of the record if found, else an empty string.
            """
            overall_start_time = time.time()
            self.logger.info("🔍 Retrieving record from storage for virtual_record_id: %s", virtual_record_id)
            try:
                # Generate JWT token for authorization
                auth_start_time = time.time()
                payload = {
                    "orgId": org_id,
                    "scopes": [TokenScopes.STORAGE_TOKEN.value],
                }

                config_start_time = time.time()
                secret_keys = await self.config_service.get_config(
                    config_node_constants.SECRET_KEYS.value
                )
                config_duration_ms = (time.time() - config_start_time) * 1000
                self.logger.info("⏱️ Secret keys config retrieval completed in %.0fms", config_duration_ms)

                scoped_jwt_secret = secret_keys.get("scopedJwtSecret")
                if not scoped_jwt_secret:
                    raise ValueError("Missing scoped JWT secret")

                jwt_start_time = time.time()
                jwt_token = jwt.encode(payload, scoped_jwt_secret, algorithm="HS256")
                jwt_duration_ms = (time.time() - jwt_start_time) * 1000
                self.logger.info("⏱️ JWT token generation completed in %.0fms", jwt_duration_ms)

                headers = {
                    "Authorization": f"Bearer {jwt_token}"
                }
                auth_duration_ms = (time.time() - auth_start_time) * 1000
                self.logger.info("⏱️ Total authorization setup completed in %.0fms", auth_duration_ms)

                # Get endpoint configuration
                endpoint_config_start_time = time.time()
                endpoints = await self.config_service.get_config(
                    config_node_constants.ENDPOINTS.value
                )
                endpoint_config_duration_ms = (time.time() - endpoint_config_start_time) * 1000
                self.logger.info("⏱️ Endpoints config retrieval completed in %.0fms", endpoint_config_duration_ms)

                nodejs_endpoint = endpoints.get("cm", {}).get("endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value)
                if not nodejs_endpoint:
                    raise ValueError("Missing CM endpoint configuration")

                # Time the document ID lookup
                lookup_start_time = time.time()
                document_id, file_size_bytes = await self.get_document_id_by_virtual_record_id(virtual_record_id)
                lookup_duration_ms = (time.time() - lookup_start_time) * 1000
                if file_size_bytes is not None:
                    self.logger.info("⏱️ Document ID lookup completed in %.0fms for virtual_record_id: %s (size: %d bytes)",
                                    lookup_duration_ms, virtual_record_id, file_size_bytes)
                else:
                    self.logger.info("⏱️ Document ID lookup completed in %.0fms for virtual_record_id: %s (size: unknown)",
                                    lookup_duration_ms, virtual_record_id)

                if not document_id:
                    self.logger.info("No document ID found for virtual record ID: %s", virtual_record_id)
                    return None

                # Build the download URL
                download_url = f"{nodejs_endpoint}{Routes.STORAGE_DOWNLOAD.value.format(documentId=document_id)}"
                download_start_time = time.time()
                async with aiohttp.ClientSession() as session:
                    http_request_start_time = time.time()
                    async with session.get(download_url, headers=headers) as resp:
                        http_request_duration_ms = (time.time() - http_request_start_time) * 1000
                        self.logger.info("⏱️ HTTP request completed in %.0fms for document_id: %s", http_request_duration_ms, document_id)

                        if resp.status == HttpStatusCode.SUCCESS.value:
                            json_parse_start_time = time.time()
                            data = await resp.json()
                            json_parse_duration_ms = (time.time() - json_parse_start_time) * 1000
                            self.logger.info("⏱️ JSON response parsing completed in %.0fms", json_parse_duration_ms)

                            download_duration_ms = (time.time() - download_start_time) * 1000
                            if data.get("record"):
                                self.logger.info("⏱️ Record download completed in %.0fms for document_id: %s", download_duration_ms, document_id)

                                # Process record (handle decompression if needed)
                                process_start_time = time.time()
                                record = self._process_downloaded_record(data)
                                process_duration_ms = (time.time() - process_start_time) * 1000
                                self.logger.info("⏱️ Record processing/decompression completed in %.0fms", process_duration_ms)

                                overall_duration_ms = (time.time() - overall_start_time) * 1000
                                self.logger.info("⏱️ Storage fetch completed in %.0fms for virtual_record_id: %s", overall_duration_ms, virtual_record_id)
                                self.logger.info("✅ Successfully retrieved record from storage for virtual_record_id: %s", virtual_record_id)
                                record_name = record.get("record_name")
                                self.logger.info("🔍 Record name: %s", record_name)
                                return record
                            elif data.get("signedUrl"):
                                signed_url = data.get("signedUrl")
                                self.logger.info("⏱️ Received signed URL, initiating secondary fetch")

                                # Reuse the same session for signed URL fetch
                                signed_url_start_time = time.time()

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
                                        json_parse_start = time.time()
                                        data = json.loads(file_bytes.decode('utf-8'))
                                        json_parse_duration_ms = (time.time() - json_parse_start) * 1000
                                        self.logger.info("⏱️ JSON parsing completed in %.0fms", json_parse_duration_ms)
                                    else:
                                        signed_url_http_start_time = time.time()
                                        async with session.get(signed_url) as res:
                                            signed_url_http_duration_ms = (time.time() - signed_url_http_start_time) * 1000
                                            self.logger.info("⏱️ Signed URL HTTP request completed in %.0fms", signed_url_http_duration_ms)
                                            if res.status == HttpStatusCode.SUCCESS.value:
                                                signed_url_json_start_time = time.time()
                                                data = await res.json()
                                                signed_url_json_duration_ms = (time.time() - signed_url_json_start_time) * 1000
                                                self.logger.info("⏱️ Signed URL JSON parsing completed in %.0fms", signed_url_json_duration_ms)
                                            else:
                                                raise Exception(f"Failed to retrieve record: status {res.status}")
                                except Exception as e:
                                    if use_parallel:
                                        self.logger.warning("⚠️ Parallel download failed: %s. Falling back to single download...", str(e))
                                        try:
                                            fallback_start = time.time()
                                            async with session.get(signed_url) as res:
                                                if res.status == HttpStatusCode.SUCCESS.value:
                                                    data = await res.json()
                                                    fallback_duration_ms = (time.time() - fallback_start) * 1000
                                                    self.logger.info("⏱️ Fallback single download completed in %.0fms", fallback_duration_ms)
                                                else:
                                                    raise Exception(f"Fallback download failed with status {res.status}")
                                        except Exception as fallback_error:
                                            self.logger.error("❌ Fallback download also failed: %s", str(fallback_error))
                                            raise Exception(f"Both parallel and fallback downloads failed: {str(e)}") from fallback_error
                                    else:
                                        self.logger.error("❌ Failed to retrieve record: %s", str(e))
                                        raise

                                # Block B – Post-process (single place; no fallback)
                                signed_url_duration_ms = (time.time() - signed_url_start_time) * 1000
                                total_download_duration_ms = (time.time() - download_start_time) * 1000
                                if data.get("record"):
                                    self.logger.info("⏱️ Signed URL fetch completed in %.0fms for document_id: %s", signed_url_duration_ms, document_id)
                                    self.logger.info("⏱️ Record download completed in %.0fms for document_id: %s", total_download_duration_ms, document_id)
                                    signed_url_process_start_time = time.time()
                                    record = self._process_downloaded_record(data)
                                    signed_url_process_duration_ms = (time.time() - signed_url_process_start_time) * 1000
                                    self.logger.info("⏱️ Record processing/decompression completed in %.0fms", signed_url_process_duration_ms)
                                    overall_duration_ms = (time.time() - overall_start_time) * 1000
                                    self.logger.info("⏱️ Storage fetch completed in %.0fms for virtual_record_id: %s", overall_duration_ms, virtual_record_id)
                                    self.logger.info("✅ Successfully retrieved record from storage for virtual_record_id: %s", virtual_record_id)
                                    record_name = record.get("record_name")
                                    self.logger.info("🔍 Record name: %s", record_name)
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
                self.logger.error("❌ Error retrieving record from storage: %s", str(e))
                self.logger.exception("Detailed error trace:")
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
            self.logger.error("❌ Failed to store virtual record mapping: %s", str(e))
            self.logger.exception("Detailed error trace:")
            raise e


