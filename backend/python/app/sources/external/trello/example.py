import os

from app.sources.client.trello.trello import TrelloClient
from app.sources.external.trello.trello import TrelloDataSource


def main() -> None:
    # Read Trello credentials from environment variables
    api_key = os.getenv("TRELLO_API_KEY")
    token = os.getenv("TRELLO_TOKEN")

    client = TrelloClient(api_key=api_key, token=token)
    datasource = TrelloDataSource(client)

    print("Fetching boards...")
    boards = datasource.list_boards()
    print("Boards:", boards)

    if boards:
        first_board = boards[0]
        board_id = first_board["id"]
        print(f"\nFetching lists on board: {first_board['name']}")
        lists_ = datasource.list_lists(board_id)
        print("Lists:", lists_)

        if lists_:
            first_list = lists_[0]
            list_id = first_list["id"]
            print(f"\nFetching cards for list: {first_list['name']}")
            cards = datasource.list_cards(list_id)
            print("Cards:", cards)


if __name__ == "__main__":
    main()
