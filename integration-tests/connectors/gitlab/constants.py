# pyright: ignore-file

"""Shared constants for the GitLab connector integration tests.

Environment carries the tenant values (token, instance, group, projects); fixture
*shapes* are discovered by the conftest against the live group, so a re-provisioned
fixture needs no code change here. Only the two frozen blocks snapshots and the
pinned merge request are addressed by number, because their content is compared
byte-for-byte or mutated in place.

Concurrency contract: the primary project is read-only for every run, forever.
Mutations go to the mutation project, tagged with this process's ``GL_IT_RUN_ID`` —
issue/MR titles via ``artifact_title``, code files via ``it_path`` — so a run can
clean up exactly its own artifacts and never assert on another run's.
"""

import os
import re
import uuid

# ---------------------------------------------------------------------------
# Tenant configuration (env)
# ---------------------------------------------------------------------------

ENV_TOKEN = "GITLAB_TEST_TOKEN"
ENV_INSTANCE_URL = "GITLAB_TEST_INSTANCE_URL"
ENV_GROUP = "GITLAB_TEST_GROUP"
ENV_SUBGROUP = "GITLAB_TEST_SUBGROUP"
ENV_PRIMARY_PROJECT = "GITLAB_TEST_PRIMARY_PROJECT"
ENV_MUTATION_PROJECT = "GITLAB_TEST_MUTATION_PROJECT"

DEFAULT_INSTANCE_URL = "https://gitlab.com"

# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------

# Generous: the connector runs GitLab calls through an executor with a 300s
# per-op budget and python-gitlab retries transient 5xx internally, so a
# throttled sync legitimately takes minutes.
GL_SYNC_WAIT_SEC = int(os.getenv("GITLAB_TEST_SYNC_WAIT_SEC", "300"))

# Blocks payloads are built on demand at stream time. The first stream of a session
# also pays the parser/processor warm-up, which alone can exceed the client's 60s
# default; subsequent ones return in seconds.
GL_STREAM_WAIT_SEC = int(os.getenv("GITLAB_TEST_STREAM_WAIT_SEC", "180"))

# Poll timeout for ``Record.indexing_status == COMPLETED``.
GL_INDEXING_WAIT_SEC = int(os.getenv("GITLAB_TEST_INDEXING_WAIT_SEC", "180"))

# Code-file timestamps are filled by a backfill task scheduled after run_sync
# returns, so they arrive some time AFTER the sync reports finished. Poll for
# their arrival; never assert their absence.
GL_TIMESTAMP_WAIT_SEC = int(os.getenv("GITLAB_TEST_TIMESTAMP_WAIT_SEC", "240"))

# ---------------------------------------------------------------------------
# Run identity / artifact naming
# ---------------------------------------------------------------------------

GL_IT_RUN_ID = uuid.uuid4().hex[:8]

GL_IT_ARTIFACT_PREFIX = "GlIT-"

# Artifacts older than this belong to a run that no longer exists (a cancelled CI
# job SIGTERMs pytest before any ``finally``). A whole leg finishes well inside an
# hour, so a two-hour gate can never reap a run still asserting on its own data.
GL_IT_STALE_ARTIFACT_AGE_SEC = 2 * 60 * 60

# Proves ownership before anything is deleted. Deliberately strict: the sweep
# refuses anything not matching this exact shape, so a hand-made fixture can never
# be mistaken for a leaked artifact.
GL_IT_ARTIFACT_RE = re.compile(
    rf"^{re.escape(GL_IT_ARTIFACT_PREFIX)}[0-9a-f]{{8}}-[A-Za-z]+-[0-9a-f]{{8}}$"
)


def artifact_title(kind: str) -> str:
    """Title for an issue/MR this run creates: ``GlIT-<run_id>-<Kind>-<hex>``."""
    if not kind.isalpha():
        raise ValueError(f"artifact kind must be letters only, got {kind!r}")
    return f"{GL_IT_ARTIFACT_PREFIX}{GL_IT_RUN_ID}-{kind}-{uuid.uuid4().hex[:8]}"


# Code files are namespaced by path rather than title. The connector syncs only the
# project's default branch, so concurrent runs necessarily share it — confining each
# run to its own directory is what keeps their compare-commits deltas from
# interfering with each other's assertions.
GL_IT_PATH_ROOT = "it"


def it_path(*parts: str) -> str:
    """Repo path for a code file this run owns: ``it/<run_id>/<parts...>``."""
    return "/".join((GL_IT_PATH_ROOT, GL_IT_RUN_ID, *parts))


def owns_path(path: str) -> bool:
    """True when ``path`` is inside this run's namespace (safe to rename/delete)."""
    return path.startswith(f"{GL_IT_PATH_ROOT}/{GL_IT_RUN_ID}/")


# ---------------------------------------------------------------------------
# Pinned fixtures
# ---------------------------------------------------------------------------

# The frozen issue and MR whose streamed blocks back the committed snapshots. Their
# bodies and comments must not change or the snapshots are invalidated.
GL_BLOCKS_ISSUE_IID = int(os.getenv("GITLAB_TEST_BLOCKS_ISSUE_IID", "1"))
GL_BLOCKS_MR_IID = int(os.getenv("GITLAB_TEST_BLOCKS_MR_IID", "1"))

# The long-lived MR that TC-INCR-MR-001 *updates* every run. It is never created or
# closed by the suite: a per-run MR would accumulate, and its title sits outside
# GL_IT_ARTIFACT_RE so the stale sweep can never touch it.
GL_INCR_MR_IID = int(os.getenv("GITLAB_TEST_INCR_MR_IID", "1"))

# The one file on the pinned MR's branch, rewritten in place each run so the branch
# never accumulates files.
PINNED_MR_FILE = "mr-fixture/change.txt"

# Marker on every comment TC-INCR-MR-001 leaves on the pinned MR, so the sweep can
# recognise its own leftovers without touching a human's comment.
PINNED_MR_COMMENT_MARKER = "TC-INCR-MR-001 run"

# Set to "1" to regenerate both snapshots in place, then hand-review and commit.
ENV_BLOCKS_BOOTSTRAP = "GITLAB_BLOCKS_BOOTSTRAP"

# ---------------------------------------------------------------------------
# Connector-behaviour constants the assertions depend on
# ---------------------------------------------------------------------------

# GitLab access levels. The connector uses the level ONLY to decide which record
# groups a member is granted on — the permission type itself is always OWNER.
GL_ACCESS_GUEST = 10
GL_ACCESS_REPORTER = 20

# Name prefix of the stand-in group the connector creates for a member whose
# ``public_email`` is unset (constants.py: PSEUDO_USER_GROUP_PREFIX).
PSEUDO_USER_GROUP_PREFIX = "[Pseudo-User]"
