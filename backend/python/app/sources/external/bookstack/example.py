# ruff: noqa
"""
BookStack API Usage Examples

This example demonstrates how to use the BookStack DataSource to interact with
the BookStack API, covering:
- User management (list, get user details)
- Books CRUD operations (create, read, update, delete)
- Pages CRUD operations
- Chapters CRUD operations
- Search functionality

Prerequisites:
- Set BOOKSTACK_TOKEN_ID environment variable
- Set BOOKSTACK_TOKEN_SECRET environment variable
- Set BOOKSTACK_BASE_URL environment variable (e.g., https://bookstack.example.com)
"""

import asyncio
import os
from typing import Optional

from app.sources.client.bookstack.bookstack import BookStackTokenConfig, BookStackClient
from app.sources.external.bookstack.bookstack import BookStackDataSource
from app.sources.external.dropbox.pretty_print import to_pretty_json

# Environment variables
TOKEN_ID = os.getenv("BOOKSTACK_TOKEN_ID")
TOKEN_SECRET = os.getenv("BOOKSTACK_TOKEN_SECRET")
BASE_URL = os.getenv("BOOKSTACK_BASE_URL")  # e.g., https://bookstack.example.com


async def main() -> None:
    """Simple example of using BookStackDataSource to call the API."""
    # Configure and build the BookStack client
    config = BookStackTokenConfig(
        base_url=BASE_URL,
        token_id=TOKEN_ID,
        token_secret=TOKEN_SECRET
    )
    client = BookStackClient.build_with_config(config)

    # Create the data source
    data_source = BookStackDataSource(client)

    
    # print("\nCreating a new book:")
    # create_response = await data_source.create_book(name="Test Book")
    # print(create_response)

    #List all users
    print("\nList users:")
    users = await data_source.list_users(filter={"email": "admin@admin.com"})
    print(users)

    # #List a particular user
    # print("\nList a particular user:")
    # user1 = await data_source.get_user(id=1)
    # user2 = await data_source.get_user(id=2)
    # print(user1)
    # print(user2)

    print("\nList roles:")
    roles = await data_source.list_roles()
    print(roles)

    print("\nGet role details")
    role = await data_source.get_role(role_id=2)
    print(role)

    # print("\nList shelves")
    # shelves = await data_source.list_shelves()
    # print(shelves)

    # print("\nListing all books:")
    # books = await data_source.list_books()
    # print(books)

    # print("\nList all chapters")
    # chapters = await data_source.list_chapters()
    # print(chapters)

    # print("\nList Pages")
    # pages = await data_source.list_pages()
    # print(pages)

    print("\nList Permissions")
    permissions = await data_source.get_content_permissions(content_type="chapter", content_id=3)
    print(permissions)

    print("\nAudit log")
    audit_log = await data_source.list_audit_log(
        filter={
            'type': 'page_create',
            'created_at:gte': '2025-10-16T06:00:00Z'
        }
    )
    print(audit_log)

    # print("\nExport Page Markdown")
    # markdown = await data_source.export_page_markdown(1)
    # print(markdown)


if __name__ == "__main__":
    asyncio.run(main())