/**
 * QA13-15: one journal folds to the same run in Node and in Python. Each
 * journal of `contracts/harness/vectors/journal-folds.json` (the golden
 * journals and the Python run machine's library) is folded by Node's
 * timeline; the summary must equal the one Python's fold recorded
 * (`backend/python/tests/contracts/test_journal_folds.py`).
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import type { JournalEvent } from '../../src/modules/agent-harness/domain/journal';
import { foldTimeline } from '../../src/modules/agent-harness/projection/timeline';
import { FoldSummary, summary } from './support/fold-summary';

const VECTORS = path.join(__dirname, '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors');

interface FoldVector {
  name: string;
  runId: string;
  journal?: string;
  events?: JournalEvent[];
  summary: FoldSummary;
}

const vectors = (
  JSON.parse(readFileSync(path.join(VECTORS, 'journal-folds.json'), 'utf-8')) as { journals: FoldVector[] }
).journals;

const eventsOf = (vector: FoldVector): JournalEvent[] =>
  vector.events ??
  (JSON.parse(readFileSync(path.join(VECTORS, vector.journal as string), 'utf-8')) as { events: JournalEvent[] }).events;

describe('QA13-15 Node folds every journal as Python does', () => {
  it('the vector holds the golden journals and the run machine library', () => {
    expect(vectors.filter((v) => v.name.startsWith('library/')).length).to.be.at.least(8);
    expect(vectors.filter((v) => v.journal !== undefined).length).to.be.at.least(15);
  });

  for (const vector of vectors) {
    it(vector.name, () => {
      expect(summary(foldTimeline(eventsOf(vector), vector.runId))).to.deep.equal(vector.summary);
    });
  }
});
