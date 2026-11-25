/**
 * SEC-B-05 (ADR-MEM-01): a memory proposed by a turn that content an outsider could write reached waits for a
 * person, in every scope, whatever the review setting. The worker's untrusted taint travels as the turn label's
 * `integrity`; these cases play what an attacker, a dropped field or an older worker would make of it.
 */
import { expect } from 'chai';
import {
  memoryView,
  settlement,
  type MemoryPolicy,
  type MemoryScope,
} from '../../../src/modules/agent-harness/domain/memory';
import {
  joinIntegrity,
  joinLabels,
  ownWorkLabel,
  parseTurnLabel,
  readOutsideContent,
  spawnedChildren,
  type TurnLabel,
} from '../../../src/modules/agent-harness/domain/turn-labels';
import { memoryFixture } from './support/memory-store-contract';

const AUTO: MemoryPolicy = { mode: 'agent', review: 'auto' };
const CLEAN: TurnLabel = { complete: true, sources: [{ kind: 'indexed', recordId: 'r1' }], integrity: 'first_party' };
const EXTERNAL: TurnLabel = { ...CLEAN, integrity: 'external' };
const SCOPES: MemoryScope[] = ['user', 'session', 'agent'];

type Event = { type: string; runId: string; data: Record<string, unknown> };
const answer = (runId: string, label: unknown): Event => ({
  type: 'x.pipeshub.answer.finalized',
  runId,
  data: { outcome: 'answered', answer: 'ok', partTexts: [], citations: [], confidence: null, label },
});
const spawned = (runId: string, childRunId: string): Event => ({ type: 'child.spawned', runId, data: { childRunId } });

/** The turn label as ExecutionService.turnLabel composes it: own work, then each child's own work and children. */
function turnLabelOf(streams: Record<string, Event[]>, runId: string): TurnLabel {
  const childLabels = (id: string): Array<TurnLabel | null> =>
    spawnedChildren(streams[id] ?? [], id).map((child) =>
      joinLabels([ownWorkLabel(streams[child] ?? [], child), ...childLabels(child)]),
    );
  return joinLabels([ownWorkLabel(streams[runId] ?? [], runId), ...childLabels(runId)]);
}

