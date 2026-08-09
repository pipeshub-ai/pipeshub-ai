"""Redis key scheme for the workflow execution journal.

Key prefixes are distinct from tasks/ to avoid collision.
"""
from __future__ import annotations


def journal_entry_key(run_id: str, step_key: str) -> str:
    """Hash holding one journal entry."""
    return f"wf:journal:{run_id}:{step_key}"


def journal_seq_key(run_id: str) -> str:
    """String key holding the next monotonic sequence number for a run."""
    return f"wf:journal:seq:{run_id}"


def journal_index_key(run_id: str) -> str:
    """Sorted set of step_keys for a run, score = seq."""
    return f"wf:journal:idx:{run_id}"


def journal_ttl_seconds() -> int:
    """Journal entries expire after 30 days by default."""
    return 30 * 24 * 3600


def workflow_state_key(org_id: str, workflow_id: str) -> str:
    """Hash holding all `ctx.state` entries for one workflow. Org-scoped so a
    workflow id leaked across tenants still cannot reach another org's state,
    and deliberately not TTL'd -- this state outlives individual runs."""
    return f"wf:state:{org_id}:{workflow_id}"
