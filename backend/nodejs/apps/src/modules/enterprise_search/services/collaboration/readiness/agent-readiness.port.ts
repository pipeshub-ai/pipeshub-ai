export interface ReadinessSubject {
  orgId: string;
  userId: string;
}

/**
 * `unknown` means the lookup could not be answered. Callers skip the check on it,
 * because Python still blocks an unready agent at run time: readiness is UX, not a control.
 */
export type AgentReadinessResult =
  | { status: 'ready' }
  | { status: 'blocked'; toolsets: string[] }
  /** The agent is gone or not visible to this user. */
  | { status: 'unavailable' }
  | { status: 'unknown' };

export interface IAgentReadinessPort {
  /** Never throws. */
  check(
    subject: ReadinessSubject,
    agentKey: string,
  ): Promise<AgentReadinessResult>;
  invalidate(subject: ReadinessSubject, agentKey: string): void;
}
