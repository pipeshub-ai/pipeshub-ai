"""Liskov contract tests for `CodingSandboxBackend`.

Every implementation must pass this suite unchanged. Swapping `local` for
`docker` or `e2b` is a config change, so any behaviour that differs between
them is a bug in one of them — that is what these assert.

Tests take the `backend` fixture, which is parametrised over every backend
the machine can actually run (see `conftest.py`). Skip conditions belong in
the fixtures: a test body that can skip itself can quietly stop asserting.
"""

from __future__ import annotations

import pytest

from app.agent_loop_lib.sandbox.base import SandboxInfo
from app.agent_loop_lib.sandbox.coding.base import (
    CodeRequest,
    CodeResult,
    CodingSandboxBackend,
    ExecutionEvent,
    IsolationLevel,
    SandboxCapabilities,
    SandboxContext,
    SandboxRef,
)

_SECRET_MARKERS = ("api_key", "secret", "password", "token", "credential")


class TestLifecycleContract:
    async def test_sandbox_id_is_stable(self, backend: CodingSandboxBackend) -> None:
        assert isinstance(backend.sandbox_id, str) and backend.sandbox_id
        assert backend.sandbox_id == backend.sandbox_id

    async def test_provision_is_idempotent(self, backend: CodingSandboxBackend) -> None:
        """The manager's sweep and a re-entrant tool call can both provision
        an already-provisioned sandbox; neither may create a second one."""
        info = await backend.provision()
        assert isinstance(info, SandboxInfo)
        assert info.sandbox_id == backend.sandbox_id
        again = await backend.provision()
        assert again.sandbox_id == info.sandbox_id

    async def test_destroy_is_idempotent(self, backend: CodingSandboxBackend) -> None:
        """The manager destroys on teardown AND on provision failure; a
        double destroy must not raise or the whole teardown aborts."""
        await backend.destroy()
        await backend.destroy()


class TestExecutionContract:
    async def test_execute_success(self, backend: CodingSandboxBackend) -> None:
        result = await backend.execute(
            CodeRequest(code='console.log("hello")', language="typescript")
        )
        assert isinstance(result, CodeResult)
        assert result.exit_code == 0, result.stderr
        assert "hello" in result.stdout

    async def test_execute_python(self, backend: CodingSandboxBackend) -> None:
        result = await backend.execute(
            CodeRequest(code='print("hi from python")', language="python")
        )
        assert result.exit_code == 0, result.stderr
        assert "hi from python" in result.stdout

    async def test_failure_is_data_not_an_exception(
        self, backend: CodingSandboxBackend,
    ) -> None:
        """A failed run is `CodeResult`, never a raise — the model has to
        see it as something it can reflect on and retry."""
        result = await backend.execute(
            CodeRequest(code="this is not valid code %%%", language="typescript")
        )
        assert isinstance(result, CodeResult)
        assert result.exit_code != 0
        assert result.error_analysis is not None

    async def test_timeout_reports_exit_code_minus_one(
        self, backend: CodingSandboxBackend,
    ) -> None:
        result = await backend.execute(
            CodeRequest(code="while(true){}", language="typescript", timeout=2.0)
        )
        assert isinstance(result, CodeResult)
        assert result.exit_code == -1

    async def test_only_the_filesystem_persists_between_runs(
        self, backend: CodingSandboxBackend,
    ) -> None:
        """The state contract: a file written by one run is visible to the
        next; an interpreter variable is not. A backend offering richer
        semantics (E2B's stateful kernels) must not expose them here, or
        swapping backends would silently change behaviour."""
        first = await backend.execute(CodeRequest(
            code=(
                'open("persisted.txt", "w").write("kept")\n'
                'leaked = "should not survive"\n'
                'print("wrote")\n'
            ),
            language="python",
        ))
        assert first.exit_code == 0, first.stderr

        second = await backend.execute(CodeRequest(
            code=(
                'print("file:" + open("persisted.txt").read())\n'
                'print("var:" + str(globals().get("leaked")))\n'
            ),
            language="python",
        ))
        assert second.exit_code == 0, second.stderr
        assert "file:kept" in second.stdout
        assert "var:None" in second.stdout


