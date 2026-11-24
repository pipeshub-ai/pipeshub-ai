import { expect } from 'chai';
import sinon from 'sinon';
import axios from 'axios';
import { TelemetryService } from '../../../../src/libs/services/telemetry/telemetry.service';
import * as cmConfigModule from '../../../../src/modules/configuration_manager/config/config';
import { StoreType } from '../../../../src/libs/keyValueStore/constants/KeyValueStoreType';
import * as flag from '../../../../src/libs/services/telemetry/flag';
import { metricsBackend } from '../../../../src/libs/services/telemetry/metrics-backend';
import { eventBuffer, recordEvent } from '../../../../src/libs/services/telemetry/event-buffer';
import { toggleMetricsCollection } from '../../../../src/modules/configuration_manager/controller/cm_controller';

// An opt-out sends nothing: not in the window between the request and the config reload it
// causes, and not by a reload that read the old consent before the opt-out was written.
const stored = (enabled: string) =>
  JSON.stringify({
    serverUrl: 'http://localhost:3031/collect-metrics',
    apiKey: 'k',
    installId: 'install-window',
    appVersion: '1',
    pushIntervalMs: '60000',
    enableMetricCollection: enabled,
  });
const flushAsync = () => new Promise<void>((resolve) => setImmediate(resolve));
const response = () => {
  const res: any = { status: sinon.stub(), json: sinon.stub(), end: sinon.stub() };
  res.status.returns(res);
  res.json.returns(res);
  res.end.returns(res);
  return res;
};
const optOut = (kv: any) =>
  toggleMetricsCollection(kv)(
    { body: { enableMetricCollection: false }, user: { orgId: 'o' }, headers: {} } as any,
    response(),
    sinon.stub(),
  );

describe('telemetry opt-out window', () => {
  let sandbox: sinon.SinonSandbox;
  let savedFlag: string | undefined;

  beforeEach(() => {
    sandbox = sinon.createSandbox();
    savedFlag = process.env.TELEMETRY_COLLECTION_FLAG;
    process.env.TELEMETRY_COLLECTION_FLAG = 'true';
    sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns({
      storeType: StoreType.Redis,
      storeConfig: { host: 'http://localhost', port: 2379, dialTimeout: 2000 },
      redisConfig: { host: 'localhost', port: 6379, password: undefined, tls: false, db: 0, keyPrefix: 'p:' },
      secretKey: 'a'.repeat(64),
      algorithm: 'aes-256-gcm',
    } as any);
    eventBuffer.drain();
  });

  afterEach(() => {
    (TelemetryService.current() as any)?.stopMetricsPush();
    (TelemetryService as any).instance = undefined;
    eventBuffer.drain();
    sandbox.restore();
    if (savedFlag === undefined) delete process.env.TELEMETRY_COLLECTION_FLAG;
    else process.env.TELEMETRY_COLLECTION_FLAG = savedFlag;
    flag.setCollectionConsent(true);
  });

  async function runningService(consent: string) {
    // The push loop itself is not what is under test: stub its start, arm an interval by hand.
    sandbox.stub(TelemetryService.prototype as any, 'startOrStopMetricCollection').resolves();
    const kv = { get: sandbox.stub().resolves(stored(consent)), set: sandbox.stub().resolves(), watchKey: sandbox.stub().resolves() };
    const svc = new TelemetryService(kv as any);
    await flushAsync();
    await flushAsync();
    return { svc, kv };
  }

  it('P1: a push or shipment that fires between the opt-out request and the reload sends nothing', async () => {
    const post = sandbox.stub(axios, 'post').resolves({ data: {} });
    const { svc } = await runningService('true');
    flag.setCollectionConsent(true);
    recordEvent('before_opt_out');

    await optOut({ get: sandbox.stub().resolves(stored('true')), set: sandbox.stub().resolves() });
    // The interval is still armed until the watch-triggered reload runs: a tick fires now.
    await (svc as any).pushMetricsToServer();
    await (svc as any).shipEventsToServer();

    expect(post.called, 'an opt-out window must not send').to.be.false;
  });

  it('P1: the opt-out stops the push interval itself', async () => {
    const { svc } = await runningService('true');
    flag.setCollectionConsent(true);
    (svc as any).pushInterval = setInterval(() => undefined, 60_000);

    await optOut({ get: sandbox.stub().resolves(stored('true')), set: sandbox.stub().resolves() });

    expect((svc as any).pushInterval).to.be.null;
  });

  it('P1: events recorded after the opt-out are not drained by a tick either', async () => {
    const { svc } = await runningService('true');
    flag.setCollectionConsent(false);
    eventBuffer.drain();
    const drain = sandbox.spy(eventBuffer, 'drain');

    await (svc as any).shipEventsToServer();

    expect(drain.called).to.be.false;
  });

  it('P6: an opt-out during the start delay of a real start leaves no interval and sends nothing', async () => {
    const clock = sinon.useFakeTimers({ toFake: ['setTimeout', 'setInterval', 'clearInterval'] });
    try {
      const post = sandbox.stub(axios, 'post').resolves({ data: {} });
      const kv = { get: sandbox.stub().resolves(stored('true')), set: sandbox.stub().resolves(), watchKey: sandbox.stub().resolves() };
      const svc = new TelemetryService(kv as any);
      for (let i = 0; i < 5; i++) await flushAsync(); // the reload ran; the push start waits in its delay

      await optOut({ get: sandbox.stub().resolves(stored('true')), set: sandbox.stub().resolves() });
      await clock.tickAsync(250);
      expect((svc as any).pushInterval, 'an interval was armed after the opt-out').to.equal(null);
      await clock.tickAsync(200_000);
      expect(post.called).to.equal(false);
    } finally {
      clock.restore();
    }
  });

  it('P2: a reload that read "true" before the opt-out was written cannot switch consent back on', async () => {
    const { svc, kv } = await runningService('false');
    let release!: (raw: string) => void;
    kv.get.onCall(kv.get.callCount).returns(new Promise<string>((resolve) => (release = resolve)));
    const reload = (svc as any).initializeMetricsCollection(); // started by the earlier opt-in write
    await flushAsync();

    await optOut({ get: sandbox.stub().resolves(stored('true')), set: sandbox.stub().resolves() });
    expect(flag.isCollecting()).to.equal(false);
    release(stored('true'));
    await reload;

    expect(flag.isCollecting(), 'the stale reload re-enabled collection').to.equal(false);
    expect((svc as any).enableMetricCollection).to.equal(false);
  });
});

