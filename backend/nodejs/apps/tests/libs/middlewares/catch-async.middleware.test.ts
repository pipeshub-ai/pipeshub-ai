import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { catchAsync } from '../../../src/libs/middlewares/catch-async.middleware';
import { followUpStream } from '../../../src/modules/enterprise_search/controller/follow-up-stream.controller';
import { firstSendStream } from '../../../src/modules/enterprise_search/controller/first-send-stream.controller';

describe('catchAsync', () => {
  const res = {} as never;

  it('routes a rejection to next and resolves', async () => {
    const next = sinon.stub();
    const boom = new Error('boom');
    await catchAsync<object>(async () => {
      throw boom;
    })({}, res, next);
    expect(next.calledOnceWithExactly(boom)).to.equal(true);
  });

  it('does not call next when the handler succeeds', async () => {
    const next = sinon.stub();
    await catchAsync<object>(async () => undefined)({}, res, next);
    expect(next.called).to.equal(false);
  });

  it('ends a response whose headers are already out', async () => {
    const next = sinon.stub();
    const end = sinon.stub();
    const streaming = { headersSent: true, writableEnded: false, end } as never;
    await catchAsync<object>(async () => {
      throw new Error('after the SSE header');
    })({}, streaming, next);
    expect(next.calledOnce).to.equal(true);
    expect(end.calledOnce).to.equal(true);
  });

  it('leaves an ended or unsent response alone', async () => {
    for (const r of [
      { headersSent: true, writableEnded: true },
      { headersSent: false, writableEnded: false },
    ]) {
      const end = sinon.stub();
      await catchAsync<object>(async () => {
        throw new Error('x');
      })({}, { ...r, end } as never, sinon.stub());
      expect(end.called).to.equal(false);
    }
  });

  it('rethrows when called without next', async () => {
    const boom = new Error('boom');
    const err = await catchAsync<object>(async () => {
      throw boom;
    })({}, res).catch((e: unknown) => e);
    expect(err).to.equal(boom);
  });
});

describe('stream controllers hand a throw before their try to the error middleware (N6)', () => {
  const emptyBody = {
    body: {},
    params: { conversationId: 'c1', agentKey: 'a1' },
    headers: {},
    user: {},
  } as never;
  for (const [name, factory] of [
    ['followUpStream', followUpStream],
    ['firstSendStream', firstSendStream],
  ] as const) {
    for (const kind of ['assistant', 'agent'] as const) {
      it(`${name}(${kind}): a missing query reaches next as a 400 instead of hanging`, async () => {
        const next = sinon.stub();
        await factory({} as never, {} as never, kind)(
          emptyBody,
          {} as never,
          next,
        );
        expect(next.calledOnce).to.equal(true);
        expect(next.firstCall.args[0]).to.have.property('statusCode', 400);
      });
    }
  }
});
