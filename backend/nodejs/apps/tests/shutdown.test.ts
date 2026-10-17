import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { createGracefulShutdown } from '../src/shutdown';
import { createMockLogger } from './helpers/mock-logger';

describe('createGracefulShutdown', () => {
  let exitStub: sinon.SinonStub;

  beforeEach(() => {
    exitStub = sinon.stub(process, 'exit');
  });

  afterEach(() => {
    sinon.restore();
  });

  it('runs one teardown when a second signal arrives while stopping', async () => {
    let finishStop!: () => void;
    const stop = sinon.stub().returns(new Promise<void>((resolve) => { finishStop = resolve; }));
    const shutdown = createGracefulShutdown({ stop }, createMockLogger() as any);

    const first = shutdown('SIGTERM');
    const second = shutdown('SIGINT');
    finishStop();
    await Promise.all([first, second]);

    expect(stop.calledOnce).to.be.true;
    expect(exitStub.calledOnceWithExactly(0)).to.be.true;
  });

  it('exits 1 when stop() fails', async () => {
    const shutdown = createGracefulShutdown(
      { stop: sinon.stub().rejects(new Error('dispose failed')) },
      createMockLogger() as any,
    );

    await shutdown('SIGTERM');

    expect(exitStub.calledOnceWithExactly(1)).to.be.true;
  });
});
