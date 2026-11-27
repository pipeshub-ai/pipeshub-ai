"""Edition constants the harness core reads: an extension point, as ``app/edition.py`` is for the app, which the
harness core may not import. A build that differs replaces this one file; keep it import-free."""

# The provider prompt-cache retention sent when PIPESHUB_PROMPT_CACHE_RETENTION is unset (None: none is sent, and
# the provider keeps its in-memory default). Here the extended one.
PROMPT_CACHE_RETENTION_DEFAULT: str | None = "24h"
