"""The pipeshub-ai/examples repository, run against a real PipesHub.

The examples are what a developer copies first, and they live in a separate
repository, so a change here that breaks one is invisible until a reader hits
it. Each test runs one example the way its README says -- as its own process,
with a personal access token -- and checks it finds a document seeded by this
run. Nothing is imported from the examples.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import requests
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

pytestmark = [pytest.mark.integration]

EXAMPLE_TIMEOUT = int(os.getenv("PIPESHUB_EXAMPLES_TIMEOUT", "600"))


# The examples are another repository's code, so they get only what a reader's
# shell would have: none of this job's secrets or connector credentials.
_PASSED_THROUGH = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
_SECRET_NAME = re.compile(r"TOKEN|AUTH|PASSWORD|SECRET", re.IGNORECASE)


def example_env(env: dict[str, str]) -> dict[str, str]:
    return {**{k: os.environ[k] for k in _PASSED_THROUGH if k in os.environ}, **env}


def redact(text: str, env: dict[str, str]) -> str:
    """`text` without the values of `env`'s token-like variables, for failure reports."""
    for name, value in env.items():
        if value and _SECRET_NAME.search(name):
            text = text.replace(value, "***")
    return text


@dataclass(frozen=True)
class Run:
    exit_code: int
    stdout: str
    stderr: str

    def report(self) -> str:
        return f"exit {self.exit_code}\nstdout:\n{self.stdout[-3000:]}\nstderr:\n{self.stderr[-3000:]}"


def run(cmd: list[str], *, cwd: Path, env: dict[str, str], timeout: int = EXAMPLE_TIMEOUT) -> Run:
    """Run an example as a separate process, the way its README does."""
    proc = subprocess.run(
        cmd, cwd=cwd, env=example_env(env), capture_output=True, text=True,
        timeout=timeout, check=False,
    )
    return Run(proc.returncode, redact(proc.stdout, env), redact(proc.stderr, env))

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_FIXTURE = REPO_ROOT / "backend/python/app/connectors/sources/demo/fixture/acme-corp.yaml"
MCP_DIR = "company-knowledge-mcp"
SEARCH_TOOL = "pipeshub_search"


def _sections(out: str) -> tuple[str, str, str]:
    """A starter's output: the search listing, the streamed answer, and its sources."""
    search, _, rest = out.partition("\n== Answer\n")
    answer, _, sources = rest.partition("== Sources")
    return search, answer.strip(), sources


def _assert_starter_found_it(result: Run, seeded: dict[str, str]) -> None:
    assert result.exit_code == 0, result.report()
    search, answer, sources = _sections(result.stdout)
    assert seeded["name"] in search, f"the search did not list the seeded record:\n{result.report()}"
    assert answer, f"the answer stream was empty:\n{result.report()}"
    # The starters print the stream's error and still exit 0, so this is the failure signal.
    assert not answer.startswith("Error:") and "\nError:" not in answer, result.report()
    assert seeded["name"] in sources, f"the answer did not cite the seeded record:\n{result.report()}"


def _reader_env(base_url: str, token: str) -> dict[str, str]:
    """The two variables the SDK examples tell readers to export."""
    return {"PIPESHUB_URL": base_url, "PIPESHUB_BEARER_AUTH": token}


