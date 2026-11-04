import { expect } from 'chai';
import {
  recordClientRequest,
  recordMcpMessages,
} from '../../../../../src/libs/services/telemetry/modules/client-metrics';
import { metricsBackend } from '../../../../../src/libs/services/telemetry/metrics-backend';

describe('telemetry modules/client-metrics', () => {
  beforeEach(() => metricsBackend.reset());

  it('should count SDK requests with their language, route and org', async () => {
    recordClientRequest({ client: 'sdk', sdk: 'py' }, 'none', '/api/v1/search', 'POST', 200, 'org1', 'acme.io');

    const text = await metricsBackend.serialize();
    expect(text).to.match(
      /pipeshub_client_requests_total\{[^}]*client="sdk",sdk="py",transport="none",route="\/api\/v1\/search",method="POST",status="200",org="org1",domain="acme.io"\} 1/,
    );
  });

  it('should not count web app traffic', async () => {
    recordClientRequest({ client: 'other', sdk: 'none' }, 'none', '/api/v1/search', 'GET', 200);

    expect(await metricsBackend.serialize()).to.not.include('pipeshub_client_requests_total{');
  });

  it('should count each JSON-RPC message, with the tool for tools/call', async () => {
    recordMcpMessages(
      [
        { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'pipeshub_search' } },
        { jsonrpc: '2.0', id: 2, method: 'tools/list' },
        { jsonrpc: '2.0', id: 3, method: 'x/unknown' },
      ],
      200,
      'org1',
      'acme.io',
    );

    const text = await metricsBackend.serialize();
    expect(text).to.include('pipeshub_mcp_calls_total{method="tools/call",tool="pipeshub_search",status="ok",org="org1",domain="acme.io"} 1');
    expect(text).to.include('pipeshub_mcp_calls_total{method="tools/list",tool="none",status="ok",org="org1",domain="acme.io"} 1');
    expect(text).to.include('pipeshub_mcp_calls_total{method="other",tool="none",status="ok",org="org1",domain="acme.io"} 1');
  });

  it('should collapse an unsafe tool name and mark HTTP errors', async () => {
    recordMcpMessages({ method: 'tools/call', params: { name: '"} 1\nevil' } }, 401);

    const text = await metricsBackend.serialize();
    expect(text).to.include('pipeshub_mcp_calls_total{method="tools/call",tool="other",status="error",org="unknown",domain="unknown"} 1');
  });

  it('should ignore a body that is not JSON-RPC', async () => {
    recordMcpMessages(undefined, 200);
    recordMcpMessages('text', 200);

    expect(await metricsBackend.serialize()).to.not.include('pipeshub_mcp_calls_total{');
  });
});
