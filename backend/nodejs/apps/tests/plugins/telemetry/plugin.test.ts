import { expect } from 'chai';
import sinon from 'sinon';
import { emitPlatformEvent } from '../../../src/libs/platform-events';
import { PluginHost } from '../../../src/libs/plugins';
import { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service';
import * as cmConfig from '../../../src/modules/configuration_manager/config/config';
import telemetryPlugin from '../../../src/plugins/telemetry';
import { eventBuffer } from '../../../src/plugins/telemetry/event-buffer';
import * as refresh from '../../../src/plugins/telemetry/metrics.refresh.service';
import * as service from '../../../src/plugins/telemetry/telemetry.service';

describe('telemetry plug-in lifecycle', () => {
  let host: PluginHost;
  const saved = process.env.TELEMETRY_COLLECTION_FLAG;

  beforeEach(() => {
    host = new PluginHost();
    eventBuffer.drain();
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({} as never);
    sinon.stub(KeyValueStoreService, 'getInstance').returns({ kv: true } as never);
  });
  afterEach(() => {
    host.reset();
    sinon.restore();
    eventBuffer.drain();
    if (saved === undefined) delete process.env.TELEMETRY_COLLECTION_FLAG;
    else process.env.TELEMETRY_COLLECTION_FLAG = saved;
  });

  it('is named telemetry and loads cleanly', async () => {
    const report = await host.load([telemetryPlugin]);
    expect(report).to.deep.equal({ loaded: ['telemetry'], failed: [] });
  });

  it('installs one HTTP middleware and says collection is active', async () => {
    await host.load([telemetryPlugin]);
    expect(host.httpMiddlewares()).to.have.length(1);
    expect(host.getCapability('data-collection')?.isActive()).to.equal(true);
  });

  it('starts the push service and the org refresh on start, not on register', async () => {
    const start = sinon.stub(service, 'startTelemetry');
    const startRefresh = sinon.stub(refresh, 'startOrgMetricsRefresh');
    await host.load([telemetryPlugin]);
    expect(start.called).to.equal(false);
    expect(await host.start()).to.deep.equal([]);
    expect(start.calledOnceWithExactly({ kv: true } as never)).to.equal(true);
    expect(startRefresh.calledOnce).to.equal(true);
  });

  it('stops the refresh and shuts the push service down on shutdown', async () => {
    const timer = setInterval(() => undefined, 60_000);
    const cleared = sinon.spy(global, 'clearInterval');
    sinon.stub(service, 'startTelemetry');
    sinon.stub(refresh, 'startOrgMetricsRefresh').returns(timer);
    const shutdown = sinon.stub().resolves();
    sinon.stub(service.TelemetryService, 'current').returns({ shutdown } as never);
    await host.load([telemetryPlugin]);
    await host.start();
    expect(await host.shutdown()).to.deep.equal([]);
    expect(cleared.calledWith(timer)).to.equal(true);
    expect(shutdown.calledOnce).to.equal(true);
  });

  it('flushes when an organisation switches collection off, and not when it switches it on', async () => {
    const flush = sinon.stub().resolves();
    sinon.stub(service.TelemetryService, 'current').returns({ flush } as never);
    await host.load([telemetryPlugin]);
    emitPlatformEvent('org.data_collection_setting.changed', { orgId: 'o', enabled: true });
    expect(flush.called).to.equal(false);
    emitPlatformEvent('org.data_collection_setting.changed', { orgId: 'o', enabled: false });
    expect(flush.calledOnce).to.equal(true);
  });

  it('does nothing but report itself inactive when the flag is off', async () => {
    process.env.TELEMETRY_COLLECTION_FLAG = 'false';
    const start = sinon.stub(service, 'startTelemetry');
    await host.load([telemetryPlugin]);
    await host.start();
    emitPlatformEvent('user.signed_in', { firstSignIn: true, authMethod: 'password' });
    expect(host.httpMiddlewares()).to.have.length(0);
    expect(start.called).to.equal(false);
    expect(eventBuffer.size()).to.equal(0);
    expect(host.getCapability('data-collection')?.isActive()).to.equal(false);
    expect(await host.shutdown()).to.deep.equal([]);
  });
});

describe('TelemetryService.shutdown', () => {
  const make = (enabled: boolean): { svc: any; flush: sinon.SinonStub } => {
    const svc: any = Object.create(service.TelemetryService.prototype);
    svc.pushInterval = setInterval(() => undefined, 60_000);
    svc.enableMetricCollection = enabled;
    const flush = sinon.stub(svc, 'flush').resolves();
    return { svc, flush };
  };
  afterEach(() => sinon.restore());

  it('stops the push loop and ships what is buffered while the install consents', async () => {
    const { svc, flush } = make(true);
    await svc.shutdown();
    expect(svc.pushInterval).to.equal(null);
    expect(flush.calledOnce).to.equal(true);
  });

  it('sends nothing on the way out when the install has opted out', async () => {
    const { svc, flush } = make(false);
    await svc.shutdown();
    expect(svc.pushInterval).to.equal(null);
    expect(flush.called).to.equal(false);
  });
});
