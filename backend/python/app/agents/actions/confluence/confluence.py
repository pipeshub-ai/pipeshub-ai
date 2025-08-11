import json
import logging
from typing import Optional, Union

from app.agents.actions.confluence.config import (
    ConfluenceApiKeyConfig,
    ConfluenceTokenConfig,
    ConfluenceUsernamePasswordConfig,
)

logger = logging.getLogger(__name__)


class Confluence:
    """Confluence tool exposed to the agents"""
    def __init__(
        self,
        config: Union[ConfluenceUsernamePasswordConfig, ConfluenceTokenConfig, ConfluenceApiKeyConfig]) -> None:
        """Initialize the Confluence tool"""
        """
        Args:
            config: Confluence configuration (ConfluenceUsernamePasswordConfig, ConfluenceTokenConfig, ConfluenceApiKeyConfig)
        Returns:
            None
        """
        self.config = config
        try:
            logger.info(f"Initializing Confluence with config: {config}")
            self.confluence = config.create_client()
        except Exception as e:
            logger.error(f"Failed to initialize Confluence: {e}")
            raise

    def create_page(self, space: str, page_title: str, page_content: str, parent_id: Optional[str] = None) -> tuple[bool, str]:
        """Create a new page in Confluence
        """
        """
        Args:
            space: The space of the page
            page_title: The title of the page
            page_content: The content of the page
            parent_id: The ID of the parent page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the page details
        """
        space_id = self.__get_space_id(space)
        if not space_id:
            return (False, json.dumps({"error": "Space not found"}))

        try:
            page = self.confluence.create_page(
                space=space_id,
                title=page_title,
                body=page_content,
                parent_id=parent_id
            )
            return (True, json.dumps({
                "confluence_url": f"{self.config.base_url}/display/{page.id}",
                "page_id": page.id,
                "page_title": page.title,
                "page_content": page.body,
                "parent_id": parent_id
            }))
        except Exception as e:
            logger.error(f"Failed to create page: {e}")
            return (False, json.dumps({"error": str(e)}))

    def get_page(self, page_id: str) -> tuple[bool, str]:
        """Get a page from Confluence
        """
        """
        Args:
            page_id: The ID of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the page details
        """
        try:
            page = self.confluence.get_page(page_id)
            return (True, json.dumps(page))
        except Exception as e:
            logger.error(f"Failed to get page: {e}")
            return (False, json.dumps({"error": str(e)}))

    def update_page(self, page_id: str, page_title: str, page_content: str) -> tuple[bool, str]:
        """Update a page in Confluence
        """
        """
        Args:
            page_id: The ID of the page
            page_title: The title of the page
            page_content: The content of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the page details
        """
        try:
            page = self.confluence.update_page(page_id, page_title, page_content)
            return (True, json.dumps(page))
        except Exception as e:
            logger.error(f"Failed to update page: {e}")
            return (False, json.dumps({"error": str(e)}))

    def delete_page(self, page_id: str) -> tuple[bool, str]:
        """Delete a page from Confluence
        """
        """
        Args:
            page_id: The ID of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the page details
        """
        try:
            self.confluence.delete_page(page_id)
            return (True, json.dumps({"message": "Page deleted successfully"}))
        except Exception as e:
            logger.error(f"Failed to delete page: {e}")
            return (False, json.dumps({"error": str(e)}))

    def get_page_children(self, page_id: str) -> tuple[bool, str]:
        """Get the children of a page in Confluence
        """
        """
        Args:
            page_id: The ID of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the page details
        """
        try:
            children = self.confluence.get_page_children(page_id)
            return (True, json.dumps(children))
        except Exception as e:
            logger.error(f"Failed to get page children: {e}")
            return (False, json.dumps({"error": str(e)}))

    def get_page_ancestors(self, page_id: str) -> tuple[bool, str]:
        """Get the ancestors of a page in Confluence
        """
        """
        Args:
            page_id: The ID of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the page details
        """
        try:
            ancestors = self.confluence.get_page_ancestors(page_id)
            return (True, json.dumps(ancestors))
        except Exception as e:
            logger.error(f"Failed to get page ancestors: {e}")
            return (False, json.dumps({"error": str(e)}))

    def get_page_descendants(self, page_id: str) -> tuple[bool, str]:
        """Get the descendants of a page in Confluence
        """
        """
        Args:
            page_id: The ID of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the page details
        """
        try:
            descendants = self.confluence.get_page_descendants(page_id)
            return (True, json.dumps(descendants))
        except Exception as e:
            logger.error(f"Failed to get page descendants: {e}")
            return (False, json.dumps({"error": str(e)}))

    def get_page_parent(self, page_id: str) -> tuple[bool, str]:
        """Get the parent of a page in Confluence
        """
        """
        Args:
            page_id: The ID of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the page details
        """
        try:
            parent = self.confluence.get_page_parent(page_id)
            return (True, json.dumps(parent))
        except Exception as e:
            logger.error(f"Failed to get page parent: {e}")
            return (False, json.dumps({"error": str(e)}))

    def search_pages(self, query: str, expand: Optional[str] = None, limit: Optional[int] = None) -> tuple[bool, str]:
        """Search for pages in Confluence
        """
        """
        Args:
            query: The query to search for
            expand: The expand to search for
            limit: The limit of the search results
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the search results
        """
        try:
            search_results = self.confluence.search_pages(query=query, expand=expand, limit=limit) # type: ignore
            return (True, json.dumps(search_results))
        except Exception as e:
            logger.error(f"Failed to search pages: {e}")
            return (False, json.dumps({"error": str(e)}))

    def __get_space_id(self, space: str) -> Optional[str]:
        """Get the ID of a space in Confluence
        """
        """
        Args:
            space: The name of the space
        Returns:
            The ID of the space
        """
        try:
            spaces = self.confluence.get_spaces()
            for space in spaces:
                if space.name == space: # type: ignore
                    return space.id # type: ignore
            return None
        except Exception as e:
            logger.error(f"Failed to get space: {e}")
            return None
