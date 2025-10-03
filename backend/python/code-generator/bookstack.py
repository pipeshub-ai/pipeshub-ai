# ruff: noqa
"""
BookStack API DataSource Generator

This script generates a comprehensive BookStack datasource class that covers ALL
BookStack API endpoints including:
- Attachments (CRUD operations)
- Books (CRUD, exports in multiple formats)
- Chapters (CRUD, exports in multiple formats)
- Pages (CRUD, exports in multiple formats)
- Image Gallery (CRUD operations)
- Search (unified search across all content)
- Shelves (CRUD operations)
- Users (CRUD operations, invitation support)
- Roles (CRUD operations, permissions management)
- Recycle Bin (list, restore, destroy)
- Content Permissions (read, update)
- Audit Log (list)

The generated class accepts a BookStackClient and uses explicit type hints
for all parameters (no Any types allowed).
"""

import argparse
import keyword
from pathlib import Path
from typing import Dict

# ============================================================================
# BOOKSTACK API ENDPOINT DEFINITIONS
# ============================================================================

BOOKSTACK_API_ENDPOINTS = {
    # ========================================================================
    # ATTACHMENTS API
    # ========================================================================
    'list_attachments': {
        'method': 'GET',
        'path': '/api/attachments',
        'description': 'Get a listing of attachments visible to the user',
        'parameters': {
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return (max 500)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to skip'},
            'sort': {'type': 'Optional[str]', 'location': 'query', 'description': 'Field to sort by with +/- prefix'},
            'filter': {'type': 'Optional[Dict[str, str]]', 'location': 'query', 'description': 'Filters to apply'}
        },
        'required': []
    },

    'create_attachment': {
        'method': 'POST',
        'path': '/api/attachments',
        'description': 'Create a new attachment. Use multipart/form-data for file uploads',
        'parameters': {
            'name': {'type': 'str', 'location': 'body', 'description': 'Attachment name'},
            'uploaded_to': {'type': 'int', 'location': 'body', 'description': 'ID of the page to attach to'},
            'file': {'type': 'Optional[bytes]', 'location': 'file', 'description': 'File data for file attachments'},
            'link': {'type': 'Optional[str]', 'location': 'body', 'description': 'URL for link attachments'}
        },
        'required': ['name', 'uploaded_to']
    },

    'get_attachment': {
        'method': 'GET',
        'path': '/api/attachments/{id}',
        'description': 'Get details of a single attachment including content',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Attachment ID'}
        },
        'required': ['id']
    },

    'update_attachment': {
        'method': 'PUT',
        'path': '/api/attachments/{id}',
        'description': 'Update an attachment. Use multipart/form-data for file updates',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Attachment ID'},
            'name': {'type': 'Optional[str]', 'location': 'body', 'description': 'Attachment name'},
            'uploaded_to': {'type': 'Optional[int]', 'location': 'body', 'description': 'ID of the page to attach to'},
            'file': {'type': 'Optional[bytes]', 'location': 'file', 'description': 'File data for file attachments'},
            'link': {'type': 'Optional[str]', 'location': 'body', 'description': 'URL for link attachments'}
        },
        'required': ['id']
    },

    'delete_attachment': {
        'method': 'DELETE',
        'path': '/api/attachments/{id}',
        'description': 'Delete an attachment',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Attachment ID'}
        },
        'required': ['id']
    },

    # ========================================================================
    # BOOKS API
    # ========================================================================
    'list_books': {
        'method': 'GET',
        'path': '/api/books',
        'description': 'Get a listing of books visible to the user',
        'parameters': {
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return (max 500)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to skip'},
            'sort': {'type': 'Optional[str]', 'location': 'query', 'description': 'Field to sort by with +/- prefix'},
            'filter': {'type': 'Optional[Dict[str, str]]', 'location': 'query', 'description': 'Filters to apply'}
        },
        'required': []
    },

    'create_book': {
        'method': 'POST',
        'path': '/api/books',
        'description': 'Create a new book. Use multipart/form-data for image upload',
        'parameters': {
            'name': {'type': 'str', 'location': 'body', 'description': 'Book name'},
            'description': {'type': 'Optional[str]', 'location': 'body', 'description': 'Plain text description'},
            'description_html': {'type': 'Optional[str]', 'location': 'body', 'description': 'HTML description'},
            'tags': {'type': 'Optional[List[Dict[str, str]]]', 'location': 'body', 'description': 'Tags array'},
            'image': {'type': 'Optional[bytes]', 'location': 'file', 'description': 'Cover image file'},
            'default_template_id': {'type': 'Optional[int]', 'location': 'body', 'description': 'Default template page ID'}
        },
        'required': ['name']
    },

    'get_book': {
        'method': 'GET',
        'path': '/api/books/{id}',
        'description': 'Get details of a single book including contents',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Book ID'}
        },
        'required': ['id']
    },

    'update_book': {
        'method': 'PUT',
        'path': '/api/books/{id}',
        'description': 'Update a book. Use multipart/form-data for image upload',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Book ID'},
            'name': {'type': 'Optional[str]', 'location': 'body', 'description': 'Book name'},
            'description': {'type': 'Optional[str]', 'location': 'body', 'description': 'Plain text description'},
            'description_html': {'type': 'Optional[str]', 'location': 'body', 'description': 'HTML description'},
            'tags': {'type': 'Optional[List[Dict[str, str]]]', 'location': 'body', 'description': 'Tags array'},
            'image': {'type': 'Optional[bytes]', 'location': 'file', 'description': 'Cover image file'},
            'default_template_id': {'type': 'Optional[int]', 'location': 'body', 'description': 'Default template page ID'}
        },
        'required': ['id']
    },

    'delete_book': {
        'method': 'DELETE',
        'path': '/api/books/{id}',
        'description': 'Delete a book (typically sends to recycle bin)',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Book ID'}
        },
        'required': ['id']
    },

    'export_book_html': {
        'method': 'GET',
        'path': '/api/books/{id}/export/html',
        'description': 'Export a book as a contained HTML file',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Book ID'}
        },
        'required': ['id']
    },

    'export_book_pdf': {
        'method': 'GET',
        'path': '/api/books/{id}/export/pdf',
        'description': 'Export a book as a PDF file',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Book ID'}
        },
        'required': ['id']
    },

    'export_book_plaintext': {
        'method': 'GET',
        'path': '/api/books/{id}/export/plaintext',
        'description': 'Export a book as a plain text file',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Book ID'}
        },
        'required': ['id']
    },

    'export_book_markdown': {
        'method': 'GET',
        'path': '/api/books/{id}/export/markdown',
        'description': 'Export a book as a markdown file',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Book ID'}
        },
        'required': ['id']
    },

    # ========================================================================
    # CHAPTERS API
    # ========================================================================
    'list_chapters': {
        'method': 'GET',
        'path': '/api/chapters',
        'description': 'Get a listing of chapters visible to the user',
        'parameters': {
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return (max 500)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to skip'},
            'sort': {'type': 'Optional[str]', 'location': 'query', 'description': 'Field to sort by with +/- prefix'},
            'filter': {'type': 'Optional[Dict[str, str]]', 'location': 'query', 'description': 'Filters to apply'}
        },
        'required': []
    },

    'create_chapter': {
        'method': 'POST',
        'path': '/api/chapters',
        'description': 'Create a new chapter in a book',
        'parameters': {
            'book_id': {'type': 'int', 'location': 'body', 'description': 'Parent book ID'},
            'name': {'type': 'str', 'location': 'body', 'description': 'Chapter name'},
            'description': {'type': 'Optional[str]', 'location': 'body', 'description': 'Plain text description'},
            'description_html': {'type': 'Optional[str]', 'location': 'body', 'description': 'HTML description'},
            'tags': {'type': 'Optional[List[Dict[str, str]]]', 'location': 'body', 'description': 'Tags array'},
            'priority': {'type': 'Optional[int]', 'location': 'body', 'description': 'Chapter priority/order'},
            'default_template_id': {'type': 'Optional[int]', 'location': 'body', 'description': 'Default template page ID'}
        },
        'required': ['book_id', 'name']
    },

    'get_chapter': {
        'method': 'GET',
        'path': '/api/chapters/{id}',
        'description': 'Get details of a single chapter including pages',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Chapter ID'}
        },
        'required': ['id']
    },

    'update_chapter': {
        'method': 'PUT',
        'path': '/api/chapters/{id}',
        'description': 'Update a chapter',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Chapter ID'},
            'book_id': {'type': 'Optional[int]', 'location': 'body', 'description': 'Parent book ID (to move chapter)'},
            'name': {'type': 'Optional[str]', 'location': 'body', 'description': 'Chapter name'},
            'description': {'type': 'Optional[str]', 'location': 'body', 'description': 'Plain text description'},
            'description_html': {'type': 'Optional[str]', 'location': 'body', 'description': 'HTML description'},
            'tags': {'type': 'Optional[List[Dict[str, str]]]', 'location': 'body', 'description': 'Tags array'},
            'priority': {'type': 'Optional[int]', 'location': 'body', 'description': 'Chapter priority/order'},
            'default_template_id': {'type': 'Optional[int]', 'location': 'body', 'description': 'Default template page ID'}
        },
        'required': ['id']
    },

    'delete_chapter': {
        'method': 'DELETE',
        'path': '/api/chapters/{id}',
        'description': 'Delete a chapter (typically sends to recycle bin)',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Chapter ID'}
        },
        'required': ['id']
    },

    'export_chapter_html': {
        'method': 'GET',
        'path': '/api/chapters/{id}/export/html',
        'description': 'Export a chapter as a contained HTML file',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Chapter ID'}
        },
        'required': ['id']
    },

    'export_chapter_pdf': {
        'method': 'GET',
        'path': '/api/chapters/{id}/export/pdf',
        'description': 'Export a chapter as a PDF file',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Chapter ID'}
        },
        'required': ['id']
    },

    'export_chapter_plaintext': {
        'method': 'GET',
        'path': '/api/chapters/{id}/export/plaintext',
        'description': 'Export a chapter as a plain text file',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Chapter ID'}
        },
        'required': ['id']
    },

    'export_chapter_markdown': {
        'method': 'GET',
        'path': '/api/chapters/{id}/export/markdown',
        'description': 'Export a chapter as a markdown file',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Chapter ID'}
        },
        'required': ['id']
    },

    # ========================================================================
    # PAGES API
    # ========================================================================
    'list_pages': {
        'method': 'GET',
        'path': '/api/pages',
        'description': 'Get a listing of pages visible to the user',
        'parameters': {
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return (max 500)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to skip'},
            'sort': {'type': 'Optional[str]', 'location': 'query', 'description': 'Field to sort by with +/- prefix'},
            'filter': {'type': 'Optional[Dict[str, str]]', 'location': 'query', 'description': 'Filters to apply'}
        },
        'required': []
    },

    'create_page': {
        'method': 'POST',
        'path': '/api/pages',
        'description': 'Create a new page in a book or chapter',
        'parameters': {
            'book_id': {'type': 'Optional[int]', 'location': 'body', 'description': 'Parent book ID (required without chapter_id)'},
            'chapter_id': {'type': 'Optional[int]', 'location': 'body', 'description': 'Parent chapter ID (required without book_id)'},
            'name': {'type': 'str', 'location': 'body', 'description': 'Page name'},
            'html': {'type': 'Optional[str]', 'location': 'body', 'description': 'HTML content (required without markdown)'},
            'markdown': {'type': 'Optional[str]', 'location': 'body', 'description': 'Markdown content (required without html)'},
            'tags': {'type': 'Optional[List[Dict[str, str]]]', 'location': 'body', 'description': 'Tags array'},
            'priority': {'type': 'Optional[int]', 'location': 'body', 'description': 'Page priority/order'}
        },
        'required': ['name']
    },

    'get_page': {
        'method': 'GET',
        'path': '/api/pages/{id}',
        'description': 'Get details of a single page including content',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Page ID'}
        },
        'required': ['id']
    },

    'update_page': {
        'method': 'PUT',
        'path': '/api/pages/{id}',
        'description': 'Update a page',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Page ID'},
            'book_id': {'type': 'Optional[int]', 'location': 'body', 'description': 'Parent book ID (to move page)'},
            'chapter_id': {'type': 'Optional[int]', 'location': 'body', 'description': 'Parent chapter ID (to move page)'},
            'name': {'type': 'Optional[str]', 'location': 'body', 'description': 'Page name'},
            'html': {'type': 'Optional[str]', 'location': 'body', 'description': 'HTML content'},
            'markdown': {'type': 'Optional[str]', 'location': 'body', 'description': 'Markdown content'},
            'tags': {'type': 'Optional[List[Dict[str, str]]]', 'location': 'body', 'description': 'Tags array'},
            'priority': {'type': 'Optional[int]', 'location': 'body', 'description': 'Page priority/order'}
        },
        'required': ['id']
    },

    'delete_page': {
        'method': 'DELETE',
        'path': '/api/pages/{id}',
        'description': 'Delete a page (typically sends to recycle bin)',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Page ID'}
        },
        'required': ['id']
    },

    'export_page_html': {
        'method': 'GET',
        'path': '/api/pages/{id}/export/html',
        'description': 'Export a page as a contained HTML file',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Page ID'}
        },
        'required': ['id']
    },

    'export_page_pdf': {
        'method': 'GET',
        'path': '/api/pages/{id}/export/pdf',
        'description': 'Export a page as a PDF file',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Page ID'}
        },
        'required': ['id']
    },

    'export_page_plaintext': {
        'method': 'GET',
        'path': '/api/pages/{id}/export/plaintext',
        'description': 'Export a page as a plain text file',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Page ID'}
        },
        'required': ['id']
    },

    'export_page_markdown': {
        'method': 'GET',
        'path': '/api/pages/{id}/export/markdown',
        'description': 'Export a page as a markdown file',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Page ID'}
        },
        'required': ['id']
    },

    # ========================================================================
    # IMAGE GALLERY API
    # ========================================================================
    'list_images': {
        'method': 'GET',
        'path': '/api/image-gallery',
        'description': 'Get a listing of images in the system',
        'parameters': {
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return (max 500)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to skip'},
            'sort': {'type': 'Optional[str]', 'location': 'query', 'description': 'Field to sort by with +/- prefix'},
            'filter': {'type': 'Optional[Dict[str, str]]', 'location': 'query', 'description': 'Filters to apply'}
        },
        'required': []
    },

    'create_image': {
        'method': 'POST',
        'path': '/api/image-gallery',
        'description': 'Create a new image. Must use multipart/form-data',
        'parameters': {
            'type': {'type': 'str', 'location': 'body', 'description': 'Image type: gallery or drawio'},
            'uploaded_to': {'type': 'int', 'location': 'body', 'description': 'ID of the page to attach to'},
            'image': {'type': 'bytes', 'location': 'file', 'description': 'Image file data'},
            'name': {'type': 'Optional[str]', 'location': 'body', 'description': 'Image name (defaults to filename)'}
        },
        'required': ['type', 'uploaded_to', 'image']
    },

    'get_image': {
        'method': 'GET',
        'path': '/api/image-gallery/{id}',
        'description': 'Get details of a single image',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Image ID'}
        },
        'required': ['id']
    },

    'update_image': {
        'method': 'PUT',
        'path': '/api/image-gallery/{id}',
        'description': 'Update image details or file. Use multipart/form-data for file updates',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Image ID'},
            'name': {'type': 'Optional[str]', 'location': 'body', 'description': 'Image name'},
            'image': {'type': 'Optional[bytes]', 'location': 'file', 'description': 'New image file data'}
        },
        'required': ['id']
    },

    'delete_image': {
        'method': 'DELETE',
        'path': '/api/image-gallery/{id}',
        'description': 'Delete an image from the system',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Image ID'}
        },
        'required': ['id']
    },

    # ========================================================================
    # SEARCH API
    # ========================================================================
    'search_all': {
        'method': 'GET',
        'path': '/api/search',
        'description': 'Search across all content types (shelves, books, chapters, pages)',
        'parameters': {
            'query': {'type': 'str', 'location': 'query', 'description': 'Search query string'},
            'page': {'type': 'Optional[int]', 'location': 'query', 'description': 'Page number for pagination'},
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of results per page (max 100)'}
        },
        'required': ['query']
    },

    # ========================================================================
    # SHELVES API
    # ========================================================================
    'list_shelves': {
        'method': 'GET',
        'path': '/api/shelves',
        'description': 'Get a listing of shelves visible to the user',
        'parameters': {
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return (max 500)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to skip'},
            'sort': {'type': 'Optional[str]', 'location': 'query', 'description': 'Field to sort by with +/- prefix'},
            'filter': {'type': 'Optional[Dict[str, str]]', 'location': 'query', 'description': 'Filters to apply'}
        },
        'required': []
    },

    'create_shelf': {
        'method': 'POST',
        'path': '/api/shelves',
        'description': 'Create a new shelf. Use multipart/form-data for image upload',
        'parameters': {
            'name': {'type': 'str', 'location': 'body', 'description': 'Shelf name'},
            'description': {'type': 'Optional[str]', 'location': 'body', 'description': 'Plain text description'},
            'description_html': {'type': 'Optional[str]', 'location': 'body', 'description': 'HTML description'},
            'books': {'type': 'Optional[List[int]]', 'location': 'body', 'description': 'Array of book IDs'},
            'tags': {'type': 'Optional[List[Dict[str, str]]]', 'location': 'body', 'description': 'Tags array'},
            'image': {'type': 'Optional[bytes]', 'location': 'file', 'description': 'Cover image file'}
        },
        'required': ['name']
    },

    'get_shelf': {
        'method': 'GET',
        'path': '/api/shelves/{id}',
        'description': 'Get details of a single shelf including books',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Shelf ID'}
        },
        'required': ['id']
    },

    'update_shelf': {
        'method': 'PUT',
        'path': '/api/shelves/{id}',
        'description': 'Update a shelf. Use multipart/form-data for image upload',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Shelf ID'},
            'name': {'type': 'Optional[str]', 'location': 'body', 'description': 'Shelf name'},
            'description': {'type': 'Optional[str]', 'location': 'body', 'description': 'Plain text description'},
            'description_html': {'type': 'Optional[str]', 'location': 'body', 'description': 'HTML description'},
            'books': {'type': 'Optional[List[int]]', 'location': 'body', 'description': 'Array of book IDs'},
            'tags': {'type': 'Optional[List[Dict[str, str]]]', 'location': 'body', 'description': 'Tags array'},
            'image': {'type': 'Optional[bytes]', 'location': 'file', 'description': 'Cover image file'}
        },
        'required': ['id']
    },

    'delete_shelf': {
        'method': 'DELETE',
        'path': '/api/shelves/{id}',
        'description': 'Delete a shelf (typically sends to recycle bin)',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Shelf ID'}
        },
        'required': ['id']
    },

    # ========================================================================
    # USERS API
    # ========================================================================
    'list_users': {
        'method': 'GET',
        'path': '/api/users',
        'description': 'Get a listing of users in the system. Requires permission to manage users',
        'parameters': {
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return (max 500)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to skip'},
            'sort': {'type': 'Optional[str]', 'location': 'query', 'description': 'Field to sort by with +/- prefix'},
            'filter': {'type': 'Optional[Dict[str, str]]', 'location': 'query', 'description': 'Filters to apply'}
        },
        'required': []
    },

    'create_user': {
        'method': 'POST',
        'path': '/api/users',
        'description': 'Create a new user. Requires permission to manage users',
        'parameters': {
            'name': {'type': 'str', 'location': 'body', 'description': 'User name'},
            'email': {'type': 'str', 'location': 'body', 'description': 'User email address'},
            'external_auth_id': {'type': 'Optional[str]', 'location': 'body', 'description': 'External authentication ID'},
            'language': {'type': 'Optional[str]', 'location': 'body', 'description': 'Language code (e.g., en, fr, de)'},
            'password': {'type': 'Optional[str]', 'location': 'body', 'description': 'User password (min 8 characters)'},
            'roles': {'type': 'Optional[List[int]]', 'location': 'body', 'description': 'Array of role IDs'},
            'send_invite': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Send invitation email to user'}
        },
        'required': ['name', 'email']
    },

    'get_user': {
        'method': 'GET',
        'path': '/api/users/{id}',
        'description': 'Get details of a single user. Requires permission to manage users',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'User ID'}
        },
        'required': ['id']
    },

    'update_user': {
        'method': 'PUT',
        'path': '/api/users/{id}',
        'description': 'Update a user. Requires permission to manage users',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'User ID'},
            'name': {'type': 'Optional[str]', 'location': 'body', 'description': 'User name'},
            'email': {'type': 'Optional[str]', 'location': 'body', 'description': 'User email address'},
            'external_auth_id': {'type': 'Optional[str]', 'location': 'body', 'description': 'External authentication ID'},
            'language': {'type': 'Optional[str]', 'location': 'body', 'description': 'Language code (e.g., en, fr, de)'},
            'password': {'type': 'Optional[str]', 'location': 'body', 'description': 'User password (min 8 characters)'},
            'roles': {'type': 'Optional[List[int]]', 'location': 'body', 'description': 'Array of role IDs'}
        },
        'required': ['id']
    },

    'delete_user': {
        'method': 'DELETE',
        'path': '/api/users/{id}',
        'description': 'Delete a user. Requires permission to manage users',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'User ID'},
            'migrate_ownership_id': {'type': 'Optional[int]', 'location': 'body', 'description': 'User ID to migrate content ownership to'}
        },
        'required': ['id']
    },

    # ========================================================================
    # ROLES API
    # ========================================================================
    'list_roles': {
        'method': 'GET',
        'path': '/api/roles',
        'description': 'Get a listing of roles in the system. Requires permission to manage roles',
        'parameters': {
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return (max 500)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to skip'},
            'sort': {'type': 'Optional[str]', 'location': 'query', 'description': 'Field to sort by with +/- prefix'},
            'filter': {'type': 'Optional[Dict[str, str]]', 'location': 'query', 'description': 'Filters to apply'}
        },
        'required': []
    },

    'create_role': {
        'method': 'POST',
        'path': '/api/roles',
        'description': 'Create a new role. Requires permission to manage roles',
        'parameters': {
            'display_name': {'type': 'str', 'location': 'body', 'description': 'Role display name'},
            'description': {'type': 'Optional[str]', 'location': 'body', 'description': 'Role description'},
            'mfa_enforced': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Enforce MFA for this role'},
            'external_auth_id': {'type': 'Optional[str]', 'location': 'body', 'description': 'External authentication ID'},
            'permissions': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'Array of permission names'}
        },
        'required': ['display_name']
    },

    'get_role': {
        'method': 'GET',
        'path': '/api/roles/{id}',
        'description': 'Get details of a single role including permissions. Requires permission to manage roles',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Role ID'}
        },
        'required': ['id']
    },

    'update_role': {
        'method': 'PUT',
        'path': '/api/roles/{id}',
        'description': 'Update a role. Requires permission to manage roles',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Role ID'},
            'display_name': {'type': 'Optional[str]', 'location': 'body', 'description': 'Role display name'},
            'description': {'type': 'Optional[str]', 'location': 'body', 'description': 'Role description'},
            'mfa_enforced': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Enforce MFA for this role'},
            'external_auth_id': {'type': 'Optional[str]', 'location': 'body', 'description': 'External authentication ID'},
            'permissions': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'Array of permission names'}
        },
        'required': ['id']
    },

    'delete_role': {
        'method': 'DELETE',
        'path': '/api/roles/{id}',
        'description': 'Delete a role. Requires permission to manage roles',
        'parameters': {
            'id': {'type': 'int', 'location': 'path', 'description': 'Role ID'}
        },
        'required': ['id']
    },

    # ========================================================================
    # RECYCLE BIN API
    # ========================================================================
    'list_recycle_bin': {
        'method': 'GET',
        'path': '/api/recycle-bin',
        'description': 'Get listing of items in recycle bin. Requires permission to manage settings and permissions',
        'parameters': {
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return (max 500)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to skip'},
            'sort': {'type': 'Optional[str]', 'location': 'query', 'description': 'Field to sort by with +/- prefix'},
            'filter': {'type': 'Optional[Dict[str, str]]', 'location': 'query', 'description': 'Filters to apply'}
        },
        'required': []
    },

    'restore_recycle_bin_item': {
        'method': 'PUT',
        'path': '/api/recycle-bin/{deletion_id}',
        'description': 'Restore an item from recycle bin. Requires permission to manage settings and permissions',
        'parameters': {
            'deletion_id': {'type': 'int', 'location': 'path', 'description': 'Deletion ID'}
        },
        'required': ['deletion_id']
    },

    'destroy_recycle_bin_item': {
        'method': 'DELETE',
        'path': '/api/recycle-bin/{deletion_id}',
        'description': 'Permanently delete item from recycle bin. Requires permission to manage settings and permissions',
        'parameters': {
            'deletion_id': {'type': 'int', 'location': 'path', 'description': 'Deletion ID'}
        },
        'required': ['deletion_id']
    },

    # ========================================================================
    # CONTENT PERMISSIONS API
    # ========================================================================
    'get_content_permissions': {
        'method': 'GET',
        'path': '/api/content-permissions/{content_type}/{content_id}',
        'description': 'Read content-level permissions for an item. Content types: page, book, chapter, bookshelf',
        'parameters': {
            'content_type': {'type': 'str', 'location': 'path', 'description': 'Content type: page, book, chapter, or bookshelf'},
            'content_id': {'type': 'int', 'location': 'path', 'description': 'Content item ID'}
        },
        'required': ['content_type', 'content_id']
    },

    'update_content_permissions': {
        'method': 'PUT',
        'path': '/api/content-permissions/{content_type}/{content_id}',
        'description': 'Update content-level permissions for an item. Content types: page, book, chapter, bookshelf',
        'parameters': {
            'content_type': {'type': 'str', 'location': 'path', 'description': 'Content type: page, book, chapter, or bookshelf'},
            'content_id': {'type': 'int', 'location': 'path', 'description': 'Content item ID'},
            'owner_id': {'type': 'Optional[int]', 'location': 'body', 'description': 'New owner user ID'},
            'role_permissions': {'type': 'Optional[List[Dict[str, Union[int, bool]]]]', 'location': 'body', 'description': 'Role permission overrides'},
            'fallback_permissions': {'type': 'Optional[Dict[str, Union[bool, None]]]', 'location': 'body', 'description': 'Fallback permissions configuration'}
        },
        'required': ['content_type', 'content_id']
    },

    # ========================================================================
    # AUDIT LOG API
    # ========================================================================
    'list_audit_log': {
        'method': 'GET',
        'path': '/api/audit-log',
        'description': 'Get listing of audit log events. Requires permission to manage users and settings',
        'parameters': {
            'count': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to return (max 500)'},
            'offset': {'type': 'Optional[int]', 'location': 'query', 'description': 'Number of records to skip'},
            'sort': {'type': 'Optional[str]', 'location': 'query', 'description': 'Field to sort by with +/- prefix'},
            'filter': {'type': 'Optional[Dict[str, str]]', 'location': 'query', 'description': 'Filters to apply'}
        },
        'required': []
    }
}

