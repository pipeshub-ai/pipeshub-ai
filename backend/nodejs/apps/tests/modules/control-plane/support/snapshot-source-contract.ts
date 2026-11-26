/**
 * SS: what every `SnapshotSource` does (03-interfaces §4.5, 02-lld §8.1); CPD-5
 * runs it on `ResolvedSnapshotCache`.
 */
import { expect } from 'chai';
import type { ChangeEvent } from '../../../../src/modules/control-plane/contracts/generated';
import type { Snapshot } from '../../../../src/modules/control-plane/domain/resolution';
import { touchKey } from '../../../../src/modules/control-plane/domain/revisions';
import type { SnapshotSource } from '../../../../src/modules/control-plane/ports/snapshot-source';
import { ORG_A, ORG_B } from './cp-objects';
import { aLayer, aSnapshot } from './resolution-fixtures';

export type SnapshotLoader = (orgId: string, agentKey: string | null, actorId: string) => Promise<Snapshot>;

/** The stores behind the cache: what a load sees, and how many loads ran. */
class Backing {
  head = new Map<string, number>([
    [ORG_A, 10],
    [ORG_B, 10],
  ]);
  /** Bumped when the test changes an agent's data. */
  readonly version = new Map<string, number>();
  loads = 0;

  readonly load: SnapshotLoader = async (orgId, agentKey, actorId) => {
    this.loads += 1;
    return {
      ...aSnapshot({
        headRevision: this.head.get(orgId) ?? 0,
        agentKey,
        actorId,
        dependsOn: [touchKey('grant', `agent:${agentKey}`), touchKey('installation', '*')],
        layers: { [`agent:${agentKey}`]: aLayer('agent', { version: this.version.get(`${orgId}/${agentKey}`) ?? 0 }) },
      }),
      orgId,
    };
  };

  /** A committed write: the head moves and the event is what the notifier would deliver. */
  write(orgId: string, kind: string, id: string): ChangeEvent {
    const previous = this.head.get(orgId) ?? 0;
    this.head.set(orgId, previous + 1);
    if (kind === 'grant') this.version.set(`${orgId}/${id.slice('agent:'.length)}`, previous + 1);
    return { type: 'cp.changed', orgId, revision: previous + 1, previous, touched: [{ kind, id }], narrowing: false };
  }
}

const versionOf = (snapshot: Snapshot, agentKey: string): unknown =>
  (snapshot.layers.get(`agent:${agentKey}`) as unknown as { version: number }).version;

export function describeSnapshotSourceContract(
  name: string,
  make: (load: SnapshotLoader, bound: number) => SnapshotSource,
): void {
  describe(`SnapshotSource contract (SS): ${name}`, () => {
    let backing: Backing;
    let source: SnapshotSource;

    beforeEach(() => {
      backing = new Backing();
      source = make(backing.load, 3);
    });

    it('SS-1: a miss loads once per (org, agent, actor); a hit loads nothing', async () => {
      await source.snapshot(ORG_A, 'a1', 'u1');
      await source.snapshot(ORG_A, 'a1', 'u1');
      expect(backing.loads).to.equal(1);
      await source.snapshot(ORG_A, 'a1', 'u2');
      await source.snapshot(ORG_A, null, 'u1');
      expect(backing.loads).to.equal(3);
    });

    it('SS-2: an untouched entry advances to the event\'s revision without a load', async () => {
      await source.snapshot(ORG_A, 'a1', 'u1');
      source.onChange(backing.write(ORG_A, 'grant', 'agent:a2'));
      const advanced = await source.snapshot(ORG_A, 'a1', 'u1');
      expect(backing.loads).to.equal(1);
      expect(advanced.headRevision).to.equal(11);
    });

    it('SS-3: a touched entry reloads on its next read', async () => {
      await source.snapshot(ORG_A, 'a1', 'u1');
      source.onChange(backing.write(ORG_A, 'grant', 'agent:a1'));
      const reloaded = await source.snapshot(ORG_A, 'a1', 'u1');
      expect(backing.loads).to.equal(2);
      expect(versionOf(reloaded, 'a1')).to.equal(11);
      expect(reloaded.headRevision).to.equal(11);
    });

    it('SS-4: a gap drops every entry of that org and no other org\'s', async () => {
      await source.snapshot(ORG_A, 'a1', 'u1');
      await source.snapshot(ORG_B, 'a1', 'u1');
      backing.write(ORG_A, 'grant', 'agent:a9');
      source.onChange(backing.write(ORG_A, 'grant', 'agent:a9'));
      await source.snapshot(ORG_A, 'a1', 'u1');
      await source.snapshot(ORG_B, 'a1', 'u1');
      expect(backing.loads).to.equal(3);
      expect((await source.snapshot(ORG_A, 'a1', 'u1')).headRevision).to.equal(12);
    });

    it('SS-5: once the writer applies its own event, its next snapshot shows the write', async () => {
      expect(versionOf(await source.snapshot(ORG_A, 'a1', 'u1'), 'a1')).to.equal(0);
      const event = backing.write(ORG_A, 'grant', 'agent:a1');
      source.onChange(event);
      expect(versionOf(await source.snapshot(ORG_A, 'a1', 'u1'), 'a1')).to.equal(event.revision);
    });

    it('SS-6: at most the bound\'s entries are held; an evicted one reloads', async () => {
      for (const agent of ['a1', 'a2', 'a3', 'a4']) await source.snapshot(ORG_A, agent, 'u1');
      expect(backing.loads).to.equal(4);
      await source.snapshot(ORG_A, 'a4', 'u1');
      expect(backing.loads).to.equal(4);
      await source.snapshot(ORG_A, 'a1', 'u1');
      expect(backing.loads).to.equal(5);
    });

    it('SS-7: an event already applied is ignored', async () => {
      await source.snapshot(ORG_A, 'a1', 'u1');
      const event = backing.write(ORG_A, 'grant', 'agent:a2');
      source.onChange(event);
      source.onChange(event);
      source.onChange({ ...event, revision: 9, previous: 8, touched: [{ kind: 'grant', id: 'agent:a1' }] });
      expect((await source.snapshot(ORG_A, 'a1', 'u1')).headRevision).to.equal(11);
      expect(backing.loads).to.equal(1);
    });
  });
}
