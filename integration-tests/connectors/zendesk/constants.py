"""Stable source records used by the live Zendesk integration suite."""

# Set these to the IDs of the dedicated test tenant's seeded records.
# The ticket needs a group, requester, assignee, and public comment; the
# published Help Center article needs a section and non-empty body.
ZENDESK_TEST_TICKET_ID: int | None = 4
ZENDESK_TEST_ARTICLE_ID: int | None = 39571438662930
