import { performance } from 'perf_hooks';
import { codeSandboxProof } from '../../domain/capabilities';
import {
  engineKindOf,
  listedEngines,
  pinnedEngineLayer,
} from '../../domain/coding-engines';
import { offersFloor } from '../../domain/engine-isolation';
import {
  acpOptInOf,
  engineRef,
  PinnedEngineProfile,
  RELEASE_ENGINE_PROFILES,
  requestNarrowing,
  resolveEngineProfile,
} from '../../domain/engine-profiles';
import {
  HarnessConflictError,
  HarnessForbiddenError,
  HarnessGroupAccessRefusedError,
  HarnessModelAccessRefusedError,
  HarnessUnavailableError,
  HarnessOperatorControlRefusedError,
} from '../../domain/errors';
import {
  assistantAgentLoopMode,
  ASSISTANT_AGENT_DEFINITION_KEY,
} from '../../domain/rollout';
import {
  assertClientNarrowing,
  CODE_TASK_TOOL_FEATURE,
  ExecutorResolver,
  grantedModel,
  OPAQUE_ID,
} from '../../domain/run-spec';
import { OUTPUT_LOCALE_FEATURE } from '../../domain/output-locale';
import { KEYWORD_SEARCH_BUDGET_FEATURE } from '../../domain/worker-input';
import { OWNER_ONLY_EXECUTORS, SessionRole } from '../../domain/session-roles';
import {
  AGENT_CODE_FEATURE,
  DELIVERABLE_PIN_FEATURE,
  ENGINE_FEATURE,
  MCP_FIRST_USE_FEATURE,
  MODEL_FALLBACK_FEATURE,
  RELEASE_PIN_FEATURE,
  RUN_SURFACE_FEATURE,
} from '../../domain/worker-compat';
import type {
  CodeToolsWithheld,
  McpToolHeldNotice,
  McpUnavailableNotice,
} from '../../domain/execution';
import { MAX_HELD_NAMED } from '../../domain/agent-definition';
import type { AgentRunDefinition } from '../../domain/agent-definition';
import type { AgentAccessPolicy } from '../../ports/agent-access';
import type { CapabilitySource } from '../../ports/capability-probe';
import type { EngineAvailability } from '../../ports/engine-availability';
import type { EngineProfileCeilings } from '../../ports/engine-profiles';
import type { SessionRoleLookup } from '../../ports/session-roles';
import { elapsedMs, harnessLogMeta, HarnessLogger } from '../harness-log';
import { codeOf, type AdmissionCodingEngine } from '../coding-engine-service';
import type { MemoryService } from '../memory-service';
import type { SessionRepositoryService } from '../session-repository-service';
import type { ModelAccessService } from '../model-access-service';
import {
  engineExecutorAbsent,
  guardedFeatureState,
  servedOrUnknown,
  type GuardedFeatureState,
  type WorkerFeatureAvailability,
} from '../worker-feature-availability';
import { settledCapabilities } from '../capability-service';
import type { GroupAccess } from '../../ports/group-access';
import type { OperatorStops } from '../../ports/operator-stops';
import {
  EngineEligibility,
  offByOrg,
  type EngineIneligibility,
} from '../engine-eligibility';
import type { PolicyContext, SubmitDurableRun } from './context';
import { AdmissionPipeline, AdmissionStep, concurrently } from './pipeline';
import { AdmissionRefusal, refusal, refusedRole } from './refusals';

type PolicyStep = AdmissionStep<PolicyContext>;

/** The sender's role in the session, asked with the agent they address. */
export const roleIn = (
  roles: SessionRoleLookup,
  request: SubmitDurableRun,
  sessionId: string,
): Promise<SessionRole | null> =>
  roles.roleOf({
    orgId: request.orgId,
    actorId: request.actorId,
    sessionId,
    ...(request.agentKey !== undefined ? { agentKey: request.agentKey } : {}),
  });

/**
 * The definition an admission asks the agent authority for: the route's
 * agent, the system assistant definition for the assistant's Agent mode
 * (ASSIST1: routing admits that mode only when the turn asked and the org
 * allows it), or none for the assistant's search modes.
 */
export const definitionKeyFor = (
  request: Pick<SubmitDurableRun, 'agentKey' | 'input'>,
): string | undefined =>
  request.agentKey ??
  (assistantAgentLoopMode(request.input.model.chatMode) !== null
    ? ASSISTANT_AGENT_DEFINITION_KEY
    : undefined);

/**
 * MCP servers the system definition names with no tool: the run gets none of them, and the person reads each one, why,
 * and which server to sign in to (a sign-in only they can redo, here) or an admin approves (`not_approved`)
 * (MCPNOTE-1). An older authority sends no reason: the server is still named.
 */
export const unlistedMcpNotices = (
  definition: AgentRunDefinition,
): McpUnavailableNotice[] =>
  definition.agentKey === ASSISTANT_AGENT_DEFINITION_KEY
    ? definition.mcpServers
        // A server granted whole names its tools at the run's first use of it (D-LAZY-2): nothing to tell yet.
        .filter(
          (server) => server.tools.length === 0 && server.atFirstUse !== true,
        )
        .map((server) => {
          const why = server.unavailable;
          return {
            code: 'mcp_unavailable' as const,
            item: noticeText(server.name),
            ...(why === undefined ? {} : { reason: why.reason }),
            ...(((why?.reason === 'sign_in' && why.signInHere === true) ||
              why?.reason === 'not_approved') &&
            OPAQUE_ID.test(server.instanceId)
              ? { instance: server.instanceId }
              : {}),
          };
        })
    : [];

const NOTICE_ITEM_MAX = 200;
const noticeText = (value: string): string =>
  Array.from(value).slice(0, NOTICE_ITEM_MAX).join('');

/** D-MCP-5: each server of any agent whose tools the description scanner held back, with the tools it held. */
export const heldMcpNotices = (
  definition: AgentRunDefinition,
): McpToolHeldNotice[] =>
  definition.mcpServers
    .filter((server) => (server.held?.length ?? 0) > 0)
    .map((server) => ({
      code: 'mcp_tool_held' as const,
      item: noticeText(server.name),
      tools: (server.held ?? []).slice(0, MAX_HELD_NAMED).map(noticeText),
    }));

