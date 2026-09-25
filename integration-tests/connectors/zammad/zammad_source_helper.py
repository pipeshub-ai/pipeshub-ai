"""Seeds and inspects the ``zammad-railsserver`` service the Zammad connector syncs from.

Everything here goes through Zammad's REST API, so the stack needs no console
access. Three details are worth knowing, each learned from the running server:

* A fresh instance has no users. The first ``POST /api/v1/users`` creates the
  admin, and that call is the one request Zammad guards with a CSRF token,
  which it returns in the ``CSRF-TOKEN`` header of any earlier response.
* An agent can only file tickets in groups it has access to, so the admin is
  given full access to each group this seeds.
* The connector finds tickets through Zammad's search, which is served by
  Elasticsearch and indexed in the background. ``wait_until_searchable`` waits
  for that, because a ticket the index has not caught up with is a ticket the
  connector cannot see.
"""

from __future__ import annotations

import time

import requests

from connectors.zammad.zammad_seed import ADMIN_PASSWORD


class ZammadSourceHelper:
    def __init__(self, base_url: str, admin_email: str, timeout: float = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_email = admin_email
        self.timeout = timeout
        self.token: str | None = None
        self._admin_id: int | None = None

    # -- access ------------------------------------------------------------

    def ensure_admin_and_token(self) -> str:
        """Create the admin if this instance has none, and return an API token."""
        if self.token:
            return self.token
        session = requests.Session()
        probe = session.get(f"{self.base_url}/api/v1/signshow", timeout=self.timeout)
        csrf = probe.headers.get("CSRF-TOKEN", "")
        created = session.post(
            f"{self.base_url}/api/v1/users",
            json={
                "firstname": "Pipeshub",
                "lastname": "Admin",
                "email": self.admin_email,
                "password": ADMIN_PASSWORD,
            },
            headers={"X-CSRF-Token": csrf},
            timeout=self.timeout,
        )
        # 4xx here means the admin already exists, from an earlier run.
        if not created.ok and created.status_code not in (401, 422):
            created.raise_for_status()

        minted = requests.post(
            f"{self.base_url}/api/v1/user_access_token",
            json={
                "name": f"pipeshub-integration-{int(time.time())}",
                "permission": ["admin", "ticket.agent"],
            },
            auth=(self.admin_email, ADMIN_PASSWORD),
            timeout=self.timeout,
        )
        minted.raise_for_status()
        self.token = minted.json()["token"]
        self._admin_id = self._api("GET", "users/me")["id"]
        return self.token

    def _api(self, method: str, path: str, **json: object) -> dict:
        response = requests.request(
            method,
            f"{self.base_url}/api/v1/{path}",
            json=json or None,
            headers={"Authorization": f"Token token={self.token}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def ping(self) -> None:
        self._api("GET", "users/me")

    # -- content -----------------------------------------------------------

    def create_group(self, name: str) -> int:
        """Create a Zammad group — a record group for the connector — and let the admin file in it."""
        group_id = self._api("POST", "groups", name=name, active=True)["id"]
        existing = self._api("GET", f"users/{self._admin_id}").get("group_ids") or {}
        access = {str(gid): rights for gid, rights in existing.items()}
        access[str(group_id)] = ["full"]
        self._api("PUT", f"users/{self._admin_id}", group_ids=access)
        return group_id

    def create_ticket(self, title: str, body: str, group_id: int) -> int:
        """File a ticket with its first article, the text the connector indexes."""
        return self._api(
            "POST",
            "tickets",
            title=title,
            group_id=group_id,
            customer_id=self._admin_id,
            article={"subject": title, "body": body, "type": "note", "internal": False},
        )["id"]

    def add_article(self, ticket_id: int, body: str, attachment: tuple[str, str] | None = None) -> None:
        """Add a reply to a ticket, optionally with an attachment the connector streams."""
        article: dict[str, object] = {
            "ticket_id": ticket_id,
            "subject": "Update",
            "body": body,
            "type": "note",
            "internal": False,
        }
        if attachment:
            import base64

            name, content = attachment
            article["attachments"] = [
                {
                    "filename": name,
                    "data": base64.b64encode(content.encode()).decode(),
                    "mime-type": "text/plain",
                }
            ]
        self._api("POST", "ticket_articles", **article)

    def update_ticket_title(self, ticket_id: int, title: str) -> None:
        self._api("PUT", f"tickets/{ticket_id}", title=title)

    # -- search index ------------------------------------------------------

    def wait_until_searchable(self, titles: list[str], timeout: float = 300) -> None:
        """Wait until Zammad's search returns each title.

        The connector reads tickets through the search API, so a run that starts
        before the index has caught up would find nothing. Raises with the
        titles still missing, which names what to look at rather than timing out
        silently later in the sync.
        """
        deadline = time.time() + timeout
        missing = list(titles)
        while time.time() < deadline and missing:
            missing = [title for title in missing if not self._search_finds(title)]
            if missing:
                time.sleep(5)
        if missing:
            raise RuntimeError(
                f"Zammad's search index has not picked up {missing} after {timeout:.0f}s. "
                "Check that the zammad-elasticsearch service is healthy."
            )

    def _search_finds(self, title: str) -> bool:
        found = self._api("GET", f"search?objects=Ticket&query={requests.utils.quote(title)}&limit=50")
        return any(
            ticket.get("title") == title
            for ticket in (found.get("assets", {}).get("Ticket", {}) or {}).values()
        )

    def tickets_in_group(self, group_id: int) -> list[dict]:
        found = self._api("GET", f"search?objects=Ticket&query=group_id:{group_id}&limit=100")
        return list((found.get("assets", {}).get("Ticket", {}) or {}).values())

    # -- teardown ----------------------------------------------------------

    def delete_tickets_titled(self, prefix: str) -> None:
        """Delete every ticket whose title starts with ``prefix``, from this run or an earlier one."""
        found = self._api("GET", f"search?objects=Ticket&query={requests.utils.quote(prefix)}&limit=200")
        for ticket in (found.get("assets", {}).get("Ticket", {}) or {}).values():
            if str(ticket.get("title", "")).startswith(prefix):
                self._api("DELETE", f"tickets/{ticket['id']}")

    def clear_objects(self, _resource_name: str, folder: str) -> None:
        """Teardown hook for ``connector_lifecycle.destructor``: delete the run's tickets."""
        self.delete_tickets_titled(folder)
