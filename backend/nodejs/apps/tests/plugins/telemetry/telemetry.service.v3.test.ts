import { expect } from 'chai';
import sinon from 'sinon';
import axios from 'axios';
import { TelemetryService } from '../../../src/plugins/telemetry/telemetry.service';
import * as cmConfigModule from '../../../src/modules/configuration_manager/config/config';
import { configPaths } from '../../../src/modules/configuration_manager/paths/paths';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import {
  recordEvent,
  eventBuffer,
} from '../../../src/plugins/telemetry/event-buffer';
import { metricsBackend } from '../../../src/plugins/telemetry/metrics-backend';
import { USAGE_TELEMETRY_CONTRACT } from '../../../src/plugins/telemetry/v3/sender';
import { StoreType } from '../../../src/libs/keyValueStore/constants/KeyValueStoreType';
import { FakeCollector } from './v3/fake-collector';

const INSTALL = '0b9d7a52-7c0b-4c0e-9c0a-2f4f6a1d2e3f';
const SECRET_KEY = 'a'.repeat(64);
const ALGORITHM = 'aes-256-gcm';
const SDK = {
  channel: 'sdk',
  sdk_language: 'python',
  sdk_version: '1.2.3',
  outcome: 'success',
};

// Collection is disabled so constructing the service never starts the real push interval.
const CONFIG = {
  serverUrl: 'http://localhost:3031/collect-metrics',
  apiKey: 'test-api-key',
  installId: INSTALL,
  appVersion: '9.9.9',
  pushIntervalMs: '60000',
  enableMetricCollection: 'false',
};

/** A key-value store that keeps what is written to it. */
function statefulKvStore(initial: string | null = JSON.stringify(CONFIG)) {
  let value = initial;
  return {
    get: sinon.stub().callsFake(async () => value),
    set: sinon.stub().callsFake(async (_key: string, next: string) => {
      value = next;
    }),
    watchKey: sinon.stub().resolves(undefined),
    current: () => value,
  };
}

const flushAsync = () => new Promise<void>((resolve) => setImmediate(resolve));

