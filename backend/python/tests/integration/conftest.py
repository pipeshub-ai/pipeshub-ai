"""Settings shared by the integration tests that need a service of their own."""

from __future__ import annotations

import os

from .vhost_pin import install

if os.environ.get("STORAGE_IT_S3_VHOST_ADDR"):
    install(os.environ["STORAGE_IT_S3_VHOST_ADDR"])


# Most integration tests drive the stores and providers directly, with no request, message or job binding an org (module-scoped
# fixtures too), and many probe other orgs on purpose to check the stores refuse them: they keep shadow mode, as before enforce became
# the default. pg_runtime, which runs the services' own runtime (key-value store, queues, consumers), puts enforce back per test.
# shortcut: shadow for the provider-level suites; bind the test's org in each of them and drop this to run them in enforce too
os.environ.setdefault("ORG_CONTEXT_MODE", "shadow")
