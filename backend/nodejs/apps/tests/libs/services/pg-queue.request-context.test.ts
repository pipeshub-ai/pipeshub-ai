import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { createMockLogger } from '../../helpers/mock-logger';
import * as runtimeBoot from '../../../src/libs/db/runtime-boot';
import { BasePgQueueConsumerConnection } from '../../../src/libs/services/pg-queue.service';
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
});
