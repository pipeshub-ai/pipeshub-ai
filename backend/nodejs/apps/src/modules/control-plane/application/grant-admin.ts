import { canonicalDigest } from '../../../libs/utils/canonical-json';
import { changedPaths, orgScope } from '../domain/config-change';
import { CpProblemError, CpRefusal } from '../domain/errors';
import { isProblem } from '../domain/problem';
import type {
  EffectiveItem,
  Grant,
  GrantEntry,
  SubjectRef,
} from '../domain/objects';
import type { PinSubject } from '../domain/resolution';
import type { GrantStore } from '../ports/grant-store';
import type { InstallationStore } from '../ports/installation-store';
import type { RunConfigPinner } from '../ports/run-config-pinner';
import type { SnapshotSource } from '../ports/snapshot-source';
import type {
  ControlPlaneTransaction,
  WriteContext,
} from '../ports/transaction';

/** The pinner and its snapshots, bound where `CONTROL_PLANE_PIN` is not `off` (the builder's grantable list). */
export interface AdminPinServices {
  readonly pinner: RunConfigPinner;
  readonly snapshots: SnapshotSource;
}

const pinSubject = (subject: {
  orgId: string;
  actorId: string;
  agentKey: string | null;
  chatMode: string;
}): PinSubject => ({
  ...subject,
  projectId: null,
  surface: 'web',
  request: { schemaVersion: 1, scope: { kind: 'request' } },
});

/** One grantable installation for the agent's author, with its state and the first reason it is not ready. */
export interface GrantableItem {
  readonly installation: string;
  readonly item: string;
  readonly kind: string;
  readonly granted: boolean;
  readonly state: EffectiveItem['state'];
  readonly reason: EffectiveItem['reason'] | null;
}

/** Every entry after was granted before, with no tool added: running work may be narrowed at once. */
export function narrows(
  before: readonly GrantEntry[],
  after: readonly GrantEntry[],
): boolean {
  const was = new Map(before.map((entry) => [entry.installation, entry.tools]));
  return after.every((entry) => {
    if (!was.has(entry.installation)) return false;
    const tools = was.get(entry.installation) ?? null;
    return (
      tools === null ||
      (entry.tools !== null &&
        entry.tools.every((tool) => tools.includes(tool)))
    );
  });
}

export interface GrantAdminDeps {
  readonly grants: GrantStore;
  readonly installations: InstallationStore;
  readonly transaction: ControlPlaneTransaction;
}

/** Grants as the admin API reads and replaces them (R8, R9) and the builder's grantable list (R22). */
export class GrantAdmin {
  constructor(private readonly deps: GrantAdminDeps) {}

  /** Revision 0 when the subject has no grant yet. */
  async get(orgId: string, subject: SubjectRef): Promise<Grant> {
    return (
      (await this.deps.grants.get(orgId, subject)) ?? {
        schemaVersion: 1,
        orgId,
        subject,
        entries: [],
        revision: 0,
      }
    );
  }

  async put(
    ctx: WriteContext,
    subject: SubjectRef,
    expected: number,
    entries: readonly GrantEntry[],
  ): Promise<Grant> {
    const committed = await this.deps.transaction.run(ctx, async (tx) => {
      const live = new Set(
        (await this.deps.installations.listForOrg(ctx.orgId, {}))
          .filter((inst) => inst.status !== 'removed')
          .map((inst) => inst.id),
      );
      const unknown = entries.find((entry) => !live.has(entry.installation));
      if (unknown !== undefined) {
        throw new CpRefusal(
          'CP_UNKNOWN_REFERENCE',
          'The grant names an installation this org does not have',
          { subject: { installation: unknown.installation } },
        );
      }
      const before = await this.get(ctx.orgId, subject);
      const next: Grant = {
        schemaVersion: 1,
        orgId: ctx.orgId,
        subject,
        entries: [...entries].sort((a, b) =>
          a.installation < b.installation
            ? -1
            : a.installation > b.installation
              ? 1
              : 0,
        ),
        revision: expected,
      };
      const revision = await this.deps.grants.put(tx, next, expected);
      const stored = { ...next, revision };
      return {
        result: stored,
        change: {
          scope: orgScope(ctx.orgId),
          object: { type: 'grant', id: subject },
          revisionFrom: expected === 0 ? null : expected,
          revisionTo: revision,
          changeDigest: canonicalDigest(stored),
          beforeDigest: expected === 0 ? null : canonicalDigest(before),
          changed: changedPaths(before.revision === 0 ? null : before, stored),
          narrowing: narrows(before.entries, stored.entries),
        },
      };
    });
    return committed.result;
  }

  /**
   * Every installation the author could grant the agent, each with the state a
   * turn of the author's would see if it were granted: the agent's grant is
   * replaced by one naming every installation and pinned as a dry run (one
   * snapshot load, the same rules admission applies).
   */
  async grantable(
    pin: AdminPinServices,
    orgId: string,
    actorId: string,
    agentKey: string,
  ): Promise<GrantableItem[]> {
    const snapshot = await pin.snapshots.snapshot(orgId, agentKey, actorId);
    const subject: SubjectRef = `agent:${agentKey}`;
    const granted = new Set(
      (snapshot.grants.get(subject)?.entries ?? []).map((e) => e.installation),
    );
    const live = [...snapshot.installations.values()].filter(
      (inst) => inst.status !== 'removed',
    );
    const everything: Grant = {
      schemaVersion: 1,
      orgId,
      subject,
      entries: live.map((inst) => ({
        installation: inst.id,
        tools: null,
        required: false,
      })),
      revision: 0,
    };
    const grants = new Map(snapshot.grants);
    grants.set(subject, everything);
    const pinned = await pin.pinner.pin(
      pinSubject({ orgId, actorId, agentKey, chatMode: 'auto' }),
      { ...snapshot, grants },
    );
    if (isProblem(pinned)) throw new CpProblemError(pinned);
    const states = new Map(
      pinned.effectiveSet.items.map((item) => [item.installation, item]),
    );
    return live
      .map((inst): GrantableItem => {
        const state = states.get(inst.id);
        return {
          installation: inst.id,
          item: inst.item,
          kind: inst.kind,
          granted: granted.has(inst.id),
          state: state?.state ?? 'unavailable',
          reason: state?.reason ?? null,
        };
      })
      .sort((a, b) => (a.installation < b.installation ? -1 : 1));
  }
}
