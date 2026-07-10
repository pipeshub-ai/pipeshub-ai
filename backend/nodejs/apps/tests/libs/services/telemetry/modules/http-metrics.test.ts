import { expect } from 'chai';
import { recordHttpRequest } from '../../../../../src/libs/services/telemetry/modules/http-metrics';
import { metricsBackend } from '../../../../../src/libs/services/telemetry/metrics-backend';

describe('telemetry modules/http-metrics', () => {
  it('should count a request with all labels set', async () => {
    recordHttpRequest('/api/v1/users', 'GET', 200, 'org1', 0.05, 'acme.io');

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'pipeshub_http_requests_total{service="node_api",route="/api/v1/users",method="GET",status="200",org="org1",domain="acme.io"} 1',
    );
  });

  it('should observe latency only when a duration is provided', async () => {
    recordHttpRequest('/api/v1/timed', 'POST', 201, 'org1', 0.2, 'acme.io');
    recordHttpRequest('/api/v1/untimed', 'POST', 201, 'org1', undefined, 'acme.io');

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'pipeshub_http_request_duration_seconds_count{service="node_api",route="/api/v1/timed",method="POST"} 1',
    );
    expect(text).to.not.include('route="/api/v1/untimed",method="POST"} 1\n');
    expect(text).to.not.match(
      /pipeshub_http_request_duration_seconds_count\{[^}]*untimed/,
    );
  });

  it('should default empty route to "unmatched" and missing org/domain to "unknown"', async () => {
    recordHttpRequest('', 'PUT', 404);

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'pipeshub_http_requests_total{service="node_api",route="unmatched",method="PUT",status="404",org="unknown",domain="unknown"} 1',
    );
  });

  it('should accumulate counts across calls with identical labels', async () => {
    recordHttpRequest('/api/v1/repeat', 'GET', 200, 'org2', 0.01, 'x.io');
    recordHttpRequest('/api/v1/repeat', 'GET', 200, 'org2', 0.01, 'x.io');

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'pipeshub_http_requests_total{service="node_api",route="/api/v1/repeat",method="GET",status="200",org="org2",domain="x.io"} 2',
    );
  });
});
