import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { reconcileConnectorSchedule } from '../../../../src/modules/crawling_manager/services/connector_schedule_orchestrator';
import { CrawlingScheduleType } from '../../../../src/modules/crawling_manager/schema/enums';

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

const makeScheduler = (overrides: Partial<any> = {}) => ({
  scheduleJob: sinon.stub().resolves({ id: 'job-1' }),
  removeJob: sinon.stub().resolves(),
  getJobStatus: sinon.stub().resolves(null),
  ...overrides,
});

const VALID_USER = '69cf5a063f9154c55dcf81cc';

describe('reconcileConnectorSchedule', () => {
  afterEach(() => sinon.restore());

  // ------------------------------------------------------------
  // Case 1: First-time SCHEDULED + isActive → schedule
  // ------------------------------------------------------------
  describe('Case 1: new SCHEDULED connector enabled for the first time', () => {
    it('schedules an INTERVAL job', async () => {
      const scheduler = makeScheduler();
      const logger = makeLogger();
      const outcome = await reconcileConnectorSchedule(
        scheduler as any,
        logger as any,
        {
          connector: 'Confluence',
          connectorId: 'conn-1',
          orgId: 'org-1',
          userId: VALID_USER,
          isActive: true,
          sync: {
            selectedStrategy: 'SCHEDULED',
            scheduledConfig: { intervalMinutes: 30, timezone: 'UTC' },
          },
        },
      );

      expect(outcome).to.equal('scheduled');
      expect(scheduler.scheduleJob.calledOnce).to.equal(true);
      const [conn, connId, schedule, orgId, userId] =
        scheduler.scheduleJob.firstCall.args;
      expect(conn).to.equal('Confluence');
      expect(connId).to.equal('conn-1');
      expect(orgId).to.equal('org-1');
      expect(userId).to.equal(VALID_USER);
      expect(schedule.scheduleType).to.equal(CrawlingScheduleType.INTERVAL);
      expect(schedule.scheduleConfig.intervalMinutes).to.equal(30);
      expect(scheduler.removeJob.called).to.equal(false); // scheduleJob handles replace internally
    });
  });

  // ------------------------------------------------------------
  // Case 2: MANUAL → SCHEDULED config update on an active connector → schedule
  // ------------------------------------------------------------
  describe('Case 2: switching strategy MANUAL → SCHEDULED while active', () => {
    it('schedules a job (scheduler is the same call as case 1; orchestrator does not differentiate)', async () => {
      const scheduler = makeScheduler();
      const logger = makeLogger();
      const outcome = await reconcileConnectorSchedule(
        scheduler as any,
        logger as any,
        {
          connector: 'Confluence',
          connectorId: 'conn-1',
          orgId: 'org-1',
          userId: VALID_USER,
          isActive: true,
          sync: {
            selectedStrategy: 'SCHEDULED',
            scheduledConfig: { intervalMinutes: 60 },
          },
        },
      );
      expect(outcome).to.equal('scheduled');
      expect(scheduler.scheduleJob.calledOnce).to.equal(true);
    });
  });

  // ------------------------------------------------------------
  // Case 3: SCHEDULED → SCHEDULED (interval changed) → reschedule
  // ------------------------------------------------------------
  describe('Case 3: SCHEDULED config edit (interval changed)', () => {
    it('calls scheduleJob again (which removes-then-creates atomically)', async () => {
      const scheduler = makeScheduler();
      const logger = makeLogger();
      const outcome = await reconcileConnectorSchedule(
        scheduler as any,
        logger as any,
        {
          connector: 'Confluence',
          connectorId: 'conn-1',
          orgId: 'org-1',
          userId: VALID_USER,
          isActive: true,
          sync: {
            selectedStrategy: 'SCHEDULED',
            scheduledConfig: { intervalMinutes: 15 }, // changed from 30
          },
        },
      );
      expect(outcome).to.equal('scheduled');
      expect(scheduler.scheduleJob.calledOnce).to.equal(true);
      expect(scheduler.scheduleJob.firstCall.args[2].scheduleConfig.intervalMinutes).to.equal(15);
    });
  });

  // ------------------------------------------------------------
  // Case 4: active → inactive (was SCHEDULED) → remove
  // ------------------------------------------------------------
  describe('Case 4: disable an active SCHEDULED connector', () => {
    it('removes the existing job', async () => {
      const scheduler = makeScheduler({
        getJobStatus: sinon.stub().resolves({ id: 'job-1', state: 'delayed' }),
      });
      const logger = makeLogger();
      const outcome = await reconcileConnectorSchedule(
        scheduler as any,
        logger as any,
        {
          connector: 'Confluence',
          connectorId: 'conn-1',
          orgId: 'org-1',
          userId: VALID_USER,
          isActive: false,
          sync: {
            selectedStrategy: 'SCHEDULED',
            scheduledConfig: { intervalMinutes: 30 },
          },
        },
      );
      expect(outcome).to.equal('removed');
      expect(scheduler.removeJob.calledOnce).to.equal(true);
      expect(scheduler.scheduleJob.called).to.equal(false);
    });

    it('returns "noop" when there is nothing to remove', async () => {
      const scheduler = makeScheduler(); // getJobStatus → null
      const logger = makeLogger();
      const outcome = await reconcileConnectorSchedule(
        scheduler as any,
        logger as any,
        {
          connector: 'Confluence',
          connectorId: 'conn-1',
          orgId: 'org-1',
          userId: VALID_USER,
          isActive: false,
          sync: { selectedStrategy: 'SCHEDULED' },
        },
      );
      expect(outcome).to.equal('noop');
      expect(scheduler.removeJob.called).to.equal(false);
    });
  });

  // ------------------------------------------------------------
  // Edge: SCHEDULED → MANUAL config update on an active connector
  // ------------------------------------------------------------
  describe('Edge: switching strategy SCHEDULED → MANUAL while active', () => {
    it('removes the existing job since the desired strategy is no longer SCHEDULED', async () => {
      const scheduler = makeScheduler({
        getJobStatus: sinon.stub().resolves({ id: 'job-1', state: 'delayed' }),
      });
      const logger = makeLogger();
      const outcome = await reconcileConnectorSchedule(
        scheduler as any,
        logger as any,
        {
          connector: 'Confluence',
          connectorId: 'conn-1',
          orgId: 'org-1',
          userId: VALID_USER,
          isActive: true,
          sync: {
            selectedStrategy: 'MANUAL',
            scheduledConfig: { intervalMinutes: 30 },
          },
        },
      );
      expect(outcome).to.equal('removed');
      expect(scheduler.removeJob.calledOnce).to.equal(true);
      expect(scheduler.scheduleJob.called).to.equal(false);
    });
  });

  // ------------------------------------------------------------
  // Edge: SCHEDULED + invalid scheduledConfig (e.g. missing intervalMinutes)
  // ------------------------------------------------------------
  describe('Edge: SCHEDULED strategy with missing/invalid scheduledConfig', () => {
    it('returns "skipped" and does not call scheduler', async () => {
      const scheduler = makeScheduler();
      const logger = makeLogger();
      const outcome = await reconcileConnectorSchedule(
        scheduler as any,
        logger as any,
        {
          connector: 'Confluence',
          connectorId: 'conn-1',
          orgId: 'org-1',
          userId: VALID_USER,
          isActive: true,
          sync: {
            selectedStrategy: 'SCHEDULED',
            scheduledConfig: { intervalMinutes: 0 }, // invalid
          },
        },
      );
      expect(outcome).to.equal('skipped');
      expect(scheduler.scheduleJob.called).to.equal(false);
      expect(logger.warn.called).to.equal(true);
    });
  });

  // ------------------------------------------------------------
  // Edge: missing identifiers
  // ------------------------------------------------------------
  describe('Edge: missing required identifiers', () => {
    const cases = [
      ['connector', { connector: '' }],
      ['connectorId', { connectorId: '' }],
      ['orgId', { orgId: '' }],
      ['userId', { userId: '' }],
    ] as const;

    for (const [field, override] of cases) {
      it(`returns "skipped" when ${field} is empty`, async () => {
        const scheduler = makeScheduler();
        const logger = makeLogger();
        const outcome = await reconcileConnectorSchedule(
          scheduler as any,
          logger as any,
          {
            connector: 'Confluence',
            connectorId: 'conn-1',
            orgId: 'org-1',
            userId: VALID_USER,
            isActive: true,
            sync: {
              selectedStrategy: 'SCHEDULED',
              scheduledConfig: { intervalMinutes: 30 },
            },
            ...override,
          } as any,
        );
        expect(outcome).to.equal('skipped');
        expect(scheduler.scheduleJob.called).to.equal(false);
        expect(scheduler.removeJob.called).to.equal(false);
      });
    }
  });

  // ------------------------------------------------------------
  // Edge: scheduler errors propagate
  // ------------------------------------------------------------
  describe('Edge: scheduler errors propagate', () => {
    it('rethrows when scheduleJob fails', async () => {
      const scheduler = makeScheduler({
        scheduleJob: sinon.stub().rejects(new Error('redis down')),
      });
      const logger = makeLogger();
      try {
        await reconcileConnectorSchedule(scheduler as any, logger as any, {
          connector: 'Confluence',
          connectorId: 'conn-1',
          orgId: 'org-1',
          userId: VALID_USER,
          isActive: true,
          sync: {
            selectedStrategy: 'SCHEDULED',
            scheduledConfig: { intervalMinutes: 30 },
          },
        });
        expect.fail('should have thrown');
      } catch (err) {
        expect((err as Error).message).to.equal('redis down');
      }
      expect(logger.error.calledOnce).to.equal(true);
    });

    it('rethrows when removeJob fails on disable path', async () => {
      const scheduler = makeScheduler({
        getJobStatus: sinon.stub().resolves({ id: 'job-1', state: 'delayed' }),
        removeJob: sinon.stub().rejects(new Error('redis down')),
      });
      const logger = makeLogger();
      try {
        await reconcileConnectorSchedule(scheduler as any, logger as any, {
          connector: 'Confluence',
          connectorId: 'conn-1',
          orgId: 'org-1',
          userId: VALID_USER,
          isActive: false,
          sync: { selectedStrategy: 'SCHEDULED' },
        });
        expect.fail('should have thrown');
      } catch (err) {
        expect((err as Error).message).to.equal('redis down');
      }
    });
  });
});
