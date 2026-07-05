from __future__ import annotations

from app.agent_loop_lib.sandbox.base import ExecResult
from app.agent_loop_lib.sandbox.confinement import confine_command
from app.agent_loop_lib.sandbox.local import LocalSandbox

"""OS sandbox (Phase 3 taxonomy) — the shell/terminal-tools typed sandbox.
Reuses `LocalSandbox`'s subprocess execution wholesale and layers kernel-level
process-tree confinement (see sandbox/confinement.py) on top, so it degrades
to plain `LocalSandbox` behavior automatically wherever confinement isn't
available rather than failing.
"""


class ConfinedLocalSandbox(LocalSandbox):
    def __init__(self, working_dir: str | None = None, allow_network: bool = False) -> None:
        super().__init__(working_dir=working_dir)
        self._allow_network = allow_network

    async def _exec(self, cmd: list[str], timeout: float) -> ExecResult:
        confined_cmd = confine_command(cmd, self._working_dir, allow_network=self._allow_network)
        return await super()._exec(confined_cmd, timeout)
