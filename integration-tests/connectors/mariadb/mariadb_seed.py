# pyright: ignore-file

"""The tables the MariaDB suite creates, shared by its fixtures and tests."""

# One record per table, so the seed decides how many records to expect.
# ``kb_articles`` has a primary key; ``kb_notes`` has none, which is what makes
# change detection rely on the table's update time alone.
KEYED_TABLE = "kb_articles"
KEYLESS_TABLE = "kb_notes"
SEED_TABLES = {
    KEYED_TABLE: [
        ("Onboarding guide", "How to set up a new workspace."),
        ("Billing FAQ", "Answers to common billing questions."),
    ],
    KEYLESS_TABLE: [
        ("Leave policy", "Annual leave accrues monthly."),
        ("Security policy", "Rotate credentials every ninety days."),
    ],
}
# Has a foreign key to KEYED_TABLE, so the sync must link the two records.
CHILD_TABLE = "kb_article_links"
# The connector syncs base tables only; a view must not become a record.
VIEW = "kb_article_titles"
# Created by the tests themselves.
NEW_TABLE = "kb_runbooks"
DROPPED_TABLE = "kb_scratch"
EXCLUDED_TABLE = KEYLESS_TABLE