# ============================================================================
# CODE GENERATION UTILITIES
# ============================================================================

_PY_RESERVED = set(keyword.kwlist) | {"from", "global", "async", "await", "None", "self", "cls"}
_ALWAYS_RESERVED_NAMES = {"self", "headers", "body", "body_additional"}


def _sanitize_name(name: str) -> str:
    """Sanitize parameter names to be valid Python identifiers."""
    sanitized = name.replace('-', '_').replace('.', '_').replace('[]', '_array')

    if sanitized in _PY_RESERVED or sanitized in _ALWAYS_RESERVED_NAMES:
        sanitized = f"{sanitized}_param"

    if sanitized[0].isdigit():
        sanitized = f"param_{sanitized}"

    return sanitized


def _build_filter_params(filter_dict: Dict[str, str]) -> str:
    """Build filter parameters for query string."""
    lines = []
    for key, value in filter_dict.items():
        lines.append(f"            params['filter[{key}]'] = {value}")
    return '\n'.join(lines) if lines else ''


def _generate_method(method_name: str, endpoint_info: Dict) -> str:
    """Generate a single method for the BookStackDataSource class."""
    method = endpoint_info['method']
    path = endpoint_info['path']
    description = endpoint_info['description']
    parameters = endpoint_info.get('parameters', {})
    required = endpoint_info.get('required', [])

    # Separate parameters by location
    path_params = []
    query_params = []
    body_params = []
    file_params = []

    for param_name, param_info in parameters.items():
        location = param_info['location']
        param_type = param_info['type']

        sanitized_name = _sanitize_name(param_name)

        param_data = {
            'name': param_name,
            'sanitized': sanitized_name,
            'type': param_type,
            'description': param_info['description'],
            'required': param_name in required
        }

        if location == 'path':
            path_params.append(param_data)
        elif location == 'query':
            query_params.append(param_data)
        elif location == 'file':
            file_params.append(param_data)
        else:  # body
            body_params.append(param_data)

    # Build method signature
    sig_parts = ['self']

    # Add required parameters first
    for param in path_params:
        if param['required']:
            sig_parts.append(f"{param['sanitized']}: {param['type']}")

    for param in body_params:
        if param['required']:
            sig_parts.append(f"{param['sanitized']}: {param['type']}")

    # Add optional parameters
    for param in path_params:
        if not param['required']:
            sig_parts.append(f"{param['sanitized']}: {param['type']} = None")

    for param in query_params:
        sig_parts.append(f"{param['sanitized']}: {param['type']} = None")

    for param in body_params:
        if not param['required']:
            sig_parts.append(f"{param['sanitized']}: {param['type']} = None")

    for param in file_params:
        sig_parts.append(f"{param['sanitized']}: {param['type']} = None")

    signature = f"    async def {method_name}(\n        " + ",\n        ".join(sig_parts) + "\n    ) -> BookStackResponse:"

    # Build docstring
    docstring_lines = [f'        """{description}']

    if parameters:
        docstring_lines.append('')
        docstring_lines.append('        Args:')
        for param in path_params + query_params + body_params + file_params:
            req_str = ' (required)' if param['required'] else ''
            docstring_lines.append(f"            {param['sanitized']}: {param['description']}{req_str}")

    docstring_lines.append('')
    docstring_lines.append('        Returns:')
    docstring_lines.append('            BookStackResponse: Response object with success status and data/error')
    docstring_lines.append('        """')

    docstring = '\n'.join(docstring_lines)

    # Build method body
    body_lines = []

    # Build query parameters
    if query_params:
        body_lines.append('        params: Dict[str, Union[str, int]] = {}')
        for param in query_params:
            # Special handling for filter parameter (Dict type)
            if param['name'] == 'filter' and 'Dict' in param['type']:
                body_lines.append(f'        if {param["sanitized"]} is not None:')
                body_lines.append(f'            for key, value in {param["sanitized"]}.items():')
                body_lines.append("                params[f'filter[{key}]'] = value")
            else:
                body_lines.append(f'        if {param["sanitized"]} is not None:')
                body_lines.append(f'            params["{param["name"]}"] = {param["sanitized"]}')
    else:
        body_lines.append('        params: Dict[str, Union[str, int]] = {}')

    # Build request body
    has_body_params = len(body_params) > 0
    has_file_params = len(file_params) > 0

    if has_body_params or has_file_params:
        body_lines.append('')
        body_lines.append('        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}')

        for param in body_params:
            body_lines.append(f'        if {param["sanitized"]} is not None:')
            body_lines.append(f'            body["{param["name"]}"] = {param["sanitized"]}')

        # File params need special handling
        if has_file_params:
            body_lines.append('')
            body_lines.append('        files: Dict[str, bytes] = {}')
            for param in file_params:
                body_lines.append(f'        if {param["sanitized"]} is not None:')
                body_lines.append(f'            files["{param["name"]}"] = {param["sanitized"]}')

    # Build URL
    body_lines.append('')
    if path_params:
        # Format path with parameters
        format_args = ', '.join([f'{p["name"]}={p["sanitized"]}' for p in path_params])
        body_lines.append(f'        url = self.base_url + "{path}".format({format_args})')
    else:
        body_lines.append(f'        url = self.base_url + "{path}"')

    # Determine content type and body for request
    body_lines.append('')
    body_lines.append('        headers = dict(self.http.headers)')

    if has_file_params:
        body_lines.append('        # Note: multipart/form-data requests need special handling')
        body_lines.append('        # The HTTPRequest should handle multipart encoding when files are present')
    elif has_body_params and method in ['POST', 'PUT']:
        body_lines.append("        headers['Content-Type'] = 'application/json'")

    # Create request
    body_lines.append('')
    body_lines.append('        request = HTTPRequest(')
    body_lines.append(f'            method="{method}",')
    body_lines.append('            url=url,')
    body_lines.append('            headers=headers,')
    body_lines.append('            query_params=params,')

    if has_file_params:
        body_lines.append('            body=body,')
        body_lines.append('            files=files')
    elif has_body_params:
        body_lines.append('            body=body')
    else:
        body_lines.append('            body=None')

    body_lines.append('        )')

    # Execute request
    body_lines.append('')
    body_lines.append('        try:')
    body_lines.append('            response = await self.http.execute(request)')
    body_lines.append('            return BookStackResponse(success=True, data=response)')
    body_lines.append('        except Exception as e:')
    body_lines.append('            return BookStackResponse(success=False, error=str(e))')

    return signature + '\n' + docstring + '\n' + '\n'.join(body_lines)


