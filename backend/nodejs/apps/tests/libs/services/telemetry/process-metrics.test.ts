import { expect } from 'chai';
import type { AddressInfo } from 'net';
import {
  EVENT_LOOP_UTILIZATION_METRIC,
  createProcessMetricsRegistry,
  createProcessMetricsServer,
} from '../../../../src/libs/services/telemetry/process-metrics';

function busyFor(ms: number): void {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    // the event loop is busy on purpose
  }
}

function utilization(text: string): number {
  const line = text
    .split('\n')
    .find((l) => l.startsWith(`${EVENT_LOOP_UTILIZATION_METRIC} `));
  expect(line, text).to.be.a('string');
  return Number(line!.split(' ')[1]);
}

describe('process metrics (PERF-E autoscaling signal)', () => {
  it('reports the event loop busy share since the previous scrape', async () => {
    const registry = createProcessMetricsRegistry();
    await registry.metrics();
    busyFor(120);
    const busy = utilization(await registry.metrics());
    await new Promise((resolve) => setTimeout(resolve, 150));
    const idle = utilization(await registry.metrics());
    expect(busy).to.be.greaterThan(0.6);
    expect(idle).to.be.lessThan(0.5);
  });

  it('serves GET /metrics and nothing else', async () => {
    const server = createProcessMetricsServer().listen(0, '127.0.0.1');
    await new Promise((resolve) => server.once('listening', resolve));
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    try {
      const ok = await fetch(`${base}/metrics`);
      expect(ok.status).to.equal(200);
      expect(ok.headers.get('content-type')).to.include('text/plain');
      expect(await ok.text()).to.include(EVENT_LOOP_UTILIZATION_METRIC);
      expect((await fetch(`${base}/`)).status).to.equal(404);
      expect((await fetch(`${base}/metrics`, { method: 'POST' })).status).to.equal(404);
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });
});
