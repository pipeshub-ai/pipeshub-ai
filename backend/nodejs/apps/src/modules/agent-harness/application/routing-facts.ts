import type { RoutingFacts } from '../../control-plane';
import { LIVENESS_FEATURE } from '../domain/worker-compat';
import type { ChatSessionRepository } from '../ports/chat-session-repository';
import type { RoutingSubject } from '../ports/routing-decider';
import type { WorkerFeatureAvailability } from './worker-feature-availability';

export interface RoutingFactsOptions {
  /** Transactions, relay and worker are available in this process. */
  infrastructureReady: () => boolean;
  /** Where an existing conversation sits. Absent: not read. */
  sessions?: Pick<ChatSessionRepository, 'placement'>;
  /** Whether any worker was seen lately. Absent: one is taken to be. */
  liveness?: Pick<WorkerFeatureAvailability, 'live'>;
}

/**
 * What a turn's route is decided on beside policy (CPD-10 §2), each read by
 * the server: this process, the conversation's own record, the workers seen
 * lately. Never the browser's word.
 */
export class RoutingFactsReader {
  constructor(private readonly options: RoutingFactsOptions) {}

  /**
   * What is known without a read: the conversation taken as new and a
   * worker as live. A turn policy alone keeps on today's loop is decided on
   * these, as today's rollout decides it before it reads the conversation.
   */
  known(subject: RoutingSubject): RoutingFacts {
    return {
      infrastructureReady: this.options.infrastructureReady(),
      workerLive: true,
      scheduled: subject.scheduled === true,
      placement: null,
      modelKey: subject.model.modelKey ?? null,
      existing: subject.sessionId !== undefined,
      requested:
        subject.durableRequested === true
          ? 'durable'
          : subject.legacyRequested === true
            ? 'legacy'
            : null,
    };
  }

  async read(subject: RoutingSubject): Promise<RoutingFacts> {
    const [placement, workerLive] = await Promise.all([
      this.placement(subject),
      this.options.liveness?.live(LIVENESS_FEATURE) ?? true,
    ]);
    return { ...this.known(subject), placement, workerLive };
  }

  private async placement(
    subject: RoutingSubject,
  ): Promise<RoutingFacts['placement']> {
    const { sessions } = this.options;
    if (subject.sessionId === undefined || sessions === undefined) return null;
    // An unreadable placement routes nothing by itself: admission checks the session again.
    const placement = await sessions
      .placement({
        orgId: subject.orgId,
        actorId: subject.actorId,
        sessionId: subject.sessionId,
        ...(subject.agentKey !== undefined
          ? { agentKey: subject.agentKey }
          : {}),
      })
      .catch(() => null);
    if (placement === null) return null;
    if (placement.inProject && placement.projectId === undefined) {
      throw new Error("A project's conversation does not name its project");
    }
    return {
      durable: placement.durable,
      projectId: placement.inProject ? (placement.projectId ?? null) : null,
    };
  }
}