/** An admission that finds the turn belongs on today's loop after all; nothing has been written yet. */
export class RouteToLegacy extends Error {
  constructor(readonly reason: 'user_chose_classic') {
    super(`the turn answers on today's loop: ${reason}`);
    this.name = 'RouteToLegacy';
  }
}

/**
 * G-41 (GA-21): the sender is in one of the groups the org lets use the
 * harness, read live, first. Submit routes such a sender to today's loop
 * before it gets here (`GroupAccessRouting`); this step is what refuses a
 * readmitted next run, a firing and any path the router did not ask.
 * Without group access composed, nothing is limited.
 */
export class HarnessAccessStep implements PolicyStep {
  readonly name = 'harness_access';

  constructor(private readonly access: GroupAccess | undefined) {}

  async run(context: PolicyContext): Promise<void> {
    if (this.access === undefined) return;
    const { orgId, actorId } = context.request;
    let admitted: boolean;
    try {
      context.accessGroups = await this.access.policy(orgId);
      admitted = await this.access.admits(
        orgId,
        actorId,
        { kind: 'harness' },
        context.accessGroups,
      );
    } catch (error) {
      throw refusal(error, 'unavailable');
    }
    if (!admitted) {
      throw new AdmissionRefusal(
        'harness_group_required',
        new HarnessGroupAccessRefusedError('HARNESS_GROUP_REQUIRED'),
      );
    }
  }
}

/** What admission reads of the operator controls (ADR-OPS-01). */
export type OperatorAdmission = Pick<
  OperatorStops,
  'admissionStop' | 'stoppedEngines' | 'egressStopped'
>;

/**
 * ADR-OPS-01 D4.2: a conversation or agent an operator stopped takes no new
 * turn; the person is told the scope, never the admin's note. Org and
 * instance stops route to today's chat (the kill switch) before this step.
 */
export class OperatorStopStep implements PolicyStep {
  readonly name = 'operator_stop';

  constructor(private readonly controls: OperatorAdmission | undefined) {}

  async run(context: PolicyContext): Promise<void> {
    if (this.controls === undefined) return;
    const { orgId, sessionId, agentKey } = context.request;
    let stop;
    try {
      // A new chat has no session yet: only its agent can be stopped.
      stop = await this.controls.admissionStop({
        orgId,
        sessionId: sessionId ?? '',
        agentKey: agentKey ?? null,
      });
    } catch (error) {
      throw refusal(error, 'unavailable');
    }
    if (stop === null) return;
    throw new AdmissionRefusal(
      'operator_stopped',
      new HarnessOperatorControlRefusedError('OPERATOR_STOPPED', {
        scope: stop.scope.kind,
        reason: stop.reason,
      }),
    );
  }
}

/** What the agent grants this actor, asked with their own credential; null for built-in chat. */
export class AgentAuthorizer implements PolicyStep {
  readonly name = 'agent_authorizer';

  constructor(
    private readonly agentAccess: AgentAccessPolicy | undefined,
    private readonly logger?: HarnessLogger,
    private readonly workerFeatures?: Pick<WorkerFeatureAvailability, 'live'>,
  ) {}

  async run(context: PolicyContext): Promise<void> {
    const step = performance.now();
    try {
      // D-LAZY-2: only a live worker that lists MCP servers at first use gets a definition that lists none.
      const mcpFirstUse =
        (await this.workerFeatures?.live(MCP_FIRST_USE_FEATURE)) === true;
      context.agentDefinition = await this.authorize(
        context.request,
        mcpFirstUse,
      );
    } catch (error) {
      throw refusal(error, 'forbidden');
    }
    context.timing.authorizeMs = elapsedMs(step);
    this.honourClassic(context);
    this.noteUnlistedMcp(context);
  }

  /**
   * The person's switch asked for the classic path (CTL-7). An agent with an engine profile runs only on the
   * harness, so the request is ignored there, logged; the client is never trusted to pick a path the agent lacks.
   */
  private honourClassic(context: PolicyContext): void {
    if (context.classicRequested !== true) return;
    const engine = context.agentDefinition?.engineProfile;
    if (engine === undefined || engine === null)
      throw new RouteToLegacy('user_chose_classic');
    this.logger?.info(
      'run.classic_request_ignored',
      harnessLogMeta('run.classic_request_ignored', {
        orgId: context.request.orgId,
        sessionId: context.request.sessionId,
        agentKey: context.request.agentKey,
        reason: 'agent_needs_harness',
      }),
    );
  }

  /**
   * A server whose tools could not be named (discovery failed, or nothing approved under the MCP hub) is left out
   * of the run, and the person is told on the answer, never left to guess (CTL-5b).
   */
  private noteUnlistedMcp(context: PolicyContext): void {
    const definition = context.agentDefinition;
    const held = definition === null ? [] : heldMcpNotices(definition);
    const notices = definition === null ? [] : unlistedMcpNotices(definition);
    if (held.length > 0) {
      this.logger?.info(
        'run.mcp_tools_held',
        harnessLogMeta('run.mcp_tools_held', {
          orgId: context.request.orgId,
          sessionId: context.request.sessionId,
          reason: 'mcp_tool_description_flagged',
          servers: held.length,
          tools: held.reduce((sum, notice) => sum + notice.tools.length, 0),
        }),
      );
    }
    if (notices.length === 0 && held.length === 0) return;
    context.notices = [
      ...(context.notices ?? []).filter(
        (notice) =>
          typeof notice === 'string' || notice.code !== 'mcp_tool_held',
      ),
      ...held,
    ];
    if (notices.length === 0) return;
    this.logger?.info(
      'run.assistant_mcp_unavailable',
      harnessLogMeta('run.assistant_mcp_unavailable', {
        orgId: context.request.orgId,
        sessionId: context.request.sessionId,
        reason: 'mcp_tools_unnamed',
        servers: notices.length,
        reasons: [
          ...new Set(notices.map((notice) => notice.reason ?? 'unknown')),
        ]
          .sort()
          .join(','),
      }),
    );
    context.notices = [
      ...(context.notices ?? []).filter(
        (notice) =>
          typeof notice === 'string' || notice.code !== 'mcp_unavailable',
      ),
      ...notices,
    ];
  }

