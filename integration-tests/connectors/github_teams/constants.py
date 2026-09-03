# pyright: ignore-file

"""Shared constants for the GitHub Teams connector integration tests.

Environment carries only tenant-specific values — the PAT and the three IT repo
names. Everything else is either pinned here (the two frozen blocks snapshots,
which must not drift or the expected JSON is meaningless) or *discovered* by the
conftest against the live org, the way the Jira suite discovers its epic/subtask/
attachment shapes rather than hard-coding issue keys that rot.

Concurrency contract (see README.md): the primary and public repos are read-only
for every run, forever. Mutations go to the mutation repo, and everything a run
writes there is tagged with this process's ``GH_IT_RUN_ID`` — issue/PR titles via
``artifact_title``, code files via ``it_path`` — so a run can identify and clean up
exactly its own artifacts and never assert on another run's.
"""

import os
import re
import uuid

# ---------------------------------------------------------------------------
# Tenant configuration (env)
# ---------------------------------------------------------------------------

ENV_TOKEN = "GH_TEAMS_TEST_TOKEN"
ENV_ORG = "GH_TEAMS_TEST_ORG"
ENV_PRIMARY_REPO = "GH_TEAMS_TEST_PRIMARY_REPO"
ENV_PUBLIC_REPO = "GH_TEAMS_TEST_PUBLIC_REPO"
ENV_MUTATION_REPO = "GH_TEAMS_TEST_MUTATION_REPO"

# Optional: path of a >5 MB file in the primary repo. When unset, the oversized-file
# leg of TC-GH-IDX-001 skips rather than failing — committing a 5 MB blob is a real
# cost and not every fixture org will want to carry one.
ENV_OVERSIZED_PATH = "GH_TEAMS_TEST_OVERSIZED_PATH"

# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------

# Sync waits. Generous because ds_call retries 429/5xx three times with jitter and
# can sleep ~30s per attempt, so a throttled sync legitimately takes minutes.
GH_SYNC_WAIT_SEC = int(os.getenv("GH_TEAMS_SYNC_WAIT_SEC", "300"))

# Poll timeout for graph ``Record.indexing_status == COMPLETED``.
GH_INDEXING_WAIT_SEC = int(os.getenv("GH_TEAMS_INDEXING_WAIT_SEC", "180"))

# Poll timeout for the code-file/folder timestamp backfill. This runs as a
# fire-and-forget asyncio task scheduled AFTER run_sync returns
# (connector.py ``repos.timestamps.schedule()``), so timestamps appear some time
# after the sync reports finished. Never assert their absence — only poll for
# their arrival.
GH_TIMESTAMP_BACKFILL_WAIT_SEC = int(os.getenv("GH_TEAMS_TIMESTAMP_WAIT_SEC", "240"))

# ---------------------------------------------------------------------------
# Run identity / artifact naming
# ---------------------------------------------------------------------------

# Every CI leg (arango + neo4j), every open PR and the nightly cron share ONE
# GitHub org, so a run sees other runs' in-flight artifacts. Assertions are always
# by external id, and anything this run creates carries this id.
GH_IT_RUN_ID = uuid.uuid4().hex[:8]

GH_IT_ARTIFACT_PREFIX = "GhIT-"

# Artifacts older than this belong to a run that no longer exists (a cancelled CI
# job SIGTERMs pytest before any ``finally``). A whole leg finishes well inside an
# hour, so a two-hour gate can never reap a run that is still asserting.
GH_IT_STALE_ARTIFACT_AGE_SEC = 2 * 60 * 60

# Proves ownership of an artifact before anything deletes it. Deliberately strict:
# the sweep refuses anything that does not match this exact shape, so a
# hand-created fixture can never be mistaken for a leaked artifact.
GH_IT_ARTIFACT_RE = re.compile(
    rf"^{re.escape(GH_IT_ARTIFACT_PREFIX)}[0-9a-f]{{8}}-[A-Za-z]+-[0-9a-f]{{8}}$"
)


def artifact_title(kind: str) -> str:
    """Title for an issue/PR this run creates: ``GhIT-<run_id>-<Kind>-<hex>``.

    ``kind`` must be letters only (``IncrIssue``, ``SubIssue``, ``IncrPr``): the
    sweep proves ownership with ``GH_IT_ARTIFACT_RE`` over this exact shape and
    refuses anything else.
    """
    if not kind.isalpha():
        raise ValueError(f"artifact kind must be letters only, got {kind!r}")
    return f"{GH_IT_ARTIFACT_PREFIX}{GH_IT_RUN_ID}-{kind}-{uuid.uuid4().hex[:8]}"


# Code files are namespaced by path rather than by title. The connector only ever
# syncs ``repo.default_branch``, so concurrent runs land in each other's
# compare-commits delta — there is no per-run branch to hide behind. Confining each
# run to its own directory means the deltas overlap harmlessly while every
# assertion (by external id) stays about files this run owns.
GH_IT_PATH_ROOT = "it"


def it_path(*parts: str) -> str:
    """Repo path for a code file this run owns: ``it/<run_id>/<parts...>``."""
    return "/".join((GH_IT_PATH_ROOT, GH_IT_RUN_ID, *parts))


def owns_path(path: str) -> bool:
    """True when ``path`` is inside this run's namespace (safe to rename/delete)."""
    return path.startswith(f"{GH_IT_PATH_ROOT}/{GH_IT_RUN_ID}/")


# ---------------------------------------------------------------------------
# Frozen snapshot fixtures (pinned, never discovered)
# ---------------------------------------------------------------------------

# The two blocks snapshots compare a full parsed BlocksContainer against committed
# JSON, so their source must not move. These are the only fixtures pinned by number:
# everything else the conftest discovers, so a re-provisioned org needs no code edit.
#
# The primary repo issue whose body + comments + non-image attachment back
# fixtures/github_issue_blocks.expected.json. It must carry a NON-image attachment:
# CommentsHelper._attachment_file_update returns None for type == "image" (images
# are inlined as base64), so an image produces no FileRecord to assert on.
GH_BLOCKS_ISSUE_NUMBER = int(os.getenv("GH_TEAMS_BLOCKS_ISSUE_NUMBER", "1"))

# The primary repo PR backing fixtures/github_pr_blocks.expected.json. Needs at
# least one changed file, one inline review comment and one conversation comment.
GH_BLOCKS_PR_NUMBER = int(os.getenv("GH_TEAMS_BLOCKS_PR_NUMBER", "2"))

# The long-lived PR that TC-INCR-PR-001 *updates* every run, in the MUTATION repo.
# It is never created or closed by the suite: GitHub has no API to delete a pull
# request, so a per-run PR made the repo's PR list grow forever. Its title sits
# outside GH_IT_ARTIFACT_RE and its branch outside the ``it/`` prefix, so neither
# sweep can reclaim it.
GH_INCR_PR_NUMBER = int(os.getenv("GH_TEAMS_INCR_PR_NUMBER", "28"))

# Set to "1" to regenerate both snapshots in place, then hand-review and commit.
ENV_BLOCKS_BOOTSTRAP = "GH_TEAMS_BLOCKS_BOOTSTRAP"
