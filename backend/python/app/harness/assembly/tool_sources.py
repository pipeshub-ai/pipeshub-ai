"""``ToolSource`` providers: the existing PipesHub tool code, one source per kind.

Each source registers what the legacy factory registers for its kind, through
the same functions (``PipesHubToolLoader``, ``MCPToolProvider``, the skills and
sandbox wiring, the artifact store), then states each tool's identity:

- a tool the grant names (connector toolsets, MCP, web search) carries its
  pinned ``toolId`` and is kept only if ``grants.tools`` lists it;
- a platform tool (knowledge, artifacts, internal tools, skills) is allowed by
  the spec's other fields (knowledge scope, skills, sandbox profile).

Domain-agent delegates are built by the assembler once the catalog is known
(``loop.build_composition``); planning and coordination tools (planExecute,
deep) have no source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from harness_core.contracts import canonical_digest
from harness_core.driver import ISOLATION_UNAVAILABLE, IsolationUnavailable
from harness_core.interactions.connect import ConnectNeed
from harness_core.logs import HarnessLog
from harness_core.provenance.rules import MCP_RULE, WEB_RULE
from harness_core.tools.tags import PROVIDER_TAG_KEY, PROVIDERS, TAG_PROVIDER_PLATFORM

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import JsonValue

    from app.agents.agent_loop.mcp_lazy import LazyMcpServers
    from app.agents.agent_loop.skills.provenance import RunSkillProvenance
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.tool_environment import ToolEnvironment
    from harness_core.models.transports.registry import TransportRegistry
    from harness_core.provenance.rules import ProvenanceRule
    from harness_core.tools.base import Tag
    from harness_core.tools.registry import ToolRegistry

Integrity = Literal["trusted", "internal", "external"]
PLATFORM = "pipeshub"
# Questions end the run with needs_input (REPORT 5.2).
ASKS_USER = frozenset({"internaltools__ask_user_question"})
WEB_TOOLS = {"dynamic__web_search": "web_search", "dynamic__fetch_url": "fetch_url"}


@dataclass(frozen=True)
class SourcedTool:
    name: str
    tool_id: str
    # The grant this tool needs; None for a platform tool.
    grant: str | None
    integrity: Integrity = "internal"
    # Observation group (``mcp:<instance>``) whose manifest it belongs to.
    group: str | None = None
    # Its item is not connected for the run's owner: listed, answering how to connect it (WS-09).
    needs_authentication: bool = False
    # That item, for the run to wait on while its owner connects it (G-12).
    connect: ConnectNeed | None = None
    # Its toolset when connected: what the run waits on instead should the vendor reject the saved sign-in during the
    # attempt (WP-64), rather than refuse every later call of it.
    reconnect: ConnectNeed | None = None
    # ADR-RUN-01 D3: what its results carry. No default: a tool without a rule cannot be declared.
    provenance: ProvenanceRule = field(kw_only=True)
    # D-LAZY-3: an MCP tool its server has not been listed for yet in this attempt: granted and named, not built
    # until first use, so nothing reads its effect, schema or digest before then.
    deferred: bool = field(default=False, kw_only=True)


class ToolSource(Protocol):
    name: str

    async def load(
        self, env: ToolEnvironment, run: RunContext, registry: ToolRegistry
    ) -> list[SourcedTool]: ...

    async def observe(self, registry: ToolRegistry) -> dict[str, JsonValue]:
        """Real digests of what this source loaded (manifests, bundles); journaled
        by the first attempt and compared by every later one."""
        ...


def _platform(name: str, tool_id: str) -> SourcedTool:
    """A platform tool, declared with the rule of what it returns (``platform_rule``)."""
    from app.harness.tool_provenance import platform_rule

    return SourcedTool(name, tool_id, None, provenance=platform_rule(name, tool_id))


def _path_parts(registry: ToolRegistry, name: str) -> list[str]:
    return [part for part in (registry.path_for_name(name) or "").split("/") if part]


def declared_provider(tags: Iterable[Tag]) -> str | None:
    """The provider a tool's registration declares (``provider`` tag), or None when it declares none or several."""
    declared = {tag.value for tag in tags if tag.key == PROVIDER_TAG_KEY}
    return next(iter(declared)) if len(declared) == 1 and declared <= PROVIDERS else None


