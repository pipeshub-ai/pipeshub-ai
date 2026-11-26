import { BrokerTopic } from '../../../../libs/types/messaging.types';
import { DispatchRecord } from '../../domain/execution';

export const AGENT_RUN_DISPATCH_EVENT = 'agent_run_dispatch_requested';

export interface DispatchMessage {
  topic: string;
  key: string;
  orderingKey: string;
  value: string;
  headers: Record<string, string>;
}

/**
 * Identifier-only broker message consumed by the Python worker
 * (`app/harness/worker/runner.py`); keyed and ordered by run.
 */
export function dispatchMessage(record: DispatchRecord): DispatchMessage {
  const event = {
    eventType: AGENT_RUN_DISPATCH_EVENT,
    timestamp: record.createdAt,
    payload: {
      schemaVersion: 1,
      commandId: record.commandId,
      runId: record.runId,
      orgId: record.orgId,
      kind: record.kind,
      createdAt: record.createdAt,
    },
  };
  return {
    topic: BrokerTopic.AGENT_RUN_EVENTS,
    key: record.runId,
    orderingKey: record.runId,
    value: JSON.stringify(event),
    headers: { eventType: AGENT_RUN_DISPATCH_EVENT, schemaVersion: '1' },
  };
}
