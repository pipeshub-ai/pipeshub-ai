import { expect } from 'chai';
import sinon from 'sinon';
import { readFileSync } from 'fs';
import { join } from 'path';
import { emitPlatformEvent } from '../../../src/libs/platform-events';
import type { McpCaller } from '../../../src/libs/platform-events/events';
import { loadPlugins, resetPlugins } from '../../../src/libs/plugins';
import {
  classifyCaller,
  emailDomain,
  requestClient,
  signupSource,
} from '../../../src/libs/utils/request-client.utils';
import { inboxFailed } from '../../../src/libs/context/envelope';
import telemetryPlugin from '../../../src/plugins/telemetry';
import { eventBuffer } from '../../../src/plugins/telemetry/event-buffer';
import { metricsBackend } from '../../../src/plugins/telemetry/metrics-backend';
import { resetClientMetrics } from '../../../src/plugins/telemetry/modules/client-metrics';

/**
 * `fixtures/direct-calls.golden.json` was captured from commit 81c0496b38, the
 * last one where application code called telemetry directly: the script made
 * the calls each call site made (recordEvent, recordServiceActivity,
 * setMetricCollectionEnabled and the DB-layer instruments) and saved the event
 * buffer and the Prometheus text. This test announces the same facts as
 * platform events and requires the plug-in to produce exactly that output.
 */
const golden = JSON.parse(
  readFileSync(join(__dirname, 'fixtures', 'direct-calls.golden.json'), 'utf8'),
) as { events: unknown[]; metrics: string[] };

const headers = {
  'user-agent': 'claude-code/2.1.0 (darwin; arm64)',
  'x-pipeshub-channel': 'docs-quickstart',
};

