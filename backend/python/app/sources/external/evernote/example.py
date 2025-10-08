# ruff: noqa
"""
Evernote API Usage Example - List Notebooks
============================================

This example demonstrates how to use the Evernote DataSource to list all notebooks
using the Thrift-based client.
"""

import asyncio
import os

from app.sources.client.evernote.evernote import EvernoteClient, EvernoteTokenConfig
from app.sources.external.evernote.evernote import EvernoteDataSource

token = os.getenv("EVERNOTE_TOKEN")
note_store_url = os.getenv("EVERNOTE_NOTE_STORE_URL")
sandbox = os.getenv("EVERNOTE_SANDBOX", "False").lower() == "true"

if token is None or note_store_url is None:
    raise ValueError("EVERNOTE_TOKEN, EVERNOTE_NOTE_STORE_URL")

async def list_notebooks_example():
    config = EvernoteTokenConfig(
        token=token,
        note_store_url=note_store_url,
        sandbox=sandbox
    )

    evernote_client = EvernoteClient.build_with_config(config)
    datasource = EvernoteDataSource(evernote_client)
    auth_token = evernote_client.get_token()

    print("Fetching notebooks...")
    response = await datasource.list_notebooks(authentication_token=auth_token)
    if response.success:
        notebooks = response.data
        print(f"\nSuccessfully retrieved {len(notebooks)} notebook(s):\n")

        for notebook in notebooks:
            print(f"  📓 {notebook.get('name', 'Unnamed')}")
            print(f"     GUID: {notebook.get('guid', 'N/A')}")
            print(f"     Created: {notebook.get('created', 'N/A')}")
            print(f"     Updated: {notebook.get('updated', 'N/A')}")

            if notebook.get('defaultNotebook'):
                print("Default Notebook")

            if notebook.get('stack'):
                print(f"Stack: {notebook.get('stack')}")

            print()
    else:
        print(f"\nError: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main():
    """Main entry point"""
    try:
        await list_notebooks_example()
    except Exception as e:
        print(f"\nException occurred: {type(e).__name__}: {str(e)}")


if __name__ == "__main__":
    asyncio.run(main())

