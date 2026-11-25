/**
 * GITF-16, GITF-17 (WP-63 part A): a repository the worker fetched for an
 * engine run is a plain line of its activity ("Cloned
 * github.com/onyx-dot-app/onyx (42 MB, read-only)"), a clone or push the
 * person refused is one too ("Clone denied by you"), and the fetch is an
 * audit record naming the host and path only. Node names
 * `repository_fetches` on its claims, so the worker journals the event.
 */
import { expect } from 'chai';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { AUDIT_EVENT_FIELDS } from '../../../src/modules/agent-harness/domain/audit';
import {
  NODE_CAPABILITIES,
  REPOSITORY_FETCHES_FEATURE,
} from '../../../src/modules/agent-harness/domain/node-capabilities';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';

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
    ts: '2026-11-01T12:00:00.000Z',
    writer,
    visibility: 'owner',
    data,
  }) as unknown as JournalEvent;

const FETCHED = {
  host: 'github.com',
  repository: 'onyx-dot-app/onyx',
  bytes: 42 * 1024 * 1024,
  cached: false,
  full: false,
  commit: '4f83564c1f0e8596be3f71d78f847a96447a3bdd',
};

/** An engine's approval, as the worker journals it: the engine frame, the reason, the request, the decision. */
const asked = (interactionId: string, toolName: string, label: string, decision: 'approved' | 'denied') => [
  event('x.pipeshub.engine.progress', {
    tool: 'opencode',
    taskId: 't1',
    kind: 'approval',
    text: toolName,
    data: { interactionId, toolName, argsJson: '{"url":"https://github.com/onyx-dot-app/onyx"}' },
  }),
  event('interaction.explained', {
    interactionId,
    rule: 'tool',
    layer: 'engine',
    toolId: 'pipeshub@git/repository_fetch',
    sensitivity: 'public',
    sources: [],
    moreSources: 0,
    conversationGrant: true,
    destination: { label, key: 'sha256:aa' },
  }),
  event('interaction.requested', { interactionId, kind: 'approval' }),
  event('interaction.resolved', { interactionId, decision, by: 'u1' }, 'node'),
];

describe('GITF: repositories in the run’s activity and audit', () => {
  it('GITF-16: a fetched repository is one plain line with its size, read-only', () => {
    const timeline = foldTimeline([event('x.pipeshub.repository.fetched', FETCHED)], RUN);
    expect(timeline.sites?.map((line) => [line.outcome, line.label])).to.deep.equal([
      ['allowed', 'Cloned github.com/onyx-dot-app/onyx (42 MB, read-only)'],
    ]);
  });

  it('GITF-16: a clone or a push the person denied is a line; an allowed one adds none until it is fetched', () => {
    const timeline = foldTimeline(
      [
        ...asked('ix.approve.a', 'repository_fetch', 'github.com/onyx-dot-app/onyx (public, read-only, up to 500 MB)', 'denied'),
        ...asked('ix.approve.b', 'pipeshub__repository_push', 'pipeshub/session-s1 on github.com/acme/widgets', 'denied'),
        ...asked('ix.approve.c', 'repository_fetch', 'github.com/acme/other (public, read-only, up to 500 MB)', 'approved'),
        ...asked('ix.approve.d', 'slack__send_message', '#sales', 'denied'),
      ],
      RUN,
    );
    expect(timeline.sites?.map((line) => [line.host, line.outcome, line.label])).to.deep.equal([
      ['github.com', 'blocked', 'Clone denied by you'],
      ['github.com', 'blocked', 'Push denied by you'],
    ]);
  });

  it('GITF-17: the fetch is an audit record of its host, path and size, never its URL or commit; Node accepts it', () => {
    const pick = AUDIT_EVENT_FIELDS['x.pipeshub.repository.fetched'];
    expect(pick).to.not.equal(undefined);
    expect(pick!(FETCHED)).to.deep.equal({
      host: 'github.com',
      repository: 'onyx-dot-app/onyx',
      bytes: 42 * 1024 * 1024,
      cached: false,
      full: false,
    });
    expect(NODE_CAPABILITIES.features).to.include(REPOSITORY_FETCHES_FEATURE);
  });
});
