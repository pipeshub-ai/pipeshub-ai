import { expect } from 'chai';
import sinon from 'sinon';
import type { PlatformEvents } from '../../../../src/libs/platform-events/events';
import { classifyCaller } from '../../../../src/libs/utils/request-client.utils';
import { eventBuffer } from '../../../../src/plugins/telemetry/event-buffer';
import { metricsBackend } from '../../../../src/plugins/telemetry/metrics-backend';
import {
  MAX_FIRST_SEEN_KEYS,
  MAX_VERSIONS_PER_LANGUAGE,
  recordClientRequest,
  resetClientMetrics,
} from '../../../../src/plugins/telemetry/modules/client-metrics';

type Completed = PlatformEvents['http.request.completed'];

const METRIC = 'pipeshub_usage_sdk_requests_total';

function completed(
  headers: Record<string, string>,
  overrides: Partial<Completed> = {},
): Completed {
  return {
    method: 'GET',
    route: '/api/v1/kb/:kbId',
    status: 200,
    durationSeconds: 0.01,
    outcome: 'success',
    orgId: 'org-1',
    emailDomain: 'acme.io',
    client: classifyCaller(headers),
    ...overrides,
  };
}

async function samples(): Promise<string[]> {
  const text = await metricsBackend.serialize();
  return text.split('\n').filter((line) => line.startsWith(`${METRIC}{`));
}

