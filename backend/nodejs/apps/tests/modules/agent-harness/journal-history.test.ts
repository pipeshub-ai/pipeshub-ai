/**
 * QA-21: the journal history check the race suites judge every outcome by:
 * QA-13's H rules (their own cases are in tests/support/harness) and the
 * decision-point rules D1..D5, each shown to fire on the history a lost or
 * duplicated effect leaves; a legal history passes.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { journalViolations } from './support/journal-history';

type Spec = [type: string, data?: Record<string, unknown>, worker?: number];

const history = (...specs: Spec[]): JournalEvent[] =>
  specs.map(([type, data, fence], index) => ({
    orgId: 'o',
    sessionId: 's',
    streamId: 's',
    seq: index + 1,
    runId: 'r1',
    ts: '2026-10-10T00:00:00.000Z',
    type,
    schemaVersion: 1,
    critical: true,
    visibility: 'owner',
    data: data ?? {},
    writer: fence === undefined ? 'node' : 'worker',
    ...(fence !== undefined ? { fence } : {}),
  }));

const ADMITTED: Spec = ['run.admitted'];
const CLAIMED: Spec = ['run.claimed', { fence: 1 }];
const PINNED: Spec = ['run.spec_pinned'];
const ASKED: Spec = ['interaction.requested', { interactionId: 'ix' }, 1];
const resolved = (decision = 'approved'): Spec => ['interaction.resolved', { interactionId: 'ix', decision }];

describe('QA-21 journal history check', () => {
  it('a legal history: admitted, claimed, asked, decided and granted, queued input, stopped, cancelled', () => {
    expect(
      journalViolations(
        history(
          ADMITTED,
          CLAIMED,
          PINNED,
          ASKED,
          resolved(),
          ['interaction.granted', { interactionId: 'ix' }],
          ['input.queued', { messageSeq: 1 }],
          ['run.cancel_requested'],
          ['run.cancelled'],
        ),
      ),
    ).to.deep.equal([]);
  });

  const broken: Array<[string, JournalEvent[], string]> = [
    ['a seq gap (QA-13 H1, composed)', history(ADMITTED, CLAIMED).map((e, i) => ({ ...e, seq: i === 1 ? 3 : 1 })), 'H1'],
    ['a write after the ending (QA-13 H6, composed)', history(ADMITTED, ['run.completed'], ['run.failed']), 'H6'],
    ['the Stop recorded twice', history(ADMITTED, CLAIMED, ['run.cancel_requested'], ['run.cancel_requested']), 'D1'],
    ['a queued message skipped', history(ADMITTED, ['input.queued', { messageSeq: 1 }], ['input.queued', { messageSeq: 3 }]), 'D2 message 3 after 1'],
    ['two decisions on one approval', history(ADMITTED, CLAIMED, PINNED, ASKED, resolved(), resolved('denied')), 'D3 ix resolved twice'],
    ['a decision on nothing asked', history(ADMITTED, resolved()), 'D3 ix resolved but never requested'],
    ['a grant without its approval', history(ADMITTED, CLAIMED, PINNED, ASKED, resolved('denied'), ['interaction.granted', { interactionId: 'ix' }]), 'D3 grant'],
    ['a decision after the Stop', history(ADMITTED, CLAIMED, PINNED, ASKED, ['run.cancel_requested'], resolved()), 'D4'],
    ['a stopped run that completed', history(ADMITTED, CLAIMED, ['run.cancel_requested'], ['run.completed']), 'D5'],
  ];
  for (const [name, events, rule] of broken) {
    it(`flags ${name}`, () => {
      expect(journalViolations(events).join('\n')).to.contain(rule);
    });
  }

  it('judges only the named runs when a seeded stream holds hand-written ones', () => {
    const seeded = history(['tool.intent', {}, 1], ADMITTED).map((e, i) => (i === 0 ? { ...e, runId: 'seed' } : e));
    expect(journalViolations(seeded, new Set(['r1']))).to.deep.equal([]);
    expect(journalViolations(seeded)).to.not.deep.equal([]);
  });
});
