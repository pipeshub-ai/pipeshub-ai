import { expect } from 'chai';
import { setMetricCollectionEnabled } from '../../../../../src/libs/services/telemetry/modules/collection-metrics';
import { metricsBackend } from '../../../../../src/libs/services/telemetry/metrics-backend';

describe('telemetry modules/collection-metrics', () => {
  it('should publish 1 when collection is enabled for an org', async () => {
    setMetricCollectionEnabled('org-1', true);

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'pipeshub_metric_collection_enabled{service="node_api",org="org-1"} 1',
    );
  });

  it('should publish 0 when collection is disabled (consent opt-out)', async () => {
    setMetricCollectionEnabled('org-2', false);

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'pipeshub_metric_collection_enabled{service="node_api",org="org-2"} 0',
    );
  });

  it('should map an empty org to "unknown"', async () => {
    setMetricCollectionEnabled('', true);

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'pipeshub_metric_collection_enabled{service="node_api",org="unknown"} 1',
    );
  });

  it('should overwrite the value for the same org on consent change', async () => {
    setMetricCollectionEnabled('org-3', true);
    setMetricCollectionEnabled('org-3', false);

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'pipeshub_metric_collection_enabled{service="node_api",org="org-3"} 0',
    );
    expect(text).to.not.include(
      'pipeshub_metric_collection_enabled{service="node_api",org="org-3"} 1',
    );
  });
});
