"""Seeds and inspects the ``bookstack-source`` service the BookStack connector syncs from.

BookStack only issues API tokens from its web UI. A token is a row in its
``api_tokens`` table with a bcrypt hash of the secret, so ``ensure_token``
writes that row for the built-in admin through the ``bookstack-db`` port. The
secret is a fixed test value; the hash below is ``password_hash`` of it, made
with BookStack's own PHP. Everything else goes through the REST API.

Each run's content lives in books whose names carry the run id, so teardown
deletes exactly those books.
"""

from __future__ import annotations

import pymysql
import requests

TOKEN_ID = "pipeshubintegrationtokenid"
TOKEN_SECRET = "pipeshub-integration-bookstack-secret"
_TOKEN_SECRET_HASH = "$2y$12$9W0m9ouzGVtAzvp1pejc1eRxOhEa91eOOhN.0pnXadKjNgox5I.Oq"


class BookStackSourceHelper:
    def __init__(
        self,
        base_url: str,
        db_host: str,
        db_port: int,
        db_user: str,
        db_password: str,
        db_name: str,
        timeout: float = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._db = dict(host=db_host, port=db_port, user=db_user, password=db_password, database=db_name)
        self.timeout = timeout
        self._admin_id: int | None = None

    # -- access ------------------------------------------------------------

    def ensure_token(self) -> None:
        """Give the built-in admin the test API token, if it has none yet."""
        with pymysql.connect(**self._db, connect_timeout=10) as conn, conn.cursor() as cur:
            # By role, not email: set_admin_email changes the email.
            cur.execute(
                "SELECT ru.user_id FROM role_user ru JOIN roles r ON r.id = ru.role_id "
                "WHERE r.system_name = 'admin' ORDER BY ru.user_id LIMIT 1"
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("BookStack has not created its admin user yet")
            self._admin_id = row[0]
            cur.execute(
                "INSERT IGNORE INTO api_tokens "
                "(name, token_id, secret, user_id, expires_at, created_at, updated_at) "
                "VALUES ('pipeshub-integration', %s, %s, %s, '2099-12-31', NOW(), NOW())",
                (TOKEN_ID, _TOKEN_SECRET_HASH, row[0]),
            )
            conn.commit()

    def _api(self, method: str, path: str, **json: object) -> dict:
        response = requests.request(
            method,
            f"{self.base_url}/api/{path}",
            json=json or None,
            headers={"Authorization": f"Token {TOKEN_ID}:{TOKEN_SECRET}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def ping(self) -> None:
        self._api("GET", "books?count=1")

    def set_admin_email(self, email: str) -> None:
        """Pages are owned by the admin, who creates them, and the connector grants
        each page's owner access by email. The test user can only read the pages
        back through PipesHub if that email is theirs."""
        self._api("PUT", f"users/{self._admin_id}", email=email)

    # -- content -----------------------------------------------------------

    def create_book(self, name: str) -> int:
        return self._api("POST", "books", name=name)["id"]

    def create_chapter(self, book_id: int, name: str) -> int:
        return self._api("POST", "chapters", book_id=book_id, name=name)["id"]

    def create_page(
        self, name: str, markdown: str, *, book_id: int | None = None, chapter_id: int | None = None
    ) -> int:
        parent = {"chapter_id": chapter_id} if chapter_id else {"book_id": book_id}
        return self._api("POST", "pages", name=name, markdown=markdown, **parent)["id"]

    def update_page(self, page_id: int, markdown: str) -> None:
        self._api("PUT", f"pages/{page_id}", markdown=markdown)

    def delete_books_named(self, prefix: str) -> None:
        """Delete every book whose name starts with ``prefix``, from this run or an earlier one."""
        for book in self._api("GET", "books?count=500").get("data", []):
            if book["name"].startswith(prefix):
                self._api("DELETE", f"books/{book['id']}")

    def clear_objects(self, _resource_name: str, folder: str) -> None:
        """Teardown hook for ``connector_lifecycle.destructor``: delete the run's books."""
        self.delete_books_named(folder)
