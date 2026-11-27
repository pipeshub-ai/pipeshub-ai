"""``EngineWorld`` with its engine on a real binary: E0's real lane under the durable engine kernel (recs2).

``RealEngineAssembler`` is ``MemoryEngineAssembler`` (the run's real tool endpoint, effect runner, approvals and
journal, in memory) with the attempt's controller on the lane: the release image of ``kind`` in ``broker``'s sandbox,
behind the stack's egress proxy, its tool calls through the stack's HTTP tool gateway. Its model calls reach the
world's scripted brain: on the stack's static model route (opencode's Chat Completions), or through E3's real model
gateway (``models``) on the world's pinned route, for engines that speak Responses (Codex) or Messages (Claude Code).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness_core.testing.engines.durable import MemoryEngineAssembler

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.engines.durable.ports import AssembledEngineRun, EngineAttempt

MODEL_KEY = "recs2-engine"


@dataclass
class RealEngineAssembler(MemoryEngineAssembler):
    stack: Any = None
    broker: Any = None
    kind: str = "opencode"
    # E3's real model gateway (a ``ModelGatewayLane``); None: the stack's static model route.
    models: Any = None
    # The content route the attempt writes to, when the journal's runs share one.
    content: Callable[[], Any] | None = None

    def controller(self, attempt: EngineAttempt) -> Any:  # noqa: ANN401 - an EngineController
        from app.harness.gateway.host import HttpToolGatewayHost
        from harness_core.engines.checkpoints import ObjectStoreEngineCheckpoints
        from harness_core.engines.controller import (
            EngineController,
            EngineControllerConfig,
        )
        from harness_core.sandbox.types import Resources
        from harness_core.skills.builder import StoredSkillBundleBuilder
        from harness_core.skills.materializer import TarSkillMaterializer
        from harness_core.workspace.sync.strategies import SyncStrategies
        from harness_core.workspace.sync.tar_sync import TarWorkspaceSync
        from harness_engines.release import release_registry
        from tests.unit.harness.engines.lane_releases import lane_release, lane_releases

        world, release = self.world, lane_release(self.kind)
        return EngineController(
            registry=release_registry(releases=lane_releases()),
            broker=self.broker,
            workspaces=world.store,
            sync_for=lambda scope: TarWorkspaceSync(objects=world.objects, scope=scope, clock=world.clock),
            strategies=SyncStrategies(),
            skill_bundles=StoredSkillBundleBuilder(source=world.skills, objects=world.objects, workspaces=world.store),
            skill_materializer=TarSkillMaterializer(objects=world.objects, workspaces=world.store),
            checkpoints=ObjectStoreEngineCheckpoints(world.objects),
            sink=attempt.sink,
            tool_host=HttpToolGatewayHost(self.stack.gateway.app, base_url=self.stack.gateway_base),
            clock=world.clock,
            placeholder_key=b"recs2-real-placeholder-key-0123456789",
            config=EngineControllerConfig(
                images={release.version: release.image},
                resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=256),
            ),
            writers=world.writers,
        )

    async def build(self, attempt: EngineAttempt) -> AssembledEngineRun:
        from harness_core.interactions.ports import RunBinding
        from tests.unit.harness.engines.real_stack import STATIC_MODEL_KEY

        built = await super().build(attempt)
        claim, gateway = attempt.claim, self.world.gateway
        if self.models is None:
            self.stack.models.register(gateway, STATIC_MODEL_KEY)
            self.stack.models.gateways[STATIC_MODEL_KEY] = gateway
        else:
            (route,) = self.world.routes
            gateway.binding = RunBinding(claim.org_id, claim.session_id, claim.run_id)
            self.models.pin(gateway, route, key=MODEL_KEY)
        self.stack.models.bind(claim.run_id, claim.attempt_id, built.tool_binding)
        if self.content is not None:
            built = dataclasses.replace(built, content=self.content())
        return built


def real_profile(kind: str, route: str = "route-azure-gpt") -> dict[str, Any]:
    """The pinned release profile of ``kind`` on the lane, in its wire form (an engine run's spec carries it)."""
    from harness_core.access.types import AccessRouteRef
    from harness_core.engines.profiles import profile_json
    from harness_engines.release import release_default_profile
    from tests.unit.harness.engines.lane_releases import lane_release

    return profile_json(release_default_profile(lane_release(kind).version, AccessRouteRef(route, "d1")))


__all__ = ["MODEL_KEY", "RealEngineAssembler", "real_profile"]
