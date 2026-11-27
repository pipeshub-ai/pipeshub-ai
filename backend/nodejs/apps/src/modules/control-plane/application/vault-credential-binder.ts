import { canonicalDigest } from '../../../libs/utils/canonical-json';
import {
  BindingNotImported,
  OAuthStartUnsupported,
} from '../domain/binding-errors';
import {
  bindingVaultPath,
  bindingVersionDigestInput,
} from '../domain/bindings';
import { changedPaths, orgScope } from '../domain/config-change';
import type {
  Binding,
  BindingMode,
  BindingOwnerRef,
  Installation,
} from '../domain/objects';
import { ObjectNotFound, UnknownOAuthState } from '../domain/store-errors';
import type { BindingStore } from '../ports/binding-store';
import type {
  BindingSource,
  BindingVault,
  BindingVaultMeta,
  OAuthStart,
  OAuthStateStore,
} from '../ports/binding-vault';
import type {
  BindingStart,
  CredentialBinder,
  SecretMaterial,
} from '../ports/credential-binder';
import type {
  ControlPlaneTransaction,
  CpTx,
  WriteContext,
} from '../ports/transaction';

export interface VaultCredentialBinderDeps {
  readonly transaction: ControlPlaneTransaction;
  readonly bindings: BindingStore;
  readonly vault: BindingVault;
  readonly states: OAuthStateStore;
  /** One per installation kind; a kind without one cannot start an OAuth flow. */
  readonly oauthStarts: readonly OAuthStart[];
  /** A ULID. */
  readonly newId: () => string;
  readonly now: () => number;
}

/** A credential record copied from an old path, or saved by a legacy route with the switch on. */
export interface ImportRequest {
  readonly installation: string;
  readonly owner: BindingOwnerRef;
  readonly mode: BindingMode;
  readonly kind: string | null;
  readonly provider: string | null;
  readonly material: SecretMaterial;
  /** The old record's path and digest (a migration); null for a legacy save, which always writes. */
  readonly source: BindingSource | null;
  /**
   * A legacy save only: the old path's record as it stands after the save (an OAuth callback may write it
   * too), recorded as the binding's source so the read-through keeps serving the binding.
   */
  readonly observed?: BindingSource | null;
}

export type ImportOutcome = 'created' | 'updated' | 'unchanged' | 'conflict';

export interface Imported {
  readonly binding: Binding | null;
  readonly outcome: ImportOutcome;
}

/** What a revert removed: the binding as it was, and whether it changed since the import. */
export interface RevertedImport {
  readonly binding: Binding;
  readonly changedSinceImport: boolean;
}

interface Plan {
  readonly before: Binding | null;
  readonly next: Binding;
  readonly material: SecretMaterial | null;
  readonly meta: Partial<BindingVaultMeta>;
  readonly revoke: boolean;
  readonly narrowing: boolean;
}

/**
 * Bindings and their secrets (03-interfaces §5.2) over `BindingStore` and the
 * vault. Each call is one control-plane write: the row in the transaction,
 * the vault effect after the audit entry is staged, so an audit failure
 * leaves both unchanged. The vault write is compare-and-set on the version,
 * so a racing writer re-plans the whole write. Never reads a secret back.
 */
export class VaultCredentialBinder implements CredentialBinder {
  constructor(private readonly deps: VaultCredentialBinderDeps) {}

