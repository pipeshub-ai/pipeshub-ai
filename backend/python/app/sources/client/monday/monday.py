# monday.py
from monday import MondayClient as OfficialMondayClient


class MondayClient:
    """Wrapper class for interacting with Monday.com."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = OfficialMondayClient(api_key)

    def get_boards(self, limit: int = 5):
        """Fetch list of boards."""
        return self.client.boards.fetch_boards(limit=limit)

    def get_items(self, board_id: int):
        """Fetch items of a given board."""
        query = f"""
        query {{
          boards(ids: [{board_id}]) {{
            items {{
              id
              name
              column_values {{
                id
                title
                text
              }}
            }}
          }}
        }}
        """
        return self.client.api_call(query)