def generate_bookstack_datasource() -> str:
    """Generate the complete BookStackDataSource class."""

    lines = [
        '"""',
        'BookStack API DataSource',
        '',
        'Auto-generated comprehensive BookStack API client wrapper.',
        'Covers all BookStack API endpoints with explicit type hints.',
        '',
        'Generated from BookStack API documentation at:',
        'https://bookstack.bassopaolo.com/api/docs',
        '"""',
        '',
        'from typing import Dict, List, Optional, Union',
        'from app.sources.client.http.http_request import HTTPRequest',
        'from app.sources.bookstack.bookstack import BookStackClient, BookStackResponse',
        '',
        '',
        'class BookStackDataSource:',
        '    """Comprehensive BookStack API client wrapper.',
        '    ',
        '    Provides async methods for ALL BookStack API endpoints:',
        '    ',
        '    CONTENT MANAGEMENT:',
        '    - Attachments (list, create, read, update, delete)',
        '    - Books (CRUD, exports: HTML, PDF, plaintext, markdown)',
        '    - Chapters (CRUD, exports: HTML, PDF, plaintext, markdown)',
        '    - Pages (CRUD, exports: HTML, PDF, plaintext, markdown)',
        '    - Image Gallery (CRUD operations)',
        '    - Shelves (CRUD operations)',
        '    ',
        '    USER & PERMISSIONS:',
        '    - Users (CRUD, invitation support)',
        '    - Roles (CRUD, permissions management)',
        '    - Content Permissions (granular access control)',
        '    ',
        '    SEARCH & DISCOVERY:',
        '    - Search (unified search across all content types)',
        '    ',
        '    SYSTEM MANAGEMENT:',
        '    - Recycle Bin (list, restore, permanently delete)',
        '    - Audit Log (system activity tracking)',
        '    ',
        '    All methods return BookStackResponse objects with standardized format.',
        '    Every parameter matches BookStack official API documentation exactly.',
        '    No Any types - all parameters are explicitly typed.',
        '    Supports multipart/form-data for file uploads.',
        '    """',
        '',
        '    def __init__(self, client: BookStackClient) -> None:',
        '        """Initialize with BookStackClient.',
        '        ',
        '        Args:',
        '            client: BookStackClient instance with authentication configured',
        '        """',
        '        self._client = client',
        '        self.http = client.get_client()',
        '        if self.http is None:',
        "            raise ValueError('HTTP client is not initialized')",
        '        try:',
        "            self.base_url = self.http.get_base_url().rstrip('/')",
        '        except AttributeError as exc:',
        "            raise ValueError('HTTP client does not have get_base_url method') from exc",
        '',
        "    def get_data_source(self) -> 'BookStackDataSource':",
        '        """Return the data source instance."""',
        '        return self',
        '',
    ]

    # Generate all API methods
    for method_name, endpoint_info in BOOKSTACK_API_ENDPOINTS.items():
        lines.append(_generate_method(method_name, endpoint_info))
        lines.append('')

    # Add utility method
    lines.extend([
        '    async def get_api_info(self) -> BookStackResponse:',
        '        """Get information about the BookStack API client.',
        '        ',
        '        Returns:',
        '            BookStackResponse: Information about available API methods',
        '        """',
        '        info = {',
        f"            'total_methods': {len(BOOKSTACK_API_ENDPOINTS)},",
        "            'base_url': self.base_url,",
        "            'api_categories': [",
        "                'Attachments (5 methods)',",
        "                'Books (9 methods - CRUD + 4 export formats)',",
        "                'Chapters (9 methods - CRUD + 4 export formats)',",
        "                'Pages (9 methods - CRUD + 4 export formats)',",
        "                'Image Gallery (5 methods)',",
        "                'Search (1 method)',",
        "                'Shelves (5 methods)',",
        "                'Users (5 methods with invitation support)',",
        "                'Roles (5 methods with permissions)',",
        "                'Recycle Bin (3 methods)',",
        "                'Content Permissions (2 methods)',",
        "                'Audit Log (1 method)'",
        "            ]",
        '        }',
        '        return BookStackResponse(success=True, data=info)',
    ])

    return '\n'.join(lines)