  async begin(
    ctx: WriteContext,
    inst: Installation,
    owner: BindingOwnerRef,
    mode: BindingMode,
    material?: SecretMaterial,
  ): Promise<BindingStart> {
    const pending: Binding = {
      schemaVersion: 1,
      id: `bnd_${this.deps.newId()}`,
      orgId: ctx.orgId,
      installation: inst.id,
      owner,
      mode,
      status: 'pending',
      secret: null,
      scopes: [],
      expiresAt: null,
      refreshedAt: null,
      lastError: null,
      revision: 0,
    };
    const meta = {
      kind: inst.kind,
      provider: null,
      source: null,
      importedVersion: null,
    };
    if (mode === 'oauth') {
      const start = this.deps.oauthStarts.find((s) => s.kind === inst.kind);
      if (start === undefined) {
        throw new OAuthStartUnsupported(
          `installations of kind ${inst.kind} cannot start an OAuth flow here`,
        );
      }
      const binding = await this.write(ctx, () =>
        this.plan(null, pending, null, meta),
      );
      const state = this.deps.newId();
      await this.deps.states.issue(state, {
        orgId: ctx.orgId,
        binding: binding.id,
      });
      const authorizeUrl = await start.authorizeUrl(
        ctx.orgId,
        inst.id,
        owner,
        state,
      );
      return { authorizeUrl, state, binding };
    }
    if (material === undefined)
      throw new Error(`a ${mode} binding needs its secret`);
    return {
      binding: await this.write(ctx, () =>
        this.plan(null, pending, material, meta),
      ),
    };
  }

  async complete(
    ctx: WriteContext,
    state: string,
    material: SecretMaterial,
  ): Promise<Binding> {
    const issued = await this.deps.states.take(state);
    if (issued?.orgId !== ctx.orgId) {
      throw new UnknownOAuthState('unknown or used OAuth state');
    }
    return this.write(ctx, async () => {
      const current = await this.current(ctx, issued.binding);
      return this.plan(current, current, material, {});
    });
  }

  setSecret(
    ctx: WriteContext,
    binding: string,
    material: SecretMaterial,
    expectedRevision: number,
  ): Promise<Binding> {
    return this.write(ctx, async () => {
      const current = await this.current(ctx, binding);
      return this.plan(
        current,
        { ...current, revision: expectedRevision },
        material,
        {},
      );
    });
  }

  revoke(
    ctx: WriteContext,
    binding: string,
    expectedRevision: number,
  ): Promise<Binding> {
    return this.write(ctx, async () => {
      const current = await this.current(ctx, binding);
      return this.revocation(current, expectedRevision);
    });
  }

  /** A legacy disconnect with the switch on: the owner's binding revoked; null when there is none. */
  async revokeOwner(
    ctx: WriteContext,
    installation: string,
    owner: BindingOwnerRef,
  ): Promise<Binding | null> {
    const current = await this.deps.bindings.find(
      ctx.orgId,
      installation,
      owner,
    );
    if (current === null || current.status === 'revoked') return current;
    return this.revoke(ctx, current.id, current.revision);
  }

  /**
   * A credential record from an old path (`source` set: the migration) or
   * from a legacy route with the switch on (`source` null). The migration
   * never brings back a revoked binding, never overwrites one it did not
   * import and writes nothing when the old record has not changed since the
   * last copy. A legacy save after a revoke is the owner connecting again: a
   * new binding, with the revoked one kept.
   */
  async importRecord(
    ctx: WriteContext,
    request: ImportRequest,
  ): Promise<Imported> {
    const existing = await this.deps.bindings.find(
      ctx.orgId,
      request.installation,
      request.owner,
    );
    const fresh: Binding = {
      schemaVersion: 1,
      id: `bnd_${this.deps.newId()}`,
      orgId: ctx.orgId,
      installation: request.installation,
      owner: request.owner,
      mode: request.mode,
      status: 'pending',
      secret: null,
      scopes: [],
      expiresAt: null,
      refreshedAt: null,
      lastError: null,
      revision: 0,
    };
    // A save after a disconnect is a new binding (the revoked one stays as history); the migration never makes one.
    const reconnect = existing?.status === 'revoked' && request.source === null;
    if (existing === null || reconnect) {
      const binding = await this.write(ctx, () =>
        this.plan(null, fresh, request.material, {
          kind: request.kind,
          provider: request.provider,
          // A reconnect keeps what the document says of its source, as a save over a live binding does.
          ...(reconnect
            ? request.observed
              ? { source: request.observed }
              : {}
            : {
                source: request.source,
                importedVersion: request.source === null ? null : 1,
              }),
        }),
      );
      return { binding, outcome: 'created' };
    }
    if (request.source !== null) {
      const state = await this.deps.vault.state(this.pathOf(existing));
      if (
        existing.status === 'revoked' ||
        state?.meta?.source?.digest === request.source.digest
      ) {
        return { binding: existing, outcome: 'unchanged' };
      }
      if (state?.meta?.source === null) {
        return { binding: existing, outcome: 'conflict' };
      }
    }
    const binding = await this.write(ctx, async () => {
      const current = await this.current(ctx, existing.id);
      return this.plan(
        current,
        { ...current, mode: request.mode, revision: existing.revision },
        request.material,
        {
          ...(request.kind === null ? {} : { kind: request.kind }),
          ...(request.provider === null ? {} : { provider: request.provider }),
          ...(request.source === null
            ? request.observed
              ? { source: request.observed }
              : {}
            : {
                source: request.source,
                importedVersion: (await this.lastVersion(current)) + 1,
              }),
        },
      );
    });
    return { binding, outcome: 'updated' };
  }