class TestFilesystemContract:
    async def test_upload_download_roundtrip(
        self, backend: CodingSandboxBackend,
    ) -> None:
        await backend.upload_file("roundtrip.txt", b"hello")
        assert await backend.download_file("roundtrip.txt") == b"hello"

    async def test_list_files_sees_uploads(
        self, backend: CodingSandboxBackend,
    ) -> None:
        await backend.upload_file("listed.txt", b"data")
        assert "listed.txt" in await backend.list_files()

    @pytest.mark.parametrize(
        "path", ["../escape.txt", "/etc/passwd", "output/../../escape.txt"],
    )
    async def test_path_traversal_rejected(
        self, backend: CodingSandboxBackend, path: str,
    ) -> None:
        with pytest.raises(ValueError):
            await backend.upload_file(path, b"bad")

    async def test_output_dir_alias_is_normalised(
        self, backend: CodingSandboxBackend,
    ) -> None:
        """The tool contract tells the model to write to `$OUTPUT_DIR`, so
        it spells path arguments the same way; nothing expands a tool
        argument, so the backend has to."""
        await backend.upload_file("$OUTPUT_DIR/aliased.txt", b"via-alias")
        assert await backend.download_file("output/aliased.txt") == b"via-alias"

    async def test_artifacts_reported_once_not_re_reported(
        self, backend: CodingSandboxBackend,
    ) -> None:
        """`output/` persists across runs, so a naive listing would re-report
        the previous run's deliverables on every subsequent call and the
        agent would keep re-registering the same artifact."""
        first = await backend.execute(CodeRequest(
            code=(
                'import os\n'
                'open(os.path.join(os.environ["OUTPUT_DIR"], "first.txt"), "w").write("one")\n'
            ),
            language="python",
        ))
        assert first.exit_code == 0, first.stderr
        assert any("first.txt" in a for a in first.artifacts)

        second = await backend.execute(CodeRequest(
            code='print("no new files")', language="python",
        ))
        assert second.exit_code == 0, second.stderr
        assert not any("first.txt" in a for a in second.artifacts)


class TestCapabilitiesContract:
    async def test_capabilities_are_declared(
        self, backend: CodingSandboxBackend,
    ) -> None:
        caps = backend.capabilities
        assert isinstance(caps, SandboxCapabilities)
        assert isinstance(caps.isolation, IsolationLevel)
        assert "typescript" in caps.supported_languages

    async def test_network_denied_when_capability_says_unsupported(
        self, backend: CodingSandboxBackend,
    ) -> None:
        """`allow_network` on a request cannot widen what the backend
        declares — otherwise the capability is advisory and an operator who
        disabled network still gets it."""
        if backend.capabilities.supports_network:
            pytest.skip("backend supports network; nothing to deny")

        result = await backend.execute(CodeRequest(
            code=(
                'fetch("https://example.com")'
                '.then(() => console.log("NETWORK_REACHED"))'
                '.catch(() => console.log("NETWORK_BLOCKED"));'
            ),
            language="typescript",
            allow_network=True,
            timeout=20.0,
        ))
        assert "NETWORK_REACHED" not in result.stdout


class TestRefContract:
    async def test_ref_names_the_registry_key(
        self, backend: CodingSandboxBackend,
    ) -> None:
        """Reconnect is `registry.get(ref.backend).reconnect(ref)`, so the
        field has to be the registry key — a class name makes the ref
        useless for the one thing it exists to do."""
        from app.agent_loop_lib.sandbox.coding.factories import BUILTIN_FACTORIES

        ref = backend.ref
        assert isinstance(ref, SandboxRef)
        assert ref.sandbox_id == backend.sandbox_id
        assert ref.backend in {cls.backend_name for cls in BUILTIN_FACTORIES}

    async def test_ref_created_at_is_provision_time_not_now(
        self, backend: CodingSandboxBackend,
    ) -> None:
        """A `created_at` read at property-access time always reports "now",
        so no TTL computed from it could ever expire."""
        import time

        first = backend.ref.created_at
        time.sleep(0.05)
        assert backend.ref.created_at == first
        assert first <= time.time()


