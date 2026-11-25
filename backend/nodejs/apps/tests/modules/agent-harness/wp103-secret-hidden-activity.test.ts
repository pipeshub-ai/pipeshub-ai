/**
 * WP103-FU-1 (WP103-ACT-N1..N3): a credential shape a gateway hid from the run's model call shows in its owner's
 * activity as one plain line, projected from the existing `gateway.secret_hidden` audit events: the kinds and how
 * many, each kind once whichever gateway reported it, never the value. The timeline route is the owner's only, and
 * it now hands the browser the run's outside-access lines and refused clones too (WP-63's, which it had dropped).
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { RunTimelineService } from '../../../src/modules/agent-harness/application/run-timeline-service';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';

const RUN = 'run-1';
const run = { orgId: 'org-1', runId: RUN, sessionId: 'ses-1', status: 'completed' } as HarnessRun;

const journalOf = (entries: Array<[string, Record<string, unknown>]>): JournalEvent[] =>
  entries.map(
    ([type, data], index) =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: index + 1,
        runId: RUN,
        type,
        schemaVersion: 1,
        critical: false,
        ts: `2026-11-10T12:00:00.${String(index).padStart(3, '0')}Z`,
        writer: 'node',
        visibility: 'audit',
        data,
      }) as JournalEvent,
  );

const hidden = (gateway: string, kind: string): [string, Record<string, unknown>] => [
  'gateway.secret_hidden',
  { gateway, kind, attemptId: `${RUN}.a1` },
];

const serviceOver = (events: JournalEvent[], audience: 'owner' | 'shared') =>
  new RunTimelineService({
    runs: { streamAccess: sinon.stub().resolves({ run, access: { audience } }) },
    journal: {
      readRun: sinon
        .stub()
        .callsFake(async (_org: string, _run: string, after: number, limit: number) =>
          events.filter((e) => e.seq > after).slice(0, limit),
        ),
    },
    journals: { resolve: sinon.stub().callsFake(async (all: JournalEvent[]) => all) },
    workspaces: null,
    ledger: null,
  });

const SCOPE = { orgId: 'org-1', sessionId: 'ses-1', actorId: 'user-1' } as never;

describe('WP103-FU-1: a hidden secret in the owner\'s activity', () => {
  it('WP103-ACT-N1: one line names each kind once, however many gateways hid it, and counts them', () => {
    const timeline = foldTimeline(
      journalOf([
        hidden('egress', 'openai_key'),
        hidden('model', 'openai_key'),
        hidden('model', 'github_token'),
        hidden('egress', 'Not A Kind!'),
      ]),
      RUN,
    );
    expect(timeline.secretsHidden).to.deep.equal({ kinds: ['openai_key', 'github_token'], count: 2 });
    expect(foldTimeline(journalOf([['turn.started', { turn: 1 }]]), RUN)).to.not.have.property('secretsHidden');
  });

  it('WP103-ACT-N2: the line carries the kind and the count only, never a value, a place or an id', () => {
    const value = 'sk-live-ABCDEFGHIJKLMNOPQRSTUVWX';
    const timeline = foldTimeline(
      journalOf([['gateway.secret_hidden', { gateway: 'model', kind: 'openai_key', attemptId: `${RUN}.a1`, value, path: '/v1/chat' }]]),
      RUN,
    );
    expect(Object.keys(timeline.secretsHidden ?? {}).sort()).to.deep.equal(['count', 'kinds']);
    const shown = JSON.stringify(timeline.secretsHidden);
    for (const leak of [value, '/v1/chat', `${RUN}.a1`, 'model']) expect(shown).to.not.include(leak);
  });

  it('WP103-ACT-N3: only the run\'s owner reads it, with the outside-access lines and refused clones', async () => {
    const events = journalOf([
      hidden('model', 'private_key'),
      ['egress.allowed', { host: 'docs.example.com', via: 'intercepted', site: { path: '/', access: 'read', use: 'read' } }],
      ['x.pipeshub.repository.unavailable', { host: 'git.example.com', path: '/acme/tools', reason: 'host_not_allowed' }],
    ]);

    const view = await serviceOver(events, 'owner').timeline(SCOPE, RUN);
    expect(view.secretsHidden).to.deep.equal({ kinds: ['private_key'], count: 1 });
    expect(view.sites?.map((line) => line.host)).to.deep.equal(['docs.example.com']);
    expect(view.repositoriesUnavailable).to.deep.equal([
      { host: 'git.example.com', path: '/acme/tools', reason: 'host_not_allowed' },
    ]);

    let refused: unknown;
    try {
      await serviceOver(events, 'shared').timeline(SCOPE, RUN);
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessNotFoundError);
  });
});