  /** Removes an imported binding (row, secret, pointer); a binding the migration did not import is refused. */
  async revertImport(
    ctx: WriteContext,
    installation: string,
    owner: BindingOwnerRef,
  ): Promise<RevertedImport | null> {
    const existing = await this.deps.bindings.find(
      ctx.orgId,
      installation,
      owner,
    );
    if (existing === null) return null;
    const path = this.pathOf(existing);
    const state = await this.deps.vault.state(path);
    // A revert that died after its vault erase (QA12-F1) left the row without its secret: finish it, a narrowing.
    const erased = state === null && existing.secret !== null;
    if (
      !erased &&
      (state?.meta?.source === null || state?.meta?.source === undefined)
    ) {
      throw new BindingNotImported(
        `binding ${existing.id} was not imported by the migration`,
      );
    }
    const changedSinceImport =
      state !== null && state.lastVersion !== state.meta?.importedVersion;
    await this.deps.transaction.run(ctx, async (tx: CpTx) => {
      await this.deps.bindings.remove(tx, existing.id, existing.revision);
      tx.external(() => this.deps.vault.erase(ctx.orgId, existing.id, path));
      return {
        result: null,
        change: {
          scope: orgScope(ctx.orgId),
          object: { type: 'binding', id: existing.id },
          revisionFrom: existing.revision,
          revisionTo: null,
          changeDigest: canonicalDigest({
            removed: existing.id,
            revision: existing.revision,
          }),
          changed: ['binding'],
          narrowing: true,
        },
      };
    });
    return { binding: existing, changedSinceImport };
  }

  /** The token refresher's new tokens: a new version, the binding active again. */
  refreshed(
    ctx: WriteContext,
    binding: string,
    expectedRevision: number,
    material: SecretMaterial,
  ): Promise<Binding> {
    return this.write(ctx, async () => {
      const current = await this.current(ctx, binding);
      return this.plan(
        current,
        { ...current, revision: expectedRevision, lastError: null },
        material,
        {},
      );
    });
  }

  /**
   * A refresh the provider refused: permanently (the user must reconnect),
   * the binding leaves the refresher's scan as `error`; transiently, only
   * `lastError` records it and it stays due.
   */
  refreshFailed(
    ctx: WriteContext,
    binding: string,
    expectedRevision: number,
    code: string,
    permanent: boolean,
  ): Promise<Binding> {
    return this.write(ctx, async () => {
      const current = await this.current(ctx, binding);
      const next: Binding = {
        ...current,
        status: permanent ? 'error' : current.status,
        lastError: { code, at: this.deps.now() },
        revision: expectedRevision,
      };
      return {
        before: current,
        next,
        material: null,
        meta: {},
        revoke: false,
        narrowing: permanent,
      };
    });
  }

