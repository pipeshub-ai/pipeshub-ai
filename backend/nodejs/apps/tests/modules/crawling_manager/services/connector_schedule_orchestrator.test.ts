import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  RECONCILE_LOG_MESSAGE,
  ScheduleReconcileInput,
  reconcileConnectorSchedule,
} from '../../../../src/modules/crawling_manager/services/connector_schedule_orchestrator';
import { CrawlingScheduleType } from '../../../../src/modules/crawling_manager/schema/enums';
import { BadRequestError } from '../../../../src/libs/errors/http.errors';
import { buildCrawlingScheduleFromSync } from '../../../../src/modules/crawling_manager/utils/schedule_config_mapper';
import { FakeQueueStore, schedulerOver } from '../fake-crawling-queue';

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

const makeScheduler = (overrides: Partial<any> = {}) => ({
  upsertRepeatingSchedule: sinon.stub().resolves('scheduled'),
  removeConnectorSchedules: sinon.stub().resolves(true),
  ...overrides,
});

const VALID_USER = '69cf5a063f9154c55dcf81cc';
const NO_WAIT = { retryDelaysMs: [0, 0] };

const input = (overrides: Partial<ScheduleReconcileInput> = {}): ScheduleReconcileInput => ({
  connector: 'Confluence',
  connectorId: 'conn-1',
  orgId: 'org-1',
  userId: VALID_USER,
  isActive: true,
  sync: { selectedStrategy: 'SCHEDULED', scheduledConfig: { intervalMinutes: 30, timezone: 'UTC' } },
  ...overrides,
});

// The single result line every call must end with.
const resultLog = (logger: ReturnType<typeof makeLogger>) => {
  const matching = (stub: sinon.SinonStub) =>
    stub.getCalls().filter((c) => c.args[0] === RECONCILE_LOG_MESSAGE);
  const errors = matching(logger.error);
  const infos = matching(logger.info);
  expect(errors.length + infos.length).to.equal(1);
  return errors.length
    ? { level: 'error', fields: errors[0].args[1] }
    : { level: 'info', fields: infos[0].args[1] };
};

