import { canonicalDigest } from '../../../libs/utils/canonical-json';
import type { CatalogItem } from '../domain/catalog-item';
import { changedPaths, orgScope } from '../domain/config-change';
import { itemDescriptorSetInput } from '../domain/digests';
import type {
  Installation,
  InstallationOwnerRef,
  InstallationStatus,
  SubjectRef,
} from '../domain/objects';
import type { CatalogStore, Page } from '../ports/catalog-store';
import type { ConfigValidator } from '../ports/extension-points';
import type { GrantStore } from '../ports/grant-store';
import type { InstallationStore } from '../ports/installation-store';
import type {
  ControlPlaneTransaction,
  WriteContext,
} from '../ports/transaction';
import { CpRefusal, notFound } from '../domain/errors';

export type Visibility = Installation['visibility'];

/** Who may see an installation, by its owner (02-lld §2.2: never wider than the owner scope allows). */
const VISIBILITIES: Readonly<
  Record<'org' | 'team' | 'user', readonly Visibility[]>
> = {
  org: ['org'],
  team: ['team', 'org'],
  user: ['owner'],
};

const ownerKind = (owner: InstallationOwnerRef): 'org' | 'team' | 'user' =>
  owner === 'org' ? 'org' : owner.startsWith('team:') ? 'team' : 'user';

export function allowedVisibility(
  owner: InstallationOwnerRef,
  visibility: Visibility,
): boolean {
  return VISIBILITIES[ownerKind(owner)].includes(visibility);
}

/** What a member sees (an admin sees all): org-wide, their own, and their teams' team-visible items. */
export function visibleTo(
  inst: Installation,
  userId: string,
  teams: readonly string[],
): boolean {
  if (inst.visibility === 'org') return true;
  if (inst.owner === `user:${userId}`) return true;
  return (
    inst.visibility === 'team' &&
    teams.some((team) => inst.owner === `team:${team}`)
  );
}

/** A requirement's owners decide whose account an installation uses; none is `none`. */
function credentialModeOf(item: CatalogItem): Installation['credentialMode'] {
  const owners = item.credentialRequirements[0]?.owners;
  if (owners === undefined) return 'none';
  if (owners.includes('user')) return 'per_user';
  return owners.includes('org') ? 'org' : 'agent';
}

export interface InstallationFilterQuery {
  readonly kind?: string;
  readonly status?: InstallationStatus;
  readonly owner?: InstallationOwnerRef;
  readonly cursor?: string;
  readonly limit: number;
}

export interface InstallRequest {
  readonly item: string;
  readonly owner: InstallationOwnerRef;
  readonly visibility: Visibility;
  readonly config: Record<string, unknown>;
}

export interface InstallationAdminDeps {
  readonly catalog: CatalogStore;
  readonly installations: InstallationStore;
  readonly grants: GrantStore;
  readonly transaction: ControlPlaneTransaction;
  readonly validator: (kind: string) => ConfigValidator | null;
  readonly newId: () => string;
  readonly now: () => number;
}

/**
 * Installations as the admin API changes them (CPD-11 R3–R7): install from a
 * catalog item, visibility, enable and disable. Each write is one audited
 * control-plane write with the value's digest before and after; revisions
 * are compared by the store (`If-Match`).
 */
export class InstallationAdmin {
  constructor(private readonly deps: InstallationAdminDeps) {}

  /** Ordered by id; `cursor` is the last id of the previous page. */
  async list(
    orgId: string,
    query: InstallationFilterQuery,
    visible: (inst: Installation) => boolean,
  ): Promise<Page<Installation>> {
    const all = await this.deps.installations.listForOrg(orgId, {
      ...(query.kind !== undefined ? { kind: query.kind } : {}),
      ...(query.owner !== undefined ? { owner: query.owner } : {}),
      ...(query.status !== undefined ? { status: [query.status] } : {}),
    });
    const after = query.cursor;
    const rows = all.filter(
      (inst) =>
        inst.status !== 'removed' &&
        (after === undefined || inst.id > after) &&
        visible(inst),
    );
    const items = rows.slice(0, query.limit);
    return {
      items,
      next:
        rows.length > query.limit
          ? (items[items.length - 1]?.id ?? null)
          : null,
    };
  }

  async get(orgId: string, id: string): Promise<Installation> {
    const inst = await this.deps.installations.get(orgId, id);
    if (inst === null || inst.status === 'removed')
      throw notFound('installation');
    return inst;
  }

  /** The subjects whose grant names the installation ("used by N agents"). */
  usedBy(orgId: string, id: string): Promise<SubjectRef[]> {
    return this.deps.grants.usersOf(orgId, id);
  }