class ToolsetSource:
    """Connector and internal toolsets plus the dynamic tools, via ``PipesHubToolLoader``."""

    name = "toolsets"

    async def load(
        self, env: ToolEnvironment, run: RunContext, registry: ToolRegistry
    ) -> list[SourcedTool]:
        from app.agents.agent_loop.entity_search_tool import (
            ENTITY_SEARCH_OFFERED_STATE_KEY,
            NAMED_ENTITIES_ENABLED_STATE_KEY,
            entity_search_tool_offered,
        )
        from app.agents.agent_loop.tool_loader import PipesHubToolLoader
        from app.agents.registry.toolset_registry import get_toolset_registry
        from app.harness.assembly.tool_environment import bound_instances
        from app.harness.tool_provenance import connector_rule, platform_rule
        from harness_core.tool_identity import granted_ref, type_key

        catalog = get_toolset_registry()
        if not catalog.get_all_toolsets():
            # The query service discovers at startup; a harness worker on its first run.
            catalog.auto_discover_toolsets()
        # Same skips as the factory; code execution comes from the sandbox source.
        skip_apps = {"coding_sandbox", "database_sandbox"}
        if env.context.has_knowledge:
            skip_apps |= {"retrieval", "knowledgehub"}
        # The factory's decision too, so KnowledgeGraph.tools_for_request offers search_by_entities as legacy does.
        state = env.context.tool_state
        from app.services.featureflag.platform_settings import (
            is_named_entity_extraction_enabled,
        )

        state[NAMED_ENTITIES_ENABLED_STATE_KEY] = await is_named_entity_extraction_enabled(state.get("config_service"))
        state[ENTITY_SEARCH_OFFERED_STATE_KEY] = entity_search_tool_offered(state)
        loaded = await PipesHubToolLoader().load(env.context, skip_apps=skip_apps)
        for tool_name in loaded.names():
            registry.adopt(loaded, tool_name)
        for group in loaded.toolsets():
            registry.register_toolset(group.name, group.description, list(group.tool_names))

        bound = bound_instances(env.context.agent_toolsets)
        unconnected = {item.instance: item.name for item in env.unavailable if item.kind == "toolset"}
        sourced: list[SourcedTool] = []
        undeclared: list[str] = []
        for tool_name in loaded.names():
            if tool_name in WEB_TOOLS:
                provider = env.web_search_provider or "default"
                tool_id = f"web@{provider}/{WEB_TOOLS[tool_name]}"
                sourced.append(
                    SourcedTool(tool_name, tool_id, f"web@{provider}/web_search", "external", provenance=WEB_RULE)
                )
                continue
            binding = env.context.tool_bindings.get(tool_name)
            if binding is not None:
                # The tool's own binding names its pinned id, never the first instance of its type.
                pinned = granted_ref(binding, env.granted_toolsets, bound)
                tool_id = pinned or f"{binding.kind}@{binding.instance or 'unknown'}/{binding.tool}"
                item = unconnected.get(binding.instance) if binding.instance is not None else None
                sourced.append(
                    SourcedTool(
                        tool_name,
                        tool_id,
                        tool_id,
                        provenance=connector_rule(tool_name, tool_id),
                        needs_authentication=item is not None,
                        connect=ConnectNeed(item, "toolset", binding.instance)
                        if item is not None and binding.instance is not None
                        else None,
                        reconnect=ConnectNeed(
                            _toolset_name(env.context.agent_toolsets, binding.instance, binding.kind),
                            "toolset",
                            binding.instance,
                            "sign_in_rejected",
                        )
                        if item is None and binding.instance is not None
                        else None,
                    )
                )
                continue
            parts = _path_parts(loaded, tool_name)
            group, method = (parts[-2], parts[-1]) if len(parts) >= 2 else ("dynamic", tool_name)
            provider = declared_provider(loaded.tags_for_name(tool_name))
            if provider == TAG_PROVIDER_PLATFORM.value:
                platform_id = f"{PLATFORM}@{group}/{method}"
                sourced.append(SourcedTool(tool_name, platform_id, None, provenance=platform_rule(tool_name, platform_id)))
                continue
            if provider is None:
                undeclared.append(tool_name)
            # A connector's tool built without a configured binding, or a tool whose provider is unknown: granted to no one.
            tool_id = f"{type_key(group)}@unknown/{method}"
            sourced.append(SourcedTool(tool_name, tool_id, tool_id, provenance=connector_rule(tool_name, tool_id)))
        if undeclared:
            HarnessLog.named(
                "app.harness.assembly", orgId=run.org_id, sessionId=run.session_id, runId=run.run_id
            ).warning("tools.provider_undeclared", tools=sorted(undeclared))
        return sourced

    async def observe(self, registry: ToolRegistry) -> dict[str, JsonValue]:
        # Nothing of its own: its granted tools are observed by tool id with every source's
        # (``Catalog.granted_digests``), and its platform tools are the release's code, which no pin names.
        return {}


