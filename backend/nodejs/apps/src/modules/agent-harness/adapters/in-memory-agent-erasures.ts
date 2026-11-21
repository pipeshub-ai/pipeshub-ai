/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous port without I/O. */
import {
  AgentDeletionLookup,
  AgentDeletionState,
  AgentErasureQueue,
  PendingAgentErasure,
} from '../ports/agent-erasures';

const key = (orgId: string, agentKey: string): string => `${orgId}:${agentKey}`;

/** Pending agent erasures in memory, on an injected clock: tests and single-process runs. */
export class InMemoryAgentErasureQueue implements AgentErasureQueue {
  private readonly entries = new Map<string, PendingAgentErasure>();

  constructor(private readonly clock: () => number = Date.now) {}

  async intend(
    orgId: string,
    agentKey: string,
    afterMs: number,
  ): Promise<void> {
    const held = this.entries.get(key(orgId, agentKey));
    const now = this.clock();
    if (held?.confirmed) return;
    if (held !== undefined) {
      held.requestedAt = now;
      held.nextAttemptAt = now + afterMs;
      return;
    }
    this.entries.set(key(orgId, agentKey), {
      orgId,
      agentKey,
      confirmed: false,
      failures: 0,
      requestedAt: now,
      nextAttemptAt: now + afterMs,
      lastErrorType: null,
    });
  }

  async enqueue(
    orgId: string,
    agentKey: string,
    errorType: string,
  ): Promise<void> {
    const held = this.entries.get(key(orgId, agentKey));
    if (held?.confirmed) return;
    const now = this.clock();
    this.entries.set(key(orgId, agentKey), {
      orgId,
      agentKey,
      confirmed: true,
      failures: (held?.failures ?? 0) + 1,
      requestedAt: held?.requestedAt ?? now,
      nextAttemptAt: now,
      lastErrorType: errorType,
    });
  }

  async due(now: number, limit: number): Promise<PendingAgentErasure[]> {
    return [...this.entries.values()]
      .filter((entry) => entry.nextAttemptAt <= now)
      .sort((a, b) => a.nextAttemptAt - b.nextAttemptAt)
      .slice(0, limit)
      .map((entry) => ({ ...entry }));
  }

  async retryLater(
    entry: PendingAgentErasure,
    at: number,
    errorType: string,
  ): Promise<void> {
    const held = this.entries.get(key(entry.orgId, entry.agentKey));
    if (held === undefined) return;
    held.failures += 1;
    held.nextAttemptAt = at;
    held.lastErrorType = errorType;
  }

  async postpone(entry: PendingAgentErasure, at: number): Promise<void> {
    const held = this.entries.get(key(entry.orgId, entry.agentKey));
    if (held === undefined) return;
    held.nextAttemptAt = at;
  }

  async settle(orgId: string, agentKey: string): Promise<void> {
    this.entries.delete(key(orgId, agentKey));
  }

  async withdraw(entry: PendingAgentErasure): Promise<boolean> {
    const held = this.entries.get(key(entry.orgId, entry.agentKey));
    if (
      held === undefined ||
      held.confirmed ||
      held.requestedAt !== entry.requestedAt
    ) {
      return false;
    }
    return this.entries.delete(key(entry.orgId, entry.agentKey));
  }
}

/** The query service's agent states, held in memory: tests. Unknown agents are `absent`, as the route answers. */
export class InMemoryAgentDeletionLookup implements AgentDeletionLookup {
  private readonly states = new Map<string, AgentDeletionState>();

  set(orgId: string, agentKey: string, state: AgentDeletionState): void {
    this.states.set(key(orgId, agentKey), state);
  }

  async state(orgId: string, agentKey: string): Promise<AgentDeletionState> {
    return this.states.get(key(orgId, agentKey)) ?? 'absent';
  }
}
