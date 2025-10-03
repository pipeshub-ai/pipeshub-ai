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

    # List all books
    print("Listing all books:")
    books = await data_source.list_books()
    print(books)
    
    print("Creating a new book:")
    create_response = await data_source.create_book(name="Test Book")
    print(create_response)

if __name__ == "__main__":
    asyncio.run(main())