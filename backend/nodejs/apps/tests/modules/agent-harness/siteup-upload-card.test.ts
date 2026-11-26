/**
 * SITEUP-08..SITEUP-10 (WP-63, the upload card) and GITF-36..GITF-37 on
 * Node's side: the held write, the person's answer and a clone refused for
 * its host are plain lines and items of the run's activity; the card's rule
 * is a journal shape Node takes (`site_upload_asks`); the websites and git
 * hosts screens read what the deployment fixes (`context.deployment`).
 */
import { expect } from 'chai';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { AUDIT_EVENT_FIELDS } from '../../../src/modules/agent-harness/domain/audit';
import {
  NODE_CAPABILITIES,
  SITE_UPLOAD_ASKS_FEATURE,
} from '../../../src/modules/agent-harness/domain/node-capabilities';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { interactionExplainedEventSchema } from '../../../src/modules/agent-harness/contracts/generated/core/events/interaction-explained';

const RUN = 'run-1';
let seq = 0;
const event = (type: string, data: Record<string, unknown>, writer = 'worker'): JournalEvent =>
  ({
    orgId: 'o1',
    sessionId: 's1',
    streamId: 's1',
    seq: (seq += 1),
    runId: RUN,
    type,
    schemaVersion: 1,
    critical: false,
    ts: '2026-11-02T12:00:00.000Z',
    writer,
    visibility: 'owner',
    data,
  }) as unknown as JournalEvent;

const card = (interactionId: string, host: string, decision: 'approved' | 'denied') => [
  event('x.pipeshub.engine.progress', {
    tool: 'opencode',
    taskId: 't1',
    kind: 'approval',
    text: 'site_upload',
    data: { interactionId, toolName: 'site_upload', argsJson: JSON.stringify({ host }) },
  }),
  event('interaction.explained', {
    interactionId,
    rule: 'site_upload',
    layer: 'flow',
    toolId: 'pipeshub@egress/site_upload',
    sensitivity: 'private',
    sources: [{ title: 'Q3 forecast' }],
    moreSources: 0,
    conversationGrant: false,
    destination: { label: host, key: `sha256:${'a'.repeat(64)}` },
  }),
  event('interaction.requested', { interactionId, kind: 'approval' }),
  event('interaction.resolved', { interactionId, decision, by: 'u1' }, 'node'),
];

describe('SITEUP: the upload card in the run’s activity', () => {
  it('SITEUP-08: a held write, then the person’s answer, are plain lines naming the site', () => {
    const timeline = foldTimeline(
      [
        event('gateway.refused', {
          gateway: 'egress',
          reason: 'site_upload_held',
          status: 403,
          host: 'upload.example.org',
          site: { path: '/up/file', access: 'read-write', use: 'write' },
        }, 'node'),
        ...card('ix.approve.sa', 'upload.example.org', 'approved'),
        ...card('ix.approve.sb', 'paste.example.net', 'denied'),
      ],
      RUN,
    );
    expect(timeline.sites?.map((line) => [line.outcome, line.label])).to.deep.equal([
      ['blocked', 'Upload to upload.example.org/up/file is held until you allow it: this conversation read private data'],
      ['allowed', 'Requests to upload.example.org allowed'],
      ['blocked', 'Requests to paste.example.net denied by you'],
    ]);
    const [approval] = timeline.approvals;
    expect(approval).to.include({ toolName: 'site_upload', status: 'approved' });
    expect(approval!.reason).to.include({ rule: 'site_upload', destination: 'upload.example.org', conversationGrant: false });
  });

  it('SITEUP-09: the card’s rule is a shape the journal takes, and Node names it on its claims', () => {
    const [, explained] = card('ix.approve.sc', 'upload.example.org', 'approved');
    const parsed = interactionExplainedEventSchema.safeParse({ ...explained, attemptId: 'a1' });
    expect(parsed.success, JSON.stringify(parsed.success ? null : parsed.error.issues)).to.equal(true);
    expect(NODE_CAPABILITIES.features).to.include(SITE_UPLOAD_ASKS_FEATURE);
  });
});

