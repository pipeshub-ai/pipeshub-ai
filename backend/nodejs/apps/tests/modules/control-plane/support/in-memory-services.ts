/* eslint-disable @typescript-eslint/require-await -- in-memory adapters implement asynchronous ports without I/O. */
/**
 * In-memory adapters of the services the control-plane core depends on
 * (03-interfaces §5): authorizer, credential binder and binding view, change
 * notifier, membership, agent directory, reference checker.
 */
import {
  changedPaths,
  ControlPlaneRetryableError,
  orgScope,
  type ChangeEvent,
  type ControlPlaneTransaction,
  type WriteContext,
} from '../../../../src/modules/control-plane';
import type { Problem } from '../../../../src/modules/control-plane/contracts/generated';
import type {
  Binding,
  BindingMode,
  BindingOwnerRef,
  Installation,
} from '../../../../src/modules/control-plane/domain/objects';
import type { PolicyLayer } from '../../../../src/modules/control-plane/domain/policy-alias';
import { problem } from '../../../../src/modules/control-plane/domain/problem';
import type { TeamMembership } from '../../../../src/modules/control-plane/domain/resolution';
import { ObjectNotFound, UnknownOAuthState } from '../../../../src/modules/control-plane/domain/store-errors';
import type {
  Authorizer,
  AuthzActor,
  AuthzDecision,
  CpAction,
  ResourceRef,
} from '../../../../src/modules/control-plane/ports/authorizer';
import { bindingVaultPath, bindingVersionDigestInput } from '../../../../src/modules/control-plane/domain/bindings';
import type { BindingStore } from '../../../../src/modules/control-plane/ports/binding-store';
import type { ChangeNotifier, Unsubscribe } from '../../../../src/modules/control-plane/ports/change-notifier';
import type {
  BindingStart,
  BindingStateView,
  BindingSummary,
  CredentialBinder,
  SecretMaterial,
} from '../../../../src/modules/control-plane/ports/credential-binder';
import type { AgentDirectory, AgentRunAccess, MembershipPort } from '../../../../src/modules/control-plane/ports/directory';
import type { ReferenceChecker } from '../../../../src/modules/control-plane/ports/reference-checker';
import { canonicalDigest } from '../../../../src/libs/utils/canonical-json';

const READ_ACTIONS: ReadonlySet<CpAction> = new Set<CpAction>([
  'catalog.read',
  'installation.read',
  'grant.read',
  'policy.read',
  'binding.read',
  'approval.read',
  'explain',
]);
/** What a member may do to objects they own themselves (a personal install, their own connection). */
const OWNER_ACTIONS: ReadonlySet<CpAction> = new Set<CpAction>([
  'installation.create',
  'installation.update',
  'installation.delete',
  'binding.create',
  'binding.update',
]);

export type OrgRole = 'admin' | 'member';

/** Org admins may do everything in their org; members read, and write what they own. Reads the role on every check. */
export class InMemoryAuthorizer implements Authorizer {
  private readonly roles = new Map<string, OrgRole>();

  setRole(orgId: string, userId: string, role: OrgRole | null): void {
    if (role === null) this.roles.delete(`${orgId}|${userId}`);
    else this.roles.set(`${orgId}|${userId}`, role);
  }

  async check(actor: AuthzActor, action: CpAction, resource: ResourceRef): Promise<AuthzDecision> {
    if (resource.orgId !== actor.orgId) return { allow: false, reason: 'cross_org' };
    const role = this.roles.get(`${actor.orgId}|${actor.userId}`);
    if (role === undefined) return { allow: false, reason: 'not_a_member' };
    if (role === 'admin' || READ_ACTIONS.has(action)) return { allow: true };
    if (OWNER_ACTIONS.has(action) && resource.owner === `user:${actor.userId}`) return { allow: true };
    return { allow: false, reason: 'requires_admin' };
  }
}

interface VaultDocument {
  versions: Array<{ version: number; material: SecretMaterial }>;
  tombstoned: boolean;
}

/** The encrypted KV vault's semantics (versioned envelope, compare-and-set, tombstones), without the encryption. */
export class InMemorySecretVault {
  readonly documents = new Map<string, VaultDocument>();