  private async authorize(
    request: SubmitDurableRun,
    mcpFirstUse: boolean,
  ): Promise<AgentRunDefinition | null> {
    const agentKey = definitionKeyFor(request);
    if (agentKey === undefined) return null;
    if (this.agentAccess === undefined) {
      throw new HarnessUnavailableError(
        'Agent access cannot be verified for durable runs',
      );
    }
    if (!request.authorization) throw new HarnessForbiddenError();
    let definition: AgentRunDefinition | null;
    try {
      definition = await this.agentAccess.authorizeRun({
        orgId: request.orgId,
        actorId: request.actorId,
        agentKey,
        authorization: request.authorization,
        ...(request.agentKey !== undefined && request.agentVersion !== undefined
          ? { agentVersion: request.agentVersion }
          : {}),
        ...(mcpFirstUse ? { mcpFirstUse } : {}),
      });
    } catch {
      throw new HarnessUnavailableError('Agent access could not be verified');
    }
    if (definition === null) throw new HarnessForbiddenError();
    return definition;
  }
}

/** The client may only narrow what the agent grants (P-14). */
export const clientNarrowing: PolicyStep = {
  name: 'client_narrowing',
  run: (context) =>
    Promise.resolve().then(() => {
      try {
        assertClientNarrowing(context.agentDefinition, context.request.input);
      } catch (error) {
        throw refusal(error, 'narrowing');
      }
    }),
};

/**
 * The agent's engine limits on its policy layer (ADR-SET-01), as the
 * snapshot the turn was routed on holds them, so no read of its own: the pin
 * folds the same layer, and code_task is offered on the authority the run is
 * pinned to. Without a routed snapshot (`CONTROL_PLANE_PIN` off or shadow)
 * the run is not pinned, and nothing reads an agent's policy layer.
 */
function agentPolicyEngine(context: PolicyContext): unknown {
  const agentKey = context.request.agentKey;
  if (agentKey === undefined) return undefined;
  const layer = context.routed?.basis.layers.get(`agent:${agentKey}`) as
    { engineProfile?: unknown } | undefined;
  return layer?.engineProfile;
}

/**
 * The engine authority of an engine agent's turn (E4a, CTL-6): release
 * defaults under the org ceiling, read now, the agent's narrowing (its
 * policy layer's, then its definition's) and the request's. The turn runs
 * on the loop either way; with the authority it offers `code_task`, whose
 * engine child narrows it. Anything that leaves no engine (a ceiling that
 * cannot be read or admits none, no image, no live engine worker) withholds
 * `code_task` and is logged, never refused. An agent without an engine
 * profile reads no ceiling. The default chat's Agent mode
 * takes its engine from the person's choice, else the org's default
 * (ENGPICK), as if the system definition named it, so it is pinned and
 * handed off exactly as an engine agent's. Whether the resolved engine may
 * start a session (an admin's switch, the person's groups, an operator's
 * stop) is `EngineEligibility`'s answer (WP-111), the one every engine choice
 * asks.
 */
export class EngineProfileStep implements PolicyStep {
  readonly name = 'engine_profile';

  constructor(
    private readonly ceilings: EngineProfileCeilings | undefined,
    private readonly availability: EngineAvailability | undefined,
    private readonly logger: HarnessLogger,
    private readonly workerFeatures?: Pick<
      WorkerFeatureAvailability,
      'live' | 'metrics'
    >,
    private readonly codingEngine?: AdmissionCodingEngine,
    private readonly releases: readonly PinnedEngineProfile[] = RELEASE_ENGINE_PROFILES,
    groupAccess?: GroupAccess,
    operator?: OperatorAdmission,
  ) {
    // The ceiling is read once below and handed to the check; this object holds its opt-in, the groups and operator stops.
    this.eligibility = new EngineEligibility({
      ceilings: ceilings ?? null,
      groupAccess: groupAccess ?? null,
      operator: operator ?? null,
      ...(releases !== RELEASE_ENGINE_PROFILES ? { releases } : {}),
      logger,
    });
  }

  /** WP-111: the org's switches, the person's groups and an operator's stops, as every engine choice asks them. */
  private readonly eligibility: EngineEligibility;

  async run(context: PolicyContext): Promise<void> {
    context.engineProfile = null;
    context.codeTask = undefined;
    context.codeTaskAutoRoute = undefined;
    await this.newChatEngine(context);
    // A `/run` uses PipesHub's code sandbox, never an engine (SLASH3 SD4).
    if (context.request.input.sandboxCommand !== undefined) return;
    const agent =
      (await this.agentEngine(context)) ??
      (await this.assistantEngine(context));
    if (agent === null) return;
    const orgId = context.request.orgId;
    if (this.ceilings === undefined) {
      this.withhold(context, 'unavailable');
      return;
    }
    const acpOptIn = acpOptInOf(this.ceilings);
    let ceiling: unknown;
    try {
      ceiling = await this.ceilings.read(orgId);
    } catch {
      this.withhold(context, 'unavailable');
      return;
    }
    const pin = await this.sessionPin(context);
    const pinned = pin === null ? null : pinnedEngineLayer(agent, pin);
    const request = requestNarrowing(
      context.request.input.model.reasoningEffort,
    );
    const agentPolicy = agentPolicyEngine(context);
    let chosen: { layer: unknown; profile: PinnedEngineProfile } | null = null;
    // The chat's version first (C14.01); one no longer allowed or installed moves the chat to the default.
    for (const layer of pinned === null ? [agent] : [pinned, agent]) {
      const decision = resolveEngineProfile(
        { org: ceiling, agentPolicy, agent: layer, request },
        this.releases,
        acpOptIn,
      );
      const ineligible =
        decision.refusal === null
          ? await this.ineligible(context, decision.profile, ceiling)
          : null;
      const unavailable =
        decision.refusal === null && ineligible === null
          ? await this.unavailable(orgId, decision.profile)
          : null;
      if (
        decision.refusal === null &&
        ineligible === null &&
        unavailable === null
      ) {
        chosen = { layer, profile: decision.profile };
        break;
      }
      // An admin's engine switch (WP-111) names the engine as off, not a profile that asks for too much.
      const refused =
        decision.refusal === null
          ? null
          : offByOrg(ceiling, layer, this.releases, acpOptIn)
            ? 'engine_off'
            : decision.refusal.code.toLowerCase();
      if (layer === pinned) {
        this.logger.info(
          'run.engine_version_moved',
          harnessLogMeta('run.engine_version_moved', {
            orgId,
            sessionId: context.request.sessionId,
            engine: pin,
            reason: refused ?? ineligible ?? unavailable,
          }),
        );
        continue;
      }
      if (decision.refusal !== null) {
        this.withhold(context, refused ?? 'engine_profile_widening', {
          dimension: decision.refusal.dimension,
          layer: decision.refusal.layer,
        });
      } else {
        this.withhold(context, ineligible ?? unavailable ?? 'unavailable');
      }
      return;
    }
    if (chosen === null) return;
    const decision = { profile: chosen.profile };
    if (!(await this.isolated(orgId, decision.profile))) {
      this.withhold(context, 'engine_isolation_unavailable', {
        isolationMin: decision.profile.isolationMin,
      });
      return;
    }
    if (!(await this.engineWorkerLive())) {
      this.workerFeatures?.metrics.withheld(CODE_TASK_TOOL_FEATURE);
      this.withhold(context, 'no_live_engine_worker');
      return;
    }
    context.engineProfile = decision.profile;
    context.codeTask = true;
    // The narrowing that chose the version is the one the run pins, so its engine child and its pin hold it.
    if (context.agentDefinition !== null) {
      context.agentDefinition = {
        ...context.agentDefinition,
        engineProfile: chosen.layer,
      };
    }
    const ran = engineRef(decision.profile.engine);
    if (ran !== pin) context.enginePin = ran;
    // DEEPROUTE: the org's switch for handing long work to the engine on its own; pinned with the authority.
    context.codeTaskAutoRoute =
      (await this.codingEngine?.autoRoute?.(orgId, context.request.actorId)) ??
      true;
  }

