/**
 * HR: what every `HeadRevisions` does (CPD-5): an org that never wrote is at
 * 0; each committed write moves its own org's head by one and no other org's.
 * CPD-6: the deployment's revision is read with the org's head, moves for
 * every org at once and moves no org's head.
 */
import { randomBytes } from 'crypto';
import { expect } from 'chai';
import type {
  DeploymentRevision,
  HeadRevisions,
  Heads,
} from '../../../../src/modules/control-plane/ports/head-revisions';
import type { CpTx } from '../../../../src/modules/control-plane/ports/transaction';

/** A fresh org id in the shape every store takes (24 lowercase hex). */
const anOrg = (): string => randomBytes(12).toString('hex');

export interface HeadRevisionsWorld {
  readonly heads: HeadRevisions;
  /** Commits one control-plane write of the org (its head row moves by one). */
  commit(orgId: string): Promise<void>;
  /** Commits one write of a document every org reads (the deployment's revision moves by one). */
  moveDeployment(): Promise<void>;
}

/** A transaction that commits what is enlisted at once: all a `DeploymentRevision` asks of one. */
export const committingTx = (orgId: string): CpTx => ({
  orgId,
  headRevision: 1,
  session: undefined,
  enlist: (write) => write(),
  external: () => {
    throw new Error('not used');
  },
});

export function describeHeadRevisionsContract(name: string, make: () => Promise<HeadRevisionsWorld>): void {
  describe(`HeadRevisions contract (HR): ${name}`, () => {
    it('HR-1: an org that never wrote is at revision 0', async () => {
      const world = await make();
      expect(await world.heads.head(anOrg())).to.equal(0);
    });

    it('HR-2: each committed write moves its own org by one, never another org', async () => {
      const world = await make();
      const [a, b] = [anOrg(), anOrg()];
      await world.commit(a);
      await world.commit(a);
      await world.commit(b);
      expect(await world.heads.head(a)).to.equal(2);
      expect(await world.heads.head(b)).to.equal(1);
    });

    it('HR-3 (CPD-6): the deployment revision is read with the org head, moves for every org and moves no org head', async () => {
      const world = await make();
      const [a, b] = [anOrg(), anOrg()];
      await world.commit(a);
      const before = await world.heads.heads(a);
      expect(before.org).to.equal(1);
      await world.moveDeployment();
      expect(await world.heads.heads(a)).to.deep.equal({ org: 1, deployment: before.deployment + 1 });
      expect(await world.heads.heads(b)).to.deep.equal({ org: 0, deployment: before.deployment + 1 });
    });
  });
}

/** Heads held in memory: the in-memory control-plane transaction's org counter. */
export class InMemoryHeadRevisions implements HeadRevisions, DeploymentRevision {
  readonly revisions = new Map<string, number>();
  deployment = 0;
  unreadable = false;
  /** How many times the heads were read (one read serves the org's and the deployment's). */
  reads = 0;

  async heads(orgId: string): Promise<Heads> {
    this.reads += 1;
    return { org: await this.head(orgId), deployment: this.deployment };
  }

  move(tx: CpTx): Promise<void> {
    return tx.enlist(async () => {
      this.deployment += 1;
    });
  }

  async head(orgId: string): Promise<number> {
    if (this.unreadable) throw new Error('heads unreadable');
    return this.revisions.get(orgId) ?? 0;
  }

  /** Both revisions as they are now, without counting a read: what a store's transaction sees. */
  current(orgId: string): Heads {
    return { org: this.revisions.get(orgId) ?? 0, deployment: this.deployment };
  }

  commit(orgId: string): number {
    const next = (this.revisions.get(orgId) ?? 0) + 1;
    this.revisions.set(orgId, next);
    return next;
  }
}
