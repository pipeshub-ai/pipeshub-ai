import { expect } from 'chai';
import { recordServiceActivity } from '../../../../../src/libs/services/telemetry/modules/activity-metrics';
import { metricsBackend } from '../../../../../src/libs/services/telemetry/metrics-backend';

describe('telemetry modules/activity-metrics', () => {
  it('counts an activation with org and domain, on the same labels as the Python counter', async () => {
    recordServiceActivity('pat_created', { org: 'org-a1', domain: 'acme.com' });

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'pipeshub_activity_total{service="node_api",activity="pat_created",connector="none",status="ok",org="org-a1",kb="none",domain="acme.com",mimetype="none"} 1',
    );
  });

  it('maps a missing org or domain to "unknown"', async () => {
    recordServiceActivity('mcp_connected');

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'pipeshub_activity_total{service="node_api",activity="mcp_connected",connector="none",status="ok",org="unknown",kb="none",domain="unknown",mimetype="none"}',
    );
  });
});
