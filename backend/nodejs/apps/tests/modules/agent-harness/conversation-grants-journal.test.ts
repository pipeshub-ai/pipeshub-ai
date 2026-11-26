/**
 * UI-B1 G5 and FLOWGRANT: how a conversation's grants are read back from journals that are not the tidy case —
 * grants written before grants named themselves (read from the ask's offer), offers withdrawn, events out of order
 * across streams, a grant given again wider or narrower, revoked and given again, and events that are not grants.
 */
import { expect } from 'chai';
import {
  conversationGrantId,
  conversationGrantList,
  GRANT_REVOKED_EVENT,
  missingGrants,
  namedGrant,
  offeredGrant,
  runGrantState,
} from '../../../src/modules/agent-harness/domain/conversation-grants';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';

const TOOL = 'web@searxng/fetch_url';
const KEY = `sha256:${'a'.repeat(64)}`;
const OTHER = `sha256:${'b'.repeat(64)}`;

let clock = 0;
const event = (
  type: string,
  writer: 'node' | 'worker',
  data: Record<string, unknown>,
  where: {
    stream?: string;
    runId?: string;
    seq?: number;
    second?: number;
  } = {},
): JournalEvent => {
  clock += 1;
  return {
    orgId: 'org-1',
    sessionId: 'ses-1',
    streamId: where.stream ?? 'ses-1',
    runId: where.runId ?? 'run-1',
    seq: where.seq ?? clock,
    type,
    writer,
    schemaVersion: 1,
    critical: false,
    visibility: 'owner',
    ts: new Date(
      Date.UTC(2026, 10, 9, 9, 0, where.second ?? clock),
    ).toISOString(),
    data,
  } as JournalEvent;
};

const offer = (ix: string, over: Record<string, unknown> = {}) =>
  event('interaction.explained', 'worker', {
    interactionId: ix,
    toolId: TOOL,
    rule: 'flow_external_write',
    conversationGrant: true,
    sensitivity: 'private',
    destination: { key: KEY, label: 'onyx.app' },
    ...over,
  });
const granted = (ix: string, over: Record<string, unknown> = {}, where = {}) =>
  event(
    'interaction.granted',
    'node',
    { interactionId: ix, scope: 'conversation', ...over },
    where,
  );
const named = { toolId: TOOL, key: KEY, sensitivity: 'public' };