class McpSource:
    """Attached MCP servers: listed at first use when ``lazy`` (D-LAZY-3, ``LazyMcpServers``), else via
    ``MCPToolProvider`` (live discovery at the claim)."""

    name = "mcp"

    def __init__(self, *, lazy: bool = False) -> None:
        self.lazy = lazy
        # Set by a lazy load: the run's servers, listed when the run first uses one.
        self.servers: LazyMcpServers | None = None
        self._instances: dict[str, list[str]] = {}
        self.live_digests: dict[str, dict[str, str]] = {}
        # The servers whose listing answered (WP-79), by instance id, with the label a person knows them by.
        self.answered: dict[str, str] = {}
        # With the hub off, the run's MCP tool pins (``mcp_tool_pins``): a tool listed at another digest is held, never
        # registered (WP-84). Under the hub the admin's approval governs, and a pin that moved from it is CPD-7's.
        self.pinned: dict[str, dict[str, str]] = {}
        # The run's granted tool ids: a server's manifest is observed over the tools the run holds, not every tool
        # the loader registered (WP79-FU-5).
        self._granted: frozenset[str] = frozenset()
        # Set by an eager load: the registry the claim's listing registered the servers' tools in.
        self._registry: ToolRegistry | None = None

    async def load(
        self, env: ToolEnvironment, run: RunContext, registry: ToolRegistry
    ) -> list[SourcedTool]:
        from app.agents.agent_loop.mcp_tool_loader import (
            MCPToolProvider,
            answered_listings,
            mcp_digests,
        )

        sourced = _unconnected_mcp(env, registry)
        self._granted = frozenset(run.tool_ids)
        if not env.context.mcp_servers:
            return sourced
        if self.lazy:
            from app.agents.agent_loop.mcp_lazy import LazyMcpServers

            self.servers = LazyMcpServers(env.context, base_pins=self.pinned, carried=run.mcp_carried_pins)
            for tool_name, tool_id, instance in await self.servers.register(
                registry, run.tool_ids, run.mcp_at_first_use
            ):
                sourced.append(
                    SourcedTool(
                        tool_name, tool_id, tool_id, "external", group=f"mcp:{instance}", provenance=MCP_RULE,
                        deferred=True,
                    )
                )
            return sourced
        before = set(registry.names())
        self._registry = registry
        # A server the parent listed is held to its pin (D-LAZY-5) whoever approves MCP tools here.
        await MCPToolProvider().load_into(
            registry, env.context, pinned={**self.pinned, **run.mcp_carried_pins},
            carried=frozenset(instance for instance, tools in run.mcp_carried_pins.items() if tools),
        )
        self.live_digests = mcp_digests(registry)
        self.answered = answered_listings(env.context, self.live_digests)
        for tool_name in sorted(set(registry.names()) - before):
            parts = _path_parts(registry, tool_name)
            if len(parts) != 3 or parts[0] != "mcp":
                continue
            instance, tool = parts[1], parts[2]
            tool_id = f"mcp@{instance}/{tool}"
            if tool_id in self._granted:
                self._instances.setdefault(instance, []).append(tool_name)
            sourced.append(
                SourcedTool(tool_name, tool_id, tool_id, "external", group=f"mcp:{instance}", provenance=MCP_RULE)
            )
        return sourced

    def pins(self) -> dict[str, dict[str, str]]:
        """What a child this run spawns inherits of each server (D-LAZY-5, R31HOT2): by the server's own tool name,
        the digest the run's listing served. Listed at first use, the first-use pins; listed at the claim (a deep run,
        lazy disclosure off), the claim's listing of the tools the run still holds (R38IDENT-FU-13), so its sub-agent
        builds them from the stored catalog that listing wrote instead of listing the server again."""
        if self.servers is not None:
            return self.servers.pins
        if self._registry is None:
            return {}
        from app.agents.agent_loop.mcp_tool_loader import mcp_digests

        return mcp_digests(self._registry)

    async def observe(self, registry: ToolRegistry) -> dict[str, JsonValue]:
        # A lazy server is not observed at an attempt's start, used or not (D-LAZY-3): its first-use pin holds it.
        if self.lazy:
            return {}
        # A tool the run is withholding is no longer registered (``Catalog.withhold``).
        return {
            f"mcp:{instance}": manifest_digest(registry, [name for name in names if registry.has(name)])
            for instance, names in sorted(self._instances.items())
        }


