/* eslint-disable @typescript-eslint/require-await -- in-memory adapters implement asynchronous ports without I/O. */
/**
 * In-memory adapters of the control-plane stores (03-interfaces §2.3–§2.7;
 * R3 M14: test support, not production code). Each passes the same contract
 * suite its production adapter will (CP-SKEL §3). Writes join the caller's
 * transaction through `tx.enlist`, so they land only when it commits; reads
 * count one round trip each (`queries`), the batched-read cases' probe.
 */
import { ControlPlaneUnavailableError, policyWritten } from '../../../../src/modules/control-plane';
import { checkedObject, compareAndSet } from '../../../../src/modules/control-plane/adapters/store-checks';
import {
  bindingSchema,
  grantSchema,
  installationSchema,
  MAX_GRANT_ENTRIES,
  type Binding,
  type BindingOwnerRef,
  type Grant,
  type Installation,
  type SubjectRef,
} from '../../../../src/modules/control-plane/domain/objects';
import { LAYERS } from '../../../../src/modules/control-plane/contracts/generated';
import type { PolicyLayer } from '../../../../src/modules/control-plane/domain/policy-alias';
import {
  InvalidObject,
  ObjectExists,
  ObjectNotFound,
  ObjectTooLarge,

} from '../../../../src/modules/control-plane/domain/store-errors';
import type { CapabilityLedger, LedgerEntry, UndoOutcome } from '../../../../src/modules/control-plane/ports/capability-ledger';
import type { BindingStore, PageRequest, RefreshClaimant } from '../../../../src/modules/control-plane/ports/binding-store';
import { loadCapabilityFacts } from '../../../../src/modules/control-plane/application/capability-facts';
import { NO_FAMILIES } from '../../../../src/modules/control-plane/domain/routing';
import { bindingCursor, bindingCursorParts, isLiveBinding } from '../../../../src/modules/control-plane/domain/bindings';
import type { GrantStore } from '../../../../src/modules/control-plane/ports/grant-store';
import type { SnapshotSource } from '../../../../src/modules/control-plane/ports/snapshot-source';
import type {
  InstallationFilter,
  InstallationRevision,
  InstallationStore,
} from '../../../../src/modules/control-plane/ports/installation-store';
import { writeEach } from '../../../../src/modules/control-plane/domain/policy-layer';
import type {
  LayerRead,
  LayerWrite,
  PolicyLayers,
  PolicyOrgSource,
  PolicyRevision,
  PolicyStorePurge,
  PolicyStore,
  VersionedLayer,
} from '../../../../src/modules/control-plane/ports/policy-store';
import type { Page, StoreTx } from '../../../../src/modules/control-plane/ports/store-tx';

const key = (...parts: string[]): string => parts.join('|');
const clone = <T>(value: T): T => structuredClone(value);

const checked = checkedObject;

export class InMemoryInstallationStore implements InstallationStore {
  queries = 0;
  private readonly rows = new Map<string, Installation>();
  private readonly revisions = new Map<string, Installation[]>();

  async get(orgId: string, id: string): Promise<Installation | null> {
    this.queries += 1;
    const row = this.rows.get(key(orgId, id));
    return row === undefined ? null : clone(row);
  }

  async listForOrg(orgId: string, filter: InstallationFilter): Promise<Installation[]> {
    this.queries += 1;
    return this.inOrg(orgId).filter(
      (inst) =>
        (filter.status === undefined || filter.status.includes(inst.status)) &&
        (filter.kind === undefined || inst.kind === filter.kind) &&
        (filter.owner === undefined || inst.owner === filter.owner),
    );
  }

  async create(tx: StoreTx, inst: Installation): Promise<void> {
    const row = { ...checked(tx, installationSchema, inst, `installation ${inst.id}`), revision: 1 };
    const id = key(tx.orgId, row.id);
    if (this.rows.has(id)) throw new ObjectExists(`installation ${row.id} exists`);
    await tx.enlist(async () => {
      if (this.rows.has(id)) throw new ObjectExists(`installation ${row.id} exists`);
      this.store(id, row);
    });
  }

