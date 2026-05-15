import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { ScheduledJobsBackfillMigration } from '../../../../../src/modules/configuration_manager/services/migrations/scheduled_jobs_backfill.migration';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';
import * as connectorUtils from '../../../../../src/modules/tokens_manager/utils/connector.utils';
import * as createJwt from '../../../../../src/libs/utils/createJwt';

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

const makeKvStore = (existingFlag: string | null = null) => ({
  get: sinon.stub().resolves(existingFlag),
  set: sinon.stub().resolves(),
});

const makeScheduler = (overrides: Partial<any> = {}) => ({
  scheduleJob: sinon.stub().resolves({ id: 'job-1' }),
  removeJob: sinon.stub().resolves(),
  getJobStatus: sinon.stub().resolves(null),
  ...overrides,
});

const makeAppConfig = (): any => ({
  connectorBackend: 'http://connector:8088',
  scopedJwtSecret: 'secret',
});

/** Simulate a single-page response (no more pages). */
const okEnumerationResponse = (items: any[]) => ({
  statusCode: 200,
  data: { success: true, items, hasMore: false },
});

/** Options that collapse backoff to a single no-delay attempt — used in
 * service-unavailable tests so they complete instantly. */
const noBackoff = { maxAttempts: 1, initialDelayMs: 0 };

const sampleScheduledItem = {
  connectorId: 'conn-1',
  type: 'Confluence',
  orgId: 'org-1',
  ownerUserId: '69cf5a063f9154c55dcf81cc',
  isActive: true,
  sync: {
    selectedStrategy: 'SCHEDULED',
    scheduledConfig: { intervalMinutes: 30, timezone: 'UTC' },
  },
};

