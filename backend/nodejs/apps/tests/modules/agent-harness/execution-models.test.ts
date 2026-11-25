import { expect } from 'chai';
import {
  AgentAdmissionReceiptModel,
  AgentCommandReceiptModel,
  AgentEventModel,
  AgentRunModel,
} from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';

type IndexDefinition = [Record<string, number>, Record<string, unknown>];

const findIndex = (
  indexes: IndexDefinition[],
  keys: Record<string, number>,
): IndexDefinition | undefined =>
  indexes.find(
    ([candidate]) => JSON.stringify(candidate) === JSON.stringify(keys),
  );

describe('agent harness Mongo models', () => {
  it('enforces tenant-scoped run identities', () => {
    const indexes = AgentRunModel.schema.indexes() as IndexDefinition[];
    expect(findIndex(indexes, { orgId: 1, runId: 1 })?.[1].unique).to.equal(
      true,
    );
  });

  it('keeps run idempotency only in admission receipts', () => {
    const indexes = AgentRunModel.schema.indexes() as IndexDefinition[];
    expect(
      findIndex(indexes, { orgId: 1, sessionId: 1, idempotencyKey: 1 }),
    ).to.equal(undefined);
    const receiptIndexes =
      AgentAdmissionReceiptModel.schema.indexes() as IndexDefinition[];
    expect(findIndex(receiptIndexes, { scopeKey: 1 })?.[1].unique).to.equal(
      true,
    );
  });

  it('indexes both sweep predicates', () => {
    const indexes = AgentRunModel.schema.indexes() as IndexDefinition[];
    expect(findIndex(indexes, { status: 1, leaseExpiresAt: 1 })).to.not.equal(
      undefined,
    );
    expect(
      findIndex(indexes, { status: 1, absoluteDeadlineAt: 1 }),
    ).to.not.equal(undefined);
  });

  it('enforces one active root through a partial unique key', () => {
    const indexes = AgentRunModel.schema.indexes() as IndexDefinition[];
    const options = findIndex(indexes, { activeSessionKey: 1 })?.[1];
    expect(options?.unique).to.equal(true);
    expect(options?.partialFilterExpression).to.deep.equal({
      activeSessionKey: { $type: 'string' },
    });
  });

  it('enforces ordered event and durable command identities', () => {
    const eventIndexes = AgentEventModel.schema.indexes() as IndexDefinition[];
    const receiptIndexes =
      AgentCommandReceiptModel.schema.indexes() as IndexDefinition[];
    expect(
      findIndex(eventIndexes, { orgId: 1, sessionId: 1, eventSeq: 1 })?.[1]
        .unique,
    ).to.equal(true);
    expect(
      findIndex(receiptIndexes, { orgId: 1, runId: 1, commandId: 1 })?.[1]
        .unique,
    ).to.equal(true);
  });

  it('preserves empty objects in durable snapshots and replay receipts', () => {
    expect(AgentRunModel.schema.get('minimize')).to.equal(false);
    expect(AgentEventModel.schema.get('minimize')).to.equal(false);
    expect(AgentCommandReceiptModel.schema.get('minimize')).to.equal(false);
    expect(AgentAdmissionReceiptModel.schema.get('minimize')).to.equal(false);
  });
});
