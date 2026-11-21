import { expect } from 'chai';
import { recordHttpRequest, sessionApiSurface } from '../../../../../src/libs/services/telemetry/modules/http-metrics';
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

  it('C20.48: counts the session API by surface and outcome, without an org label', async () => {
    recordHttpRequest('/api/v1/conversations/:conversationId/runs/:runId/stream', 'GET', 200, 'org1', 0.1, 'a.io');
    recordHttpRequest('/api/v1/agents/:agentKey/conversations/stream', 'POST', 503, 'org1', 0.1, 'a.io');
    recordHttpRequest('/api/v1/agent-harness/admin/dependencies', 'GET', 500, 'org1', 0.1, 'a.io');
    recordHttpRequest('/api/v1/conversations/:conversationId', 'GET', 500, 'org1', 0.1, 'a.io');

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'pipeshub_agent_harness_session_api_requests_total{surface="conversation_runs",outcome="ok"} 1',
    );
    expect(text).to.include(
      'pipeshub_agent_harness_session_api_requests_total{surface="agent_conversations",outcome="server_error"} 1',
    );
    expect(text).to.include(
      'pipeshub_agent_harness_session_api_requests_total{surface="agent_harness",outcome="server_error"} 1',
    );
    expect(text).to.not.match(/pipeshub_agent_harness_session_api_requests_total\{[^}]*org=/);
  });

  it('C20.48: names the session API surfaces only, never the internal routes', () => {
    expect(sessionApiSurface('/api/v1/conversations/:id/runs')).to.equal('conversation_runs');
    expect(sessionApiSurface('/api/v1/conversations/:id/runs/:runId/jobs')).to.equal('conversation_runs');
    expect(sessionApiSurface('/api/v1/agents/:agentKey/conversations')).to.equal('agent_conversations');
    expect(sessionApiSurface('/api/v1/agent-harness/admin/dependencies')).to.equal('agent_harness');
    expect(sessionApiSurface('/api/v1/agent-harness/internal/metrics')).to.equal(null);
    expect(sessionApiSurface('/api/v1/conversations/:id')).to.equal(null);
    expect(sessionApiSurface('/api/v1/conversations/:id/runsx')).to.equal(null);
    expect(sessionApiSurface('unmatched')).to.equal(null);
  });

  // Must run last: fills the module-level unique-route set up to the cap.
  it('should collapse routes beyond the unique-route cap into "other"', async () => {
    for (let i = 0; i < 300; i++) {
      recordHttpRequest(`/api/v1/cap/${i}`, 'GET', 200, 'org1', undefined, 'a.io');
    }
    recordHttpRequest('/api/v1/overflow', 'GET', 200, 'org1', undefined, 'a.io');
    recordHttpRequest('/api/v1/repeat', 'GET', 200, 'org2', undefined, 'x.io');

    const text = await metricsBackend.serialize();
    expect(text).to.not.include('route="/api/v1/overflow"');
    expect(text).to.match(/pipeshub_http_requests_total\{[^}]*route="other"/);
    // Routes seen before the cap keep recording under their own label.
    expect(text).to.include(
      'pipeshub_http_requests_total{service="node_api",route="/api/v1/repeat",method="GET",status="200",org="org2",domain="x.io"} 3',
    );
  });
});