describe('SITEUP-11: every activity line carries what a frontend words it from, in any language', () => {
  it('kind, host, path, access, bytes and reason, with the English label kept as the fallback', () => {
    const timeline = foldTimeline(
      [
        event('egress.allowed', { host: 'docs.example.com', via: 'intercepted', site: { path: '/api', access: 'read', use: 'read' } }, 'node'),
        event('egress.allowed', { host: 'mirror.example.org', via: 'intercepted', site: { path: '/up', access: 'read-write', use: 'write' } }, 'node'),
        event('egress.allowed', { host: 'pinned.example.org', via: 'tunnel', site: { path: '/', access: 'host-level', use: 'write' } }, 'node'),
        event('gateway.refused', { gateway: 'egress', reason: 'site_read_only', status: 403, host: 'docs.example.com', site: { path: '/api', access: 'read', use: 'write' } }, 'node'),
        event('gateway.refused', { gateway: 'egress', reason: 'site_upload_held', status: 403, host: 'mirror.example.org', site: { path: '/up', access: 'read-write', use: 'write' } }, 'node'),
        ...card('ix.approve.sd', 'mirror.example.org', 'approved'),
        ...card('ix.approve.se', 'paste.example.net', 'denied'),
        event('x.pipeshub.repository.fetched', { host: 'github.com', repository: 'onyx-dot-app/onyx', bytes: 44040192, cached: false, full: false }),
      ],
      RUN,
    );
    const lines = timeline.sites ?? [];
    expect(lines.map((line) => line.kind)).to.deep.equal([
      'read', 'write', 'host_level', 'blocked', 'held', 'upload_allowed', 'upload_denied', 'cloned',
    ]);
    expect(lines[3]).to.include({ host: 'docs.example.com', path: '/api', access: 'read', reason: 'site_read_only' });
    expect(lines[4]).to.include({ host: 'mirror.example.org', path: '/up', reason: 'site_upload_held' });
    expect(lines[7]).to.include({ host: 'github.com', path: '/onyx-dot-app/onyx', access: 'read', bytes: 44040192 });
    expect(lines.every((line) => typeof line.label === 'string' && line.label.length > 0)).to.equal(true);
  });

  it('a clone or push the person denied names its repository', () => {
    const denied = (interactionId: string, toolName: string, label: string) => [
      event('x.pipeshub.engine.progress', { tool: 'opencode', taskId: 't1', kind: 'approval', text: toolName, data: { interactionId, toolName, argsJson: '{}' } }),
      event('interaction.explained', {
        interactionId, rule: 'tool', layer: 'engine', toolId: 'pipeshub@git/x', sensitivity: 'public', sources: [],
        moreSources: 0, conversationGrant: true, destination: { label, key: `sha256:${'b'.repeat(64)}` },
      }),
      event('interaction.requested', { interactionId, kind: 'approval' }),
      event('interaction.resolved', { interactionId, decision: 'denied', by: 'u1' }, 'node'),
    ];
    const lines =
      foldTimeline(
        [
          ...denied('ix.approve.c1', 'repository_fetch', 'github.com/onyx-dot-app/onyx (public, read-only, up to 500 MB)'),
          ...denied('ix.approve.c2', 'repository_push', 'pipeshub/session-s1 on github.com/acme/widgets'),
        ],
        RUN,
      ).sites ?? [];
    expect(lines.map(({ kind, host, path, label }) => ({ kind, host, path, label }))).to.deep.equal([
      { kind: 'clone_denied', host: 'github.com', path: '/onyx-dot-app/onyx', label: 'Clone denied by you' },
      { kind: 'push_denied', host: 'github.com', path: '/acme/widgets', label: 'Push denied by you' },
    ]);
  });
});

describe('GITF: a clone refused for its host', () => {
  it('GITF-36: is a "Clone not available" item with its host and path, and an audit record of them', () => {
    const data = { host: 'github.com', path: '/onyx-dot-app/onyx', reason: 'host_not_allowed' };
    const timeline = foldTimeline(
      [event('x.pipeshub.repository.unavailable', data), event('x.pipeshub.repository.unavailable', { host: 'x' })],
      RUN,
    );
    expect(timeline.repositoriesUnavailable).to.deep.equal([data]);
    expect(AUDIT_EVENT_FIELDS['x.pipeshub.repository.unavailable']!(data)).to.deep.equal(data);
    expect(foldTimeline([], RUN)).to.not.have.property('repositoriesUnavailable');
  });
});