def mcp_tool_pins(run: RunContext) -> dict[str, dict[str, str]]:
    """The MCP tools' version-2 descriptor pins, per instance, by the server's own tool name."""
    pins: dict[str, dict[str, str]] = {}
    for pin in run.descriptor_pins:
        source, _, rest = pin.ref.partition("@")
        if pin.kind == "tool" and source == "mcp" and "/" in rest:
            instance, tool = rest.split("/", 1)
            pins.setdefault(instance, {})[tool] = pin.digest
    return pins


def _unconnected_mcp(env: ToolEnvironment, registry: ToolRegistry) -> list[SourcedTool]:
    """The granted tools of the MCP servers the run's owner has not connected, masked (WS-09). No observation
    group: there is no manifest to observe until the server is connected."""
    from app.agents.agent_loop.tool_approvals import never_reaches_a_server
    from app.harness.assembly.needs_authentication import UnconnectedMcpTool
    from harness_core.tools.errors import DuplicateToolNameError, DuplicateToolPathError

    sourced: list[SourcedTool] = []
    for item in env.unavailable:
        if item.kind != "mcp":
            continue
        for name, tool in item.tools:
            stand_in = UnconnectedMcpTool(instance=item.instance, server=item.name, tool=tool, name=name)
            try:
                registry.register_tool(stand_in)
            except (DuplicateToolNameError, DuplicateToolPathError):
                continue
            never_reaches_a_server(env.context, stand_in.path)
            tool_id = f"mcp@{item.instance}/{tool}"
            sourced.append(
                SourcedTool(
                    name,
                    tool_id,
                    tool_id,
                    "external",
                    provenance=MCP_RULE,
                    needs_authentication=True,
                    connect=ConnectNeed(item.name, "mcp", item.instance),
                )
            )
    return sourced


class MetaToolSource:
    """``list_toolsets`` / ``fetch_tools`` / ``search_tools``, registered as the
    factory registers them (``register_lazy_tool_meta_tools``); which of them
    the model sees is the turn shaper's disclosure decision."""

    name = "meta"

    async def load(
        self, env: ToolEnvironment, run: RunContext, registry: ToolRegistry
    ) -> list[SourcedTool]:
        from app.agents.agent_loop.lazy_tools_wiring import (
            register_lazy_tool_meta_tools,
        )

        before = set(registry.names())
        register_lazy_tool_meta_tools(registry, env.context)
        return [
            _platform(name, f"{PLATFORM}@builtin/{name}")
            for name in registry.names()
            if name not in before
        ]

    async def observe(self, registry: ToolRegistry) -> dict[str, JsonValue]:
        return {}


class FinalAnswerSource:
    """``final_answer``, when the deployment enables it (``PIPESHUB_ENABLE_FINAL_ANSWER``)."""

    name = "final_answer"

    async def load(
        self, env: ToolEnvironment, run: RunContext, registry: ToolRegistry
    ) -> list[SourcedTool]:
        from app.agents.agent_loop.factory import _register_final_answer_if_enabled

        before = set(registry.names())
        _register_final_answer_if_enabled(registry)
        return [
            _platform(name, f"{PLATFORM}@builtin/{name}")
            for name in registry.names()
            if name not in before
        ]

    async def observe(self, registry: ToolRegistry) -> dict[str, JsonValue]:
        return {}


