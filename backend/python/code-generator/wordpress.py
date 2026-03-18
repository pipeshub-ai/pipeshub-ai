# ruff: noqa
"""
WordPress REST API Code Generator

Generates WordPressDataSource class covering WordPress REST API v2:
- Posts CRUD
- Pages CRUD
- Categories and Tags
- Comments
- Users
- Media
- Post Types, Statuses, Taxonomies
- Search

The generated DataSource accepts a WordPressClient and uses the client's
configured base URL to construct API endpoints. Methods are generated
with explicit parameter signatures and no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# WordPress REST API Endpoints
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which already includes /wp/v2 or /wp-json/wp/v2)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
# ================================================================================

WORDPRESS_API_ENDPOINTS = {
    # ================================================================================
    # POSTS
    # ================================================================================
    "list_posts": {
        "method": "GET",
        "path": "/posts",
        "description": "List all posts",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Current page of the collection (default 1)"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Maximum number of items per page (default 10, max 100)"},
            "search": {"type": "Optional[str]", "location": "query", "description": "Limit results to those matching a search string"},
            "after": {"type": "Optional[str]", "location": "query", "description": "Limit to posts published after a given ISO8601 date"},
            "before": {"type": "Optional[str]", "location": "query", "description": "Limit to posts published before a given ISO8601 date"},
            "author": {"type": "Optional[str]", "location": "query", "description": "Limit to posts by one or more author IDs (comma-separated)"},
            "categories": {"type": "Optional[str]", "location": "query", "description": "Limit to posts in specific category IDs (comma-separated)"},
            "tags": {"type": "Optional[str]", "location": "query", "description": "Limit to posts with specific tag IDs (comma-separated)"},
            "status": {"type": "Optional[str]", "location": "query", "description": "Limit to posts with a specific status (publish, draft, pending, etc.)"},
            "orderby": {"type": "Optional[str]", "location": "query", "description": "Sort by attribute (date, relevance, id, include, title, slug)"},
            "order": {"type": "Optional[str]", "location": "query", "description": "Sort order (asc or desc)"},
        },
        "required": [],
    },
    "get_post": {
        "method": "GET",
        "path": "/posts/{post_id}",
        "description": "Get a specific post by ID",
        "parameters": {
            "post_id": {"type": "str", "location": "path", "description": "The post ID"},
        },
        "required": ["post_id"],
    },
    "create_post": {
        "method": "POST",
        "path": "/posts",
        "description": "Create a new post",
        "parameters": {
            "title": {"type": "str", "location": "body", "description": "The title for the post"},
            "content": {"type": "Optional[str]", "location": "body", "description": "The content for the post"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Post status (publish, draft, pending, private)"},
            "excerpt": {"type": "Optional[str]", "location": "body", "description": "The excerpt for the post"},
            "author": {"type": "Optional[int]", "location": "body", "description": "The ID of the author"},
            "categories": {"type": "Optional[list[int]]", "location": "body", "description": "Category IDs for the post"},
            "tags": {"type": "Optional[list[int]]", "location": "body", "description": "Tag IDs for the post"},
            "format": {"type": "Optional[str]", "location": "body", "description": "Post format (standard, aside, chat, gallery, link, image, quote, status, video, audio)"},
            "slug": {"type": "Optional[str]", "location": "body", "description": "Alphanumeric identifier for the post"},
            "comment_status": {"type": "Optional[str]", "location": "body", "description": "Whether comments are open (open or closed)"},
            "ping_status": {"type": "Optional[str]", "location": "body", "description": "Whether pings are accepted (open or closed)"},
            "featured_media": {"type": "Optional[int]", "location": "body", "description": "The ID of the featured media"},
        },
        "required": ["title"],
    },
    "update_post": {
        "method": "PUT",
        "path": "/posts/{post_id}",
        "description": "Update an existing post",
        "parameters": {
            "post_id": {"type": "str", "location": "path", "description": "The post ID"},
            "title": {"type": "Optional[str]", "location": "body", "description": "The title for the post"},
            "content": {"type": "Optional[str]", "location": "body", "description": "The content for the post"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Post status (publish, draft, pending, private)"},
            "excerpt": {"type": "Optional[str]", "location": "body", "description": "The excerpt for the post"},
            "author": {"type": "Optional[int]", "location": "body", "description": "The ID of the author"},
            "categories": {"type": "Optional[list[int]]", "location": "body", "description": "Category IDs for the post"},
            "tags": {"type": "Optional[list[int]]", "location": "body", "description": "Tag IDs for the post"},
            "slug": {"type": "Optional[str]", "location": "body", "description": "Alphanumeric identifier for the post"},
            "comment_status": {"type": "Optional[str]", "location": "body", "description": "Whether comments are open (open or closed)"},
            "featured_media": {"type": "Optional[int]", "location": "body", "description": "The ID of the featured media"},
        },
        "required": ["post_id"],
    },
    "delete_post": {
        "method": "DELETE",
        "path": "/posts/{post_id}",
        "description": "Delete a post",
        "parameters": {
            "post_id": {"type": "str", "location": "path", "description": "The post ID"},
        },
        "required": ["post_id"],
    },

    # ================================================================================
    # PAGES
    # ================================================================================
    "list_pages": {
        "method": "GET",
        "path": "/pages",
        "description": "List all pages",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Current page of the collection (default 1)"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Maximum number of items per page (default 10, max 100)"},
            "search": {"type": "Optional[str]", "location": "query", "description": "Limit results to those matching a search string"},
            "after": {"type": "Optional[str]", "location": "query", "description": "Limit to pages published after a given ISO8601 date"},
            "before": {"type": "Optional[str]", "location": "query", "description": "Limit to pages published before a given ISO8601 date"},
            "author": {"type": "Optional[str]", "location": "query", "description": "Limit to pages by one or more author IDs (comma-separated)"},
            "status": {"type": "Optional[str]", "location": "query", "description": "Limit to pages with a specific status"},
            "orderby": {"type": "Optional[str]", "location": "query", "description": "Sort by attribute (date, relevance, id, include, title, slug, menu_order)"},
            "order": {"type": "Optional[str]", "location": "query", "description": "Sort order (asc or desc)"},
        },
        "required": [],
    },
    "get_page": {
        "method": "GET",
        "path": "/pages/{page_id}",
        "description": "Get a specific page by ID",
        "parameters": {
            "page_id": {"type": "str", "location": "path", "description": "The page ID"},
        },
        "required": ["page_id"],
    },
    "create_page": {
        "method": "POST",
        "path": "/pages",
        "description": "Create a new page",
        "parameters": {
            "title": {"type": "str", "location": "body", "description": "The title for the page"},
            "content": {"type": "Optional[str]", "location": "body", "description": "The content for the page"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Page status (publish, draft, pending, private)"},
            "excerpt": {"type": "Optional[str]", "location": "body", "description": "The excerpt for the page"},
            "author": {"type": "Optional[int]", "location": "body", "description": "The ID of the author"},
            "parent": {"type": "Optional[int]", "location": "body", "description": "Parent page ID"},
            "menu_order": {"type": "Optional[int]", "location": "body", "description": "Page order in menu"},
            "slug": {"type": "Optional[str]", "location": "body", "description": "Alphanumeric identifier for the page"},
            "comment_status": {"type": "Optional[str]", "location": "body", "description": "Whether comments are open (open or closed)"},
            "featured_media": {"type": "Optional[int]", "location": "body", "description": "The ID of the featured media"},
        },
        "required": ["title"],
    },
    "update_page": {
        "method": "PUT",
        "path": "/pages/{page_id}",
        "description": "Update an existing page",
        "parameters": {
            "page_id": {"type": "str", "location": "path", "description": "The page ID"},
            "title": {"type": "Optional[str]", "location": "body", "description": "The title for the page"},
            "content": {"type": "Optional[str]", "location": "body", "description": "The content for the page"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Page status (publish, draft, pending, private)"},
            "excerpt": {"type": "Optional[str]", "location": "body", "description": "The excerpt for the page"},
            "author": {"type": "Optional[int]", "location": "body", "description": "The ID of the author"},
            "parent": {"type": "Optional[int]", "location": "body", "description": "Parent page ID"},
            "menu_order": {"type": "Optional[int]", "location": "body", "description": "Page order in menu"},
            "slug": {"type": "Optional[str]", "location": "body", "description": "Alphanumeric identifier for the page"},
            "featured_media": {"type": "Optional[int]", "location": "body", "description": "The ID of the featured media"},
        },
        "required": ["page_id"],
    },
    "delete_page": {
        "method": "DELETE",
        "path": "/pages/{page_id}",
        "description": "Delete a page",
        "parameters": {
            "page_id": {"type": "str", "location": "path", "description": "The page ID"},
        },
        "required": ["page_id"],
    },

    # ================================================================================
    # CATEGORIES
    # ================================================================================
    "list_categories": {
        "method": "GET",
        "path": "/categories",
        "description": "List all categories",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Current page of the collection (default 1)"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Maximum number of items per page (default 10, max 100)"},
            "search": {"type": "Optional[str]", "location": "query", "description": "Limit results to those matching a search string"},
            "parent": {"type": "Optional[int]", "location": "query", "description": "Limit to categories with a specific parent ID"},
            "orderby": {"type": "Optional[str]", "location": "query", "description": "Sort by attribute (id, include, name, slug, count, description)"},
            "order": {"type": "Optional[str]", "location": "query", "description": "Sort order (asc or desc)"},
        },
        "required": [],
    },
    "get_category": {
        "method": "GET",
        "path": "/categories/{category_id}",
        "description": "Get a specific category by ID",
        "parameters": {
            "category_id": {"type": "str", "location": "path", "description": "The category ID"},
        },
        "required": ["category_id"],
    },
    "create_category": {
        "method": "POST",
        "path": "/categories",
        "description": "Create a new category",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "The name of the category"},
            "description": {"type": "Optional[str]", "location": "body", "description": "Category description"},
            "slug": {"type": "Optional[str]", "location": "body", "description": "Alphanumeric identifier for the category"},
            "parent": {"type": "Optional[int]", "location": "body", "description": "Parent category ID"},
        },
        "required": ["name"],
    },
    "update_category": {
        "method": "PUT",
        "path": "/categories/{category_id}",
        "description": "Update a category",
        "parameters": {
            "category_id": {"type": "str", "location": "path", "description": "The category ID"},
            "name": {"type": "Optional[str]", "location": "body", "description": "The name of the category"},
            "description": {"type": "Optional[str]", "location": "body", "description": "Category description"},
            "slug": {"type": "Optional[str]", "location": "body", "description": "Alphanumeric identifier for the category"},
            "parent": {"type": "Optional[int]", "location": "body", "description": "Parent category ID"},
        },
        "required": ["category_id"],
    },
    "delete_category": {
        "method": "DELETE",
        "path": "/categories/{category_id}",
        "description": "Delete a category",
        "parameters": {
            "category_id": {"type": "str", "location": "path", "description": "The category ID"},
        },
        "required": ["category_id"],
    },

    # ================================================================================
    # TAGS
    # ================================================================================
    "list_tags": {
        "method": "GET",
        "path": "/tags",
        "description": "List all tags",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Current page of the collection (default 1)"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Maximum number of items per page (default 10, max 100)"},
            "search": {"type": "Optional[str]", "location": "query", "description": "Limit results to those matching a search string"},
            "orderby": {"type": "Optional[str]", "location": "query", "description": "Sort by attribute (id, include, name, slug, count, description)"},
            "order": {"type": "Optional[str]", "location": "query", "description": "Sort order (asc or desc)"},
        },
        "required": [],
    },
    "get_tag": {
        "method": "GET",
        "path": "/tags/{tag_id}",
        "description": "Get a specific tag by ID",
        "parameters": {
            "tag_id": {"type": "str", "location": "path", "description": "The tag ID"},
        },
        "required": ["tag_id"],
    },
    "create_tag": {
        "method": "POST",
        "path": "/tags",
        "description": "Create a new tag",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "The name of the tag"},
            "description": {"type": "Optional[str]", "location": "body", "description": "Tag description"},
            "slug": {"type": "Optional[str]", "location": "body", "description": "Alphanumeric identifier for the tag"},
        },
        "required": ["name"],
    },
    "update_tag": {
        "method": "PUT",
        "path": "/tags/{tag_id}",
        "description": "Update a tag",
        "parameters": {
            "tag_id": {"type": "str", "location": "path", "description": "The tag ID"},
            "name": {"type": "Optional[str]", "location": "body", "description": "The name of the tag"},
            "description": {"type": "Optional[str]", "location": "body", "description": "Tag description"},
            "slug": {"type": "Optional[str]", "location": "body", "description": "Alphanumeric identifier for the tag"},
        },
        "required": ["tag_id"],
    },
    "delete_tag": {
        "method": "DELETE",
        "path": "/tags/{tag_id}",
        "description": "Delete a tag",
        "parameters": {
            "tag_id": {"type": "str", "location": "path", "description": "The tag ID"},
        },
        "required": ["tag_id"],
    },

    # ================================================================================
    # COMMENTS
    # ================================================================================
    "list_comments": {
        "method": "GET",
        "path": "/comments",
        "description": "List all comments",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Current page of the collection (default 1)"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Maximum number of items per page (default 10, max 100)"},
            "search": {"type": "Optional[str]", "location": "query", "description": "Limit results to those matching a search string"},
            "after": {"type": "Optional[str]", "location": "query", "description": "Limit to comments published after a given ISO8601 date"},
            "before": {"type": "Optional[str]", "location": "query", "description": "Limit to comments published before a given ISO8601 date"},
            "post": {"type": "Optional[int]", "location": "query", "description": "Limit to comments for a specific post ID"},
            "author": {"type": "Optional[str]", "location": "query", "description": "Limit to comments by a specific author ID"},
            "status": {"type": "Optional[str]", "location": "query", "description": "Limit to comments with a specific status (approve, hold, spam, trash)"},
            "orderby": {"type": "Optional[str]", "location": "query", "description": "Sort by attribute (date, date_gmt, id, include, post, parent, type)"},
            "order": {"type": "Optional[str]", "location": "query", "description": "Sort order (asc or desc)"},
        },
        "required": [],
    },
    "get_comment": {
        "method": "GET",
        "path": "/comments/{comment_id}",
        "description": "Get a specific comment by ID",
        "parameters": {
            "comment_id": {"type": "str", "location": "path", "description": "The comment ID"},
        },
        "required": ["comment_id"],
    },
    "create_comment": {
        "method": "POST",
        "path": "/comments",
        "description": "Create a new comment",
        "parameters": {
            "post": {"type": "int", "location": "body", "description": "The ID of the post the comment is for"},
            "content": {"type": "str", "location": "body", "description": "The content of the comment"},
            "author": {"type": "Optional[int]", "location": "body", "description": "The ID of the comment author"},
            "author_name": {"type": "Optional[str]", "location": "body", "description": "Display name of the comment author"},
            "author_email": {"type": "Optional[str]", "location": "body", "description": "Email of the comment author"},
            "author_url": {"type": "Optional[str]", "location": "body", "description": "URL of the comment author"},
            "parent": {"type": "Optional[int]", "location": "body", "description": "Parent comment ID for threaded comments"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Comment status (approve, hold, spam, trash)"},
        },
        "required": ["post", "content"],
    },
    "update_comment": {
        "method": "PUT",
        "path": "/comments/{comment_id}",
        "description": "Update a comment",
        "parameters": {
            "comment_id": {"type": "str", "location": "path", "description": "The comment ID"},
            "content": {"type": "Optional[str]", "location": "body", "description": "The content of the comment"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Comment status (approve, hold, spam, trash)"},
            "author": {"type": "Optional[int]", "location": "body", "description": "The ID of the comment author"},
        },
        "required": ["comment_id"],
    },
    "delete_comment": {
        "method": "DELETE",
        "path": "/comments/{comment_id}",
        "description": "Delete a comment",
        "parameters": {
            "comment_id": {"type": "str", "location": "path", "description": "The comment ID"},
        },
        "required": ["comment_id"],
    },

    # ================================================================================
    # USERS
    # ================================================================================
    "list_users": {
        "method": "GET",
        "path": "/users",
        "description": "List all users",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Current page of the collection (default 1)"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Maximum number of items per page (default 10, max 100)"},
            "search": {"type": "Optional[str]", "location": "query", "description": "Limit results to those matching a search string"},
            "roles": {"type": "Optional[str]", "location": "query", "description": "Limit to users with specific roles (comma-separated)"},
            "orderby": {"type": "Optional[str]", "location": "query", "description": "Sort by attribute (id, include, name, registered_date, slug, email, url)"},
            "order": {"type": "Optional[str]", "location": "query", "description": "Sort order (asc or desc)"},
        },
        "required": [],
    },
    "get_user": {
        "method": "GET",
        "path": "/users/{user_id}",
        "description": "Get a specific user by ID",
        "parameters": {
            "user_id": {"type": "str", "location": "path", "description": "The user ID"},
        },
        "required": ["user_id"],
    },
    "get_current_user": {
        "method": "GET",
        "path": "/users/me",
        "description": "Get the current authenticated user",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # MEDIA
    # ================================================================================
    "list_media": {
        "method": "GET",
        "path": "/media",
        "description": "List all media items",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Current page of the collection (default 1)"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Maximum number of items per page (default 10, max 100)"},
            "search": {"type": "Optional[str]", "location": "query", "description": "Limit results to those matching a search string"},
            "after": {"type": "Optional[str]", "location": "query", "description": "Limit to media uploaded after a given ISO8601 date"},
            "before": {"type": "Optional[str]", "location": "query", "description": "Limit to media uploaded before a given ISO8601 date"},
            "media_type": {"type": "Optional[str]", "location": "query", "description": "Limit to a specific media type (image, video, text, application, audio)"},
            "mime_type": {"type": "Optional[str]", "location": "query", "description": "Limit to a specific MIME type"},
            "orderby": {"type": "Optional[str]", "location": "query", "description": "Sort by attribute (date, relevance, id, include, title, slug)"},
            "order": {"type": "Optional[str]", "location": "query", "description": "Sort order (asc or desc)"},
        },
        "required": [],
    },
    "get_media_item": {
        "method": "GET",
        "path": "/media/{media_id}",
        "description": "Get a specific media item by ID",
        "parameters": {
            "media_id": {"type": "str", "location": "path", "description": "The media item ID"},
        },
        "required": ["media_id"],
    },
    "delete_media_item": {
        "method": "DELETE",
        "path": "/media/{media_id}",
        "description": "Delete a media item",
        "parameters": {
            "media_id": {"type": "str", "location": "path", "description": "The media item ID"},
        },
        "required": ["media_id"],
    },

    # ================================================================================
    # POST TYPES
    # ================================================================================
    "list_post_types": {
        "method": "GET",
        "path": "/types",
        "description": "List all post types",
        "parameters": {},
        "required": [],
    },
    "get_post_type": {
        "method": "GET",
        "path": "/types/{type_slug}",
        "description": "Get a specific post type by slug",
        "parameters": {
            "type_slug": {"type": "str", "location": "path", "description": "The post type slug (e.g., post, page)"},
        },
        "required": ["type_slug"],
    },

    # ================================================================================
    # POST STATUSES
    # ================================================================================
    "list_post_statuses": {
        "method": "GET",
        "path": "/statuses",
        "description": "List all post statuses",
        "parameters": {},
        "required": [],
    },
    "get_post_status": {
        "method": "GET",
        "path": "/statuses/{status_slug}",
        "description": "Get a specific post status by slug",
        "parameters": {
            "status_slug": {"type": "str", "location": "path", "description": "The status slug (e.g., publish, draft, pending)"},
        },
        "required": ["status_slug"],
    },

    # ================================================================================
    # TAXONOMIES
    # ================================================================================
    "list_taxonomies": {
        "method": "GET",
        "path": "/taxonomies",
        "description": "List all taxonomies",
        "parameters": {},
        "required": [],
    },
    "get_taxonomy": {
        "method": "GET",
        "path": "/taxonomies/{taxonomy_slug}",
        "description": "Get a specific taxonomy by slug",
        "parameters": {
            "taxonomy_slug": {"type": "str", "location": "path", "description": "The taxonomy slug (e.g., category, post_tag)"},
        },
        "required": ["taxonomy_slug"],
    },

    # ================================================================================
    # SEARCH
    # ================================================================================
    "search_content": {
        "method": "GET",
        "path": "/search",
        "description": "Search site content across multiple types",
        "parameters": {
            "search": {"type": "str", "location": "query", "description": "The search term (required)"},
            "type_": {"type": "Optional[str]", "location": "query", "description": "Limit to a specific object type (post, term, post-format)"},
            "subtype": {"type": "Optional[str]", "location": "query", "description": "Limit to specific subtypes (post, page, category, tag, or any)"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Maximum number of items per page (default 10, max 100)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Current page of the collection (default 1)"},
        },
        "required": ["search"],
    },
}


class WordPressDataSourceGenerator:
    """Generator for comprehensive WordPress REST API datasource class.

    Generates methods for WordPress REST API v2 endpoints.
    The generated DataSource class accepts a WordPressClient whose base URL
    setting determines the API target.
    """

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    # Python builtins that must be avoided as parameter names
    _PYTHON_BUILTINS = frozenset({
        "format", "type", "input", "id", "hash", "range", "list",
        "dict", "set", "map", "filter", "open", "print", "next", "object",
        "property", "super", "abs", "all", "any", "bin", "bool", "bytes",
        "callable", "chr", "complex", "dir", "divmod", "enumerate", "eval",
        "exec", "float", "frozenset", "getattr", "globals", "hasattr",
        "help", "hex", "int", "isinstance", "issubclass", "iter", "len",
        "locals", "max", "memoryview", "min", "oct", "ord", "pow", "repr",
        "reversed", "round", "setattr", "slice", "sorted", "str", "sum",
        "tuple", "vars", "zip",
    })

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        # Avoid shadowing Python builtins
        if sanitized in self._PYTHON_BUILTINS:
            sanitized = f"{sanitized}_"
        return sanitized

    def _build_query_params(self, endpoint_info: Dict) -> List[str]:
        """Build query parameter handling code."""
        lines = ["        query_params: dict[str, Any] = {}"]
        required = endpoint_info.get("required", [])

        for param_name, param_info in endpoint_info["parameters"].items():
            if param_info["location"] == "query":
                sanitized_name = self._sanitize_parameter_name(param_name)
                # Map the sanitized Python name back to the actual API query key
                # For 'type_' parameter, the API key should be 'type'
                api_key = param_name.rstrip("_")
                is_required = param_name in required

                if is_required:
                    # Required query params are always added unconditionally
                    if "bool" in param_info["type"]:
                        lines.append(
                            f"        query_params['{api_key}'] = str({sanitized_name}).lower()"
                        )
                    elif "int" in param_info["type"]:
                        lines.append(
                            f"        query_params['{api_key}'] = str({sanitized_name})"
                        )
                    else:
                        lines.append(
                            f"        query_params['{api_key}'] = {sanitized_name}"
                        )
                elif "Optional[bool]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_key}'] = str({sanitized_name}).lower()",
                    ])
                elif "Optional[int]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_key}'] = str({sanitized_name})",
                    ])
                else:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_key}'] = {sanitized_name}",
                    ])

        return lines

    def _build_path_formatting(self, path: str, endpoint_info: Dict) -> str:
        """Build URL path with parameter substitution."""
        path_params = [
            name
            for name, info in endpoint_info["parameters"].items()
            if info["location"] == "path"
        ]

        if path_params:
            format_dict = ", ".join(
                f"{param}={self._sanitize_parameter_name(param)}"
                for param in path_params
            )
            return f'        url = self.base_url + "{path}".format({format_dict})'
        else:
            return f'        url = self.base_url + "{path}"'

    def _build_request_body(self, endpoint_info: Dict) -> List[str]:
        """Build request body handling."""
        body_params = {
            name: info
            for name, info in endpoint_info["parameters"].items()
            if info["location"] == "body"
        }

        if not body_params:
            return []

        lines = ["        body: dict[str, Any] = {}"]

        for param_name, param_info in body_params.items():
            sanitized_name = self._sanitize_parameter_name(param_name)

            if param_name in endpoint_info["required"]:
                lines.append(f"        body['{param_name}'] = {sanitized_name}")
            else:
                lines.extend([
                    f"        if {sanitized_name} is not None:",
                    f"            body['{param_name}'] = {sanitized_name}",
                ])

        return lines

    @staticmethod
    def _modernize_type(type_str: str) -> str:
        """Convert typing-style annotations to modern Python 3.10+ syntax.

        Optional[str] -> str | None, Dict[str, Any] -> dict[str, Any],
        List[str] -> list[str], etc.
        """
        if type_str.startswith("Optional[") and type_str.endswith("]"):
            inner = type_str[len("Optional["):-1]
            inner = WordPressDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = WordPressDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                WordPressDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = WordPressDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                WordPressDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"list[{modernized}]"
        if type_str == "List":
            return "list"
        return type_str

    @staticmethod
    def _split_type_args(s: str) -> List[str]:
        """Split type arguments respecting nested brackets."""
        parts = []
        depth = 0
        current = ""
        for ch in s:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current.strip())
        return parts

    def _generate_method_signature(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate method signature with explicit parameters."""
        params = ["self"]
        has_any_bool = False

        # Collect required params, split into non-bool and bool groups
        required_non_bool: List[str] = []
        required_bool: List[str] = []
        for param_name in endpoint_info["required"]:
            if param_name in endpoint_info["parameters"]:
                param_info = endpoint_info["parameters"][param_name]
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                param_str = f"{sanitized_name}: {modern_type}"
                if "bool" in param_info.get("type", ""):
                    required_bool.append(param_str)
                    has_any_bool = True
                else:
                    required_non_bool.append(param_str)

        # Collect optional parameters
        optional_params: List[str] = []
        for param_name, param_info in endpoint_info["parameters"].items():
            if param_name not in endpoint_info["required"]:
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                if "| None" not in modern_type:
                    modern_type = f"{modern_type} | None"
                optional_params.append(f"{sanitized_name}: {modern_type} = None")
                if "bool" in param_info.get("type", ""):
                    has_any_bool = True

        # Build signature: non-bool required first, then * if needed, then bool required + optional
        params.extend(required_non_bool)
        if has_any_bool and (required_bool or optional_params):
            params.append("*")
        params.extend(required_bool)
        params.extend(optional_params)

        signature_params = ",\n        ".join(params)
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> WordPressResponse:"

    def _generate_method_docstring(self, endpoint_info: Dict) -> List[str]:
        """Generate method docstring."""
        lines = [f'        """{endpoint_info["description"]}', ""]

        if endpoint_info["parameters"]:
            lines.append("        Args:")
            for param_name, param_info in endpoint_info["parameters"].items():
                sanitized_name = self._sanitize_parameter_name(param_name)
                lines.append(
                    f"            {sanitized_name}: {param_info['description']}"
                )
            lines.append("")

        lines.extend([
            "        Returns:",
            "            WordPressResponse with operation result",
            '        """',
        ])

        return lines

    def _generate_method(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate a complete method for an API endpoint."""
        lines = []

        # Method signature
        lines.append(self._generate_method_signature(method_name, endpoint_info))

        # Docstring
        lines.extend(self._generate_method_docstring(endpoint_info))

        # Query parameters
        has_query = any(
            info["location"] == "query"
            for info in endpoint_info["parameters"].values()
        )
        if has_query:
            query_lines = self._build_query_params(endpoint_info)
            lines.extend(query_lines)
            lines.append("")

        # URL construction
        lines.append(self._build_path_formatting(endpoint_info["path"], endpoint_info))

        # Request body
        body_lines = self._build_request_body(endpoint_info)
        if body_lines:
            lines.append("")
            lines.extend(body_lines)

        # Request construction and execution
        lines.append("")
        lines.append("        try:")
        lines.append("            request = HTTPRequest(")
        lines.append(f'                method="{endpoint_info["method"]}",')
        lines.append("                url=url,")
        lines.append('                headers={"Content-Type": "application/json"},')
        if has_query:
            lines.append("                query=query_params,")
        if body_lines:
            lines.append("                body=body,")
        lines.append("            )")
        lines.extend([
            "            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]",
            "            response_data = response.json() if response.text() else None",
            "            return WordPressResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return WordPressResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_wordpress_datasource(self) -> str:
        """Generate the complete WordPress datasource class."""

        class_lines = [
            '"""',
            "WordPress REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from WordPress REST API v2 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.http.http_request import HTTPRequest",
            "from app.sources.client.wordpress.wordpress import WordPressClient, WordPressResponse",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class WordPressDataSource:",
            '    """WordPress REST API DataSource',
            "",
            "    Provides async wrapper methods for WordPress REST API v2 operations:",
            "    - Posts CRUD",
            "    - Pages CRUD",
            "    - Categories and Tags",
            "    - Comments",
            "    - Users",
            "    - Media",
            "    - Post Types, Statuses, Taxonomies",
            "    - Search",
            "",
            "    The base URL is determined by the WordPressClient's configured",
            "    authentication method (WordPress.com OAuth or self-hosted).",
            "",
            "    All methods return WordPressResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: WordPressClient) -> None:",
            '        """Initialize with WordPressClient.',
            "",
            "        Args:",
            "            client: WordPressClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'WordPressDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> WordPressClient:",
            '        """Return the underlying WordPressClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in WORDPRESS_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the WordPress datasource to a file."""
        if filename is None:
            filename = "wordpress.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        wordpress_dir = script_dir.parent / "app" / "sources" / "external" / "wordpress"
        wordpress_dir.mkdir(parents=True, exist_ok=True)

        full_path = wordpress_dir / filename

        class_code = self.generate_wordpress_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated WordPress data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print resource summary
        resource_categories = {
            "Post": 0,
            "Page": 0,
            "Category": 0,
            "Tag": 0,
            "Comment": 0,
            "User": 0,
            "Media": 0,
            "Post Type": 0,
            "Post Status": 0,
            "Taxonomy": 0,
            "Search": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "post" in name and "type" not in name and "status" not in name:
                resource_categories["Post"] += 1
            elif "page" in name:
                resource_categories["Page"] += 1
            elif "categor" in name:
                resource_categories["Category"] += 1
            elif "tag" in name:
                resource_categories["Tag"] += 1
            elif "comment" in name:
                resource_categories["Comment"] += 1
            elif "user" in name:
                resource_categories["User"] += 1
            elif "media" in name:
                resource_categories["Media"] += 1
            elif "type" in name:
                resource_categories["Post Type"] += 1
            elif "status" in name:
                resource_categories["Post Status"] += 1
            elif "taxonom" in name:
                resource_categories["Taxonomy"] += 1
            elif "search" in name:
                resource_categories["Search"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for WordPress data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate WordPress REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = WordPressDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate WordPress data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