  private pathOf(binding: Binding): string {
    return (
      binding.secret?.path ??
      bindingVaultPath(binding.orgId, binding.installation, binding.owner)
    );
  }

  private async lastVersion(binding: Binding): Promise<number> {
    return (
      (await this.deps.vault.state(this.pathOf(binding)))?.lastVersion ?? 0
    );
  }

  private async current(ctx: WriteContext, id: string): Promise<Binding> {
    const found = await this.deps.bindings.get(ctx.orgId, id);
    if (found === null) throw new ObjectNotFound(`binding ${id}`);
    return found;
  }

  private plan(
    before: Binding | null,
    next: Binding,
    material: SecretMaterial | null,
    meta: Partial<BindingVaultMeta>,
  ): Plan {
    return { before, next, material, meta, revoke: false, narrowing: false };
  }

  private revocation(current: Binding, expectedRevision: number): Plan {
    return {
      before: current,
      next: { ...current, status: 'revoked', revision: expectedRevision },
      material: null,
      meta: {},
      revoke: true,
      narrowing: true,
    };
  }

  /**
   * One control-plane write. `planned` runs inside the transaction's work,
   * which may run again: it reads what it needs there.
   */
  private async write(
    ctx: WriteContext,
    planned: () => Promise<Plan> | Plan,
  ): Promise<Binding> {
    const committed = await this.deps.transaction.run(ctx, async (tx) => {
      const plan = await planned();
      const path = this.pathOf(plan.next);
      let next: Binding = plan.next;
      // A new binding for an owner whose last one was revoked takes its place: the document continues at
      // the next version, and the revoked binding's pointer goes, so a run that pinned it reaches nothing.
      const superseded =
        plan.before === null
          ? await this.deps.bindings.find(
              ctx.orgId,
              next.installation,
              next.owner,
            )
          : null;
      if (superseded?.status === 'revoked') {
        const old = superseded.id;
        tx.external(() => this.deps.vault.unlink(ctx.orgId, old));
      }
      if (plan.material !== null) {
        const state = await this.deps.vault.state(path);
        const version = (state?.lastVersion ?? 0) + 1;
        const meta: BindingVaultMeta = {
          binding: next.id,
          installation: next.installation,
          owner: next.owner,
          mode: next.mode,
          materialKind: plan.material.kind,
          kind: state?.meta?.kind ?? null,
          provider: state?.meta?.provider ?? null,
          source: state?.meta?.source ?? null,
          importedVersion: state?.meta?.importedVersion ?? null,
          ...plan.meta,
        };
        const material = plan.material;
        tx.external(() =>
          this.deps.vault.write(path, version, meta, material, ctx.actor),
        );
        if (plan.before === null)
          tx.external(() => this.deps.vault.link(ctx.orgId, next.id, path));
        next = {
          ...next,
          status: 'active',
          secret: {
            path,
            version,
            versionDigest: canonicalDigest(
              bindingVersionDigestInput(next.id, version),
            ),
          },
          expiresAt: material.expiresAt ?? null,
          refreshedAt: this.deps.now(),
        };
      }
      if (plan.revoke)
        tx.external(() => this.deps.vault.tombstone(path, ctx.actor));
      const expected = plan.before === null ? null : plan.next.revision;
      const revision = await this.deps.bindings.upsert(tx, next, expected);
      const stored = { ...next, revision };
      return {
        result: stored,
        change: {
          scope: orgScope(ctx.orgId),
          object: { type: 'binding', id: stored.id },
          revisionFrom: plan.before?.revision ?? null,
          revisionTo: revision,
          changeDigest: canonicalDigest(stored),
          changed: changedPaths(plan.before ?? {}, stored),
          narrowing: plan.narrowing,
        },
      };
    });
    return committed.result;
  }
}