def main() -> None:
    """Generate and save the BookStack datasource."""
    parser = argparse.ArgumentParser(
        description='Generate comprehensive BookStack API DataSource'
    )
    parser.add_argument(
        '--out',
        default='bookstack/bookstack_data_source.py',
        help='Output path for the generated datasource'
    )
    parser.add_argument(
        '--print',
        dest='do_print',
        action='store_true',
        help='Print generated code to stdout'
    )

    args = parser.parse_args()

    print('🚀 Generating comprehensive BookStack API DataSource...')
    print(f'📊 Total endpoints: {len(BOOKSTACK_API_ENDPOINTS)}')

    code = generate_bookstack_datasource()

    # Create directory if needed
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to file
    output_path.write_text(code, encoding='utf-8')

    print('✅ Generated BookStackDataSource successfully!')
    print(f'📁 Saved to: {output_path}')
    print('\n📋 Summary:')
    print(f'   ✅ {len(BOOKSTACK_API_ENDPOINTS)} API methods')
    print('   ✅ All parameters explicitly typed (no Any)')
    print('   ✅ Comprehensive documentation')
    print('   ✅ Multipart/form-data support for file uploads')
    print('   ✅ Matches BookStack official API exactly')
    print('\n🎯 Coverage:')
    print('   • Attachments: 5 methods')
    print('   • Books: 9 methods (CRUD + 4 exports)')
    print('   • Chapters: 9 methods (CRUD + 4 exports)')
    print('   • Pages: 9 methods (CRUD + 4 exports)')
    print('   • Image Gallery: 5 methods')
    print('   • Search: 1 method')
    print('   • Shelves: 5 methods')
    print('   • Users: 5 methods (with invitations)')
    print('   • Roles: 5 methods (with permissions)')
    print('   • Recycle Bin: 3 methods')
    print('   • Content Permissions: 2 methods')
    print('   • Audit Log: 1 method')

    if args.do_print:
        print('\n' + '='*80)
        print('GENERATED CODE:')
        print('='*80 + '\n')
        print(code)


if __name__ == '__main__':
    main()
