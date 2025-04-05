from typing import Dict, Any
from typing_extensions import List
import json
import spacy
import fitz
from app.utils.logger import create_logger
import os
from datetime import datetime

from app.modules.parsers.pdf.ocr_handler import OCRHandler
from app.config.arangodb_constants import CollectionNames
from app.config.configuration_service import config_node_constants
from app.config.ai_models_named_constants import OCRProvider, AzureDocIntelligenceModel
from app.utils.llm import get_llm

logger = create_logger(__name__)

class Processor:
    def __init__(self, config_service, domain_extractor, indexing_pipeline, arango_service, parsers):
        logger.info("🚀 Initializing Processor")
        self.domain_extractor = domain_extractor
        self.indexing_pipeline = indexing_pipeline
        self.arango_service = arango_service
        self.parsers = parsers
        self.config_service = config_service
        
    async def process_google_slides(self, record_id, record_version):
        logger.info("🚀 Processing Google Slides")

        return {"status": "success", "message": "Google Slides processed successfully"}

    async def process_google_docs(self, record_id, record_version):
        """Process Google Docs document and extract structured content

        Args:
            record_id (str): ID of the Google Doc
            record_version (str): Version of the document
        """
        logger.info(
            f"🚀 Starting Google Docs processing for record: {record_id}")

        try:
            # Initialize Google Docs parser
            logger.debug("📄 Processing Google Docs content")
            parser = self.parsers['google_docs']
            docs_result = await parser.parse_doc_content(record_id)

            # Extract domain metadata from content
            logger.info("🎯 Extracting domain metadata")
            if docs_result and docs_result.get('elements'):
                # Join all text content with newlines
                text_content = "\n".join(
                    element['text'].strip() for element in docs_result['elements']
                    if element.get('text') and element['text'].strip()
                )

                # Extract metadata using domain extractor
                try:
                    logger.info("🎯 Extracting metadata from content")
                    metadata = await self.domain_extractor.extract_metadata(text_content)
                    logger.info(f"✅ Extracted metadata: {metadata}")
                    record = await self.domain_extractor.save_metadata_to_arango(record_id, metadata)
                    docs_result["metadata"] = record
                except Exception as e:
                    logger.error(f"❌ Error extracting metadata: {str(e)}")
                    docs_result["metadata"] = None

            # Format content for output
            formatted_content = ""
            numbered_items = []

            # Process elements for numbering and formatting
            logger.debug("📝 Processing elements")
            for idx, element in enumerate(docs_result.get('elements', []), 1):
                if element.get('text', '').strip():
                    # Create element entry
                    element_entry = {
                        "number": idx,
                        "content": element['text'].strip(),
                        "type": element['type'],
                        "style": element.get('style', {}),
                        "links": element.get('links', [])
                    }
                    numbered_items.append(element_entry)
                    formatted_content += f"""[{idx}] {
                        element['text'].strip()}\n\n"""

            # Process tables
            tables = docs_result.get("tables", [])
            if tables:
                logger.debug("📊 Processing tables")
                for idx, table in enumerate(tables, 1):
                    table_entry = {
                        "number": f"T{idx}",
                        "content": table,
                        "type": "table"
                    }
                    numbered_items.append(table_entry)

            # Prepare metadata
            logger.debug("📋 Preparing metadata")
            metadata = {
                "recordId": record_id,
                "version": record_version,
                "domain_metadata": docs_result.get("metadata"),
                "has_header": bool(docs_result.get("headers")),
                "has_footer": bool(docs_result.get("footers")),
                "image_count": len(docs_result.get("images", [])),
                "table_count": len(tables)
            }

            # Create sentence data for indexing
            sentence_data = []
            for element in docs_result.get('elements', []):
                if element.get('text'):
                    # Simple sentence splitting (can be improved with NLP)
                    sentences = [
                        s.strip() + '.' for s in element['text'].split('.') if s.strip()]
                    for sentence in sentences:
                        sentence_data.append({
                            'text': sentence,
                            'bounding_box': None  # Google Docs doesn't have bounding boxes
                        })

            # Index sentences if available
            if sentence_data:
                logger.debug("📑 Creating semantic sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

            logger.info("✅ Google Docs processing completed successfully")
            return {
                "docs_result": docs_result,
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata
            }

        except Exception as e:
            logger.error(f"❌ Error processing Google Docs document: {str(e)}")
            raise

    async def process_google_sheets(self, record_id, record_version):
        logger.info("🚀 Processing Google Sheets")
        # Implement Google Sheets processing logic here
        return {"status": "success", "message": "Google Sheets processed successfully"}
    
    async def process_gmail_message(self, recordName, recordId, version, source, orgId, html_content):
        logger.info("🚀 Processing Gmail Message")

        try:
            # Convert binary to string
            html_content = html_content.decode('utf-8') if isinstance(html_content, bytes) else html_content
            logger.debug(f"📄 Decoded HTML content length: {len(html_content)}")

            # Initialize HTML parser and parse content
            logger.debug("📄 Processing HTML content")
            parser = self.parsers['html']
            html_result = parser.parse_string(html_content)

            # Get the full document structure
            doc_dict = html_result.export_to_dict()
            logger.debug("📑 Document structure processed")

            # Process content in reading order
            logger.debug("📑 Processing document structure in reading order")
            ordered_content = self._process_content_in_order(doc_dict)
            
            # Extract text in reading order
            text_content = "\n".join(
                item['text'].strip()
                for item in ordered_content
                if item['text'].strip()
            )

            # Extract domain metadata
            logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    logger.info(f"🎯 Extracting metadata from HTML content {text_content}")
                    metadata = await self.domain_extractor.extract_metadata(text_content)
                    logger.info(f"✅ Extracted metadata: {metadata}")
                    record = await self.domain_extractor.save_metadata_to_arango(recordId, metadata)
                    mail = await self.arango_service.get_document(recordId, CollectionNames.MAILS.value)
                    domain_metadata = {**record, **mail}
                    domain_metadata['extension'] = 'html'
                    domain_metadata['mimeType'] = 'text/html'
                except Exception as e:
                    logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create sentence data for indexing
            logger.debug("📑 Creating semantic sentences")
            sentence_data = []
            
            # Keep track of previous items for context
            context_window = []
            context_window_size = 3  # Number of previous items to include for context

            for idx, item in enumerate(ordered_content, 1):
                if item['text'].strip():
                    context = item['context']
                    
                    # Create context text from previous items
                    previous_context = " ".join([prev['text'].strip() for prev in context_window])
                    
                    # Current item's context with previous items
                    full_context = {
                        "previous": previous_context,
                        "current": item['text'].strip()
                    }

                    sentence_data.append({
                        'text': item['text'].strip(),
                        'bounding_box': None,
                        'metadata': {
                            **(domain_metadata or {}),
                            "recordId": recordId,
                            "blockType": context.get('label', 'text'),
                            "blockNum": idx,
                            "blockText": json.dumps(full_context),  # Include full context
                            "slideNumber": context.get('slide_number'),
                            "level": context.get('level')
                        }
                    })

                    # Update context window
                    context_window.append(item)
                    if len(context_window) > context_window_size:
                        context_window.pop(0)

            # Index sentences if available
            if sentence_data:
                logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "schema_name": doc_dict.get('schema_name'),
                    "version": doc_dict.get('version'),
                    "name": doc_dict.get('name'),
                    "origin": doc_dict.get('origin')
                },
                "structure_info": {
                    "text_count": len(doc_dict.get('texts', [])),
                    "group_count": len(doc_dict.get('groups', [])),
                    "list_count": len([item for item in ordered_content if item.get('context', {}).get('list_info')]),
                    "heading_count": len([item for item in ordered_content if item.get('context', {}).get('label') == 'heading'])
                }
            }

            logger.info("✅ HTML processing completed successfully")
            return {
                "html_result": {
                    "document_structure": {
                        "body": doc_dict.get('body'),
                        "groups": doc_dict.get('groups', [])
                    },
                    "metadata": domain_metadata
                },
                "formatted_content": text_content,
                "numbered_items": ordered_content,
                "metadata": metadata
            }

        except Exception as e:
            logger.error(f"❌ Error processing HTML document: {str(e)}")
            raise

    async def process_pdf_document(self, recordName, recordId, version, source, orgId, pdf_binary):
        """Process PDF document with automatic OCR selection based on environment settings"""
        logger.info(
            f"🚀 Starting PDF document processing for record: {recordName}")

        try:
            logger.debug("📄 Processing PDF binary content")
            # Get OCR configurations
            ai_models = await self.config_service.get_config(config_node_constants.AI_MODELS.value)
            ocr_configs = ai_models['ocr']
            print("OCR configs", ocr_configs)
            
            # Configure OCR handler
            logger.debug("🛠️ Configuring OCR handler")
            handler = None
            
            for config in ocr_configs:
                provider = config['provider']
                logger.info(f"🔧 Checking OCR provider: {provider}")
                
                if provider == OCRProvider.AZURE_PROVIDER.value:
                    logger.debug("☁️ Setting up Azure OCR handler")
                    handler = OCRHandler(
                        OCRProvider.AZURE_PROVIDER.value,
                        endpoint=config['configuration']['endpoint'],
                        key=config['configuration']['apiKey'],
                        model_id=AzureDocIntelligenceModel.PREBUILT_DOCUMENT.value
                    )
                    break
                elif provider == OCRProvider.OCRMYPDF_PROVIDER.value:
                    logger.debug("📚 Setting up PyMuPDF OCR handler")
                    handler = OCRHandler(
                        OCRProvider.OCRMYPDF_PROVIDER.value
                    )
                    break
            
            if not handler:
                logger.debug("📚 Setting up PyMuPDF OCR handler")
                handler = OCRHandler(
                    OCRProvider.OCRMYPDF_PROVIDER.value
                )
                provider = OCRProvider.OCRMYPDF_PROVIDER.value
            
            # Process document
            logger.info("🔄 Processing document with OCR handler")
            ocr_result = await handler.process_document(pdf_binary)
            logger.debug("✅ OCR processing completed")

            # Extract domain metadata from paragraphs
            logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            paragraphs = ocr_result.get("paragraphs", [])
            sentences = ocr_result.get("sentences", [])
            if paragraphs:
                # Join all paragraph content with newlines
                paragraphs_text = "\n".join(
                    p["content"].strip() for p in paragraphs
                    if p.get("content") and p["content"].strip()
                )

                # Extract metadata using domain extractor
                try:
                    logger.info(f"""🎯 Extracting metadata from paragraphs: {
                                paragraphs_text}""")
                    metadata = await self.domain_extractor.extract_metadata(paragraphs_text)
                    logger.info(f"✅ Extracted metadata: {metadata}")
                    record = await self.domain_extractor.save_metadata_to_arango(recordId, metadata)
                    file = await self.arango_service.get_document(recordId, CollectionNames.FILES.value)
                    domain_metadata = record
                    ocr_result["metadata"] = {**record, **file}
                except Exception as e:
                    logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None
                    ocr_result["metadata"] = None

            # Use the OCR-processed PDF for highlighting if available
            highlight_pdf_binary = handler.strategy.ocr_pdf_content or pdf_binary

            # Initialize containers
            logger.debug("🏗️ Initializing result containers")
            output_pdf_path = f"{recordName}_highlighted.pdf"
            formatted_content = ""
            numbered_paragraphs = []

            # Process paragraphs for numbering and formatting
            logger.debug("📝 Processing paragraphs")
            paragraphs = ocr_result.get("paragraphs", [])
            for paragraph in paragraphs:
                paragraph['blockText'] = json.dumps(paragraph['content'])

            # Create sentence data for indexing
            logger.debug("📑 Creating semantic sentences")
            sentence_data = []
            sentences = ocr_result.get("sentences", [])
            if sentences:
                logger.debug("📑 Creating semantic sentences")

                # Prepare sentences for indexing with separated metadata
                sentence_data = [{
                    'text': s["content"].strip(),
                    'bounding_box': s["bounding_box"],
                    'metadata': {
                        **ocr_result.get("metadata"),
                        "recordId": recordId,
                        "blockText": s["content"].strip(),
                        "blockType": s.get("block_type", 0),
                        "blockNum": s.get("block_number", 0),
                        "pageNum": s.get("page_number", 0)
                    }
                } for idx, s in enumerate(sentences) if s.get("content")]

            # Index sentences if available
            if sentence_data:
                pipeline = self.indexing_pipeline
                # Get chunks (these will be merged based on semantic similarity)
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            logger.debug("📋 Preparing metadata")
            metadata = {
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "ocr_provider": provider,
                    "page_count": len(set(p.get("page_number", 1) for p in paragraphs))
                },
                "structure_info": {
                    "paragraph_count": len(paragraphs),
                    "sentence_count": len(sentences),
                    "average_confidence": sum(p.get("confidence", 1.0) for p in paragraphs) / len(paragraphs) if paragraphs else 0
                }
            }

            logger.info("✅ PDF processing completed successfully")
            return {
                "ocr_result": ocr_result,
                "formatted_content": formatted_content,
                "numbered_paragraphs": numbered_paragraphs,
                "metadata": metadata
            }

        except Exception as e:
            logger.error(f"❌ Error processing PDF document: {str(e)}")
            raise

    async def process_doc_document(self, recordName, recordId, version, source, orgId, doc_binary):
        logger.info(
            f"🚀 Starting DOC document processing for record: {recordName}")
        # Implement DOC processing logic here
        parser = self.parsers['doc']
        doc_result = parser.convert_doc_to_docx(doc_binary)
        await self.process_docx_document(recordName, recordId,  version, source, orgId, doc_result)

        return {"status": "success", "message": "DOC processed successfully"}

    async def process_docx_document(self, recordName, recordId, version, source, orgId, docx_binary):
        """Process DOCX document and extract structured content

        Args:
            recordName (str): Name of the record
            recordId (str): ID of the record
            version (str): Version of the record
            source (str): Source of the document
            orgId (str): Organization ID
            docx_binary (bytes): Binary content of the DOCX file
        """
        logger.info(f"🚀 Starting DOCX document processing for record: {recordName}")

        try:
            # Convert binary to string if necessary
            # Initialize DocxParser and parse content
            logger.debug("📄 Processing DOCX content")
            parser = self.parsers['docx']
            docx_result = parser.parse(docx_binary)
            
            # Get the full document structure
            doc_dict = docx_result.export_to_dict()
            logger.debug(f"📑 Document structure processed, {doc_dict}")

            # Process content in reading order
            logger.debug("📑 Processing document structure in reading order")
            ordered_content = self._process_content_in_order(doc_dict)
            logger.debug(f"📑 Ordered content processed, {ordered_content}")
            
            # Extract text in reading order
            text_content = "\n".join(
                item['text'].strip()
                for item in ordered_content
                if item['text'].strip()
            )

            # Extract domain metadata
            logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    logger.info("🎯 Extracting metadata from DOCX content")
                    metadata = await self.domain_extractor.extract_metadata(text_content)
                    logger.info(f"✅ Extracted metadata: {metadata}")
                    record = await self.domain_extractor.save_metadata_to_arango(recordId, metadata)
                    file = await self.arango_service.get_document(recordId, CollectionNames.FILES.value)
                    domain_metadata = {**record, **file}
                except Exception as e:
                    logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create sentence data for indexing
            logger.debug("📑 Creating semantic sentences")
            sentence_data = []
            
            # Keep track of previous items for context
            context_window = []
            context_window_size = 3  # Number of previous items to include for context

            for idx, item in enumerate(ordered_content, 1):
                if item['text'].strip():
                    context = item['context']
                    
                    # Create context text from previous items
                    previous_context = " ".join([prev['text'].strip() for prev in context_window])
                    
                    # Current item's context with previous items
                    full_context = {
                        "previous": previous_context,
                        "current": item['text'].strip()
                    }

                    sentence_data.append({
                        'text': item['text'].strip(),
                        'bounding_box': None,
                        'metadata': {
                            **(domain_metadata or {}),
                            "recordId": recordId,
                            "blockType": context.get('label', 'text'),
                            "blockNum": idx,
                            "blockText": json.dumps(full_context),  # Include full context
                            "slideNumber": context.get('slide_number'),
                            "level": context.get('level')
                        }
                    })

                    # Update context window
                    context_window.append(item)
                    if len(context_window) > context_window_size:
                        context_window.pop(0)

            # Index sentences if available
            if sentence_data:
                logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "schema_name": doc_dict.get('schema_name'),
                    "version": doc_dict.get('version'),
                    "name": doc_dict.get('name'),
                    "origin": doc_dict.get('origin')
                },
                "structure_info": {
                    "text_count": len(doc_dict.get('texts', [])),
                    "group_count": len(doc_dict.get('groups', [])),
                    "list_count": len([item for item in ordered_content if item.get('context', {}).get('list_info')]),
                    "heading_count": len([item for item in ordered_content if item.get('context', {}).get('label') == 'heading'])
                }
            }

            logger.info("✅ DOCX processing completed successfully")
            return {
                "docx_result": {
                    "document_structure": {
                        "body": doc_dict.get('body'),
                        "groups": doc_dict.get('groups', [])
                    },
                    "metadata": domain_metadata
                },
                "formatted_content": text_content,
                "numbered_items": ordered_content,
                "metadata": metadata
            }

        except Exception as e:
            logger.error(f"❌ Error processing DOCX document: {str(e)}")
            raise

    async def process_excel_document(self, recordName, recordId, version, source, orgId, excel_binary):
        """Process Excel document and extract structured content"""
        logger.info(
            f"🚀 Starting Excel document processing for record: {recordName}")

        try:
            logger.debug("📊 Processing Excel content")
            llm = await get_llm(self.config_service)
            parser = self.parsers['excel']
            excel_result = parser.parse(excel_binary)
            logger.debug(f"📑 Excel result processed, {excel_result}")

            # Extract domain metadata from text content
            logger.info("🎯 Extracting domain metadata")
            if excel_result['text_content']:
                try:
                    logger.info(f"🎯 Extracting metadata from Excel content")
                    metadata = await self.domain_extractor.extract_metadata(excel_result['text_content'])
                    logger.info(f"✅ Extracted metadata: {metadata}")
                    record = await self.domain_extractor.save_metadata_to_arango(recordId, metadata)
                    file = await self.arango_service.get_document(recordId, CollectionNames.FILES.value)
                    # Convert datetime objects to strings
                    domain_metadata = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in {**record, **file}.items()}
                except Exception as e:
                    logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Format content for output
            formatted_content = ""
            numbered_items = []
            sentence_data = []

            # Process each sheet using process_sheet_with_summaries
            logger.debug("📝 Processing sheets")
            for sheet_idx, sheet_name in enumerate(excel_result['sheet_names'], 1):
                sheet_data = await parser.process_sheet_with_summaries(llm, sheet_name)
                # Add sheet entry
                sheet_entry = {
                    "number": f"S{sheet_idx}",
                    "name": sheet_data['sheet_name'],
                    "type": "sheet",
                    "row_count": len(sheet_data['tables']),
                    "column_count": max((len(table['headers']) for table in sheet_data['tables']), default=0)
                }
                numbered_items.append(sheet_entry)

                # Format content and sentence data
                formatted_content += f"\n[Sheet]: {sheet_data['sheet_name']}\n"

                for table in sheet_data['tables']:
                    formatted_content += f"\nTable Summary: {table['summary']}\n"
                    for row in table['rows']:
                        # Convert datetime objects in row_data to strings
                        row_data = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row['raw_data'].items()}
                        formatted_content += f"Row Data: {row_data}\n"
                        formatted_content += f"Natural Text: {row['natural_language_text']}\n"

                        # Add processed rows to sentence data
                        sentence_data.append({
                            'text': row['natural_language_text'],
                            'bounding_box': None,
                            'metadata': {
                                **({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in domain_metadata.items()}),
                                "recordId": recordId,
                                "sheetName": sheet_data['sheet_name'],
                                "sheetNum": sheet_idx,
                                "blockNum": row['row_num'],  # Use actual row number
                                "blockType": "table_row",
                                "blockText": json.dumps(row_data)  # Include entire row data
                            }
                        })
            # Index sentences if available
            if sentence_data:
                logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)
            # Prepare metadata
            logger.debug("📋 Preparing metadata")
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in excel_result.get("metadata", {}).items()},
                "sheet_count": len(excel_result['sheets']),
                "total_rows": excel_result['total_rows'],
                "total_cells": excel_result['total_cells']
            }
            logger.info("✅ Excel processing completed successfully")
            return {
                "excel_result": excel_result,
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata
            }

        except Exception as e:
            logger.error(f"❌ Error processing Excel document: {str(e)}")
            raise
        
    async def process_xls_document(self, recordName, recordId, version, source, orgId, xls_binary):
        """Process XLS document and extract structured content"""
        logger.info(f"🚀 Starting XLS document processing for record: {recordName}")
        
        try:
            # Convert XLS to XLSX binary
            xls_parser = self.parsers['xls']
            xlsx_binary = xls_parser.convert_xls_to_xlsx(xls_binary)
            
            # Process the converted XLSX using the Excel parser
            result = await self.process_excel_document(recordName, recordId, version, source, orgId, xlsx_binary)
            logger.debug(f"📑 XLS document processed successfully")
            return result
            
        except Exception as e:
            logger.error(f"❌ Error processing XLS document: {str(e)}")
            raise

    async def process_csv_document(self, recordName, recordId, version, source, orgId, csv_binary):
        """Process CSV document and extract structured content

        Args:
            recordName (str): Name of the record
            recordId (str): ID of the record
            version (str): Version of the record
            source (str): Source of the document
            orgId (str): Organization ID
            csv_binary (bytes): Binary content of the CSV file
        """
        logger.info(f"🚀 Starting CSV document processing for record: {recordName}")

        try:
            # Initialize CSV parser
            logger.debug("📊 Processing CSV content")
            parser = self.parsers['csv']
            
            llm = await get_llm(self.config_service)

            # Save temporary file to process CSV
            temp_file_path = f"/tmp/{recordName}_temp.csv"
            try:
                with open(temp_file_path, "wb") as f:
                    f.write(csv_binary)

                # Try different encodings
                encodings = ['utf-8', 'latin1', 'cp1252', 'iso-8859-1']
                csv_result = None
                
                for encoding in encodings:
                    try:
                        logger.debug(f"Attempting to read CSV with {encoding} encoding")
                        csv_result = parser.read_file(temp_file_path, encoding=encoding)
                        logger.debug(f"Successfully read CSV with {encoding} encoding")
                        break
                    except UnicodeDecodeError:
                        continue

                if csv_result is None:
                    raise ValueError("Unable to decode CSV file with any supported encoding")

                logger.debug(f"📑 CSV result processed, {csv_result}")

                # Extract domain metadata from CSV content
                logger.info("🎯 Extracting domain metadata")
                if csv_result:
                    # Convert CSV data to text for metadata extraction
                    csv_text = "\n".join(
                        [" ".join(str(value) for value in row.values())
                         for row in csv_result]
                    )

                    try:
                        logger.info("🎯 Extracting metadata from CSV content")
                        metadata = await self.domain_extractor.extract_metadata(csv_text)
                        logger.info(f"✅ Extracted metadata: {metadata}")
                        record = await self.domain_extractor.save_metadata_to_arango(recordId, metadata)
                        file = await self.arango_service.get_document(recordId, CollectionNames.FILES.value)
                        domain_metadata = {**record, **file}
                    except Exception as e:
                        logger.error(f"❌ Error extracting metadata: {str(e)}")
                        domain_metadata = None

            finally:
                # Clean up temporary file
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)

            # Format content for output
            formatted_content = ""
            numbered_rows = []
            sentence_data = []

            # Process rows for formatting
            logger.debug("📝 Processing rows")
            batch_size = 10  # Define a suitable batch size
            for i in range(0, len(csv_result), batch_size):
                batch = csv_result[i:i + batch_size]
                row_texts = await parser.get_rows_text(llm, batch)

                for idx, (row, row_text) in enumerate(zip(batch, row_texts), start=i+1):
                    row_entry = {
                        "number": idx,
                        "content": row,
                        "type": "row"
                    }
                    numbered_rows.append(row_entry)
                    formatted_content += f"[{idx}] {json.dumps(row)}\n"
                    formatted_content += f"Natural Text: {row_text}\n"

                    # Add sentence data for indexing
                    sentence_data.append({
                        'text': row_text,
                        'bounding_box': None,
                        'metadata': {
                            **(domain_metadata),
                            "recordId": recordId,
                            "blockType": "table_row",
                            "blockText": json.dumps(row),
                            "blockNum": idx  
                        }
                    })

            # Index sentences if available
            if sentence_data:
                logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            logger.debug("📋 Preparing metadata")
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "row_count": len(csv_result),
                "column_count": len(csv_result[0]) if csv_result else 0,
                "columns": list(csv_result[0].keys()) if csv_result else []
            }

            logger.info("✅ CSV processing completed successfully")
            return {
                "csv_result": csv_result,
                "formatted_content": formatted_content,
                "numbered_rows": numbered_rows,
                "metadata": metadata
            }

        except Exception as e:
            logger.error(f"❌ Error processing CSV document: {str(e)}")
            raise
        
    def _process_content_in_order(self, doc_dict):
        """
        Process document content in proper reading order by following references.
        
        Args:
            doc_dict (dict): The document dictionary from Docling
            
        Returns:
            list: Ordered list of text items with their context
        """
        ordered_items = []
        processed_refs = set()
        
        def process_item(ref, level=0, parent_context=None):
            """Recursively process items following references"""
            if isinstance(ref, dict):
                ref_path = ref.get('$ref', '')
            else:
                ref_path = ref
                
            if not ref_path or ref_path in processed_refs:
                return
            processed_refs.add(ref_path)
                
            if not ref_path.startswith('#/'):
                return
                
            path_parts = ref_path[2:].split('/')
            item_type = path_parts[0]  # 'texts', 'groups', etc.
            try:
                item_index = int(path_parts[1])
            except (IndexError, ValueError):
                return
                
            items = doc_dict.get(item_type, [])
            if item_index >= len(items):
                return
            item = items[item_index]
            logger.debug(f"Processing item: {item_type}[{item_index}] = {item}")
            
            # Create context for current item
            current_context = {
                'ref': item.get('self_ref'),
                'label': item.get('label'),
                'level': item.get('level'),
                'parent_context': parent_context,
                'slide_number': item.get('slide_number')
            }
            
            if item_type == 'texts':
                ordered_items.append({
                    'text': item.get('text', ''),
                    'context': current_context
                })
            
            # Process children with current_context as parent
            children = item.get('children', [])
            logger.debug(f"Processing children of {item_type}[{item_index}]: {children}")
            for child in children:
                process_item(child, level + 1, current_context)
        
        # Start processing from body
        body = doc_dict.get('body', {})
        logger.debug(f"Starting from body: {body}")
        for child in body.get('children', []):
            process_item(child)
            
        logger.debug(f"Processed {len(ordered_items)} items in order")
        return ordered_items

    async def process_html_document(self, recordName, recordId, version, source, orgId, html_content):
        """Process HTML document and extract structured content"""
        logger.info(f"🚀 Starting HTML document processing for record: {recordName}")

        try:
            # Convert binary to string
            html_content = html_content.decode('utf-8') if isinstance(html_content, bytes) else html_content
            logger.debug(f"📄 Decoded HTML content length: {len(html_content)}")

            # Initialize HTML parser and parse content
            logger.debug("📄 Processing HTML content")
            parser = self.parsers['html']
            html_result = parser.parse_string(html_content)
            
            # Get the full document structure
            doc_dict = html_result.export_to_dict()
            logger.debug("📑 Document structure processed")

            # Process content in reading order
            logger.debug("📑 Processing document structure in reading order")
            ordered_content = self._process_content_in_order(doc_dict)
            
            # Extract text in reading order
            text_content = "\n".join(
                item['text'].strip()
                for item in ordered_content
                if item['text'].strip()
            )

            # Extract domain metadata
            logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    logger.info("🎯 Extracting metadata from HTML content")
                    metadata = await self.domain_extractor.extract_metadata(text_content)
                    logger.info(f"✅ Extracted metadata: {metadata}")
                    record = await self.domain_extractor.save_metadata_to_arango(recordId, metadata)
                    file = await self.arango_service.get_document(recordId, CollectionNames.FILES.value)
                    domain_metadata = {**record, **file}

                except Exception as e:
                    logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create sentence data for indexing
            logger.debug("📑 Creating semantic sentences")
            sentence_data = []
            
            # Keep track of previous items for context
            context_window = []
            context_window_size = 3  # Number of previous items to include for context

            for idx, item in enumerate(ordered_content, 1):
                if item['text'].strip():
                    context = item['context']
                    
                    # Create context text from previous items
                    previous_context = " ".join([prev['text'].strip() for prev in context_window])
                    
                    # Current item's context with previous items
                    full_context = {
                        "previous": previous_context,
                        "current": item['text'].strip()
                    }

                    sentence_data.append({
                        'text': item['text'].strip(),
                        'bounding_box': None,
                        'metadata': {
                            **(domain_metadata or {}),
                            "recordId": recordId,
                            "blockType": context.get('label', 'text'),
                            "blockNum": idx,
                            "blockText": json.dumps(full_context),  # Include full context
                            "slideNumber": context.get('slide_number'),
                            "level": context.get('level')
                        }
                    })

                    # Update context window
                    context_window.append(item)
                    if len(context_window) > context_window_size:
                        context_window.pop(0)

            # Index sentences if available
            if sentence_data:
                logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "schema_name": doc_dict.get('schema_name'),
                    "version": doc_dict.get('version'),
                    "name": doc_dict.get('name'),
                    "origin": doc_dict.get('origin')
                },
                "structure_info": {
                    "text_count": len(doc_dict.get('texts', [])),
                    "group_count": len(doc_dict.get('groups', [])),
                    "list_count": len([item for item in ordered_content if item.get('context', {}).get('list_info')]),
                    "heading_count": len([item for item in ordered_content if item.get('context', {}).get('label') == 'heading'])
                }
            }

            logger.info("✅ HTML processing completed successfully")
            return {
                "html_result": {
                    "document_structure": {
                        "body": doc_dict.get('body'),
                        "groups": doc_dict.get('groups', [])
                    },
                    "metadata": domain_metadata
                },
                "formatted_content": text_content,
                "numbered_items": ordered_content,
                "metadata": metadata
            }

        except Exception as e:
            logger.error(f"❌ Error processing HTML document: {str(e)}")
            raise

    async def process_md_document(self, recordName, recordId, version, source, orgId, md_binary):
        logger.info(f"🚀 Starting Markdown document processing for record: {recordName}")

        try:
            # Convert binary to string
            md_content = md_binary.decode('utf-8')

            # Initialize Markdown parser
            logger.debug("📄 Processing Markdown content")
            parser = self.parsers['md']
            md_result = parser.parse_string(md_content)
            
            # Get the full document structure
            doc_dict = md_result.export_to_dict()

            # Extract text content from all text elements
            text_content = "\n".join(
                text_item.get('text', '').strip()
                for text_item in doc_dict.get('texts', [])
                if text_item.get('text', '').strip()
            )

            # Extract domain metadata from content
            logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    metadata = await self.domain_extractor.extract_metadata(text_content)
                    logger.info(f"✅ Extracted metadata: {metadata}")
                    record = await self.domain_extractor.save_metadata_to_arango(recordId, metadata)
                    file = await self.arango_service.get_document(recordId, CollectionNames.FILES.value)
                    domain_metadata = {**record, **file}
                except Exception as e:
                    logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Format content for output
            formatted_content = ""
            numbered_items = []

            # Process text items for numbering and formatting
            logger.debug("📝 Processing text items")
            for idx, item in enumerate(doc_dict.get('texts', []), 1):
                if item.get('text', '').strip():
                    # Create item entry with metadata
                    item_entry = {
                        "number": idx,
                        "content": item['text'].strip(),
                        "type": item.get('label', 'text'),
                        "level": item.get('level'),
                        "parent_ref": item.get('parent', {}).get('$ref'),
                        "children_refs": [child.get('$ref') for child in item.get('children', [])],
                        "code_language": item.get('language') if item.get('label') == 'code' else None
                    }
                    numbered_items.append(item_entry)
                    formatted_content += f"[{idx}] {item['text'].strip()}\n\n"

            # Create sentence data for indexing
            logger.debug("📑 Creating semantic sentences")
            sentence_data = []
            
            # Keep track of previous items for context
            context_window = []
            context_window_size = 3  # Number of previous items to include for context

            for idx, item in enumerate(doc_dict.get('texts', []), 1):
                if item.get('text') and item.get('label') != 'code':  # Skip code blocks
                    # Create context text from previous items
                    previous_context = " ".join([prev.get('text', '').strip() for prev in context_window])
                    
                    # Current item's context with previous items
                    full_context = {
                        "previous": previous_context,
                        "current": item['text'].strip()
                    }

                    sentence_data.append({
                        'text': item['text'].strip(),
                        'bounding_box': None,
                        'metadata': {
                            **(domain_metadata or {}),
                            "recordId": recordId,
                            "blockType": item.get('label', 'text'),
                            "blockNum": idx,
                            "blockText": json.dumps(full_context),  # Include full context
                            "level": item.get('level'),
                            "codeLanguage": item.get('language') if item.get('label') == 'code' else None
                        }
                    })

                    # Update context window
                    context_window.append(item)
                    if len(context_window) > context_window_size:
                        context_window.pop(0)

            # Index sentences if available
            if sentence_data:
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            logger.debug("📋 Preparing metadata")
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "schema_name": doc_dict.get('schema_name'),
                    "version": doc_dict.get('version'),
                    "name": doc_dict.get('name'),
                    "origin": doc_dict.get('origin')
                },
                "structure_info": {
                    "text_count": len(doc_dict.get('texts', [])),
                    "group_count": len(doc_dict.get('groups', [])),
                    "table_count": len(doc_dict.get('tables', [])),
                    "code_block_count": len([item for item in doc_dict.get('texts', []) 
                                          if item.get('label') == 'code']),
                    "heading_count": len([item for item in doc_dict.get('texts', []) 
                                       if item.get('label') == 'heading'])
                }
            }

            logger.info("✅ Markdown processing completed successfully")
            return {
                "md_result": {
                    "items": numbered_items,
                    "document_structure": {
                        "body": doc_dict.get('body'),
                        "groups": doc_dict.get('groups')
                    },
                    "metadata": domain_metadata
                },
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata
            }

        except Exception as e:
            logger.error(f"❌ Error processing Markdown document: {str(e)}")
            raise

    async def process_pptx_document(self, recordName, recordId, version, source, orgId, pptx_binary):
        """Process PPTX document and extract structured content

        Args:
            recordName (str): Name of the record
            recordId (str): ID of the record
            version (str): Version of the record
            source (str): Source of the document
            orgId (str): Organization ID
            pptx_binary (bytes): Binary content of the PPTX file
        """
        logger.info(f"🚀 Starting PPTX document processing for record: {recordName}")

        try:
            # Initialize PPTX parser
            logger.debug("📄 Processing PPTX content")
            parser = self.parsers['pptx']
            pptx_result = parser.parse_binary(pptx_binary)
            pptx_result["metadata"] = None

            # Get the full document structure
            doc_dict = pptx_result.export_to_dict()
            # logger.debug(f"📑 Full document structure: {doc_dict}")

            # Log structure counts
            logger.debug(f"📊 Document structure counts:")
            logger.debug(f"- Texts: {len(doc_dict.get('texts', []))}")
            logger.debug(f"- Groups: {len(doc_dict.get('groups', []))}")
            logger.debug(f"- Pictures: {len(doc_dict.get('pictures', []))}")

            # Process content in reading order
            ordered_items = []
            processed_refs = set()
            
            def process_item(ref, level=0, parent_context=None):
                if isinstance(ref, dict):
                    ref_path = ref.get('$ref', '')
                else:
                    ref_path = ref
                    
                if not ref_path or ref_path in processed_refs:
                    return
                processed_refs.add(ref_path)
                
                if not ref_path.startswith('#/'):
                    return
                    
                path_parts = ref_path[2:].split('/')
                item_type = path_parts[0]
                try:
                    item_index = int(path_parts[1])
                except (IndexError, ValueError):
                    return

                items = doc_dict.get(item_type, [])
                if item_index >= len(items):
                    return
                item = items[item_index]
                
                # Create context for current item
                current_context = {
                    'ref': item.get('self_ref'),
                    'label': item.get('label'),
                    'level': item.get('level'),
                    'parent_context': parent_context,
                    'slide_number': item.get('slide_number')
                }
                
                if item_type == 'texts':
                    ordered_items.append({
                        'text': item.get('text', ''),
                        'context': current_context
                    })
                
                children = item.get('children', [])
                for child in children:
                    process_item(child, level + 1, current_context)

            # Start processing from body
            body = doc_dict.get('body', {})
            for child in body.get('children', []):
                process_item(child)

            # Extract text content from ordered items
            text_content = "\n".join(
                item['text'].strip()
                for item in ordered_items
                if item['text'].strip()
            )
            logger.debug(f"📝 Extracted text content: {text_content}")

            # Extract domain metadata
            logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    metadata = await self.domain_extractor.extract_metadata(text_content)
                    logger.info(f"✅ Extracted metadata: {metadata}")
                    record = await self.domain_extractor.save_metadata_to_arango(recordId, metadata)
                    file = await self.arango_service.get_document(recordId, CollectionNames.FILES.value)
                    domain_metadata = {**record, **file}
                     
                except Exception as e:
                    logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None
                    

            # Create numbered items with slide information
            numbered_items = []
            formatted_content = ""
            
            for idx, item in enumerate(ordered_items, 1):
                if item['text'].strip():
                    context = item['context']
                    item_entry = {
                        "number": idx,
                        "content": item['text'].strip(),
                        "type": context.get('label', 'text'),
                        "level": context.get('level'),
                        "ref": context.get('ref'),
                        "parent_ref": context.get('parent_context', {}).get('ref'),
                        "slide_number": context.get('slide_number')
                    }
                    numbered_items.append(item_entry)
                    
                    # Format with slide numbers
                    slide_info = f"[Slide {context.get('slide_number', '?')}] " if context.get('slide_number') else ""
                    formatted_content += f"{slide_info}[{idx}] {item['text'].strip()}\n"

            # Create sentence data for indexing
            logger.debug("📑 Creating semantic sentences")
            sentence_data = []
            
            # Keep track of previous items for context
            context_window = []
            context_window_size = 3  # Number of previous items to include for context

            for idx, item in enumerate(ordered_items, 1):
                if item['text'].strip():
                    context = item['context']
                    
                    # Create context text from previous items
                    previous_context = " ".join([prev['text'].strip() for prev in context_window])
                    
                    # Current item's context with previous items
                    full_context = {
                        "previous": previous_context,
                        "current": item['text'].strip()
                    }

                    sentence_data.append({
                        'text': item['text'].strip(),
                        'bounding_box': None,
                        'metadata': {
                            **(domain_metadata or {}),
                            "recordId": recordId,
                            "blockType": context.get('label', 'text'),
                            "blockNum": idx,
                            "blockText": json.dumps(full_context),  # Include full context
                            "slideNumber": context.get('slide_number'),
                            "level": context.get('level')
                        }
                    })

                    # Update context window
                    context_window.append(item)
                    if len(context_window) > context_window_size:
                        context_window.pop(0)

            # Index sentences if available
            if sentence_data:
                logger.debug("📑 Indexing %s sentences", len(sentence_data))
                logger.debug("sentence_data: %s", sentence_data)
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "schema_name": doc_dict.get('schema_name'),
                    "version": doc_dict.get('version'),
                    "name": doc_dict.get('name'),
                    "origin": doc_dict.get('origin')
                },
                "structure_info": {
                    "text_count": len(doc_dict.get('texts', [])),
                    "group_count": len(doc_dict.get('groups', [])),
                    "picture_count": len(doc_dict.get('pictures', [])),
                    "slide_count": len(set(item['context'].get('slide_number') 
                                        for item in ordered_items 
                                        if item['context'].get('slide_number')))
                }
            }

            logger.info("✅ PPTX processing completed successfully")
            return {
                "pptx_result": {
                    "items": numbered_items,
                    "document_structure": {
                        "body": doc_dict.get('body'),
                        "groups": doc_dict.get('groups')
                    },
                    "metadata": domain_metadata
                },
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata
            }

        except Exception as e:
            logger.error(f"❌ Error processing PPTX document: {str(e)}")
            raise
