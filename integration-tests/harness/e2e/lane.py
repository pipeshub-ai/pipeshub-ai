"""A running stack of the options-matrix lane (``deployment/e2e/run.sh matrix-up``) as the runner reaches it.

Everything goes through what an operator or a client has: Node's public API as the seeded admin, the scripted
model's and SaaS's control routes, the stack's process supervisor (``app_from_source.sh``), the product's own
journal reader in the app container (``journal_read.js``) and Node's log. Standard library only.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stack_signin import org_access_token

REPO = Path(__file__).resolve().parents[3]
RUN_SH = REPO / "deployment/e2e/run.sh"
KIND_MATRIX = REPO / "deployment/helm/tests/kind_matrix.sh"
HELM_STACKS = frozenset({"ST-2", "ST-6", "ST-7"})
STATE_DIR = Path(os.environ.get("E2E_STATE_DIR", str(Path.home() / ".cache/pxh-e2e")))
Json = dict[str, Any]


class StackError(RuntimeError):
    """The stack did not do what the lane needs from it (not a product finding by itself)."""


def wait_until(predicate: Callable[[], bool], *, timeout: float, what: str, every: float = 0.5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(every)
    raise StackError(f"timed out after {timeout:.0f}s waiting for {what}")


@dataclass
class HttpAnswer:
    status: int
    body: Any
    headers: dict[str, str]


class Http:
    def __init__(self, base: str, headers: dict[str, str] | None = None) -> None:
        self.base = base.rstrip("/")
        self.headers = dict(headers or {})

    def request(self, method: str, path: str, body: object | None = None, *, headers: dict[str, str] | None = None,
                timeout: float = 120) -> HttpAnswer:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for name, value in {**self.headers, **(headers or {})}.items():
            req.add_header(name, value)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    raw = response.read()
                    status, response_headers = response.status, dict(response.headers)
                break
            except urllib.error.HTTPError as error:
                raw, status, response_headers = error.read(), error.code, dict(error.headers)
                break
            except (ConnectionResetError, ConnectionRefusedError, urllib.error.URLError):
                # A scripted service restarting between rows; Node never answers like this.
                if attempt == 2:
                    raise
                time.sleep(2)
        try:
            parsed: Any = json.loads(raw) if raw else None
        except ValueError:
            parsed = raw.decode(errors="replace")
        return HttpAnswer(status, parsed, response_headers)

    def ok(self, method: str, path: str, body: object | None = None, **kw: Any) -> Any:  # noqa: ANN401
        answer = self.request(method, path, body, **kw)
        if not 200 <= answer.status < 300:
            raise StackError(f"{method} {path} -> {answer.status}: {str(answer.body)[:500]}")
        return answer.body


@dataclass
class Frame:
    id: str | None
    event: str | None
    data: Json

    @property
    def type(self) -> str:
        return str(self.data.get("type", self.event or ""))

    @property
    def name(self) -> str:
        return str(self.data.get("name", ""))


@dataclass
class Stream:
    """One SSE response as the client read it: its frames, and whether the client dropped it on purpose."""

    status: int
    frames: list[Frame] = field(default_factory=list[Frame])
    dropped: bool = False
    error: str | None = None

    @property
    def last_id(self) -> str | None:
        return next((f.id for f in reversed(self.frames) if f.id), None)


def read_sse(response: Any, stop: Callable[[Frame], bool] | None = None) -> Iterator[Frame]:  # noqa: ANN401
    event: str | None = None
    frame_id: str | None = None
    data: list[str] = []
    for raw in response:
        line = raw.decode().rstrip("\r\n")
        if line.startswith("id:"):
            frame_id = line[3:].strip()
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line and data:
            payload = "\n".join(data)
            parsed = json.loads(payload) if payload.startswith("{") else {"raw": payload}
            frame = Frame(frame_id, event, parsed)
            yield frame
            event, frame_id, data = None, None, []
            if stop is not None and stop(frame):
                return


class Api(Http):
    """Node's public API as the seeded admin (a session token, renewed when it lapses)."""

    def __init__(self, base: str, email: str, password: str) -> None:
        super().__init__(base)
        self.email, self.password = email, password
        self._token: str | None = None

    def token(self) -> str:
        if self._token is None:
            first = self._sign_in_step("/api/v1/userAccount/initAuth", {"email": self.email}, {})
            session = next((v for k, v in first.headers.items() if k.lower() == "x-session-token"), None)
            if not session:
                raise StackError(f"initAuth gave no session token ({first.status})")
            answer = self._sign_in_step("/api/v1/userAccount/authenticate", {
                "method": "password", "credentials": {"password": self.password}, "email": self.email,
            }, {"x-session-token": session})
            if answer.status != 200:
                raise StackError(f"authenticate -> {answer.status}: {str(answer.body)[:300]}")
            self._token = org_access_token(answer.body if isinstance(answer.body, dict) else {}, self._signed_in_call)
        return self._token

    def _signed_in_call(self, method: str, path: str, body: object | None, token: str) -> Any:  # noqa: ANN401
        answer = Http(self.base).request(method, path, body, headers={"Authorization": f"Bearer {token}"})
        if answer.status != 200:
            raise StackError(f"{path} -> {answer.status}: {str(answer.body)[:300]}")
        return answer.body

    def _sign_in_step(self, path: str, body: Json, headers: dict[str, str]) -> HttpAnswer:
        """A sign-in request, waited out when Node's per-minute sign-in limit refuses it (a lane signs in as
        several users and from several processes; the limit's window is one minute)."""
        answer = Http(self.base).request("POST", path, body, headers=headers)
        for _ in range(8):
            if answer.status != 429:
                break
            time.sleep(10)
            answer = Http(self.base).request("POST", path, body, headers=headers)
        return answer

    def request(self, method: str, path: str, body: object | None = None, *, headers: dict[str, str] | None = None,
                timeout: float = 120) -> HttpAnswer:
        auth = {"Authorization": f"Bearer {self.token()}", **(headers or {})}
        answer = super().request(method, path, body, headers=auth, timeout=timeout)
        if answer.status == 401:
            self._token = None
            auth["Authorization"] = f"Bearer {self.token()}"
            answer = super().request(method, path, body, headers=auth, timeout=timeout)
        return answer

    def stream(self, method: str, path: str, body: object | None = None, *, headers: dict[str, str] | None = None,
               stop: Callable[[Frame], bool] | None = None, timeout: float = 240) -> Stream:
        """Reads an SSE response until it ends, ``stop`` says so (a dropped connection), or the read times out."""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream")
        req.add_header("Authorization", f"Bearer {self.token()}")
        for name, value in (headers or {}).items():
            req.add_header(name, value)
        out = Stream(status=0)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                out.status = response.status
                if "text/event-stream" not in response.headers.get("Content-Type", ""):
                    out.error = f"not a stream: {response.read()[:300]!r}"
                    return out
                for frame in read_sse(response, stop):
                    out.frames.append(frame)
                    if stop is not None and stop(frame):
                        out.dropped = True
                        break
        except urllib.error.HTTPError as error:
            out.status, out.error = error.code, error.read().decode(errors="replace")[:500]
        except (TimeoutError, OSError) as error:
            out.error = f"{type(error).__name__}: {error}"
        return out


