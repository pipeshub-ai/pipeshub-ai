"""Settings shared by the integration tests that need a service of their own."""

from __future__ import annotations

import os

from .vhost_pin import install

if os.environ.get("STORAGE_IT_S3_VHOST_ADDR"):
    install(os.environ["STORAGE_IT_S3_VHOST_ADDR"])
