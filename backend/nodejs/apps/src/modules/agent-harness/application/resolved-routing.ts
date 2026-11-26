import {
  assistantAgentLoop,
  classicAllowed,
  LEGACY_REASONS,
  routingOf,
  type LegacyReason,
  type Routing,
  type RunConfigPinner,
} from '../../control-plane';
import { HarnessRunConfigUnavailableError } from '../domain/errors';
import {
  CURRENT_HARNESS_MAJOR,
  type HarnessRolloutDecision,
  type HarnessRolloutReason,
} from '../domain/rollout';
import type { PinShadowMetrics } from '../ports/pin-shadow';
import type {
  ResolvedRoute,
  RoutingDecider,
  RoutingDecision,
  RoutingScope,
  RoutingSubject,
} from '../ports/routing-decider';
import { harnessLogMeta, type HarnessLogger } from './harness-log';
import type { RoutingFactsReader } from './routing-facts';

// Every reason the control plane routes a turn to today's loop for is a rollout reason, by its own name.
const rolloutReason = (reason: LegacyReason): HarnessRolloutReason => reason;

/** A pin's `routing` in the shape LAT1's callers read; a reason the table does not hold is no decision. */
export function rolloutDecision(routing: Routing): HarnessRolloutDecision {
  if (routing.path === 'durable') {
    return {
      useHarness: true,
      harnessMajor: CURRENT_HARNESS_MAJOR,
      reason: 'enabled',
    };
  }
  const reason = LEGACY_REASONS.find((known) => known === routing.reason);
  if (reason === undefined) {
    throw new Error('The route names a reason the routing table does not hold');
  }
  return {
    useHarness: false,
    harnessMajor: CURRENT_HARNESS_MAJOR,
    reason: rolloutReason(reason),
  };
}

export interface ResolvedRoutingOptions {
  pinner: Pick<RunConfigPinner, 'route'>;
  facts: RoutingFactsReader;
}

/**
 * The second `RoutingDecider` (CPD-10 §2): the route the run's pin will hold,
 * from the resolved rollout rows and the conversation's own facts. Policy is
 * asked first, on what needs no read; only a turn it lets through reads the
 * conversation and the workers, as today's rollout orders it. Whatever it
 * cannot resolve is `RUN_CONFIG_UNAVAILABLE`: under the pin a turn is never
 * handed to today's loop for want of a decision.
 */
export class ResolvedRoutingDecider implements RoutingDecider {
  constructor(private readonly options: ResolvedRoutingOptions) {}

  async decide(subject: RoutingSubject): Promise<RoutingDecision> {
    try {
      return await this.resolve(subject);
    } catch (error) {
      throw error instanceof HarnessRunConfigUnavailableError
        ? error
        : new HarnessRunConfigUnavailableError(
            "The turn's route could not be resolved",
          );
    }
  }

  private async resolve(subject: RoutingSubject): Promise<RoutingDecision> {
    const agentKey = subject.agentKey ?? null;
    const turn = {
      orgId: subject.orgId,
      actorId: subject.actorId,
      agentKey,
      chatMode: subject.model.chatMode,
      projectId: subject.projectId ?? null,
    };
    // An existing conversation's Agent-mode turn follows its record (ASSIST1): its mode is decided on it.
    const recordDecides =
      agentKey === null &&
      subject.sessionId !== undefined &&
      assistantAgentLoop(turn.chatMode) !== null;
    const first = recordDecides
      ? await this.options.facts.read(subject)
      : this.options.facts.known(subject);
    const route = await this.options.pinner.route({
      ...turn,
      routingFacts: first,
    });
    const resolved = (facts: ResolvedRoute['facts']): ResolvedRoute => ({
      facts,
      basis: route.basis,
      config: route.config,
    });
    if (route.routing.path !== 'durable') {
      return {
        routing: rolloutDecision(route.routing),
        scope: scopeOf(agentKey, turn.projectId),
        resolved: resolved(first),
      };
    }
    const facts = recordDecides
      ? first
      : await this.options.facts.read(subject);
    const projectId = facts.placement?.projectId ?? turn.projectId;
    const routing = routingOf(
      route.config,
      route.basis.families,
      { ...turn, projectId },
      facts,
    );
    // Admission honours the classic choice once it knows the agent can run on today's loop (CTL-7).
    const classic =
      routing.path === 'durable' &&
      facts.requested === 'legacy' &&
      classicAllowed(facts);
    return {
      routing: rolloutDecision(routing),
      scope: scopeOf(agentKey, projectId),
      resolved: resolved(facts),
      ...(classic ? { classicRequested: true as const } : {}),
    };
  }
}