def multipart(field: str, filename: str, content: bytes, mime: str) -> tuple[bytes, str]:
    boundary = f"qa6{os.urandom(8).hex()}"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class Control(Http):
    """A scripted service's control routes (``X-E2E-Control``)."""

    def __init__(self, base: str, token: str) -> None:
        super().__init__(base, {"X-E2E-Control": token})


@dataclass
class Stack:
    run: str
    stack_id: str
    run_dir: Path
    env: dict[str, str]
    seed: Json
    api: Api
    model: Control
    saas: Control
    runtime: ComposeRuntime | KubeRuntime

    @classmethod
    def attach(cls, run: str, stack_id: str) -> Stack:
        run_dir = STATE_DIR / run
        env = dict(line.split("=", 1) for line in (run_dir / "stack.env").read_text().splitlines()
                   if "=" in line and not line.startswith("#"))
        seed = json.loads((run_dir / "seed.json").read_text())
        port = env["E2E_APP_PORT"]
        runtime = (KubeRuntime if env.get("E2E_DEPLOY") == "helm" else ComposeRuntime)(run, run_dir, env)
        return cls(
            run=run, stack_id=stack_id, run_dir=run_dir, env=env, seed=seed, runtime=runtime,
            api=Api(f"http://127.0.0.1:{port}", env["E2E_ADMIN_EMAIL"], env["E2E_ADMIN_PASSWORD"]),
            model=Control(f"http://127.0.0.1:{env['E2E_LLM_PORT']}", env["E2E_LLM_CONTROL"]),
            saas=Control(f"http://127.0.0.1:{env['E2E_SAAS_PORT']}", env["E2E_LLM_CONTROL"]),
        )

    @property
    def org_id(self) -> str:
        return str(self.seed["orgId"])

    def upload(self, path: str, filename: str, content: bytes, mime: str = "text/markdown") -> list[Frame]:
        """A file upload as the web app sends it (multipart ``files``); the route answers with progress events."""
        body, content_type = multipart("files", filename, content, mime)
        req = urllib.request.Request(f"{self.api.base}{path}", data=body, method="POST")
        req.add_header("Content-Type", content_type)
        req.add_header("Authorization", f"Bearer {self.api.token()}")
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return list(read_sse(response))
        except urllib.error.HTTPError as error:
            raise StackError(f"upload {path} -> {error.code}: {error.read()[:400]!r}") from None

    # --- the stack's processes (Compose containers or Kubernetes pods) ---------------------

    def exec_app(self, *argv: str, timeout: float = 120) -> str:
        return self.runtime.exec_app(list(argv), timeout)

    def journal(self, session_id: str) -> Json:
        """The session's journal (unsealed by the product's own code), the org's audit verification and records."""
        return json.loads(self.exec_app(self.runtime.node, "/e2e/journal_read.js", self.org_id, session_id))

    def mongo(self, script: str) -> str:
        """``mongosh --eval`` on the stack's database."""
        body = f'const d = db.getSiblingDB({json.dumps(self.env["MONGO_DB_NAME"])}); {script}'
        return self.runtime.mongo(body)

    def node_records(self, request_id: str) -> list[Json]:
        """Node's structured log records of one request (``[req:<id>`` lines, each followed by its metadata)."""
        return self._node_records(lambda line: f"[req:{request_id} " in line)

    def node_events(self, event: str) -> list[Json]:
        """Node's structured log records of one harness event (the line's message), whatever request wrote them."""
        return self._node_records(lambda line: line.rsplit(" - ", 1)[-1] == event)

    def _node_records(self, wanted: Callable[[str], bool]) -> list[Json]:
        out: list[Json] = []
        lines = self.runtime.node_log().splitlines()
        for index, line in enumerate(lines):
            if not wanted(line):
                continue
            record: Json = {"message": line.rsplit(" - ", 1)[-1]}
            if index + 1 < len(lines) and lines[index + 1].startswith("metadata: {"):
                block = ["{"]
                for follow in lines[index + 2:]:
                    block.append(follow)
                    if follow == "}":
                        break
                with contextlib.suppress(ValueError):
                    record.update(json.loads("\n".join(block)))
            out.append(record)
        return out

    def set_process_env(self, env: dict[str, str]) -> bool:
        """Restarts Node, the worker and the query service with ``env`` when it differs from what they run with
        (04 §4.2 rows that set HARNESS_* or SANDBOX_* on the services)."""
        return self.runtime.set_process_env(env)


