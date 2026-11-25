import type { Container } from 'inversify';
import type { PolicyLayer } from '../../control-plane';
import type { OrgLayers } from '../adapters/control-plane/policy-snapshot-loader';
import type { AgentHarnessDependencies } from '../composition';
import { harnessEditionOf, type OrgPolicyLayersFactory } from '../edition';

/**
 * The org-layer sources this build folds itself, before the edition's (`HarnessEdition.orgPolicyLayers`). Each holds
 * only the sections people set up for themselves, never a rule an admin narrows runs by.
 */
export const BUILT_IN_ORG_LAYER_SOURCES: readonly OrgPolicyLayersFactory[] = [];

const isDeploymentScope = (scope: string): boolean =>
  scope === 'deployment' || scope.startsWith('deployment:');

/**
 * The layers of several sources by scope: a later source's section replaces an earlier one's, so the edition's
 * value wins. The deployment's scopes are left out: they are read from the deployment alone.
 */
export function mergeOrgLayers(
  all: ReadonlyArray<ReadonlyMap<string, PolicyLayer>>,
): Map<string, PolicyLayer> {
  const merged = new Map<string, PolicyLayer>();
  for (const layers of all) {
    for (const [scope, layer] of layers) {
      if (isDeploymentScope(scope)) continue;
      const held = merged.get(scope);
      merged.set(
        scope,
        held === undefined ? layer : { ...held, ...layer, scope: held.scope },
      );
    }
  }
  return merged;
}

/** The org-layer sources wired here, the built-in ones first; none when neither this build nor the edition has one. */
export function orgLayerSources(
  container: Container,
  dependencies: AgentHarnessDependencies,
): OrgLayers[] {
  const edition = harnessEditionOf(container).orgPolicyLayers;
  return [...BUILT_IN_ORG_LAYER_SOURCES, ...(edition !== null ? [edition] : [])]
    .map((make) => make(container, dependencies))
    .filter((source): source is OrgLayers => source !== undefined);
}