class SkillsSource:
    """The skill tools, scoped to the run's skills, via the skills wiring."""

    name = "skills"

    def __init__(self) -> None:
        self._manager: object | None = None
        self._skills: tuple[str, ...] = ()
        self._digests: dict[str, str] = {}
        self._transports: TransportRegistry | None = None
        # What the run's skill writes are stamped with; the assembler binds it to the run's source ledger.
        self.provenance: RunSkillProvenance | None = None

    async def load(
        self, env: ToolEnvironment, run: RunContext, registry: ToolRegistry
    ) -> list[SourcedTool]:
        from app.agent_loop_lib.transport.registry import TransportRegistry
        from app.agents.agent_loop.skills_wiring import (
            build_skill_manager,
            register_skill_tools,
            skills_enabled,
        )
        from app.services.featureflag.platform_settings import is_skills_enabled

        if not skills_enabled() or not await is_skills_enabled(env.context.config_service):
            return []
        from app.agents.agent_loop.skills.labelled_manager import LabelledSkillManager
        from app.agents.agent_loop.skills.provenance import RunSkillProvenance

        # Filled with the run's model once the assembler has it (``use_transports``): the learning's
        # extractor resolves its transport lazily, at the end of the run.
        self._transports = TransportRegistry()
        self.provenance = RunSkillProvenance(run.run_id, run.session_id)
        manager = await build_skill_manager(env.context, self._transports, provenance=self.provenance)
        if manager is None:
            return []
        if run.skill_digests:
            from app.agents.agent_loop.skills.bundle_source import PinnedSkills
            from app.agents.agent_loop.skills.offer import skill_viewers
            from app.agents.agent_loop.skills.pinned_manager import PinnedSkillManager
            from app.harness.supply_chain.skills import SkillTrust

            agent_key = run.agent.agent_key if run.agent is not None else None
            pins = PinnedSkills(
                env.context.graph_provider,
                run.org_id,
                agent_key,
                viewers=skill_viewers(env.context),
                trust=SkillTrust(env.context.config_service),
            )
            manager = PinnedSkillManager(manager, pins, run.skill_digests)
            await manager.prime()
        manager = LabelledSkillManager(manager, env.context.tool_state)
        self._manager, self._skills, self._digests = manager, run.skills, run.skill_digests
        before = set(registry.names())
        register_skill_tools(registry, manager)  # type: ignore[arg-type]  # a wrapper with the manager's read surface
        return [
            _platform(name, f"{PLATFORM}@skills/{name}")
            for name in sorted(set(registry.names()) - before)
        ]

    def use_transports(self, transports: TransportRegistry, providers: Iterable[str]) -> None:
        """The skill manager's model calls (the learning's extractor) go to the run's model."""
        if self._transports is None:
            return
        for provider in providers:
            self._transports.register(provider, lambda provider=provider: transports.resolve(provider))

    @property
    def loaded(self) -> bool:
        return self._manager is not None

    @property
    def manager(self) -> object | None:
        """The run's skill manager once loaded: the hooks, the shaper and skill learning use the same one."""
        return self._manager

    def missing(self, run: RunContext) -> list[str]:
        if self._manager is None:
            return [f"skill:{name}" for name in run.skills]
        advertised = {m.name for m in self._manager.catalog_snapshot()}  # type: ignore[attr-defined]
        return [f"skill:{name}" for name in run.skills if name not in advertised]

    async def observe(self, registry: ToolRegistry) -> dict[str, JsonValue]:
        if self._manager is None:
            return {}
        observed: dict[str, JsonValue] = {}
        for name in self._skills:
            # A pinned skill is read at its pin whatever is stored now (FB-11), so its pin is what the run observes.
            digest = self._digests.get(name)
            if digest is None:
                skill = await self._manager.get_skill(name)  # type: ignore[attr-defined]
                digest = canonical_digest(skill.model_dump(mode="json"))
            observed[f"skill:{name}"] = digest
        return observed


