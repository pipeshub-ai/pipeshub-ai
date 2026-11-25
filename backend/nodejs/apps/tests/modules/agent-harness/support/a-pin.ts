import { canonicalDigest } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { RELEASE_DEFAULTS } from '../../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import { PolicyResolver } from '../../../../src/modules/agent-harness/domain/policy';
import {
  pinScope,
  sealPin,
  type Heads,
  type PinBasis,
  type PinResult,
  type PinSubject,
  type ResolvedRunConfig,
  type RouteSubject,
  type RunConfigPinner,
} from '../../../../src/modules/control-plane';
import { aSnapshot } from '../../control-plane/support/resolution-fixtures';

/** A pin as the control plane seals one, resolved from the release and a request's deadline, at an org head of 0 unless given. */
export function aPin(activeTimeS = 3_600, headRevision = 0): ResolvedRunConfig {
  const resolved = new PolicyResolver().resolve([
    { kind: 'release', values: RELEASE_DEFAULTS },
    { kind: 'request', values: { budgets: { activeTimeS } } },
  ]);
  if (resolved.config === null) throw new Error('the release resolves');
  return sealPin({
    schemaVersion: 1,
    headRevision,
    routing: { path: 'durable', reason: null },
    scope: { agentKey: null, projectId: null },
    effectiveSet: { items: [], digest: canonicalDigest([]) },
    effectiveConfig: resolved.config,
    llm: null,
    deploymentDigest: canonicalDigest({}),
  });
}

/**
 * A pinner whose pins are `pin`'s: every turn routes durable (a child as its
 * parent), on a basis at `heads`: by default the revisions a store that saw
 * no control-plane write holds, else what the store under test holds now.
 */
export const pinnerOf = (
  pin: (subject: PinSubject, basis: PinBasis) => Promise<PinResult>,
  heads: Heads | ((orgId: string) => Promise<Heads>) = { org: 0, deployment: 0 },
): RunConfigPinner => {
  const basisOf = async (subject: RouteSubject): Promise<PinBasis> => {
    const at = typeof heads === 'function' ? await heads(subject.orgId) : heads;
    return {
      ...aSnapshot({ headRevision: at.org, deploymentRevision: at.deployment, agentKey: subject.agentKey, actorId: subject.actorId }),
      orgId: subject.orgId,
    };
  };
  return {
    route: async (subject) => ({
      routing: subject.parent?.routing ?? { path: 'durable', reason: null },
      scope: pinScope(subject),
      basis: await basisOf(subject),
      config: { schemaVersion: 1, dimensions: {} },
    }),
    pin: async (subject, basis) => pin(subject, basis ?? (await basisOf(subject))),
  };
};