const scopeOf = (
  agentKey: string | null,
  projectId: string | null,
): RoutingScope => ({
  ...(agentKey !== null ? { agentKey } : {}),
  ...(projectId !== null ? { projectId } : {}),
});

export interface ShadowRoutingOptions {
  /** Decides: today's rollout. */
  today: RoutingDecider;
  /** Compared: the resolved run configuration. */
  resolved: RoutingDecider;
  metrics: PinShadowMetrics;
  logger: HarnessLogger;
}

/**
 * `CONTROL_PLANE_PIN=shadow` for routing (CPD-10 §4): today's decider
 * answers; the resolved one runs after the answer and a difference in path
 * or reason is counted and logged, never acted on. The turn waits for
 * neither the comparison nor a resolution that fails.
 */
export class ShadowRoutingDecider implements RoutingDecider {
  private readonly running = new Set<Promise<unknown>>();

  constructor(private readonly options: ShadowRoutingOptions) {}

  async decide(subject: RoutingSubject): Promise<RoutingDecision> {
    const today = await this.options.today.decide(subject);
    const shadow = this.compare(subject, today);
    this.running.add(shadow);
    void shadow.finally(() => this.running.delete(shadow));
    return { ...today, shadow };
  }

  /** Settles once every comparison started so far has finished (tests, shutdown). */
  async idle(): Promise<void> {
    while (this.running.size > 0) await Promise.all([...this.running]);
  }

  private async compare(
    subject: RoutingSubject,
    today: RoutingDecision,
  ): Promise<ResolvedRoute | undefined> {
    let resolved: RoutingDecision;
    try {
      resolved = await this.options.resolved.decide(subject);
    } catch {
      this.count(subject, 'unavailable');
      return undefined;
    }
    if (
      resolved.routing.useHarness !== today.routing.useHarness ||
      resolved.routing.reason !== today.routing.reason
    ) {
      this.count(subject, 'routing');
    }
    return resolved.resolved;
  }

  private count(
    subject: RoutingSubject,
    dimension: 'routing' | 'unavailable',
  ): void {
    this.options.metrics.mismatch(dimension);
    this.options.logger.warn(
      'cp.shadow_mismatch',
      harnessLogMeta('cp.shadow_mismatch', {
        orgId: subject.orgId,
        sessionId: subject.sessionId,
        actorId: subject.actorId,
        agentKey: subject.agentKey,
        dimensions: [dimension],
      }),
    );
  }
}

/**
 * Routes a turn of an org with the harness on (ADR-OPS-08) by the resolved run configuration, and any other org's by
 * today's decider, which a release without the harness used: its classic turns never wait on a pin.
 */
export class OrgScopedRoutingDecider implements RoutingDecider {
  constructor(
    private readonly harnessOn: (orgId: string) => Promise<boolean>,
    private readonly resolved: RoutingDecider,
    private readonly today: RoutingDecider,
  ) {}

  async decide(subject: RoutingSubject): Promise<RoutingDecision> {
    return (await this.harnessOn(subject.orgId))
      ? this.resolved.decide(subject)
      : this.today.decide(subject);
  }
}
