import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { createMockLogger } from '../../helpers/mock-logger';
import * as runtimeBoot from '../../../src/libs/db/runtime-boot';
import { BasePgQueueConsumerConnection } from '../../../src/libs/services/pg-queue.service';
import { configureInbox } from '../../../src/libs/context/envelope';
import { MemInbox } from '../../helpers/memory-inbox';
import {
  getOrgId,
  getRequestContext,
  getRootId,
} from '../../../src/libs/context/request-context';

class TestConsumer extends BasePgQueueConsumerConnection {}

describe('PostgreSQL queue consumer: request context', () => {
  afterEach(() => sinon.restore());

  it('runs the handler under the message request id and org', async () => {
    const tx = { query: sinon.stub().resolves({ rows: [] }) };
    sinon.stub(runtimeBoot, 'ensurePgRuntime').returns({
      withUow: (_opts: unknown, fn: (t: unknown) => Promise<unknown>) => fn(tx),
    } as unknown as ReturnType<typeof runtimeBoot.ensurePgRuntime>);

    const consumer = new TestConsumer(
      { groupId: 'g' } as never,
      createMockLogger() as never,
    );
    const seen: Array<[string | undefined, string | undefined]> = [];
    const row = {
      topic: 't',
      message_id: '1',
      attempts: 1,
      value: JSON.stringify({
        eventType: 'e',
        payload: {},
        requestId: 'req-1',
        orgId: 'org-1',
      }),
      msg_key: null,
      headers: null,
    };

    await (
      consumer as unknown as {
        settle: (r: typeof row, h: () => Promise<void>) => Promise<void>;
      }
    ).settle(row, async () => {
      seen.push([getRootId(), getOrgId()]);
    });

    expect(seen).to.deep.equal([['req-1', 'org-1']]);
    expect(getRequestContext()).to.equal(undefined);
  });

  it('a redelivered event runs once for the group and is acked both times', async () => {
    const tx = { query: sinon.stub().resolves({ rows: [] }) };
    sinon.stub(runtimeBoot, 'ensurePgRuntime').returns({
      withUow: (_opts: unknown, fn: (t: unknown) => Promise<unknown>) => fn(tx),
    } as unknown as ReturnType<typeof runtimeBoot.ensurePgRuntime>);
    const inbox = new MemInbox();
    configureInbox(inbox);
    try {
      const consumer = new TestConsumer({ groupId: 'g' } as never, createMockLogger() as never);
      const value = JSON.stringify({ orgId: 'org-1', type: 't', eventId: 'evt-1' });
      const handler = sinon.stub().resolves();
      const settle = (consumer as unknown as {
        settle: (r: unknown, h: () => Promise<void>) => Promise<void>;
      }).settle.bind(consumer);
      for (const attempts of [1, 2])
        await settle({ topic: 't', message_id: '1', attempts, value, msg_key: null, headers: null }, handler);
      expect(handler.callCount).to.equal(1);
      const acks = tx.query.args.filter((a) => String(a[0]).includes('pipeshub:queue.ack'));
      expect(acks).to.have.length(2);
      expect([...inbox.rows]).to.deep.equal(['org-1|g|evt-1']);
    } finally {
      configureInbox(null);
    }
  });
});