describe('conversation grants read back from the journal', () => {
  beforeEach(() => {
    clock = 0;
  });

  it('a grant written before grants named themselves is read from its ask, named by who decided it', () => {
    const events = [
      offer('ix-1'),
      event('interaction.requested', 'worker', { interactionId: 'ix-1' }),
      event('interaction.resolved', 'node', {
        interactionId: 'ix-1',
        by: 'p_u-1',
      }),
      granted('ix-1'),
    ];
    const listed = conversationGrantList(events);
    expect(listed).to.have.length(1);
    expect(listed[0]).to.deep.include({
      toolId: TOOL,
      key: KEY,
      sensitivity: 'private',
      by: 'p_u-1',
      destination: 'onyx.app',
      grantId: conversationGrantId({ toolId: TOOL, key: KEY }),
    });
    const state = runGrantState(events);
    expect(state.held).to.deep.equal([
      { toolId: TOOL, key: KEY, sensitivity: 'private', by: 'p_u-1' },
    ]);
    expect([...state.given.keys()]).to.deep.equal(['ix-1']);
    expect(state.openOffers.size).to.equal(0);
  });

  it('a grant naming neither itself nor an ask in reach is not a grant; nor is one by the worker, for a run, or without an ask id', () => {
    const events = [
      granted('ix-lost'),
      event('interaction.granted', 'worker', {
        interactionId: 'ix-2',
        scope: 'conversation',
        ...named,
      }),
      granted('ix-3', { ...named, scope: 'run' }),
      event('interaction.granted', 'node', { scope: 'conversation', ...named }),
      granted('ix-4', { ...named, sensitivity: 'secret' }),
    ];
    expect(conversationGrantList(events)).to.deep.equal([]);
    expect(runGrantState(events).held).to.deep.equal([]);
  });

  it('an offer explained again without a grant offer is withdrawn: the open ask has nothing a grant could answer', () => {
    const events = [
      offer('ix-1'),
      event('interaction.requested', 'worker', { interactionId: 'ix-1' }),
      offer('ix-1', { conversationGrant: false }),
      offer('ix-2', { destination: { key: OTHER } }),
      event('interaction.requested', 'worker', { interactionId: 'ix-2' }),
    ];
    const state = runGrantState(events);
    expect([...state.openOffers.keys()]).to.deep.equal(['ix-2']);
    expect(
      offeredGrant({
        conversationGrant: true,
        toolId: TOOL,
        destination: 'onyx.app',
      }),
    ).to.equal(null);
    expect(
      offeredGrant({
        conversationGrant: true,
        toolId: TOOL,
        destination: { key: KEY },
        sensitivity: 'odd',
      }),
    ).to.deep.equal({
      toolId: TOOL,
      key: KEY,
      sensitivity: 'private',
    });
    expect(
      namedGrant({ toolId: TOOL, key: KEY, sensitivity: 'unknown', by: '' }),
    ).to.deep.equal({
      toolId: TOOL,
      key: KEY,
      sensitivity: 'unknown',
    });
  });

  it("a copy from another run is held but not this run's own, and takes no decider of this run", () => {
    const events = [
      event('interaction.resolved', 'node', {
        interactionId: 'ix-1',
        by: 'p_u-1',
      }),
      offer('ix-1'),
      granted('ix-1', { fromRunId: 'run-0' }),
      granted('ix-9', { ...named, key: OTHER, fromRunId: 'run-0' }),
    ];
    const state = runGrantState(events);
    expect(state.held).to.deep.equal([{ ...named, key: OTHER }]);
    expect(state.given.size).to.equal(0);
  });

  it('given again wider, the list keeps the first grant at the wider sensitivity; narrower changes nothing', () => {
    const events = [
      granted('ix-1', named, { second: 1 }),
      granted('ix-2', { ...named, sensitivity: 'private' }, { second: 2 }),
      granted('ix-3', named, { second: 3 }),
    ];
    const [only] = conversationGrantList(events);
    expect(only).to.deep.include({
      sensitivity: 'private',
      grantedAt: '2026-11-09T09:00:01.000Z',
      destination: null,
    });
    expect(conversationGrantList(events)).to.have.length(1);
  });

  it('events are read in time order across streams, then by stream and sequence; revoked and given again it holds again', () => {
    const revoked = event(
      GRANT_REVOKED_EVENT,
      'node',
      { toolId: TOOL, key: KEY },
      { second: 5, stream: 'ses-1', seq: 2 },
    );
    const first = granted('ix-1', named, {
      second: 5,
      stream: 'ses-1',
      seq: 1,
    });
    const later = granted(
      'ix-2',
      { ...named, sensitivity: 'private' },
      { second: 7, stream: 'job-1', seq: 1, runId: 'job-1' },
    );
    const other = granted(
      'ix-3',
      { ...named, key: OTHER },
      { second: 5, stream: 'job-1', seq: 9, runId: 'job-1' },
    );
    expect(
      conversationGrantList([later, revoked, other, first]).map((g) => [
        g.key,
        g.sensitivity,
        g.grantedAt,
      ]),
    ).to.deep.equal([
      [OTHER, 'public', '2026-11-09T09:00:05.000Z'],
      [KEY, 'private', '2026-11-09T09:00:07.000Z'],
    ]);
    expect(conversationGrantList([first, revoked])).to.deep.equal([]);
    const state = runGrantState([
      first,
      granted('ix-5', { ...named, key: OTHER }),
      revoked,
    ]);
    expect(state.held.map((g) => g.key)).to.deep.equal([OTHER]);
    expect([...state.given.keys()]).to.deep.equal(['ix-5']);
  });

  it("a revocation by the worker, or of another destination, revokes nothing; a copy's destination is its origin ask's", () => {
    const events = [
      offer('ix-1', {}),
      granted(
        'ix-1',
        { fromRunId: 'run-1' },
        { runId: 'job-1', stream: 'job-1' },
      ),
      event(GRANT_REVOKED_EVENT, 'worker', { toolId: TOOL, key: KEY }),
      event(GRANT_REVOKED_EVENT, 'node', { toolId: TOOL, key: OTHER }),
    ];
    expect(
      conversationGrantList(events).map((g) => [g.key, g.destination]),
    ).to.deep.equal([[KEY, 'onyx.app']]);
    const unlabelled = [
      offer('ix-1', { destination: { key: KEY, label: 7 } }),
      granted('ix-1'),
    ];
    expect(conversationGrantList(unlabelled)[0]?.destination).to.equal(null);
  });

  it('a run is missing only the grants it does not hold at that sensitivity or wider', () => {
    const pub = { toolId: TOOL, key: KEY, sensitivity: 'public' as const };
    const priv = { ...pub, sensitivity: 'private' as const };
    expect(missingGrants([priv], [pub, priv])).to.deep.equal([]);
    expect(missingGrants([pub], [pub, priv])).to.deep.equal([priv]);
    expect(missingGrants([], [{ ...pub, key: OTHER }])).to.deep.equal([
      { ...pub, key: OTHER },
    ]);
  });
});