function announceEverything(): void {
  // sign-in, account set-up, organisation creation
  emitPlatformEvent('user.signed_in', { orgId: 'org-1', userId: 'user-1', email: 'Ada@Example.COM', firstSignIn: true, authMethod: 'password' });
  emitPlatformEvent('user.signed_in', { orgId: undefined, userId: undefined, email: undefined, firstSignIn: false, authMethod: 'google' });
  emitPlatformEvent('user.account_setup.completed', { orgId: 'org-1', userId: 'user-2', email: 'bob@example.com' });
  const linked = signupSource({ ref: 'launch-week' });
  emitPlatformEvent('org.created', {
    orgId: 'org-2', adminUserId: 'admin-2', contactEmail: 'owner@acme.io', adminFullName: 'Owner Person',
    signupChannel: linked.channel,
    ...(linked.referrerDomain !== undefined ? { signupReferrerDomain: linked.referrerDomain } : {}),
  });
  const direct = signupSource({ referrerDomain: 'News.Example.org' });
  emitPlatformEvent('org.created', {
    orgId: 'org-3', adminUserId: 'admin-3', contactEmail: 'x@y.dev', adminFullName: 'X Y',
    signupChannel: direct.channel,
    ...(direct.referrerDomain !== undefined ? { signupReferrerDomain: direct.referrerDomain } : {}),
  });

  // MCP
  const oauthCaller: McpCaller = {
    orgId: 'org-1', userId: 'user-1', emailDomain: emailDomain('ada@example.com'),
    authType: 'oauth', oauthClientId: 'client-abc', ...requestClient(headers),
  };
  emitPlatformEvent('mcp.client.connected', { ...oauthCaller, clientName: 'Claude Code', clientVersion: '2.1.0' });
  emitPlatformEvent('mcp.tool.called', { ...oauthCaller, tool: 'search' });
  emitPlatformEvent('mcp.tool.called', {
    orgId: undefined, userId: undefined, emailDomain: emailDomain(undefined), authType: 'session', ...requestClient({}), tool: undefined,
  });
  emitPlatformEvent('mcp.client.connected', {
    orgId: 'org-1', userId: 'user-1', emailDomain: 'example.com', authType: 'pat', ...requestClient({}),
    clientName: undefined, clientVersion: undefined,
  });

  // OAuth tokens and personal access tokens
  emitPlatformEvent('oauth.token.issued', { grantType: 'authorization_code', clientId: 'client-abc', ...requestClient(headers) });
  emitPlatformEvent('oauth.token.issued', { grantType: 'client_credentials', clientId: 'client-xyz', ...requestClient({}) });
  emitPlatformEvent('personal_access_token.created', {
    orgId: 'org-1', userId: 'user-1', emailDomain: emailDomain('ada@example.com'), scopeCount: 3, expiryDays: 90, ...requestClient(headers),
  });
  emitPlatformEvent('personal_access_token.knowledge_limit.changed', {
    orgId: 'org-1', userId: 'user-1', emailDomain: emailDomain(undefined), limited: true, itemCount: 4, ...requestClient(headers),
  });
  emitPlatformEvent('personal_access_token.knowledge_limit.changed', {
    orgId: 'org-1', userId: 'user-1', emailDomain: emailDomain('ada@example.com'), limited: false, itemCount: 0, ...requestClient({}),
  });

  // Data Collection setting
  emitPlatformEvent('org.data_collection_setting.changed', { orgId: 'org-1', enabled: false });
  emitPlatformEvent('org.data_collection_setting.changed', { orgId: 'org-2', enabled: true });
  emitPlatformEvent('org.data_collection_setting.changed', { orgId: undefined, enabled: true });
  emitPlatformEvent('org.data_collection_setting.changed', { orgId: { toString: () => 'org-obj' }, enabled: false });

  // relational database
  emitPlatformEvent('db.query.completed', { service: 'node_api', site: 'users.find_by_id', access: 'read', durationSeconds: 0.004 });
  emitPlatformEvent('db.query.completed', { service: 'node_api', site: 'untagged', access: 'write', durationSeconds: 0.25 });
  emitPlatformEvent('db.unit_of_work.completed', { service: 'node_api', outcome: 'commit', durationSeconds: 0.02 });
  emitPlatformEvent('db.unit_of_work.completed', { service: 'jobs', outcome: 'serialization_failure', durationSeconds: 2 });
  emitPlatformEvent('db.connection.acquired', { waitSeconds: 0.0007 });
  emitPlatformEvent('db.unit_of_work.retried', { cause: '40001' });
  emitPlatformEvent('db.unit_of_work.retried', { cause: 'connection' });
  emitPlatformEvent('db.connection.session_role_leaked');
  emitPlatformEvent('db.unit_of_work.refused', { reason: 'org_context_missing' });
  emitPlatformEvent('db.unit_of_work.refused', { reason: 'org_context_missing' });

  // document store
  for (const reason of ['unreadable', 'deadline', 'readonly', 'retired'] as const) {
    emitPlatformEvent('docstore.write.refused', { store: 'mongo', reason });
  }
  emitPlatformEvent('docstore.write.refused', { store: 'postgres', reason: 'readonly' });
  emitPlatformEvent('docstore.write.refused', { store: 'postgres', reason: 'epoch' });
  emitPlatformEvent('docstore.fence.checked', { durationSeconds: 0.003 });
  emitPlatformEvent('docstore.transaction.failed');
  emitPlatformEvent('docstore.row_policy.violated');
  emitPlatformEvent('docstore.shadow_read.compared', { repo: 'users', result: 'equal' });
  emitPlatformEvent('docstore.shadow_read.compared', { repo: 'users', result: 'diff' });
  emitPlatformEvent('docstore.nul_characters.stripped', { repo: 'notifications', field: 'payload', count: 5 });
  emitPlatformEvent('docstore.ttl.swept', { target: 'outbox', removed: 12 });
  emitPlatformEvent('docstore.ttl.sweep_failed', { target: 'notifications' });

  // messaging: the real call site for the inbox, as the capture used
  inboxFailed('seen', 'notification', new Error('x'));
  emitPlatformEvent('notification.event.refused', { reason: 'id_with_many_recipients' });
}

