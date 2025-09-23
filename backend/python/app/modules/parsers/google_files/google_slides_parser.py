"""Google Slides Parser module for parsing Google Slides content"""

from typing import Any, Dict, Optional

from app.connectors.sources.google.admin.google_admin_service import GoogleAdminService
from app.connectors.utils.decorators import exponential_backoff
from app.modules.parsers.google_files.parser_user_service import ParserUserService


class GoogleSlidesParser:
    """Parser class for Google Slides content"""

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
                self.logger.error("❌ Failed to connect to Google Slides service")
                return None

            self.service = self.user_service.slides_service
            self.logger.info("🚀 Connected to Google Slides service: %s", self.service)
        elif self.admin_service:
            user_service = await self.admin_service.create_parser_user_service(
                user_email
            )
            self.service = user_service.slides_service
            self.logger.info("🚀 Connected to Google Slides service: %s", self.service)

    async def _process_slide(self, slide: Dict) -> Dict[str, Any]:
        """Process individual slide content"""
        try:
            slide_data = {
                "slideId": slide.get("objectId", ""),
                "layout": slide.get("slideProperties", {}).get("layoutObjectId", ""),
                "masterObjectId": slide.get("slideProperties", {}).get(
                    "masterObjectId", ""
                ),
                "notesPageId": slide.get("slideProperties", {})
                .get("notesPage", {})
                .get("notesProperties", {})
                .get("speakerNotesObjectId", ""),
                "slideProperties": {
                    "displayName": slide.get("slideProperties", {}).get(
                        "displayName", ""
                    ),
                    "layoutProperties": slide.get("slideProperties", {}).get(
                        "layoutProperties", {}
                    ),
                    "masterProperties": slide.get("slideProperties", {}).get(
                        "masterProperties", {}
                    ),
                },
                "elements": [],
            }

            for element in slide.get("pageElements", []):
                element_data = await self._process_element(element)
                if element_data:
                    slide_data["elements"].append(element_data)

            return slide_data

        except Exception as e:
            self.logger.error("❌ Failed to process slide: %s", str(e))
            return None

    async def _process_element(self, element: Dict) -> Dict[str, Any]:
        """Process individual page element"""
        try:
            element_data = {
                "id": element.get("objectId", ""),
                "type": self._get_element_type(element),
                "transform": element.get("transform", {}),
                "size": element.get("size", {}),
            }

            # Process based on element type
            if "shape" in element:
                element_data.update(await self._process_shape(element))
            elif "table" in element:
                element_data.update(await self._process_table(element))
            elif "image" in element:
                element_data.update(await self._process_image(element))
            elif "video" in element:
                element_data.update(await self._process_video(element))
            elif "line" in element:
                element_data.update(await self._process_line(element))

            return element_data

        except Exception as e:
            self.logger.error("❌ Failed to process element: %s", str(e))
            return None

    def _get_element_type(self, element: Dict) -> str:
        """Determine element type"""
        if "shape" in element:
            return "shape"
        elif "table" in element:
            return "table"
        elif "image" in element:
            return "image"
        elif "video" in element:
            return "video"
        elif "line" in element:
            return "line"
        return "unknown"

    async def _process_shape(self, element: Dict) -> Dict[str, Any]:
        """Process shape element"""
        shape = element.get("shape", {})
        shape_data = {
            "shapeType": shape.get("shapeType", ""),
            "text": await self._extract_text_content(shape.get("text", {})),
            "placeholder": shape.get("placeholder", {}),
            "style": {
                "shapeBackgroundFill": shape.get("shapeProperties", {}).get(
                    "shapeBackgroundFill", {}
                ),
                "outline": shape.get("shapeProperties", {}).get("outline", {}),
            },
        }
        return shape_data

    async def _process_table(self, element: Dict) -> Dict[str, Any]:
        """Process table element"""
        table = element.get("table", {})
        table_data = {
            "rows": table.get("rows", 0),
            "columns": table.get("columns", 0),
            "cells": [],
        }

        for row_idx, row in enumerate(table.get("tableRows", [])):
            for col_idx, cell in enumerate(row.get("tableCells", [])):
                cell_data = {
                    "rowIndex": row_idx,
                    "columnIndex": col_idx,
                    "text": await self._extract_text_content(cell.get("text", {})),
                    "style": cell.get("tableCellProperties", {}),
                }
                table_data["cells"].append(cell_data)

        return table_data

    async def _process_image(self, element: Dict) -> Dict[str, Any]:
        """Process image element"""
        image = element.get("image", {})
        return {
            "sourceUrl": image.get("sourceUrl", ""),
            "contentUrl": image.get("contentUrl", ""),
            "imageProperties": image.get("imageProperties", {}),
        }

    async def _process_video(self, element: Dict) -> Dict[str, Any]:
        """Process video element"""
        video = element.get("video", {})
        return {
            "id": video.get("id", ""),
            "source": video.get("source", ""),
            "url": video.get("url", ""),
            "videoProperties": video.get("videoProperties", {}),
        }

    async def _process_line(self, element: Dict) -> Dict[str, Any]:
        """Process line element"""
        line = element.get("line", {})
        return {
            "lineType": line.get("lineType", ""),
            "lineProperties": line.get("lineProperties", {}),
        }

    async def _extract_text_content(self, text_element: Dict) -> Dict[str, Any]:
        """Extract text content and styling"""
        if not text_element:
            return {"content": "", "style": {}}

        text_content = {"content": "", "style": {}, "lists": [], "links": []}

        for element in text_element.get("textElements", []):
            if "textRun" in element:
                text_run = element["textRun"]
                text_content["content"] += text_run.get("content", "")

                # Capture text style
                if "style" in text_run:
                    text_content["style"] = {
                        "bold": text_run["style"].get("bold", False),
                        "italic": text_run["style"].get("italic", False),
                        "underline": text_run["style"].get("underline", False),
                        "fontSize": text_run["style"].get("fontSize", {}),
                        "foregroundColor": text_run["style"].get("foregroundColor", {}),
                        "backgroundColor": text_run["style"].get("backgroundColor", {}),
                    }

                # Capture links
                if "link" in text_run:
                    text_content["links"].append(
                        {
                            "url": text_run["link"].get("url", ""),
                            "text": text_run.get("content", ""),
                        }
                    )

            elif "paragraphMarker" in element:
                # Capture list information
                if "bullet" in element["paragraphMarker"]:
                    text_content["lists"].append(
                        {
                            "glyph": element["paragraphMarker"]["bullet"].get(
                                "glyph", ""
                            ),
                            "listId": element["paragraphMarker"]["bullet"].get(
                                "listId", ""
                            ),
                            "nestingLevel": element["paragraphMarker"]["bullet"].get(
                                "nestingLevel", 0
                            ),
                        }
                    )

        return text_content

    @exponential_backoff()
    async def process_presentation(self, presentation_id: str) -> Dict[str, Any]:
        """Process an entire presentation including all slides"""
        try:
            if not self.service:
                self.logger.error("❌ No valid service available for parsing")
                return None

            # Get presentation data
            presentation = (
                self.service.presentations()
                .get(presentationId=presentation_id)
                .execute()
            )

            # Extract presentation metadata
            metadata = {
                "presentationId": presentation.get("presentationId", ""),
                "title": presentation.get("title", ""),
                "locale": presentation.get("locale", ""),
                "revisionId": presentation.get("revisionId", ""),
                "pageSize": presentation.get("pageSize", {}),
                "masterCount": len(presentation.get("masters", [])),
                "layoutCount": len(presentation.get("layouts", [])),
                "hasNotesMaster": bool(presentation.get("notesMaster")),
            }

            processed_slides = []
            for index, slide in enumerate(presentation.get("slides", []), 1):
                slide_data = await self._process_slide(slide)
                if slide_data:
                    # Add slide number and additional metadata
                    slide_data.update(
                        {
                            "slideNumber": index,
                            "totalSlides": len(presentation.get("slides", [])),
                            "hasNotesPage": bool(
                                slide.get("slideProperties", {}).get("notesPage")
                            ),
                            "masterProperties": slide.get("slideProperties", {}).get(
                                "masterProperties", {}
                            ),
                            "layoutProperties": slide.get("slideProperties", {}).get(
                                "layoutProperties", {}
                            ),
                        }
                    )
                    processed_slides.append(slide_data)

            return {
                "metadata": metadata,
                "slides": processed_slides,
                "summary": {
                    "totalSlides": len(processed_slides),
                    "hasNotes": any(
                        slide.get("hasNotesPage") for slide in processed_slides
                    ),
                    "presentationTitle": metadata["title"],
                    "locale": metadata["locale"],
                },
            }

        except Exception as e:
            self.logger.error("❌ Failed to process presentation: %s", str(e))
            return None
