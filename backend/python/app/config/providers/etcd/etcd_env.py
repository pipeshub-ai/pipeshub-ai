"""The etcd client settings every Python etcd client reads from the environment (one parser, not one per caller).

SEC-D-06: the bundled etcd takes only TLS clients with a certificate its CA signed. ``ETCD_URL`` is then
``https://etcd:2379``, and ``ETCD_CA_FILE``, ``ETCD_CERT_FILE`` and ``ETCD_KEY_FILE`` name the files Compose's
``etcd-tls`` service writes for this component.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from app.config.key_value_store_factory import StoreConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_PORT = 2379


def etcd_store_config(env: Mapping[str, str] | None = None) -> StoreConfig | None:
    """None when ``ETCD_URL`` is unset; raises ValueError for an https URL without the client's TLS files.

    ``env`` defaults to the process environment, read through ``os.getenv`` as every caller read it before.
    """
    get = env.get if env is not None else os.getenv
    url = (get("ETCD_URL") or "").strip()
    if not url:
        return None
    parts = urlsplit(url if "://" in url else f"http://{url}")
    secure = parts.scheme.lower() == "https"
    tls_files = {name: (get(name) or "").strip() or None for name in ("ETCD_CA_FILE", "ETCD_CERT_FILE", "ETCD_KEY_FILE")}
    if secure and not all(tls_files.values()):
        missing = ", ".join(name for name, value in tls_files.items() if not value)
        raise ValueError(f"ETCD_URL is https but {missing} is not set: etcd needs this component's client certificate")
    return StoreConfig(
        host=parts.hostname or "localhost",
        port=parts.port or DEFAULT_PORT,
        # ETCD_TIMEOUT is in milliseconds (consistent with Node.js).
        timeout=float(get("ETCD_TIMEOUT") or "5000") / 1000,
        username=get("ETCD_USERNAME") or None,
        password=get("ETCD_PASSWORD") or None,
        ca_cert=tls_files["ETCD_CA_FILE"] if secure else None,
        cert_cert=tls_files["ETCD_CERT_FILE"] if secure else None,
        cert_key=tls_files["ETCD_KEY_FILE"] if secure else None,
    )