describe('reconcileConnectorSchedule', () => {
  afterEach(() => sinon.restore());

  describe('active SCHEDULED connector', () => {
    it('upserts an INTERVAL schedule and reports "scheduled"', async () => {
      const scheduler = makeScheduler();
      const logger = makeLogger();

      const outcome = await reconcileConnectorSchedule(scheduler as any, logger as any, input(), NO_WAIT);

      expect(outcome).to.equal('scheduled');
      const [conn, connId, schedule, orgId, userId] = scheduler.upsertRepeatingSchedule.firstCall.args;
      expect([conn, connId, orgId, userId]).to.deep.equal(['Confluence', 'conn-1', 'org-1', VALID_USER]);
      expect(schedule.scheduleType).to.equal(CrawlingScheduleType.INTERVAL);
      expect(schedule.scheduleConfig.intervalMinutes).to.equal(30);
      expect(resultLog(logger)).to.deep.include({ level: 'info' });
      expect(resultLog(logger).fields).to.include({ outcome: 'scheduled', connectorId: 'conn-1' });
    });

    it('reports "unchanged" when the schedule already matches', async () => {
      const scheduler = makeScheduler({ upsertRepeatingSchedule: sinon.stub().resolves('unchanged') });

      const outcome = await reconcileConnectorSchedule(scheduler as any, makeLogger() as any, input(), NO_WAIT);

      expect(outcome).to.equal('unchanged');
    });

    it('still schedules when the owner is unknown, stamping it as system', async () => {
      const scheduler = makeScheduler();

      const outcome = await reconcileConnectorSchedule(scheduler as any, makeLogger() as any, input({ userId: '' }), NO_WAIT);

      expect(outcome).to.equal('scheduled');
      expect(scheduler.upsertRepeatingSchedule.firstCall.args[4]).to.equal('system');
    });
  });

  describe('connector that should have no schedule', () => {
    for (const [label, overrides] of [
      ['disabled', { isActive: false }],
      ['MANUAL', { sync: { selectedStrategy: 'MANUAL' } }],
    ] as const) {
      it(`removes the schedule when ${label}`, async () => {
        const scheduler = makeScheduler();

        const outcome = await reconcileConnectorSchedule(scheduler as any, makeLogger() as any, input(overrides), NO_WAIT);

        expect(outcome).to.equal('removed');
        expect(scheduler.removeConnectorSchedules.calledOnceWith('Confluence', 'conn-1', 'org-1')).to.be.true;
        expect(scheduler.upsertRepeatingSchedule.called).to.be.false;
      });
    }

    it('reports "noop" when nothing was scheduled', async () => {
      const scheduler = makeScheduler({ removeConnectorSchedules: sinon.stub().resolves(false) });

      const outcome = await reconcileConnectorSchedule(scheduler as any, makeLogger() as any, input({ isActive: false }), NO_WAIT);

      expect(outcome).to.equal('noop');
    });
  });

  describe('invalid input and config are reported as errors', () => {
    it('returns "invalid_config" for SCHEDULED without a usable interval', async () => {
      const scheduler = makeScheduler();
      const logger = makeLogger();

      const outcome = await reconcileConnectorSchedule(
        scheduler as any,
        logger as any,
        input({ sync: { selectedStrategy: 'SCHEDULED', scheduledConfig: { intervalMinutes: 0 } } }),
        NO_WAIT,
      );

      expect(outcome).to.equal('invalid_config');
      expect(scheduler.upsertRepeatingSchedule.called).to.be.false;
      expect(resultLog(logger)).to.deep.include({ level: 'error' });
    });

    for (const field of ['connector', 'connectorId', 'orgId'] as const) {
      it(`returns "invalid_input" when ${field} is empty`, async () => {
        const scheduler = makeScheduler();
        const logger = makeLogger();

        const outcome = await reconcileConnectorSchedule(scheduler as any, logger as any, input({ [field]: '' }), NO_WAIT);

        expect(outcome).to.equal('invalid_input');
        expect(scheduler.upsertRepeatingSchedule.called).to.be.false;
        expect(scheduler.removeConnectorSchedules.called).to.be.false;
        expect(resultLog(logger)).to.deep.include({ level: 'error' });
      });
    }
  });

  describe('queue errors', () => {
    it('retries a transient failure and then succeeds', async () => {
      const upsert = sinon.stub();
      upsert.onFirstCall().rejects(new Error('redis blip'));
      upsert.onSecondCall().resolves('scheduled');
      const logger = makeLogger();

      const outcome = await reconcileConnectorSchedule(
        makeScheduler({ upsertRepeatingSchedule: upsert }) as any,
        logger as any,
        input(),
        NO_WAIT,
      );

      expect(outcome).to.equal('scheduled');
      expect(upsert.callCount).to.equal(2);
      expect(logger.warn.calledOnce).to.be.true;
    });

    it('rethrows after the last retry and logs "failed" with the stack', async () => {
      const upsert = sinon.stub().rejects(new Error('redis down'));
      const logger = makeLogger();

      try {
        await reconcileConnectorSchedule(makeScheduler({ upsertRepeatingSchedule: upsert }) as any, logger as any, input(), NO_WAIT);
        expect.fail('should have thrown');
      } catch (err) {
        expect((err as Error).message).to.equal('redis down');
      }

      expect(upsert.callCount).to.equal(3);
      const { level, fields } = resultLog(logger);
      expect(level).to.equal('error');
      expect(fields).to.include({ outcome: 'failed', error: 'redis down' });
      expect(fields.stack).to.be.a('string');
    });

    it('does not retry a validation error', async () => {
      const upsert = sinon.stub().rejects(new BadRequestError('bad schedule'));

      try {
        await reconcileConnectorSchedule(makeScheduler({ upsertRepeatingSchedule: upsert }) as any, makeLogger() as any, input(), NO_WAIT);
        expect.fail('should have thrown');
      } catch (err) {
        expect(err).to.be.instanceOf(BadRequestError);
      }

      expect(upsert.calledOnce).to.be.true;
    });

    it('retries and rethrows on the removal path too', async () => {
      const remove = sinon.stub().rejects(new Error('redis down'));

      try {
        await reconcileConnectorSchedule(
          makeScheduler({ removeConnectorSchedules: remove }) as any,
          makeLogger() as any,
          input({ isActive: false }),
          NO_WAIT,
        );
        expect.fail('should have thrown');
      } catch (err) {
        expect((err as Error).message).to.equal('redis down');
      }

      expect(remove.callCount).to.equal(3);
    });
  });

  describe('against the scheduler and queue', () => {
    let store: FakeQueueStore;
    let scheduler: ReturnType<typeof schedulerOver>['service'];
    let queue: ReturnType<typeof schedulerOver>['queue'];

    const schedules = () =>
      [...store.repeatables.values()].filter((r) => r.name === scheduler.jobNameFor('Confluence', 'conn-1'));

    beforeEach(() => {
      store = new FakeQueueStore();
      ({ service: scheduler, queue } = schedulerOver(store));
    });

    it('removes a schedule that has no queued run left when the connector is disabled', async () => {
      await reconcileConnectorSchedule(scheduler, makeLogger() as any, input(), NO_WAIT);
      for (const job of store.pending()) store.jobs.delete(job.id);

      const outcome = await reconcileConnectorSchedule(scheduler, makeLogger() as any, input({ isActive: false }), NO_WAIT);

      expect(outcome).to.equal('removed');
      expect(schedules()).to.be.empty;
    });

    it('leaves an identical schedule untouched', async () => {
      await reconcileConnectorSchedule(scheduler, makeLogger() as any, input(), NO_WAIT);
      const [before] = schedules();

      const outcome = await reconcileConnectorSchedule(scheduler, makeLogger() as any, input(), NO_WAIT);

      expect(outcome).to.equal('unchanged');
      expect(schedules().map((r) => r.key)).to.deep.equal([before.key]);
    });

    it('replaces a schedule whose interval changed, leaving one', async () => {
      await reconcileConnectorSchedule(scheduler, makeLogger() as any, input(), NO_WAIT);

      await reconcileConnectorSchedule(
        scheduler,
        makeLogger() as any,
        input({ sync: { selectedStrategy: 'SCHEDULED', scheduledConfig: { intervalMinutes: 60, timezone: 'UTC' } } }),
        NO_WAIT,
      );

      expect(schedules().map((r) => r.every)).to.deep.equal([String(60 * 60_000)]);
    });

    it('keeps the old schedule when adding the new one keeps failing', async () => {
      await reconcileConnectorSchedule(scheduler, makeLogger() as any, input(), NO_WAIT);
      sinon.stub(queue, 'add').rejects(new Error('redis down'));

      try {
        await reconcileConnectorSchedule(
          scheduler,
          makeLogger() as any,
          input({ sync: { selectedStrategy: 'SCHEDULED', scheduledConfig: { intervalMinutes: 60, timezone: 'UTC' } } }),
          NO_WAIT,
        );
        expect.fail('should have thrown');
      } catch (err) {
        expect((err as Error).message).to.equal('redis down');
      }

      expect(schedules().map((r) => r.every)).to.deep.equal([String(30 * 60_000)]);
    });

    it('rejects a disabled schedule without touching the queue', async () => {
      const schedule = { ...buildCrawlingScheduleFromSync(input().sync, VALID_USER)!, isEnabled: false };

      try {
        await scheduler.upsertRepeatingSchedule('Confluence', 'conn-1', schedule, 'org-1', VALID_USER);
        expect.fail('should have thrown');
      } catch (err) {
        expect(err).to.be.instanceOf(BadRequestError);
      }

      expect(schedules()).to.be.empty;
    });
  });
});