class ComposeRuntime:
    """A Compose stack of ``run.sh matrix-up``: containers by the project's labels, the supervisor's control files."""

    node = "/opt/node/bin/node"

    def __init__(self, run: str, run_dir: Path, env: dict[str, str]) -> None:
        self.project, self.run_dir, self.env = env.get("E2E_PROJECT", f"pxh-e2e-{run}"), run_dir, env
        self._process_env: dict[str, str] | None = None

    def container(self, service: str) -> str:
        found = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={self.project}",
             "--filter", f"label=com.docker.compose.service={service}"],
            check=True, capture_output=True, text=True,
        ).stdout.split()
        if not found:
            raise StackError(f"no running {service} container in {self.project}")
        return found[0]

    def exec_app(self, argv: list[str], timeout: float) -> str:
        done = subprocess.run(["docker", "exec", "-w", "/app/backend", self.container("pipeshub-ai"), *argv],
                              check=False, capture_output=True, text=True, timeout=timeout)
        if done.returncode != 0:
            raise StackError(f"{shlex.join(argv)} in the app container: {done.stderr[-800:]}")
        return done.stdout

    def mongo(self, body: str) -> str:
        if self.env.get("E2E_MONGO") == "standalone":
            argv = ["docker", "exec", self.container("pxh-mongo-standalone"), "mongosh", "--quiet", "--eval", body]
        else:
            container = os.environ.get("E2E_MONGO_CONTAINER", "pxh-mongo-rs")
            port = (self.run_dir / "mongo-port").read_text().strip()
            argv = ["docker", "exec", container, "mongosh", "--quiet", "--port", port, "--eval", body]
        done = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            raise StackError(f"mongosh: {done.stderr[-500:]}")
        return done.stdout

    def log(self, name: str) -> Path:
        return self.run_dir / "logs" / f"{name}.log"

    def node_log(self) -> str:
        return self.log("node").read_text(errors="replace")

    def _running_env(self) -> dict[str, str] | None:
        if self._process_env is None:
            files = [self.run_dir / "control" / f"{name}.env" for name in SETTINGS_PROCESSES]
            bodies = {f.read_text() for f in files if f.exists()}
            if len(bodies) == 1 and all(f.exists() for f in files):
                self._process_env = dict(line.split("=", 1) for line in bodies.pop().splitlines() if "=" in line)
        return self._process_env

    def set_process_env(self, env: dict[str, str]) -> bool:
        if env == self._running_env():
            return False
        control = self.run_dir / "control"
        body = "".join(f"{k}={v}\n" for k, v in sorted(env.items()))
        for name in SETTINGS_PROCESSES:
            (control / f"{name}.env").write_text(body)
            (control / f"restart-{name}.done").unlink(missing_ok=True)
            started = self.log(name).read_text(errors="replace").count(_READY[name]) if self.log(name).exists() else 0
            (control / f"restart-{name}").touch()
            wait_until(lambda n=name: (control / f"restart-{n}.done").exists(), timeout=120, what=f"{name} to stop")
            wait_until(lambda n=name, s=started: self.log(n).read_text(errors="replace").count(_READY[n]) > s,
                       timeout=300, what=f"{name} to start again")
        self._process_env = dict(env)
        return True


