import { expect } from 'chai';
import { InMemoryRecordReadAccess } from '../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import {
  CitationViewer,
  SharedCitationService,
} from '../../../src/modules/agent-harness/application/shared-citations';
import {
  joinLabels,
  labelPasses,
  MAX_LABEL_SOURCES,
  MAX_VISIBLE_RUNS,
  ownWorkLabel,
  parseTurnLabel,
  spawnedChildren,
  TURN_LABELS_FEATURE,
  TurnLabel,
} from '../../../src/modules/agent-harness/domain/turn-labels';
import { runAudience, seenRuns } from '../../../src/modules/agent-harness/domain/session-audience';
import { admissionRequirements } from '../../../src/modules/agent-harness/domain/worker-compat';
import { drainCommand } from '../../../src/modules/agent-harness/domain/next-runs';
import type { AdmitRunCommand } from '../../../src/modules/agent-harness/domain/admission';
import { foreignAnswers, forkTranscript, turnAnswers } from '../../../src/modules/agent-harness/domain/session-forks';
import { sourceKey } from '../../../src/modules/agent-harness/projection/citation-source';
import type { SourceStatus } from '../../../src/modules/agent-harness/ports/record-access';

type Line = [level: string, message: string, meta: Record<string, unknown>];

function recording(): { logger: HarnessLogger; lines: Line[] } {
  const lines: Line[] = [];
  const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    lines.push([level, message, meta]);
  };
  return { lines, logger: { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') } };
}

const indexed = (recordId: string) => ({ kind: 'indexed' as const, recordId });
const labelOf = (...ids: string[]): TurnLabel => ({ complete: true, sources: ids.map(indexed) });
const INCOMPLETE: TurnLabel = { complete: false, sources: [] };
const statusesOf = (entries: Record<string, SourceStatus>): Map<string, SourceStatus> =>
  new Map(Object.entries(entries).map(([id, status]) => [sourceKey(indexed(id)), status]));

describe('agent-harness: turn labels (P2.5a)', () => {
  describe('LB-4: the rules', () => {
    it('joins by union, ordered by key, so equal labels are equal documents', () => {
      expect(joinLabels([labelOf('r2'), labelOf('r1', 'r2')])).to.deep.equal(labelOf('r1', 'r2'));
      expect(joinLabels([])).to.deep.equal({ complete: true, sources: [] });
    });

    it('a label nobody recorded, or an incomplete one, makes the join unknown', () => {
      expect(joinLabels([labelOf('r1'), null])).to.deep.equal(INCOMPLETE);
      expect(joinLabels([labelOf('r1'), undefined])).to.deep.equal(INCOMPLETE);
      expect(joinLabels([INCOMPLETE, labelOf('r1')])).to.deep.equal(INCOMPLETE);
    });

    it('more sources than the bound is unknown', () => {
      const many = Array.from({ length: MAX_LABEL_SOURCES }, (_, i) => `r${String(i)}`);
      expect(joinLabels([labelOf(...many)]).complete).to.equal(true);
      expect(joinLabels([labelOf(...many), labelOf('one-more')])).to.deep.equal(INCOMPLETE);
    });

    it('passes only a complete label whose every source is readable', () => {
      expect(labelPasses(labelOf('r1'), statusesOf({ r1: 'readable' }))).to.equal(true);
      expect(labelPasses(labelOf(), new Map())).to.equal(true);
      expect(labelPasses(labelOf('r1', 'r2'), statusesOf({ r1: 'readable', r2: 'denied' }))).to.equal(false);
      expect(labelPasses(labelOf('r1'), statusesOf({ r1: 'unresolved' }))).to.equal(false);
      expect(labelPasses(labelOf('r1'), new Map())).to.equal(false);
      expect(labelPasses(INCOMPLETE, new Map())).to.equal(false);
      expect(labelPasses(null, new Map())).to.equal(false);
    });

    it('reads a recorded label, and refuses a malformed one rather than reading it as empty', () => {
      expect(parseTurnLabel(labelOf('r1'))).to.deep.equal(labelOf('r1'));
      expect(parseTurnLabel(undefined)).to.equal(null);
      expect(parseTurnLabel({ sources: [] })).to.equal(null);
      expect(parseTurnLabel({ complete: true, sources: [{ kind: 'web' }] })).to.equal(null);
    });
  });

  describe('CK: the check, with the viewer\'s own credential', () => {
    const bob: CitationViewer = {
      authorization: 'Bearer bob',
      orgId: 'org-1',
      sessionId: 'session-1',
      requestId: 'req-1',
      surface: 'conversation',
    };
    let records: InMemoryRecordReadAccess;

    beforeEach(() => {
      records = new InMemoryRecordReadAccess().grant('Bearer bob', 'r-open', 'r-also').grant('Bearer alice', 'r-closed');
    });

    it('CK-1: one batch over every label; a turn passes when complete and every source is readable', async () => {
      const service = new SharedCitationService(records);
      const passed = await service.passingTurns(bob, [
        { id: 'run-open', label: labelOf('r-open', 'r-also') },
        { id: 'run-closed', label: labelOf('r-open', 'r-closed') },
        { id: 'run-unlabelled', label: null },
        { id: 'run-incomplete', label: INCOMPLETE },
        { id: 'run-empty', label: labelOf() },
      ]);
      expect([...passed].sort()).to.deep.equal(['run-empty', 'run-open']);
      expect(records.queries).to.have.length(1);
      expect(records.queries[0]!.authorization).to.equal('Bearer bob');
      expect(records.queries[0]!.sources.map(sourceKey).sort()).to.deep.equal(
        ['r-also', 'r-closed', 'r-open'].map((id) => sourceKey(indexed(id))),
      );
    });

    it('CK-1: no label to check asks nothing', async () => {
      const service = new SharedCitationService(records);
      expect([...(await service.passingTurns(bob, []))]).to.deep.equal([]);
      expect([...(await service.passingTurns(bob, [{ id: 'run-x', label: null }]))]).to.deep.equal([]);
      expect(records.queries).to.have.length(0);
    });

    it('CK-2: fails closed when the check cannot be made, and says why once', async () => {
      const turns = [{ id: 'run-open', label: labelOf('r-open') }];
      const failing = new InMemoryRecordReadAccess().grant('Bearer bob', 'r-open');
      failing.failWith = 'unavailable';
      for (const [reason, authority, viewer] of [
        ['not_configured', null, bob],
        ['no_credential', records, { ...bob, authorization: undefined }],
        ['unavailable', failing, bob],
      ] as const) {
        const { logger, lines } = recording();
        const passed = await new SharedCitationService(authority, logger).passingTurns(viewer, turns);
        expect([...passed]).to.deep.equal([]);
        const warned = lines.filter(([level]) => level === 'warn');
        expect(warned).to.have.length(1);
        expect(warned[0]![1]).to.equal('turn_labels.access_check_failed');
        expect(warned[0]![2]).to.include({ reason, choice: 'stub_turns' });
      }
    });

    it('CK-3: nothing is cached: a revocation between two checks is seen by the second', async () => {
      const service = new SharedCitationService(records);
      const turns = [{ id: 'run-open', label: labelOf('r-open') }];
      expect([...(await service.passingTurns(bob, turns))]).to.deep.equal(['run-open']);
      records.revoke('Bearer bob', 'r-open');
      expect([...(await service.passingTurns(bob, turns))]).to.deep.equal([]);
      expect(records.queries).to.have.length(2);
    });

    it('CK-L: the log line carries counts, never a record id', async () => {
      const { logger, lines } = recording();
      await new SharedCitationService(records, logger).passingTurns(bob, [
        { id: 'run-open', label: labelOf('r-open') },
        { id: 'run-closed', label: labelOf('r-closed') },
        { id: 'run-unlabelled', label: null },
      ]);
      const [, message, meta] = lines.find(([, m]) => m === 'turn_labels.access_checked')!;
      expect(message).to.equal('turn_labels.access_checked');
      expect(meta).to.include({ labels: 3, incomplete: 1, sources: 2, passed: 1, stubbed: 2, orgId: 'org-1' });
      expect(JSON.stringify(lines)).to.not.match(/r-open|r-closed|run-open/);
    });
  });

  describe('where the label comes from and goes (pure)', () => {
    const event = (type: string, runId: string, data: Record<string, unknown>) => ({ type, runId, data });

    it('ST: the own-work label is the one the worker pinned on the run\'s last answer; none is null', () => {
      const events = [
        event('x.pipeshub.answer.finalized', 'run-1', { label: labelOf('r0') }),
        event('x.pipeshub.answer.finalized', 'run-2', { label: labelOf('r9') }),
        event('x.pipeshub.answer.finalized', 'run-1', { label: labelOf('r1') }),
      ];
      expect(ownWorkLabel(events, 'run-1')).to.deep.equal(labelOf('r1'));
      expect(ownWorkLabel([event('x.pipeshub.answer.finalized', 'run-1', {})], 'run-1')).to.equal(null);
      expect(ownWorkLabel([], 'run-1')).to.equal(null);
    });

    it('ST: the children whose labels a turn joins are the ones it spawned, once each', () => {
      const events = [
        event('child.spawned', 'run-1', { childRunId: 'c1' }),
        event('child.spawned', 'run-1', { childRunId: 'c1' }),
        event('child.spawned', 'run-2', { childRunId: 'c9' }),
        event('child.spawned', 'run-1', { childRunId: 'c2' }),
      ];
      expect(spawnedChildren(events, 'run-1')).to.deep.equal(['c1', 'c2']);
    });

    it('ST: a personal turn joins the run before it; a shared one its latest own run and its visible runs', () => {
      expect(seenRuns(null)).to.equal(null);
      expect(seenRuns({ ownRuns: ['a'], visibleRuns: ['v'] })).to.deep.equal({ own: ['a'], visible: ['v'] });
      expect(seenRuns({ ownRuns: [] })).to.deep.equal({ own: [], visible: [] });
    });

    it('S2: the audience lists passed runs sorted, never an own run, and only when there is one', () => {
      const context = { ownerId: 'alice', shared: true, actors: ['alice', 'bob'] };
      expect(runAudience('bob', 'collaborator', context, ['b1'], ['a2', 'b1', 'a1', 'a2'])).to.deep.equal({
        sessionRole: 'collaborator',
        ownerRef: 'p_alice',
        ownRuns: ['b1'],
        visibleRuns: ['a1', 'a2'],
      });
      expect(runAudience('bob', 'collaborator', context, ['b1'], [])).to.not.have.property('visibleRuns');
      const many = Array.from({ length: MAX_VISIBLE_RUNS + 5 }, (_, i) => `a${String(i).padStart(4, '0')}`);
      expect(runAudience('bob', 'collaborator', context, [], many)?.visibleRuns).to.have.length(MAX_VISIBLE_RUNS);
      expect(runAudience('alice', 'owner', { ownerId: 'alice', shared: false, actors: [] }, [], ['x'])).to.equal(null);
    });

    it('S2: a run whose audience lists visible runs needs turn_labels; nothing else does', () => {
      const run = { agentKey: 'a1', input: { model: { chatMode: 'auto' } }, sessionRole: 'collaborator' as const };
      const needs = (audience: { visibleRuns?: string[] } | null) =>
        admissionRequirements(run as Parameters<typeof admissionRequirements>[0], [], 'loop', audience).features;
      expect(needs({ visibleRuns: ['a1'] })).to.include(TURN_LABELS_FEATURE);
      expect(needs({})).to.not.include(TURN_LABELS_FEATURE);
      expect(needs(null)).to.not.include(TURN_LABELS_FEATURE);
    });

    it('S2-4: a drained next run never carries the label decision made when it was queued', () => {
      const command = { visibleRuns: ['a1'], steerOnly: true, orgId: 'o' } as unknown as AdmitRunCommand;
      const drained = drainCommand({ id: 'n1', orgId: 'o', sessionId: 's', actorId: 'bob', runId: 'r', command, behindRunId: 'x', createdAt: 1 });
      expect(drained).to.not.have.property('visibleRuns');
      expect(drained).to.not.have.property('steerOnly');
    });

    it('S3-1: the fork keeps a passed foreign turn whole (question and answer) and stubs the rest', () => {
      const message = (messageType: string, senderId: string | null, content: string, label?: TurnLabel) => ({
        messageType,
        senderId,
        content,
        citations: [],
        fields: {},
        ...(label !== undefined ? { label } : {}),
      });
      const messages = [
        message('user_query', null, 'A1?'),
        message('tool_call', null, 'A1 tool'),
        message('bot_response', null, 'A1!', labelOf('r1')),
        message('user_query', null, 'A2?'),
        message('bot_response', null, 'A2!', labelOf('r2')),
        message('user_query', 'bob', 'B?'),
        message('bot_response', 'bob', 'B!'),
      ];
      expect(turnAnswers(messages)).to.deep.equal([2, 2, 2, 4, 4, 6, 6]);
      expect(foreignAnswers(messages, 'bob', 'alice')).to.deep.equal([
        { id: '2', label: labelOf('r1') },
        { id: '4', label: labelOf('r2') },
      ]);
      const forked = forkTranscript(messages, 'bob', 'alice', new Set(['2']));
      expect(forked.map((m) => (m.foreignTurn ? 'stub' : `${m.content}${m.passed === true ? ' (passed)' : ''}`))).to.deep.equal([
        'A1? (passed)',
        'A1! (passed)',
        'stub',
        'stub',
        'B?',
        'B!',
      ]);
      expect(forkTranscript(messages, 'bob', 'alice').filter((m) => m.foreignTurn)).to.have.length(2);
    });
  });
});
