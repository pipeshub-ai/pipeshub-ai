import {
  HarnessConflictError,
  HarnessEngineProfileRefusedError,
  HarnessForbiddenError,
  HarnessModelAccessRefusedError,
  HarnessNotFoundError,
  HarnessQuotaExceededError,
  HarnessSessionRoleRefusedError,
  HarnessGroupAccessRefusedError,
  HarnessOperatorControlRefusedError,
} from '../domain/errors';
import type {
  ScheduledAdmission,
  ScheduledAdmissionOutcome,
  ScheduledRunAdmission,
} from '../ports/schedules';
import type { RunAdmissionService } from './run-admission-service';

/** An admission refusal as a firing's reason code; anything unrecognised is an outage (retried). */
export function admissionRefusalReason(error: unknown): string {
  if (error instanceof HarnessForbiddenError) return 'agent_forbidden';
  if (error instanceof HarnessQuotaExceededError) return 'quota';
  if (error instanceof HarnessModelAccessRefusedError) return error.code;
  if (error instanceof HarnessEngineProfileRefusedError) {
    return error.code.toLowerCase();
  }
  if (error instanceof HarnessGroupAccessRefusedError) {
    return error.code.toLowerCase();
  }
  if (error instanceof HarnessSessionRoleRefusedError) {
    return error.code.toLowerCase();
  }
  if (error instanceof HarnessConflictError) return error.code.toLowerCase();
  // ADR-OPS-01: a stopped conversation or agent refuses a firing and a drained message as it refuses a turn.
  if (error instanceof HarnessOperatorControlRefusedError) {
    return error.code.toLowerCase();
  }
  if (error instanceof HarnessNotFoundError) return 'not_found';
  return 'unavailable';
}

/**
 * A firing's run through the ordinary admission path: rollout, the agent
 * authority asked with the actor's own credential, engine profile, model
 * access, quotas, then one admission transaction (session, message, run,
 * journal, outbox, receipt). A new session per firing.
 */
export class RunAdmissionScheduledRuns implements ScheduledRunAdmission {
  constructor(
    private readonly admissions: Pick<RunAdmissionService, 'submit'>,
  ) {}

  async admit(request: ScheduledAdmission): Promise<ScheduledAdmissionOutcome> {
    try {
      const outcome = await this.admissions.submit({
        orgId: request.orgId,
        actorId: request.actorId,
        ...(request.agentKey !== undefined
          ? { agentKey: request.agentKey }
          : {}),
        ...(request.agentVersion !== undefined
          ? { agentVersion: request.agentVersion }
          : {}),
        idempotencyKey: request.idempotencyKey,
        deadlineMs: request.deadlineMs,
        input: request.input,
        authorization: request.authorization,
        via: 'trigger',
        trigger: request.trigger,
        // A firing has no browser turn to ask for the harness, and the schedule itself is that ask: without it the
        // assistant's Agent mode routes a scheduled run to today's loop, which a firing cannot take.
        durableRequested: true,
        // The engine the schedule's chat chose, checked as any new chat's first message names one.
        ...(request.codingEngine !== undefined
          ? { codingEngine: request.codingEngine }
          : {}),
      });
      if (outcome.route === 'legacy') {
        return { kind: 'refused', reason: 'rollout_legacy' };
      }
      return {
        kind: 'admitted',
        runId: outcome.admission.run.runId,
        sessionId: outcome.admission.sessionId,
      };
    } catch (error) {
      return { kind: 'refused', reason: admissionRefusalReason(error) };
    }
  }
}