describe('TelemetryService: usage telemetry v3', () => {
  let sandbox: sinon.SinonSandbox;
  let collector: FakeCollector;
  let postStub: sinon.SinonStub;
  // The counter the plug-in's SDK module registered when it was loaded.
  let sdkRequests: {
    inc: (labels: Record<string, string>, value?: number) => void;
  };

  before(async () => {
    await import('../../../src/plugins/telemetry/modules/client-metrics');
    const registry = (metricsBackend as any).registry;
    sdkRequests = registry.getSingleMetric('pipeshub_usage_sdk_requests_total');
  });

  beforeEach(() => {
    sandbox = sinon.createSandbox();
    sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns({
      storeType: StoreType.Redis,
      storeConfig: { host: 'http://localhost', port: 2379, dialTimeout: 2000 },
      redisConfig: {
        host: 'localhost',
        port: 6379,
        password: undefined,
        tls: false,
        db: 0,
        keyPrefix: 'pipeshub:kv:',
      },
      secretKey: SECRET_KEY,
      algorithm: ALGORITHM,
    } as any);
    collector = new FakeCollector();
    postStub = sandbox
      .stub(axios, 'post')
      .callsFake(async (url: string, body?: unknown, config?: any) => {
        const answer = await collector.post(
          url,
          body,
          config?.headers?.Authorization
            ? { Authorization: config.headers.Authorization }
            : {},
        );
        if (answer === undefined) throw new Error('socket hang up');
        const response = {
          status: answer.status,
          headers:
            answer.contract === undefined
              ? {}
              : { 'x-usage-contract': answer.contract },
          data: answer.body,
        };
        // The legacy calls let axios throw on an error status; the v3 port asks for every status.
        if (answer.status >= 400 && config?.validateStatus === undefined) {
          throw Object.assign(new Error(`status ${String(answer.status)}`), {
            isAxiosError: true,
            response,
          });
        }
        return response;
      });
    eventBuffer.drain();
    metricsBackend.reset();
    // v3 is opt-in; these tests opt in unless they say otherwise.
    process.env[USAGE_TELEMETRY_CONTRACT] = 'auto';
  });

  afterEach(() => {
    const instance = TelemetryService.current();
    if (instance) (instance as any).stopMetricsPush();
    (TelemetryService as any).instance = undefined;
    eventBuffer.drain();
    metricsBackend.reset();
    delete process.env[USAGE_TELEMETRY_CONTRACT];
    sandbox.restore();
  });

  const decrypt = (raw: string) =>
    JSON.parse(
      EncryptionService.getInstance(ALGORITHM, SECRET_KEY).decrypt(raw),
    );
  const paths = () => collector.paths();

  it('registers once, keeps the secret encrypted next to the install id, and sends over v3', async () => {
    const kv = statefulKvStore();
    const svc = new TelemetryService(kv as any);
    await flushAsync();

    sdkRequests.inc(
      { service: 'node_api', ...SDK, org: 'org-1', domain: 'acme.com' },
      3,
    );
    recordEvent('login', {
      orgId: 'org-1',
      userId: 'user-1',
      email: 'jane@acme.com',
      domain: 'acme.com',
      first_login: true,
      auth_method: 'google',
    });
    await svc.flush();

    expect(paths()).to.deep.equal([
      '/v3/installs',
      '/v3/metrics',
      '/v3/events',
    ]);
    const secret = collector.secrets.get(INSTALL)!;
    const [key, stored] = kv.set.lastCall.args as [string, string];
    expect(key).to.equal(configPaths.metricsCollection);
    expect(stored).to.not.include(secret);
    expect(decrypt(stored)).to.deep.equal({ ...CONFIG, installSecret: secret });

    expect(collector.total('pipeshub.usage.sdk.requests', SDK)).to.equal(3);
    expect(collector.events).to.have.length(1);
    expect(collector.events[0].attributes).to.deep.equal({
      auth_method: 'google',
      first_login: true,
    });
    const sent = JSON.stringify(
      collector.requests
        .filter((r) => r.path !== '/v3/installs')
        .map((r) => r.body),
    );
    for (const personal of ['org-1', 'user-1', 'jane@acme.com', 'acme.com']) {
      expect(sent, personal).to.not.include(personal);
    }

    // The next push uses the stored secret; counters go as what was counted since.
    sdkRequests.inc(
      { service: 'node_api', ...SDK, org: 'org-2', domain: 'example.org' },
      2,
    );
    await svc.flush();
    expect(paths().filter((p) => p === '/v3/installs')).to.have.length(1);
    expect(collector.total('pipeshub.usage.sdk.requests', SDK)).to.equal(5);
    expect(collector.legacyMetrics).to.have.length(0);
  });

  it('uses a secret that is already stored, without registering', async () => {
    collector.secrets.set(INSTALL, 'stored-secret');
    const kv = statefulKvStore(
      JSON.stringify({ ...CONFIG, installSecret: 'stored-secret' }),
    );
    const svc = new TelemetryService(kv as any);
    await flushAsync();
    sdkRequests.inc(
      { service: 'node_api', ...SDK, org: 'org-1', domain: 'acme.com' },
      1,
    );
    await svc.flush();
    expect(paths()).to.deep.equal(['/v3/metrics']);
    expect(collector.requests[0]!.headers.Authorization).to.equal(
      `Bearer ${INSTALL}.stored-secret`,
    );
  });

  it('falls back to the legacy endpoints against a collector from before v3', async () => {
    collector.legacyOnly = true;
    const kv = statefulKvStore();
    const svc = new TelemetryService(kv as any);
    await flushAsync();
    const writes = kv.set.callCount;

    sdkRequests.inc(
      { service: 'node_api', ...SDK, org: 'org-1', domain: 'acme.com' },
      3,
    );
    recordEvent('login', { orgId: 'org-1' });
    await svc.flush();
    await svc.flush();

    // Asked once; the answer is remembered.
    expect(paths()).to.deep.equal([
      '/v3/installs',
      '/collect-metrics',
      '/collect-events',
      '/collect-metrics',
    ]);
    expect(collector.legacyMetrics[0].instanceId).to.equal(INSTALL);
    expect(collector.legacyMetrics[0].metrics).to.include(
      'pipeshub_usage_sdk_requests_total',
    );
    expect(collector.legacyEvents[0].events[0].event).to.equal('login');
    expect(kv.set.callCount).to.equal(writes);
  });

  it('uses v3 alone, never the legacy endpoints, when the setting says v3', async () => {
    process.env[USAGE_TELEMETRY_CONTRACT] = 'v3';
    collector.legacyOnly = true;
    const svc = new TelemetryService(statefulKvStore() as any);
    await flushAsync();
    sdkRequests.inc(
      { service: 'node_api', ...SDK, org: 'org-1', domain: 'acme.com' },
      2,
    );
    await svc.flush();
    expect(paths()).to.deep.equal(['/v3/installs']);
    collector.legacyOnly = false;
    (svc as any).v3.retryAt = 0;
    await svc.flush();
    expect(paths()).to.deep.equal([
      '/v3/installs',
      '/v3/installs',
      '/v3/metrics',
    ]);
    expect(collector.total('pipeshub.usage.sdk.requests', SDK)).to.equal(2);
  });

  it('never asks for v3, and registers nothing, when the setting is unset', async () => {
    delete process.env[USAGE_TELEMETRY_CONTRACT];
    const kv = statefulKvStore();
    const svc = new TelemetryService(kv as any);
    await flushAsync();
    const writes = kv.set.callCount;
    sdkRequests.inc(
      { service: 'node_api', ...SDK, org: 'org-1', domain: 'acme.com' },
      1,
    );
    recordEvent('login', { orgId: 'org-1' });
    await svc.flush();
    expect(paths()).to.deep.equal(['/collect-metrics', '/collect-events']);
    expect(collector.secrets.size).to.equal(0);
    expect(kv.set.callCount).to.equal(writes);
  });

  it('never asks for v3 when the setting says legacy', async () => {
    process.env[USAGE_TELEMETRY_CONTRACT] = 'legacy';
    const svc = new TelemetryService(statefulKvStore() as any);
    await flushAsync();
    sdkRequests.inc(
      { service: 'node_api', ...SDK, org: 'org-1', domain: 'acme.com' },
      1,
    );
    await svc.flush();
    expect(paths()).to.deep.equal(['/collect-metrics']);
  });

  it('sends nothing on either contract while the install has no id', async () => {
    const svc = new TelemetryService(statefulKvStore() as any);
    await flushAsync();
    (svc as any).instanceId = '';
    recordEvent('login', {});
    await svc.flush();
    expect(postStub.called).to.equal(false);
  });

  it('does not replace the stored configuration when it cannot be read', async () => {
    const kv = statefulKvStore();
    const svc = new TelemetryService(kv as any);
    await flushAsync();
    const writes = kv.set.callCount;
    // The store answers nothing readable at the moment the secret is to be stored.
    kv.get.callsFake(async () => null);
    sdkRequests.inc(
      { service: 'node_api', ...SDK, org: 'org-1', domain: 'acme.com' },
      1,
    );
    await svc.flush();
    expect(kv.set.callCount).to.equal(writes);
    // The push itself went through; the secret is stored once the store reads again.
    expect(collector.total('pipeshub.usage.sdk.requests', SDK)).to.equal(1);
    kv.get.callsFake(async () => kv.current());
    await svc.flush();
    expect(kv.set.callCount).to.equal(writes + 1);
    expect(decrypt(kv.current()!).installSecret).to.equal(
      collector.secrets.get(INSTALL),
    );
  });

  it('does not let two pushes run at once', async () => {
    const svc = new TelemetryService(statefulKvStore() as any);
    await flushAsync();
    sdkRequests.inc(
      { service: 'node_api', ...SDK, org: 'org-1', domain: 'acme.com' },
      4,
    );
    await Promise.all([svc.flush(), svc.flush(), svc.flush()]);
    expect(paths().filter((p) => p === '/v3/installs')).to.have.length(1);
    expect(collector.total('pipeshub.usage.sdk.requests', SDK)).to.equal(4);
  });
});