class ArtifactSource:
    """``retrieve_artifact_content`` over the conversation's artifact store."""

    name = "artifacts"

    async def load(
        self, env: ToolEnvironment, run: RunContext, registry: ToolRegistry
    ) -> list[SourcedTool]:
        from app.agent_loop_lib.tools.builtin.data.retrieve_artifact import (
            RetrieveArtifactContentTool,
        )
        from app.agents.agent_loop.artifact_store import build_artifact_store

        # The artifact-registration hook stores large results in the same store.
        self.store = build_artifact_store(env.context)
        tool = RetrieveArtifactContentTool(store=self.store)
        registry.register_tool(tool)
        return [_platform(tool.name, f"{PLATFORM}@artifacts/{tool.name}")]

    async def observe(self, registry: ToolRegistry) -> dict[str, JsonValue]:
        return {}


def code_network(run: RunContext) -> bool:
    """Whether code the run executes may reach the network: the deployment allows it
    (``SANDBOX_ALLOW_NETWORK``, as legacy) and the pin grants egress (Node pins ``none``)."""
    from app.agents.agent_loop.sandbox_bridge import sandbox_network_enabled

    return run.egress != "none" and sandbox_network_enabled()


class SandboxSource:
    """``run_code`` and its companions, only when the pin grants a code sandbox."""

    name = "sandbox"

    async def load(
        self, env: ToolEnvironment, run: RunContext, registry: ToolRegistry
    ) -> list[SourcedTool]:
        if run.sandbox_profile != "agent-code":
            return []
        from app.agent_loop_lib.sandbox.coding.settings import SandboxUnavailableError
        from app.agents.agent_loop.sandbox_bridge import (
            build_coding_sandbox_manager,
            register_coding_sandbox_tools,
        )
        from app.modules.agents.qna.tool_system import code_execution_enabled

        if not code_execution_enabled(env.context.tool_state):
            return []
        network = code_network(run)
        try:
            manager = await build_coding_sandbox_manager(
                allow_network=network, ctx=env.context, run_id=run.run_id, attempt_id=run.attempt_id
            )
        except SandboxUnavailableError as exc:
            # The legacy chat drops run_code and carries on; a durable run was
            # pinned with it, so running without it would break the pin, and
            # falling back to the host would break isolation (X-3).
            HarnessLog.named(
                "app.harness.assembly", orgId=run.org_id, sessionId=run.session_id, runId=run.run_id
            ).warning(
                "run.sandbox_unavailable",
                reason=ISOLATION_UNAVAILABLE,
                sandboxProfile=run.sandbox_profile,
            )
            raise IsolationUnavailable(str(exc), reason="sandbox_unconfigured") from exc
        env.context.sandbox_manager = manager
        before = set(registry.names())
        register_coding_sandbox_tools(registry, manager, allow_network=network)
        # Registration order, as legacy offers them: the model is shown tools in this order (DUR1).
        return [
            _platform(name, f"{PLATFORM}@sandbox/{name}")
            for name in registry.names()
            if name not in before
        ]

    async def observe(self, registry: ToolRegistry) -> dict[str, JsonValue]:
        return {}


def descriptor_digest(registry: ToolRegistry, name: str) -> str:
    """What the model is told about a tool: its name, description and input schema."""
    schema = registry.resolve_by_name(name).to_schema()
    return canonical_digest(schema.model_dump(mode="json"))


def manifest_digest(registry: ToolRegistry, names: list[str]) -> str:
    return canonical_digest(
        {name: descriptor_digest(registry, name) for name in sorted(names)}
    )


def default_sources() -> list[ToolSource]:
    # The factory's registration order, so the grant (and the prompt) lists
    # tools in the same order.
    return [
        ToolsetSource(),
        McpSource(),
        MetaToolSource(),
        SandboxSource(),
        SkillsSource(),
        ArtifactSource(),
        FinalAnswerSource(),
    ]



def _toolset_name(agent_toolsets: list[dict[str, Any]], instance: str, kind: str) -> str:
    """The toolset as the person knows it, as its calls name it when they need it reconnected."""
    binding = next((t for t in agent_toolsets if t.get("instanceId") == instance), {})
    return str(binding.get("instanceName") or binding.get("displayName") or kind.title())

__all__ = [
    "ASKS_USER",
    "ArtifactSource",
    "FinalAnswerSource",
    "MetaToolSource",
    "McpSource",
    "SandboxSource",
    "SkillsSource",
    "SourcedTool",
    "ToolSource",
    "ToolsetSource",
    "code_network",
    "declared_provider",
    "default_sources",
    "descriptor_digest",
    "manifest_digest",
]
