import aiohttp
from io import BytesIO
from app.config.arangodb_constants import EventTypes
from app.config.arangodb_constants import CollectionNames
import json
import asyncio
import io
from functools import partial
from concurrent.futures import ThreadPoolExecutor


class EventProcessor:
    def __init__(self, logger, processor, arango_service, max_concurrent_tasks=10):
        self.logger = logger
        self.logger.info("🚀 Initializing EventProcessor")
        self.processor = processor
        self.arango_service = arango_service
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self.executor = ThreadPoolExecutor(max_workers=max_concurrent_tasks)
        self.supported_mime_types = [
            "text/gmail_content", 
            "application/vnd.google-apps.presentation", 
            "application/vnd.google-apps.document", 
            "application/vnd.google-apps.spreadsheet"
        ]
        self.supported_extensions = [
            "pdf", "docx", "doc", "xlsx", "xls", "csv", 
            "html", "pptx", "ppt", "md", "txt"
        ]

    async def on_event(self, event_data: dict):
        """
        Process events received from Kafka consumer
        Args:
            event_data: Dictionary containing:
                - event_type: Type of event (create, update, delete)
                - record_id: ID of the record
                - record_version: Version of the record
                - signed_url: Signed URL to download the file
                - connector_name: Name of the connector 
                - metadata_route: Route to get metadata
        """
        # Create a task for each event to process them concurrently
        # This prevents one event from blocking others
        asyncio.create_task(self._process_event(event_data))
        return {"status": "processing"}

    async def _process_event(self, event_data: dict):
        """Internal method to process an event asynchronously"""
        # Use a semaphore to limit the number of concurrent tasks
        async with self.semaphore:
            try:
                self.logger.info(f"📥 Processing event: recordId: {event_data.get('recordId')}, version: {event_data.get('version')}")

                # Extract event type and record ID
                event_type = event_data.get('eventType', EventTypes.NEW_RECORD.value)
                event_data = event_data.get('payload')
                record_id = event_data.get('recordId')
                org_id = event_data.get('orgId')

                if not record_id:
                    self.logger.error("❌ No record ID provided in event data")
                    return

                # Handle delete event separately as it's fast and doesn't need complex processing
                if event_type == EventTypes.DELETE_RECORD.value:
                    await self._handle_delete_event(record_id)
                    return

                # For create/update events, process the document
                await self._handle_create_update_event(event_type, record_id, event_data, org_id)

            except Exception as e:
                self.logger.error(f"❌ Error in event processor for record {event_data.get('payload', {}).get('recordId')}: {repr(e)}")
                # Update record status to error
                await self._update_record_status(
                    event_data.get('payload', {}).get('recordId'), 
                    "ERROR", 
                    f"Error processing: {str(e)[:100]}"
                )

    async def _handle_delete_event(self, record_id):
        """Handle document deletion events"""
        self.logger.info(f"🗑️ Deleting embeddings for record {record_id}")
        await self.processor.indexing_pipeline.delete_embeddings(record_id)

    async def _handle_create_update_event(self, event_type, record_id, event_data, org_id):
        """Handle document creation and update events"""
        # For updates, first delete existing embeddings
        if event_type == EventTypes.UPDATE_RECORD.value:
            self.logger.info(f"🔄 Updating record {record_id} - deleting existing embeddings")
            await self.processor.indexing_pipeline.delete_embeddings(record_id)

        # Update indexing status to IN_PROGRESS
        await self._update_record_status(record_id, "IN_PROGRESS", "IN_PROGRESS")

        # Extract necessary data
        record_version = event_data.get('version', 0)
        signed_url = event_data.get('signedUrl')
        connector = event_data.get('connectorName', '')
        extension = event_data.get('extension', 'unknown')
        mime_type = event_data.get('mimeType', 'unknown')
        
        if extension is None and mime_type != 'text/gmail_content':
            record_name = event_data.get('recordName', '')
            extension = record_name.split('.')[-1] if '.' in record_name else 'unknown'
        
        self.logger.info(f"🚀 File info - mime_type: {mime_type}, extension: {extension}")
        
        # Check if file type is supported
        if mime_type not in self.supported_mime_types and extension not in self.supported_extensions:
            self.logger.info(f"🔴 Unsupported: Mime Type: {mime_type}, Extension: {extension}")
            await self._update_record_status(record_id, "FILE_TYPE_NOT_SUPPORTED", "FILE_TYPE_NOT_SUPPORTED")
            return

        # Process based on file type
        if mime_type == "text/gmail_content":
            await self._process_gmail(record_id, record_version, connector, org_id, event_data)
        elif signed_url:
            # Download and process file
            file_content = await self._download_file(signed_url)
            if file_content:
                await self._process_file(record_id, record_version, connector, org_id, file_content, mime_type, extension)
        else:
            # Process buffer directly
            file_content = event_data.get('buffer')
            await self._process_file(record_id, record_version, connector, org_id, file_content, mime_type, extension)

    async def _update_record_status(self, record_id, indexing_status, extraction_status):
        """Update the status of a record in the database"""
        if not record_id:
            return
            
        try:
            record = await self.arango_service.get_document(record_id, CollectionNames.RECORDS.value)
            doc = dict(record)
            doc.update({
                "indexingStatus": indexing_status,
                "extractionStatus": extraction_status
            })
            await self.arango_service.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)
        except Exception as e:
            self.logger.error(f"❌ Error updating record status for {record_id}: {repr(e)}")

    async def _download_file(self, signed_url):
        """Download file using signed URL with chunked streaming"""
        chunk_size = 1024 * 1024 * 8  # 8MB chunks
        last_logged_size = 0
        total_size = 0
        log_interval = chunk_size

        # Increase timeouts significantly
        timeout = aiohttp.ClientTimeout(
            total=1800,      # 30 minutes total
            connect=60,      # 1 minute for initial connection
            sock_read=300    # 5 minutes per chunk read
        )

        # Pre-allocate a BytesIO buffer for better memory efficiency
        file_buffer = io.BytesIO()
        
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(signed_url) as response:
                    if response.status != 200:
                        self.logger.error(f"❌ Failed to download file: {response.status}")
                        self.logger.error(f"Response headers: {response.headers}")
                        return None
                    
                    # Get content length if available
                    content_length = response.headers.get('Content-Length')
                    if content_length:
                        self.logger.info(f"Expected file size: {int(content_length) / (1024*1024):.2f} MB")
                    
                    self.logger.info("Starting chunked download...")
                    try:
                        async for chunk in response.content.iter_chunked(chunk_size):
                            file_buffer.write(chunk)
                            total_size += len(chunk)
                            if total_size - last_logged_size >= log_interval:
                                self.logger.debug(f"Total size so far: {total_size / (1024*1024):.2f} MB")
                                last_logged_size = total_size
                        
                        # Get the final content and reset buffer position
                        file_content = file_buffer.getvalue()
                        file_buffer.close()
                        return file_content
                        
                    except asyncio.TimeoutError as e:
                        self.logger.error(f"❌ Timeout during file download at {total_size / (1024*1024):.2f} MB: {repr(e)}")
                        raise
                    
                    self.logger.info(f"✅ Download complete. Total size: {total_size / (1024*1024):.2f} MB")
                    
        except aiohttp.ClientError as e:
            self.logger.error(f"❌ Network error during download: {repr(e)}")
        except Exception as e:
            self.logger.error(f"❌ Unexpected error during download: {repr(e)}")
        
        return None

    async def _process_gmail(self, record_id, record_version, connector, org_id, event_data):
        """Process Gmail message"""
        self.logger.info("🚀 Processing Gmail Message")
        try:
            result = await self.processor.process_gmail_message(
                recordName=f"Record-{record_id}",
                recordId=record_id,
                version=record_version,
                source=connector,
                orgId=org_id,
                html_content=event_data.get('body')
            )
            self.logger.info(f"✅ Successfully processed Gmail for record {record_id}")
            return result
        except Exception as e:
            self.logger.error(f"❌ Error processing Gmail message for {record_id}: {repr(e)}")
            await self._update_record_status(record_id, "ERROR", f"Gmail processing error: {str(e)[:100]}")
            raise

    async def _process_file(self, record_id, record_version, connector, org_id, file_content, mime_type, extension):
        """Process file based on its type"""
        try:
            # Google Workspace documents
            if mime_type in self.supported_mime_types:
                await self._process_google_doc(record_id, record_version, org_id, file_content, mime_type)
                return

            # Process other file types based on extension
            process_func = self._get_processor_function(extension)
            if process_func:
                # Run CPU-intensive file processing in a thread pool to avoid blocking the event loop
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    self.executor,
                    partial(
                        process_func,
                        recordName=f"Record-{record_id}",
                        recordId=record_id,
                        version=record_version,
                        source=connector,
                        orgId=org_id,
                        **{f"{extension}_binary": self._prepare_binary(file_content, extension)}
                    )
                )
                self.logger.info(f"✅ Successfully processed document for record {record_id}")
                return result
            else:
                self.logger.info(f"🔴 Unsupported file extension: {extension}")
                await self._update_record_status(record_id, indexing_status="FILE_TYPE_NOT_SUPPORTED", extraction_status="FILE_TYPE_NOT_SUPPORTED")
                
        except Exception as e:
            self.logger.error(f"❌ Error processing file for {record_id}: {repr(e)}")
            await self._update_record_status(record_id, indexing_status="FAILED", extraction_status="FAILED")
            raise

    async def _process_google_doc(self, record_id, record_version, org_id, file_content, mime_type):
        """Process Google Workspace documents"""
        try:
            # Decode JSON content if it's streamed data
            if isinstance(file_content, bytes):
                try:
                    file_content = json.loads(file_content.decode('utf-8'))
                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to decode Google Doc content: {str(e)}")
                    raise

            if mime_type == "application/vnd.google-apps.presentation":
                self.logger.info("🚀 Processing Google Slides")
                result = await self.processor.process_google_slides(record_id, record_version, org_id, file_content)
                return result

            elif mime_type == "application/vnd.google-apps.document":
                self.logger.info("🚀 Processing Google Docs")
                result = await self.processor.process_google_docs(record_id, record_version, org_id, file_content)
                return result

            elif mime_type == "application/vnd.google-apps.spreadsheet":
                self.logger.info("🚀 Processing Google Sheets")
                result = await self.processor.process_google_sheets(record_id, record_version, org_id, file_content)
                return result

        except Exception as e:
            self.logger.error(f"❌ Error processing Google Doc for {record_id}: {repr(e)}")
            await self._update_record_status(record_id, "ERROR", f"Google Doc processing error: {str(e)[:100]}")
            raise

    def _get_processor_function(self, extension):
        """Get the appropriate processor function based on file extension"""
        processors = {
            "pdf": self.processor.process_pdf_document,
            "docx": self.processor.process_docx_document,
            "doc": self.processor.process_doc_document,
            "xlsx": self.processor.process_excel_document,
            "xls": self.processor.process_xls_document,
            "csv": self.processor.process_csv_document,
            "html": self.processor.process_html_document,
            "pptx": self.processor.process_pptx_document,
            "md": self.processor.process_md_document,
            "txt": self.processor.process_txt_document
        }
        return processors.get(extension.lower())

    def _prepare_binary(self, file_content, extension):
        """Prepare binary content based on file extension"""
        if extension == "docx":
            return BytesIO(file_content)
        return file_content