  async replace(tx: StoreTx, inst: Installation, expectedRevision: number): Promise<number> {
    const checkedRow = checked(tx, installationSchema, inst, `installation ${inst.id}`);
    const id = key(tx.orgId, checkedRow.id);
    const revisionOf = (): number => {
      const current = this.rows.get(id);
      if (current === undefined) throw new ObjectNotFound(`installation ${checkedRow.id}`);
      return current.revision;
    };
    compareAndSet(revisionOf(), expectedRevision, `installation ${checkedRow.id}`);
    const row = { ...checkedRow, revision: expectedRevision + 1 };
    await tx.enlist(async () => {
      compareAndSet(revisionOf(), expectedRevision, `installation ${checkedRow.id}`);
      this.store(id, row);
    });
    return row.revision;
  }

  async history(orgId: string, id: string, limit: number): Promise<InstallationRevision[]> {
    this.queries += 1;
    return [...(this.revisions.get(key(orgId, id)) ?? [])]
      .reverse()
      .slice(0, limit)
      .map((installation) => ({ revision: installation.revision, installation: clone(installation) }));
  }

  async children(orgId: string, parent: string): Promise<Installation[]> {
    this.queries += 1;
    return this.inOrg(orgId).filter((inst) => inst.installedVia === parent);
  }

  /** The capability ledger's revert: the row and its history go (in-memory twin of the Mongo ledger's delete). */
  drop(orgId: string, id: string): void {
    this.rows.delete(key(orgId, id));
    this.revisions.delete(key(orgId, id));
  }

  private store(id: string, row: Installation): void {
    this.rows.set(id, clone(row));
    this.revisions.set(id, [...(this.revisions.get(id) ?? []), clone(row)]);
  }

  private inOrg(orgId: string): Installation[] {
    return [...this.rows.values()]
      .filter((inst) => inst.orgId === orgId)
      .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
      .map(clone);
  }
}

export class InMemoryGrantStore implements GrantStore {
  queries = 0;
  private readonly rows = new Map<string, Grant>();

  async get(orgId: string, subject: SubjectRef): Promise<Grant | null> {
    this.queries += 1;
    const row = this.rows.get(key(orgId, subject));
    return row === undefined ? null : clone(row);
  }

  async getMany(orgId: string, subjects: readonly SubjectRef[]): Promise<Map<SubjectRef, Grant>> {
    this.queries += 1;
    const found = new Map<SubjectRef, Grant>();
    for (const subject of subjects) {
      const row = this.rows.get(key(orgId, subject));
      if (row !== undefined) found.set(subject, clone(row));
    }
    return found;
  }

  async put(tx: StoreTx, grant: Grant, expectedRevision: number): Promise<number> {
    if (grant.entries.length > MAX_GRANT_ENTRIES) {
      throw new ObjectTooLarge(`a grant holds at most ${MAX_GRANT_ENTRIES} entries, not ${grant.entries.length}`);
    }
    const checkedRow = checked(tx, grantSchema, grant, `grant ${grant.subject}`);
    const id = key(tx.orgId, checkedRow.subject);
    const revisionOf = (): number => this.rows.get(id)?.revision ?? 0;
    compareAndSet(revisionOf(), expectedRevision, `grant ${checkedRow.subject}`);
    const row = { ...checkedRow, revision: expectedRevision + 1 };
    await tx.enlist(async () => {
      compareAndSet(revisionOf(), expectedRevision, `grant ${checkedRow.subject}`);
      this.rows.set(id, clone(row));
    });
    return row.revision;
  }

  drop(orgId: string, subject: SubjectRef): void {
    this.rows.delete(key(orgId, subject));
  }

  async usersOf(orgId: string, installation: string): Promise<SubjectRef[]> {
    this.queries += 1;
    return [...this.rows.values()]
      .filter((grant) => grant.orgId === orgId && grant.entries.some((entry) => entry.installation === installation))
      .map((grant) => grant.subject)
      .sort();
  }
}

const isLayerName = (value: unknown): boolean => (LAYERS as readonly unknown[]).includes(value);

/** Until CPD-3's generated `policy-layer` schema: an object naming a known scope kind. */
export function parsePolicyLayer(raw: unknown): PolicyLayer {
  const scope = (raw as { scope?: { kind?: unknown } } | null)?.scope;
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw) || !isLayerName(scope?.kind)) {
    throw new Error('not a policy layer');
  }
  return raw as PolicyLayer;
}

