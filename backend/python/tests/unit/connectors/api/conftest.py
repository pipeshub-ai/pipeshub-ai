"""Break the router <-> edition_config import cycle before collection.

`app/connectors/api/router.py:63` imports `app.edition_config`, which imports
the router back, and `router = APIRouter()` is defined 85 lines *after* that
import — so a re-entry finds the module half-built and `ImportError`s.

A full `pytest tests/unit` run survives only by accident of alphabetical
collection: `tests/unit/api/routes/test_mcp_servers.py` pulls `edition_config`
to completion first. Scope the run to this directory and nothing has, so twelve
router modules fail at collection.

Importing `edition_config` here drives it to completion before any test module
is imported, which makes the outcome the same either way.
"""

import app.edition_config  # noqa: F401
