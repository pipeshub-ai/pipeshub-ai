import asyncio
from uuid import uuid4
import aiohttp
import logging
from typing import List, Dict, Any, Optional, Tuple, AsyncGenerator
from datetime import datetime
from app.config.utils.named_constants.arangodb_constants import (
    CollectionNames,
    EventTypes,
    OriginTypes,
    RecordTypes,
)
from app.config.configuration_service import (
    config_node_constants,
    DefaultEndpoints
)

class NotionRouterService:
    """Service class for interacting with Notion API."""

    BASE_URL = "https://api.notion.com/v1"

    def __init__(self, integration_secret: str, org_id: str, workspace_id:str, logger=None, arango_service=None):
        """Initialize Notion service with integration secret."""
        self.integration_secret = integration_secret
        self.workspace_id = workspace_id
        self.org_id = org_id
        self.logger = logger or logging.getLogger(__name__)
        self.arango_service = arango_service
        self.headers = {
            "Authorization": f"Bearer {integration_secret}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    async def fetch_blocks_of_page(self, page_id):
        self.logger.info(f"fetching blocks for page {page_id}")
        processed_blocks = []
        
        try:
            blocks = await self._fetch_block_children(page_id)
            
            for block in blocks:
                block_id = block.get("id")
                block_type = block.get("type")
                block_created_time = block.get("created_time")
                block_last_edited_time = block.get("last_edited_time")
            
                if block_type in ["child_page", "child_database", "unsupported"]:
                    continue

                block_content = block.get(block_type, {})
                
                # Handle file blocks - return existing record info
                if block_type == "file":
                    self.logger.info(f"got a file record")
                    file_info = await self._get_file_info(block_content, block_type, block_id)
                    if file_info:
                        processed_blocks.append({
                            "block_id": block_id,
                            "block_type": block_type,
                            "record_id": file_info["record_id"],
                            "file_url": file_info["file_url"],
                            "name": file_info["name"],
                            "mimetype": file_info["mimetype"],
                            "created_time": block_created_time,
                            "last_edited_time": block_last_edited_time
                        })

                # Handle media blocks (video, image, gif) - return existing record info
                elif block_type in ["video", "image", "gif"]:
                    self.logger.info(f"got a {block_type} record - treating as file")
                    file_info = await self._get_file_info(block_content, block_type, block_id)
                    if file_info:
                        processed_blocks.append({
                            "block_id": block_id,
                            "block_type": block_type,
                            "record_id": file_info["record_id"],
                            "file_url": file_info["file_url"],
                            "name": file_info["name"],
                            "mimetype": file_info["mimetype"],
                            "created_time": block_created_time,
                            "last_edited_time": block_last_edited_time
                        })

                # Handle audio blocks - return existing record info
                elif block_type == "audio":
                    self.logger.info(f"got an audio record")
                    file_info = await self._get_file_info(block_content, block_type, block_id)
                    if file_info:
                        processed_blocks.append({
                            "block_id": block_id,
                            "block_type": block_type,
                            "record_id": file_info["record_id"],
                            "file_url": file_info["file_url"],
                            "name": file_info["name"],
                            "mimetype": file_info["mimetype"],
                            "created_time": block_created_time,
                            "last_edited_time": block_last_edited_time
                        })

                # Process other block types normally
                else:
                    processed_block = await self._process_block(block_id, block_type, block_content, 
                                                block_created_time, block_last_edited_time)
                    if processed_block:
                        processed_blocks.append(processed_block)
            
            self.logger.info(f"Processed {len(processed_blocks)} blocks for page {page_id}")
            return processed_blocks
            
        except Exception as e:
            self.logger.error(f"Error fetching block children for page {page_id}: {str(e)}")
            raise

    async def _get_file_info(self, block_content, block_type, block_id):
        """
        Extract file information and return record ID with file details.
        This assumes records already exist and we're just retrieving information.
        """
        try:
            # Generate the expected record key (same logic as before)
            record_key = f"notion_file_{block_id}"
            
            # Extract file information based on block type
            if block_type == "file":
                file_type = block_content.get("type")
                if file_type == "external":
                    file_url = block_content.get("external", {}).get("url", "")
                elif file_type == "file":
                    file_url = block_content.get("file", {}).get("url", "")
                else:
                    file_url = ""
                
                name = block_content.get("name", "Untitled")
                mimetype = self._extract_mimetype(block_content, block_type, name)
                
            elif block_type in ["video", "image", "gif"]:
                # Get the nested block content
                media_content = block_content.get(block_type, {})
                file_type = media_content.get("type")
                
                file_url = ""
                if file_type == "external":
                    file_url = media_content.get("external", {}).get("url", "")
                elif file_type == "file":
                    file_url = media_content.get("file", {}).get("url", "")
                elif file_type == "youtube" and block_type == "video":
                    file_url = media_content.get("youtube", {}).get("url", "")
                elif file_type == "vimeo" and block_type == "video":
                    file_url = media_content.get("vimeo", {}).get("url", "")
                
                # Extract name from media content or caption
                name = f"Untitled {block_type.title()}"
                if media_content.get("name"):
                    name = media_content.get("name")
                else:
                    caption = media_content.get("caption", [])
                    if caption and len(caption) > 0:
                        caption_text = caption[0].get("plain_text", "")
                        if caption_text:
                            name = caption_text
                
                mimetype = self._extract_mimetype(media_content, block_type, name)
                
            elif block_type == "audio":
                # Get the nested audio content
                audio_content = block_content.get("audio", {})
                audio_type = audio_content.get("type")
                
                file_url = ""
                if audio_type == "external":
                    file_url = audio_content.get("external", {}).get("url", "")
                elif audio_type == "file":
                    file_url = audio_content.get("file", {}).get("url", "")
                
                # Extract name from audio content or caption
                name = "Untitled Audio"
                if audio_content.get("name"):
                    name = audio_content.get("name")
                else:
                    caption = audio_content.get("caption", [])
                    if caption and len(caption) > 0:
                        caption_text = caption[0].get("plain_text", "")
                        if caption_text:
                            name = caption_text
                
                mimetype = self._extract_mimetype(audio_content, block_type, name)
            
            else:
                return None
            
            # Return the file information with assumed existing record ID
            return {
                "record_id": record_key,
                "file_url": file_url,
                "name": name,
                "mimetype": mimetype
            }
            
        except Exception as e:
            self.logger.error(f"Error extracting file info for block {block_id}: {str(e)}")
            return None

    def _extract_mimetype(self, content, block_type, name):
        """Extract or infer mimetype from content and block type"""
        # Try to get mimetype from file content first
        mimetype = None
        if content.get("file"):
            mimetype = content.get("file", {}).get("mimetype") or content.get("file", {}).get("mime_type")
        
        # If no mimetype found, infer from block type and file extension
        if not mimetype:
            if block_type == "image":
                if name.lower().endswith(('.jpg', '.jpeg')):
                    mimetype = "image/jpeg"
                elif name.lower().endswith('.png'):
                    mimetype = "image/png"
                elif name.lower().endswith('.gif'):
                    mimetype = "image/gif"
                elif name.lower().endswith('.webp'):
                    mimetype = "image/webp"
                else:
                    mimetype = "image/unknown"
            elif block_type == "video":
                mimetype = "video/unknown"
            elif block_type == "gif":
                mimetype = "image/gif"
            elif block_type == "audio":
                if name.lower().endswith(('.mp3', '.mpeg')):
                    mimetype = "audio/mpeg"
                elif name.lower().endswith('.wav'):
                    mimetype = "audio/wav"
                elif name.lower().endswith('.ogg'):
                    mimetype = "audio/ogg"
                elif name.lower().endswith('.m4a'):
                    mimetype = "audio/mp4"
                else:
                    mimetype = "audio/unknown"
            else:
                # For file type, try to infer from extension
                if name.lower().endswith(('.pdf',)):
                    mimetype = "application/pdf"
                elif name.lower().endswith(('.doc', '.docx')):
                    mimetype = "application/msword"
                else:
                    mimetype = "application/octet-stream"
        
        return mimetype

    async def _process_block(self, block_id, block_type, block_content, 
                        created_time, last_edited_time):
        """
        Process an individual Notion block and return formatted content.
        (Keep the existing implementation for non-file blocks)
        """
        try:
            # Initialize the standard block structure
            formatted_block = {
                "block_id": block_id,
                "block_type": block_type,
                "block_name": "",
                "block_format": "txt",  # Default format
                "block_comments": [],
                "block_creation_date": created_time,
                "block_update_date": last_edited_time,
                "links": [],
                "data": None,
                "public_data_link": "",
                "public_data_link_expiration_epoch_time_in_ms": None
            }
            
            # Process text-based blocks
            if block_type in ["paragraph", "heading_1", "heading_2", "heading_3", 
                            "bulleted_list_item", "numbered_list_item", "quote", "callout"]:
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                formatted_block["data"] = text_content
                formatted_block["block_format"] = "txt"
                
                # Extract links from rich text if any
                links = self._extract_links_from_rich_text(block_content.get("rich_text", []))
                if links:
                    formatted_block["links"] = links
                    
            # Process to-do items
            elif block_type == "to_do":
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                checked = block_content.get("checked", False)
                formatted_block["data"] = {
                    "text": text_content,
                    "checked": checked
                }
                formatted_block["block_format"] = "txt"
                
            # Process toggle blocks
            elif block_type == "toggle":
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                formatted_block["data"] = text_content
                formatted_block["block_format"] = "txt"
                
            # Process code blocks
            elif block_type == "code":
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                language = block_content.get("language", "")
                formatted_block["data"] = {
                    "code": text_content,
                    "language": language
                }
                formatted_block["block_format"] = "markdown"
                formatted_block["block_name"] = f"code_{language}"
                
            # Process divider
            elif block_type == "divider":
                formatted_block["data"] = "---"
                formatted_block["block_format"] = "markdown"
                
            # Process bookmark blocks
            elif block_type == "bookmark":
                url = block_content.get("url", "")
                caption = self._extract_text_from_rich_text(block_content.get("caption", []))
                
                formatted_block["data"] = {
                    "url": url,
                    "caption": caption
                }
                formatted_block["block_format"] = "txt"
                formatted_block["links"] = [url]
                
            # Process embed blocks
            elif block_type == "embed":
                url = block_content.get("url", "")
                caption = self._extract_text_from_rich_text(block_content.get("caption", []))
                
                formatted_block["data"] = {
                    "url": url,
                    "caption": caption
                }
                formatted_block["block_format"] = "txt"
                formatted_block["links"] = [url]
                
            # Process web_bookmark blocks
            elif block_type == "link_preview":
                url = block_content.get("url", "")
                
                formatted_block["data"] = {
                    "url": url
                }
                formatted_block["block_format"] = "txt"
                formatted_block["links"] = [url]
                
            # Process table blocks
            elif block_type == "table":
                # Table properties
                has_column_header = block_content.get("has_column_header", False)
                has_row_header = block_content.get("has_row_header", False)
                
                table_data = {
                    "has_column_header": has_column_header,
                    "has_row_header": has_row_header,
                    "rows": []  # Would be populated by fetching child blocks
                }
                
                formatted_block["data"] = table_data
                formatted_block["block_format"] = "markdown"
                formatted_block["block_name"] = f"table_{block_id[-8:]}"
                
            # Process equation blocks
            elif block_type == "equation":
                expression = block_content.get("expression", "")
                
                formatted_block["data"] = expression
                formatted_block["block_format"] = "markdown"
                formatted_block["block_name"] = "equation"
                
            # Process table of contents
            elif block_type == "table_of_contents":
                formatted_block["data"] = "Table of Contents"
                formatted_block["block_format"] = "markdown"
                
            # Process breadcrumb
            elif block_type == "breadcrumb":
                formatted_block["data"] = "Breadcrumb"
                formatted_block["block_format"] = "txt"
                
            # Process synced blocks
            elif block_type == "synced_block":
                synced_from = block_content.get("synced_from")
                if synced_from:
                    original_block_id = synced_from.get("block_id")
                    formatted_block["data"] = {
                        "synced_from_id": original_block_id
                    }
                else:
                    formatted_block["data"] = {
                        "is_original": True
                    }
                formatted_block["block_format"] = "txt"
                
            # Process template blocks
            elif block_type == "template":
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                formatted_block["data"] = text_content
                formatted_block["block_format"] = "txt"
                formatted_block["block_name"] = "template"
                
            else:
                # For any unsupported block types, store basic info
                formatted_block["data"] = f"Unsupported block type: {block_type}"
                
            # Check if we have comments for this block
            comments = await self._fetch_comments(block_id)
            if comments:
                formatted_block["block_comments"] = comments
                
            return formatted_block
        
        except Exception as e:
            self.logger.error(f"Error processing block {block_id} of type {block_type}: {str(e)}")
            return None

    def _extract_text_from_rich_text(self, rich_text):
        """Helper method to extract plain text from Notion's rich text objects."""
        if not rich_text:
            return ""
        
        return "".join([text.get("plain_text", "") for text in rich_text])

    def _extract_links_from_rich_text(self, rich_text):
        """Extract links from Notion's rich text array"""
        if not rich_text:
            return []
            
        links = []
        for text_item in rich_text:
            if text_item.get("href"):
                links.append(text_item.get("href"))
                
        return links
        
    def _generate_filename_from_url(self, url):
        """Generate a simple filename from a URL"""
        if not url:
            return "untitled"
            
        # Get the last part of the URL
        filename = url.split("/")[-1]
        
        # Remove query parameters if any
        filename = filename.split("?")[0]
        
        # If filename is still empty or too long, use a default
        if not filename or len(filename) > 50:
            filename = str(uuid4())[:8]
            
        return filename
        
    def _convert_to_epoch_ms(self, iso_datetime):
        """Convert ISO datetime string to epoch milliseconds"""
        from datetime import datetime
        import calendar
        
        dt = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
        return calendar.timegm(dt.utctimetuple()) * 1000 + dt.microsecond // 1000
        
    async def _fetch_block_children(self, block_id):
        """Fetch all child blocks for a given block ID (page or block)."""
        try:
            self.logger.info(f"Fetching child blocks for block: {block_id}")
            url = f"{self.BASE_URL}/blocks/{block_id}/children"

            all_blocks = []
            has_more = True
            next_cursor = None

            async with aiohttp.ClientSession() as session:
                while has_more:
                    params = {"page_size": 100}
                    if next_cursor:
                        params["start_cursor"] = next_cursor

                    async with session.get(
                        url, headers=self.headers, params=params
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.logger.error(
                                f"Failed to fetch block children: {error_text}"
                            )
                            raise Exception(f"Notion API error: {response.status}")

                        data = await response.json()
                        all_blocks.extend(data.get("results", []))

                        has_more = data.get("has_more", False)
                        next_cursor = data.get("next_cursor")

                self.logger.info(f"Successfully fetched {len(all_blocks)} child blocks")
                return all_blocks

        except Exception as e:
            self.logger.error(f"Error fetching block children: {str(e)}")
            raise

    def _iso_to_timestamp(self, iso_string: str) -> int:
        """Convert ISO date string to millisecond timestamp."""
        if not iso_string:
            return None
        try:
            dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

    async def _fetch_comments(self, block_id):
        """ Fetch comments for the specific page or block """
        try:
            url = f"{self.BASE_URL}/comments"
            
            # API params - filter by the block_id to get comments for this specific block
            params = {
                "block_id": block_id,
                "page_size": 100
            }
            
            all_comments = []
            has_more = True
            next_cursor = None
            
            async with aiohttp.ClientSession() as session:
                while has_more:
                    if next_cursor:
                        params["start_cursor"] = next_cursor
                    
                    async with session.get(url, headers=self.headers, params=params) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.logger.error(f"Failed to fetch comments: {error_text}")
                            raise Exception(f"Notion API error: {response.status}")
                        
                        data = await response.json()
                        all_comments.extend(data.get("results", []))
                        
                        has_more = data.get("has_more", False)
                        next_cursor = data.get("next_cursor")
                
                return all_comments
            
        except Exception as e:
            self.logger.error(f"Error fetching comments for block {block_id}: {str(e)}")
            raise

    async def fetch_file_data(self, block_id: str):
        """
        Fetch file data from Notion API and return structured file information.
        
        Args:
            block_id (str): The block ID of the file block
            
        Returns:
            dict: Structured file data with id, page_id, type, and file information
        """
        try:
            self.logger.info(f"Fetching file data for block: {block_id}")
            
            # Fetch the block data from Notion API
            url = f"{self.BASE_URL}/blocks/{block_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        self.logger.error(f"Failed to fetch block data: {error_text}")
                        raise Exception(f"Notion API error: {response.status}")
                    
                    block_data = await response.json()
            
            # Extract basic block information
            block_type = block_data.get("type")
            parent_info = block_data.get("parent", {})
            page_id = parent_info.get("page_id") or parent_info.get("database_id")
            
            # Handle different file block types
            if block_type == "file":
                file_content = block_data.get("file", {})
                file_type = file_content.get("type")
                
                # Extract file URL based on type
                if file_type == "external":
                    file_url = file_content.get("external", {}).get("url", "")
                elif file_type == "file":
                    file_info = file_content.get("file", {})
                    file_url = file_info.get("url", "")
                    expiry_time = file_info.get("expiry_time")
                else:
                    file_url = ""
                    expiry_time = None
                
                file_name = file_content.get("name", "Untitled File")
                caption = file_content.get("caption", [])
                
            elif block_type in ["image", "video", "audio"]:
                # Handle media blocks
                media_content = block_data.get(block_type, {})
                media_type = media_content.get("type")
                
                if media_type == "external":
                    file_url = media_content.get("external", {}).get("url", "")
                    expiry_time = None
                elif media_type == "file":
                    file_info = media_content.get("file", {})
                    file_url = file_info.get("url", "")
                    expiry_time = file_info.get("expiry_time")
                else:
                    file_url = ""
                    expiry_time = None
                
                # Extract name from caption or use default
                caption = media_content.get("caption", [])
                if caption and len(caption) > 0:
                    file_name = caption[0].get("plain_text", f"Untitled {block_type.title()}")
                else:
                    file_name = f"Untitled {block_type.title()}"
                    
            else:
                raise Exception(f"Block type '{block_type}' is not a supported file type")
            
            # Structure the response according to the required format
            structured_data = {
                "id": block_id,
                "page_id": page_id,
                "type": "file",
                "file": {
                    "caption": caption if isinstance(caption, list) else [],
                    "type": "file",
                    "file": {
                        "url": file_url,
                        "expiry_time": expiry_time
                    },
                    "name": file_name
                }
            }
            
            self.logger.info(f"Successfully fetched file data for block: {block_id}")
            return structured_data
            
        except Exception as e:
            self.logger.error(f"Error fetching file data for block {block_id}: {str(e)}")
            raise Exception(f"Failed to fetch file data: {str(e)}")
    
    async def get_file_download_info(self, block_id: str):
        """
        Convenience method to get just the essential file download information.
        
        Args:
            block_id (str): The block ID of the file block
            
        Returns:
            dict: File download information with URL, name, and expiry
        """
        try:
            file_data = await self.fetch_file_data(block_id)
            
            return {
                "url": file_data["file"]["file"]["url"],
                "name": file_data["file"]["name"],
                "expiry_time": file_data["file"]["file"]["expiry_time"],
                "block_id": block_id
            }
            
        except Exception as e:
            self.logger.error(f"Error getting file download info for block {block_id}: {str(e)}")
            raise