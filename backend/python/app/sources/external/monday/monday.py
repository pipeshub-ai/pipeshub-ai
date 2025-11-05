# monday.py
from app.sources.client.monday.monday import MondayClient


class MondayDataSource:
    """High-level data source wrapper for Monday.com."""

    def __init__(self, api_key: str):
        # Create an instance of MondayClient using API key
        self.client = MondayClient(api_key)

    def fetch_all_boards(self):
        """Fetch and return list of boards."""
        boards = self.client.get_boards()
        return boards

    def fetch_items_from_board(self, board_id: int):
        """Fetch items for a given board."""
        items = self.client.get_items(board_id)
        return items
