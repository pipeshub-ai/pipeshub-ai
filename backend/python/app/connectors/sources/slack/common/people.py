"""Slack users as SourcePerson values for authorship links."""
from __future__ import annotations

from typing import Any

from app.models.entities import SourcePerson

SLACKBOT_USER_ID = "USLACKBOT"


def file_uploader(
    file: dict[str, Any], user_id_to_email: dict[str, str], user_id_to_name: dict[str, str],
) -> SourcePerson | None:
    """The user who uploaded ``file``; email and name only from the caches already loaded."""
    uid = file.get("user")
    if not uid:
        return None
    return SourcePerson(
        source_id=uid,
        email=user_id_to_email.get(uid),
        display_name=user_id_to_name.get(uid),
        # Bot ids start with "B"; app uploads may carry bot_id beside a user id.
        is_service_account=bool(file.get("bot_id")) or uid == SLACKBOT_USER_ID or uid.startswith("B"),
    )
