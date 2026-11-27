/**
 * What every WorkspaceObjectBytes must do (SS3), WO-1..WO-8: the in-memory
 * store (`workspace-object-bytes.contract.test.ts`) and PipesHub storage's
 * real internal routes on its local backend (`workspace-object-bytes.storage.itest.ts`).
 */
import { expect } from 'chai';
import { createHash, randomBytes } from 'node:crypto';
import { InnerObjectRef } from '../../../../src/modules/agent-harness/domain/workspace-sealing';
import { WorkspaceObjectBytes } from '../../../../src/modules/agent-harness/ports/workspace-objects';

export interface WorkspaceObjectsFixture {
  objects: WorkspaceObjectBytes;
  /** Two orgs the store accepts (storage scopes by ObjectId org ids). */
  orgs: readonly [string, string];
  /** An object id of the store's own form that names nothing stored. */
  missingObjectId(): string;
  /** Replaces the stored bytes behind `ref` (bit rot, a restore from the wrong backup). */
  tamper(orgId: string, ref: InnerObjectRef, bytes: Buffer): Promise<void>;
}

const sha256 = (bytes: Buffer): string => `sha256:${createHash('sha256').update(bytes).digest('hex')}`;

const rejects = async (work: Promise<unknown>): Promise<void> => {
  let failed = false;
  try {
    await work;
  } catch {
    failed = true;
  }
  expect(failed, 'expected the read to be refused').to.equal(true);
};

export function describeWorkspaceObjectBytesContract(
  name: string,
  create: () => Promise<WorkspaceObjectsFixture>,
): void {
  describe(`WorkspaceObjectBytes contract: ${name}`, function () {
    this.timeout(30_000);
    let f: WorkspaceObjectsFixture;
    let org: string;
    let otherOrg: string;

    beforeEach(async () => {
      f = await create();
      [org, otherOrg] = f.orgs;
    });

    it('WO-1: put answers a ref with the content hash and size, and get returns the same bytes', async () => {
      const bytes = randomBytes(1_024);
      const ref = await f.objects.put(org, bytes);
      expect(ref.contentHash).to.equal(sha256(bytes));
      expect(ref.sizeBytes).to.equal(bytes.length);
      expect(ref.objectId).to.be.a('string').and.not.equal('');
      expect((await f.objects.get(org, ref)).equals(bytes)).to.equal(true);
    });

    it('WO-2: every byte value and a larger object round-trip exactly', async () => {
      const every = Buffer.from(Array.from({ length: 256 }, (_, i) => i));
      const large = randomBytes(512 * 1_024);
      for (const bytes of [every, large]) {
        const ref = await f.objects.put(org, bytes);
        expect((await f.objects.get(org, ref)).equals(bytes)).to.equal(true);
      }
    });

    it("WO-3: another org cannot read an org's object", async () => {
      const ref = await f.objects.put(org, randomBytes(64));
      await rejects(f.objects.get(otherOrg, ref));
    });

    it("WO-4: a ref whose hash or size is not the object's is refused", async () => {
      const bytes = randomBytes(64);
      const ref = await f.objects.put(org, bytes);
      await rejects(f.objects.get(org, { ...ref, contentHash: sha256(randomBytes(64)) }));
      await rejects(f.objects.get(org, { ...ref, sizeBytes: ref.sizeBytes + 1 }));
    });

    it('WO-5: a ref to nothing stored is refused', async () => {
      const bytes = randomBytes(64);
      await rejects(
        f.objects.get(org, { objectId: f.missingObjectId(), contentHash: sha256(bytes), sizeBytes: bytes.length }),
      );
    });

    it('WO-6: the same bytes put twice stay readable through either ref', async () => {
      const bytes = randomBytes(256);
      const first = await f.objects.put(org, bytes);
      const second = await f.objects.put(org, bytes);
      expect(second.contentHash).to.equal(first.contentHash);
      for (const ref of [first, second]) expect((await f.objects.get(org, ref)).equals(bytes)).to.equal(true);
    });

    it('WO-7: concurrent puts of distinct objects, in two orgs, each read back their own bytes', async () => {
      const writes = Array.from({ length: 10 }, (_, i) => ({ orgId: i % 2 === 0 ? org : otherOrg, bytes: randomBytes(128 + i) }));
      const refs = await Promise.all(writes.map(({ orgId, bytes }) => f.objects.put(orgId, bytes)));
      const read = await Promise.all(refs.map((ref, i) => f.objects.get(writes[i]!.orgId, ref)));
      read.forEach((bytes, i) => expect(bytes.equals(writes[i]!.bytes), String(i)).to.equal(true));
    });

    it('WO-8: bytes changed in the store after the put are refused, not returned', async () => {
      const bytes = randomBytes(128);
      const ref = await f.objects.put(org, bytes);
      await f.tamper(org, ref, randomBytes(128));
      await rejects(f.objects.get(org, ref));
    });
  });
}
