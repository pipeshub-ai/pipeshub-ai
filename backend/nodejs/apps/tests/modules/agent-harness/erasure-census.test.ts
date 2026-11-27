import { expect } from 'chai';
import {
  Trace,
  judgeCensus,
  needlesOf,
} from './support/erasure-census';
import { SessionDataStore } from '../../../src/modules/agent-harness/domain/session-erasure';

/** QA15-2 (ERA-18): what an erasure census found, judged against the coverage table. */

const STORES: SessionDataStore[] = [
  { store: 'erased', kind: 'mongo', coverage: { step: 'journal' } },
  { store: 'tombstoned', kind: 'mongo', coverage: { step: 'journal' }, residue: 'ids only' },
  { store: 'counters', kind: 'mongo', coverage: { kept: 'ids and numbers' } },
  { store: 'attachments', kind: 'graph', coverage: { deferred: 'open item' } },
];

const [ID, CANARY] = needlesOf(['ses-1'], ['canary-1']) as [Trace['needle'], Trace['needle']];
const trace = (store: string, needle = ID): Trace => ({ store, at: 'doc-1', needle });

describe('QA15-2: the erasure census verdict', () => {
  const cases: Array<{ name: string; traces: Trace[]; leaks: string[]; kept: number; deferred: number }> = [
    { name: 'nothing found is clean', traces: [], leaks: [], kept: 0, deferred: 0 },
    { name: 'an id in a store the table does not list is a leak', traces: [trace('newCollection')], leaks: ['unlisted'], kept: 0, deferred: 0 },
    { name: 'an id in a store its step erases is a leak', traces: [trace('erased')], leaks: ['not_erased'], kept: 0, deferred: 0 },
    { name: 'an id in a step store with a declared residue is kept', traces: [trace('tombstoned')], leaks: [], kept: 1, deferred: 0 },
    { name: 'an id in a kept store is kept', traces: [trace('counters')], leaks: [], kept: 1, deferred: 0 },
    { name: 'the canary is a leak even where ids are kept', traces: [trace('counters', CANARY), trace('tombstoned', CANARY)], leaks: ['canary', 'canary'], kept: 0, deferred: 0 },
    { name: 'the canary in an unlisted store is a canary leak', traces: [trace('newCollection', CANARY)], leaks: ['canary'], kept: 0, deferred: 0 },
    { name: 'a deferred store is reported, not judged', traces: [trace('attachments')], leaks: [], kept: 0, deferred: 1 },
  ];
  for (const c of cases) {
    it(c.name, () => {
      const verdict = judgeCensus(c.traces, STORES);
      expect(verdict.leaks.map((leak) => leak.reason)).to.deep.equal(c.leaks);
      expect(verdict.kept).to.have.length(c.kept);
      expect(verdict.deferred).to.have.length(c.deferred);
    });
  }
});