  /**
   * An installation of a catalog item, enabled with the item's descriptors
   * approved by the installer (D4, approvals off). Only kinds whose module
   * validates configuration are installed here; the others are installed on
   * their own page, which runs their drivers.
   */
  async install(
    ctx: WriteContext,
    request: InstallRequest,
  ): Promise<Installation> {
    const item = await this.deps.catalog.get(ctx.orgId, request.item);
    if (item === null || item.deprecated) throw notFound('catalog item');
    const validator = this.deps.validator(item.kind);
    if (validator === null) {
      throw new CpRefusal(
        'CP_INVALID_CONFIG',
        `A ${item.kind} is installed on its own page`,
        { subject: { item: item.ref } },
      );
    }
    if (!allowedVisibility(request.owner, request.visibility)) {
      throw new CpRefusal(
        'CP_INVALID_CONFIG',
        `An installation owned by ${ownerKind(request.owner)} cannot be visible to ${request.visibility}`,
      );
    }
    const problems = await validator.validate(item, request.config, {
      orgId: ctx.orgId,
      actor: ctx.actor,
    });
    if (problems.length > 0) {
      const first = problems[0];
      throw new CpRefusal(
        'CP_INVALID_CONFIG',
        first?.message ?? 'invalid configuration',
        {
          ...(first?.dimension != null ? { dimension: first.dimension } : {}),
        },
      );
    }
    const now = this.deps.now();
    const tools = itemDescriptorSetInput(item);
    const inst: Installation = {
      schemaVersion: 1,
      id: this.deps.newId(),
      orgId: ctx.orgId,
      item: item.ref,
      kind: item.kind,
      owner: request.owner,
      visibility: request.visibility,
      config: request.config,
      approved: {
        descriptorSetDigest: canonicalDigest(tools),
        tools,
        approvalRef: null,
        at: now,
        by: ctx.actor,
      },
      pending: null,
      status: 'enabled',
      installedVia: null,
      credentialMode: credentialModeOf(item),
      revision: 1,
      updatedAt: now,
      updatedBy: ctx.actor,
    };
    const committed = await this.deps.transaction.run(ctx, async (tx) => {
      await this.deps.installations.create(tx, inst);
      return {
        result: inst,
        change: {
          scope: orgScope(ctx.orgId),
          object: { type: 'installation', id: inst.id },
          revisionFrom: null,
          revisionTo: 1,
          changeDigest: canonicalDigest(inst),
          beforeDigest: null,
          changed: changedPaths(null, inst),
          narrowing: false,
        },
      };
    });
    return committed.result;
  }

  setVisibility(
    ctx: WriteContext,
    id: string,
    expected: number,
    visibility: Visibility,
  ): Promise<Installation> {
    return this.change(ctx, id, expected, (inst) => {
      if (!allowedVisibility(inst.owner, visibility)) {
        throw new CpRefusal(
          'CP_INVALID_CONFIG',
          `An installation owned by ${ownerKind(inst.owner)} cannot be visible to ${visibility}`,
        );
      }
      // Narrower when fewer people see it: org → team.
      return { next: { ...inst, visibility }, narrowing: visibility !== 'org' };
    });
  }

  setEnabled(
    ctx: WriteContext,
    id: string,
    expected: number,
    enabled: boolean,
  ): Promise<Installation> {
    return this.change(ctx, id, expected, (inst) => {
      if (enabled && inst.approved === null) {
        throw new CpRefusal(
          'CP_NOT_APPROVED',
          'Nothing of this installation is approved yet; approve it first',
        );
      }
      return {
        next: { ...inst, status: enabled ? 'enabled' : 'disabled' },
        narrowing: !enabled,
      };
    });
  }

  /**
   * The installation is removed (its owner was deleted, CLEANUP-G2c): no run
   * may use it again. Its bindings are the binding service's to revoke.
   */
  remove(
    ctx: WriteContext,
    id: string,
    expected: number,
  ): Promise<Installation> {
    return this.change(ctx, id, expected, (inst) => ({
      next: { ...inst, status: 'removed' },
      narrowing: true,
    }));
  }

  /** One audited replace at the revision the editor read. */
  private async change(
    ctx: WriteContext,
    id: string,
    expected: number,
    plan: (inst: Installation) => { next: Installation; narrowing: boolean },
  ): Promise<Installation> {
    const committed = await this.deps.transaction.run(ctx, async (tx) => {
      const before = await this.get(ctx.orgId, id);
      const { next, narrowing } = plan(before);
      const stamped = {
        ...next,
        updatedAt: this.deps.now(),
        updatedBy: ctx.actor,
      };
      const revision = await this.deps.installations.replace(
        tx,
        stamped,
        expected,
      );
      const stored = { ...stamped, revision };
      return {
        result: stored,
        change: {
          scope: orgScope(ctx.orgId),
          object: { type: 'installation', id },
          revisionFrom: expected,
          revisionTo: revision,
          changeDigest: canonicalDigest(stored),
          beforeDigest: canonicalDigest(before),
          changed: changedPaths(before, stored),
          narrowing,
        },
      };
    });
    return committed.result;
  }
}
