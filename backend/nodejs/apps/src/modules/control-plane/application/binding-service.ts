import { bindingView, type BindingView } from '../domain/bindings';
import { CpRefusal } from '../domain/errors';
import type { Binding, BindingOwnerRef, Installation } from '../domain/objects';
import { ObjectNotFound } from '../domain/store-errors';
import type { BindingStore } from '../ports/binding-store';
import type { SecretMaterial } from '../ports/credential-binder';
import type { WriteContext } from '../ports/transaction';
import type {
  Imported,
  ImportRequest,
  RevertedImport,
  VaultCredentialBinder,
} from './vault-credential-binder';

/** What the token refresher needs of a due binding: never its secret. */
export interface DueBinding {
  readonly id: string;
  readonly orgId: string;
  readonly installation: string;
  readonly owner: BindingOwnerRef;
  readonly mode: Binding['mode'];
  readonly revision: number;
  readonly expiresAt: number | null;
  readonly path: string | null;
  /** False when another refresher replica holds it (an on-demand read): renewing it now would spend its refresh token twice. */
  readonly claimed: boolean;
}

const dueOf = (b: Binding, claimed = true): DueBinding => ({
  id: b.id,
  orgId: b.orgId,
  installation: b.installation,
  owner: b.owner,
  mode: b.mode,
  revision: b.revision,
  expiresAt: b.expiresAt,
  path: b.secret?.path ?? null,
  claimed,
});

/** The owner prefix a binding of each credential mode must have; null where nothing is connected. */
const OWNER_OF_MODE: Readonly<
  Record<Installation['credentialMode'], string | null>
> = { per_user: 'user:', agent: 'agent:', org: 'org', none: null };

/** How many of an installation's bindings one read takes while revoking them. */
const REVOKE_PAGE = 100;

/** The most bindings one refresher cycle takes. */
export const MAX_DUE_PAGE = 200;

/**
 * How long a refresher replica holds a binding it took (QA23-F2): longer
 * than its cycle (5 min), so a replica whose write-back failed takes its own
 * binding again and stores the tokens it holds; a replica that died frees it
 * after this.
 */
export const REFRESH_CLAIM_MS = 6 * 60_000;

/**
 * Bindings as the routes see them: "My connections" (list, revoke) for
 * users and admins, and the maintenance writes of the migration, the legacy
 * routes and the token refresher. Reads come from the store; every write is
 * the binder's (one audited control-plane write each).
 */
export class BindingService {
  constructor(
    private readonly bindings: BindingStore,
    private readonly binder: VaultCredentialBinder,
    private readonly now: () => number = Date.now,
  ) {}

  async list(orgId: string, owner: BindingOwnerRef): Promise<BindingView[]> {
    return (await this.bindings.listForOwner(orgId, owner)).map(bindingView);
  }

  /** The binding when `canSee` admits it; otherwise not found, so another user's binding is never confirmed. */
  async revoke(
    ctx: WriteContext,
    id: string,
    expectedRevision: number,
    canSee: (binding: Binding) => Promise<boolean>,
  ): Promise<BindingView> {
    const current = await this.bindings.get(ctx.orgId, id);
    if (current === null || !(await canSee(current))) {
      throw new ObjectNotFound(`binding ${id}`);
    }
    return bindingView(await this.binder.revoke(ctx, id, expectedRevision));
  }

  /**
   * A connection made with a secret the caller types (API key or header,
   * CPD-11 R15): the owner must be one the installation's credential mode
   * takes. OAuth connects on the installation's own page (no production
   * OAuth start here yet).
   */
  async create(
    ctx: WriteContext,
    inst: Installation,
    owner: BindingOwnerRef,
    mode: 'api_key' | 'header',
    material: SecretMaterial,
  ): Promise<BindingView> {
    const expected = OWNER_OF_MODE[inst.credentialMode];
    if (expected === null || !owner.startsWith(expected)) {
      throw new CpRefusal(
        'CP_INVALID_CONFIG',
        expected === null
          ? 'This installation takes no connection'
          : `This installation is connected by ${expected === 'user:' ? 'each user' : expected.replace(':', '')}`,
        { subject: { installation: inst.id } },
      );
    }
    const started = await this.binder.begin(ctx, inst, owner, mode, material);
    return bindingView(started.binding);
  }

