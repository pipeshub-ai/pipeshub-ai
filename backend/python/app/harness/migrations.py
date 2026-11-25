"""The agent harness's data migrations over the graph and configuration stores (C-15, Python half).

Node's index migrations are ``HARNESS_MIGRATIONS`` (Mongo). A data migration here rewrites stored
documents, so each one reports before it writes (``dry_run``, the default everywhere it is run),
keeps a ledger of what it wrote, and has a ``revert`` that undoes exactly that. Each is idempotent.
``tests/cohesion/test_c15_data_migrations.py`` keeps every one registered here and shaped so.
"""

from __future__ import annotations

from app.harness.credentials.migration import CredentialBindingMigration
from app.harness.tool_identity.migration import AgentToolsetMigration

HARNESS_DATA_MIGRATIONS: dict[str, type[AgentToolsetMigration] | type[CredentialBindingMigration]] = {
    AgentToolsetMigration.id: AgentToolsetMigration,
    CredentialBindingMigration.id: CredentialBindingMigration,
}

__all__ = ["HARNESS_DATA_MIGRATIONS"]
