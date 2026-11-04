import { expect } from 'chai';
import {
  batchText,
  fromQueuedMessage,
  mergeBatchMessage,
  toQueuedMessage,
  withoutBatchMessages,
} from '../../../src/integrations/slack-bot/src/utils/turn-batch';

describe('turn-batch', () => {
  it('joins texts in order and drops empty ones', () => {
    expect(batchText(['a', ' ', 'b'])).to.equal('a\n\nb');
  });

  it("merges files without duplicates and keeps the newest message's ids", () => {
    const f1 = { id: 'F1' };
    const f2 = { id: 'F2' };
    const merged = mergeBatchMessage([
      { ts: '1', thread_ts: 'T', text: 'a', files: [f1] },
      { ts: '2', thread_ts: 'T', text: 'b', files: [f1, f2] },
    ]);
    expect(merged.files).to.deep.equal([f1, f2]);
    expect(merged.ts).to.equal('2');
    expect(merged.thread_ts).to.equal('T');
    expect(merged.text).to.equal('a\n\nb');
  });

  it("leaves the batch's own messages out of the thread context", () => {
    const prior = [{ ts: '1' }, { ts: '2' }, { ts: '3' }];
    expect(withoutBatchMessages(prior, new Set(['2'])).map((m) => m.ts)).to.deep.equal(['1', '3']);
  });

  it('round-trips a queued message', () => {
    const m = { ts: '5', thread_ts: 'T', channel: 'C', user: 'U', text: 'hi', files: [{ id: 'F' }] };
    const queued = toQueuedMessage('mention', m, () => 42);
    expect(queued.receivedAt).to.equal(42);
    expect(fromQueuedMessage(queued)).to.deep.equal(m);
  });
});
