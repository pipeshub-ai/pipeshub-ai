import {
  assistantAgentLoopMode,
  HarnessRolloutDecision,
  legacyFor,
} from '../domain/rollout';
import { LIVENESS_FEATURE } from '../domain/worker-compat';
import {
  ChatSessionRepository,
  SessionPlacement,
} from '../ports/chat-session-repository';
import { HarnessRolloutPolicy } from '../ports/harness-rollout';
import {
  RoutingDecider,
  RoutingDecision,
  RoutingScope,
  RoutingSubject,
} from '../ports/routing-decider';
import type { WorkerFeatureAvailability } from './worker-feature-availability';

export interface RolloutRoutingOptions {
  rollout: HarnessRolloutPolicy;
  /** Where an existing conversation sits (UX3). Absent: not checked. */
  sessions?: Pick<ChatSessionRepository, 'placement'>;
  /** Whether any worker was seen lately (UX3). Absent: not checked. */
  liveness?: Pick<WorkerFeatureAvailability, 'live'>;
}

/**
 * Today's routing until CPD-10 (control-plane 05 §5): the org rollout and
 * the chat mode, then the conversation's own facts, never the browser's. A
 * project's turn stays on today's loop, which serves its knowledge; with no
 * worker seen lately, a turn that would start a durable conversation answers
 * on today's loop instead of queueing for nobody; a conversation already
 * durable stays durable, so its history never splits.
 */
export class RolloutRoutingDecider implements RoutingDecider {
  constructor(private readonly options: RolloutRoutingOptions) {}

  async decide(subject: RoutingSubject): Promise<RoutingDecision> {
    // An existing conversation's Agent-mode turn follows its record, whoever sends it and whatever it asks (ASSIST1).
    const recordDecides =
      subject.agentKey === undefined &&
      subject.sessionId !== undefined &&
      this.options.sessions !== undefined &&
      assistantAgentLoopMode(subject.model.chatMode) !== null;
    const recorded = recordDecides ? await this.placement(subject) : undefined;
    const durableRequested = recordDecides
      ? recorded?.durable === true
      : subject.durableRequested === true;
    const rollout = await this.options.rollout.decide({
      orgId: subject.orgId,
      model: subject.model,
      assistant: subject.agentKey === undefined,
      ...(durableRequested ? { durableRequested: true } : {}),
    });
    const scope: RoutingScope = {
      ...(subject.agentKey !== undefined ? { agentKey: subject.agentKey } : {}),
      ...(subject.projectId !== undefined
        ? { projectId: subject.projectId }
        : {}),
    };
    if (!rollout.useHarness) return { routing: rollout, scope };
    if (subject.projectId !== undefined) {
      return { routing: legacyFor('project_scope'), scope };
    }
    let durable = false;
    // The classic path is honoured only where the conversation is known to have no durable turns (ASSIST1).
    let classicAllowed = subject.sessionId === undefined;
    if (
      subject.sessionId !== undefined &&
      this.options.sessions !== undefined
    ) {
      const placement = recordDecides
        ? recorded
        : await this.placement(subject);
      if (placement?.inProject === true) {
        return {
          routing: legacyFor('project_scope'),
          scope: {
            ...scope,
            ...(placement.projectId !== undefined
              ? { projectId: placement.projectId }
              : {}),
          },
        };
      }
      durable = placement?.durable === true;
      classicAllowed = placement?.durable === false;
    }
    const routing = await this.live(subject, durable, rollout);
    const classic =
      routing.useHarness && subject.legacyRequested === true && classicAllowed;
    return {
      routing,
      scope,
      ...(classic ? { classicRequested: true as const } : {}),
    };
  }

  /** Where the conversation sits; an unreadable placement routes nothing by itself: admission checks the session again. */
  private placement(subject: RoutingSubject): Promise<SessionPlacement | null> {
    const sessions = this.options.sessions;
    if (sessions === undefined || subject.sessionId === undefined) {
      return Promise.resolve(null);
    }
    return sessions
      .placement({
        orgId: subject.orgId,
        actorId: subject.actorId,
        sessionId: subject.sessionId,
        ...(subject.agentKey !== undefined
          ? { agentKey: subject.agentKey }
          : {}),
      })
      .catch(() => null);
  }

  private async live(
    subject: RoutingSubject,
    durable: boolean,
    rollout: HarnessRolloutDecision,
  ): Promise<HarnessRolloutDecision> {
    if (
      durable ||
      subject.scheduled === true ||
      this.options.liveness === undefined ||
      (await this.options.liveness.live(LIVENESS_FEATURE))
    ) {
      return rollout;
    }
    return legacyFor('no_live_worker');
  }
}
