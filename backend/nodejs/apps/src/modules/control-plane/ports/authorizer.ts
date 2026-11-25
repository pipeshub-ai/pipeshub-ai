/** Every action the control plane authorizes (03-interfaces §5.1). */
export type CpAction =
  | 'catalog.read'
  | 'catalog.import'
  | 'installation.read'
  | 'installation.create'
  | 'installation.update'
  | 'installation.delete'
  | 'grant.read'
  | 'grant.write'
  | 'policy.read'
  /** A section only the org's admins read (`access`, G-41; an edition's `adminOnly` section). */
  | `policy.read:${string}`
  | `policy.write:${string}`
  | 'binding.read'
  | 'binding.create'
  | 'binding.update'
  | 'approval.read'
  | 'approval.decide'
  | 'explain'
  | 'audit.read'
  /** What an agent spent this month (WP-56): its editors and the org's admins; never delegated. */
  | 'usage.read';

/** The authenticated caller; both ids come from the token, never from a body. */
export interface AuthzActor {
  readonly orgId: string;
  readonly userId: string;
}

/** What an action is on: the object's org and its owner scope where it has one. */
export interface ResourceRef {
  readonly orgId: string;
  readonly type: string;
  readonly id?: string;
  readonly owner?: string;
}

export type AuthzDecision =
  { readonly allow: true } | { readonly allow: false; readonly reason: string };

/**
 * Whether an actor may perform an action on a resource: the one port for every
 * admin check. Reads the live role on each check, so a demotion takes effect on
 * the next request.
 */
export interface Authorizer {
  check(
    actor: AuthzActor,
    action: CpAction,
    resource: ResourceRef,
  ): Promise<AuthzDecision>;
}
