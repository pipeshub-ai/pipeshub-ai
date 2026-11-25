/** QA13-4 (JRN-22): each history rule, broken once, is reported as exactly that rule (Python's cases). */
import { expect } from 'chai';
import type { HarnessEvent } from '../../../src/modules/agent-harness/domain/execution';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import {
  HistoryViolation,
  journalHistoryViolations,
  v1HistoryViolations,
} from './journal-history';

const event = (
  seq: number,
  type: string,
  writer: 'node' | 'worker' = 'node',
  fence?: number,
  data: Record<string, unknown> = {},
): JournalEvent => ({
  orgId: 'org',
  sessionId: 'ses',
  streamId: 'ses',
  seq,
  runId: 'run',
  ...(fence !== undefined ? { attemptId: `run.a${String(fence)}`, fence } : {}),
  type,
  schemaVersion: 1,
  critical: true,
  visibility: 'owner',
  ts: '2026-01-01T00:00:00.000Z',
  writer,
  data,
});

const claim = (seq: number, fence: number): JournalEvent =>
  event(seq, 'run.claimed', 'node', fence, { attempt: fence, fence, leaseMs: 30_000 });

const valid = (): JournalEvent[] => {
  const erased = event(8, 'session.erased');
  delete erased.runId;
  return [
    event(1, 'run.admitted', 'node', undefined, { runKind: 'interactive' }),
    claim(2, 1),
    event(3, 'run.spec_pinned', 'node', 1),
    event(4, 'tool.intent', 'worker', 1, { invocationId: 't1.0' }),
    claim(5, 2),
    event(6, 'tool.outcome', 'worker', 2, { invocationId: 't1.0', status: 'ok' }),
    event(7, 'run.completed', 'node', 2, { outcome: 'answered' }),
    erased,
  ];
};

const renumber = (events: JournalEvent[]): JournalEvent[] =>
  events.map((e, index) => ({ ...e, seq: index + 1 }));
const replace = (seq: number, next: JournalEvent) => (events: JournalEvent[]) =>
  events.map((e) => (e.seq === seq ? next : e));

const BROKEN: Record<string, [HistoryViolation['rule'], (events: JournalEvent[]) => JournalEvent[]]> = {
  'a gap in seq': ['H1', (events) => [...events.slice(0, 7), { ...events[7]!, seq: 9 }]],
  'a repeated seq': ['H1', (events) => [...events.slice(0, 4), events[3]!, ...events.slice(4)]],
  'a run that does not start with its admission': ['H2', (events) => renumber(events.slice(1))],
  'a second pin': ['H3', (events) => renumber([...events.slice(0, 3), events[2]!, ...events.slice(3)])],
  'a worker event before the pin': [
    'H3',
    (events) => renumber([events[0]!, events[1]!, events[3]!, events[2]!, ...events.slice(4)]),
  ],
  'a claim that does not raise the fence': ['H4', replace(5, claim(5, 1))],
  'a worker event under an older fence': [
    'H4',
    replace(6, event(6, 'tool.outcome', 'worker', 1, { invocationId: 't1.0', status: 'ok' })),
  ],
  'an outcome without its intent': ['H5', replace(4, event(4, 'turn.started', 'worker', 1, { turn: 1 }))],
  // EV-06 (C2.49): every call ends with exactly one outcome.
  'a call left open when the run ends': ['H8', (events) => renumber(events.filter((e) => e.seq !== 6))],
  'a second outcome of one call': [
    'H8',
    (events) => renumber([...events.slice(0, 6), events[5]!, ...events.slice(6)]),
  ],
  'an event after the end': ['H6', replace(8, event(8, 'turn.started', 'worker', 2, { turn: 2 }))],
  'a second end': ['H6', replace(8, event(8, 'run.failed', 'node', 2, { errorType: 'x' }))],
};

