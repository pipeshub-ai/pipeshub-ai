# example.py
from app.sources.external.monday.monday import MondayDataSource

if __name__ == "__main__":
    # 🔑 Replace this with your Monday.com API key
    api_key = "eyJhbGciOiJIUzI1NiJ9.eyJ0aWQiOjU4MjYwNDIxOSwiYWFpIjoxMSwidWlkIjo5NTU4MDY1NiwiaWFkIjoiMjAyNS0xMS0wNVQwNzowMDoyOS42ODhaIiwicGVyIjoibWU6d3JpdGUiLCJhY3RpZCI6MzIzMzk3MzcsInJnbiI6ImFwc2UyIn0.xf2rx9TclzKhPcvWu0zMkc771yqnpw5ENg7HwXxkFxA"

    # Initialize data source
    monday_source = MondayDataSource(api_key)

    # Fetch and print boards
    boards = monday_source.fetch_all_boards()
    print("Boards:", boards)

    # Example: fetch items from first board if exists
    try:
        board_id = boards["data"]["boards"][0]["id"]
        items = monday_source.fetch_items_from_board(board_id)
        print("Items from first board:", items)
    except Exception as e:
        print("Error fetching items:", e)
