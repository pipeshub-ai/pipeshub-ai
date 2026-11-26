import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import {
  AGENT_HARNESS_ERASURE_WORKER,
  AGENT_HARNESS_RETENTION_SWEEPER,
  startSessionErasureServices,
} from '../../../src/modules/agent-harness/composition';

const logger = () => ({ info: sinon.stub(), error: sinon.stub() });

const withServices = () => {
  const container = new Container();
  const worker = { start: sinon.stub() };
  const sweeper = { start: sinon.stub() };
  container.bind(AGENT_HARNESS_ERASURE_WORKER).toConstantValue(worker);
  container.bind(AGENT_HARNESS_RETENTION_SWEEPER).toConstantValue(sweeper);
  return { container, worker, sweeper };
};

describe('session erasure startup (E7 + D2)', () => {
  afterEach(() => sinon.restore());

  it('SE-1 builds the erasure indexes before starting, whatever the harness flag', async () => {
    const { container, worker, sweeper } = withServices();
    const migrate = sinon
      .stub()
      .resolves({ created: ['agentSessionErasures.session'] });
    const log = logger();
    await startSessionErasureServices(container, log, migrate);
    expect(migrate.calledOnce).to.equal(true);
    expect(migrate.calledBefore(worker.start)).to.equal(true);
    expect(worker.start.calledOnce).to.equal(true);
    expect(sweeper.start.calledOnce).to.equal(true);
    expect(log.info.calledWithMatch('Session erasure built indexes')).to.equal(
      true,
    );
  });

  it('SE-2 a failed index build leaves erasure stopped and says so', async () => {
    const { container, worker, sweeper } = withServices();
    const log = logger();
    await startSessionErasureServices(
      container,
      log,
      sinon.stub().rejects(new Error('not primary')),
    );
    expect(worker.start.called).to.equal(false);
    expect(sweeper.start.called).to.equal(false);
    expect(log.error.calledOnce).to.equal(true);
  });

  it('SE-4 on PostgreSQL the default preparation builds nothing (Alembic owns the tables) and erasure starts', async () => {
    const before = process.env.DOC_STORE;
    process.env.DOC_STORE = 'postgres';
    try {
      const { container, worker, sweeper } = withServices();
      const log = logger();
      await startSessionErasureServices(container, log);
      expect(log.error.called).to.equal(false);
      expect(log.info.called).to.equal(false);
      expect(worker.start.calledOnce).to.equal(true);
      expect(sweeper.start.calledOnce).to.equal(true);
    } finally {
      if (before === undefined) delete process.env.DOC_STORE;
      else process.env.DOC_STORE = before;
    }
  });

  it('SE-3 nothing is built where no erasure service is bound', async () => {
    const migrate = sinon.stub().resolves({ created: [] });
    await startSessionErasureServices(new Container(), logger(), migrate);
    expect(migrate.called).to.equal(false);
  });
});
