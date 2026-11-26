import 'reflect-metadata';
import { expect } from 'chai';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { HkdfKeyEncryptionKey } from '../../../src/modules/agent-harness/adapters/crypto/hkdf-key-encryption-key';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { RecordingJournalStore } from '../../../src/modules/agent-harness/application/recording-journal-store';
import { PayloadReferencingJournalStore } from '../../../src/modules/agent-harness/application/payload-referencing-journal-store';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { SnapshotRecordingJournalStore } from '../../../src/modules/agent-harness/application/snapshot-recording-journal-store';
import { createHarnessJournal } from '../../../src/modules/agent-harness/composition';
import { noopHarnessEdition } from '../../../src/modules/agent-harness/edition';
import type { JournalAppendRecorder } from '../../../src/modules/agent-harness/ports/journal-append-recorder';
import { PayloadStore } from '../../../src/modules/agent-harness/ports/payload-store';

const innerOf = (store: unknown): unknown => (store as { inner: unknown }).inner;
const kek = new HkdfKeyEncryptionKey(() => Buffer.alloc(32, 1));

/**
 * The journal every harness service shares, as production composes it (no I/O
 * at construction): the audit chain outermost, then an edition's journal
 * recorders (none by default), the sandbox snapshots an append names
 * (OWD-12) and payload references (all read event data before it is
 * sealed), then the sealing store.
 */
describe('agent-harness composition: the shared journal stack', () => {
  it('a payload store that tracks references: audited, then snapshot-recording, referencing, then sealed', () => {
    const payloads = new InMemoryPayloadStore();
    const journal = createHarnessJournal({ payloads, keyEncryptionKey: kek });
    expect(journal.store).to.be.instanceOf(AuditedJournalStore);
    const recording = innerOf(journal.store);
    expect(recording).to.be.instanceOf(SnapshotRecordingJournalStore);
    const referencing = innerOf(recording);
    expect(referencing).to.be.instanceOf(PayloadReferencingJournalStore);
    expect(innerOf(referencing)).to.be.instanceOf(SealedJournalStore);
    expect(journal.payloads).to.equal(payloads);
  });

  it('a payload store without references: audited, then snapshot-recording, over the sealing store', () => {
    const plain = new InMemoryPayloadStore();
    const payloads: PayloadStore = {
      put: plain.put.bind(plain),
      get: plain.get.bind(plain),
    } as unknown as PayloadStore;
    const journal = createHarnessJournal({ payloads, keyEncryptionKey: kek });
    expect(innerOf(journal.store)).to.be.instanceOf(SnapshotRecordingJournalStore);
    expect(innerOf(innerOf(journal.store))).to.be.instanceOf(SealedJournalStore);
  });

  it("an edition's journal recorders sit under the audit chain, each built once, and see the append's plain data", () => {
    const built: string[] = [];
    const recorder = (): JournalAppendRecorder<never> => {
      built.push('counts');
      return { name: 'counts', take: () => null, record: async () => undefined };
    };
    const journal = createHarnessJournal({
      payloads: new InMemoryPayloadStore(),
      keyEncryptionKey: kek,
      edition: { ...noopHarnessEdition, journalRecorders: [recorder] },
    });
    expect(built).to.deep.equal(['counts']);
    const recording = innerOf(journal.store);
    expect(recording).to.be.instanceOf(RecordingJournalStore);
    expect((recording as { recorders: unknown[] }).recorders).to.have.length(1);
    expect(innerOf(recording)).to.be.instanceOf(SnapshotRecordingJournalStore);
  });
});