export class InMemoryPolicyStore implements PolicyStore, PolicyLayers, PolicyStorePurge {
  queries = 0;
  private readonly rows = new Map<string, { raw: unknown; revision: number }>();
  private readonly revisions = new Map<string, PolicyRevision[]>();

  constructor(private readonly parse: (raw: unknown) => PolicyLayer = parsePolicyLayer) {}

  async read(orgId: string, scope: string): Promise<VersionedLayer> {
    this.queries += 1;
    const found = this.readOne(orgId, scope);
    if (found.status === 'unavailable') {
      throw new ControlPlaneUnavailableError(`the stored policy layer of ${scope} does not parse`);
    }
    return { layer: found.layer, revision: found.revision };
  }

  async readMany(orgId: string, scopes: readonly string[]): Promise<Map<string, LayerRead>> {
    this.queries += 1;
    return new Map(scopes.map((scope) => [scope, this.readOne(orgId, scope)]));
  }

  async write(tx: StoreTx, scope: string, layer: PolicyLayer | null, expectedRevision: number): Promise<number> {
    if (layer !== null) {
      try {
        this.parse(layer);
      } catch {
        throw new InvalidObject(`the policy layer for ${scope} does not parse`);
      }
    }
    const id = key(tx.orgId, scope);
    const revisionOf = (): number => this.rows.get(id)?.revision ?? 0;
    compareAndSet(revisionOf(), expectedRevision, `policy layer ${scope}`);
    const revision = expectedRevision + 1;
    await tx.enlist(async () => {
      compareAndSet(revisionOf(), expectedRevision, `policy layer ${scope}`);
      this.rows.set(id, { raw: clone(layer), revision });
      this.revisions.set(id, [...(this.revisions.get(id) ?? []), { revision, layer: clone(layer) }]);
    });
    await policyWritten(tx, scope, layer);
    return revision;
  }

  async history(orgId: string, scope: string, limit: number): Promise<PolicyRevision[]> {
    this.queries += 1;
    return [...(this.revisions.get(key(orgId, scope)) ?? [])].reverse().slice(0, limit).map(clone);
  }

  async scopesOf(orgId: string, kind: string): Promise<string[]> {
    this.queries += 1;
    const prefix = key(orgId, '');
    return [...this.rows.entries()]
      .filter(([id, row]) => id.startsWith(prefix) && row.raw !== null)
      .map(([id]) => id.slice(prefix.length))
      .filter((scope) => scope.split(':', 1)[0] === kind)
      .sort();
  }

  async orgSource(): Promise<PolicyOrgSource> {
    return 'store';
  }

  writeMany(tx: StoreTx, writes: readonly LayerWrite[]): Promise<Map<string, number>> {
    return writeEach(this, tx, writes);
  }

  async *orgLayers(): AsyncIterable<{ orgId: string; layer: PolicyLayer }> {
    const rows = [...this.rows.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    for (const [id, row] of rows) {
      const [orgId, scope] = id.split('|');
      if (scope?.startsWith('org:') !== true || row.raw === null) continue;
      try {
        yield { orgId: orgId!, layer: clone(this.parse(row.raw)) };
      } catch {
        continue;
      }
    }
  }

  async purge(tx: StoreTx, orgId: string): Promise<void> {
    const prefix = key(orgId, '');
    await tx.enlist(async () => {
      for (const map of [this.rows, this.revisions]) {
        for (const id of [...map.keys()]) if (id.startsWith(prefix)) map.delete(id);
      }
    });
  }

  /** Fault injection: what a hand edit or an old writer left in the row. */
  corrupt(orgId: string, scope: string, raw: unknown = { scope: 'not-a-scope' }): void {
    const id = key(orgId, scope);
    this.rows.set(id, { raw, revision: (this.rows.get(id)?.revision ?? 0) + 1 });
  }

  private readOne(orgId: string, scope: string): LayerRead {
    const row = this.rows.get(key(orgId, scope));
    if (row === undefined) return { status: 'ok', layer: null, revision: 0 };
    if (row.raw === null) return { status: 'ok', layer: null, revision: row.revision };
    try {
      return { status: 'ok', layer: clone(this.parse(row.raw)), revision: row.revision };
    } catch {
      return { status: 'unavailable', code: 'CP_UNAVAILABLE' };
    }
  }
}

export class InMemoryBindingStore implements BindingStore {
  queries = 0;
  private readonly rows = new Map<string, Binding>();
  private readonly claims = new Map<string, { holder: string; until: number }>();

