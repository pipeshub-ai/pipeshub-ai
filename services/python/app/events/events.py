import aiohttp
from io import BytesIO
from app.config.arangodb_constants import EventTypes
from app.config.arangodb_constants import CollectionNames
import json
import asyncio
import io

class EventProcessor:
    def __init__(self, logger, processor, arango_service):
        self.logger = logger
        self.logger.info("🚀 Initializing EventProcessor")
        self.processor = processor
        self.arango_service = arango_service

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
        try:
            self.logger.info(f"📥 Processing event: {event_data}")

            # Extract event type and record ID
            event_type = event_data.get(
                'eventType', EventTypes.NEW_RECORD.value)  # default to create
            event_data = event_data.get('payload')
            record_id = event_data.get('recordId')
            org_id = event_data.get('orgId')

            if not record_id:
                self.logger.error("❌ No record ID provided in event data")
                return
            
            # Handle delete event
            if event_type == EventTypes.DELETE_RECORD.value:
                self.logger.info(f"🗑️ Deleting embeddings for record {record_id}")
                await self.processor.indexing_pipeline.delete_embeddings(record_id)
                return

            # For both create and update events, we need to process the document
            if event_type == EventTypes.UPDATE_RECORD.value:
                # For updates, first delete existing embeddings
                self.logger.info(f"""🔄 Updating record {record_id} - deleting existing embeddings""")
                await self.processor.indexing_pipeline.delete_embeddings(record_id)

            # Update indexing status to IN_PROGRESS
            record = await self.arango_service.get_document(record_id, CollectionNames.RECORDS.value)
            if record is None:
                self.logger.error(f"❌ Record {record_id} not found in database")
                return
            doc = dict(record)

            # Update with new metadata fields
            doc.update({
                "indexingStatus": "IN_PROGRESS",
                "extractionStatus": "IN_PROGRESS"
            })

            docs = [doc]
            await self.arango_service.batch_upsert_nodes(docs, CollectionNames.RECORDS.value)   

            # Extract necessary data
            record_version = event_data.get('version', 0)
            signed_url = event_data.get('signedUrl')
            connector = event_data.get('connectorName', '')
            extension = event_data.get('extension', 'unknown')
            mime_type = event_data.get('mimeType', 'unknown')
            
            if extension is None and mime_type != 'text/gmail_content':
                extension = event_data['recordName'].split('.')[-1]
            
            self.logger.info("🚀 Checking for mime_type")
            self.logger.info("🚀 mime_type: %s", mime_type)
            self.logger.info("🚀 extension: %s", extension)
            
            supported_mime_types = ["text/gmail_content", 
                                 "application/vnd.google-apps.presentation", 
                                 "application/vnd.google-apps.document", 
                                 "application/vnd.google-apps.spreadsheet"]
            
            supported_extensions = ["pdf", "docx", "doc", "xlsx", "xls", "csv", "html", "pptx", "ppt", "md", "txt"]

            if mime_type not in supported_mime_types and extension not in supported_extensions:
                self.logger.info(f"🔴🔴🔴 Unsupported file: Mime Type: {mime_type}, Extension: {extension} 🔴🔴🔴")
                doc = docs[0]
                doc.update({
                    "indexingStatus": "FILE_TYPE_NOT_SUPPORTED",
                    "extractionStatus": "FILE_TYPE_NOT_SUPPORTED"
                })
                docs = [doc]
                await self.arango_service.batch_upsert_nodes(docs, CollectionNames.RECORDS.value)

                return

            if mime_type == "text/gmail_content":
                self.logger.info("🚀 Processing Gmail Message")
                result = await self.processor.process_gmail_message(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    html_content=event_data.get('body'))
                
                self.logger.info(f"Content: {event_data.get('body')}")
                return result
            
            if signed_url:
                self.logger.debug(f"Signed URL: {signed_url}")
                # Download file using signed URL with chunked streaming
                chunk_size = 1024 * 1024 * 8  # 8MB chunks
                last_logged_size = 0
                total_size = 0
                log_interval = chunk_size  # Log at same interval as chunk size
                # Increase timeouts significantly
                timeout = aiohttp.ClientTimeout(
                    total=600,      # 10 minutes total
                    connect=30,      # 1/2 minute for initial connection
                    sock_read=60    # 1 minute per chunk read
                )
                file_buffer = io.BytesIO()
                try:
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        try:
                            async with session.get(signed_url) as response:
                                if response.status != 200:
                                    self.logger.error(f"❌ Failed to download file: {response.status}")
                                    self.logger.error(f"Response headers: {response.headers}")
                                    return
                                
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
                                    
                                    file_content = file_buffer.getvalue()
                                except asyncio.TimeoutError as e:
                                    self.logger.error(f"❌ Timeout during file download at {total_size / (1024*1024):.2f} MB: {repr(e)}")
                                    doc.update({
                                        "indexingStatus": "FAILED",
                                        "extractionStatus": "FAILED",
                                        "errorMessage": f"Timeout during file download: {str(e)}"
                                    })
                                    await self.arango_service.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)
                                    raise
                                finally:
                                    file_buffer.close()
                                
                                self.logger.info(f"✅ Download complete. Total size: {total_size / (1024*1024):.2f} MB")

                        except aiohttp.ClientError as e:
                            self.logger.error(f"❌ Network error during download: {repr(e)}")
                            doc.update({
                                "indexingStatus": "FAILED",
                                "extractionStatus": "FAILED",
                                "errorMessage": f"Network error during download: {str(e)}"
                            })
                            await self.arango_service.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)
                            raise
                finally:
                    # Ensure buffer is closed even if an error occurs outside the inner try blocks
                    if not file_buffer.closed:
                        file_buffer.close()
            else:
                file_content = event_data.get('buffer')
            
            self.logger.debug(f"file_content type: {type(file_content)}")
                
            if mime_type == "application/vnd.google-apps.presentation":
                self.logger.info("🚀 Processing Google Slides")
                # Decode JSON content if it's streamed data
                if isinstance(file_content, bytes):
                    try:
                        file_content = json.loads(file_content.decode('utf-8'))
                    except json.JSONDecodeError as e:
                        self.logger.error(f"Failed to decode Google Slides content: {str(e)}")
                        raise
                result = await self.processor.process_google_slides(record_id, record_version, org_id, file_content)
                return result

            if mime_type == "application/vnd.google-apps.document":
                self.logger.info("🚀 Processing Google Docs")
                # Decode JSON content if it's streamed data
                if isinstance(file_content, bytes):
                    try:
                        file_content = json.loads(file_content.decode('utf-8'))
                    except json.JSONDecodeError as e:
                        self.logger.error(f"Failed to decode Google Docs content: {str(e)}")
                        raise
                result = await self.processor.process_google_docs(record_id, record_version, org_id, file_content)
                return result

            if mime_type == "application/vnd.google-apps.spreadsheet":
                self.logger.info("🚀 Processing Google Sheets")
                # Decode JSON content if it's streamed data
                if isinstance(file_content, bytes):
                    try:
                        file_content = json.loads(file_content.decode('utf-8'))
                    except json.JSONDecodeError as e:
                        self.logger.error(f"Failed to decode Google Sheets content: {str(e)}")
                        raise
                result = await self.processor.process_google_sheets(record_id, record_version, org_id, file_content)
                return result
            

            if extension == "pdf":
                result = await self.processor.process_pdf_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    pdf_binary=file_content
                )

            elif extension == "docx":
                result = await self.processor.process_docx_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    docx_binary=BytesIO(file_content)
                )
            
            elif extension == "doc":
                result = await self.processor.process_doc_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    doc_binary=file_content
                )
            elif extension in ['xlsx']:
                result = await self.processor.process_excel_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    excel_binary=file_content
                )
            elif extension == "xls":
                result = await self.processor.process_xls_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    xls_binary=file_content
                )
            elif extension == "csv":
                result = await self.processor.process_csv_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    csv_binary=file_content
                )
                
            elif extension == "html":
                result = await self.processor.process_html_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    html_content=file_content
                )
                
            elif extension == "pptx":
                result = await self.processor.process_pptx_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    pptx_binary=file_content
                )
                
            elif extension == "ppt":
                result = await self.processor.process_ppt_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    ppt_binary=file_content
                )
                
            elif extension == "md":
                result = await self.processor.process_md_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    md_binary=file_content
                )
            elif extension == "txt":
                result = await self.processor.process_txt_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    txt_binary=file_content
                )

            else:
                self.logger.info(f"""🔴🔴🔴 Unsupported file extension: {
                               extension} 🔴🔴🔴""")
                doc = docs[0]
                doc.update({
                    "indexingStatus": "FILE_TYPE_NOT_SUPPORTED",
                    "extractionStatus": "FILE_TYPE_NOT_SUPPORTED"
                })
                docs = [doc]
                await self.arango_service.batch_upsert_nodes(docs, CollectionNames.RECORDS.value)
                
                return


            self.logger.info(
                f"✅ Successfully processed document for record {record_id}")
            return result

        except Exception as e:
            # Let the error bubble up to Kafka consumer
            self.logger.error(f"❌ Error in event processor: {repr(e)}")
            raise