describe('ScheduledJobsBackfillMigration', () => {
  let executeStub: sinon.SinonStub;
  let jwtStub: sinon.SinonStub;

  beforeEach(() => {
    executeStub = sinon.stub(connectorUtils, 'executeConnectorCommand');
    jwtStub = sinon
      .stub(createJwt, 'fetchConfigJwtGenerator')
      .returns('signed.jwt.token');
  });

  afterEach(() => sinon.restore());

  // ------------------------------------------------------------
  // Flag-based skip
  // ------------------------------------------------------------
  describe('migration flag', () => {
    it('skips when flag is "true"', async () => {
      const logger = makeLogger();
      const kv = makeKvStore('true');
      const scheduler = makeScheduler();
      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
      );
      await m.run();

      expect(kv.get.calledOnceWith(configPaths.connectorSyncScheduledJobsMigration)).to.equal(true);
      expect(executeStub.called).to.equal(false);
      expect(scheduler.scheduleJob.called).to.equal(false);
      expect(kv.set.called).to.equal(false);
    });

    it('proceeds when flag is null', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler();
      executeStub.resolves(okEnumerationResponse([]));
      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
      );
      await m.run();
      expect(executeStub.calledOnce).to.equal(true);
    });

    it('proceeds even when KV get throws (idempotency safety net)', async () => {
      const logger = makeLogger();
      const kv = {
        get: sinon.stub().rejects(new Error('redis down')),
        set: sinon.stub().resolves(),
      };
      const scheduler = makeScheduler();
      executeStub.resolves(okEnumerationResponse([]));
      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
      );
      await m.run();
      expect(executeStub.calledOnce).to.equal(true);
      expect(logger.warn.called).to.equal(true);
    });
  });

  // ------------------------------------------------------------
  // Successful schedule
  // ------------------------------------------------------------
  describe('successful schedule', () => {
    it('schedules each SCHEDULED+active connector and sets the flag', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler();
      executeStub.resolves(
        okEnumerationResponse([
          sampleScheduledItem,
          {
            ...sampleScheduledItem,
            connectorId: 'conn-2',
            type: 'GoogleDrive',
            orgId: 'org-2',
          },
        ]),
      );

      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
      );
      await m.run();

      expect(scheduler.scheduleJob.callCount).to.equal(2);
      // First call: Confluence / conn-1 / org-1
      const firstArgs = scheduler.scheduleJob.firstCall.args;
      expect(firstArgs[0]).to.equal('Confluence');
      expect(firstArgs[1]).to.equal('conn-1');
      expect(firstArgs[3]).to.equal('org-1');
      expect(firstArgs[4]).to.equal(sampleScheduledItem.ownerUserId);

      // Flag set after success
      expect(
        kv.set.calledOnceWith(
          configPaths.connectorSyncScheduledJobsMigration,
          'true',
        ),
      ).to.equal(true);
    });
  });

  // ------------------------------------------------------------
  // Defensive per-connector skip (job already exists)
  // ------------------------------------------------------------
  describe('per-connector idempotency', () => {
    it('skips connectors that already have a job', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler({
        getJobStatus: sinon.stub().resolves({ id: 'existing-job' }),
      });
      executeStub.resolves(okEnumerationResponse([sampleScheduledItem]));

      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
      );
      await m.run();

      expect(scheduler.getJobStatus.calledOnce).to.equal(true);
      expect(scheduler.scheduleJob.called).to.equal(false);
      // Flag still set so we don't re-enumerate next boot
      expect(kv.set.calledOnce).to.equal(true);
    });

    it('skips items that arrive with isActive=false', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler();
      executeStub.resolves(
        okEnumerationResponse([{ ...sampleScheduledItem, isActive: false }]),
      );

      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
      );
      await m.run();
      expect(scheduler.scheduleJob.called).to.equal(false);
    });

    it('skips items where strategy is not SCHEDULED', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler();
      executeStub.resolves(
        okEnumerationResponse([
          {
            ...sampleScheduledItem,
            sync: { selectedStrategy: 'MANUAL' },
          },
        ]),
      );

      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
      );
      await m.run();
      expect(scheduler.scheduleJob.called).to.equal(false);
    });

    it('skips items where intervalMinutes is invalid', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler();
      executeStub.resolves(
        okEnumerationResponse([
          {
            ...sampleScheduledItem,
            sync: {
              selectedStrategy: 'SCHEDULED',
              scheduledConfig: { intervalMinutes: 0 },
            },
          },
        ]),
      );

      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
      );
      await m.run();
      expect(scheduler.scheduleJob.called).to.equal(false);
      expect(logger.warn.called).to.equal(true);
    });
  });

  // ------------------------------------------------------------
  // Per-connector errors don't abort the run
  // ------------------------------------------------------------
  describe('error isolation', () => {
    it('one failing connector does not block the others', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler({
        scheduleJob: sinon
          .stub()
          .onFirstCall()
          .rejects(new Error('redis flapping'))
          .onSecondCall()
          .resolves({ id: 'ok' }),
      });
      executeStub.resolves(
        okEnumerationResponse([
          { ...sampleScheduledItem, connectorId: 'conn-1' },
          { ...sampleScheduledItem, connectorId: 'conn-2' },
        ]),
      );

      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
      );
      await m.run();

      expect(scheduler.scheduleJob.callCount).to.equal(2);
      expect(logger.error.called).to.equal(true);
      // Flag NOT set when errored > 0 — migration retries on next boot for failed connectors
      expect(kv.set.called).to.equal(false);
    });
  });

  // ------------------------------------------------------------
  // Enumeration failures: do NOT set flag (so next boot retries)
  // ------------------------------------------------------------
  describe('enumeration failures', () => {
    it('does not set flag when Python returns non-2xx', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler();
      executeStub.resolves({ statusCode: 500, data: { error: 'oops' } });

      // noBackoff: collapse retries to 1 attempt so the test completes instantly.
      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
        noBackoff,
      );
      await m.run();

      expect(scheduler.scheduleJob.called).to.equal(false);
      expect(kv.set.called).to.equal(false);
      expect(logger.warn.called).to.equal(true);
    });

    it('does not set flag when executeConnectorCommand throws', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler();
      executeStub.rejects(new Error('connect ECONNREFUSED'));

      // noBackoff: single attempt, no delay.
      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
        noBackoff,
      );
      await m.run();

      expect(scheduler.scheduleJob.called).to.equal(false);
      expect(kv.set.called).to.equal(false);
      expect(logger.warn.called).to.equal(true);
    });

    it('skips when connectorBackend URL is missing from AppConfig', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler();
      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        { connectorBackend: '', scopedJwtSecret: 'secret' } as any,
        noBackoff,
      );
      await m.run();

      expect(executeStub.called).to.equal(false);
      expect(scheduler.scheduleJob.called).to.equal(false);
      expect(kv.set.called).to.equal(false);
    });

    it('skips when scopedJwtSecret is missing', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler();
      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        { connectorBackend: 'http://x', scopedJwtSecret: '' } as any,
        noBackoff,
      );
      await m.run();

      expect(jwtStub.called).to.equal(false);
      expect(executeStub.called).to.equal(false);
    });
  });

  // ------------------------------------------------------------
  // Multi-page batch fetch
  // ------------------------------------------------------------
  describe('paginated batch fetch', () => {
    it('follows hasMore=true and fetches subsequent pages until hasMore=false', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler();

      // Page 1: hasMore=true → Node fetches page 2
      // Page 2: hasMore=false → stop
      executeStub
        .onFirstCall().resolves({
          statusCode: 200,
          data: {
            success: true,
            items: [sampleScheduledItem],
            hasMore: true,
          },
        })
        .onSecondCall().resolves({
          statusCode: 200,
          data: {
            success: true,
            items: [{ ...sampleScheduledItem, connectorId: 'conn-2' }],
            hasMore: false,
          },
        });

      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
      );
      await m.run();

      // Two batch calls (skip=0 then skip=50)
      expect(executeStub.callCount).to.equal(2);
      // Both connectors scheduled
      expect(scheduler.scheduleJob.callCount).to.equal(2);
      expect(kv.set.calledOnce).to.equal(true);
    });
  });

  // ------------------------------------------------------------
  // Auth header construction
  // ------------------------------------------------------------
  describe('auth header', () => {
    it('mints scoped JWT with system identifiers and includes Bearer + X-Is-Admin headers', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      const scheduler = makeScheduler();
      executeStub.resolves(okEnumerationResponse([]));

      const m = new ScheduledJobsBackfillMigration(
        logger as any,
        kv as any,
        scheduler as any,
        makeAppConfig(),
      );
      await m.run();

      expect(jwtStub.calledOnce).to.equal(true);
      expect(jwtStub.firstCall.args[0]).to.equal('system');
      expect(jwtStub.firstCall.args[1]).to.equal('system');
      expect(jwtStub.firstCall.args[2]).to.equal('secret');

      const [, , headers] = executeStub.firstCall.args;
      expect(headers.Authorization).to.equal('Bearer signed.jwt.token');
      expect(headers['X-Is-Admin']).to.equal('true');
    });
  });
});