  async get(orgId: string, id: string): Promise<Binding | null> {
    this.queries += 1;
    const row = this.rows.get(id);
    return row === undefined || row.orgId !== orgId ? null : clone(row);
  }

  async find(orgId: string, installation: string, owner: BindingOwnerRef): Promise<Binding | null> {
    this.queries += 1;
    const rows = this.ofOwner(orgId, installation, owner);
    const row = rows.find(isLiveBinding) ?? rows.at(-1);
    return row === undefined ? null : clone(row);
  }

  async listByInstallation(orgId: string, installation: string, page: PageRequest): Promise<Page<Binding>> {
    this.queries += 1;
    const key = (row: Pick<Binding, 'owner' | 'id'>): string => `${row.owner}\u0000${row.id}`;
    const after = page.after === null ? null : key(bindingCursorParts(page.after));
    const rows = [...this.rows.values()]
      .filter((row) => row.orgId === orgId && row.installation === installation)
      .filter((row) => after === null || key(row) > after)
      .sort((a, b) => (key(a) < key(b) ? -1 : 1));
    const items = rows.slice(0, page.limit).map(clone);
    return { items, next: rows.length > page.limit ? bindingCursor(items.at(-1)!) : null };
  }

  async listForOwner(orgId: string, owner: BindingOwnerRef): Promise<Binding[]> {
    this.queries += 1;
    return [...this.rows.values()]
      .filter((row) => row.orgId === orgId && row.owner === owner)
      .sort((a, b) => (a.id < b.id ? -1 : 1))
      .map(clone);
  }

  async listForOwners(orgId: string, owners: readonly BindingOwnerRef[]): Promise<Binding[]> {
    this.queries += 1;
    const rows = [...this.rows.values()].sort((a, b) => (a.id < b.id ? -1 : 1));
    return owners.flatMap((owner) => rows.filter((row) => row.orgId === orgId && row.owner === owner).map(clone));
  }

  async upsert(tx: StoreTx, binding: Binding, expectedRevision: number | null): Promise<number> {
    const checkedRow = checked(tx, bindingSchema, binding, `binding ${binding.id}`);
    const taken = (): ObjectExists => new ObjectExists(`a binding of ${checkedRow.installation} for ${checkedRow.owner} exists`);
    const precondition = (): void => {
      const current = this.rows.get(checkedRow.id);
      if (expectedRevision === null) {
        if (current !== undefined) throw taken();
      } else {
        if (current === undefined || current.orgId !== tx.orgId) throw new ObjectNotFound(`binding ${checkedRow.id}`);
        compareAndSet(current.revision, expectedRevision, `binding ${checkedRow.id}`);
      }
      if (isLiveBinding(checkedRow) && this.liveOther(tx.orgId, checkedRow) !== undefined) throw taken();
    };
    precondition();
    const row = { ...checkedRow, revision: (expectedRevision ?? 0) + 1 };
    await tx.enlist(async () => {
      precondition();
      this.rows.set(row.id, clone(row));
      this.claims.delete(row.id);
    });
    return row.revision;
  }

  async remove(tx: StoreTx, id: string, expectedRevision: number): Promise<void> {
    const precondition = (): void => {
      const current = this.rows.get(id);
      if (current === undefined || current.orgId !== tx.orgId) throw new ObjectNotFound(`binding ${id}`);
      compareAndSet(current.revision, expectedRevision, `binding ${id}`);
    };
    precondition();
    await tx.enlist(async () => {
      precondition();
      this.rows.delete(id);
    });
  }

  async dueForRefresh(before: number, limit: number, claimable?: RefreshClaimant): Promise<Binding[]> {
    this.queries += 1;
    return [...this.rows.values()]
      .filter(
        (row) =>
          (row.status === 'active' || row.status === 'expiring') && row.expiresAt !== null && row.expiresAt < before,
      )
      .filter((row) => claimable === undefined || this.claimable(row.id, claimable))
      .sort((a, b) => (a.expiresAt ?? 0) - (b.expiresAt ?? 0) || (a.id < b.id ? -1 : 1))
      .slice(0, limit)
      .map(clone);
  }

