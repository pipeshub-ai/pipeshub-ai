from typing import Any

from app.sources.client.trello.trello import TrelloClient


class TrelloDataSource:

    def __init__(self, client: TrelloClient) ->None:
        self.client = client

    def list_boards(self) -> list[dict[str, Any]]:
        """List boards for the authenticated user."""
        resp = self.client.get("/members/me/boards", params={"fields": "id,name,url"})
        resp.raise_for_status()
        return resp.json()

    def list_lists(self, board_id: str) -> list[dict[str, Any]]:
        """List lists on a board."""
        resp = self.client.get(f"/boards/{board_id}/lists", params={"fields": "id,name"})
        resp.raise_for_status()
        return resp.json()

    def list_cards(self, list_id: str) -> list[dict[str, Any]]:
        """List cards inside a list."""
        resp = self.client.get(f"/lists/{list_id}/cards", params={"fields": "id,name,desc,url"})
        resp.raise_for_status()
        return resp.json()
