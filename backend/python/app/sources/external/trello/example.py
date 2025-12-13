"""
Example usage of TrelloClient and TrelloDataSource

This demonstrates how to:
1. Create a Trello client with API key and token authentication
2. Initialize the datasource with the client
3. Make API calls using the datasource methods
"""

import asyncio
import json
import os

from app.sources.client.trello.trello import (
    TrelloApiKeyConfig,
    TrelloClient,
    TrelloResponse,
)
from app.sources.external.trello.trello import TrelloDataSource


def _print_response(title: str, response: TrelloResponse, max_items: int = 10) -> None:
    """Print a TrelloResponse in a simple format."""
    print(title)

    if not response.success:
        print(f"Error: {response.error}")
        return

    data = response.data
    if not data:
        print("(no data)")
        return

    # Handle list of items
    if isinstance(data, list):
        print(f"OK Found {len(data)} items:")
        for i, item in enumerate(data[:max_items], 1):
            print(f"  {i}. {json.dumps(item, indent=4, default=str)}")
        if len(data) > max_items:
            print(f"  ... and {len(data) - max_items} more items")
        return

    # Handle single object
    print("OK Result:")
    print(json.dumps(data, indent=2, default=str))


async def example_with_api_key() -> None:
    """Example using API Key and Token authentication"""

    # Get credentials from environment
    api_key = os.getenv("TRELLO_API_KEY")
    api_token = os.getenv("TRELLO_API_TOKEN")

    if not api_key:
        raise ValueError("TRELLO_API_KEY is not set")
    if not api_token:
        raise ValueError("TRELLO_API_TOKEN is not set")

    # Create Trello client with API key config
    trello_client = TrelloClient.build_with_config(
        TrelloApiKeyConfig(
            api_key=api_key,
            api_token=api_token,
        ),
    )

    print(f"OK Created Trello client: {trello_client}")

    # Create datasource with the client
    trello_datasource = TrelloDataSource(trello_client)
    print(f"OK Created Trello datasource: {trello_datasource}")

    # Example 1: Get authenticated member (current user)
    print("\n--- Getting authenticated member ---")
    user_response = await trello_datasource.get_authenticated_member()
    _print_response("Current user:", user_response)

    # Example 2: List all boards
    print("\n--- Listing all boards ---")
    boards_response = await trello_datasource.list_boards(board_filter="open")
    _print_response("Boards:", boards_response)

    # Get first board ID for further examples
    board_id = None
    if (
        boards_response.success
        and boards_response.data
        and len(boards_response.data) > 0
    ):
        board_id = boards_response.data[0].get("id")
        print(f"\nOK Using board ID for examples: {board_id}")

    if board_id:
        # Example 3: Get board details
        print(f"\n--- Getting board details for {board_id} ---")
        board_response = await trello_datasource.get_board(board_id)
        _print_response("Board details:", board_response)

        # Example 4: Get lists on the board
        print(f"\n--- Getting lists for board {board_id} ---")
        lists_response = await trello_datasource.get_board_lists(board_id)
        _print_response("Board lists:", lists_response)

        # Get first list ID for card examples
        list_id = None
        if (
            lists_response.success
            and lists_response.data
            and len(lists_response.data) > 0
        ):
            list_id = lists_response.data[0].get("id")
            print(f"\nOK Using list ID for examples: {list_id}")

        if list_id:
            # Example 5: Get cards in the list
            print(f"\n--- Getting cards in list {list_id} ---")
            cards_response = await trello_datasource.get_list_cards(list_id)
            _print_response("List cards:", cards_response)

            # Example 6: Create a new card
            print(f"\n--- Creating a new card in list {list_id} ---")
            create_response = await trello_datasource.create_card(
                list_id=list_id,
                name="Test card from API",
                desc="This card was created via the Trello API using py-trello",
            )
            _print_response("Created card:", create_response)

            # Get the created card ID
            if create_response.success and create_response.data:
                card_id = create_response.data.get("id")

                # Example 7: Update the card
                print(f"\n--- Updating card {card_id} ---")
                update_response = await trello_datasource.update_card(
                    card_id=card_id,
                    name="Updated test card",
                    desc="This card was updated via the API",
                )
                _print_response("Updated card:", update_response)

                # Example 8: Get card details
                print(f"\n--- Getting card details for {card_id} ---")
                card_response = await trello_datasource.get_card(card_id)
                _print_response("Card details:", card_response)

                # Example 9: Add a comment to the card
                print(f"\n--- Adding comment to card {card_id} ---")
                comment_response = await trello_datasource.add_card_comment(
                    card_id=card_id, text="This is a test comment added via the API"
                )
                _print_response("Comment added:", comment_response)

                # Example 10: Get card comments
                print(f"\n--- Getting comments for card {card_id} ---")
                comments_response = await trello_datasource.get_card_comments(card_id)
                _print_response("Card comments:", comments_response)

        # Example 11: Get board members
        print(f"\n--- Getting board members for {board_id} ---")
        members_response = await trello_datasource.get_board_members(board_id)
        _print_response("Board members:", members_response)

        # Example 12: Get board cards
        print(f"\n--- Getting all cards on board {board_id} ---")
        all_cards_response = await trello_datasource.get_board_cards(board_id)
        _print_response("All board cards:", all_cards_response, max_items=5)

    # Example 13: List member boards
    print("\n--- Listing member boards ---")
    member_boards_response = await trello_datasource.list_member_boards("me", "open")
    _print_response("Member boards:", member_boards_response)

    # Example 14: Get organizations
    print("\n--- Listing member organizations ---")
    orgs_response = await trello_datasource.list_member_organizations("me")
    _print_response("Member organizations:", orgs_response)


def main() -> None:
    """Main entry point"""
    print("=" * 70)
    print("Trello API Client Examples")
    print("=" * 70)

    # Run API key authentication example
    print("\nExample: API Key & Token Authentication")
    print("-" * 70)
    asyncio.run(example_with_api_key())

    print("\n" + "=" * 70)
    print("OK Examples completed")
    print("=" * 70)


if __name__ == "__main__":
    main()