describe('R38 SEC-B-05: memory settlement reads the turn label\'s integrity', () => {
  describe('settlement', () => {
    for (const scope of SCOPES) {
      it(`${scope}: external under review auto waits for a person`, () => {
        expect(settlement(scope, EXTERNAL, AUTO)).to.equal('proposed');
      });
    }

    it('only first_party is kept at once; agent memories still wait', () => {
      expect(settlement('user', CLEAN, AUTO)).to.equal('accepted');
      expect(settlement('session', CLEAN, AUTO)).to.equal('accepted');
      expect(settlement('agent', CLEAN, AUTO)).to.equal('proposed');
    });

    const forged: Array<[string, unknown]> = [
      ['missing (an older worker)', undefined],
      ['null', null],
      ['the string "false"', 'false'],
      ['the boolean false', false],
      ['upper case', 'FIRST_PARTY'],
      ['padded', ' first_party '],
      ['a homoglyph (Cyrillic а)', 'first_pаrty'],
      ['an empty string', ''],
      ['an object', { value: 'first_party' }],
    ];
    for (const [what, integrity] of forged) {
      it(`integrity ${what}: waits for a person (fail closed)`, () => {
        const label = { complete: true, sources: [], ...(integrity !== undefined ? { integrity } : {}) } as unknown as TurnLabel;
        expect(settlement('user', label, AUTO)).to.equal('proposed');
        expect(settlement('session', label, AUTO)).to.equal('proposed');
      });
    }

    it('a forged integrity never parses as a label: the worker\'s label is unknown, never clean', () => {
      for (const integrity of ['false', null, 'FIRST_PARTY', '', 1]) {
        expect(parseTurnLabel({ complete: true, sources: [], integrity })).to.equal(null);
      }
      expect(parseTurnLabel({ complete: true, sources: [], integrity: 'external' })?.integrity).to.equal('external');
    });
  });

  describe('the join', () => {
    it('external when any part is, first_party only when every part says so', () => {
      expect(joinIntegrity([CLEAN, EXTERNAL, CLEAN])).to.equal('external');
      expect(joinIntegrity([CLEAN, CLEAN])).to.equal('first_party');
      expect(joinIntegrity([CLEAN, { complete: true, sources: [] }])).to.equal(undefined);
      expect(joinIntegrity([CLEAN, null])).to.equal(undefined);
      expect(joinIntegrity([])).to.equal(undefined);
    });

    it('an unknown part keeps external, so the review can still say why', () => {
      expect(joinLabels([EXTERNAL, null])).to.deep.equal({ complete: false, sources: [], integrity: 'external' });
      expect(joinLabels([{ complete: false, sources: [] }, EXTERNAL]).integrity).to.equal('external');
      expect(joinLabels([CLEAN, null])).to.deep.equal({ complete: false, sources: [] });
    });

    it('labels without integrity join as they always did (stored documents keep their shape)', () => {
      expect(joinLabels([{ complete: true, sources: [] }])).to.deep.equal({ complete: true, sources: [] });
      expect(joinLabels([])).to.deep.equal({ complete: true, sources: [] });
    });
  });

  describe('a child run\'s taint reaches its parent\'s proposal', () => {
    it('an engine job read a web page; its parent\'s own work is clean', () => {
      const streams = {
        root: [spawned('root', 'job'), answer('root', CLEAN)],
        job: [answer('job', { complete: true, sources: [], integrity: 'external' })],
      };
      const label = turnLabelOf(streams, 'root');
      expect(label.integrity).to.equal('external');
      expect(settlement('user', label, AUTO)).to.equal('proposed');
    });

    it('a grandchild\'s taint reaches the root', () => {
      const streams = {
        root: [spawned('root', 'job'), answer('root', CLEAN)],
        job: [spawned('job', 'helper'), answer('job', CLEAN)],
        helper: [answer('helper', EXTERNAL)],
      };
      expect(settlement('user', turnLabelOf(streams, 'root'), AUTO)).to.equal('proposed');
    });

    it('a child that dropped its integrity (an older worker in the job) leaves the turn unsaid: it waits', () => {
      const streams = {
        root: [spawned('root', 'job'), answer('root', CLEAN)],
        job: [answer('job', { complete: true, sources: [] })],
      };
      const label = turnLabelOf(streams, 'root');
      expect(label.integrity).to.equal(undefined);
      expect(settlement('user', label, AUTO)).to.equal('proposed');
    });

    it('control: a clean root with clean children is kept at once', () => {
      const streams = {
        root: [spawned('root', 'job'), answer('root', CLEAN)],
        job: [answer('job', CLEAN)],
      };
      expect(settlement('user', turnLabelOf(streams, 'root'), AUTO)).to.equal('accepted');
    });
  });

  describe('defence in depth: what the run\'s own journal shows', () => {
    const lying = (event: Event): Event[] => [event, answer('run', CLEAN)];

    it('a web page among its citations', () => {
      const events = lying({ type: 'x.pipeshub.citations.recorded', runId: 'run', data: { webRecords: [{ url: 'https://x.example' }] } });
      expect(ownWorkLabel(events, 'run')?.integrity).to.equal('external');
    });

    it('a tool outcome labelled external (an MCP server\'s result, an outsider\'s email)', () => {
      const events = lying({ type: 'tool.outcome', runId: 'run', data: { invocationId: 'i1', status: 'ok', labels: { integrity: 'external' } } });
      expect(ownWorkLabel(events, 'run')?.integrity).to.equal('external');
    });

    it('a fetched repository', () => {
      const events = lying({ type: 'x.pipeshub.repository.fetched', runId: 'run', data: { host: 'github.com', repository: 'a/b', commit: 'c' } });
      expect(ownWorkLabel(events, 'run')?.integrity).to.equal('external');
    });

    it('no answer at all, but a web read: unknown and external', () => {
      const events = [{ type: 'x.pipeshub.citations.recorded', runId: 'run', data: { webRecords: [{}] } }];
      expect(ownWorkLabel(events, 'run')).to.deep.equal({ complete: false, sources: [], integrity: 'external' });
    });

    it('another run\'s web read is not this run\'s own work; empty web lists and internal outcomes are nothing', () => {
      const events: Event[] = [
        { type: 'x.pipeshub.citations.recorded', runId: 'other', data: { webRecords: [{}] } },
        { type: 'x.pipeshub.citations.recorded', runId: 'run', data: { webRecords: [] } },
        { type: 'tool.outcome', runId: 'run', data: { labels: { integrity: 'internal' } } },
        answer('run', CLEAN),
      ];
      expect(readOutsideContent(events, 'run')).to.equal(false);
      expect(ownWorkLabel(events, 'run')).to.deep.equal(CLEAN);
    });

    it('sticky within the run: one early web read, then many clean steps', () => {
      const events: Event[] = [
        { type: 'x.pipeshub.citations.recorded', runId: 'run', data: { webRecords: [{}] } },
        ...Array.from({ length: 30 }, (_, i) => ({
          type: 'tool.outcome',
          runId: 'run',
          data: { invocationId: `i${String(i)}`, status: 'ok', labels: { integrity: 'first_party' } },
        })),
        answer('run', CLEAN),
      ];
      expect(settlement('user', joinLabels([ownWorkLabel(events, 'run')]), AUTO)).to.equal('proposed');
    });
  });

  describe('the review says why', () => {
    it('a memory whose turn was external is shown as having followed outside content', () => {
      expect(memoryView(memoryFixture({ status: 'proposed', label: EXTERNAL })).followedOutsideContent).to.equal(true);
      expect(memoryView(memoryFixture({ status: 'proposed', label: CLEAN })).followedOutsideContent).to.equal(false);
      expect(memoryView(memoryFixture({ status: 'pending', label: null })).followedOutsideContent).to.equal(false);
    });
  });
});
