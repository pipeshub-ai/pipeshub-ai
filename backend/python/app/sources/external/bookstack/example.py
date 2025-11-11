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
    try:
        client = await BookStackClient.build_and_validate(config)
    except ValueError as e:
        print(f"Error: Failed to initialize BookStack client.")
        print(f"Details: {e}")
        return # Exit the main function

    # Create the data source
    data_source = BookStackDataSource(client)

    
    # print("\nCreating a new book:")
    # create_response = await data_source.create_book(name="Test Book")
    # print(create_response)

    #List all users
    print("\nList users:")
    users = await data_source.list_users(filter={"email":"harshit@pipeshub.app"})
    print(users)

    # response = await data_source.list_users(
    #             count=50, 
    #             offset=0
    #         )
    # print(response)

    # #List a particular user
    print("\nList a particular user:")
    user1 = await data_source.get_user(user_id=1)
    # user2 = await data_source.get_user(id=2)
    print(user1)
    # print(user2)

    print("\nList roles:")
    roles = await data_source.list_roles()
    print(roles)

    # print("\nGet role details")
    # role = await data_source.get_role(role_id=7)
    # print(role)

    # print("\nGet role details 2")
    # role = await data_source.get_role(role_id=2)
    # print(role)

    # print("\nGet user details")
    # user = await data_source.get_user(user_id=6)
    # print(user)

    # print("\nList shelves")
    # shelves = await data_source.list_shelves()
    # print(shelves)

    # print("\n get bookshelf")
    # shelf = await data_source.get_shelf(shelf_id=3)
    # print(shelf)

    # print("\nListing all books:")
    # books = await data_source.list_books()
    # print(books)

    # print("\n get book")
    # book = await data_source.get_book(book_id=1)
    # print(book)

    
    # print("\nList all chapters")
    # chapters = await data_source.list_chapters()
    # print(chapters)

    # print("\n get chapter")
    # chapter = await data_source.get_chapter(chapter_id=1)
    # print(chapter)

    print("\nList Pages")
    pages = await data_source.list_pages()
    print(pages)

    # print("\nGet page")
    # page = await data_source.get_page(page_id=1)
    # print(page)

    print("\n List attachments")
    attachments = await data_source.list_attachments()
    print(attachments)

    # print("\nList Permissions")
    # permissions = await data_source.get_content_permissions(content_type="page", content_id=12)
    # print(permissions)

    # print("\nAudit log")
    # audit_log = await data_source.list_audit_log(
    #     filter={
    #         'type': 'permissions_update',
    #         'created_at:gte': '2025-10-22T14:00:00Z'
    #     }
    # )
    # print(audit_log)

    # if audit_log.success and audit_log.data and audit_log.data.get('data'):
    #     # 1. Access the first log entry in the list
    #     log_entry = audit_log.data['data'][0]

    #     # 2. Get the 'detail' string, which is '(5) Harshit'
    #     detail_string = log_entry['detail']

    #     # 3. Parse the string to get the name and ID
    #     # We split by the first space to separate the ID part from the name part.
    #     try:
    #         id_part, user_name = detail_string.split(' ', 1)

    #         # Remove the parentheses from the ID part and convert it to an integer
    #         user_id = int(id_part.strip('()'))

    #         print(f"The user created is: {user_name}")
    #         print(f"The user ID from detail is: {user_id}")

    #     except (IndexError, ValueError):
    #         print(f"Could not parse the user name and ID from detail: '{detail_string}'")

    # else:
    #     print("No 'user_create' logs found in the response or the request failed.")

    # print("\nExport Page Markdown")
    # markdown = await data_source.export_page_markdown(1)
    # print(markdown)


if __name__ == "__main__":
    asyncio.run(main())