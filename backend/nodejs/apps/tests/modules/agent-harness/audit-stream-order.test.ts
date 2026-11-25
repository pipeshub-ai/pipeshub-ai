/** PERFC-20: the order pending audit appends are linked in (`inStreamOrder`). */
import { expect } from 'chai';
import fc from 'fast-check';
import { inStreamOrder } from '../../../src/modules/agent-harness/domain/audit';
import { propertyRuns } from '../../support/harness/property-profile';

interface Append {
  streamId: string;
  journalSeq: number;
}

const append = (streamId: string, journalSeq: number): Append => ({ streamId, journalSeq });

describe('PERFC-20: pending audit appends are linked with every stream in journal order', () => {
  it('appends already in journal order stay as they were read', () => {
    const read = [append('a', 1), append('b', 7), append('a', 2), append('c', 1), append('b', 8)];
    expect(inStreamOrder(read)).to.deep.equal(read);
    expect(inStreamOrder([])).to.deep.equal([]);
  });

  it("a stream read out of order is put right in the places that stream had; other streams keep theirs", () => {
    const read = [append('a', 3), append('b', 1), append('a', 1), append('b', 2), append('a', 2)];
    expect(inStreamOrder(read)).to.deep.equal([append('a', 1), append('b', 1), append('a', 2), append('b', 2), append('a', 3)]);
  });

  it('property: the same appends, every stream ascending, each place still held by the stream that had it', () => {
    const appends = fc
      .array(fc.constantFrom('a', 'b', 'c', 'd'), { maxLength: 24 })
      .chain((streams) => {
        const next = new Map<string, number>();
        const inOrder = streams.map((streamId) => {
          const journalSeq = (next.get(streamId) ?? 0) + 1;
          next.set(streamId, journalSeq);
          return append(streamId, journalSeq);
        });
        return fc.shuffledSubarray(inOrder, { minLength: inOrder.length });
      });
    fc.assert(
      fc.property(appends, (read) => {
        const ordered = inStreamOrder(read);
        expect(ordered.map((each) => each.streamId), 'places').to.deep.equal(read.map((each) => each.streamId));
        expect([...ordered].sort(byPosition), 'a permutation').to.deep.equal([...read].sort(byPosition));
        const last = new Map<string, number>();
        for (const each of ordered) {
          expect(each.journalSeq).to.be.greaterThan(last.get(each.streamId) ?? 0);
          last.set(each.streamId, each.journalSeq);
        }
      }),
      propertyRuns(500),
    );
  });
});

function byPosition(left: Append, right: Append): number {
  return left.streamId.localeCompare(right.streamId) || left.journalSeq - right.journalSeq;
}
