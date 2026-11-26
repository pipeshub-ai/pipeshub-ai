/**
 * For unit tests that fake the document store underneath (they stub `DocumentModel`) and are not about the write state: a
 * repository whose write-state operations do nothing and cost nothing, while every other method is the real MongoDB one over
 * the test's stubs. The tests about the write state itself use the real operations (tests/stores) or their own fakes.
 *
 *   describe('...', () => { useInertWriteState(); ... })
 */
import { MongoDocumentRepository } from '../../src/libs/repositories/document/mongo';
import { setDocumentRepository } from '../../src/libs/repositories/document';
import type { DocumentRepository } from '../../src/libs/repositories/document';

export function inertWriteState(base: DocumentRepository = new MongoDocumentRepository()): DocumentRepository {
  const overrides: Partial<DocumentRepository> = {
    beginWrite: async () => ({ writeSeq: 1 }),
    renewWrite: async () => undefined,
    endWrite: async () => undefined,
    bumpSeq: async () => ({ writeSeq: 1 }),
    adoptDirectUpload: async () => true,
    correctSize: async () => undefined,
    serverClockMs: async () => Date.now(),
    peekWriteState: async () => ({ writeSeq: 0, writers: [] }),
    markAwaitingDirectUpload: async (orgId, id, patch) => {
      const row = await base.update(orgId, id, patch);
      return row ? { row, writeSeq: 1 } : null;
    },
    getWithFingerprintState: async (orgId, id) => {
      const row = await base.get(orgId, id);
      return row ? { row, writeSeq: 0, writers: [], serverNow: Date.now() } : null;
    },
  };
  return new Proxy(base, {
    get(target, prop, receiver) {
      if (prop in overrides) return overrides[prop as keyof DocumentRepository];
      const v = Reflect.get(target, prop, receiver) as unknown;
      return typeof v === 'function' ? (v as (...a: unknown[]) => unknown).bind(target) : v;
    },
  });
}

/** Installs the inert repository around each test of the calling describe and puts the default back after it. */
export function useInertWriteState(): void {
  beforeEach(() => setDocumentRepository(inertWriteState()));
  afterEach(() => setDocumentRepository());
}