  nextVersion(path: string): number {
    return (this.documents.get(path)?.versions.at(-1)?.version ?? 0) + 1;
  }

  async write(path: string, version: number, material: SecretMaterial): Promise<void> {
    if (this.nextVersion(path) !== version) throw new ControlPlaneRetryableError(`vault ${path} moved`);
    const doc = this.documents.get(path) ?? { versions: [], tombstoned: false };
    this.documents.set(path, { versions: [...doc.versions, { version, material }], tombstoned: false });
  }

  async tombstone(path: string): Promise<void> {
    const doc = this.documents.get(path);
    if (doc !== undefined) doc.tombstoned = true;
  }

  /** The current secret, or null when there is none or it is tombstoned. */
  read(path: string): SecretMaterial | null {
    const doc = this.documents.get(path);
    return doc === undefined || doc.tombstoned ? null : (doc.versions.at(-1)?.material ?? null);
  }
}

export interface CredentialBinderDeps {
  readonly transaction: ControlPlaneTransaction;
  readonly bindings: BindingStore;
  readonly vault: InMemorySecretVault;
  readonly newId: () => string;
  readonly now: () => number;
}


/** Bindings and their secrets, one audited control-plane write per call. */
export class InMemoryCredentialBinder implements CredentialBinder {
  private readonly states = new Map<string, { orgId: string; binding: string }>();

  constructor(private readonly deps: CredentialBinderDeps) {}

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
    if (mode === 'oauth') {
      const binding = await this.write(ctx, null, pending, null, false);
      const state = this.deps.newId();
      this.states.set(state, { orgId: ctx.orgId, binding: binding.id });
      return { authorizeUrl: `/oauth/authorize?state=${state}`, state, binding };
    }
    if (material === undefined) throw new Error(`a ${mode} binding needs its secret`);
    return { binding: await this.write(ctx, null, pending, material, false) };
  }

  async complete(ctx: WriteContext, state: string, material: SecretMaterial): Promise<Binding> {
    const issued = this.states.get(state);
    if (issued === undefined || issued.orgId !== ctx.orgId) throw new UnknownOAuthState('unknown or used OAuth state');
    this.states.delete(state);
    const current = await this.current(ctx, issued.binding);
    return this.write(ctx, current, current, material, false);
  }

  async setSecret(ctx: WriteContext, binding: string, material: SecretMaterial, expectedRevision: number): Promise<Binding> {
    const current = await this.current(ctx, binding);
    return this.write(ctx, current, { ...current, revision: expectedRevision }, material, false);
  }

  async revoke(ctx: WriteContext, binding: string, expectedRevision: number): Promise<Binding> {
    const current = await this.current(ctx, binding);
    const revoked: Binding = { ...current, status: 'revoked', revision: expectedRevision };
    return this.write(ctx, current, revoked, null, true);
  }

  private async current(ctx: WriteContext, id: string): Promise<Binding> {
    const found = await this.deps.bindings.get(ctx.orgId, id);
    if (found === null) throw new ObjectNotFound(`binding ${id}`);
    return found;
  }

  /** One control-plane write: the vault effect (when there is material or a revoke) and the binding row, audited. */
  private async write(
    ctx: WriteContext,
    before: Binding | null,
    planned: Binding,
    material: SecretMaterial | null,
    revoke: boolean,
  ): Promise<Binding> {
    const path = bindingVaultPath(ctx.orgId, planned.installation, planned.owner);
    const committed = await this.deps.transaction.run(ctx, async (tx) => {
      let next: Binding = planned;
      if (material !== null) {
        const version = this.deps.vault.nextVersion(path);
        tx.external(() => this.deps.vault.write(path, version, material));
        next = {
          ...planned,
          status: 'active',
          secret: { path, version, versionDigest: canonicalDigest(bindingVersionDigestInput(planned.id, version)) },
          expiresAt: material.expiresAt ?? null,
          refreshedAt: this.deps.now(),
        };
      }
      if (revoke) tx.external(() => this.deps.vault.tombstone(path));
      const expected = before === null ? null : planned.revision;
      const revision = await this.deps.bindings.upsert(tx, next, expected);
      const stored = { ...next, revision };
      return {
        result: stored,
        change: {
          scope: orgScope(ctx.orgId),
          object: { type: 'binding', id: stored.id },
          revisionFrom: before?.revision ?? null,
          revisionTo: revision,
          changeDigest: canonicalDigest(stored),
          changed: changedPaths(before ?? {}, stored),
          narrowing: revoke,
        },
      };
    });
    return committed.result;
  }
}

