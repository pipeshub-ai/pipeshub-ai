"""Round 10 integration: what WP-41 and WP-42 do to each other and to WP-35, pinned below any host.

R10-SEC-1 (WP-41 x WP-35) the quarantined summary masks every secret shape the shared pasted-input rules find, not
          only the vendor tokens: a password or a Stripe key quoted in a Slack thread never reaches the agent.
R10-SEC-2 (WP-42 x WP-35) a repository's own AGENTS.md or CLAUDE.md is quoted to the engine with the same shapes
          masked: the harness never puts a secret a repository committed into the engine's first message itself.
          Its provenance (digest and size) stays the file's.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_core.context.quarantine import QuarantinedSummarizer
from harness_core.engines.repository_instructions import (
    read_repository_instructions,
    render_repository_instructions,
)
from harness_core.models.transport_model import TransportModel
from harness_core.sandbox.errors import SandboxPathNotFound
from tests.unit.harness_core.context.test_ga28_quarantined_summarizer import (
    _messages,
    _reply,
    _Scripted,
    _text,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


VECTOR = Path(__file__).resolve().parents[5] / "contracts" / "harness" / "vectors" / "token-patterns.json"


def _pasted_examples() -> list[tuple[str, str, str]]:
    """``(kind, text, secret)`` for every example of the shared pasted-input rules: the secret is the last part, or
    for credentials in a URL the part between the user and the host."""
    rules = json.loads(VECTOR.read_text())["pastedInput"]
    found: list[tuple[str, str, str]] = []
    for detector in rules["detectors"]:
        found.extend(
            (detector["kind"], "".join(e), e[1] if detector["kind"] == "url_credentials" else e[-1])
            for e in detector.get("examples", [])
        )
    found.extend(("assigned", "".join(e), e[-1]) for e in rules["assigned"]["examples"])
    found.extend(("contextual", "".join(e), e[-1]) for e in rules["contextual"]["examples"])
    return found


EXAMPLES = _pasted_examples()


@pytest.mark.parametrize(("kind", "text", "secret"), EXAMPLES, ids=[f"{k}-{i}" for i, (k, _, _) in enumerate(EXAMPLES)])
async def test_r10_sec1_the_summary_masks_every_pasted_secret_shape(kind: str, text: str, secret: str) -> None:
    del kind
    reply = _reply(f"The thread says {text}.", [{"text": f"Someone wrote {text}", "refs": []}])
    summary = await QuarantinedSummarizer(TransportModel(_Scripted(lambda _p: _text(reply)))).summarize(
        _messages(2), request="standup"
    )
    shown = summary.model_dump_json()
    assert secret not in shown
    assert "The thread says" in shown


class _Files:
    id = "sbx-r10"

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    async def get_tar(self, src: str) -> AsyncIterator[bytes]:
        if src not in self.files:
            raise SandboxPathNotFound(src)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo(src.rsplit("/", 1)[-1])
            info.size = len(self.files[src])
            archive.addfile(info, io.BytesIO(self.files[src]))
        data = buffer.getvalue()

        async def stream() -> AsyncIterator[bytes]:
            yield data

        return stream()


@pytest.mark.parametrize(("kind", "text", "secret"), EXAMPLES, ids=[f"{k}-{i}" for i, (k, _, _) in enumerate(EXAMPLES)])
async def test_r10_sec2_repository_instructions_are_quoted_with_secrets_masked(kind: str, text: str, secret: str) -> None:
    del kind
    raw = f"# Deploy\n\nRun `make deploy`. Staging: {text}\n".encode()
    sandbox = _Files({"/workspace/AGENTS.md": raw})
    (found,) = await read_repository_instructions(sandbox, "/workspace")  # type: ignore[arg-type]
    rendered = render_repository_instructions((found,))
    assert secret not in rendered and secret not in found.text
    assert "Run `make deploy`." in rendered
    assert (found.sha256, found.size) == (hashlib.sha256(raw).hexdigest(), len(raw))