class TestSdkStarter:
    def test_python(self, examples_dir, examples_base_url, examples_token, seeded_record) -> None:
        if shutil.which("uv") is None:
            pytest.skip("uv is not on PATH, and the Python starter is run with it")
        result = run(
            ["uv", "run", "main.py", seeded_record["question"]],
            cwd=examples_dir / "sdk-starter/python",
            env=_reader_env(examples_base_url, examples_token),
        )
        _assert_starter_found_it(result, seeded_record)

    def test_typescript(self, examples_dir, examples_base_url, examples_token, seeded_record) -> None:
        if shutil.which("npm") is None or shutil.which("npx") is None:
            pytest.skip("npm is not on PATH, so the TypeScript starter cannot be installed")
        cwd = examples_dir / "sdk-starter/typescript"
        install = run(["npm", "install", "--no-audit", "--no-fund"], cwd=cwd, env={})
        assert install.exit_code == 0, f"npm install failed:\n{install.report()}"
        result = run(
            ["npx", "tsx", "index.ts", seeded_record["question"]],
            cwd=cwd,
            env=_reader_env(examples_base_url, examples_token),
        )
        _assert_starter_found_it(result, seeded_record)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestPrivateEnterpriseSearch:
    def test_search_and_ask(self, examples_dir, examples_base_url, examples_token, seeded_record, tmp_path) -> None:
        if shutil.which("uv") is None:
            pytest.skip("uv is not on PATH, and the search app is run with it")
        port = _free_port()
        env = {**_reader_env(examples_base_url, examples_token), "PORT": str(port)}
        # A file, not a pipe: nothing reads a pipe while the app runs, and a full one would block it.
        log_path = tmp_path / "app.log"
        log = log_path.open("w")
        proc = subprocess.Popen(
            ["uv", "run", "app.py"],
            cwd=examples_dir / "private-enterprise-search/python",
            env=example_env(env), stdout=log, stderr=subprocess.STDOUT, text=True,
        )

        def app_log() -> str:
            return redact(log_path.read_text(encoding="utf-8", errors="replace")[-3000:], env)

        page = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + EXAMPLE_TIMEOUT
            while True:
                if proc.poll() is not None:
                    pytest.fail(f"the app exited with {proc.returncode}:\n{app_log()}")
                try:
                    if requests.get(page, timeout=5).status_code == 200:
                        break
                except requests.ConnectionError:
                    pass
                if time.monotonic() > deadline:
                    pytest.fail(f"the app never started listening:\n{app_log()}")
                time.sleep(2)

            found = requests.get(f"{page}/api/search", params={"q": seeded_record["needle"]}, timeout=120)
            assert found.status_code == 200, found.text[:2000]
            titles = [r["title"] for r in found.json()["results"]]
            assert seeded_record["name"] in titles, f"search did not list the seeded record: {titles}"

            asked = requests.get(f"{page}/api/ask", params={"q": seeded_record["question"]}, timeout=300)
            assert asked.status_code == 200, asked.text[:2000]
            body = asked.json()
            assert not body.get("error"), f"the answer stream failed: {body}"
            assert body.get("answer", "").strip(), f"empty answer: {body}"
            cited = [c["title"] for c in body.get("citations", [])]
            assert seeded_record["name"] in cited, f"the answer did not cite the seeded record: {body}"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=30)
            log.close()


# --- Company Knowledge MCP: each client's config, read the way that client reads it ---


def _documented_mcp_url(examples_dir: Path) -> str:
    """The PIPESHUB_MCP_URL the tutorial tells readers to export."""
    script = (examples_dir / MCP_DIR / "claude-code/add-pipeshub.sh").read_text(encoding="utf-8")
    m = re.search(r"export PIPESHUB_MCP_URL=(\S+)", script)
    assert m, "add-pipeshub.sh no longer documents the PIPESHUB_MCP_URL to export"
    return m.group(1)


def _on_this_stack(url: str, base_url: str) -> str:
    """The documented URL with this stack's scheme and host: the path is what's tested."""
    doc, base = urlsplit(url), urlsplit(base_url)
    return urlunsplit((base.scheme, base.netloc, doc.path, doc.query, ""))


def _mcp_env(examples_dir: Path, base_url: str, token: str) -> dict[str, str]:
    return {
        "PIPESHUB_MCP_URL": _on_this_stack(_documented_mcp_url(examples_dir), base_url),
        "PIPESHUB_MCP_TOKEN": token,
    }


def _cursor(examples_dir: Path, env: dict[str, str]) -> tuple[str, dict[str, str]]:
    server = json.loads((examples_dir / MCP_DIR / "cursor/mcp.json").read_text())["mcpServers"]["pipeshub"]

    def expand(value: str) -> str:
        def one(m: re.Match[str]) -> str:
            assert m.group(1) in env, f"cursor/mcp.json reads {m.group(1)}, which the tutorial never sets"
            return env[m.group(1)]

        return re.sub(r"\$\{env:([A-Z0-9_]+)\}", one, value)

    return expand(server["url"]), {k: expand(v) for k, v in server.get("headers", {}).items()}


def _codex(examples_dir: Path, env: dict[str, str]) -> tuple[str, dict[str, str]]:
    server = tomllib.loads((examples_dir / MCP_DIR / "codex/config.toml").read_text())["mcp_servers"]["pipeshub"]
    name = server["bearer_token_env_var"]
    assert name in env, f"codex/config.toml reads the token from {name}, which the tutorial never sets"
    base = urlsplit(env["PIPESHUB_MCP_URL"])
    return _on_this_stack(server["url"], f"{base.scheme}://{base.netloc}"), {"Authorization": f"Bearer {env[name]}"}


