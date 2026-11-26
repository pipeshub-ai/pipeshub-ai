import { expect } from 'chai';
import sinon from 'sinon';
import {
  InMemoryJournalStore,
  InMemoryPayloadStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { StoragePayloadStore } from '../../../src/modules/agent-harness/adapters/storage/storage-payload-store';
import { PayloadStoreUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import { JOURNAL_EVENT_WRITERS } from '../../../src/modules/agent-harness/contracts/event-catalog';
import { describeJournalStoreContract, JournalBackend } from './support/journal-store-contract';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { PayloadReferencingJournalStore } from '../../../src/modules/agent-harness/application/payload-referencing-journal-store';
import { InMemoryAuditChainStore } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import { JournalStore } from '../../../src/modules/agent-harness/ports/journal-store';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { SnapshotRecordingJournalStore } from '../../../src/modules/agent-harness/application/snapshot-recording-journal-store';
import { RecordingJournalStore } from '../../../src/modules/agent-harness/application/recording-journal-store';
import type { JournalAppendRecorder } from '../../../src/modules/agent-harness/ports/journal-append-recorder';
import { InMemorySandboxSnapshotIndex } from '../../../src/modules/agent-harness/adapters/in-memory-sandbox-snapshots';
import { testCipher } from './support/data-protection';

const inMemoryBackend = (
  wrap: (
    inner: InMemoryJournalStore,
    payloads: InMemoryPayloadStore,
  ) => JournalStore<unknown> = (inner) => inner,
): JournalBackend<unknown> => {
  const payloads = new InMemoryPayloadStore();
  return {
    journal: wrap(new InMemoryJournalStore(), payloads),
    payloads,
    transact: (work) => work(undefined),
    corruptPayload: async (_orgId, ref) => {
      payloads.objects.set(ref, Buffer.from('{"tampered":true}'));
    },
    deletePayload: async (_orgId, ref) => {
      payloads.objects.delete(ref);
    },
  };
};

describeJournalStoreContract('in-memory', () => inMemoryBackend());

describeJournalStoreContract('sealed in-memory (O-1c)', () =>
  inMemoryBackend((inner) => new SealedJournalStore(inner, testCipher())),
);

describeJournalStoreContract('audited in-memory (O-2a)', () =>
  inMemoryBackend((inner) => new AuditedJournalStore(inner, new InMemoryAuditChainStore())),
);

describeJournalStoreContract('payload-referencing in-memory (T5)', () =>
  inMemoryBackend((inner, payloads) => new PayloadReferencingJournalStore(inner, payloads)),
);

describeJournalStoreContract('snapshot-recording in-memory (OWD-12)', () =>
  inMemoryBackend((inner) => new SnapshotRecordingJournalStore(inner, new InMemorySandboxSnapshotIndex(), 'default')),
);

/** A recorder that keeps every event's type, as an edition's recorder would keep its part. */
const typesRecorder = (): JournalAppendRecorder<unknown> => ({
  name: 'types',
  take: (type) => type,
  record: async () => undefined,
});

describeJournalStoreContract('recording in-memory (an edition recorder)', () =>
  inMemoryBackend((inner) => new RecordingJournalStore(inner, [typesRecorder()])),
);

describeJournalStoreContract('production decorator stack in-memory', () =>
  inMemoryBackend(
    (inner, payloads) =>
      new AuditedJournalStore(
        new RecordingJournalStore(
          new SnapshotRecordingJournalStore(
            new PayloadReferencingJournalStore(new SealedJournalStore(inner, testCipher()), payloads),
            new InMemorySandboxSnapshotIndex(),
            'default',
          ),
          [typesRecorder()],
        ),
        new InMemoryAuditChainStore(),
      ),
  ),
);

describe('journal event catalog', () => {
  it('merges core and x.pipeshub writers, with run.* and interaction.resolved node-only', () => {
    expect(JOURNAL_EVENT_WRITERS['x.pipeshub.citation.recorded']).to.deep.equal(
      ['worker'],
    );
    for (const [type, writers] of Object.entries(JOURNAL_EVENT_WRITERS)) {
      if (type.startsWith('run.') || type === 'interaction.resolved') {
        expect(writers, type).to.deep.equal(['node']);
      }
    }
  });

  it('A-13 (I3): no worker may write principal, grant, policy, admission, interaction resolution or session authority', () => {
    const authority = [
      'run.admitted',
      'run.spec_pinned',
      'run.claimed',
      'run.cancel_requested',
      'run.resumed',
      'interaction.resolved',
      'input.queued',
      'child.spawned',
      'session.imported',
      'session.snapshot',
      'session.participant_changed',
      'session.forked',
      'session.erased',
    ];
    for (const type of authority) {
      expect(JOURNAL_EVENT_WRITERS[type], type).to.deep.equal(['node']);
    }
    // A future type named for one of these concerns must not open to workers.
    const authorityName =
      /principal|grant|policy|admi(t|ssion)|resolv|visibility|spec_pinned/;
    for (const [type, writers] of Object.entries(JOURNAL_EVENT_WRITERS)) {
      if (authorityName.test(type)) {
        expect(writers, type).to.not.include('worker');
      }
    }
  });
});

describe('StoragePayloadStore', () => {
  const storage = () => ({
    uploadDocumentToStorageService: sinon.stub().resolves({
      statusCode: 200,
      data: 'http://storage/agent-harness/journal/org-1/abc.bin',
    }),
    getBufferFromStorageService: sinon
      .stub()
      .resolves({ statusCode: 200, data: Buffer.from('{}') }),
    objectExistsAtPath: sinon.stub().resolves(false),
  });

  it('uploads under a per-org content address and reads back through the vendor URL', async () => {
    const backend = storage();
    const store = new StoragePayloadStore(async () => backend as never, 's3');
    const ref = await store.put('org-1', 'sha256:abc', Buffer.from('{}'));
    expect(ref).to.equal('http://storage/agent-harness/journal/org-1/abc.bin');
    expect(backend.uploadDocumentToStorageService.firstCall.args[0]).to.include(
      {
        documentPath: 'agent-harness/journal/org-1/abc.bin',
        mimeType: 'application/octet-stream',
        isVersioned: false,
      },
    );
    expect((await store.get('org-1', ref))?.toString()).to.equal('{}');
    const document = backend.getBufferFromStorageService.firstCall.args[0];
    expect(document.s3).to.deep.equal({ url: ref });
  });

  it("refuses another org's ref and maps a confirmed-absent object to null", async () => {
    const backend = storage();
    backend.getBufferFromStorageService.rejects(new Error('ENOENT'));
    const store = new StoragePayloadStore(
      async () => backend as never,
      'local',
    );
    expect(
      await store.get(
        'org-2',
        'http://storage/agent-harness/journal/org-1/abc.bin',
      ),
    ).to.equal(null);
    expect(
      await store.get(
        'org-1',
        'http://storage/agent-harness/journal/org-1/abc.bin',
      ),
    ).to.equal(null);
  });

  const unavailable = async (
    work: Promise<unknown>,
    operation: 'put' | 'get',
  ): Promise<void> => {
    try {
      await work;
      expect.fail('expected the payload store to be unavailable');
    } catch (error) {
      expect(error).to.be.instanceOf(PayloadStoreUnavailableError);
      expect((error as PayloadStoreUnavailableError).operation).to.equal(
        operation,
      );
    }
  };

  it('DM-5: a read failure is "unavailable" unless the store answered absent', async () => {
    const ref = 'http://storage/agent-harness/journal/org-1/abc.bin';
    const backend = storage();
    backend.getBufferFromStorageService.rejects(new Error('timeout'));
    backend.objectExistsAtPath.resolves(true);
    const store = new StoragePayloadStore(
      async () => backend as never,
      'local',
    );
    await unavailable(store.get('org-1', ref), 'get');
    backend.objectExistsAtPath.rejects(new Error('timeout'));
    await unavailable(store.get('org-1', ref), 'get');
    await unavailable(
      new StoragePayloadStore(
        () => Promise.reject(new Error('storage config unreadable')),
        'local',
      ).get('org-1', ref),
      'get',
    );
  });

  it('DM-4: an upload that fails or is refused is "unavailable"', async () => {
    const backend = storage();
    backend.uploadDocumentToStorageService.rejects(new Error('ECONNRESET'));
    const store = new StoragePayloadStore(async () => backend as never, 's3');
    await unavailable(
      store.put('org-1', 'sha256:abc', Buffer.from('x')),
      'put',
    );
    backend.uploadDocumentToStorageService.resolves({ statusCode: 503 });
    await unavailable(
      store.put('org-1', 'sha256:abc', Buffer.from('x')),
      'put',
    );
  });
});
