from typing import Any, Dict, List, Optional

from boxsdk import Client, OAuth2


def _to_dict(obj: Any) -> Dict[str, Any]:
    """Convert Box SDK object to dict safely."""
    if hasattr(obj, "id") and hasattr(obj, "type"):
        return {
            "id": getattr(obj, "id", None),
            "type": getattr(obj, "type", None),
            "name": getattr(obj, "name", None),
            "login": getattr(obj, "login", None),
        }
    return {"raw": str(obj)}


class BoxDataSourceBase:
    """
    Base class containing all Box API operations.
    Auth is handled by child classes.
    """

    def __init__(self, client: Client) -> None:
        self.client = client

    # ---------------- Users ----------------
    def get_user_info(self) -> Dict[str, Any]:
        return _to_dict(self.client.user().get())

    def get_user(self, user_id: str) -> Dict[str, Any]:
        return _to_dict(self.client.user(user_id=user_id).get())

    def list_users(self, limit: int = 100) -> List[Dict[str, Any]]:
        return [_to_dict(u) for u in self.client.users(limit=limit)]

    # ---------------- Folders ----------------
    def list_folder_items(self, folder_id: str = "0", limit: int = 100) -> List[Dict[str, Any]]:
        return [_to_dict(item) for item in self.client.folder(folder_id).get_items(limit=limit)]

    def create_folder(self, name: str, parent_id: str = "0") -> Dict[str, Any]:
        return _to_dict(self.client.folder(parent_id).create_subfolder(name))

    def get_folder_info(self, folder_id: str) -> Dict[str, Any]:
        return _to_dict(self.client.folder(folder_id).get())

    def delete_folder(self, folder_id: str, recursive: bool = True) -> None:
        self.client.folder(folder_id).delete(recursive=recursive)

    # ---------------- Files ----------------
    def upload_file(self, folder_id: str, file_path: str) -> Dict[str, Any]:
        return _to_dict(self.client.folder(folder_id).upload(file_path))

    def download_file(self, file_id: str, destination_path: str) -> None:
        with open(destination_path, "wb") as f:
            self.client.file(file_id).download_to(f)

    def get_file_info(self, file_id: str) -> Dict[str, Any]:
        return _to_dict(self.client.file(file_id).get())

    def delete_file(self, file_id: str) -> None:
        self.client.file(file_id).delete()

    # ---------------- Groups ----------------
    def list_groups(self, limit: int = 100) -> List[Dict[str, Any]]:
        return [_to_dict(g) for g in self.client.groups(limit=limit)]

    def get_group(self, group_id: str) -> Dict[str, Any]:
        return _to_dict(self.client.group(group_id=group_id).get())

    # ---------------- Collaborations ----------------
    def list_collaborations(self, item_id: str, item_type: str = "folder") -> List[Dict[str, Any]]:
        if item_type == "folder":
            return [_to_dict(c) for c in self.client.folder(item_id).get_collaborations()]
        elif item_type == "file":
            return [_to_dict(c) for c in self.client.file(item_id).get_collaborations()]
        else:
            raise ValueError("item_type must be 'file' or 'folder'")

    def add_collaboration(self, item_id: str, user_login: str, role: str, item_type: str = "folder") -> Dict[str, Any]:
        if item_type == "folder":
            return _to_dict(self.client.folder(item_id).collaborate(user_login, role))
        elif item_type == "file":
            return _to_dict(self.client.file(item_id).collaborate(user_login, role))
        else:
            raise ValueError("item_type must be 'file' or 'folder'")

    # ---------------- Tasks ----------------
    def list_file_tasks(self, file_id: str) -> List[Dict[str, Any]]:
        return [_to_dict(t) for t in self.client.file(file_id).get_tasks()]

    def create_task(self, file_id: str, message: str, due_at: Optional[str] = None) -> Dict[str, Any]:
        return _to_dict(self.client.file(file_id).add_task(message=message, due_at=due_at))

    # ---------------- Comments ----------------
    def list_file_comments(self, file_id: str) -> List[Dict[str, Any]]:
        return [_to_dict(c) for c in self.client.file(file_id).get_comments()]

    def add_comment(self, file_id: str, message: str) -> Dict[str, Any]:
        return _to_dict(self.client.file(file_id).add_comment(message))

    # ---------------- Web Links ----------------
    def create_web_link(self, parent_id: str, url: str, name: str, description: str = "") -> Dict[str, Any]:
        return _to_dict(self.client.folder(parent_id).create_web_link(url=url, name=name, description=description))

    def get_web_link(self, link_id: str) -> Dict[str, Any]:
        return _to_dict(self.client.web_link(link_id).get())

    def delete_web_link(self, link_id: str) -> None:
        self.client.web_link(link_id).delete()

    # ---------------- Webhooks ----------------
    def create_webhook(self, target_id: str, target_type: str, triggers: List[str], address: str) -> Dict[str, Any]:
        return _to_dict(self.client.create_webhook(target=(target_type, target_id), triggers=triggers, address=address))

    def delete_webhook(self, webhook_id: str) -> None:
        self.client.webhook(webhook_id).delete()

    # ---------------- Search ----------------
    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        return [_to_dict(item) for item in self.client.search().query(query, limit=limit)]


class BoxDataSourceWithRefresh(BoxDataSourceBase):
    """Authenticate using client_id, client_secret, and refresh_token"""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str) -> None:
        oauth2 = OAuth2(
            client_id=client_id,
            client_secret=client_secret,
            access_token=None,
            refresh_token=refresh_token,
        )
        client = Client(oauth2)
        super().__init__(client)


class BoxDataSourceWithToken(BoxDataSourceBase):
    """Authenticate using a plain access token (short-lived, for testing)"""

    def __init__(self, access_token: str) -> None:
        oauth2 = OAuth2(
            client_id=None,
            client_secret=None,
            access_token=access_token,
        )
        client = Client(oauth2)
        super().__init__(client)