  /** The engine version this chat's turns run on (C14.01); null: a new chat, none yet, or it cannot be read. */
  private async sessionPin(context: PolicyContext): Promise<string | null> {
    const sessionId = context.request.sessionId;
    if (
      sessionId === undefined ||
      this.codingEngine?.sessionPin === undefined
    ) {
      return null;
    }
    try {
      return await this.codingEngine.sessionPin(
        context.request.orgId,
        sessionId,
      );
    } catch {
      return null;
    }
  }

  /**
   * The engine a new chat's first message names (CMDUI2), checked as `PUT …/coding-engine` checks it: in an
   * agent's chat, against the engines the definition this admission read with the caller's credential lists.
   * A refused choice is logged and dropped, so the turn runs on the default engine and nothing is saved.
   */
  private async newChatEngine(context: PolicyContext): Promise<void> {
    context.newChatEngine = undefined;
    const choice = context.request.codingEngine;
    if (
      choice === undefined ||
      context.request.sessionId !== undefined ||
      this.codingEngine === undefined
    ) {
      return;
    }
    const agentEngines =
      context.request.agentKey !== undefined
        ? listedEngines(context.agentDefinition?.engineProfile ?? null)
        : undefined;
    try {
      await this.codingEngine.assertSessionChoice(
        context.request.orgId,
        choice,
        agentEngines,
      );
    } catch (error) {
      this.logger.info(
        'run.coding_engine_choice_refused',
        harnessLogMeta('run.coding_engine_choice_refused', {
          orgId: context.request.orgId,
          agentKey: context.request.agentKey,
          engine: choice,
          reason: codeOf(error).toLowerCase(),
        }),
      );
      return;
    }
    context.newChatEngine = choice;
  }

  /**
   * An agent's engine narrowing; in a chat whose `/engine` chose one of the engines the profile lists, that one
   * (SLASH3 SD5). A choice the profile no longer lists is ignored: the profile is the owner's.
   */
  private async agentEngine(context: PolicyContext): Promise<unknown> {
    const profile: unknown = context.agentDefinition?.engineProfile ?? null;
    const engines = listedEngines(profile);
    const sessionId = context.request.sessionId;
    if (engines.length < 2 || this.codingEngine === undefined) return profile;
    if (sessionId === undefined && context.newChatEngine === undefined) {
      return profile;
    }
    let choice: string | null;
    try {
      choice =
        sessionId === undefined
          ? (context.newChatEngine ?? null)
          : await this.codingEngine.sessionChoice(
              context.request.orgId,
              sessionId,
            );
    } catch {
      return profile;
    }
    const chosen = engines.find((engine) => engineKindOf(engine) === choice);
    return chosen === undefined
      ? profile
      : { ...(profile as object), engines: [chosen] };
  }

  /**
   * The default chat's engine narrowing for this Agent-mode turn, written
   * onto the system definition the run pins; null: none chosen or usable.
   */
  private async assistantEngine(
    context: PolicyContext,
  ): Promise<{ engines: string[] } | null> {
    const definition = context.agentDefinition;
    if (
      this.codingEngine === undefined ||
      definition === null ||
      context.request.agentKey !== undefined ||
      definition.agentKey !== ASSISTANT_AGENT_DEFINITION_KEY
    ) {
      return null;
    }
    const chosen = await this.codingEngine.forTurn(
      context.request.orgId,
      context.request.actorId,
      context.request.sessionId,
      context.newChatEngine,
    );
    if (chosen.engine === null) {
      if (chosen.withheld !== undefined) {
        this.withhold(context, chosen.withheld, { source: chosen.source });
      }
      return null;
    }
    if (chosen.movedFrom !== undefined) {
      // WP-111: the chosen engine is off; the person reads which one their code work uses instead.
      context.notices = [
        ...(context.notices ?? []),
        {
          code: 'engine_switched',
          item: chosen.movedFrom.engine,
          engine: chosen.engine,
        },
      ];
      this.logger.info(
        'run.coding_engine_moved',
        harnessLogMeta('run.coding_engine_moved', {
          orgId: context.request.orgId,
          sessionId: context.request.sessionId,
          from: chosen.movedFrom.engine,
          engine: chosen.engine,
          source: chosen.source,
          reason: chosen.movedFrom.reason,
        }),
      );
    }
    const engineProfile = { engines: [chosen.engine] };
    context.agentDefinition = { ...definition, engineProfile };
    this.logger.info(
      'run.coding_engine_chosen',
      harnessLogMeta('run.coding_engine_chosen', {
        orgId: context.request.orgId,
        sessionId: context.request.sessionId,
        engine: chosen.engine,
        source: chosen.source,
      }),
    );
    return engineProfile;
  }

