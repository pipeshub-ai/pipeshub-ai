import { expect } from 'chai';
import sinon from 'sinon';
import type { BufferedEvent } from '../../../../src/plugins/telemetry/event-buffer';
import { axiosV3Http } from '../../../../src/plugins/telemetry/v3/http';
import {
  BACKOFF_BASE_MS,
  BACKOFF_MAX_MS,
  NOT_V3_TTL_MS,
  SecretStore,
  UsageContractMode,
  UsageV3Sender,
  usageContractMode,
  usageEdition,
  v3Url,
} from '../../../../src/plugins/telemetry/v3/sender';
import type { MetricSample } from '../../../../src/plugins/telemetry/v3/translate';
import { FakeCollector } from './fake-collector';

const INSTALL = '0b9d7a52-7c0b-4c0e-9c0a-2f4f6a1d2e3f';
const SDK = {
  channel: 'sdk',
  sdk_language: 'python',
  sdk_version: '1.2.3',
  outcome: 'success',
};
const SERVER_URL = 'https://collector.example/collect-metrics';

/** The install: its registry, its event buffer and its stored configuration, shared by its processes. */
class Install {
  secret: string | undefined;
  secretWrites = 0;
  failWrites = false;
  readonly store: SecretStore = {
    read: async () => this.secret,
    write: async (secret) => {
      if (this.failWrites) throw new Error('kv down');
      this.secret = secret;
      this.secretWrites += 1;
    },
  };
}

class Process {
  now = 1_000_000;
  /** SDK requests counted by this process, per organisation: the legacy registry's shape. */
  readonly sdkRequests = new Map<string, number>();
  orgs = 0;
  readonly buffer: BufferedEvent[] = [];
  readonly logger = { debug: sinon.spy(), warn: sinon.spy() };
  readonly sender: UsageV3Sender;

  constructor(
    readonly collector: FakeCollector,
    readonly install: Install,
    mode: UsageContractMode = 'auto',
    private readonly serverUrl = SERVER_URL,
  ) {
    this.sender = new UsageV3Sender({
      mode,
      http: collector,
      secrets: install.store,
      logger: this.logger,
      snapshot: () => this.snapshot(),
      events: {
        take: (max) => this.buffer.splice(0, max),
        putBack: (events) => {
          this.buffer.unshift(...events);
        },
      },
      now: () => this.now,
      edition: 'oss',
    });
  }

  count(n: number, org = 'org-1'): void {
    this.sdkRequests.set(org, (this.sdkRequests.get(org) ?? 0) + n);
  }

  event(event: string, props: Record<string, unknown> = {}): void {
    this.buffer.push({
      event,
      timestamp: new Date(this.now).toISOString(),
      props,
    });
  }

  snapshot(): MetricSample[] {
    const samples: MetricSample[] = [...this.sdkRequests].map(
      ([org, value]) => ({
        name: 'pipeshub_usage_sdk_requests_total',
        type: 'counter',
        labels: { service: 'node_api', ...SDK, org, domain: `${org}.example` },
        value,
      }),
    );
    if (this.orgs > 0) {
      samples.push({
        name: 'pipeshub_orgs_total',
        type: 'gauge',
        labels: { account_type: 'business', domain: 'acme.com' },
        value: this.orgs,
      });
    }
    return samples;
  }

  /** One interval: the sender first, then the legacy push when it says so, as the service does. */
  async tick(): Promise<string> {
    const route = await this.sender.tick({
      serverUrl: this.serverUrl,
      installId: INSTALL,
      version: '1.4.0',
      service: 'node_api',
    });
    if (route === 'legacy') {
      await this.collector.post(
        this.serverUrl,
        { instanceId: INSTALL, metrics: JSON.stringify(this.snapshot()) },
        {},
      );
      const events = this.buffer.splice(0);
      if (events.length > 0) {
        await this.collector.post(
          this.serverUrl.replace('collect-metrics', 'collect-events'),
          { instanceId: INSTALL, events },
          {},
        );
      }
      await this.sender.deliveredByLegacy();
    }
    return route;
  }

  advance(ms: number): void {
    this.now += ms;
  }
}

const sdkTotal = (collector: FakeCollector) =>
  collector.total('pipeshub.usage.sdk.requests', SDK);
const v3Requests = (collector: FakeCollector, name: string) =>
  collector.requests.filter((r) => r.path === `/v3/${name}`);

