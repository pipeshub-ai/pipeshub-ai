"""Fixtures for driving the published @pipeshub-ai/mcp package against this stack.

The package under test is a client. Everything here runs it the way a customer
does -- as a separate process, over the command line, holding a token minted the
way a real MCP host mints one -- rather than importing anything from it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from typing import Iterator

import pytest

from helper.clients.kb_client import KBClient
from helper.indexing_progress import wait_until_finished
from helper.mcp_oauth import (
    OAuthApp,
    authorization_code_token,
    create_oauth_app,
    delete_oauth_app,
)
from helper.local_auth import obtain_user_session_token
from helper.stored_names import stored_name

logger = logging.getLogger("mcp-package")

# Which build of the package to exercise. Unset means the published release,
# which is what customers have; the MCP repository's own pull requests set this
# to a path so the branch under review is tested instead.
PACKAGE_SPEC_ENV = "PIPESHUB_MCP_PACKAGE"
DEFAULT_PACKAGE_SPEC = "@pipeshub-ai/mcp@latest"

CLI_TIMEOUT = int(os.getenv("PIPESHUB_MCP_CLI_TIMEOUT", "120"))


@dataclass(frozen=True)
class CliResult:
    exit_code: int
    stdout: str
    stderr: str

    def json(self) -> object:
        """The parsed stdout. Raises with the output attached when it is not JSON."""
        try:
            return json.loads(self.stdout)
        except json.JSONDecodeError as e:
            raise AssertionError(
                f"expected JSON on stdout, got:\n{self.stdout[:2000]}\n"
                f"stderr:\n{self.stderr[:2000]}"
            ) from e


@pytest.fixture(scope="session")
def mcp_base_url() -> str:
    base_url = os.getenv("PIPESHUB_BASE_URL", "").rstrip("/")
    if not base_url:
        pytest.skip("PIPESHUB_BASE_URL is not set")
    return base_url


@pytest.fixture(scope="session")
def package_spec() -> str:
    """What `npx` is asked to run, reported so a failure says which build broke.

    The published release moves under us. When one of these tests fails after a
    release, the version in the report is the difference between a mystery and
    an obvious cause, which is why this is logged rather than pinned.
    """
    spec = os.getenv(PACKAGE_SPEC_ENV, "").strip() or DEFAULT_PACKAGE_SPEC
    # `npm`, not `npx`: run_cli shells out to `npm exec`, and a PATH can carry
    # one without the other -- which would raise FileNotFoundError here instead
    # of skipping.
    if shutil.which("npm") is None:
        pytest.skip("npm is not on PATH, so the package cannot be run")
    return spec


@pytest.fixture(scope="session")
def resolved_package_version(package_spec: str) -> str:
    """The exact version npx resolved to, printed into the test report."""
    proc = subprocess.run(
        ["npm", "view", package_spec.split("@latest")[0] or "@pipeshub-ai/mcp", "version"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    return proc.stdout.strip() or "unknown"


@pytest.fixture(scope="session")
def mcp_user_jwt(mcp_base_url: str) -> str:
    if not (os.getenv("PIPESHUB_TEST_USER_EMAIL") and os.getenv("PIPESHUB_TEST_USER_PASSWORD")):
        pytest.skip("PIPESHUB_TEST_USER_EMAIL / PIPESHUB_TEST_USER_PASSWORD are not set")
    return obtain_user_session_token(mcp_base_url)


@pytest.fixture(scope="session")
def package_oauth_app(mcp_base_url: str, mcp_user_jwt: str) -> Iterator[OAuthApp]:
    app = create_oauth_app(
        mcp_base_url,
        mcp_user_jwt,
        name=f"integration-mcp-package-{uuid.uuid4().hex[:12]}",
    )
    try:
        yield app
    finally:
        try:
            delete_oauth_app(mcp_base_url, mcp_user_jwt, app.id)
        except Exception:  # noqa: BLE001 - teardown must not mask test results
            pass


@pytest.fixture(scope="session")
def package_token(mcp_base_url: str, mcp_user_jwt: str, package_oauth_app: OAuthApp) -> str:
    """Authorization code with PKCE, which is the grant a real MCP host uses.

    Deliberately not client credentials: that grant carries no user, so the
    permission filtering this product depends on would be switched off and the
    tests would prove nothing about what a given person can see.
    """
    return authorization_code_token(mcp_base_url, mcp_user_jwt, package_oauth_app)["access_token"]


@pytest.fixture(scope="session")
def run_cli(package_spec: str, mcp_base_url: str, package_token: str):
    """Run the package's CLI as a customer would, and return what it reported."""

    def _run(*args: str, token: str | None = None, timeout: int = CLI_TIMEOUT) -> CliResult:
        env = {
            **os.environ,
            "PIPESHUB_BASE_URL": mcp_base_url,
            # Set explicitly to empty when a test wants the unauthenticated
            # path, so an ambient token from the developer's shell cannot make
            # a failing case look like it passed.
            "PIPESHUB_TOKEN": package_token if token is None else token,
            "PIPESHUB_MCP_TOKEN": "",
        }
        # `npm exec --package <spec> -- pipeshub`, not `npx <spec>`. The package
        # maps its own name to the MCP server binary, so `npx @pipeshub-ai/mcp
        # directory whoami` starts the server and exits 251 with "No command
        # registered for `directory`". Naming the binary after the spec does not
        # help either; it has to be selected explicitly.
        proc = subprocess.run(
            ["npm", "exec", "--yes", "--package", package_spec, "--", "pipeshub", *args],
            capture_output=True, text=True, env=env, timeout=timeout, check=False,
        )
        return CliResult(proc.returncode, proc.stdout, proc.stderr)

    return _run