  /**
   * Why the resolved engine may not start a session for this person (WP-111), with the ceiling this step read.
   * G-41: a group policy that cannot be read refuses the turn, as before.
   */
  private async ineligible(
    context: PolicyContext,
    profile: PinnedEngineProfile,
    ceiling: unknown,
  ): Promise<EngineIneligibility | null> {
    try {
      return await this.eligibility.check(
        context.request.orgId,
        context.request.actorId,
        profile.engine,
        {
          ceiling,
          ...(context.accessGroups !== undefined
            ? { groups: context.accessGroups }
            : {}),
        },
      );
    } catch (error) {
      throw refusal(error, 'unavailable');
    }
  }

  /** A worker that runs engines (`HARNESS_ENGINE_RUNS`) and one that offers `code_task` are both live. */
  private async engineWorkerLive(): Promise<boolean> {
    if (this.workerFeatures === undefined) return true;
    const [engines, tool] = await Promise.all([
      this.workerFeatures.live(ENGINE_FEATURE),
      this.workerFeatures.live(CODE_TASK_TOOL_FEATURE),
    ]);
    return engines && tool;
  }

  private withhold(
    context: PolicyContext,
    reason: string,
    detail: {
      dimension?: string;
      layer?: string;
      source?: string;
      isolationMin?: string;
    } = {},
  ): void {
    // G-48: the turn still answers on the loop; the person reads that it ran without code tools, and why.
    context.notices = [
      ...(context.notices ?? []).filter(
        (notice) =>
          typeof notice === 'string' ||
          notice.code !== 'ran_without_code_tools',
      ),
      { code: 'ran_without_code_tools', item: codeToolsWithheld(reason) },
    ];
    this.logger.info(
      'run.code_task_withheld',
      harnessLogMeta('run.code_task_withheld', {
        orgId: context.request.orgId,
        sessionId: context.request.sessionId,
        reason,
        ...detail,
      }),
    );
  }

