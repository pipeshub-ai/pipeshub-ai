import json

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
                placeholder_data = {
                    "documentName": f"record_{record_id}",
                    "documentPath": "records",
                    "extension": "json"
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

                        # Step 2: Get signed URL
                        self.logger.info("🔑 Getting signed URL for document: %s", document_id)
                        upload_data = {
                            "record": record,
                            "virtualRecordId": virtual_record_id
                        }

                        upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                        upload_result = await self._get_signed_url(session, upload_url, upload_data, headers)

                        signed_url = upload_result.get('signedUrl')
                        if not signed_url:
                            self.logger.error("❌ No signed URL in response for document: %s", document_id)
                            raise Exception("No signed URL in response for document")

                        # Step 3: Upload to signed URL
                        self.logger.info("📤 Uploading record to storage for document: %s", document_id)
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
            query = f'FOR doc IN {collection_name} FILTER doc.virtualRecordId == "{virtual_record_id}" RETURN doc.documentId'
            cursor = self.arango_service.db.aql.execute(query)

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
            self.logger.info("🔍 Retrieving record from storage for virtual_record_id: %s", virtual_record_id)
            try:
                # Generate JWT token for authorization
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

                # Get endpoint configuration
                endpoints = await self.config_service.get_config(
                    config_node_constants.ENDPOINTS.value
                )
                nodejs_endpoint = endpoints.get("cm", {}).get("endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value)
                if not nodejs_endpoint:
                    raise ValueError("Missing CM endpoint configuration")

                document_id = await self.get_document_id_by_virtual_record_id(virtual_record_id)
                if not document_id:
                    self.logger.info("No document ID found for virtual record ID: %s", virtual_record_id)
                    return None

                # Build the download URL
                download_url = f"{nodejs_endpoint}{Routes.STORAGE_DOWNLOAD.value.format(documentId=document_id)}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(download_url, headers=headers) as resp:
                        if resp.status == HttpStatusCode.SUCCESS.value:
                            data = await resp.json()
                            if(data.get("signedUrl")):
                                signed_url = data.get("signedUrl")
                                # Reuse the same session for signed URL fetch
                                async with session.get(signed_url, headers=headers) as resp:
                                        if resp.status == HttpStatusCode.OK.value:
                                            data = await resp.json()
                            self.logger.info("✅ Successfully retrieved record for virtual_record_id from blob storage: %s", virtual_record_id)
                            return data.get("record")
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
            mapping_key = f"{virtual_record_id}_{document_id}"

            mapping_document = {
                "_key": mapping_key,
                "virtualRecordId": virtual_record_id,
                "documentId": document_id,
                "createdAt": get_epoch_timestamp_in_ms()
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


# async def store_virtual_record_mapping_standalone(
#     arango_service: ArangoService,
#     virtual_record_id: str,
#     document_id: str,
#     logger=None
# ) -> bool:
#     """
#     Standalone function to store the mapping between virtual_record_id and document_id in ArangoDB.

#     Args:
#         arango_service (ArangoService): The ArangoDB service instance
#         virtual_record_id (str): The virtual record ID
#         document_id (str): The document ID from blob storage
#         logger: Optional logger instance for logging

#     Returns:
#         bool: True if successful, False otherwise.
#     """
#     if not arango_service:
#         if logger:
#             logger.warning("ArangoService not provided, cannot store virtual record mapping.")
#         return False

#     try:
#         collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value

#         # Create a unique key for the mapping using both IDs
#         mapping_key = f"{virtual_record_id}_{document_id}"

#         mapping_document = {
#             "_key": mapping_key,
#             "virtualRecordId": virtual_record_id,
#             "documentId": document_id,
#             "createdAt": get_epoch_timestamp_in_ms()
#         }

#         success = await arango_service.batch_upsert_nodes(
#             [mapping_document],
#             collection_name
#         )

#         if success:
#             if logger:
#                 logger.info("✅ Successfully stored virtual record mapping: virtual_record_id=%s, document_id=%s", virtual_record_id, document_id)
#             return True
#         else:
#             if logger:
#                 logger.error("❌ Failed to store virtual record mapping")
#             return False

#     except Exception as e:
#         if logger:
#             logger.error("❌ Failed to store virtual record mapping: %s", str(e))
#             logger.exception("Detailed error trace:")
#         return False