/** Metrics added after the golden file was captured; everything else must match it line for line. */
const ADDED_SINCE = /^(# (HELP|TYPE) )?pipeshub_usage_sdk_requests_total[ {]/;

/** Finished requests from a browser, a script, an SDK and the MCP server. */
function announceClientRequests(): void {
  const done = (requestHeaders: Record<string, string>, status = 200): void =>
    emitPlatformEvent('http.request.completed', {
      method: 'GET', route: '/api/v1/kb/:kbId', status, durationSeconds: 0.02,
      outcome: status < 400 ? 'success' : 'failure',
      orgId: 'org-1', emailDomain: 'example.com', client: classifyCaller(requestHeaders),
    });
  done({ 'user-agent': 'Mozilla/5.0 (X11; Linux x86_64)' });
  done({ 'user-agent': 'curl/8.4.0' }, 404);
  done({ 'user-agent': 'speakeasy-sdk/python 2.0.1 2.946.0 1.0.0 pipeshub-sdk' });
  done({ 'user-agent': 'speakeasy-sdk/python 2.0.1 2.946.0 1.0.0 pipeshub-sdk' });
  done({ 'x-pipeshub-client': 'pipeshub-mcp/1.4.2', 'x-request-id': 'mcp-1' }, 500);
}

describe('telemetry plug-in: platform events produce what the direct calls produced', () => {
  let clock: sinon.SinonFakeTimers;

  beforeEach(async () => {
    resetPlugins();
    await loadPlugins([telemetryPlugin]);
    metricsBackend.reset();
    eventBuffer.drain();
    resetClientMetrics();
    clock = sinon.useFakeTimers({
      now: Date.parse('2026-01-02T03:04:05.000Z'),
      toFake: ['Date'],
    });
  });

  afterEach(() => {
    clock.restore();
    resetPlugins();
    metricsBackend.reset();
    eventBuffer.drain();
  });

  it('buffers the same events, with the same names, props and order', () => {
    announceEverything();
    const events = JSON.parse(JSON.stringify(eventBuffer.drain())) as unknown[];
    expect(events).to.have.length(golden.events.length);
    expect(events).to.deep.equal(golden.events);
  });

  it('serialises the same Prometheus text: metric names, help, labels and values', async () => {
    announceEverything();
    const text = await metricsBackend.serialize();
    const lines = text.split('\n').filter((line) => line !== '').sort();
    expect(lines.filter((line) => !ADDED_SINCE.test(line))).to.deep.equal(golden.metrics);
    // What the golden file does not know is exactly the metrics added since, with no sample yet.
    expect(lines.filter((line) => ADDED_SINCE.test(line))).to.deep.equal([
      '# HELP pipeshub_usage_sdk_requests_total API requests made through a PipesHub SDK or the PipesHub MCP server',
      '# TYPE pipeshub_usage_sdk_requests_total counter',
    ]);
  });

  it('counting SDK and MCP requests changes no metric and no event that existed before', async () => {
    announceEverything();
    announceClientRequests();
    const text = await metricsBackend.serialize();
    const lines = text.split('\n').filter((line) => line !== '').sort();
    expect(lines.filter((line) => !ADDED_SINCE.test(line))).to.deep.equal(golden.metrics);
    expect(lines.filter((line) => ADDED_SINCE.test(line) && !line.startsWith('#'))).to.deep.equal([
      'pipeshub_usage_sdk_requests_total{service="node_api",channel="mcp",sdk_language="other",sdk_version="1.4.2",outcome="failure",org="org-1",domain="example.com"} 1',
      'pipeshub_usage_sdk_requests_total{service="node_api",channel="sdk",sdk_language="python",sdk_version="2.0.1",outcome="success",org="org-1",domain="example.com"} 2',
    ]);

    const events = JSON.parse(JSON.stringify(eventBuffer.drain())) as { event: string }[];
    expect(events.filter((e) => e.event !== 'sdk.client.seen')).to.deep.equal(golden.events);
    expect(events.filter((e) => e.event === 'sdk.client.seen')).to.deep.equal([
      {
        event: 'sdk.client.seen', timestamp: '2026-01-02T03:04:05.000Z',
        props: { orgId: 'org-1', domain: 'example.com', channel: 'sdk', sdk_language: 'python', sdk_version: '2.0.1' },
      },
      {
        event: 'sdk.client.seen', timestamp: '2026-01-02T03:04:05.000Z',
        props: { orgId: 'org-1', domain: 'example.com', channel: 'mcp', mcp_server_version: '1.4.2' },
      },
    ]);
  });

  it('records nothing once the plug-in is unloaded', async () => {
    resetPlugins();
    await loadPlugins([]);
    announceEverything();
    expect(eventBuffer.size()).to.equal(0);
    const text = await metricsBackend.serialize();
    const samples = text
      .split('\n')
      .filter((line) => line !== '' && !line.startsWith('#'))
      .filter((line) => !line.endsWith(' 0'));
    expect(samples).to.deep.equal([]);
  });
});
