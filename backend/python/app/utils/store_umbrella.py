"""``PIPESHUB_STORE=postgres``: one switch for the PostgreSQL runtime.

It fills in, for every setting that is not already set, the per-store settings this
runtime adds: the key-value store, the message broker, the crawl scheduler and the
shared state (``REDIS_MODE``). A setting that is set, in the environment or in
``.env``, always wins, so a mixed deployment (say PostgreSQL for configuration and
Kafka for messages) still works. Unset, nothing changes.

The Node API has the same switch in ``src/libs/config/storeUmbrella.ts``; keep the
two lists identical.
"""

import os
from collections.abc import Mapping, MutableMapping

UMBRELLA_ENV = "PIPESHUB_STORE"
POSTGRES_SETTINGS = {
    "KV_STORE_TYPE": "postgres",
    "MESSAGE_BROKER": "postgres",
    "CRAWL_SCHEDULER": "postgres",
    "REDIS_MODE": "postgres",
}


def apply_store_umbrella(env: MutableMapping[str, str] | None = None) -> bool:
    """Apply ``PIPESHUB_STORE`` to ``env`` (default ``os.environ``); True when it switched anything on."""
    target = os.environ if env is None else env
    dotenv: Mapping[str, str | None] = {}
    if env is None:  # .env is read later by the services, so look at it now to honour what it sets
        from dotenv import dotenv_values, find_dotenv

        dotenv = dotenv_values(find_dotenv(usecwd=True))

    def current(key: str) -> str:
        return (target.get(key) or dotenv.get(key) or "").strip()

    value = current(UMBRELLA_ENV).lower()
    if not value:
        return False
    if value != "postgres":
        raise ValueError(f"{UMBRELLA_ENV}={value!r} is not supported; use 'postgres' or leave it unset")
    for key, setting in POSTGRES_SETTINGS.items():
        if not current(key):
            target[key] = setting
    return True
