import type { PolicyLayer, PolicyLayers } from '../../control-plane';

/** A section of a policy layer that records who saved it and when. */
export type PolicySection = keyof NonNullable<PolicyLayer['updated']>;

/**
 * Where an edition's org settings live (`CONTROL_PLANE_POLICY_STORE`): `dual`
 * the policy store read through the KV documents, `on` the store only. This
 * build binds none, so its runs read the deployment's layers alone.
 */
export type PolicyStoreMode = 'off' | 'dual' | 'on';

/** The policy store an edition's settings read and write through (`AGENT_HARNESS_POLICY_BACKING`). */
export interface PolicyBacking {
  readonly mode: Exclude<PolicyStoreMode, 'off'>;
  /** What saves go through: in `dual` the store read through the KV documents. */
  readonly layers: PolicyLayers;
  /** The store itself, for the `dual` shadow comparison. */
  readonly store: PolicyLayers;
  /** A `dual` read whose stored section differs from the KV document's (logged and counted, never acted on). */
  readonly onDiverged?: (orgId: string, section: PolicySection) => void;
}
