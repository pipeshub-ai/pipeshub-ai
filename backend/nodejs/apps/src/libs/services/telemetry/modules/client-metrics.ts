import { metricsBackend } from '../metrics-backend';
import { SERVICE_NAME } from '../constants';
import { ClientInfo } from '../client';
import { boundRoute } from './http-metrics';

// Only SDK and MCP requests are counted here: the web app's traffic is already
// in pipeshub_http_requests_total, and leaving it out keeps this metric's
// series to the routes those clients actually call.
const clientRequests = metricsBackend.createCounter({
  name: 'pipeshub_client_requests_total',
  help: 'HTTP requests from the SDKs and MCP clients',
  labelNames: [
    'service',
    'client',
    'sdk',
    'transport',
    'route',
    'method',
    'status',
    'org',
    'domain',
  ],
});

const mcpCalls = metricsBackend.createCounter({
  name: 'pipeshub_mcp_calls_total',
  help: 'JSON-RPC messages to the hosted MCP endpoint, by method and tool',
  labelNames: ['method', 'tool', 'status', 'org', 'domain'],
});

// JSON-RPC methods of the MCP spec; anything else is `other`.
const MCP_METHODS: ReadonlySet<string> = new Set([
  'initialize',
  'ping',
  'tools/list',
  'tools/call',
  'resources/list',
  'resources/read',
  'resources/templates/list',
  'prompts/list',
  'prompts/get',
  'completion/complete',
  'logging/setLevel',
  'notifications/initialized',
  'notifications/cancelled',
]);

// Tool names come from the client, so they are checked and capped like routes.
const TOOL_NAME = /^[A-Za-z0-9_.-]{1,64}$/;
const MAX_UNIQUE_TOOLS = 100;
const seenTools = new Set<string>();

function boundTool(name: unknown): string {
  if (typeof name !== 'string' || !TOOL_NAME.test(name)) return 'other';
  if (seenTools.has(name)) return name;
  if (seenTools.size >= MAX_UNIQUE_TOOLS) return 'other';
  seenTools.add(name);
  return name;
}

function labelOrUnknown(value: string | undefined): string {
  return value == null || value === '' ? 'unknown' : value;
}

export function recordClientRequest(
  info: ClientInfo,
  transport: 'hosted' | 'local' | 'none',
  route: string,
  method: string,
  statusCode: number,
  orgId?: string,
  domain?: string,
): void {
  if (info.client === 'other') return;
  clientRequests.inc({
    service: SERVICE_NAME,
    client: info.client,
    sdk: info.sdk,
    transport,
    route: boundRoute(route === '' ? 'unmatched' : route),
    method,
    status: String(statusCode),
    org: labelOrUnknown(orgId),
    domain: labelOrUnknown(domain),
  });
}

/**
 * One count per JSON-RPC message in a hosted `/mcp` POST body (a body may be
 * a batch). `status` is `ok` or `error` from the HTTP status: a JSON-RPC
 * error inside a 200 response is not visible here.
 */
export function recordMcpMessages(
  body: unknown,
  statusCode: number,
  orgId?: string,
  domain?: string,
): void {
  const messages = Array.isArray(body) ? body : [body];
  const status = statusCode >= 400 ? 'error' : 'ok';
  for (const message of messages) {
    if (message == null || typeof message !== 'object') continue;
    const { method, params } = message as { method?: unknown; params?: unknown };
    if (typeof method !== 'string') continue;
    const knownMethod = MCP_METHODS.has(method) ? method : 'other';
    const tool =
      knownMethod === 'tools/call' && params != null && typeof params === 'object'
        ? boundTool((params as { name?: unknown }).name)
        : 'none';
    mcpCalls.inc({
      method: knownMethod,
      tool,
      status,
      org: labelOrUnknown(orgId),
      domain: labelOrUnknown(domain),
    });
  }
}