class KubeRuntime:
    """A Helm stack of ``deployment/helm/tests/kind_matrix.sh``: the app's Deployment, the chart's MongoDB, ``kubectl``.

    A process setting is the app Deployment's environment (what an operator changes with Helm values); changing it
    rolls the pod, after which ``kind_matrix.sh forward`` points the run's local ports at the new pod."""

    node = "node"

    def __init__(self, run: str, run_dir: Path, env: dict[str, str]) -> None:
        self.run, self.run_dir, self.env = run, run_dir, env
        self.kubectl = ["kubectl", "--context", env["E2E_KUBE_CONTEXT"], "-n", env["E2E_NAMESPACE"]]
        self._process_env: dict[str, str] | None = None

    def _run(self, argv: list[str], timeout: float, what: str) -> str:
        done = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=timeout)
        if done.returncode != 0:
            raise StackError(f"{what}: {done.stderr[-800:]}")
        return done.stdout

    def exec_app(self, argv: list[str], timeout: float) -> str:
        return self._run([*self.kubectl, "exec", f"deploy/{self.env['E2E_APP_DEPLOYMENT']}", "-c", "pipeshub-ai", "--",
                          "sh", "-c", f"cd /app/backend && exec {shlex.join(argv)}"], timeout, shlex.join(argv))

    def mongo(self, body: str) -> str:
        return self._run([*self.kubectl, "exec", self.env["E2E_MONGO_POD"], "--", "mongosh", "--quiet",
                          "-u", self.env["E2E_MONGO_USER"], "-p", self.env["E2E_MONGO_PASSWORD"],
                          "--authenticationDatabase", "admin", "--eval", body], 60, "mongosh")

    def node_log(self) -> str:
        return self._run([*self.kubectl, "logs", f"deploy/{self.env['E2E_APP_DEPLOYMENT']}", "-c", "pipeshub-ai",
                          "--tail", "-1"], 120, "kubectl logs")

    def set_process_env(self, env: dict[str, str]) -> bool:
        if env == self._process_env:
            return False
        deployment = f"deploy/{self.env['E2E_APP_DEPLOYMENT']}"
        self._run([*self.kubectl, "set", "env", deployment, *(f"{k}={v}" for k, v in sorted(env.items()))], 60,
                  "kubectl set env")
        self._run([*self.kubectl, "rollout", "status", deployment, "--timeout", "20m"], 1300, "kubectl rollout status")
        self._run(["bash", str(KIND_MATRIX), "forward", self.run], 300, "kind_matrix.sh forward")
        self._process_env = dict(env)
        return True