  /**
   * Why the engine never gets a run here, or null: no engine sandbox on this deployment (recs2: Helm has no
   * egress proxy or agent-sandbox yet; live workers prove none runs engines, as on an install without the broker,
   * whose silence alone proves nothing), or no image the broker can find or pull (H1, E9). The picker asks the same.
   */
  private async unavailable(
    orgId: string,
    profile: PinnedEngineProfile,
  ): Promise<'engine_sandbox_missing' | 'engine_unavailable' | null> {
    const engine = engineRef(profile.engine);
    if (await engineExecutorAbsent(this.workerFeatures)) {
      return 'engine_sandbox_missing';
    }
    if (this.availability === undefined) return null;
    let missing: ReadonlySet<string> | null;
    let sandboxMissing: boolean;
    try {
      [sandboxMissing, missing] = await Promise.all([
        this.availability.sandboxMissing?.() ?? Promise.resolve(false),
        this.availability.unavailable(),
      ]);
    } catch (error) {
      this.logger.warn(
        'engine_availability.unknown',
        harnessLogMeta('engine_availability.unknown', {
          orgId,
          engine,
          choice: 'admit',
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return null;
    }
    if (sandboxMissing) return 'engine_sandbox_missing';
    return missing?.has(engine) === true ? 'engine_unavailable' : null;
  }

  /**
   * An engine whose isolation floor no runtime of the broker meets (gVisor on a production install without runsc)
   * never gets a run; the worker's controller refuses it again (`gvisor_unavailable`). Unknown runtimes admit.
   */
  private async isolated(
    orgId: string,
    profile: PinnedEngineProfile,
  ): Promise<boolean> {
    if (this.availability?.modes === undefined) return true;
    let modes: ReadonlySet<string> | null;
    try {
      modes = await this.availability.modes();
    } catch (error) {
      this.logger.warn(
        'engine_availability.unknown',
        harnessLogMeta('engine_availability.unknown', {
          orgId,
          engine: engineRef(profile.engine),
          choice: 'admit',
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return true;
    }
    return modes === null || offersFloor(modes, profile.isolationMin);
  }
}

/** The withholding reasons that are about this server's engines, not the org's rules (G-48). */
const NOT_AVAILABLE: ReadonlySet<string> = new Set([
  'unavailable',
  'engine_sandbox_missing',
  'engine_unavailable',
  'no_live_engine_worker',
]);
const PAUSED: ReadonlySet<string> = new Set([
  'engine_version_stopped',
  'egress_stopped',
]);

/**
 * G-48: why code tools were withheld, as the person reads it: any withholding that is not this server's engines, an
 * admin's stop or the isolation floor is a rule of the org's (a ceiling, a model access rule, a group).
 */
export function codeToolsWithheld(reason: string): CodeToolsWithheld {
  if (NOT_AVAILABLE.has(reason)) return 'not_available';
  if (PAUSED.has(reason)) return 'paused_by_admin';
  if (reason === 'engine_isolation_unavailable') return 'isolation_unavailable';
  if (reason === 'engine_off') return 'engine_off';
  return 'not_allowed';
}

/**
 * The model access a turn names (A1). Every turn runs on the loop (CTL-6):
 * the access an engine child pins is decided when the loop hands off, so a
 * turn that may hand off pins none, and any other turn naming an account is
 * refused (an account is for engine runs only).
 */
export class ModelAccessStep implements PolicyStep {
  readonly name = 'model_access';

  constructor(
    private readonly access: Pick<ModelAccessService, 'decide'> | undefined,
  ) {}

  async run(context: PolicyContext): Promise<void> {
    const { request } = context;
    // CTL-6: every turn runs on the loop. A turn with an engine authority leaves its child's access (and the model
    // account the person chose for it) to the spawn; any other turn that names an account is refused here.
    if (
      context.codeTask === true ||
      request.input.model.accountId === undefined
    ) {
      context.modelAccess = null;
      return;
    }
    if (this.access === undefined) {
      throw new AdmissionRefusal(
        'unavailable',
        new HarnessUnavailableError('Model access cannot be decided here'),
      );
    }
    try {
      const decided = await this.access.decide({
        orgId: request.orgId,
        actorId: request.actorId,
        definition: context.agentDefinition,
        model: request.input.model,
        engineProfile: null,
      });
      context.modelAccess = decided?.access ?? null;
    } catch (error) {
      if (error instanceof HarnessModelAccessRefusedError) {
        throw new AdmissionRefusal(error.code, error);
      }
      throw refusal(error, 'unavailable');
    }
  }
}

/**
 * G-41: the configured model the turn runs on is one the sender's groups may
 * use: the model it names (an agent's granted one), else the agent's first,
 * else the org's default entry. Refused in plain words, never run on another.
 * The key read is the entry `ModelAccessService.resolveModel` resolves the
 * same input to, whatever account reaches it (SEC-C-02); the engine child's
 * spawn checks the groups again on the access it actually resolved.
 */
export class ModelGroupStep implements PolicyStep {
  readonly name = 'model_group';

  constructor(private readonly access: GroupAccess | undefined) {}

  async run(context: PolicyContext): Promise<void> {
    if (this.access === undefined) return;
    const { request } = context;
    const modelKey =
      context.modelAccess?.model?.modelKey ??
      grantedModel(context.agentDefinition, request.input.model).model
        ?.modelKey;
    let admitted: boolean;
    try {
      admitted = await this.access.admits(
        request.orgId,
        request.actorId,
        { kind: 'model', modelKey },
        context.accessGroups,
      );
    } catch (error) {
      throw refusal(error, 'unavailable');
    }
    if (!admitted) {
      throw new AdmissionRefusal(
        'model_group_required',
        new HarnessGroupAccessRefusedError(
          'MODEL_GROUP_REQUIRED',
          await this.access
            .allowedModelNames(request.orgId, request.actorId)
            .catch(() => undefined),
        ),
      );
    }
  }
}

/** How the run executes, as its claim will pin it; input artifacts need an engine. */
export class ExecutorStep implements PolicyStep {
  readonly name = 'executor';

  constructor(private readonly executors: ExecutorResolver) {}

  run(context: PolicyContext): Promise<void> {
    return Promise.resolve().then(() => {
      this.decide(context);
    });
  }

  private decide(context: PolicyContext): void {
    context.executor = this.executors({
      agentDefinition: context.agentDefinition,
      input: context.request.input,
      engineProfile: context.engineProfile,
      codeTask: context.codeTask === true,
    }).kind;
    // CTL-6: a turn that may hand off lists them to its loop and hands them to its engine child.
    if (
      context.executor !== 'engine' &&
      context.codeTask !== true &&
      (context.request.input.inputArtifacts?.length ?? 0) > 0
    ) {
      throw refusal(
        new HarnessConflictError(
          'INPUT_ARTIFACTS_REQUIRE_ENGINE',
          'Input artifacts can only be given to a code-task engine run',
        ),
        'input_artifacts',
      );
    }
  }
}

/**
 * An agent loop run admitted while the deployment may have a code sandbox
 * needs `agent_code` (P5.11), but only while a live worker can run it (DUR2):
 * a rolling deploy that upgrades Node first runs it as before. Unknown is not
 * none (ADR-CAP-01): a sandbox the last probe round could not ask about is
 * waited for once, boundedly, and then left to the claim, which pins
 * `agent-code` only on a proved sandbox; a worker feature no live worker has
 * yet shown either way is required, and relaxed by the sweeper if none serves it.
 */
export class AgentCodeStep implements PolicyStep {
  readonly name = 'agent_code';

  constructor(
    private readonly capabilities: CapabilitySource | undefined,
    private readonly workerFeatures:
      Pick<WorkerFeatureAvailability, 'live' | 'metrics'> | undefined,
    private readonly logger: HarnessLogger,
  ) {}

  async run(context: PolicyContext): Promise<void> {
    context.agentCode = undefined;
    if (context.executor !== 'loop' || context.agentDefinition === null) return;
    if (this.capabilities === undefined) return;
    const sandbox = codeSandboxProof(
      await settledCapabilities(this.capabilities, 'sandbox.isolation'),
    );
    if (sandbox === 'none') return;
    const served = await this.served(context.request);
    context.agentCode = served !== 'absent';
    if (sandbox === 'unknown' || served === 'unknown') {
      this.logger.info(
        'run.agent_code_pending',
        harnessLogMeta('run.agent_code_pending', {
          orgId: context.request.orgId,
          sessionId: context.request.sessionId,
          reason:
            sandbox === 'unknown' ? 'capabilities_unsettled' : 'no_worker_seen',
        }),
      );
    }
  }

  private async served(
    request: SubmitDurableRun,
  ): Promise<GuardedFeatureState> {
    if (this.workerFeatures === undefined) return 'live';
    const state = await guardedFeatureState(
      this.workerFeatures,
      AGENT_CODE_FEATURE,
    );
    if (state !== 'absent') return state;
    this.workerFeatures.metrics.withheld(AGENT_CODE_FEATURE);
    this.logger.info(
      'run.agent_code_withheld',
      harnessLogMeta('run.agent_code_withheld', {
        orgId: request.orgId,
        sessionId: request.sessionId,
        reason: 'no_live_worker',
      }),
    );
    return state;
  }
}

/**
 * FU4: the run's surface is pinned for the worker (main's MCP sign-in card is web-only) while a live worker reads
 * it (`run_surface`); a rolling deploy that upgrades Node first runs it as before, without the card. With no
 * availability wired it is never required.
 */
export class RunSurfaceStep implements PolicyStep {
  readonly name = 'run_surface';

  constructor(
    private readonly workerFeatures:
      Pick<WorkerFeatureAvailability, 'live' | 'metrics'> | undefined,
  ) {}

  async run(context: PolicyContext): Promise<void> {
    // Unwired, it stays off: the card is an upgrade, and a run without it is today's durable run.
    if (this.workerFeatures === undefined) return;
    context.runSurface = await servedOrUnknown(
      this.workerFeatures,
      RUN_SURFACE_FEATURE,
    );
    if (!context.runSurface) {
      this.workerFeatures.metrics.withheld(RUN_SURFACE_FEATURE);
    }
  }
}

/**
 * C2.07: the run's claim pins what it was admitted under (`x.pipeshub.release`: the `/pipeshub` contract, its
 * catalog digest and policy revision; an engine run's image digest) while a live worker reads it (`release_pin`).
 * A rolling deploy that upgrades Node first runs it as before, unpinned. With no availability wired it is off.
 */
export class ReleasePinStep implements PolicyStep {
  readonly name = 'release_pin';

  constructor(
    private readonly workerFeatures?: Pick<
      WorkerFeatureAvailability,
      'live' | 'metrics'
    >,
  ) {}

  async run(context: PolicyContext): Promise<void> {
    if (this.workerFeatures === undefined) return;
    context.releasePin = await servedOrUnknown(
      this.workerFeatures,
      RELEASE_PIN_FEATURE,
    );
    if (!context.releasePin) {
      this.workerFeatures.metrics.withheld(RELEASE_PIN_FEATURE);
    }
  }
}

/**
 * ADR-RUN-01 D1: the run's claim pins what it owes (`x.pipeshub.deliverable`), resolved once from its request,
 * while a live worker reads it (`deliverable_pin`). A rolling deploy that upgrades Node first runs it as before: the
 * worker resolves the deliverable itself when the run loads. With no availability wired it is off.
 */
export class DeliverablePinStep implements PolicyStep {
  readonly name = 'deliverable_pin';

  constructor(
    private readonly workerFeatures?: Pick<
      WorkerFeatureAvailability,
      'live' | 'metrics'
    >,
  ) {}

  async run(context: PolicyContext): Promise<void> {
    if (this.workerFeatures === undefined) return;
    context.deliverablePin = await servedOrUnknown(
      this.workerFeatures,
      DELIVERABLE_PIN_FEATURE,
    );
    if (!context.deliverablePin) {
      this.workerFeatures.metrics.withheld(DELIVERABLE_PIN_FEATURE);
    }
  }
}

/**
 * G-52: the run's input names the person's locale (the request's, or on a drained run's readmission the one its input
 * already holds) while a live worker reads it (`output_locale`); a rolling deploy that upgrades Node first runs it as
 * before, the model answering in the language the person writes in. With no availability wired it is off.
 */
export class OutputLocaleStep implements PolicyStep {
  readonly name = 'output_locale';

  constructor(
    private readonly workerFeatures?: Pick<
      WorkerFeatureAvailability,
      'live' | 'metrics'
    >,
  ) {}

  async run(context: PolicyContext): Promise<void> {
    const locale = context.request.locale ?? context.request.input.locale;
    if (this.workerFeatures === undefined || locale === undefined) return;
    if (await servedOrUnknown(this.workerFeatures, OUTPUT_LOCALE_FEATURE)) {
      context.outputLocale = locale;
    } else {
      this.workerFeatures.metrics.withheld(OUTPUT_LOCALE_FEATURE);
    }
  }
}

/**
 * R33JGAPS-FU-1: the run's input keeps the turn's `keywordSearchBudget` while a live worker reads it
 * (`keyword_search_budget`); a rolling deploy that upgrades Node first runs it as before, each search waiting for all
 * its keyword matches. With no availability wired it is off.
 */
export class KeywordSearchBudgetStep implements PolicyStep {
  readonly name = 'keyword_search_budget';

  constructor(
    private readonly workerFeatures?: Pick<
      WorkerFeatureAvailability,
      'live' | 'metrics'
    >,
  ) {}

  async run(context: PolicyContext): Promise<void> {
    if (
      this.workerFeatures === undefined ||
      context.request.input.keywordSearchBudget === undefined
    ) {
      return;
    }
    if (
      await servedOrUnknown(this.workerFeatures, KEYWORD_SEARCH_BUDGET_FEATURE)
    ) {
      context.keywordSearchBudget = true;
    } else {
      this.workerFeatures.metrics.withheld(KEYWORD_SEARCH_BUDGET_FEATURE);
    }
  }
}

/**
 * G-48: a loop run of an agent that grants more than one model pins its others as alternates while a live worker
 * moves a run whose provider is down (`model_fallback`, P4.7); otherwise one route, as before. With no availability
 * wired it is off.
 */
export class ModelFallbackStep implements PolicyStep {
  readonly name = 'model_fallback';

  constructor(
    private readonly workerFeatures?: Pick<
      WorkerFeatureAvailability,
      'live' | 'metrics'
    >,
  ) {}

  async run(context: PolicyContext): Promise<void> {
    context.modelFallback = undefined;
    if (this.workerFeatures === undefined) return;
    if ((context.agentDefinition?.models.length ?? 0) < 2) return;
    context.modelFallback = await this.workerFeatures.live(
      MODEL_FALLBACK_FEATURE,
    );
    if (!context.modelFallback) {
      this.workerFeatures.metrics.withheld(MODEL_FALLBACK_FEATURE);
    }
  }
}

/**
 * Engine work in a session someone else owns is refused before anything is
 * written, whatever the store would answer (SS1, v1: the owner's alone). Only
 * owner-only executors pay for the role read; without a role lookup it cannot
 * be decided, so it is refused rather than allowed.
 */
export class OwnerOnlyStep implements PolicyStep {
  readonly name = 'owner_only';

  constructor(private readonly roles: SessionRoleLookup | undefined) {}

  async run(context: PolicyContext): Promise<void> {
    const { request } = context;
    // CTL-6: a turn that may hand off to an engine is engine work too; for anyone else it is withheld, not refused.
    const handsOff = context.codeTask === true;
    if (
      request.sessionId === undefined ||
      (!OWNER_ONLY_EXECUTORS.has(context.executor) && !handsOff)
    ) {
      return;
    }
    const unavailable = new AdmissionRefusal(
      'unavailable',
      new HarnessUnavailableError(
        'Who may start work in this conversation could not be verified',
      ),
    );
    if (this.roles === undefined) throw unavailable;
    let role: SessionRole | null;
    try {
      role = await roleIn(this.roles, request, request.sessionId);
    } catch {
      throw unavailable;
    }
    const refused = refusedRole(role, handsOff ? 'engine' : context.executor);
    if (refused === null) return;
    if (!handsOff) throw refused;
    context.codeTask = undefined;
    context.engineProfile = null;
  }
}

/**
 * Governed memory (P7.3), decided with the sender's own credential and never
 * remembered; a steering message starts no run and gets none. Any failure
 * recalls nothing and grants no write.
 */
export class MemoryStep implements PolicyStep {
  readonly name = 'memory';

  constructor(
    private readonly memory: Pick<MemoryService, 'admission'> | undefined,
    private readonly logger: HarnessLogger,
  ) {}

  async run(context: PolicyContext): Promise<void> {
    const { request } = context;
    if (this.memory === undefined || request.steerOnly === true) return;
    try {
      context.memory = await this.memory.admission({
        orgId: request.orgId,
        actorId: request.actorId,
        ...(request.agentKey !== undefined
          ? { agentKey: request.agentKey }
          : {}),
        ...(request.sessionId !== undefined
          ? { sessionId: request.sessionId }
          : {}),
        ...(request.authorization !== undefined
          ? { authorization: request.authorization }
          : {}),
        ...(request.trigger !== undefined ? { unattended: true } : {}),
      });
    } catch (error) {
      this.logger.warn(
        'memory.recall_failed',
        harnessLogMeta('memory.recall_failed', {
          orgId: request.orgId,
          sessionId: request.sessionId,
          actorId: request.actorId,
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'memory_off',
        }),
      );
    }
  }
}

/** What admission reads and checks of a chat's repository (GIT-FLOW, WP-26). */
export type AdmissionRepositories = Pick<
  SessionRepositoryService,
  'assertNewChat' | 'stored'
>;

/**
 * GIT-FLOW (WP-26): a turn that hands work to an engine (an engine run, or a loop run that may hand off through
 * `code_task`) pins its chat's repository (`x.pipeshub.repository`); its code_task child inherits the pin. A new
 * chat's first message names one (the composer chip), checked as `PUT …/repository` checks it and refused with its
 * code: a chat without an engine is refused `REPOSITORY_NEEDS_ENGINE` before any sandbox. Any other turn reads
 * nothing.
 */
export class RepositoryStep implements PolicyStep {
  readonly name = 'repository';

  constructor(
    private readonly repositories: AdmissionRepositories | undefined,
    private readonly logger: HarnessLogger,
  ) {}

  async run(context: PolicyContext): Promise<void> {
    context.repository = undefined;
    context.newChatRepository = undefined;
    const { request } = context;
    const handsToEngine =
      context.executor === 'engine' || context.codeTask === true;
    if (request.sessionId === undefined) {
      await this.newChat(context, handsToEngine);
      return;
    }
    if (
      !handsToEngine ||
      this.repositories === undefined ||
      request.steerOnly === true
    ) {
      return;
    }
    let stored;
    try {
      stored = await this.repositories.stored(request.orgId, request.sessionId);
    } catch (error) {
      throw refusal(error, 'unavailable');
    }
    if (stored !== null) context.repository = stored;
  }

  private async newChat(
    context: PolicyContext,
    handsToEngine: boolean,
  ): Promise<void> {
    const { request } = context;
    const chosen = request.repository;
    if (chosen === undefined) return;
    if (this.repositories === undefined) {
      throw refusal(
        new HarnessUnavailableError('Chat repositories are not configured'),
        'unavailable',
      );
    }
    if (!handsToEngine) {
      this.logger.info(
        'run.repository_refused',
        harnessLogMeta('run.repository_refused', {
          orgId: request.orgId,
          agentKey: request.agentKey,
          reason: 'repository_needs_engine',
        }),
      );
      throw refusal(
        new HarnessConflictError(
          'REPOSITORY_NEEDS_ENGINE',
          'This chat has no coding engine to work on a repository',
        ),
        'repository',
      );
    }
    if (request.authorization === undefined) {
      throw refusal(new HarnessForbiddenError(), 'forbidden');
    }
    try {
      await this.repositories.assertNewChat(
        {
          orgId: request.orgId,
          actorId: request.actorId,
          authorization: request.authorization,
        },
        chosen,
      );
    } catch (error) {
      throw refusal(error, 'repository');
    }
    context.repository = chosen;
    context.newChatRepository = chosen;
  }
}

export interface PolicyStepsOptions {
  agentAccess?: AgentAccessPolicy;
  executors: ExecutorResolver;
  engineProfiles?: EngineProfileCeilings;
  engineAvailability?: EngineAvailability;
  modelAccess?: Pick<ModelAccessService, 'decide'>;
  capabilities?: CapabilitySource;
  workerFeatures?: Pick<WorkerFeatureAvailability, 'live' | 'metrics'>;
  roles?: SessionRoleLookup;
  memory?: Pick<MemoryService, 'admission'>;
  /** The default chat's coding engine (ENGPICK); absent: its Agent mode offers no engine. */
  codingEngine?: AdmissionCodingEngine;
  /** The release's engine versions; absent: this release's (tests give their own). */
  engineReleases?: readonly PinnedEngineProfile[];
  /** GIT-FLOW: chats' repositories; absent: a new chat that names one is refused (503), others pin none. */
  repositories?: AdmissionRepositories;
  logger: HarnessLogger;
  /** G-41: the org's group subjects for the harness, engines and models; absent: nothing is limited by group. */
  groupAccess?: GroupAccess;
  /** ADR-OPS-01: the operator controls; absent: nothing is stopped. */
  operatorControls?: OperatorAdmission;
  /** Steps that read every decision above (CPD-5's policy pinning), last. */
  after?: readonly PolicyStep[];
}

/**
 * What admission decides from policy for its sender: the agent's grant asked
 * with the sender's credential, the client's narrowing, the engine profile
 * under today's ceiling, model access, the executor's own refusals and
 * memory. Submit and the next-run drain run this same list.
 */
export function policyPipeline(
  options: PolicyStepsOptions,
): AdmissionPipeline<PolicyContext> {
  return new AdmissionPipeline<PolicyContext>([
    new HarnessAccessStep(options.groupAccess),
    new OperatorStopStep(options.operatorControls),
    new AgentAuthorizer(
      options.agentAccess,
      options.logger,
      options.workerFeatures,
    ),
    clientNarrowing,
    new EngineProfileStep(
      options.engineProfiles,
      options.engineAvailability,
      options.logger,
      options.workerFeatures,
      options.codingEngine,
      options.engineReleases,
      options.groupAccess,
      options.operatorControls,
    ),
    new ModelAccessStep(options.modelAccess),
    new ModelGroupStep(options.groupAccess),
    new ExecutorStep(options.executors),
    new AgentCodeStep(
      options.capabilities,
      options.workerFeatures,
      options.logger,
    ),
    new RunSurfaceStep(options.workerFeatures),
    new ReleasePinStep(options.workerFeatures),
    new DeliverablePinStep(options.workerFeatures),
    new ModelFallbackStep(options.workerFeatures),
    new OutputLocaleStep(options.workerFeatures),
    new KeywordSearchBudgetStep(options.workerFeatures),
    concurrently('participants', [
      new OwnerOnlyStep(options.roles),
      new MemoryStep(options.memory, options.logger),
    ]),
    // After the participants: a collaborator's turn whose hand-off was withheld pins no repository.
    new RepositoryStep(options.repositories, options.logger),
    ...(options.after ?? []),
  ]);
}
