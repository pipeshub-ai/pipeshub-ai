import type {
  Binding,
  BindingMode,
  BindingOwnerRef,
  Installation,
} from '../domain/objects';
import type { WriteContext } from './transaction';

/**
 * What a binding's secret is made from; never returned, logged or audited.
 * The vault keeps `value` as JSON; `record` is a toolset or MCP credential
 * record kept verbatim, so readers of the old paths read the same shape.
 */
export interface SecretMaterial {
  readonly kind: 'api_key' | 'oauth_tokens' | 'header' | 'record';
  readonly value: Readonly<Record<string, unknown>>;
  readonly expiresAt?: number;
}

export type BindingStart =
  | { readonly binding: Binding }
  | {
      readonly authorizeUrl: string;
      readonly state: string;
      readonly binding: Binding;
    };

/**
 * Creates, rotates and revokes bindings and writes their secrets to the vault
 * (03-interfaces §5.2). Each call is one control-plane write (audited in its
 * transaction). Never reads a secret back to a client.
 */
export interface CredentialBinder {
  begin(
    ctx: WriteContext,
    inst: Installation,
    owner: BindingOwnerRef,
    mode: BindingMode,
    material?: SecretMaterial,
  ): Promise<BindingStart>;
  /** The OAuth callback; throws `UnknownOAuthState` for a state never issued or already used. */
  complete(
    ctx: WriteContext,
    state: string,
    material: SecretMaterial,
  ): Promise<Binding>;
  setSecret(
    ctx: WriteContext,
    binding: string,
    material: SecretMaterial,
    expectedRevision: number,
  ): Promise<Binding>;
  revoke(
    ctx: WriteContext,
    binding: string,
    expectedRevision: number,
  ): Promise<Binding>;
}

export type BindingSummary = Pick<
  Binding,
  | 'id'
  | 'installation'
  | 'owner'
  | 'mode'
  | 'status'
  | 'expiresAt'
  | 'refreshedAt'
>;

/** A binding's state for the explain view; Node never leases secrets for runs. */
export interface BindingStateView {
  state(orgId: string, binding: string): Promise<BindingSummary | null>;
}
