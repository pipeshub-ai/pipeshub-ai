"""Whether a Google Workspace directory listing names every user of the domain."""

from logging import Logger
from typing import Any


async def partial_directory_reason(admin_data_source: Any, admin_email: str, logger: Logger) -> str | None:  # noqa: ANN401 - the Admin SDK data source
    """Why ``users.list(customer="my_customer")`` read as ``admin_email`` may leave users out, or None
    when it lists the whole directory.

    A delegated admin is answered with the users of the organizational units it manages only, so its
    listing proves nobody absent. Only a super admin (``isAdmin``) reads every user. When that cannot
    be read, the listing is taken as partial."""
    if not admin_email:
        return "the admin the service account acts as is not configured"
    try:
        admin = await admin_data_source.users_get(userKey=admin_email)
    except Exception as e:
        logger.warning("Could not read whether the Google Workspace admin is a super admin: %s", e)
        return "whether the admin is a super admin could not be read"
    if isinstance(admin, dict) and admin.get("isAdmin") is True:
        return None
    return "delegated admin sees only its OUs"
