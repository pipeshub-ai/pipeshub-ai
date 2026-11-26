/**
 * Org layers for pin tests, as `orgLayers` (binders/control-plane-pin.ts) folds
 * them: the deployment's layers from the approvals document and its flow
 * rule, then the org, agent and user layers a store holds (an edition's
 * source; this build wires none). The pinner folds whatever layers it is
 * handed, so its tests read them through this fixture.
 */
import type { PolicyLayer, PolicyLayers } from '../../../../src/modules/control-plane';
import type { FlowRule } from '../../../../src/modules/agent-harness/domain/approvals';
import { deploymentLayers } from '../../../../src/modules/agent-harness/domain/deployment-policy';
import type { ConfigDocumentStore } from '../../../../src/modules/agent-harness/ports/config-document-store';
import type { OrgLayers } from '../../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';

export interface OrgPolicyLayersOptions {
  /** The approvals document: the deployment's `default` and `agents[*]`. */
  approvals: ConfigDocumentStore<unknown>;
  /** The deployment's flow rule, read from its environment at boot. */
  flow: FlowRule | null;
  /** A store of org, agent and user layers, as an edition's source reads one. */
  store?: Pick<PolicyLayers, 'scopesOf' | 'readMany'>;
}

/** The org's, agents' and users' layers the store holds; an unreadable or deleted layer is left out. */
export const storedOrgLayers =
  (store: Pick<PolicyLayers, 'scopesOf' | 'readMany'>): OrgLayers =>
  async (orgId) => {
    const scopes = await Promise.all(['org', 'agent', 'user'].map((kind) => store.scopesOf(orgId, kind)));
    const layers = new Map<string, PolicyLayer>();
    for (const [scope, entry] of await store.readMany(orgId, scopes.flat())) {
      if (entry.status === 'ok' && entry.layer !== null) layers.set(scope, entry.layer);
    }
    return layers;
  };

export function orgPolicyLayers(options: OrgPolicyLayersOptions): OrgLayers {
  return async (orgId) => {
    const approvals = (await options.approvals.read()).value;
    const stored = options.store !== undefined ? await storedOrgLayers(options.store)(orgId) : new Map();
    return new Map([...deploymentLayers(approvals, options.flow), ...stored]);
  };
}