def _claude_code(examples_dir: Path, env: dict[str, str], tmp_path: Path) -> tuple[str, dict[str, str]]:
    """Run add-pipeshub.sh with a stand-in `claude` that records what it was asked to register."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "claude-args.json"
    fake = bin_dir / "claude"
    fake.write_text(f"#!{sys.executable}\nimport json, sys\njson.dump(sys.argv[1:], open({str(record)!r}, 'w'))\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    result = run(
        ["bash", str(examples_dir / MCP_DIR / "claude-code/add-pipeshub.sh")],
        cwd=tmp_path, env={**env, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, timeout=60,
    )
    assert result.exit_code == 0, result.report()
    # The arguments carry the token in a header, so failures name the shape, not the values.
    args = json.loads(record.read_text())
    assert args[:2] == ["mcp", "add"], f"add-pipeshub.sh ran `claude {' '.join(args[:2])} ...`, not `claude mcp add`"
    assert args[args.index("--transport") + 1] == "http", "add-pipeshub.sh no longer registers an http transport"
    headers, positional, i = {}, [], 2
    while i < len(args):
        if args[i] in ("--transport", "--scope"):
            i += 2
        elif args[i] == "--header":
            key, _, value = args[i + 1].partition(":")
            headers[key.strip()] = value.strip()
            i += 2
        else:
            positional.append(args[i])
            i += 1
    assert len(positional) == 2, f"expected `<name> <url>`, got {positional}"
    return positional[1], headers


async def _search_over_http(url: str, headers: dict[str, str], needle: str) -> tuple[list[str], str]:
    async with Client(StreamableHttpTransport(url, headers=headers)) as client:
        return await _search(client, needle)


async def _search(client: Client, needle: str) -> tuple[list[str], str]:
    tools = [t.name for t in await client.list_tools()]
    if SEARCH_TOOL not in tools:
        return tools, ""
    # Not call_tool: a failed call should be reported with what the server said, not raised.
    result = await client.call_tool_mcp(SEARCH_TOOL, {"query": needle})
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    if result.isError:
        return tools, f"the tool call failed: {text}"
    return tools, text + json.dumps(result.structuredContent or {})


class TestCompanyKnowledgeMcp:
    @pytest.mark.parametrize("client_name", ["cursor", "codex", "claude-code"])
    def test_http_config_reaches_pipeshub(
        self, client_name, examples_dir, examples_base_url, examples_token, seeded_record, tmp_path,
    ) -> None:
        env = _mcp_env(examples_dir, examples_base_url, examples_token)
        try:
            if client_name == "cursor":
                url, headers = _cursor(examples_dir, env)
            elif client_name == "codex":
                url, headers = _codex(examples_dir, env)
            else:
                url, headers = _claude_code(examples_dir, env, tmp_path)
        except (KeyError, IndexError, ValueError) as e:
            pytest.fail(f"{client_name}: the config no longer has the shape its client reads: {e!r}")

        try:
            tools, found = asyncio.run(_search_over_http(url, headers, seeded_record["needle"]))
        except Exception as e:  # noqa: BLE001 - reported with what the config sent
            pytest.fail(f"{client_name}: could not use PipesHub at {url} with headers {sorted(headers)}: {e!r}")
        assert SEARCH_TOOL in tools, f"{client_name}: connected to {url}, but no {SEARCH_TOOL} in {tools}"
        assert seeded_record["name"] in found or seeded_record["needle"] in found, (
            f"{client_name}: {SEARCH_TOOL} at {url} did not return the seeded record:\n{found[:3000]}"
        )

    def test_claude_desktop_config_reaches_pipeshub(
        self, examples_dir, examples_base_url, examples_token, seeded_record,
    ) -> None:
        if shutil.which("npx") is None:
            pytest.skip("npx is not on PATH, and Claude Desktop starts the MCP package with it")
        path = examples_dir / MCP_DIR / "claude-desktop/claude_desktop_config.json"
        server = json.loads(path.read_text())["mcpServers"]["pipeshub"]
        args = list(server["args"])
        for flag in ("--server-url", "--bearer-auth"):
            assert flag in args, f"{path.name} no longer passes {flag}: {args}"
        # Keep the documented path, which is what readers copy; only the host is this stack's.
        url_at = args.index("--server-url") + 1
        args[url_at] = _on_this_stack(args[url_at], examples_base_url)
        args[args.index("--bearer-auth") + 1] = examples_token
        # Launched without a terminal, as Claude Desktop does: npx then installs without asking.
        transport = StdioTransport(server["command"], args, env=example_env({}))

        async def connect() -> tuple[list[str], str]:
            async with Client(transport) as client:
                return await _search(client, seeded_record["needle"])

        tools, found = asyncio.run(connect())
        assert SEARCH_TOOL in tools, f"the MCP package started, but has no {SEARCH_TOOL} in {tools}"
        assert seeded_record["name"] in found or seeded_record["needle"] in found, (
            f"claude-desktop: {SEARCH_TOOL} through --server-url {args[url_at]} did not return "
            f"the seeded record:\n{found[:3000]}"
        )


class TestBuildPacks:
    def test_packs_cite_records_in_this_demo_data(self, examples_dir) -> None:
        """A pack's questions name demo records; removing one here breaks that pack."""
        script = examples_dir / ".github/scripts/check_packs.py"
        if not script.is_file():
            pytest.skip("this examples checkout has no pack checks yet (pipeshub-ai/examples#7)")
        result = run(
            [sys.executable, str(script)],
            cwd=examples_dir, env={"PIPESHUB_DEMO_FIXTURE": str(DEMO_FIXTURE)}, timeout=120,
        )
        assert result.exit_code == 0, result.report()
