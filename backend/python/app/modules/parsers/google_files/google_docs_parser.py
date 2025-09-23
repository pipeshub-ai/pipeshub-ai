"""Google Docs Parser module for parsing Google Docs content"""

from typing import Dict, List, Optional, Tuple

from app.connectors.sources.google.admin.google_admin_service import GoogleAdminService
from app.connectors.utils.decorators import exponential_backoff
from app.modules.parsers.google_files.parser_user_service import ParserUserService


class GoogleDocsParser:
    """Parser class for Google Docs content"""

    def __init__(
        self,
        logger,
        admin_service: Optional[GoogleAdminService] = None,
        user_service: Optional[ParserUserService] = None,
    ) -> None:
        """Initialize with either admin or user service"""
        self.logger = logger
        self.admin_service = admin_service
        self.user_service = user_service
        self.service = None

    async def connect_service(
        self, user_email: str = None, org_id: str = None, user_id: str = None, app_name: str = "drive"
    ) -> None:
        if self.user_service:
            if not await self.user_service.connect_individual_user(org_id, user_id,app_name=app_name):
                self.logger.error("❌ Failed to connect to Google Docs service")
                return None

            self.service = self.user_service.docs_service
            self.logger.info("🚀 Connected to Google Docs service: %s", self.service)
        elif self.admin_service:
            user_service = await self.admin_service.create_parser_user_service(
                user_email
            )
            self.service = user_service.docs_service
            self.logger.info("🚀 Connected to Google Docs service: %s", self.service)

    @exponential_backoff()
    async def parse_doc_content(self, doc_id: str) -> Optional[Dict]:
        """Parse detailed content from a Google Doc including structural elements"""
        try:
            if not self.service:
                self.logger.error("❌ No valid service available for parsing")
                return None
            # Fetch the document
            document = self.service.documents().get(documentId=doc_id).execute()

            # Initialize content structure
            content = {
                "title": document.get("title", ""),
                "elements": [],
                "tables": [],
                "images": [],
                "headers": [],
                "footers": [],
            }

            # Process document body
            for element in document.get("body", {}).get("content", []):
                # Process paragraphs
                if "paragraph" in element:
                    para_content = {
                        "type": "paragraph",
                        "start_index": element.get("startIndex", 0),
                        "end_index": element.get("endIndex", 0),
                        "text": "",
                        "style": element["paragraph"].get("paragraphStyle", {}),
                        "bullet": element["paragraph"].get("bullet", None),
                        "links": [],  # Add links array to store link information
                    }

                    # Extract text, formatting, and links from paragraph elements
                    for para_element in element["paragraph"].get("elements", []):
                        if "textRun" in para_element:
                            text_run = para_element["textRun"]
                            text_content = text_run.get("content", "")
                            para_content["text"] += text_content

                            # Include text styling information
                            para_content["textStyle"] = text_run.get("textStyle", {})

                            # Check for links in the text run
                            if text_run.get("textStyle", {}).get("link"):
                                para_content["links"].append(
                                    {
                                        "text": text_content,
                                        "url": text_run["textStyle"]["link"].get(
                                            "url", ""
                                        ),
                                        "start_index": para_element.get(
                                            "startIndex", 0
                                        ),
                                        "end_index": para_element.get("endIndex", 0),
                                    }
                                )

                    content["elements"].append(para_content)

                # Process tables
                elif "table" in element:
                    table_content = {
                        "type": "table",
                        "start_index": element.get("startIndex", 0),
                        "end_index": element.get("endIndex", 0),
                        "rows": len(element["table"].get("tableRows", [])),
                        "columns": len(
                            element["table"]
                            .get("tableRows", [{}])[0]
                            .get("tableCells", [])
                        ),
                        "cells": [],
                    }

                    # Process each cell in the table
                    for row_idx, row in enumerate(
                        element["table"].get("tableRows", [])
                    ):
                        for col_idx, cell in enumerate(row.get("tableCells", [])):
                            cell_content = {
                                "row": row_idx,  # Add row number
                                "column": col_idx,  # Add column number
                                "start_index": cell.get("startIndex", 0),
                                "end_index": cell.get("endIndex", 0),
                                "content": [],
                                "links": [],  # Add links array for table cells
                            }

                            # Extract content from cell
                            for cell_element in cell.get("content", []):
                                if "paragraph" in cell_element:
                                    text = ""
                                    for para_element in cell_element["paragraph"].get(
                                        "elements", []
                                    ):
                                        if "textRun" in para_element:
                                            text_run = para_element["textRun"]
                                            text_content = text_run.get("content", "")
                                            text += text_content

                                            # Check for links in the cell
                                            if text_run.get("textStyle", {}).get(
                                                "link"
                                            ):
                                                cell_content["links"].append(
                                                    {
                                                        "text": text_content,
                                                        "url": text_run["textStyle"][
                                                            "link"
                                                        ].get("url", ""),
                                                        "start_index": para_element.get(
                                                            "startIndex", 0
                                                        ),
                                                        "end_index": para_element.get(
                                                            "endIndex", 0
                                                        ),
                                                    }
                                                )
                                    cell_content["content"].append(text)

                            table_content["cells"].append(cell_content)

                    content["tables"].append(table_content)

                # Process inline images
                elif "inlineObjectElement" in element:
                    obj_id = element["inlineObjectElement"].get("inlineObjectId")
                    if obj_id and obj_id in document.get("inlineObjects", {}):
                        image_obj = document["inlineObjects"][obj_id]
                        image_content = {
                            "type": "image",
                            "start_index": element.get("startIndex", 0),
                            "end_index": element.get("endIndex", 0),
                            "source_uri": image_obj.get("inlineObjectProperties", {})
                            .get("embeddedObject", {})
                            .get("imageProperties", {})
                            .get("sourceUri", ""),
                            "size": image_obj.get("inlineObjectProperties", {})
                            .get("embeddedObject", {})
                            .get("size", {}),
                        }
                        content["images"].append(image_content)

            # Process headers
            for header_id, header in document.get("headers", {}).items():
                header_content = {"id": header_id, "content": []}
                for element in header.get("content", []):
                    if "paragraph" in element:
                        text = ""
                        for para_element in element["paragraph"].get("elements", []):
                            if "textRun" in para_element:
                                text += para_element["textRun"].get("content", "")
                        header_content["content"].append(text)
                content["headers"].append(header_content)

            # Process footers
            for footer_id, footer in document.get("footers", {}).items():
                footer_content = {"id": footer_id, "content": []}
                for element in footer.get("content", []):
                    if "paragraph" in element:
                        text = ""
                        for para_element in element["paragraph"].get("elements", []):
                            if "textRun" in para_element:
                                text += para_element["textRun"].get("content", "")
                        footer_content["content"].append(text)
                content["footers"].append(footer_content)

            self.logger.debug("✅ Google Docs content parsed successfully")
            return content

        except Exception as e:
            error_msg = str(e)
            if "SERVICE_DISABLED" in error_msg or "API has not been used" in error_msg:
                self.logger.error(f"❌ Google Docs API is not enabled. Please enable it in Google Cloud Console: {error_msg}")
            elif "PERMISSION_DENIED" in error_msg:
                self.logger.error(f"❌ Permission denied for Google Docs API: {error_msg}")
            else:
                self.logger.error(f"❌ Error parsing Google Docs content: {error_msg}")
            return None

    def order_document_content(self, content: Dict) -> Tuple[List, List, List]:
        """
        Orders document content chronologically by start and end indices.

        Args:
            content: Dictionary containing parsed document content

        Returns:
            Tuple containing:
            - List of ordered content elements
            - List of headers
            - List of footers
        """
        all_content = []

        # Add paragraphs
        for para in content["elements"]:
            all_content.append(
                {
                    "type": "paragraph",
                    "start_index": para["start_index"],
                    "end_index": para["end_index"],
                    "content": para,
                }
            )

        # Add tables
        for table in content["tables"]:
            all_content.append(
                {
                    "type": "table",
                    "start_index": table["start_index"],
                    "end_index": table["end_index"],
                    "content": table,
                }
            )

        # Add images
        for image in content["images"]:
            all_content.append(
                {
                    "type": "image",
                    "start_index": image["start_index"],
                    "end_index": image["end_index"],
                    "content": image,
                }
            )

        # Sort all content by start_index and end_index
        all_content.sort(key=lambda x: (x["start_index"], x["end_index"]))

        return all_content, content["headers"], content["footers"]