  async claimRefresh(
    orgId: string,
    id: string,
    revision: number,
    claim: RefreshClaimant & { readonly until: number },
  ): Promise<boolean> {
    this.queries += 1;
    const row = this.rows.get(id);
    if (row === undefined || row.orgId !== orgId || row.revision !== revision || !this.claimable(id, claim)) return false;
    this.claims.set(id, { holder: claim.holder, until: claim.until });
    return true;
  }

  private claimable(id: string, { holder, now }: RefreshClaimant): boolean {
    const held = this.claims.get(id);
    return held === undefined || held.until <= now || held.holder === holder;
  }

  /** The owner's bindings of the installation, oldest first. */
  private ofOwner(orgId: string, installation: string, owner: string): Binding[] {
    return [...this.rows.values()]
      .filter((row) => row.orgId === orgId && row.installation === installation && row.owner === owner)
      .sort((a, b) => (a.id < b.id ? -1 : 1));
  }

  /** Another binding of the same owner that is not revoked. */
  private liveOther(orgId: string, binding: Binding): Binding | undefined {
    return this.ofOwner(orgId, binding.installation, binding.owner).find((row) => row.id !== binding.id && isLiveBinding(row));
  }
}

/**
 * The capability migration's ledger over the in-memory stores: entries join
 * the caller's transaction like every other write; `undo` drops a row only
 * while it is at the entry's revision.
 */
export class InMemoryCapabilityLedger implements CapabilityLedger {
  /** Point reads of the org's migrated row, for the cases that count what a snapshot load costs. */
  markerReads = 0;
  private readonly rows = new Map<string, LedgerEntry>();
  private readonly marked = new Set<string>();

  constructor(
    private readonly installations: InMemoryInstallationStore,
    private readonly grants: InMemoryGrantStore,
  ) {}

  async entries(orgId: string): Promise<LedgerEntry[]> {
    return [...this.rows.entries()]
      .filter(([id]) => id.startsWith(`${orgId}|`))
      .map(([, entry]) => clone(entry))
      .sort((a, b) => (a.object + a.id < b.object + b.id ? -1 : 1));
  }

  async record(tx: StoreTx, entry: LedgerEntry): Promise<void> {
    await tx.enlist(async () => {
      this.rows.set(key(tx.orgId, entry.object, entry.id), clone(entry));
    });
  }

  async undo(tx: StoreTx, entry: LedgerEntry): Promise<UndoOutcome> {
    const current =
      entry.object === 'installation'
        ? (await this.installations.get(tx.orgId, entry.id))?.revision
        : (await this.grants.get(tx.orgId, entry.id as SubjectRef))?.revision;
    if (current !== undefined && current !== entry.revision) return 'drifted';
    await tx.enlist(async () => {
      if (entry.object === 'installation') this.installations.drop(tx.orgId, entry.id);
      else this.grants.drop(tx.orgId, entry.id as SubjectRef);
      this.rows.delete(key(tx.orgId, entry.object, entry.id));
    });
    return current === undefined ? 'absent' : 'reverted';
  }

  async migrated(orgId: string): Promise<boolean> {
    this.markerReads += 1;
    return this.marked.has(orgId);
  }

  async markMigrated(tx: StoreTx): Promise<void> {
    await tx.enlist(async () => {
      this.marked.add(tx.orgId);
    });
  }

  async unmarkMigrated(tx: StoreTx): Promise<void> {
    await tx.enlist(async () => {
      this.marked.delete(tx.orgId);
    });
  }
}

/** Snapshots as the pinner's loader builds them over these stores (uncached: each read sees the stores as they are). */
export function snapshotsOver(
  installations: Pick<InstallationStore, 'listForOrg'>,
  grants: Pick<GrantStore, 'getMany'>,
): Pick<SnapshotSource, 'snapshot'> {
  return {
    snapshot: async (orgId, agentKey, actorId) => ({
      orgId,
      headRevision: 1,
      deploymentRevision: 0,
      models: [],
      families: NO_FAMILIES,
      agentKey,
      actorId,
      ...(await loadCapabilityFacts({ installations, grants }, { orgId, agentKey, actorId, actorTeams: [] })),
      layers: new Map(),
      actorTeams: [],
      agentOwnerTeam: null,
      dependsOn: new Set<string>(),
    }),
  };
}