describe('telemetry modules/client-metrics', () => {
  let clock: sinon.SinonFakeTimers;

  beforeEach(() => {
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
    metricsBackend.reset();
    eventBuffer.drain();
    resetClientMetrics();
  });

  it('counts SDK requests by language, version, outcome, organisation and domain', async () => {
    const python = { 'user-agent': 'speakeasy-sdk/python 2.0.1 2.946.0 1.0.0 pipeshub-sdk' };
    recordClientRequest(completed(python));
    recordClientRequest(completed(python));
    recordClientRequest(completed(python, { status: 500, outcome: 'failure' }));
    recordClientRequest(completed({ 'x-pipeshub-client': 'pipeshub-sdk-go/2.1.0' }, { orgId: 'org-2', emailDomain: 'x.dev' }));

    expect(await samples()).to.have.members([
      `${METRIC}{service="node_api",channel="sdk",sdk_language="python",sdk_version="2.0.1",outcome="success",org="org-1",domain="acme.io"} 2`,
      `${METRIC}{service="node_api",channel="sdk",sdk_language="python",sdk_version="2.0.1",outcome="failure",org="org-1",domain="acme.io"} 1`,
      `${METRIC}{service="node_api",channel="sdk",sdk_language="go",sdk_version="2.1.0",outcome="success",org="org-2",domain="x.dev"} 1`,
    ]);
  });

  it('counts the MCP server with its own version, apart from the SDKs', async () => {
    recordClientRequest(
      completed({
        'x-pipeshub-client': 'pipeshub-mcp/1.4.2',
        'user-agent': 'speakeasy-sdk/mcp-typescript 0.0.4 2.846.1 1.0.0 mcp',
      }),
    );
    expect(await samples()).to.deep.equal([
      `${METRIC}{service="node_api",channel="mcp",sdk_language="other",sdk_version="1.4.2",outcome="success",org="org-1",domain="acme.io"} 1`,
    ]);
    expect(eventBuffer.drain().map((e) => ({ event: e.event, props: e.props }))).to.deep.equal([
      {
        event: 'sdk.client.seen',
        props: { orgId: 'org-1', domain: 'acme.io', channel: 'mcp', mcp_server_version: '1.4.2' },
      },
    ]);
  });

  it('ignores every other channel', async () => {
    recordClientRequest(completed({ 'user-agent': 'Mozilla/5.0 (X11)' }));
    recordClientRequest(completed({ 'user-agent': 'curl/8.4.0' }));
    recordClientRequest(completed({ 'x-pipeshub-channel': 'slack' }));
    expect(await samples()).to.deep.equal([]);
    expect(eventBuffer.size()).to.equal(0);
  });

  it('says unknown for a request nobody signed in to, and for an SDK that named no version', async () => {
    recordClientRequest(
      completed({ 'x-request-id': 'sdk-ts-abc' }, { orgId: undefined, emailDomain: 'unknown' }),
    );
    expect(await samples()).to.deep.equal([
      `${METRIC}{service="node_api",channel="sdk",sdk_language="typescript",sdk_version="unknown",outcome="success",org="unknown",domain="unknown"} 1`,
    ]);
  });

  it(`keeps ${MAX_VERSIONS_PER_LANGUAGE} versions per language and counts later ones as other`, async () => {
    const send = (language: string, version: string): void =>
      recordClientRequest(completed({ 'x-pipeshub-client': `pipeshub-sdk-${language}/${version}` }));
    for (let patch = 0; patch < MAX_VERSIONS_PER_LANGUAGE + 15; patch++) send('python', `1.0.${patch}`);
    send('python', '1.0.0'); // an early one is still counted under its own version
    send('go', '9.9.9'); // another language has its own allowance

    const lines = await samples();
    const versions = (language: string): string[] =>
      lines
        .filter((line) => line.includes(`sdk_language="${language}"`))
        .map((line) => /sdk_version="([^"]+)"/.exec(line)![1]!);
    expect(versions('python')).to.have.length(MAX_VERSIONS_PER_LANGUAGE + 1);
    expect(versions('python')).to.include('other').and.to.include('1.0.0');
    expect(lines.find((line) => line.includes('sdk_version="other"'))).to.match(/ 15$/);
    expect(lines.find((line) => line.includes('sdk_version="1.0.0"'))).to.match(/ 2$/);
    expect(versions('go')).to.deep.equal(['9.9.9']);
  });

  it('never grows past the cap whatever versions a caller invents', async () => {
    for (let i = 0; i < 2000; i++) {
      recordClientRequest(
        completed({ 'x-pipeshub-client': `pipeshub-sdk-${['python', 'go', 'typescript', 'rust'][i % 4]}/${i}.${i % 7}.0` }),
      );
      recordClientRequest(completed({ 'x-pipeshub-client': `pipeshub-mcp/0.${i}.0` }));
    }
    // typescript, python, go, unknown and the MCP server: versions + other each.
    expect((await samples()).length).to.be.at.most(5 * (MAX_VERSIONS_PER_LANGUAGE + 1));
  });

  describe('sdk.client.seen', () => {
    const ts = { 'x-pipeshub-client': 'pipeshub-sdk-typescript/2.0.1' };
    const seen = (): { event: string; props?: Record<string, unknown> }[] =>
      eventBuffer.drain().map((e) => ({ event: e.event, props: e.props }));

    it('is reported once per organisation and client in a day', () => {
      recordClientRequest(completed(ts));
      recordClientRequest(completed(ts));
      recordClientRequest(completed(ts, { orgId: 'org-2', emailDomain: 'x.dev' }));
      recordClientRequest(completed({ 'x-pipeshub-client': 'pipeshub-sdk-typescript/2.0.2' }));
      expect(seen()).to.deep.equal([
        { event: 'sdk.client.seen', props: { orgId: 'org-1', domain: 'acme.io', channel: 'sdk', sdk_language: 'typescript', sdk_version: '2.0.1' } },
        { event: 'sdk.client.seen', props: { orgId: 'org-2', domain: 'x.dev', channel: 'sdk', sdk_language: 'typescript', sdk_version: '2.0.1' } },
        { event: 'sdk.client.seen', props: { orgId: 'org-1', domain: 'acme.io', channel: 'sdk', sdk_language: 'typescript', sdk_version: '2.0.2' } },
      ]);
    });

    it('is reported again the next day (UTC)', () => {
      recordClientRequest(completed(ts));
      clock.tick(21 * 60 * 60 * 1000); // 2026-01-03T00:04:05Z
      recordClientRequest(completed(ts));
      recordClientRequest(completed(ts));
      expect(seen()).to.have.length(2);
    });

    it('carries no user id, address or name', () => {
      recordClientRequest(completed(ts));
      const [event] = seen();
      expect(Object.keys(event!.props!)).to.have.members(['orgId', 'domain', 'channel', 'sdk_language', 'sdk_version']);
    });

    it(`remembers at most ${MAX_FIRST_SEEN_KEYS} clients a day and reports none after that`, () => {
      for (let i = 0; i < MAX_FIRST_SEEN_KEYS + 50; i++) {
        recordClientRequest(completed(ts, { orgId: `org-${i}` }));
        eventBuffer.drain(); // the buffer has its own, separate cap
      }
      recordClientRequest(completed(ts, { orgId: 'org-late' }));
      recordClientRequest(completed(ts, { orgId: 'org-0' }));
      expect(seen()).to.deep.equal([]);
    });
  });
});
