import type { AuditFields } from './audit';
import { HarnessForbiddenError } from './errors';
import { principalRefFor } from './run-spec';
import type { RunTrigger } from './schedules';

/**
 * Who a run acts as (OWD-2, D11, REPORT §6.13): a person, or an agent's
 * service account (`isServiceAccount` agents), which uses the agent's own
 * credentials for everyone who talks to it.
 */
export type PrincipalKind = 'user' | 'agent_service';

/**
 * A run's attribution, fixed at admission from verified auth only, kept on
 * the run and recorded in `run.admitted`'s audit record:
 * - `initiatorRef`: who or what started it, the person (`p_…`) or the
 *   schedule that fired (`trigger:{scheduleId}`);
 * - `onBehalfOf`: whose authority its tools act with, the person
 *   (`p_…`, the principal itself) or the agent's service account
 *   (`agent:{agentKey}`).
 * A child run carries its parent's unchanged: a child can only narrow what
 * its parent may do, never act for someone else.
 */
export interface RunAttribution {
  principalKind: PrincipalKind;
  initiatorRef: string;
  onBehalfOf: string;
}

export interface AttributionSubject {
  actorId: string;
  principalKind: PrincipalKind;
  /** The agent the run pins; required for an agent's service account. */
  agentKey?: string;
  trigger?: Pick<RunTrigger, 'scheduleId'>;
}

export function runAttribution(subject: AttributionSubject): RunAttribution {
  const person = principalRefFor(subject.actorId);
  const initiatorRef =
    subject.trigger !== undefined
      ? `trigger:${subject.trigger.scheduleId}`
      : person;
  if (subject.principalKind === 'user') {
    return { principalKind: 'user', initiatorRef, onBehalfOf: person };
  }
  const agentKey = subject.agentKey ?? '';
  if (agentKey.length === 0) {
    // A service-account credential is only ever verified for one agent; without it the run would act for no one.
    throw new HarnessForbiddenError(
      'A service account can only start a run of its own agent',
    );
  }
  return {
    principalKind: 'agent_service',
    initiatorRef,
    onBehalfOf: `agent:${agentKey}`,
  };
}

/** What `run.admitted`'s audit record says of it (OWD-2); `principalKind` labels a service account's runs. */
export const attributionAuditFields = (
  attribution: RunAttribution,
): AuditFields => ({
  principalKind: attribution.principalKind,
  initiatorRef: attribution.initiatorRef,
  onBehalfOf: attribution.onBehalfOf,
});