_READY = {"worker": "worker.started", "query": "Application startup complete", "node": "Server started on port"}
# The processes that read a row's settings (``ProcessEnv``): Node too, which pins the deployment's flow rule into each
# run it admits (QAFINAL-FU-PW012: with it left on, PW-012's card named the flow rule its row had switched off).
SETTINGS_PROCESSES = ("node", "worker", "query")


def _script(stack_id: str) -> Path:
    return KIND_MATRIX if stack_id in HELM_STACKS else RUN_SH


def launch(stack_id: str, run: str, extra: dict[str, str] | None = None) -> None:
    """``run.sh matrix-up`` (Compose) or ``kind_matrix.sh up`` (Helm) for one stack profile; ``extra`` is what the
    bring-up reads besides (the MCP rows' ``E2E_MCP_HUB``)."""
    env = {**os.environ, **(extra or {}), "E2E_STACK": stack_id, "E2E_RUN": run, "E2E_STATE_DIR": str(STATE_DIR)}
    command = "up" if stack_id in HELM_STACKS else "matrix-up"
    done = subprocess.run(["bash", str(_script(stack_id)), command], env=env, check=False, capture_output=True, text=True)
    if done.returncode != 0:
        raise StackError(f"{_script(stack_id).name} {command} ({stack_id}) failed:\n{(done.stdout + done.stderr)[-3000:]}")


def tear_down(stack_id: str, run: str) -> None:
    env = {**os.environ, "E2E_STATE_DIR": str(STATE_DIR)}
    subprocess.run(["bash", str(_script(stack_id)), "down", run], env=env, check=False, capture_output=True, text=True)


def kind_available() -> bool:
    """A Helm stack can come up here: kind, kubectl and helm on the PATH and the app image to load."""
    import shutil

    image = os.environ.get("E2E_APP_IMAGE", "")
    if not image or not all(shutil.which(tool) for tool in ("kind", "kubectl", "helm")):
        return False
    return subprocess.run(["docker", "image", "inspect", image], check=False, capture_output=True).returncode == 0
