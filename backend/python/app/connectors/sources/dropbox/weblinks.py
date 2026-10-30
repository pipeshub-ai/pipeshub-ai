"""The web link a record cites: a Dropbox shared link that opens the item for anyone who already has access.

The connector made the link with `sharing/create_shared_link_with_settings` (audience no_one, so it grants nothing)
for every file and folder on every sync that listed it: one write, and often a second call, per item per sync. A
link, once made, does not change, so the stored record's link is kept. A new item gets a new link; when Dropbox
says one already exists, that one is read (`sharing/list_shared_links`). When Dropbox refuses both (a team policy
that forbids links), the link to the item in the Dropbox web app is used.
"""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

from dropbox.sharing import LinkAudience, SharedLinkSettings

if TYPE_CHECKING:
    import logging

    from dropbox.files import FileMetadata, FolderMetadata

    from app.sources.external.dropbox.dropbox_ import DropboxDataSource

HOME_URL = "https://www.dropbox.com/home"


def home_url(path_display: str) -> str:
    return HOME_URL + urllib.parse.quote(path_display or "/", safe="/")


class WebLinks:
    def __init__(self, data_source: DropboxDataSource, logger: logging.Logger) -> None:
        self.data_source = data_source
        self.logger = logger

    async def link_for(self, entry: FileMetadata | FolderMetadata, *, stored: str | None, team_member_id: str | None = None,
                       team_folder_id: str | None = None) -> str:
        if stored and not stored.startswith(HOME_URL):
            return stored
        scope = {"team_member_id": team_member_id, "team_folder_id": team_folder_id} if team_member_id else {}
        created = await self.data_source.sharing_create_shared_link_with_settings(
            path=entry.path_lower, settings=SharedLinkSettings(audience=LinkAudience("no_one"), allow_download=True),
            **scope,
        )
        if created.success:
            return created.data.url
        if "shared_link_already_exists" in str(created.error):
            # With settings given, Dropbox leaves the existing link out of the error (sharing.stone): read it.
            existing = await self.data_source.sharing_list_shared_links(path=entry.path_lower, direct_only=True, **scope)
            if existing.success and getattr(existing.data, "links", None):
                return existing.data.links[0].url
        self.logger.debug("No shared link for %s (%s); citing the Dropbox web app", entry.path_lower, created.error)
        return home_url(getattr(entry, "path_display", None) or entry.path_lower)
