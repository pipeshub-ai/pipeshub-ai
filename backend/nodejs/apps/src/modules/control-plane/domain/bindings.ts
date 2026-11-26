import type { Binding, BindingOwnerRef } from './objects';

/**
 * Where a binding's secret lives (02-lld §4.3): one document per installation
 * and owner, the P2.4b versioned envelope. The org owner is `org/org`.
 */
export const CREDENTIALS_ROOT = '/services/controlPlane/credentials';
/** Binding id → the document above, for readers that hold only the pinned reference (Python's broker). */
export const BINDING_POINTERS_ROOT = '/services/controlPlane/bindings';

const ownerSegments = (owner: BindingOwnerRef): string =>
  owner === 'org' ? 'org/org' : owner.replace(':', '/');

export const bindingVaultPath = (
  orgId: string,
  installation: string,
  owner: BindingOwnerRef,
): string =>
  `${CREDENTIALS_ROOT}/${orgId}/${installation}/${ownerSegments(owner)}`;

export const bindingPointerPath = (orgId: string, binding: string): string =>
  `${BINDING_POINTERS_ROOT}/${orgId}/${binding}`;

/**
 * What a binding version's digest covers (hashed with `canonicalDigest`):
 * the binding and the version number, so a digest names one version of one
 * binding and every later version of it is checkable from the pin alone.
 * Python's twin: `app.harness.credentials.bindings.version_digest`.
 */
export const bindingVersionDigestInput = (
  binding: string,
  version: number,
): Record<string, unknown> => ({ binding, version });

/**
 * A binding that still stands for its owner: every status but `revoked`. An
 * installation has one per owner; revoked ones stay beside it as history.
 */
export const LIVE_BINDING_STATUSES = [
  'pending',
  'active',
  'expiring',
  'expired',
  'error',
] as const satisfies ReadonlyArray<Binding['status']>;

export const isLiveBinding = (binding: Pick<Binding, 'status'>): boolean =>
  binding.status !== 'revoked';

/** Where a page of an installation's bindings ended: by owner, then by id (an owner's superseded bindings share it). */
export const bindingCursor = (binding: Pick<Binding, 'owner' | 'id'>): string =>
  JSON.stringify([binding.owner, binding.id]);

export function bindingCursorParts(cursor: string): {
  owner: string;
  id: string;
} {
  const [owner, id] = JSON.parse(cursor) as [string, string];
  return { owner, id };
}

/** What "My connections" and the explain view show of a binding: never its secret or where it is. */
export interface BindingView {
  readonly id: string;
  readonly installation: string;
  readonly owner: BindingOwnerRef;
  readonly mode: Binding['mode'];
  readonly status: Binding['status'];
  readonly expiresAt: number | null;
  readonly refreshedAt: number | null;
  readonly lastError: string | null;
  readonly revision: number;
}

export const bindingView = (binding: Binding): BindingView => ({
  id: binding.id,
  installation: binding.installation,
  owner: binding.owner,
  mode: binding.mode,
  status: binding.status,
  expiresAt: binding.expiresAt,
  refreshedAt: binding.refreshedAt,
  lastError: binding.lastError?.code ?? null,
  revision: binding.revision,
});

/** The actors of system writes to bindings, as they appear in `config.changed`. */
export const MIGRATION_ACTOR = 'service:credential-migration';
export const REFRESHER_ACTOR = 'service:token-refresher';
