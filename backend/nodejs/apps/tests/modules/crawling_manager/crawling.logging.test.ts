/**
 * What the crawling manager writes to the log (docs/logging.md "Crawling jobs (Node)"): one failure line per failed request,
 * one INFO line per schedule change besides the access line, `connector_id` / `connector_type` on every line of a request and
 * of a tick, and one `crawling_job.end` per tick. Real routers, scheduler (over the suite's fake queue), worker, request
 * context, access line, error middleware and Logger; the connector service is the suite's FakeBackend.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { EventEmitter } from 'events';
import express from 'express';
import winston from 'winston';
import { Container } from 'inversify';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import { Logger } from '../../../src/libs/services/logger.service';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context';
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware';
// The class the crawling router resolves (from src/config), as tests/modules/crawling_manager/routes/cm_routes.test.ts binds it.
import { AuthMiddleware } from '../../../src/config';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { registerUpstreams } from '../../../src/libs/http/upstream-call';
import * as requestContext from '../../../src/libs/context/request-context';
import { captureLogRecords, inRequestContext, newRequestId, recordsOf } from '../../helpers/log-capture';
import { ServiceUnavailableError } from '../../../src/libs/errors/http.errors';
import { createCrawlingManagerRouter } from '../../../src/modules/crawling_manager/routes/cm_routes';
import { CrawlingSchedulerService } from '../../../src/modules/crawling_manager/services/crawling_service';
import { CrawlingWorkerService } from '../../../src/modules/crawling_manager/services/crawling_worker';
import { ConnectorsCrawlingService } from '../../../src/modules/crawling_manager/services/connectors/connectors';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import { UserActivities } from '../../../src/modules/auth/schema/userActivities.schema';
import { Org } from '../../../src/modules/user_management/schema/org.schema';
import { AppConfig, registerAppUpstreams } from '../../../src/modules/tokens_manager/config/config';
import { FakeBackend, buildConfig } from '../tokens_manager/routes/connectors-http-harness';
import { FakeCrawlingQueue, FakeQueueStore, schedulerOver } from './fake-crawling-queue';

type Row = Record<string, any>;
const MESSAGE = Symbol.for('message');
const ORG = '64b000000000000000000a01';
const ADMIN = { _id: '64b0000000000000000000a1', orgId: ORG, email: 'ada@acme.test', fullName: 'Ada Admin', role: 'admin' };
const daily = { scheduleConfig: { scheduleType: 'daily', isEnabled: true, timezone: 'UTC', hour: 2, minute: 30 } };

const query = (value: unknown): any => {
  const q: any = {
    select: () => q,
    lean: () => q,
    sort: () => q,
    exec: async () => value,
    then: (a: any, b: any) => Promise.resolve(value).then(a, b),
  };
  return q;
};

describe('crawling manager logging', function () {
  this.timeout(20000);
  let records: Row[];
  // The request or tick under test; a line from any other context is not this test's.
  let requestId: string;
  const root = () => (Logger.getInstance() as any).logger as winston.Logger;
  const render = (r: Row): string => {
    const fmt = root().transports.find((t) => t instanceof winston.transports.Console)!.format!;
    const first = root().format.transform({ ...r }, {});
    const out = first ? fmt.transform(first as any, {}) : undefined;
    return out ? String((out as any)[MESSAGE]) : '';
  };
  const level = (r: Row) => {
    const l = String(r.level).toLowerCase();
    return l === 'warning' ? 'warn' : l;
  };
  const mine = () => recordsOf(records, requestId);
  const loud = () => mine().filter((r) => level(r) !== 'debug');
  let orgActive = true;

  beforeEach(() => {
    records = captureLogRecords();
    requestId = newRequestId('crawling-logging');
    sinon.stub(Users, 'findOne').callsFake((() => query(ADMIN)) as any);
    sinon.stub(UserActivities, 'findOne').callsFake((() => query(null)) as any);
    sinon.stub(Org, 'findOne').callsFake((() => query(orgActive ? { _id: ORG } : null)) as any);
  });
  afterEach(() => {
    sinon.restore();
    orgActive = true;
  });

  describe('schedule requests', () => {
    const backend = new FakeBackend();
    const tokens = new AuthTokenService('l11c-jwt', 'l11c-scoped');
    let config: AppConfig;
    let server: Server;
    let origin = '';
    let store: FakeQueueStore;
    let queue: FakeCrawlingQueue;

    before(async () => {
      await backend.start();
      config = buildConfig(backend.url);
    });
    after(async () => {
      await backend.stop();
    });
    beforeEach(async () => {
      registerAppUpstreams(config);
      backend.reset();
      backend.on('GET', '/api/v1/connectors/drive-team', () => ({
        status: 200,
        body: { success: true, connector: { _key: 'drive-team', type: 'Google Drive', orgId: ORG, scope: 'team', createdBy: ADMIN._id } },
      }));
      store = new FakeQueueStore();
      const over = schedulerOver(store);
      queue = over.queue;
      const container = new Container();
      container.bind(AuthMiddleware).toConstantValue(new AuthMiddleware(Logger.getInstance(), tokens, () => ({}) as any));
      container.bind<AppConfig>('AppConfig').toConstantValue(config);
      container.bind(CrawlingSchedulerService).toConstantValue(over.service);
      const app = express();
      app.use(requestContextMiddleware);
      app.use(accessLog());
      app.use(express.json());
      app.use('/api/v1/crawlingManager', createCrawlingManagerRouter(container));
      app.use(ErrorMiddleware.handleError());
      server = await new Promise<Server>((resolve) => {
        const s = app.listen(0, '127.0.0.1', () => resolve(s));
      });
      origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    });
    afterEach(async () => {
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
      registerUpstreams({});
    });

    const send = async (method: string, path: string, body?: unknown) => {
      const token = tokens.generateToken({
        userId: ADMIN._id,
        orgId: ORG,
        email: ADMIN.email,
        fullName: ADMIN.fullName,
        accountType: 'business',
        role: 'admin',
      });
      records.length = 0;
      requestId = newRequestId('crawling-logging');
      const res = await fetch(`${origin}/api/v1/crawlingManager/Google%20Drive/drive-team${path}`, {
        method,
        headers: {
          authorization: `Bearer ${token}`,
          'x-request-id': requestId,
          ...(body ? { 'content-type': 'application/json' } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      await res.text();
      for (let i = 0; i < 400 && !mine().some((r) => r.message === 'http.request.end'); i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
      return res.status;
    };
    const named = (m: string) => mine().filter((r) => r.message === m);

    it('a refused change (nothing paused, a 400): one WARNING failure line and the access line, both naming the connector', async () => {
      expect(await send('POST', '/resume')).to.equal(400);
      expect(loud().map((r) => r.message)).to.deep.equal(['http.request.error', 'http.request.end']);
      expect(loud().map(level)).to.deep.equal(['warn', 'info']);
      for (const line of loud()) {
        expect(render(line)).to.include('connector_id=drive-team').and.to.include('connector_type="Google Drive"');
      }
    });

    it('a disabled schedule (a 400): one WARNING failure line and the access line, nothing from the scheduler', async () => {
      const disabled = { scheduleConfig: { ...daily.scheduleConfig, isEnabled: false } };
      expect(await send('POST', '/schedule', disabled)).to.equal(400);
      expect(loud().map((r) => r.message)).to.deep.equal(['http.request.error', 'http.request.end']);
      expect(loud().map(level)).to.deep.equal(['warn', 'info']);
    });

    it('a schedule: the scheduler’s one INFO line and the access line, both naming the connector', async () => {
      expect(await send('POST', '/schedule', daily)).to.equal(201);
      expect(loud().map((r) => r.message)).to.deep.equal(['Crawling job scheduled successfully', 'http.request.end']);
      for (const line of loud()) expect(render(line)).to.include('connector_id=drive-team');
    });

    for (const [method, path, body] of [['POST', '/pause'], ['POST', '/schedule', daily]] as const) {
      it(`a queue outage on ${path}: one ERROR, from the error middleware`, async () => {
        store.failWith = new Error('redis down');
        expect(await send(method, path, body)).to.equal(500);
        expect(loud().map((r) => r.message)).to.deep.equal(['http.request.error', 'http.request.end']);
        expect(level(named('http.request.error')[0])).to.equal('error');
      });
    }

    it('a schedule that could not be removed: one ERROR whose error carries the queue’s failure as its cause', async () => {
      expect(await send('POST', '/schedule', daily)).to.equal(201);
      sinon.stub(queue, 'removeRepeatable').rejects(new Error('redis went away'));
      expect(await send('DELETE', '/remove')).to.equal(500);
      expect(loud().map((r) => r.message)).to.deep.equal(['http.request.error', 'http.request.end']);
      expect(render(named('http.request.error')[0])).to.include('redis went away');
    });
  });

  describe('a tick', () => {
    const tick = async (crawl: (() => Promise<unknown>) | ConnectorsCrawlingService, data: Row = {}) => {
      const processJob = (CrawlingWorkerService.prototype as any).processJob;
      const context = {
        logger: Logger.getInstance({ service: 'CrawlingWorkerService' }),
        taskService: crawl instanceof ConnectorsCrawlingService ? crawl : { crawl: sinon.stub().callsFake(crawl) },
        processJobInContext: (CrawlingWorkerService.prototype as any).processJobInContext,
        processJobForOrg: (CrawlingWorkerService.prototype as any).processJobForOrg,
      };
      const job = {
        id: 'job-7',
        attemptsMade: 1,
        data: { connector: 'Google Drive', connectorId: 'drive-team', orgId: ORG, userId: ADMIN._id, scheduleConfig: daily.scheduleConfig, ...data },
        updateProgress: sinon.stub().resolves(),
      };
      // The tick takes a fresh system root; make it this test's id so its lines can be told from any other's.
      if (!(requestContext.newSystemRoot as { restore?: unknown }).restore) {
        sinon.stub(requestContext, 'newSystemRoot').returns(requestId);
      }
      return processJob.call(context, job);
    };
    const ends = () => mine().filter((r) => r.message === 'crawling_job.end');

    it('a run whose publish fails: exactly one crawling_job.end, with the org, the connector and the class', async () => {
      // the real task: its publish fails, and only the worker reports the run
      const task = new ConnectorsCrawlingService({ publishEvent: sinon.stub().rejects(new TypeError('publish exploded')) } as any);
      const failure = await tick(task).catch((e: unknown) => e);
      expect((failure as Error).message).to.equal('publish exploded');
      expect(loud()).to.have.length(1);
      const [end] = ends();
      expect(level(end)).to.equal('error');
      expect(end).to.include({ outcome: 'failure', error_class: 'internal', attempt: 2 });
      expect(render(end)).to.include(`org_id=${ORG}`).and.to.include('connector_id=drive-team');
      // the error itself, with its stack at ERROR
      expect(render(end)).to.include('"name": "TypeError"').and.to.include('publish exploded');
    });

    it('a run whose org lookup throws, before the crawl: still exactly one crawling_job.end, at its class’s level', async () => {
      (Org.findOne as sinon.SinonStub).callsFake(() => {
        throw new ServiceUnavailableError('document store is away');
      });
      const failure = await tick(async () => ({ success: true })).catch((e: unknown) => e);
      expect(failure).to.be.instanceOf(ServiceUnavailableError);
      expect(loud()).to.have.length(1);
      const [end] = ends();
      expect(level(end)).to.equal('warn');
      expect(end).to.include({ outcome: 'failure', error_class: 'unavailable' });
      expect(render(end)).to.include('connector_id=drive-team');
    });

    it('BullMQ’s own failed event is DEBUG: the run’s end line already said it', () => {
      const worker = new EventEmitter();
      (CrawlingWorkerService.prototype as any).setupWorkerListeners.call({
        worker,
        logger: Logger.getInstance({ service: 'CrawlingWorkerService' }),
      });
      inRequestContext(requestId, () =>
        worker.emit('failed', { id: 'job-7', data: { connectorId: 'drive-team' } }, new Error('publish exploded')),
      );
      const lines = mine().filter((r) => r.message === 'Job failed');
      expect(lines.map(level)).to.deep.equal(['debug']);
    });

    it('a run the broker refuses (unavailable): its one line is WARNING, not ERROR', async () => {
      const task = new ConnectorsCrawlingService({ publishEvent: sinon.stub().rejects(new ServiceUnavailableError('broker down')) } as any);
      await tick(task).catch(() => undefined);
      expect(loud()).to.have.length(1);
      expect(level(ends()[0])).to.equal('warn');
      expect(ends()[0]).to.include({ outcome: 'failure', error_class: 'unavailable' });
    });

    it('a run that publishes: one INFO crawling_job.end', async () => {
      await tick(async () => ({ success: true }));
      expect(loud()).to.have.length(1);
      expect(level(ends()[0])).to.equal('info');
      expect(ends()[0]).to.include({ outcome: 'success', attempt: 2 });
    });

    it('a Local FS run with no desktop: one INFO line, skipped with its reason', async () => {
      await tick(async () => ({ success: true, skipped: 'desktop_offline' }));
      expect(loud()).to.have.length(1);
      expect(ends()[0]).to.include({ outcome: 'skipped', reason: 'desktop_offline' });
    });

    it('the skip lines name the connector; an inactive org’s line also names the org', async () => {
      orgActive = false;
      await tick(async () => ({ success: true }));
      await tick(async () => ({ success: true }), { orgId: 'system' });
      const skips = loud();
      expect(skips.map((r) => r.message)).to.deep.equal([
        'Skipping crawling job: org does not exist or is not active',
        'Skipping crawling job: org id is not valid',
      ]);
      for (const line of skips) expect(render(line)).to.include('connector_id=drive-team');
      expect(render(skips[0])).to.include(`org_id=${ORG}`);
    });
  });
});
