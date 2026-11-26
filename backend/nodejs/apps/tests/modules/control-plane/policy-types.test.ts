/**
 * CP-SKEL D16: the control plane's mirror of CPD-3's policy types holds the real
 * ones (compile time), and the pinner runs on CPD-3's resolver (run time).
 */
import { expect } from 'chai';
import {
  FoldedPrefix,
  PolicyResolver as HarnessPolicyResolver,
} from '../../../src/modules/agent-harness/domain/policy';
import type { PolicyLayer as GeneratedPolicyLayer } from '../../../src/modules/agent-harness/contracts/generated/core/policy/policy-layer';
import type { EffectiveRunConfig as GeneratedEffectiveRunConfig } from '../../../src/modules/agent-harness/contracts/generated/core/policy/effective-run-config';
import type {
  EffectiveRunConfig,
  PolicyEngine,
  PolicyLayer,
  PolicyResolver,
} from '../../../src/modules/control-plane/domain/policy-alias';
import { isProblem } from '../../../src/modules/control-plane/domain/problem';
import type { ResolvedRunConfig } from '../../../src/modules/control-plane/domain/resolution';
import {
  InMemoryEffectiveSetResolver,
  InMemoryPolicyLayerSource,
  InMemoryRunConfigPinner,
  PIN_LAYER_KINDS,
} from './support/in-memory-resolution';
import { StaticSnapshots } from './support/pinner-contract';
import { ORG_A } from './support/cp-objects';
import { aLayer, aSnapshot, aSubject } from './support/resolution-fixtures';

const resolverFits: PolicyResolver = new HarnessPolicyResolver();
const engineFits: PolicyEngine<FoldedPrefix> = new HarnessPolicyResolver();
const layerFits = (layer: GeneratedPolicyLayer): PolicyLayer => layer;
const configFits = (config: GeneratedEffectiveRunConfig): EffectiveRunConfig => config;

const maxUsd = (kind: Parameters<typeof aLayer>[0], value: number) =>
  aLayer(kind, { schemaVersion: 1, engineProfile: { limits: { maxUsd: value } } });

describe('CP-SKEL D16: CPD-3 policy types', () => {
  const pinner = new InMemoryRunConfigPinner({
    snapshots: new StaticSnapshots(
      aSnapshot({ layers: { [`org:${ORG_A}`]: maxUsd('org', 5), 'agent:a7': maxUsd('agent', 3) } }),
    ),
    sources: PIN_LAYER_KINDS.map((kind) => new InMemoryPolicyLayerSource(kind)),
    resolver: resolverFits,
    effectiveSet: new InMemoryEffectiveSetResolver(),
  });

  it('D16-1: CPD-3 resolver, layer and config satisfy the control plane mirror', () => {
    expect([typeof layerFits, typeof configFits]).to.deep.equal(['function', 'function']);
    expect(engineFits.foldPrefix([]).refusal).to.equal(null);
  });

  it('D16-2: the pinner over CPD-3 resolver narrows and refuses a widening request', async () => {
    const narrowed = (await pinner.pin(aSubject({ request: maxUsd('request', 2) }))) as ResolvedRunConfig;
    expect(isProblem(narrowed)).to.equal(false);
    expect(narrowed.effectiveConfig.dimensions['engineProfile.limits.maxUsd']).to.deep.include({ value: 2, setBy: 'request' });
    const widened = await pinner.pin(aSubject({ request: maxUsd('request', 4) }));
    expect(widened).to.deep.include({ code: 'CP_WIDENING', dimension: 'engineProfile.limits.maxUsd', layer: 'request' });
  });
});