describe('telemetry consent gauge at startup', () => {
  let sandbox: sinon.SinonSandbox;
  beforeEach(() => {
    sandbox = sinon.createSandbox();
    process.env.TELEMETRY_COLLECTION_FLAG = 'true';
    sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns({
      storeType: StoreType.Redis,
      storeConfig: { host: 'http://localhost', port: 2379, dialTimeout: 2000 },
      redisConfig: { host: 'localhost', port: 6379, password: undefined, tls: false, db: 0, keyPrefix: 'p:' },
      secretKey: 'a'.repeat(64),
      algorithm: 'aes-256-gcm',
    } as any);
    sandbox.stub(TelemetryService.prototype as any, 'startOrStopMetricCollection').resolves();
    metricsBackend.reset();
  });
  afterEach(() => {
    (TelemetryService as any).instance = undefined;
    sandbox.restore();
    flag.setCollectionConsent(true);
    metricsBackend.reset();
  });

  const gaugeLine = async () =>
    (await metricsBackend.serialize()).split('\n').find((l) => l.startsWith('pipeshub_metric_collection_enabled{'));

  it('is 1 once the stored consent is read as on, without any opt-in request', async () => {
    const kv = { get: sandbox.stub().resolves(stored('true')), set: sandbox.stub().resolves(), watchKey: sandbox.stub().resolves() };
    new TelemetryService(kv as any); // eslint-disable-line no-new
    await flushAsync();
    await flushAsync();
    expect(await gaugeLine()).to.match(/ 1$/);
  });

  it('is written even when consent was off in this process before the stored consent was read', async () => {
    flag.setCollectionConsent(false);
    const kv = { get: sandbox.stub().resolves(stored('true')), set: sandbox.stub().resolves(), watchKey: sandbox.stub().resolves() };
    new TelemetryService(kv as any); // eslint-disable-line no-new
    await flushAsync();
    await flushAsync();
    expect(await gaugeLine()).to.match(/ 1$/);
  });

  it('stays one series after a restart followed by an opt-in', async () => {
    const kv = { get: sandbox.stub().resolves(stored('true')), set: sandbox.stub().resolves(), watchKey: sandbox.stub().resolves() };
    new TelemetryService(kv as any); // eslint-disable-line no-new
    await flushAsync();
    await flushAsync();
    await toggleMetricsCollection({ get: sandbox.stub().resolves(stored('false')), set: sandbox.stub().resolves() } as any)(
      { body: { enableMetricCollection: true }, user: { orgId: 'o' }, headers: {} } as any,
      response(),
      sinon.stub(),
    );
    const lines = (await metricsBackend.serialize()).split('\n').filter((l) => l.startsWith('pipeshub_metric_collection_enabled{'));
    expect(lines).to.have.length(1);
    expect(lines[0]).to.match(/ 1$/);
  });

  it('is not written while the stored consent is off', async () => {
    const kv = { get: sandbox.stub().resolves(stored('false')), set: sandbox.stub().resolves(), watchKey: sandbox.stub().resolves() };
    new TelemetryService(kv as any); // eslint-disable-line no-new
    await flushAsync();
    await flushAsync();
    expect(await gaugeLine()).to.equal(undefined);
  });
});
