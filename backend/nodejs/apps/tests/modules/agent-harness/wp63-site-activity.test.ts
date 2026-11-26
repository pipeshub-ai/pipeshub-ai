/**
 * WP-63: every allowed-website request and every request the egress proxy
 * refused is a plain activity line on the run's timeline: what was read,
 * cloned or sent, where, and how the site is allowed; never an id, a query
 * or a raw code.
 */
import { expect } from 'chai';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';

const RUN = 'run-1';
let seq = 0;
const event = (type: string, data: Record<string, unknown>): JournalEvent =>
  ({
    orgId: 'o1',
    sessionId: 's1',
    streamId: 's1',
    seq: (seq += 1),
    runId: RUN,
    type,
    schemaVersion: 1,
    critical: false,
    ts: '2026-11-01T12:00:00.000Z',
    writer: 'node',
    visibility: 'audit',
    data,
  }) as unknown as JournalEvent;

describe('WP-63: allowed websites in the run’s activity', () => {
  it('C6.11: reads and writes are plain lines naming the site, its path and its access', () => {
    const timeline = foldTimeline(
      [
        event('egress.allowed', { host: 'docs.example.com', via: 'intercepted', site: { path: '/api/v2', access: 'read', use: 'read' } }),
        event('egress.allowed', { host: 'mirror.example.org', via: 'intercepted', site: { path: '/upload', access: 'read-write', use: 'write' } }),
        event('egress.allowed', { host: 'pinned.example.org', via: 'tunnel', site: { path: '/', access: 'host-level', use: 'write' } }),
        event('egress.allowed', { host: 'model-gateway', via: 'http' }),
      ],
      RUN,
    );
    expect(timeline.sites?.map((line) => line.label)).to.deep.equal([
      'Read docs.example.com/api/v2: allowed, read-only',
      'Sent data to mirror.example.org/upload: allowed, read and write',
      'Connected to pinned.example.org: host-level only, not inspected',
    ]);
  });

  it('C6.11 SEC-EG-07: blocked uploads, other paths, git, other hosts and private targets are plain lines too', () => {
    const timeline = foldTimeline(
      [
        event('gateway.refused', { gateway: 'egress', reason: 'site_read_only', status: 403, host: 'docs.example.com', site: { path: '/api/v2', access: 'read', use: 'write' } }),
        event('gateway.refused', { gateway: 'egress', reason: 'site_path_not_allowed', status: 403, host: 'docs.example.com', site: { path: '/blog', access: 'read', use: 'read' } }),
        event('gateway.refused', { gateway: 'egress', reason: 'host_mismatch', status: 421, host: 'docs.example.com' }),
        event('gateway.refused', { gateway: 'egress', reason: 'site_git_refused', status: 403, host: 'mirror.example.org', site: { path: '/r.git', access: 'read-write', use: 'read' } }),
        event('gateway.refused', { gateway: 'egress', reason: 'destination_not_allowed', status: 403, host: 'github.com' }),
        event('gateway.refused', { gateway: 'egress', reason: 'destination_private_address', status: 403, host: 'internal.example.org' }),
        event('gateway.refused', { gateway: 'model', reason: 'unsupported_parameter', status: 400, param: 'tools.type' }),
      ],
      RUN,
    );
    expect(timeline.sites?.map((line) => [line.outcome, line.label])).to.deep.equal([
      ['blocked', 'Blocked upload to docs.example.com/api/v2 (read-only)'],
      ['blocked', 'Blocked docs.example.com/blog: outside the allowed paths'],
      ['blocked', 'Blocked a request to docs.example.com that named another site'],
      ['blocked', 'Blocked git at mirror.example.org/r.git: clone repositories with ph git clone'],
      ['blocked', 'Blocked github.com: not an allowed website'],
      ['blocked', 'Blocked internal.example.org: it points to a private address'],
    ]);
  });

  it('C6.11 SEC-EG-07: the tunnel, volume and credential refusals read in words, never their code', () => {
    const reasons = ['destination_unresolvable', 'tunnel_ip_literal', 'tunnel_refused', 'volume_cap_exceeded', 'forbidden_pattern', 'token_replayed'];
    const timeline = foldTimeline(
      reasons.map((reason) => event('gateway.refused', { gateway: 'egress', reason, status: 403, host: 'files.example.net' })),
      RUN,
    );
    expect(timeline.sites?.map((line) => line.label)).to.deep.equal([
      'Could not reach files.example.net: the name does not resolve',
      'Blocked files.example.net: sites are reached by name, not address',
      'Blocked files.example.net: this connection is not allowed',
      'Blocked files.example.net: the run moved more data than allowed',
      'Blocked a request to files.example.net that carried a credential',
      'Blocked a request to files.example.net that carried a credential',
    ]);
    for (const line of timeline.sites ?? []) for (const reason of reasons) expect(line.label).to.not.include(reason);
  });

  it('a run that reached nothing beyond the gateways has no site lines at all', () => {
    expect(foldTimeline([event('egress.allowed', { host: 'tool-gateway', via: 'http' })], RUN)).to.not.have.property('sites');
  });
});