@pytest.fixture(scope="session")
def seeded_record(kb_client: KBClient, ai_models_configured) -> Iterator[dict[str, str]]:
    """A document put here by this test, with a phrase nothing else contains.

    Searching for something already in the instance would not prove much: the
    result could predate the package, or the run, or the index. A phrase invented
    here can only be found if upload, indexing, embedding and search all worked
    during this run.

    Takes ``ai_models_configured`` because indexing needs an organisation LLM and
    embedding model. Without it the document never leaves QUEUED and every test
    below skips for a reason that has nothing to do with the package.
    """
    token = uuid.uuid4().hex[:12]
    needle = f"zarquon {token} calibration protocol"
    name = f"mcp-package-{token}.md"
    body = (
        f"# Runbook {token}\n\n"
        f"{needle}. The {needle} is reviewed each quarter by the operations team.\n\n"
        + "\n\n".join(
            f"## Section {n}\n\nOperating note {n} for batch {token}. "
            + "Routine maintenance detail. " * 12
            for n in range(1, 16)
        )
    ).encode()

    kb_name = f"mcp-package-{token}"
    kb_id = kb_client.create_kb(kb_name)["id"]
    try:
        upload = kb_client.upload_file(kb_id, name, body, mimetype="text/markdown")
        # The upload answers the SSE envelope `{records: [...], failed, summary}`,
        # not a record document, so the id is nested. Same shape the retrieval,
        # cleanup and resilience suites read.
        record_id = upload["records"][0]["recordId"]

        final = asyncio.run(wait_until_finished(kb_client, [record_id]))
        if final.get(record_id) != "COMPLETED":
            pytest.skip(
                f"the seeded document did not finish indexing ({final.get(record_id)}), "
                "so there is nothing for the package to find; this is a stack problem, "
                "not a package one"
            )

        yield {
            "record_id": record_id,
            "name": stored_name(name),
            "query": needle,
            "needle": needle,
            "kb_name": kb_name,
        }
    finally:
        # In a finally, so a failing test still cleans up: this suite can run
        # against a long-lived instance, where every run would otherwise leave
        # another collection behind for good.
        try:
            kb_client.delete_kb(kb_id)
        except Exception:  # noqa: BLE001 - teardown must not mask test results
            logger.warning("could not delete the seeded knowledge base %s", kb_id)