export class InMemoryBindingStateView implements BindingStateView {
  constructor(private readonly bindings: BindingStore) {}

  async state(orgId: string, binding: string): Promise<BindingSummary | null> {
    const found = await this.bindings.get(orgId, binding);
    if (found === null) return null;
    const { id, installation, owner, mode, status, expiresAt, refreshedAt } = found;
    return { id, installation, owner, mode, status, expiresAt, refreshedAt };
  }
}

/** Delivers each event to every subscriber at publish, in publish order. */
export class InMemoryChangeNotifier implements ChangeNotifier {
  private readonly handlers = new Set<(event: ChangeEvent) => void>();

  async publish(event: ChangeEvent): Promise<void> {
    for (const handler of [...this.handlers]) handler(structuredClone(event));
  }

  subscribe(handler: (event: ChangeEvent) => void): Unsubscribe {
    const own = (event: ChangeEvent): void => handler(event);
    this.handlers.add(own);
    return () => {
      this.handlers.delete(own);
    };
  }
}

export class DirectoryUnavailable extends Error {
  constructor(what: string) {
    super(`${what} is unavailable`);
    this.name = 'DirectoryUnavailable';
  }
}

export class InMemoryMembership implements MembershipPort {
  readonly teams = new Map<string, TeamMembership[]>();
  available = true;

  async teamsOf(orgId: string, userId: string): Promise<readonly TeamMembership[]> {
    if (!this.available) throw new DirectoryUnavailable('team membership');
    return structuredClone(this.teams.get(`${orgId}|${userId}`) ?? []);
  }
}

export interface DirectoryAgent {
  readonly definitionVersion: number;
  readonly runners: readonly string[];
  readonly editors: readonly string[];
  readonly ownerTeam: string | null;
}

export class InMemoryAgentDirectory implements AgentDirectory {
  readonly agents = new Map<string, DirectoryAgent>();
  available = true;

  async authorizeRun(orgId: string, agentKey: string, actor: AuthzActor): Promise<AgentRunAccess | null> {
    const agent = this.agent(orgId, agentKey);
    if (agent === undefined || actor.orgId !== orgId || !agent.runners.includes(actor.userId)) return null;
    return { agentKey, definitionVersion: agent.definitionVersion };
  }

  async canEdit(orgId: string, agentKey: string, actor: AuthzActor): Promise<boolean> {
    const agent = this.agent(orgId, agentKey);
    return agent !== undefined && actor.orgId === orgId && agent.editors.includes(actor.userId);
  }

  async ownerTeam(orgId: string, agentKey: string): Promise<string | null> {
    return this.agent(orgId, agentKey)?.ownerTeam ?? null;
  }

  private agent(orgId: string, agentKey: string): DirectoryAgent | undefined {
    if (!this.available) throw new DirectoryUnavailable('the agent directory');
    return this.agents.get(`${orgId}|${agentKey}`);
  }
}

/** Finds the references in a layer; production composes one finder per section a domain module registers. */
export type ReferenceFinder = (layer: PolicyLayer) => readonly string[];

export class InMemoryReferenceChecker implements ReferenceChecker {
  readonly known = new Map<string, Set<string>>();

  constructor(private readonly find: ReferenceFinder) {}

  async check(orgId: string, layer: PolicyLayer): Promise<Problem[]> {
    const known = this.known.get(orgId) ?? new Set<string>();
    return this.find(layer)
      .filter((ref) => !known.has(ref))
      .map((ref) => problem('CP_UNKNOWN_REFERENCE', `This org has no ${ref}.`, { action: 'fix_reference' }));
  }
}
