"""Settings of the live ServiceNow suite. Nothing about the instance is written here: it all comes from env."""

from __future__ import annotations

import os
import re
from pathlib import Path

SERVICENOW_CONNECTOR_TYPE = "ServiceNow"

ENV_INSTANCE_URL = "SERVICENOW_TEST_INSTANCE_URL"
ENV_USERNAME = "SERVICENOW_TEST_USERNAME"
ENV_PASSWORD = "SERVICENOW_TEST_PASSWORD"
ENV_CLIENT_ID = "SERVICENOW_TEST_CLIENT_ID"
ENV_CLIENT_SECRET = "SERVICENOW_TEST_CLIENT_SECRET"
# The other sign-in methods: the instance's integration user (basic auth) and its REST API key.
ENV_INTEGRATION_USERNAME = "SERVICENOW_TEST_INTEGRATION_USERNAME"
ENV_INTEGRATION_PASSWORD = "SERVICENOW_TEST_INTEGRATION_PASSWORD"
ENV_API_KEY = "SERVICENOW_TEST_API_KEY"
REQUIRED_ENV = (ENV_INSTANCE_URL, ENV_USERNAME, ENV_PASSWORD, ENV_CLIENT_ID, ENV_CLIENT_SECRET, "SECRET_KEY")

# Every object the suite writes is named with this prefix and its run id; nothing else is ever changed.
PREFIX = "PHIT"
# Objects of a run that died before its teardown are removed by the next run once they are this old.
STALE_HOURS = 2

# Test users the instance must already have (README, "Instance fixture"): user name, and the email
# their PipesHub account gets. ``noemail`` has no email and no password: nobody logs in as it.
PERSONA_USER_PREFIX = "phqa."
PERSONA_EMAIL_DOMAIN = "example.com"
PERSONAS = (
    "plain", "hr", "eng", "eng2", "contrib", "kbmgr", "owner", "kadmin", "kmanager",
    "ogroup", "author", "roledirect", "rolec", "inactive",
)
NO_EMAIL_PERSONA = "noemail"
# A role the fixture grants to ``roledirect`` directly and to ``rolec`` through a role that contains it.
PERSONA_ROLE = "phqa_reader"


def persona_password_env(name: str) -> str:
    return f"SERVICENOW_TEST_PERSONA_{name.upper()}_PASSWORD"


def persona_user_name(name: str) -> str:
    return f"{PERSONA_USER_PREFIX}{name}"


def persona_email(name: str) -> str:
    return f"{PERSONA_USER_PREFIX}{name}@{PERSONA_EMAIL_DOMAIN}"


SYNC_TIMEOUT = int(os.getenv("SERVICENOW_TEST_SYNC_TIMEOUT", "900"))
INDEX_TIMEOUT = 600
GRAPH_CLEANUP_TIMEOUT = 300
# A user criterion edit can be read back stale for about a second (ServiceNow caches criteria).
CRITERIA_SETTLE_SEC = 3

_CONNECTOR_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "backend/python/app/connectors/sources/servicenow/servicenow/connector.py"
)


def connector_has_filter(enum_name: str, key: str) -> bool:
    """Whether the ServiceNow connector registers a filter, e.g. ``("SyncFilterKey", "KNOWLEDGE_BASES")``.

    Read from the source this checkout runs, so a test of a filter that has not landed yet skips
    instead of failing on a payload the connector ignores.
    """
    try:
        text = _CONNECTOR_SOURCE.read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(rf"name\s*=\s*{enum_name}\.{key}\.value", text) is not None


def connector_offers_auth(auth_type: str) -> bool:
    """Whether the connector registers a sign-in method, e.g. ``"BASIC_AUTH"`` (read from source, as above)."""
    try:
        return re.search(rf"AuthType\.{auth_type}\b", _CONNECTOR_SOURCE.read_text(encoding="utf-8")) is not None
    except OSError:
        return False


KB_FILTER = connector_has_filter("SyncFilterKey", "KNOWLEDGE_BASES")
CATEGORY_FILTER = connector_has_filter("SyncFilterKey", "FOLDER_IDS")
DATE_FILTERS = "modified_date_filter(" in (_CONNECTOR_SOURCE.read_text(encoding="utf-8") if _CONNECTOR_SOURCE.is_file() else "")
INDEX_ARTICLES_FILTER = connector_has_filter("IndexingFilterKey", "KNOWLEDGE_BASE")
INDEX_ATTACHMENTS_FILTER = connector_has_filter("IndexingFilterKey", "ATTACHMENTS")
NO_KB_FILTER = "the connector has no knowledge_bases sync filter yet"
