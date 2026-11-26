import {
  HarnessRunConfigUnavailableError,
  HarnessUnavailableError,
} from '../../domain/errors';
import type {
  RoutingDecider,
  RoutingDecision,
} from '../../ports/routing-decider';
import type { SubmitDurableRun } from './context';
import { AdmissionRefusal } from './refusals';

/**
 * Durable or legacy for one turn (LAT1's `RoutingDecider`), asked by every
 * admission path that starts a turn: submit, a schedule's firing and the
 * next-run drain. A decider that cannot decide refuses `unavailable`; it is
 * never read as a decision. One that could not resolve the run's
 * configuration keeps that refusal, which no caller answers with today's loop.
 */
export class RolloutGate {
  constructor(private readonly routing: RoutingDecider) {}

  async decide(
    request: Pick<
      SubmitDurableRun,
      | 'orgId'
      | 'actorId'
      | 'agentKey'
      | 'sessionId'
      | 'projectId'
      | 'input'
      | 'trigger'
      | 'durableRequested'
      | 'legacyRequested'
    >,
  ): Promise<RoutingDecision> {
    try {
      return await this.routing.decide({
        orgId: request.orgId,
        actorId: request.actorId,
        ...(request.agentKey !== undefined
          ? { agentKey: request.agentKey }
          : {}),
        ...(request.sessionId !== undefined
          ? { sessionId: request.sessionId }
          : {}),
        ...(request.projectId !== undefined
          ? { projectId: request.projectId }
          : {}),
        model: request.input.model,
        ...(request.trigger !== undefined ? { scheduled: true } : {}),
        ...(request.durableRequested === true
          ? { durableRequested: true }
          : {}),
        ...(request.legacyRequested === true ? { legacyRequested: true } : {}),
      });
    } catch (error) {
      throw new AdmissionRefusal(
        'unavailable',
        error instanceof HarnessRunConfigUnavailableError
          ? error
          : new HarnessUnavailableError(
              'Durable agent run admission availability could not be verified',
            ),
      );
    }
  }
}
