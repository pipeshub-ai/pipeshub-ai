import httpx

from app.services.redis.loop_clients import LoopBoundClients

# Connections to the search APIs are kept open between searches; httpx clients are bound to the
# event loop that made them, so each loop gets its own.
_CLIENTS: LoopBoundClients[httpx.AsyncClient] = LoopBoundClients(
    lambda: httpx.AsyncClient(limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)),
)


def shared_http_client() -> httpx.AsyncClient:
    return _CLIENTS.get()
