import json
import time

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
from app.connectors.services.base_arango_service import BaseArangoService
from app.modules.transformers.transformer import TransformContext, Transformer
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class BlobStorage(Transformer):
    def __init__(self,logger,config_service, arango_service: BaseArangoService = None) -> None:
        self.logger = logger
        self.config_service = config_service
        self.arango_service = arango_service

    def _compress_record(self, record: dict) -> tuple[str, int]:
        """
        Compress record data using msgpack + zstd.
        Returns: (base64_encoded_compressed_data, original_size)
        """
        import base64
        import msgpack
        import zstandard as zstd
        
        # Serialize directly to bytes using msgpack (faster than JSON)
        msgpack_bytes = msgpack.packb(record)
        original_size = len(msgpack_bytes)
        
        # Compression level 10: maximum compression
        compressor = zstd.ZstdCompressor(level=10)
        compressed = compressor.compress(msgpack_bytes)
        
        compressed_size = len(compressed)
        ratio = (1 - compressed_size / original_size) * 100
        self.logger.info("📦 Compressed record (msgpack): %d -> %d bytes (%.1f%% reduction)", 
                        original_size, compressed_size, ratio)
        
        return base64.b64encode(compressed).decode('utf-8'), original_size

    def _decompress_record(self, compressed_data: str) -> dict:
        """
        Decompress zstd-compressed record data (msgpack format).
        """
        import base64
        import msgpack
        import zstandard as zstd
        
        compressed_bytes = base64.b64decode(compressed_data)
        
        decompressor = zstd.ZstdDecompressor()
        decompressed = decompressor.decompress(compressed_bytes)
        
        # Direct msgpack parsing - no UTF-8 decode needed
        return msgpack.unpackb(decompressed)

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
        import msgpack
        
        # NEW FORMAT: Check for isCompressed flag
        if data.get("isCompressed"):
            self.logger.info("🔍 Decompressing compressed record (msgpack format)")
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
                self.logger.info("⏱️ Base64 decode completed in %.0fms (decoded size: %d bytes)", base64_duration_ms, len(compressed_bytes))
                
                # Step 2: Decompress
                decompress_start = time.time()
                decompressed_bytes = self._decompress_bytes(compressed_bytes)
                decompress_duration_ms = (time.time() - decompress_start) * 1000
                self.logger.info("⏱️ Decompression completed in %.0fms (decompressed size: %d bytes)", decompress_duration_ms, len(decompressed_bytes))
                
                # Step 3: MessagePack parse (no UTF-8 decode needed - direct bytes to dict)
                msgpack_parse_start = time.time()
                record = msgpack.unpackb(decompressed_bytes)
                msgpack_parse_duration_ms = (time.time() - msgpack_parse_start) * 1000
                self.logger.info("⏱️ MessagePack parsing completed in %.0fms", msgpack_parse_duration_ms)
                
                overall_processing_ms = (time.time() - overall_processing_start) * 1000
                self.logger.info("📦 Total record processing completed in %.0fms (base64: %.0fms, decompress: %.0fms, msgpack: %.0fms)", 
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

    async def apply(self, ctx: TransformContext) -> TransformContext:
        record = ctx.record
        org_id = record.org_id
        record_id = record.id
        virtual_record_id = record.virtual_record_id
        record_dict = record.model_dump(mode='json')
        document_id = await self.save_record_to_storage(org_id, record_id, virtual_record_id, record_dict)

        # Store the mapping if we have both IDs and arango_service is available
        if document_id and self.arango_service:
            await self.store_virtual_record_mapping(virtual_record_id, document_id)

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

    async def save_record_to_storage(self, org_id: str, record_id: str, virtual_record_id: str, record: dict) -> str | None:
        """
        Save document to storage using FormData upload
        Returns:
            str | None: document_id if successful, None if failed
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

            if storage_type == "local":
                try:
                    async with aiohttp.ClientSession() as session:
                        upload_data = {
                            "record": record,
                            "virtualRecordId": virtual_record_id
                        }
                        json_data = json.dumps(upload_data).encode('utf-8')

                        # Create form data
                        form_data = aiohttp.FormData()
                        form_data.add_field('file',
                                        json_data,
                                        filename=f'record_{record_id}.json',
                                        content_type='application/json')
                        form_data.add_field('documentName', f'record_{record_id}')
                        form_data.add_field('documentPath', 'records')
                        form_data.add_field('isVersionedFile', 'true')
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
                            return document_id
                except aiohttp.ClientError as e:
                    self.logger.error("❌ Network error during upload process: %s", str(e))
                    raise e
                except Exception as e:
                    self.logger.error("❌ Unexpected error during upload process: %s", str(e))
                    self.logger.exception("Detailed error trace:")
                    raise e
            else:
                # Compress record first for S3 storage
                try:
                    start_time = time.time()
                    compressed_data, original_size = self._compress_record(record)
                    compression_time_ms = (time.time() - start_time) * 1000
                    self.logger.info("⏱️ Compression completed in %.0fms", compression_time_ms)
                    
                    # Prepare placeholder with compression metadata for MongoDB
                    placeholder_data = {
                        "documentName": f"record_{record_id}",
                        "documentPath": f"records/{virtual_record_id}",
                        "extension": "msgpack",
                        "customMetadata": [
                            {
                                "key": "compression",
                                "value": {
                                    "algorithm": "zstd",
                                    "level": 10,
                                    "format": "msgpack",
                                    "version": "v0",
                                    "originalSize": original_size,
                                    "compressed": True
                                }
                            },
                            {
                                "key": "virtualRecordId",
                                "value": virtual_record_id
                            }
                        ]
                    }
                    compressed_record = compressed_data
                except Exception as e:
                    self.logger.warning("⚠️ Compression failed, uploading uncompressed: %s", str(e))
                    # Fallback to uncompressed
                    placeholder_data = {
                        "documentName": f"record_{record_id}",
                        "documentPath": f"records/{virtual_record_id}",
                        "extension": "json",
                        "customMetadata": [
                            {
                                "key": "virtualRecordId",
                                "value": virtual_record_id
                            }
                        ]
                    }
                    compressed_record = None

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
                        signed_url_request = {
                            "virtualRecordId": virtual_record_id
                        }

                        upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                        upload_result = await self._get_signed_url(session, upload_url, signed_url_request, headers)

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
                                "virtualRecordId": virtual_record_id
                            }
                        
                        await self._upload_to_signed_url(session, signed_url, upload_data)

                        self.logger.info("✅ Successfully completed record storage process for document: %s", document_id)
                        return document_id

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

    async def get_document_id_by_virtual_record_id(self, virtual_record_id: str) -> str:
        """
        Get the document ID by virtual record ID from ArangoDB.
        Returns:
            str: The document ID if found, else None.
        """
        if not self.arango_service:
            self.logger.error("❌ ArangoService not initialized, cannot get document ID by virtual record ID.")
            raise Exception("ArangoService not initialized, cannot get document ID by virtual record ID.")

        try:
            collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
            query = 'FOR doc IN @@collection FILTER doc.virtualRecordId == @virtualRecordId OR doc._key == @virtualRecordId RETURN doc.documentId'
            bind_vars = {
                '@collection': collection_name,
                'virtualRecordId': virtual_record_id
            }
            cursor = self.arango_service.db.aql.execute(query, bind_vars=bind_vars)

            # Check if cursor has any results before calling next()
            results = list(cursor)
            if results:
                return results[0]  # Return first document ID
            else:
                self.logger.info("No document ID found for virtual record ID: %s", virtual_record_id)
                return None
        except Exception as e:
            self.logger.error("❌ Error getting document ID by virtual record ID: %s", str(e))
            raise e

    async def get_record_from_storage(self, virtual_record_id: str, org_id: str) -> str:
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
                document_id = await self.get_document_id_by_virtual_record_id(virtual_record_id)
                lookup_duration_ms = (time.time() - lookup_start_time) * 1000
                self.logger.info("⏱️ Document ID lookup completed in %.0fms for virtual_record_id: %s", lookup_duration_ms, virtual_record_id)
                
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
                                return record
                            elif data.get("signedUrl"):
                                signed_url = data.get("signedUrl")
                                self.logger.info("⏱️ Received signed URL, initiating secondary fetch")
                                
                                # Reuse the same session for signed URL fetch
                                signed_url_start_time = time.time()
                                signed_url_http_start_time = time.time()
                                async with session.get(signed_url) as res:
                                    signed_url_http_duration_ms = (time.time() - signed_url_http_start_time) * 1000
                                    self.logger.info("⏱️ Signed URL HTTP request completed in %.0fms", signed_url_http_duration_ms)
                                    
                                    if res.status == HttpStatusCode.SUCCESS.value:
                                        signed_url_json_start_time = time.time()
                                        data = await res.json()
                                        signed_url_json_duration_ms = (time.time() - signed_url_json_start_time) * 1000
                                        self.logger.info("⏱️ Signed URL JSON parsing completed in %.0fms", signed_url_json_duration_ms)
                                        
                                        signed_url_duration_ms = (time.time() - signed_url_start_time) * 1000
                                        total_download_duration_ms = (time.time() - download_start_time) * 1000

                                        if data.get("record"):
                                            self.logger.info("⏱️ Signed URL fetch completed in %.0fms for document_id: %s", signed_url_duration_ms, document_id)
                                            self.logger.info("⏱️ Record download completed in %.0fms for document_id: %s", total_download_duration_ms, document_id)
                                            
                                            # Process record (handle decompression if needed)
                                            signed_url_process_start_time = time.time()
                                            record = self._process_downloaded_record(data)
                                            signed_url_process_duration_ms = (time.time() - signed_url_process_start_time) * 1000
                                            self.logger.info("⏱️ Record processing/decompression completed in %.0fms", signed_url_process_duration_ms)
                                            
                                            overall_duration_ms = (time.time() - overall_start_time) * 1000
                                            self.logger.info("⏱️ Storage fetch completed in %.0fms for virtual_record_id: %s", overall_duration_ms, virtual_record_id)
                                            self.logger.info("✅ Successfully retrieved record from storage for virtual_record_id: %s", virtual_record_id)
                                            return record
                                        else:
                                            self.logger.error("❌ No record found for virtual_record_id: %s", virtual_record_id)
                                            raise Exception("No record found for virtual_record_id")
                                    else:
                                        self.logger.error("❌ Failed to retrieve record: status %s, virtual_record_id: %s", resp.status, virtual_record_id)
                                        raise Exception("Failed to retrieve record from storage")
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

    async def store_virtual_record_mapping(self, virtual_record_id: str, document_id: str) -> bool:
        """
        Stores the mapping between virtual_record_id and document_id in ArangoDB.
        Returns:
            bool: True if successful, False otherwise.
        """

        try:
            collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value

            # Create a unique key for the mapping using both IDs
            mapping_key = virtual_record_id

            mapping_document = {
                "_key": mapping_key,
                "documentId": document_id,
                "updatedAt": get_epoch_timestamp_in_ms()
            }

            success = await self.arango_service.batch_upsert_nodes(
                [mapping_document],
                collection_name
            )

            if success:
                self.logger.info("✅ Successfully stored virtual record mapping: virtual_record_id=%s, document_id=%s", virtual_record_id, document_id)
                return True
            else:
                self.logger.error("❌ Failed to store virtual record mapping")
                raise Exception("Failed to store virtual record mapping")

        except Exception as e:
            self.logger.error("❌ Failed to store virtual record mapping: %s", str(e))
            self.logger.exception("Detailed error trace:")
            raise e