describe('usage telemetry v3: sender', () => {
  let collector: FakeCollector;
  let install: Install;

  beforeEach(() => {
    collector = new FakeCollector();
    install = new Install();
  });

  describe('the setting', () => {
    it('is legacy unless it says auto or v3', () => {
      expect(usageContractMode(undefined)).to.equal('legacy');
      expect(usageContractMode('')).to.equal('legacy');
      expect(usageContractMode('nonsense')).to.equal('legacy');
      expect(usageContractMode(' Auto ')).to.equal('auto');
      expect(usageContractMode(' V3 ')).to.equal('v3');
      expect(usageContractMode('legacy')).to.equal('legacy');
    });

    it('reports the open-source edition unless the build says otherwise', () => {
      expect(usageEdition(undefined)).to.equal('oss');
      expect(usageEdition('whatever')).to.equal('oss');
      expect(usageEdition(' EE ')).to.equal('ee');
    });

    it('puts the v3 endpoints next to the configured legacy one', () => {
      expect(v3Url('https://c.example/collect-metrics', 'metrics')).to.equal(
        'https://c.example/v3/metrics',
      );
      expect(v3Url('https://c.example/collect-metrics/', 'events')).to.equal(
        'https://c.example/v3/events',
      );
      expect(
        v3Url('https://c.example/usage/collect-metrics?x=1', 'installs'),
      ).to.equal('https://c.example/usage/v3/installs');
      expect(v3Url('https://c.example', 'installs')).to.equal(
        'https://c.example/v3/installs',
      );
    });
  });

  describe('registration', () => {
    it('registers once, stores the secret, and never registers again', async () => {
      const p = new Process(collector, install);
      p.count(3);
      expect(await p.tick()).to.equal('v3');
      expect(v3Requests(collector, 'installs')).to.have.length(1);
      expect(v3Requests(collector, 'installs')[0]!.body).to.deep.equal({
        installId: INSTALL,
        version: '1.4.0',
        edition: 'oss',
      });
      expect(install.secret).to.equal(collector.secrets.get(INSTALL));
      expect(install.secretWrites).to.equal(1);

      p.count(1);
      await p.tick();
      await p.tick();
      expect(v3Requests(collector, 'installs')).to.have.length(1);
      expect(install.secretWrites).to.equal(1);
    });

    it('uses the secret another replica stored instead of registering', async () => {
      const first = new Process(collector, install);
      await first.tick();
      const second = new Process(collector, install);
      second.count(2);
      expect(await second.tick()).to.equal('v3');
      expect(v3Requests(collector, 'installs')).to.have.length(1);
      expect(sdkTotal(collector)).to.equal(2);
    });

    it('picks up the secret of the replica that won the registration', async () => {
      // Both replicas start with nothing stored; the other one registers first
      // and has not stored the secret when this one asks.
      const other = new Install();
      const winner = new Process(collector, other);
      await winner.tick();
      const p = new Process(collector, install);
      p.count(4);
      expect(await p.tick()).to.equal('legacy');
      install.secret = other.secret; // the winner's write lands
      expect(await p.tick()).to.equal('v3');
      expect(v3Requests(collector, 'installs')).to.have.length(2);
      // The interval that went by the legacy endpoints is not sent again.
      expect(sdkTotal(collector)).to.equal(0);
    });

    it('gives up on v3 for a day when the id is registered and the secret is nowhere to be found', async () => {
      collector.secrets.set(INSTALL, 'someone-elses');
      const p = new Process(collector, install);
      for (let i = 0; i < 3; i++) expect(await p.tick()).to.equal('legacy');
      expect(p.logger.warn.calledOnce).to.equal(true);
      const asked = v3Requests(collector, 'installs').length;
      expect(await p.tick()).to.equal('legacy');
      expect(v3Requests(collector, 'installs')).to.have.length(asked);
      p.advance(NOT_V3_TTL_MS + 1);
      await p.tick();
      expect(v3Requests(collector, 'installs')).to.have.length(asked + 1);
    });

    it('keeps a secret it could not store and stores it later, without registering twice', async () => {
      install.failWrites = true;
      const p = new Process(collector, install);
      p.count(1);
      expect(await p.tick()).to.equal('v3');
      expect(install.secret).to.equal(undefined);
      install.failWrites = false;
      await p.tick();
      expect(install.secret).to.equal(collector.secrets.get(INSTALL));
      expect(v3Requests(collector, 'installs')).to.have.length(1);
    });

    it('waits and asks again when registration is unavailable', async () => {
      collector.failNext({ status: 503, contract: '3' }, undefined);
      const p = new Process(collector, install);
      p.count(2);
      expect(await p.tick()).to.equal('none');
      expect(await p.tick()).to.equal('none'); // still backing off: no request
      expect(collector.requests).to.have.length(1);
      p.advance(BACKOFF_BASE_MS);
      expect(await p.tick()).to.equal('none'); // no answer at all
      p.advance(BACKOFF_BASE_MS * 2);
      expect(await p.tick()).to.equal('v3');
      expect(collector.legacyMetrics).to.have.length(0);
      expect(sdkTotal(collector)).to.equal(2);
    });
  });

  describe('sending', () => {
    it('sends counters as deltas and gauges as they are, with nothing that says who', async () => {
      const p = new Process(collector, install);
      p.count(3, 'org-1');
      p.count(2, 'org-2');
      p.orgs = 2;
      p.event('login', {
        orgId: 'org-1',
        userId: 'user-1',
        email: 'jane@acme.com',
        domain: 'acme.com',
        first_login: true,
        auth_method: 'google',
      });
      p.event('pat_created', {
        orgId: 'org-1',
        userId: 'user-1',
        domain: 'acme.com',
        scope_count: 2,
      });
      await p.tick();
      p.count(4, 'org-2');
      await p.tick();

      const [first, second] = v3Requests(collector, 'metrics');
      expect(first!.headers.Authorization).to.equal(
        `Bearer ${INSTALL}.${install.secret!}`,
      );
      expect(first!.body).to.deep.equal({
        processId: p.sender.processId,
        sequence: 1,
        service: 'node_api',
        version: '1.4.0',
        edition: 'oss',
        points: [
          {
            name: 'pipeshub.usage.sdk.requests',
            type: 'counter',
            labels: SDK,
            value: 5,
          },
          {
            name: 'pipeshub.usage.orgs',
            type: 'gauge',
            labels: { account_type: 'business' },
            value: 2,
          },
        ],
      });
      expect(second!.body.sequence).to.equal(2);
      expect(second!.body.points[0]).to.deep.equal({
        name: 'pipeshub.usage.sdk.requests',
        type: 'counter',
        labels: SDK,
        value: 4,
      });
      expect(sdkTotal(collector)).to.equal(9);

      expect(collector.events).to.deep.equal([
        {
          name: 'user.logged_in',
          ts: new Date(1_000_000).toISOString(),
          attributes: { auth_method: 'google', first_login: true },
        },
      ]);
      const wire = JSON.stringify(
        collector.requests
          .filter((r) => r.path !== '/v3/installs')
          .map((r) => r.body),
      );
      for (const personal of [
        'org-1',
        'org-2',
        'user-1',
        'jane@acme.com',
        'acme.com',
        'example',
        'pat_created',
        INSTALL,
      ]) {
        expect(wire, personal).to.not.include(personal);
      }
      expect(collector.legacyMetrics).to.have.length(0);
      expect(collector.legacyEvents).to.have.length(0);
    });

    it('sends nothing when nothing was counted', async () => {
      const p = new Process(collector, install);
      await p.tick();
      p.count(1);
      await p.tick();
      await p.tick();
      expect(v3Requests(collector, 'metrics')).to.have.length(1);
      expect(v3Requests(collector, 'events')).to.have.length(0);
    });

    it('numbers metrics and events separately', async () => {
      const p = new Process(collector, install);
      p.count(1);
      await p.tick();
      p.count(1);
      p.event('signup');
      await p.tick();
      expect(
        v3Requests(collector, 'metrics').map((r) => r.body.sequence),
      ).to.deep.equal([1, 2]);
      expect(
        v3Requests(collector, 'events').map((r) => r.body.sequence),
      ).to.deep.equal([1]);
    });
  });

  describe('retry', () => {
    it('sends the same batch again after a failure and is counted once', async () => {
      const p = new Process(collector, install);
      await p.tick(); // registers
      p.count(5);
      collector.failNext({ status: 502, contract: '3' });
      expect(await p.tick()).to.equal('none');
      p.count(2); // counted while the batch is pending
      p.advance(BACKOFF_BASE_MS);
      expect(await p.tick()).to.equal('v3');
      const [failed, retried] = v3Requests(collector, 'metrics');
      expect(retried!.body).to.deep.equal(failed!.body);
      expect(sdkTotal(collector)).to.equal(5);
      await p.tick();
      expect(sdkTotal(collector)).to.equal(7);
      expect(
        v3Requests(collector, 'metrics').map((r) => r.body.sequence),
      ).to.deep.equal([1, 1, 2]);
    });

    it('is not counted twice when the collector stored the batch and the answer was lost', async () => {
      const p = new Process(collector, install);
      await p.tick();
      p.count(5);
      p.event('signup');
      // The collector handles the push; the sender never sees the answer.
      collector.failNext((request) => {
        void collector.post(request.url, request.body, request.headers);
        collector.requests.pop();
        return undefined;
      });
      expect(await p.tick()).to.equal('none');
      expect(sdkTotal(collector)).to.equal(5);
      p.advance(BACKOFF_BASE_MS);
      expect(await p.tick()).to.equal('v3');
      expect(sdkTotal(collector)).to.equal(5);
      expect(collector.events).to.have.length(1);
    });

    it('backs off longer each time, up to a cap, and honours Retry-After', async () => {
      const p = new Process(collector, install);
      await p.tick();
      p.count(1);
      const waits: number[] = [];
      for (let i = 0; i < 9; i++) {
        collector.failNext({ status: 502, contract: '3' });
        const before = v3Requests(collector, 'metrics').length;
        await p.tick();
        expect(v3Requests(collector, 'metrics').length).to.equal(before + 1);
        const wait = p.sender.state().retryAt - p.now;
        waits.push(wait);
        p.advance(wait - 1);
        await p.tick(); // one millisecond early: nothing is sent
        expect(v3Requests(collector, 'metrics').length).to.equal(before + 1);
        p.advance(1);
      }
      expect(waits.slice(0, 4)).to.deep.equal(
        [1, 2, 4, 8].map((n) => n * BACKOFF_BASE_MS),
      );
      expect(waits[8]).to.equal(BACKOFF_MAX_MS);

      collector.failNext({
        status: 429,
        contract: '3',
        retryAfterSeconds: 7200,
      });
      await p.tick();
      expect(p.sender.state().retryAt - p.now).to.equal(7200 * 1000);
      p.advance(7200 * 1000);
      expect(await p.tick()).to.equal('v3');
      expect(sdkTotal(collector)).to.equal(1);
    });

    it('holds one pending batch however long the collector is down', async () => {
      const p = new Process(collector, install);
      await p.tick();
      for (let i = 0; i < 20; i++) {
        p.count(1);
        p.event('signup');
        collector.failNext(undefined);
        await p.tick();
        p.advance(BACKOFF_MAX_MS);
      }
      expect(p.sender.state().pendingMetrics).to.equal(1);
      expect(p.buffer).to.have.length(20); // events wait in the bounded buffer
      await p.tick();
      await p.tick();
      expect(sdkTotal(collector)).to.equal(20);
      expect(collector.events).to.have.length(20);
    });

    it('drops a batch the collector says it will never take, and carries on', async () => {
      const p = new Process(collector, install);
      await p.tick();
      p.count(5);
      collector.failNext({ status: 413, contract: '3' });
      expect(await p.tick()).to.equal('v3');
      expect(p.logger.warn.calledOnce).to.equal(true);
      p.count(2);
      await p.tick();
      expect(sdkTotal(collector)).to.equal(2);
      expect(
        v3Requests(collector, 'metrics').map((r) => r.body.sequence),
      ).to.deep.equal([1, 2]);
    });
  });

  describe('fallback to the legacy endpoints', () => {
    it('uses them when the collector does not answer as v3, remembers that, and asks again a day later', async () => {
      collector.legacyOnly = true;
      const p = new Process(collector, install);
      p.count(3);
      p.event('login', { email: 'jane@acme.com' });
      expect(await p.tick()).to.equal('legacy');
      expect(collector.paths()).to.deep.equal([
        '/v3/installs',
        '/collect-metrics',
        '/collect-events',
      ]);

      p.count(1);
      p.advance(NOT_V3_TTL_MS - 1);
      expect(await p.tick()).to.equal('legacy');
      expect(v3Requests(collector, 'installs')).to.have.length(1);

      // A day on, the collector has been upgraded.
      collector.legacyOnly = false;
      p.advance(1);
      p.count(2);
      expect(await p.tick()).to.equal('v3');
      expect(v3Requests(collector, 'installs')).to.have.length(2);
      // Only what was counted after the last legacy push goes over v3.
      expect(sdkTotal(collector)).to.equal(2);
    });

    it('falls back when a v3 collector is replaced by an older one, and loses no event', async () => {
      const p = new Process(collector, install);
      p.count(1);
      await p.tick();
      collector.legacyOnly = true;
      p.count(4);
      p.event('signup', { email: 'jane@acme.com' });
      expect(await p.tick()).to.equal('legacy');
      expect(collector.legacyMetrics).to.have.length(1);
      expect(collector.legacyEvents).to.have.length(1);
      expect(p.sender.state().pendingMetrics).to.equal(undefined);

      // The events of a batch that was pending go to the legacy endpoint too.
      collector.legacyOnly = false;
      p.advance(NOT_V3_TTL_MS + 1);
      p.event('login');
      collector.failNext(undefined); // no new counts, so the events push is the request; it gets no answer
      await p.tick();
      expect(p.sender.state().pendingEvents).to.equal(1);
      collector.legacyOnly = true;
      p.advance(BACKOFF_BASE_MS);
      expect(await p.tick()).to.equal('legacy');
      expect(
        collector.legacyEvents[1].events.map((e: BufferedEvent) => e.event),
      ).to.deep.equal(['login']);
    });

    it('treats any answer without the contract header as not v3, whatever its status', async () => {
      for (const status of [404, 405, 200, 302, 401, 500]) {
        const c = new FakeCollector();
        c.failNext({ status, body: '<html>login</html>' });
        const p = new Process(c, new Install());
        expect(await p.tick(), String(status)).to.equal('legacy');
        expect(p.sender.state().notV3Until, String(status)).to.equal(
          p.now + NOT_V3_TTL_MS,
        );
      }
    });

    it('never tries v3 when the setting says legacy', async () => {
      const p = new Process(collector, install, 'legacy');
      p.count(3);
      for (let i = 0; i < 3; i++) expect(await p.tick()).to.equal('legacy');
      expect(
        collector.paths().filter((path) => path.startsWith('/v3')),
      ).to.deep.equal([]);
    });

    it('never uses the legacy endpoints when the setting says v3', async () => {
      collector.legacyOnly = true;
      const p = new Process(collector, install, 'v3');
      p.count(3);
      p.event('signup');
      for (let i = 0; i < 4; i++) {
        expect(await p.tick()).to.equal('none');
        p.advance(BACKOFF_MAX_MS);
      }
      expect(collector.legacyMetrics).to.have.length(0);
      collector.legacyOnly = false;
      expect(await p.tick()).to.equal('v3');
      expect(sdkTotal(collector)).to.equal(3);
      expect(collector.events).to.have.length(1);
    });
  });

  describe('a refused credential', () => {
    it('picks up a secret that was rotated elsewhere, without losing the batch', async () => {
      const p = new Process(collector, install);
      await p.tick();
      p.count(5);
      // Another replica rotates: the collector has the new secret before the configuration does.
      const rotated = await collector.post(
        v3Url(SERVER_URL, 'installs'),
        { installId: INSTALL },
        { Authorization: `Bearer ${INSTALL}.${install.secret!}` },
      );
      const newSecret = (rotated!.body as { secret: string }).secret;
      expect(await p.tick()).to.equal('none'); // 401
      expect(await p.tick()).to.equal('none'); // the configuration still has the old one
      install.secret = newSecret;
      expect(await p.tick()).to.equal('v3');
      expect(sdkTotal(collector)).to.equal(5);
      expect(collector.legacyMetrics).to.have.length(0);
    });

    it('registers again when the collector has forgotten the install', async () => {
      const p = new Process(collector, install);
      p.count(1);
      await p.tick();
      const old = install.secret;
      collector.secrets.clear(); // the collector lost its install store
      p.count(5);
      const routes: string[] = [];
      for (let i = 0; i < 5; i++) routes.push(await p.tick());
      expect(routes[routes.length - 1]).to.equal('v3');
      expect(install.secret).to.not.equal(old);
      expect(install.secret).to.equal(collector.secrets.get(INSTALL));
      expect(sdkTotal(collector)).to.equal(6);
    });

    it('falls back for a day when the secret stays refused and the id is taken', async () => {
      const p = new Process(collector, install);
      await p.tick();
      collector.secrets.set(INSTALL, 'held-by-someone-else');
      p.count(5);
      const routes: string[] = [];
      for (let i = 0; i < 8; i++) routes.push(await p.tick());
      expect(routes.slice(-2)).to.deep.equal(['legacy', 'legacy']);
      expect(p.sender.state().notV3Until).to.be.greaterThan(p.now);
      expect(collector.legacyMetrics.length).to.be.greaterThan(0);
    });
  });

  describe('more than one process', () => {
    it('adds up the pushes of several workers', async () => {
      const workers = [1, 2, 3].map(() => new Process(collector, install));
      await workers[0]!.tick();
      workers.forEach((w, i) => w.count((i + 1) * 10));
      await Promise.all(workers.map((w) => w.tick()));
      workers[1]!.count(5);
      await workers[1]!.tick();
      expect(sdkTotal(collector)).to.equal(65);
      expect(new Set(workers.map((w) => w.sender.processId)).size).to.equal(3);
      expect(v3Requests(collector, 'installs')).to.have.length(1);
      // Each worker numbers its own pushes from 1.
      const sequences = v3Requests(collector, 'metrics').map(
        (r) =>
          `${r.body.processId === workers[1]!.sender.processId ? 'b' : 'x'}${String(r.body.sequence)}`,
      );
      expect(sequences.filter((s) => s === 'b1' || s === 'b2')).to.deep.equal([
        'b1',
        'b2',
      ]);
    });

    it('starts again safely after a restart: a new process id, numbered from 1, nothing counted twice', async () => {
      const before = new Process(collector, install);
      before.count(7);
      await before.tick();
      before.count(2);
      collector.failNext(undefined);
      await before.tick(); // unacknowledged when the process dies: these 2 are lost with it

      const after = new Process(collector, install);
      after.count(3); // a fresh registry
      expect(await after.tick()).to.equal('v3');
      expect(after.sender.processId).to.not.equal(before.sender.processId);
      const last = v3Requests(collector, 'metrics').pop()!;
      expect(last.body.sequence).to.equal(1);
      expect(v3Requests(collector, 'installs')).to.have.length(1);
      expect(sdkTotal(collector)).to.equal(10);
    });
  });

  describe('secrecy', () => {
    it('never logs the secret, in success or in failure', async () => {
      const p = new Process(collector, install);
      p.count(1);
      await p.tick();
      const secret = install.secret!;
      collector.failNext(
        { status: 502, contract: '3' },
        { status: 400, contract: '3' },
        { status: 401, contract: '3' },
      );
      for (let i = 0; i < 6; i++) {
        p.count(1);
        await p.tick();
        p.advance(BACKOFF_MAX_MS);
      }
      install.failWrites = true;
      collector.secrets.clear();
      for (let i = 0; i < 6; i++) await p.tick();
      const logged = JSON.stringify([
        ...p.logger.debug.args,
        ...p.logger.warn.args,
      ]);
      expect(logged).to.not.include(secret);
      for (const issued of collector.secrets.values())
        expect(logged).to.not.include(issued);
      expect(JSON.stringify(p.sender.state())).to.not.include(secret);
    });
  });

  describe('over real HTTP', () => {
    it('registers, sends, and tells a v3 collector from an older one by the header', async () => {
      const served = await collector.listen();
      try {
        const http = axiosV3Http({ timeoutMs: 2000 });
        const url = `${served.baseUrl}/collect-metrics`;
        const registered = await http.post(
          v3Url(url, 'installs'),
          { installId: INSTALL, version: '1.0.0', edition: 'oss' },
          {},
        );
        expect(registered?.status).to.equal(201);
        expect(registered?.contract).to.equal('3');
        const secret = (registered?.body as { secret: string }).secret;

        const pushed = await http.post(
          v3Url(url, 'metrics'),
          {
            processId: 'p1',
            sequence: 1,
            points: [
              {
                name: 'pipeshub.usage.sdk.requests',
                type: 'counter',
                labels: SDK,
                value: 2,
              },
            ],
          },
          { Authorization: `Bearer ${INSTALL}.${secret}` },
        );
        expect(pushed?.status).to.equal(200);
        expect(sdkTotal(collector)).to.equal(2);

        collector.failNext({
          status: 429,
          contract: '3',
          retryAfterSeconds: 30,
        });
        const limited = await http.post(v3Url(url, 'metrics'), {}, {});
        expect(limited).to.include({
          status: 429,
          contract: '3',
          retryAfterSeconds: 30,
        });

        collector.legacyOnly = true;
        const old = await http.post(v3Url(url, 'installs'), {}, {});
        expect(old?.status).to.equal(404);
        expect(old?.contract).to.equal(undefined);

        collector.legacyOnly = false;
        collector.failNext(undefined); // the connection is dropped
        expect(await http.post(v3Url(url, 'metrics'), {}, {})).to.equal(
          undefined,
        );
      } finally {
        await served.close();
      }
    });
  });
});
