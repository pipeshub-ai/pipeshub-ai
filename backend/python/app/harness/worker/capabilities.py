"""PipesHub's part of the worker advertisement (P1b RU-2): the extension shapes its models accept."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.contracts.generated.worker_capabilities import WorkerCapabilities
from harness_core.driver.capabilities import worker_capabilities
from harness_core.engines.durable.kernel import ENGINE_INPUTS, EXECUTOR_ENGINE

if TYPE_CHECKING:
    from app.harness.contracts.generated.pipeshub.enums import WorkerFeature
    from harness_core.contracts.generated.worker_capabilities import WorkerCapabilities
    from harness_core.logs import HarnessLog

# `chat`: the built-in assistant's `x.pipeshub.chat` pin (T1a); `caller`: the
# run input's caller behind a service credential (T1b); `deep_mode`: deep
# mode's loop and its sub-agent children, `x.pipeshub.subAgent` (K-L);
# `workspace_forks`: a child's `x.pipeshub.workspace` fork, merged at its parent's join (P6.4);
# `executor_engine`: a run pinned with an engine executor, run by the engine kernel (E1);
# `engine_inputs`: an engine run whose input names artifacts, found under `in/` (E1b);
# `session_roles`: a run in a shared session, its model seeing other participants'
# turns as a stub through the pinned `x.pipeshub.audience` (SS2);
# `turn_labels`: an audience listing other participants' runs whose label passed (P2.5a);
# `governed_memory`: a spec pinning memories (`x.pipeshub.memory`) and the remember tool (P7.3);
# `agent_code`: a loop run pinned `sandboxProfile: agent-code`, its `run_code` with the legacy
# sandbox hooks and receipts (P5.11); `inherited_labels`: a child whose spec pins its parent's label,
# `x.pipeshub.inheritedLabel`, which its source ledger starts from (UX2); `inherited_grants`: a child whose spec pins
# the conversation grants its parent held, `x.pipeshub.inheritedGrants`, which its approval policy holds (FLOWASK);
# `descriptor_pins`: a spec whose tool and MCP manifest pins carry `digestVersion: 2`, verified at claim (CPS);
# `resolved_config`: a spec carrying the run's pin (`x.pipeshub.resolved`), which the worker reads approvals, the
# flow rule, the model and the Labs stages from instead of the live documents (CPD-6).
# `child_models`: a stream holding a `child.spawned` that names the model a sub-agent role set (MA1);
# `assistant_agent`: a spec pinning the system assistant definition (`agentIdPlaceholder`), the built-in assistant's
# Agent mode, whose environment is built as Universal Agent Mode builds it (ASSIST1);
# `code_task_tool`: a loop run pinning its engine authority (`x.pipeshub.codeTask`), whose loop offers `code_task`
# (CTL-6). Every worker of this build offers it; whether engines run anywhere, Node decides (`executor_engine`).
# `command_input`: a run input naming the /command its query was expanded from (`input.command`, SLASH1).
# `compact_request`: a run input carrying the person's `/compact [focus]` (`input.compact`, SLASH2 C2), honoured
# before the run's first turn by the loop and by every engine adapter that can compact.
# `knowledge_selection`: a spec pinning a selection below app level or a saved agent's source limits in
# `x.pipeshub.knowledge`, which this worker resolves with main's turn scope every attempt (SYNC4).
# `release_pin`: a spec pinning what the run was admitted under (`x.pipeshub.release`: the /pipeshub contract, its
# catalog digest and policy revision) and an engine run's image digest (`pins.sandboxImage`), which the engine
# controller holds its sandbox to (C2.07, C2.20).
# `run_gates`: a spec pinning run gates (`gates`: a dry run, plan approval, a verify gate; G-03..G-05), and a stream
# holding what they journal (`tool.outcome.shadow`, a plan's `tool.denied.toolId`, `verification.recorded`).
# `run_surface`: a spec pinning where the run was started (`x.pipeshub.surface`), for main's MCP sign-in card (FU4).
# `sandbox_command`: a person's `/run` (`input.sandboxCommand`, run kind `command`, SLASH3): one `run_code` call, no
# model; it needs code execution, so a worker without it does not advertise it.
# `vendor_signin`: this process can carry a person's own vendor sign-in in an engine sandbox (SIGNGAP): the relay,
# the egress proxy's tunnels and the release's signed host list, as its engine controller is given them. Node pins a
# run on the actor's own plan only while a live worker advertises it, so a server without the add-on offers no
# sign-in and runs such a person's tasks on the org's model access instead of failing them in the engine.
# `trigger_context`: a scheduled run's input naming what fired it (`input.trigger`, TR-03): an engine finds it at
# `in/trigger.json`, the loop is told it in words. Every worker of this build reads it.
# `deliverable_pin`: a spec pinning what the run owes (`x.pipeshub.deliverable`: a file the request names or asks
# for, or an answer; ADR-RUN-01 D1), which the outcome ledger, the presenter and the engine's writing window read
# instead of parsing the request again. Every worker of this build reads it.
# `model_fallback`: this deployment moves a run whose model provider is down to an alternate route the run pinned
# (P4.7; a loop run in its kernel, an engine's calls in the Model Gateway, WP-100; ``HARNESS_MODEL_FALLBACK``, on by
# default); advertised only with it on, so Node pins an agent's other models as alternates only where they are used
# (G-48).
# `environment_pin`: a spec pinning the agent's environment (`x.pipeshub.environment`: a setup script, variables,
# cache folders, or a repository's devcontainer.json; C20.18), which the engine controller
# sets up or restores in each fresh sandbox. Every worker of this build reads it.
# `region_aware`: this worker knows regions (OWD-12) and always says where it is: it sends `workerRegion` when its
# region is set and none when it is not, which Node then places as no region's (`default`), never as its own; a worker
# that does not advertise it is older than regions and is Node's own region's (WP88-FU-2). Every worker of this build.
# `trace_content`: this deployment writes model content (prompts, answers, tool arguments) to its traces: tracing is on
# (``HARNESS_OTEL``) and so is the GenAI content opt-in (``OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT``), which
# the worker and the Model Gateway read from the same deployment settings. Advertised only then, so the admins' AI
# Access page says what the servers do, never what Node's own environment says (C411-FU-1; a split install's Node
# need not share them).
# `child_asks`: a child whose spec pins whether its parent can ask a person (`x.pipeshub.parentAsks`), which the
# engine's gateway reads to offer `ask_user` (ANSWER-CONTRACT-FU-3). Every worker of this build reads it.
# `mcp_first_use`: this worker lists each MCP server at the run's first use of it and pins it there (ADR-OWD-07
# D-LAZY-2): Node then asks the agent authority for a definition that lists nothing (`mcpFirstUse`), and may grant a
# server whole (`x.pipeshub.mcp[].tools: at_first_use`). Every worker of this build.
# `mcp_carried_pins`: a child whose spec carries its parent's first-use pin of an MCP server (`x.pipeshub.mcp[].pins`,
# ADR-OWD-07 D-LAZY-5), which this worker holds the server to and builds from the stored catalog without listing it
# again (R31HOT2). Every worker of this build.
# `keyword_search_budget`: a run whose input carries the chat turn's `keywordSearchBudget`, which this worker's knowledge
# searches read as today's loop does (R33JGAPS-FU-1). Every worker of this build.
# Each is a name of the contract's `WorkerFeature`, which Node reads too.
PIPESHUB_FEATURES: tuple[WorkerFeature, ...] = (
    "agent_code",
    "assistant_agent",
    "caller",
    "chat",
    "child_asks",
    "child_models",
    "code_task_tool",
    "command_input",
    "compact_request",
    "deep_mode",
    "deliverable_pin",
    "descriptor_pins",
    ENGINE_INPUTS,
    "environment_pin",
    EXECUTOR_ENGINE,
    "governed_memory",
    "inherited_grants",
    "inherited_labels",
    "keyword_search_budget",
    "knowledge_selection",
    "mcp_carried_pins",
    "mcp_first_use",
    "model_fallback",
    "output_locale",
    "region_aware",
    "release_pin",
    "resolved_config",
    "run_gates",
    "run_surface",
    "sandbox_command",
    "session_roles",
    "trace_content",
    "trigger_context",
    "turn_labels",
    "vendor_signin",
    "workspace_forks",
)

VENDOR_SIGNIN: WorkerFeature = "vendor_signin"
MODEL_FALLBACK: WorkerFeature = "model_fallback"
TRACE_CONTENT: WorkerFeature = "trace_content"

WORKER_CAPABILITIES = worker_capabilities(PIPESHUB_FEATURES)


def advertised(*, engines: bool, code: bool = True, signin: bool = False) -> WorkerCapabilities:
    """What this worker advertises: a worker composed without the engine kernel never takes an engine run, and one
    whose process has no code execution (``SANDBOX_MODE`` unset) never takes an ``agent-code`` run (QA6-F4): Node pins
    that profile only while a live worker advertises it, so the run is admitted without ``run_code`` instead of
    failing ``isolation_unavailable`` at claim. ``vendor_signin`` likewise only when its engines can carry a person's
    own sign-in (``carries_sign_ins``, SIGNGAP), ``model_fallback`` only while the fallback is on (G-48), and
    ``trace_content`` only while traces hold model content (C411-FU-1)."""
    withheld = (
        (() if engines else (EXECUTOR_ENGINE, ENGINE_INPUTS))
        + (() if code else ("agent_code", "sandbox_command"))
        + (() if engines and signin else (VENDOR_SIGNIN,))
        + (() if _model_fallback_on() else (MODEL_FALLBACK,))
        + (() if _content_traced() else (TRACE_CONTENT,))
    )
    if not withheld:
        return WORKER_CAPABILITIES
    return worker_capabilities(tuple(f for f in PIPESHUB_FEATURES if f not in withheld))


def _model_fallback_on() -> bool:
    from app.harness.assembly.model_policy import model_fallback_enabled

    return model_fallback_enabled()


def _content_traced() -> bool:
    from app.harness.telemetry import content_capture_enabled, tracing_enabled

    return tracing_enabled() and content_capture_enabled()


def code_execution_configured() -> bool:
    """This process can run generated code: ``SANDBOX_MODE`` names a backend it supports."""
    from app.sandbox.manager import SandboxUnavailableError, get_sandbox_mode

    try:
        get_sandbox_mode()
    except SandboxUnavailableError:
        return False
    return True


def log_capabilities(log: HarnessLog, advertised: WorkerCapabilities = WORKER_CAPABILITIES) -> None:
    log.info(
        "worker.capabilities",
        harnessMajor=advertised.harness_major,
        supportedSchemaVersions=[str(v.root) for v in advertised.supported_schema_versions],
        foldVersion=advertised.fold_version,
        features=[f.root for f in advertised.features],
    )


__all__ = ["MODEL_FALLBACK", "PIPESHUB_FEATURES", "TRACE_CONTENT", "VENDOR_SIGNIN", "WORKER_CAPABILITIES", "advertised", "code_execution_configured", "log_capabilities"]