class TestStreamingContract:
    async def test_execute_stream_ends_with_exactly_one_result(
        self, backend: CodingSandboxBackend,
    ) -> None:
        events: list[ExecutionEvent] = []
        async for event in backend.execute_stream(
            CodeRequest(code='console.log("stream test")', language="typescript")
        ):
            events.append(event)

        assert events, "stream produced no events"
        assert events[-1].kind == "result"
        assert events[-1].result is not None
        assert sum(1 for e in events if e.kind == "result") == 1

    async def test_stream_result_matches_execute(
        self, backend: CodingSandboxBackend,
    ) -> None:
        code = 'console.log("same either way")'
        direct = await backend.execute(CodeRequest(code=code, language="typescript"))
        streamed = [
            e async for e in backend.execute_stream(
                CodeRequest(code=code, language="typescript")
            )
        ][-1].result
        assert streamed is not None
        assert streamed.exit_code == direct.exit_code
        assert streamed.stdout == direct.stdout


class TestSecretsContract:
    async def test_sandbox_info_carries_no_secrets(
        self, backend: CodingSandboxBackend,
    ) -> None:
        info = await backend.provision()
        dumped = str(info.metadata).lower()
        for marker in _SECRET_MARKERS:
            assert marker not in dumped, f"{marker!r} in SandboxInfo.metadata"

    async def test_ref_carries_no_secrets(
        self, backend: CodingSandboxBackend,
    ) -> None:
        dumped = backend.ref.model_dump_json().lower()
        for marker in _SECRET_MARKERS:
            assert marker not in dumped, f"{marker!r} in SandboxRef"


class TestCapabilitiesMatchTheirFactory:
    """The factory answers `capabilities()` before any instance exists, and
    the instance answers it at run time. Two literals would drift apart
    silently — the middleware decision is made from the factory's copy."""

    @pytest.mark.parametrize("backend_name", ["local", "docker", "e2b"])
    def test_factory_and_instance_agree(self, backend_name: str) -> None:
        from app.agent_loop_lib.sandbox.coding.factories import build_factory

        factory = build_factory(backend_name)
        instance = factory.create(SandboxContext())
        assert factory.capabilities() == instance.capabilities


class TestOutputDirPersistsAcrossRuns:
    """`$OUTPUT_DIR` is part of the sandbox filesystem, so it has to obey the
    same persistence contract as the working directory.

    Docker gives every `execute()` a fresh container and rebuilds its
    filesystem from the host working dir, so this is the case where a
    backend can silently diverge: on `local`, `output/` IS a working-dir
    subdirectory and persists for free; on `docker` it only persists if it
    is explicitly restored. A model that writes a deliverable and then reads
    it back to verify it — the normal pattern — breaks on one backend and
    not the other.
    """

    async def test_deliverable_written_in_one_run_is_readable_in_the_next(
        self, backend: CodingSandboxBackend,
    ) -> None:
        first = await backend.execute(CodeRequest(
            code=(
                'import os\n'
                'p = os.path.join(os.environ["OUTPUT_DIR"], "deliverable.txt")\n'
                'open(p, "w").write("payload")\n'
                'print("wrote")\n'
            ),
            language="python",
        ))
        assert first.exit_code == 0, first.stderr
        assert any("deliverable.txt" in a for a in first.artifacts)

        second = await backend.execute(CodeRequest(
            code=(
                'import os\n'
                'p = os.path.join(os.environ["OUTPUT_DIR"], "deliverable.txt")\n'
                'print("exists:" + str(os.path.exists(p)))\n'
                'print("content:" + open(p).read())\n'
            ),
            language="python",
        ))
        assert second.exit_code == 0, second.stderr
        assert "exists:True" in second.stdout
        assert "content:payload" in second.stdout

    async def test_reread_does_not_re_report_the_artifact(
        self, backend: CodingSandboxBackend,
    ) -> None:
        """Restoring `output/` must not make the previous run's deliverable
        look new again — the bridge would re-register it every turn."""
        await backend.execute(CodeRequest(
            code=(
                'import os\n'
                'open(os.path.join(os.environ["OUTPUT_DIR"], "once.txt"), "w").write("x")\n'
            ),
            language="python",
        ))
        second = await backend.execute(CodeRequest(
            code=(
                'import os\n'
                'print(open(os.path.join(os.environ["OUTPUT_DIR"], "once.txt")).read())\n'
            ),
            language="python",
        ))
        assert second.exit_code == 0, second.stderr
        assert not any("once.txt" in a for a in second.artifacts), second.artifacts
