import { expect } from 'chai';
import sinon from 'sinon';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { PayloadReferencingJournalStore } from '../../../src/modules/agent-harness/application/payload-referencing-journal-store';
import { RecordingJournalStore } from '../../../src/modules/agent-harness/application/recording-journal-store';
import { JournalStore } from '../../../src/modules/agent-harness/ports/journal-store';

/**
 * The journal decorators act on appends only (payload references, the audit
 * chain): every read answers exactly what the store they wrap answers, with
 * the caller's arguments and transaction, and writes nothing.
 */
const decorators: Array<[string, (inner: JournalStore<string>, sideEffects: sinon.SinonStub) => JournalStore<string>]> = [
  ['PayloadReferencingJournalStore', (inner, effect) => new PayloadReferencingJournalStore(inner, { markReferenced: effect } as never)],
  ['AuditedJournalStore', (inner, effect) => new AuditedJournalStore(inner, { append: effect } as never)],
  ['RecordingJournalStore', (inner, effect) => new RecordingJournalStore(inner, [{ name: 'r', take: () => 1, record: effect }])],
];

describe('agent-harness journal store decorators: reads pass through', () => {
  for (const [name, wrap] of decorators) {
    it(name, async () => {
      const events = [{ seq: 1 }] as never;
      const inner = {
        append: sinon.stub(),
        lastSeq: sinon.stub().resolves({ 's-1': 4, 's-2': 0 }),
        readStream: sinon.stub().resolves(events),
        readRun: sinon.stub().resolves(events),
      };
      const effect = sinon.stub().resolves();
      const store = wrap(inner as unknown as JournalStore<string>, effect);
      expect(await store.lastSeq('org-1', ['s-1', 's-2'], 'tx-1')).to.deep.equal({ 's-1': 4, 's-2': 0 });
      expect(inner.lastSeq.calledOnceWithExactly('org-1', ['s-1', 's-2'], 'tx-1')).to.equal(true);
      expect(await store.readStream('org-1', 's-1', 3, 50)).to.equal(events);
      expect(inner.readStream.calledOnceWithExactly('org-1', 's-1', 3, 50)).to.equal(true);
      expect(await store.readRun('org-1', 'run-1', 0, 10)).to.equal(events);
      expect(inner.readRun.calledOnceWithExactly('org-1', 'run-1', 0, 10)).to.equal(true);
      expect(effect.called).to.equal(false);
      expect(inner.append.called).to.equal(false);
    });
  }
});