  /** A new secret for a binding the caller may see, at the revision they read. */
  async rotate(
    ctx: WriteContext,
    id: string,
    expectedRevision: number,
    material: SecretMaterial,
    canSee: (binding: Binding) => Promise<boolean>,
  ): Promise<BindingView> {
    const current = await this.bindings.get(ctx.orgId, id);
    if (current === null || !(await canSee(current))) {
      throw new ObjectNotFound(`binding ${id}`);
    }
    if (current.mode !== 'api_key' && current.mode !== 'header') {
      throw new CpRefusal(
        'CP_INVALID_CONFIG',
        'Reconnect this account on its own page to renew it',
        { subject: { binding: id } },
      );
    }
    return bindingView(
      await this.binder.setSecret(ctx, id, material, expectedRevision),
    );
  }

  importRecord(ctx: WriteContext, request: ImportRequest): Promise<Imported> {
    return this.binder.importRecord(ctx, request);
  }

  revertImport(
    ctx: WriteContext,
    installation: string,
    owner: BindingOwnerRef,
  ): Promise<RevertedImport | null> {
    return this.binder.revertImport(ctx, installation, owner);
  }

  revokeOwner(
    ctx: WriteContext,
    installation: string,
    owner: BindingOwnerRef,
  ): Promise<Binding | null> {
    return this.binder.revokeOwner(ctx, installation, owner);
  }

  /**
   * Revokes every binding made on the installation that is not revoked yet
   * (its uninstall): each is the binder's own audited write and tombstones
   * its secret, so a run that pinned it is refused at its next lease. Safe
   * to run again after a failure part-way: what is revoked stays revoked.
   */
  async revokeInstallation(
    ctx: WriteContext,
    installation: string,
  ): Promise<Binding[]> {
    const revoked: Binding[] = [];
    let after: string | null = null;
    do {
      const page = await this.bindings.listByInstallation(
        ctx.orgId,
        installation,
        { after, limit: REVOKE_PAGE },
      );
      for (const binding of page.items) {
        if (binding.status === 'revoked') continue;
        revoked.push(
          await this.binder.revoke(ctx, binding.id, binding.revision),
        );
      }
      after = page.next;
    } while (after !== null);
    return revoked;
  }

  /** The owner's binding of an installation as the refresher `holder` sees it (an on-demand refresh), claimed when nobody else holds it; null when none. */
  async findForRefresh(
    orgId: string,
    installation: string,
    owner: BindingOwnerRef,
    holder: string,
  ): Promise<DueBinding | null> {
    const found = await this.bindings.find(orgId, installation, owner);
    if (found === null) return null;
    return dueOf(found, await this.claim(found, holder));
  }

  /** The bindings due before `before` that the refresher `holder` now holds, soonest first. */
  async due(
    before: number,
    limit: number,
    holder: string,
  ): Promise<DueBinding[]> {
    const due = await this.bindings.dueForRefresh(
      before,
      Math.min(limit, MAX_DUE_PAGE),
      { holder, now: this.now() },
    );
    const claimed = await Promise.all(due.map((b) => this.claim(b, holder)));
    return due.filter((_, i) => claimed[i]).map((b) => dueOf(b));
  }

  private claim(binding: Binding, holder: string): Promise<boolean> {
    const now = this.now();
    return this.bindings.claimRefresh(
      binding.orgId,
      binding.id,
      binding.revision,
      { holder, now, until: now + REFRESH_CLAIM_MS },
    );
  }

  refreshed(
    ctx: WriteContext,
    id: string,
    expectedRevision: number,
    material: SecretMaterial,
  ): Promise<Binding> {
    return this.binder.refreshed(ctx, id, expectedRevision, material);
  }

  refreshFailed(
    ctx: WriteContext,
    id: string,
    expectedRevision: number,
    code: string,
    permanent: boolean,
  ): Promise<Binding> {
    return this.binder.refreshFailed(
      ctx,
      id,
      expectedRevision,
      code,
      permanent,
    );
  }
}
