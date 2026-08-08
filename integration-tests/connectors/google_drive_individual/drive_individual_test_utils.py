# pyright: ignore-file

"""Helpers for Google Drive personal (individual) folder-filter integration tests."""

from __future__ import annotations

import logging
import os
import uuid

from google.auth.transport.requests import Request  # type: ignore[import-not-found]
from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]
from googleapiclient.discovery import build  # type: ignore[import-not-found]

from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]
    GoogleDriveDataSource,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # type: ignore[import-not-found]
    FULL_DRIVE_SCOPE,
    create_drive_folder,
    create_drive_text_file,
)

logger = logging.getLogger("drive-individual-test-utils")

ENV_CLIENT_ID = "GOOGLE_DRIVE_CLIENT_ID"
ENV_CLIENT_SECRET = "GOOGLE_DRIVE_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "GOOGLE_DRIVE_REFRESH_TOKEN"
ENV_TEST_USER = "GOOGLE_DRIVE_TEST_USER_EMAIL"
ENV_SECRET_KEY = "SECRET_KEY"

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def require_drive_individual_env() -> tuple[str, str, str, str]:
    """Return (client_id, client_secret, refresh_token, test_user_email) or raise."""
    client_id = os.getenv(ENV_CLIENT_ID, "").strip()
    client_secret = os.getenv(ENV_CLIENT_SECRET, "").strip()
    refresh_token = os.getenv(ENV_REFRESH_TOKEN, "").strip()
    test_user = os.getenv(ENV_TEST_USER, "").strip()
    secret_key = os.getenv(ENV_SECRET_KEY, "").strip()

    missing = [
        name
        for name, val in (
            (ENV_CLIENT_ID, client_id),
            (ENV_CLIENT_SECRET, client_secret),
            (ENV_REFRESH_TOKEN, refresh_token),
            (ENV_TEST_USER, test_user),
            (ENV_SECRET_KEY, secret_key),
        )
        if not val
    ]
    if missing:
        raise ValueError(
            "Drive personal credentials not set: "
            + ", ".join(missing)
            + ". Set client id/secret + test user + SECRET_KEY in .env.local; "
            "refresh token in .env.refresh_tokens."
        )
    return client_id, client_secret, refresh_token, test_user


async def build_drive_datasource_from_refresh_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> GoogleDriveDataSource:
    """Build GoogleDriveDataSource from a user OAuth refresh token (full drive scope).

    Full ``drive`` scope is required for fixture create/delete/move/rename.
    Connector sync still uses ``drive.readonly`` via Pipeshub.
    """
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=GOOGLE_TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        scopes=[FULL_DRIVE_SCOPE],
    )
    credentials.refresh(Request())
    client = build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )
    return GoogleDriveDataSource(client)


async def create_folder_filter_fixtures(
    drive: GoogleDriveDataSource,
) -> dict[str, str]:
    """Create the folder-filter fixture tree under the OAuth user's My Drive.

    Layout::

        {root}/
          seed/
            nested/
              child.txt
          out_of_scope/
            sibling.txt
    """
    suffix = uuid.uuid4().hex[:8]
    root_name = f"pipeshub-it-drive-personal-ff-{suffix}"

    root_id = await create_drive_folder(drive, root_name)
    seed_id = await create_drive_folder(drive, "seed", parent_id=root_id)
    nested_id = await create_drive_folder(drive, "nested", parent_id=seed_id)
    child_id = await create_drive_text_file(
        drive,
        "child.txt",
        parent_id=nested_id,
        content="pipeshub drive personal folder-filter it\n",
    )
    oos_id = await create_drive_folder(drive, "out_of_scope", parent_id=root_id)
    sibling_id = await create_drive_text_file(
        drive, "sibling.txt", parent_id=oos_id, content="out of scope\n"
    )

    fixtures = {
        "root_folder_id": root_id,
        "root_folder_name": root_name,
        "seed_folder_id": seed_id,
        "seed_folder_name": "seed",
        "nested_folder_id": nested_id,
        "nested_folder_name": "nested",
        "child_file_id": child_id,
        "child_file_name": "child.txt",
        "oos_folder_id": oos_id,
        "oos_folder_name": "out_of_scope",
        "oos_file_id": sibling_id,
        "oos_file_name": "sibling.txt",
    }
    logger.info("Created Drive personal folder-filter fixtures: %s", fixtures)
    return fixtures