describe('QA13-4 journal history rules (JRN-22)', () => {
  it('a valid history breaks no rule', () => {
    expect(journalHistoryViolations(valid())).to.deep.equal([]);
  });

  for (const [name, [rule, breaking]] of Object.entries(BROKEN)) {
    it(`${name}: reported as ${rule}, and only ${rule}`, () => {
      const violations = journalHistoryViolations(breaking(valid()));
      expect(violations).not.to.deep.equal([]);
      expect([...new Set(violations.map((v) => v.rule))]).to.deep.equal([rule]);
    });
  }

  it('an import before the admission and a call that never ran (an error outcome) break no rule', () => {
    // As admission journals an imported history: the import and its label, then run.admitted (QA21).
    const imported = [event(1, 'session.imported'), event(2, 'x.pipeshub.history.labelled')];
    const shifted = valid().map((e) => ({ ...e, seq: e.seq + 2 }));
    expect(journalHistoryViolations([...imported, ...shifted])).to.deep.equal([]);
    const unrun = event(6, 'tool.outcome', 'worker', 2, { invocationId: 't1.1', status: 'error' });
    const events = valid();
    expect(journalHistoryViolations(renumber([...events.slice(0, 6), unrun, ...events.slice(6)]))).to.deep.equal([]);
  });

  it('EV-06: an unknown outcome ends a call, and a re-call after reconciliation opens it again', () => {
    const events = valid();
    const unknown = event(6, 'tool.unknown', 'worker', 2, { invocationId: 't1.0' });
    expect(journalHistoryViolations(replace(6, unknown)(events))).to.deep.equal([]);
    const recalled = event(7, 'tool.intent', 'worker', 2, { invocationId: 't1.0' });
    const outcome = event(8, 'tool.outcome', 'worker', 2, { invocationId: 't1.0', status: 'ok' });
    const reopened = renumber([...events.slice(0, 5), unknown, recalled, ...events.slice(6)]);
    expect([...new Set(journalHistoryViolations(reopened).map((v) => v.rule))]).to.deep.equal(['H8']);
    expect(
      journalHistoryViolations(renumber([...events.slice(0, 5), unknown, recalled, outcome, ...events.slice(6)])),
    ).to.deep.equal([]);
  });

  it('admittedOnly judges only the runs the journal admitted, and seq over every event', () => {
    const synthetic = valid().slice(1);
    expect(journalHistoryViolations(renumber(synthetic), { admittedOnly: true })).to.deep.equal([]);
    expect(journalHistoryViolations(renumber(synthetic)).map((v) => v.rule)).to.include('H2');
    const gap = synthetic.map((e) => ({ ...e, seq: e.seq + 1 }));
    expect(journalHistoryViolations(gap, { admittedOnly: true }).map((v) => v.rule)).to.include('H1');
  });

  it('a sealed claim is read by its envelope fence; a cancelled outcome needs no intent', () => {
    const events = valid();
    const sealed = { ...events[1]! };
    delete sealed.data;
    const cancelled = event(6, 'tool.outcome', 'worker', 2, { invocationId: 't7.7', status: 'cancelled' });
    expect(
      journalHistoryViolations(renumber([...replace(2, sealed)(events).slice(0, 6), cancelled, ...events.slice(6)])),
    ).to.deep.equal([]);
  });

  it('H7: v1 events are gap-free per session and commitSeq never goes back within a run', () => {
    const v1 = (eventSeq: number, commitSeq: number): HarnessEvent => ({
      orgId: 'org',
      sessionId: 'ses',
      runId: 'run',
      eventSeq,
      commitSeq,
      type: 'run_queued',
      data: {},
      createdAt: 0,
    });
    expect(v1HistoryViolations([v1(1, 0), v1(2, 1), v1(3, 1)])).to.deep.equal([]);
    expect(v1HistoryViolations([v1(1, 0), v1(3, 1)]).map((v) => v.rule)).to.deep.equal(['H7']);
    expect(v1HistoryViolations([v1(1, 2), v1(2, 1)]).map((v) => v.rule)).to.deep.equal(['H7']);
  });
});
