
import markdown
from docling.datamodel.document import DoclingDocument
from docling.document_converter import DocumentConverter
import re
from typing import List, Dict, Tuple


class MarkdownParser:
    def __init__(self) -> None:
        self.converter = DocumentConverter()

    def parse_string(self, md_content: str) -> bytes:
        """
        Parse Markdown content from a string.

        Args:
            md_content (str): Markdown content as a string

        Returns:
            Document: Parsed Docling document

        Raises:
            ValueError: If parsing fails
        """
        # Convert string to bytes

        html = markdown.markdown(md_content, extensions=["md_in_html"])
        md_bytes = html.encode("utf-8")
        return md_bytes

    def parse_file(self, file_path: str) -> DoclingDocument:
        """
        Parse Markdown content from a file.

        Args:
            file_path (str): Path to the Markdown file

        Returns:
            Document: Parsed Docling document

        Raises:
            ValueError: If parsing fails
        """
        result = self.converter.convert(file_path)

        if result.status.value != "success":
            raise ValueError(f"Failed to parse Markdown: {result.status}")

        return result.document

    def extract_and_replace_images(self, md_content: str) -> Tuple[str, List[Dict[str, str]]]:
        """
        Extract all images from markdown content (inline, reference-style, and HTML syntax)
        and replace their alt text sequentially.
        
        Args:
            md_content (str): The content of the markdown file
            
        Returns:
            Tuple[str, List[Dict[str, str]]]: 
                - Modified markdown content with replaced alt text
                - List of dictionaries containing image details:
                    {
                        'original_text': str,
                        'url': str,
                        'alt_text': str,
                        'new_alt_text': str,
                        'image_type': str  # 'markdown', 'reference', or 'html'
                    }
        """
        images: List[Dict[str, str]] = []
        modified_content = md_content
        image_counter = 1
        
        # Pattern for markdown inline images: ![alt text](url "optional title")
        markdown_img_pattern = r'!\[([^\]]*)\]\(([^\)]+)\)'
        
        # Pattern for reference-style image usage: ![alt text][reference]
        reference_usage_pattern = r'!\[([^\]]*)\]\[([^\]]+)\]'
        
        # Pattern for reference definitions: [reference]: url "optional title"
        reference_def_pattern = r'^\[([^\]]+)\]:\s*(.+?)(?:\s+"[^"]*")?\s*$'
        
        # Pattern for HTML img tags with various formats
        html_img_pattern = r'<img[^>]*?(?:src\s*=\s*["\']([^"\']+)["\'][^>]*?alt\s*=\s*["\']([^"\']*)["\']|alt\s*=\s*["\']([^"\']*)["\'][^>]*?src\s*=\s*["\']([^"\']+)["\'])[^>]*?>'
        
        # First, collect all reference definitions
        reference_map = {}
        for match in re.finditer(reference_def_pattern, md_content, re.MULTILINE):
            ref_id = match.group(1)
            ref_url = match.group(2).strip()
            reference_map[ref_id] = ref_url
        
        # Track reference-style images to avoid duplicates with inline
        reference_positions = set()
        
        # Find all reference-style images first (to avoid conflicts with inline images)
        for match in re.finditer(reference_usage_pattern, md_content):
            original_alt = match.group(1)
            ref_id = match.group(2)
            original_text = match.group(0)
            
            # Get URL from reference map
            url = reference_map.get(ref_id, f"[unknown reference: {ref_id}]")
            
            new_alt = f"Image_{image_counter}"
            
            # Create new reference-style image syntax
            new_text = f"![{new_alt}][{ref_id}]"
            
            # Store image info
            images.append({
                'original_text': original_text,
                'url': url,
                'alt_text': original_alt,
                'new_alt_text': new_alt,
                'image_type': 'reference'
            })
            
            # Track position to avoid processing as inline
            reference_positions.add(match.start())
            
            # Replace in content
            modified_content = modified_content.replace(original_text, new_text, 1)
            image_counter += 1
        
        # Find all markdown inline images
        for match in re.finditer(markdown_img_pattern, md_content):
            # Skip if this position was already processed as reference-style
            if match.start() in reference_positions:
                continue
                
            original_alt = match.group(1)
            url = match.group(2)
            original_text = match.group(0)
            new_alt = f"Image_{image_counter}"
            
            # Create new markdown image syntax
            new_text = f"![{new_alt}]({url})"
            
            # Store image info
            images.append({
                'original_text': original_text,
                'url': url,
                'alt_text': original_alt,
                'new_alt_text': new_alt,
                'image_type': 'markdown'
            })
            
            # Replace in content
            modified_content = modified_content.replace(original_text, new_text, 1)
            image_counter += 1
        
        # Find all HTML images
        for match in re.finditer(html_img_pattern, md_content):
            original_text = match.group(0)
            
            # Determine which groups captured the src and alt
            if match.group(1):  # src came first
                url = match.group(1)
                original_alt = match.group(2)
            else:  # alt came first
                original_alt = match.group(3)
                url = match.group(4)
            
            new_alt = f"Image_{image_counter}"
            
            # Create new HTML img tag with updated alt text
            new_text = re.sub(
                r'alt\s*=\s*["\'][^"\']*["\']',
                f'alt="{new_alt}"',
                original_text
            )
            
            # Store image info
            images.append({
                'original_text': original_text,
                'url': url,
                'alt_text': original_alt,
                'new_alt_text': new_alt,
                'image_type': 'html'
            })
            
            # Replace in content
            modified_content = modified_content.replace(original_text, new_text, 1)
            image_counter += 1
        
        return modified_content, images


    